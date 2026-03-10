"""
持仓追踪器 / Position Tracker
记录当前所有开放仓位，供无币种信号（reduce/close）推断操作对象使用
Track open positions for symbol inference when signal has no symbol
数据持久化到 positions.json，重启后自动恢复 / Persisted to positions.json, restored on restart
"""

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .models import Action, Signal

logger = logging.getLogger(__name__)

POSITIONS_FILE = os.path.join(os.path.dirname(__file__), "..", "positions.json")


@dataclass
class Position:
    symbol: str                  # e.g. BTCUSDT
    side: str                    # "long" | "short"
    entry_price: Optional[float]
    size_usdt: float
    leverage: int
    opened_at: str               # ISO 8601 字符串，方便 JSON 序列化 / ISO 8601 for JSON
    raw_text: str = ""           # 开仓原始消息，便于回溯 / Original signal for traceback

    @property
    def opened_dt(self) -> datetime:
        return datetime.fromisoformat(self.opened_at)


class PositionTracker:
    def __init__(self):
        self._positions: Dict[str, Position] = {}  # symbol -> Position
        self._load()

    # ------------------------------------------------------------------
    # 持仓 CRUD / Position CRUD
    # ------------------------------------------------------------------

    def open(self, signal: Signal, size_usdt: float = 0.0) -> None:
        """记录新开仓 / Record new position"""
        side = "long" if signal.action == Action.OPEN_LONG else "short"
        entry_price = signal.entries[0].price if signal.entries else signal.entry_price
        leverage = signal.entries[0].leverage if signal.entries else (signal.leverage or 20)

        pos = Position(
            symbol=signal.symbol,
            side=side,
            entry_price=entry_price,
            size_usdt=size_usdt,
            leverage=leverage,
            opened_at=datetime.now(timezone.utc).isoformat(),
            raw_text=signal.raw_text[:200],
        )
        self._positions[signal.symbol] = pos
        logger.info(f"[持仓] 新增 {side.upper()} {signal.symbol}  entry={entry_price}  {leverage}x")
        self._save()

    def close(self, symbol: str) -> Optional[Position]:
        """移除持仓，返回已关闭的仓位信息 / Remove position, return closed info"""
        pos = self._positions.pop(symbol, None)
        if pos:
            logger.info(f"[持仓] 关闭 {pos.side.upper()} {symbol}")
            self._save()
        return pos

    def reduce(self, symbol: str, pct: float) -> Optional[Position]:
        """减仓（仅记录日志，不修改 size，因为无精确成交回报）/ Reduce (log only, no size update)"""
        pos = self._positions.get(symbol)
        if pos:
            logger.info(f"[持仓] 减仓 {symbol}  {pct}%  剩余持仓继续追踪")
        return pos

    # ------------------------------------------------------------------
    # 推断无币种信号的 symbol / Infer symbol when signal has no symbol
    # ------------------------------------------------------------------

    async def resolve_symbol(self, text: str, parser) -> Optional[str]:
        """
        当信号无明确币种时，推断操作的是哪个仓位：
        - 无持仓 → 返回 None
        - 只有 1 个持仓 → 直接返回该 symbol
        - 多个持仓 → 先交给 AI 根据消息内容判断，AI 无法判断时回退到最近开仓
        Infer target position when symbol unknown. None if no positions; AI if multiple.
        """
        if not self._positions:
            logger.warning("[持仓] 收到无币种信号但当前无记录持仓，无法推断 symbol")
            return None

        if len(self._positions) == 1:
            symbol = next(iter(self._positions))
            logger.info(f"[持仓] 自动推断 symbol={symbol}（唯一持仓）")
            return symbol

        # 多个持仓：先让 AI 根据消息内容判断 / Multiple positions: let AI infer from text
        positions = list(self._positions.values())
        logger.info(f"[持仓] 多个持仓（{len(positions)} 个），交由 AI 判断目标仓位...")
        resolved = await parser.resolve_position(text, positions)
        if resolved:
            return resolved

        # AI 无法判断 → 回退到最近开仓 / AI failed → fallback to latest opened
        latest = max(positions, key=lambda p: p.opened_at)
        logger.warning(
            f"[持仓] AI 无法判断，回退到最近开仓: {latest.symbol}"
            f"（开仓于 {latest.opened_at[:19]}）"
        )
        return latest.symbol

    # ------------------------------------------------------------------
    # 查询 / Query
    # ------------------------------------------------------------------

    def get(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    def all(self) -> List[Position]:
        return list(self._positions.values())

    def summary(self) -> str:
        if not self._positions:
            return "[持仓] 当前无持仓"
        lines = ["[持仓] 当前持仓："]
        for pos in self._positions.values():
            lines.append(
                f"  {pos.side.upper()} {pos.symbol}"
                f"  entry={pos.entry_price or '市价'}"
                f"  {pos.leverage}x"
                f"  开仓: {pos.opened_at[:19]}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 持久化 / Persistence
    # ------------------------------------------------------------------

    def _save(self) -> None:
        try:
            data = {k: asdict(v) for k, v in self._positions.items()}
            with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[持仓] 保存失败: {e}")

    def _load(self) -> None:
        if not os.path.exists(POSITIONS_FILE):
            return
        try:
            with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._positions = {k: Position(**v) for k, v in data.items()}
            if self._positions:
                logger.info(f"[持仓] 从文件恢复 {len(self._positions)} 个持仓")
                logger.info(self.summary())
        except Exception as e:
            logger.error(f"[持仓] 加载持仓文件失败: {e}")
