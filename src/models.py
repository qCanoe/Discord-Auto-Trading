from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List


class Action(str, Enum):
    OPEN_LONG = "open_long"
    OPEN_SHORT = "open_short"
    CLOSE = "close"
    REDUCE = "reduce"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


@dataclass
class EntryOrder:
    """单个入场委托（一条信号可能含多个入场点位）"""
    order_type: OrderType
    price: Optional[float]          # market 时为 None
    leverage: int = 20              # 杠杆倍数
    margin_pct: Optional[float] = None  # 保证金占余额百分比（2% / 3% 等）


@dataclass
class TakeProfit:
    """单个止盈目标"""
    price: float
    close_pct: Optional[float] = None   # 该止盈档位平仓比例（50 = 50%），None 表示全平


@dataclass
class Signal:
    action: Action
    symbol: str                             # e.g. BTCUSDT
    entries: List[EntryOrder] = field(default_factory=list)
    take_profits: List[TakeProfit] = field(default_factory=list)
    sl: Optional[float] = None              # stop loss
    # 兼容旧字段（单一入场）
    order_type: OrderType = OrderType.MARKET
    entry_price: Optional[float] = None
    tp: Optional[float] = None
    size_usdt: Optional[float] = None
    size_pct: Optional[float] = None
    reduce_pct: Optional[float] = None
    leverage: Optional[int] = None          # 全局杠杆（entries 为空时使用）
    raw_text: str = ""

    def __post_init__(self):
        if isinstance(self.action, str):
            self.action = Action(self.action)
        if isinstance(self.order_type, str):
            self.order_type = OrderType(self.order_type)
        self.symbol = self.symbol.upper().replace("/", "").replace("-", "")
        if not self.symbol.endswith("USDT"):
            self.symbol = self.symbol + "USDT"
        # 规范化 entries 中的 OrderType
        for e in self.entries:
            if isinstance(e.order_type, str):
                e.order_type = OrderType(e.order_type)

    def is_open(self) -> bool:
        return self.action in (Action.OPEN_LONG, Action.OPEN_SHORT)

    def is_close(self) -> bool:
        return self.action == Action.CLOSE

    def is_reduce(self) -> bool:
        return self.action == Action.REDUCE

    def side(self) -> str:
        """返回 Binance API 的 side 参数"""
        if self.action == Action.OPEN_LONG:
            return "BUY"
        if self.action == Action.OPEN_SHORT:
            return "SELL"
        if self.action == Action.CLOSE:
            return "SELL"   # 平多；平空由 executor 根据实际持仓方向决定
        if self.action == Action.REDUCE:
            return "SELL"
        return "BUY"

    def summary(self) -> str:
        """可读摘要，用于日志与测试输出"""
        lines = [
            f"[Signal] {self.action.value.upper()}  {self.symbol}",
        ]
        if self.entries:
            for i, e in enumerate(self.entries, 1):
                price_str = f"@{e.price}" if e.price else "市价"
                margin_str = f"  保证金{e.margin_pct}%" if e.margin_pct is not None else ""
                lines.append(f"  入场#{i}: {e.order_type.value} {price_str}  {e.leverage}x{margin_str}")
        elif self.entry_price:
            lines.append(f"  入场: limit @{self.entry_price}  {self.leverage or '?'}x")
        else:
            lines.append(f"  入场: 市价  {self.leverage or '?'}x")

        if self.take_profits:
            for i, tp in enumerate(self.take_profits, 1):
                close_str = f"  平{tp.close_pct}%" if tp.close_pct is not None else ""
                lines.append(f"  止盈#{i}: {tp.price}{close_str}")
        elif self.tp:
            lines.append(f"  止盈: {self.tp}")

        if self.sl:
            lines.append(f"  止损: {self.sl}")
        return "\n".join(lines)
