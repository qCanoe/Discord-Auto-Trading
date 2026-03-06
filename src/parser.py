"""
AI 信号解析器
使用 OpenRouter API（兼容 OpenAI SDK）将自然语言消息解析为标准 Signal
"""

import os
import json
import logging
from typing import Optional

from openai import AsyncOpenAI

from .models import Signal, Action, OrderType

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个专业的加密货币交易信号解析器。

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
  "order_type": "market | limit",
  "entry_price": 95000 或 null,
  "tp": 100000 或 null,
  "sl": 90000 或 null,
  "size_usdt": 100 或 null,
  "size_pct": 10 或 null,
  "reduce_pct": 50 或 null
}

规则：
1. symbol 只需要基础币种，如 BTC、ETH，不需要加 USDT
2. 若消息不包含任何交易指令，返回 null
3. entry_price：仅挂限价单时填写，市价单填 null
4. size_usdt 和 size_pct 二选一，都没提到则都填 null（使用默认值）
5. reduce_pct：仅 reduce 操作时填写，如"减仓50%"填 50
6. 所有价格为数字，不带单位
"""


class SignalParser:
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1",
        )
        self.model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")

    async def parse(self, text: str) -> Optional[Signal]:
        """
        将原始消息文本解析为 Signal，无法识别则返回 None
        """
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
                max_tokens=300,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content.strip()
            logger.debug(f"AI 原始输出: {content}")

            data = json.loads(content)

            # AI 判断不是交易信号
            if data is None or data.get("action") is None:
                logger.info(f"非交易信号，跳过: {text[:80]}")
                return None

            signal = Signal(
                action=data["action"],
                symbol=data.get("symbol", "BTC"),
                order_type=data.get("order_type", "market"),
                entry_price=data.get("entry_price"),
                tp=data.get("tp"),
                sl=data.get("sl"),
                size_usdt=data.get("size_usdt"),
                size_pct=data.get("size_pct"),
                reduce_pct=data.get("reduce_pct"),
                raw_text=text,
            )

            logger.info(f"解析成功: {signal.action.value} {signal.symbol} | "
                        f"entry={signal.entry_price} tp={signal.tp} sl={signal.sl}")
            return signal

        except json.JSONDecodeError as e:
            logger.error(f"AI 返回的 JSON 无法解析: {e}")
            return None
        except Exception as e:
            logger.error(f"解析信号时出错: {e}")
            return None
