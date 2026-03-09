"""
AI 信号解析器
使用 OpenRouter API（兼容 OpenAI SDK）将自然语言消息解析为标准 Signal
"""

import os
import json
import re
import logging
from typing import Optional

from openai import AsyncOpenAI

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .position_tracker import Position

from .models import Signal, Action, OrderType, EntryOrder, TakeProfit

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个专业的加密货币合约交易信号解析器。

你的任务是从用户发来的消息中提取交易指令，并以 JSON 格式返回。

支持的操作类型（action）：
- open_long：开多仓
- open_short：开空仓
- close：全部平仓
- reduce：减仓（部分平仓）

输出格式（严格 JSON，不要有任何额外文字）：
{
  "action": "open_long | open_short | close | reduce",
  "symbol": "BTC",
  "entries": [
    {
      "order_type": "market | limit",
      "price": 95000 或 null,
      "leverage": 100,
      "margin_pct": 2.0 或 null
    }
  ],
  "take_profits": [
    { "price": 90000, "close_pct": 50 或 null },
    { "price": 85000, "close_pct": null }
  ],
  "sl": 98000 或 null,
  "reduce_pct": 50 或 null
}

规则：
1. symbol 只需要基础币种，如 BTC、ETH，不需要加 USDT；消息未明确提及币种时填 null
2. 若消息不包含任何交易指令（仅为行情分析、评论等），返回 {"action": null}
3. entries 数组：每个入场点一个对象；market 单时 price 为 null
4. "市价直接进" / "市价入" → order_type="market", price=null
5. "挂 xxxx" / "限价 xxxx" → order_type="limit", price=xxxx
6. leverage：杠杆倍数，整数，若未提到填 20
7. margin_pct：保证金百分比（如"2%保证金"→ 2.0），未提到填 null
8. take_profits：按顺序列出所有止盈目标；close_pct 表示该档平仓比例（50=平一半），未说明填 null
9. sl：止损价格，未提到填 null
10. 所有价格为数字，不带单位
11. 减仓/止盈识别规则（重要）：
    - "止盈 X% 仓位" / "减仓 X%" / "平 X% 仓" / "出 X%" → action="reduce", reduce_pct=X
    - "止盈一半" / "减半" → action="reduce", reduce_pct=50
    - "全部止盈" / "全平" / "清仓" → action="close"
    - 只说"可以止盈"或"止盈"但没有指定比例 → action="reduce", reduce_pct=null
    - "移动止损到保本" / "移动保本损" / "保本止损" → sl 字段填 0（表示移至保本，无具体价格）
12. 消息中含有"止盈"、"减仓"、"平仓"、"出货"等操作动词，即使语气口语化也应视为交易指令，不能返回 {"action": null}
"""


class SignalParser:
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1",
        )
        self.model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")

    async def parse(self, text: str) -> Optional[Signal]:
        """将原始消息文本解析为 Signal，无法识别则返回 None"""
        if not text or not text.strip():
            return None

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                temperature=0,
                max_tokens=500,
            )

            content = response.choices[0].message.content.strip()
            logger.debug(f"AI 原始输出: {content}")

            # 兼容各模型：从响应中提取 JSON（支持 ```json 代码块或裸 JSON）
            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
            if json_match:
                content = json_match.group(1)
            else:
                # 取第一个 { ... } 块
                brace_match = re.search(r"\{.*\}", content, re.DOTALL)
                if brace_match:
                    content = brace_match.group(0)

            data = json.loads(content)

            if data is None or data.get("action") is None:
                logger.info(f"非交易信号，跳过: {text[:80]}")
                return None

            entries = [
                EntryOrder(
                    order_type=e.get("order_type", "market"),
                    price=e.get("price"),
                    leverage=int(e.get("leverage", 20)),
                    margin_pct=e.get("margin_pct"),
                )
                for e in (data.get("entries") or [])
            ]

            take_profits = [
                TakeProfit(
                    price=tp["price"],
                    close_pct=tp.get("close_pct"),
                )
                for tp in (data.get("take_profits") or [])
            ]

            signal = Signal(
                action=data["action"],
                symbol=data.get("symbol", "BTC"),
                entries=entries,
                take_profits=take_profits,
                sl=data.get("sl"),
                reduce_pct=data.get("reduce_pct"),
                raw_text=text,
            )

            logger.info(
                f"解析成功: {signal.action.value} {signal.symbol} | "
                f"入场={len(entries)}个 止盈={len(take_profits)}个 止损={signal.sl}"
            )
            return signal

        except json.JSONDecodeError as e:
            logger.error(f"AI 返回的 JSON 无法解析: {e}")
            return None
        except Exception as e:
            logger.error(f"解析信号时出错: {e}")
            return None

    async def resolve_position(self, text: str, positions: list) -> Optional[str]:
        """
        当信号无明确币种时，将持仓列表和消息文本交给 AI 判断操作的是哪个仓位。
        返回 symbol 字符串（如 "ETHUSDT"），无法判断时返回 None。
        """
        if not positions:
            return None

        pos_desc = "\n".join(
            f"- {p.symbol}  方向={p.side}  开仓价={p.entry_price or '市价'}  开仓时间={p.opened_at[:19]}"
            for p in positions
        )

        prompt = f"""当前持有以下仓位：
{pos_desc}

收到操作消息："{text}"

请判断这条消息操作的是哪个仓位，只返回对应的 symbol（如 BTCUSDT），无法判断时返回 null。
不要有任何额外文字，只返回 symbol 字符串或 null。"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=20,
            )
            result = response.choices[0].message.content.strip().strip('"').strip("'")
            logger.debug(f"持仓推断结果: {result!r}")
            if result.lower() == "null" or not result:
                return None
            # 规范化格式
            result = result.upper().replace("/", "").replace("-", "")
            if not result.endswith("USDT"):
                result = result + "USDT"
            # 确认在持仓列表中
            valid_symbols = {p.symbol for p in positions}
            if result in valid_symbols:
                logger.info(f"[持仓推断] AI 判断操作的是: {result}")
                return result
            logger.warning(f"[持仓推断] AI 返回 {result!r} 不在持仓列表中，回退到最近开仓")
            return None
        except Exception as e:
            logger.error(f"[持仓推断] 出错: {e}")
            return None
