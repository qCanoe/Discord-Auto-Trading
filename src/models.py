from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Action(str, Enum):
    OPEN_LONG = "open_long"
    OPEN_SHORT = "open_short"
    CLOSE = "close"
    REDUCE = "reduce"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


@dataclass
class Signal:
    action: Action
    symbol: str                         # e.g. BTCUSDT
    order_type: OrderType = OrderType.MARKET
    entry_price: Optional[float] = None # limit 价格，market 时为 None
    tp: Optional[float] = None          # take profit
    sl: Optional[float] = None          # stop loss
    size_usdt: Optional[float] = None   # 开仓金额（USDT）
    size_pct: Optional[float] = None    # 占可用余额百分比，与 size_usdt 二选一
    reduce_pct: Optional[float] = None  # action=reduce 时，减仓百分比（0~100）
    raw_text: str = ""                  # 原始消息文本，用于日志

    def __post_init__(self):
        if isinstance(self.action, str):
            self.action = Action(self.action)
        if isinstance(self.order_type, str):
            self.order_type = OrderType(self.order_type)
        self.symbol = self.symbol.upper().replace("/", "").replace("-", "")
        if not self.symbol.endswith("USDT"):
            self.symbol = self.symbol + "USDT"

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
            return "SELL"   # 同上
        return "BUY"
