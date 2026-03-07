"""
Binance U本位合约执行器
支持：开多/开空、全平仓、减仓、TP/SL 挂单
"""

import os
import math
import logging
from typing import Optional

from binance.client import Client
from binance.exceptions import BinanceAPIException

from .models import Signal, Action, OrderType

logger = logging.getLogger(__name__)


class BinanceExecutor:
    def __init__(self):
        self.client = Client(
            api_key=os.getenv("BINANCE_API_KEY"),
            api_secret=os.getenv("BINANCE_API_SECRET"),
        )
        self.default_leverage = int(os.getenv("DEFAULT_LEVERAGE", "10"))
        self.default_size_usdt = float(os.getenv("DEFAULT_SIZE_USDT", "100"))

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------

    async def execute(self, signal: Signal) -> bool:
        """根据 Signal 执行对应操作，返回是否成功"""
        try:
            if signal.action == Action.OPEN_LONG:
                return await self._open_position(signal, side="BUY")
            elif signal.action == Action.OPEN_SHORT:
                return await self._open_position(signal, side="SELL")
            elif signal.action == Action.CLOSE:
                return await self._close_position(signal)
            elif signal.action == Action.REDUCE:
                return await self._reduce_position(signal)
            else:
                logger.warning(f"未知操作类型: {signal.action}")
                return False
        except BinanceAPIException as e:
            logger.error(f"Binance API 错误 [{e.status_code}]: {e.message}")
            return False
        except Exception as e:
            logger.error(f"执行交易时发生未知错误: {e}")
            return False

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    async def _open_position(self, signal: Signal, side: str) -> bool:
        """开仓：设置杠杆 → 下主单 → 挂 TP/SL"""
        symbol = signal.symbol

        # 1. 设置杠杆
        leverage = self.default_leverage
        self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
        logger.info(f"设置杠杆: {symbol} x{leverage}")

        # 2. 计算下单数量
        quantity = await self._calc_quantity(signal, symbol)
        if quantity is None or quantity <= 0:
            logger.error("无法计算下单数量")
            return False

        # 3. 主单
        order_params = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
        }

        if signal.order_type == OrderType.LIMIT and signal.entry_price:
            order_params["type"] = "LIMIT"
            order_params["price"] = signal.entry_price
            order_params["timeInForce"] = "GTC"
        else:
            order_params["type"] = "MARKET"

        order = self.client.futures_create_order(**order_params)
        logger.info(f"主单成交: {side} {quantity} {symbol} | orderId={order['orderId']}")

        # 4. 挂 TP 单
        if signal.tp:
            close_side = "SELL" if side == "BUY" else "BUY"
            try:
                self.client.futures_create_order(
                    symbol=symbol,
                    side=close_side,
                    type="TAKE_PROFIT_MARKET",
                    stopPrice=signal.tp,
                    closePosition=True,
                    timeInForce="GTE_GTC",
                    workingType="MARK_PRICE",
                )
                logger.info(f"TP 挂单: {signal.tp}")
            except BinanceAPIException as e:
                logger.warning(f"TP 挂单失败（不影响主单）: {e.message}")

        # 5. 挂 SL 单
        if signal.sl:
            close_side = "SELL" if side == "BUY" else "BUY"
            try:
                self.client.futures_create_order(
                    symbol=symbol,
                    side=close_side,
                    type="STOP_MARKET",
                    stopPrice=signal.sl,
                    closePosition=True,
                    timeInForce="GTE_GTC",
                    workingType="MARK_PRICE",
                )
                logger.info(f"SL 挂单: {signal.sl}")
            except BinanceAPIException as e:
                logger.warning(f"SL 挂单失败（不影响主单）: {e.message}")

        return True

    async def _close_position(self, signal: Signal) -> bool:
        """全平仓：查询当前持仓 → 市价反向全平"""
        symbol = signal.symbol
        position = self._get_position(symbol)

        if position is None:
            logger.warning(f"没有找到 {symbol} 的持仓")
            return False

        amt = float(position["positionAmt"])
        if amt == 0:
            logger.warning(f"{symbol} 当前无持仓")
            return False

        close_side = "SELL" if amt > 0 else "BUY"
        quantity = abs(amt)

        order = self.client.futures_create_order(
            symbol=symbol,
            side=close_side,
            type="MARKET",
            quantity=quantity,
            reduceOnly=True,
        )
        logger.info(f"全平仓: {close_side} {quantity} {symbol} | orderId={order['orderId']}")

        # 取消未成交的 TP/SL
        self._cancel_open_orders(symbol)
        return True

    async def _reduce_position(self, signal: Signal) -> bool:
        """减仓：按百分比减少当前持仓"""
        symbol = signal.symbol
        pct = signal.reduce_pct or 50.0  # 默认减仓 50%

        position = self._get_position(symbol)
        if position is None:
            logger.warning(f"没有找到 {symbol} 的持仓")
            return False

        amt = float(position["positionAmt"])
        if amt == 0:
            logger.warning(f"{symbol} 当前无持仓")
            return False

        close_side = "SELL" if amt > 0 else "BUY"
        quantity = self._floor_quantity(abs(amt) * pct / 100, symbol)

        if quantity <= 0:
            logger.error("减仓数量计算为 0，跳过")
            return False

        order = self.client.futures_create_order(
            symbol=symbol,
            side=close_side,
            type="MARKET",
            quantity=quantity,
            reduceOnly=True,
        )
        logger.info(f"减仓 {pct}%: {close_side} {quantity} {symbol} | orderId={order['orderId']}")
        return True

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    async def _calc_quantity(self, signal: Signal, symbol: str) -> Optional[float]:
        """根据 size_usdt 或 size_pct 计算合约数量"""
        # 获取当前标记价格
        mark = float(self.client.futures_mark_price(symbol=symbol)["markPrice"])

        if signal.size_usdt:
            notional = signal.size_usdt * self.default_leverage
        elif signal.size_pct:
            balance = self._get_usdt_balance()
            notional = balance * signal.size_pct / 100 * self.default_leverage
        else:
            notional = self.default_size_usdt * self.default_leverage

        quantity = notional / mark
        return self._floor_quantity(quantity, symbol)

    def _get_position(self, symbol: str) -> Optional[dict]:
        """获取指定 symbol 的持仓信息"""
        positions = self.client.futures_position_information(symbol=symbol)
        for p in positions:
            if p["symbol"] == symbol:
                return p
        return None

    def _get_usdt_balance(self) -> float:
        """获取合约账户可用 USDT 余额"""
        balances = self.client.futures_account_balance()
        for b in balances:
            if b["asset"] == "USDT":
                return float(b["availableBalance"])
        return 0.0

    def _cancel_open_orders(self, symbol: str):
        """取消指定 symbol 的所有未成交订单"""
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            logger.info(f"已取消 {symbol} 的所有挂单")
        except BinanceAPIException as e:
            logger.warning(f"取消挂单失败: {e.message}")

    def _floor_quantity(self, quantity: float, symbol: str) -> float:
        """根据交易所精度规则向下取整合约数量"""
        try:
            info = self.client.futures_exchange_info()
            for s in info["symbols"]:
                if s["symbol"] == symbol:
                    for f in s["filters"]:
                        if f["filterType"] == "LOT_SIZE":
                            step = float(f["stepSize"])
                            precision = len(str(step).rstrip("0").split(".")[-1])
                            factor = 10 ** precision
                            return math.floor(quantity * factor) / factor
        except Exception:
            pass
        return round(quantity, 3)
