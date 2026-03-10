"""
交易记录推送器
每次成交后将结果格式化发送到 Discord 专属频道
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import discord

from .models import Signal, Action

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))


class TradeLogger:
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.channel_id = int(os.getenv("TRADE_LOG_CHANNEL_ID", "0"))

    async def log(
        self,
        signal: Signal,
        success: bool,
        note: str = "",
    ):
        """将交易结果发送到 trade-log 频道"""
        if not self.channel_id:
            return

        channel = self.bot.get_channel(self.channel_id)
        if channel is None:
            logger.warning(f"找不到 TRADE_LOG_CHANNEL_ID={self.channel_id}")
            return

        embed = self._build_embed(signal, success, note)
        try:
            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"发送交易日志失败: {e}")

    def _build_embed(self, signal: Signal, success: bool, note: str) -> discord.Embed:
        now = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")

        action_map = {
            Action.OPEN_LONG:  ("📈 开多", discord.Color.green()),
            Action.OPEN_SHORT: ("📉 开空", discord.Color.red()),
            Action.CLOSE:      ("🏁 全平", discord.Color.light_grey()),
            Action.REDUCE:     ("✂️ 减仓", discord.Color.orange()),
        }
        action_label, color = action_map.get(signal.action, ("❓ 未知", discord.Color.default()))

        status = "✅ 成功" if success else "❌ 失败"
        title = f"{status}  {action_label}  {signal.symbol}"

        embed = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
        embed.set_footer(text=f"北京时间 {now}")

        # 入场信息
        if signal.entries:
            lines = []
            for i, e in enumerate(signal.entries, 1):
                price_str = f"@{e.price}" if e.price else "市价"
                margin_str = f"  保证金{e.margin_pct}%" if e.margin_pct else ""
                lines.append(f"入场#{i}: {e.order_type.value} {price_str}  {e.leverage}x{margin_str}")
            embed.add_field(name="入场", value="\n".join(lines), inline=False)

        # 止盈
        if signal.take_profits:
            tp_lines = []
            for i, tp in enumerate(signal.take_profits, 1):
                close_str = f"  平{tp.close_pct}%" if tp.close_pct else "  全平"
                tp_lines.append(f"TP#{i}: {tp.price}{close_str}")
            embed.add_field(name="止盈", value="\n".join(tp_lines), inline=True)

        # 止损
        if signal.sl is not None:
            sl_str = "移至保本" if signal.sl == 0 else str(signal.sl)
            embed.add_field(name="止损", value=sl_str, inline=True)

        # 减仓比例
        if signal.reduce_pct is not None:
            embed.add_field(name="减仓比例", value=f"{signal.reduce_pct}%", inline=True)

        # 备注
        if note:
            embed.add_field(name="备注", value=note, inline=False)

        return embed
