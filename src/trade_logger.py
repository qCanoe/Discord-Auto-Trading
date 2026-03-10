"""
交易记录推送器 / Trade Logger
通过 Discord Webhook 将成交结果发送到专属频道
Push trade results to Discord channel via Webhook
无需 Bot Token，在自己的服务器创建 Webhook 即可使用 / No Bot token needed, create Webhook in your server
"""

import os
import logging
from datetime import datetime, timezone, timedelta

import aiohttp

from .models import Signal, Action

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))

ACTION_META = {
    Action.OPEN_LONG:  ("📈 开多", 0x2ECC71),   # 绿 / green
    Action.OPEN_SHORT: ("📉 开空", 0xE74C3C),   # 红 / red
    Action.CLOSE:      ("🏁 全平", 0x95A5A6),   # 灰 / grey
    Action.REDUCE:     ("✂️ 减仓", 0xE67E22),   # 橙 / orange
}


class TradeLogger:
    def __init__(self):
        self.webhook_url = os.getenv("TRADE_LOG_WEBHOOK_URL", "")

    async def log(self, signal: Signal, success: bool, note: str = ""):
        if not self.webhook_url:
            return

        payload = self._build_payload(signal, success, note)
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(self.webhook_url, json=payload)
                if resp.status not in (200, 204):
                    text = await resp.text()
                    logger.warning(f"Webhook 发送失败 [{resp.status}]: {text[:200]}")
        except Exception as e:
            logger.error(f"发送交易日志失败: {e}")

    def _build_payload(self, signal: Signal, success: bool, note: str) -> dict:
        action_label, color = ACTION_META.get(
            signal.action, ("❓ 未知", 0x7F8C8D)
        )
        status = "✅ 成功" if success else "❌ 失败"
        now_cst = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")

        fields = []

        # 入场信息 / Entry info
        if signal.entries:
            lines = []
            for i, e in enumerate(signal.entries, 1):
                price_str = f"@{e.price}" if e.price else "市价"
                margin_str = f"  保证金{e.margin_pct}%" if e.margin_pct else ""
                lines.append(f"入场#{i}: {e.order_type.value} {price_str}  {e.leverage}x{margin_str}")
            fields.append({"name": "入场", "value": "\n".join(lines), "inline": False})

        # 止盈 / Take profit
        if signal.take_profits:
            tp_lines = []
            for i, tp in enumerate(signal.take_profits, 1):
                close_str = f"  平{tp.close_pct}%" if tp.close_pct else "  全平"
                tp_lines.append(f"TP#{i}: {tp.price}{close_str}")
            fields.append({"name": "止盈", "value": "\n".join(tp_lines), "inline": True})

        # 止损 / Stop loss
        if signal.sl is not None:
            sl_str = "移至保本" if signal.sl == 0 else str(signal.sl)
            fields.append({"name": "止损", "value": sl_str, "inline": True})

        # 减仓比例 / Reduce pct
        if signal.reduce_pct is not None:
            fields.append({"name": "减仓比例", "value": f"{signal.reduce_pct}%", "inline": True})

        # 备注 / Note
        if note:
            fields.append({"name": "备注", "value": note, "inline": False})

        return {
            "embeds": [{
                "title": f"{status}  {action_label}  {signal.symbol}",
                "color": color,
                "fields": fields,
                "footer": {"text": f"北京时间 {now_cst}"},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }]
        }
