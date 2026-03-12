"""
Binance U本位合约执行器 / Binance USDT-M Futures Executor
支持：开多/开空、全平仓、减仓、TP/SL 挂单
Supports: long/short, full close, reduce, TP/SL orders
"""

import os
import math
import time
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
        self.default_size_pct = float(os.getenv("DEFAULT_SIZE_PCT", "10"))

        # 校准时间戳偏移，防止 -1021 错误 / Calibrate timestamp offset to avoid -1021
        try:
            server_ts = self.client.futures_time()["serverTime"]
            self.client.timestamp_offset = server_ts - int(time.time() * 1000)
        except Exception:
            pass

        # exchange_info 缓存：weight=40，启动时拉取一次，运行期间复用
        # exchange_info cache: weight=40, fetch once at startup, refresh every 1h
        self._exchange_info_cache: Optional[dict] = None
        self._exchange_info_ts: float = 0
        self._exchange_info_ttl: float = 3600  # 1小时刷新一次 / 1h TTL

        # 检测账户持仓模式：双向(hedge)或单向(one-way)
        # Detect account position mode: hedge (dual) or one-way
        try:
            mode = self.client.futures_get_position_mode()
            self.hedge_mode: bool = mode.get("dualSidePosition", False)
            logger.info(f"持仓模式: {'双向(Hedge)' if self.hedge_mode else '单向(One-Way)'}")
        except Exception:
            self.hedge_mode = False
            logger.warning("无法获取持仓模式，默认使用单向模式")

    # ------------------------------------------------------------------
    # 公开入口 / Public API
    # ------------------------------------------------------------------

    async def execute(self, signal: Signal) -> bool:
        """根据 Signal 执行对应操作，返回是否成功 / Execute from Signal, return success"""
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
    # 内部方法 / Internal methods
    # ------------------------------------------------------------------

    async def _open_position(self, signal: Signal, side: str) -> bool:
        """开仓：设置杠杆 → 下主单(支持多入场) → 挂 TP/SL
        Open: set leverage → main order(s) (multi-entry supported) → TP/SL"""
        symbol = signal.symbol

        # hedge mode 下 positionSide: BUY→LONG, SELL→SHORT
        position_side = ("LONG" if side == "BUY" else "SHORT") if self.hedge_mode else None
        close_side = "SELL" if side == "BUY" else "BUY"

        # 0. 撤销之前的挂单（如信号要求更新）/ Cancel previous orders if signal says so
        if signal.cancel_previous:
            self._cancel_open_orders(symbol)
            logger.info(f"[撤单] 已取消 {symbol} 所有未成交挂单，准备重新下单")

        # ------------------------------------------------------------------
        # 优先使用 signal.entries（多入场点），兼容旧字段单一入场
        # Prefer signal.entries (multi-entry); fall back to legacy single entry
        # ------------------------------------------------------------------
        if signal.entries:
            # 1. 设置杠杆（取第一个入场的杠杆）/ Set leverage from first entry
            leverage = signal.entries[0].leverage
            self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
            logger.info(f"设置杠杆: {symbol} x{leverage}")

            mark = float(self.client.futures_mark_price(symbol=symbol)["markPrice"])
            balance = self._get_usdt_balance()
            total_quantity = 0.0

            # 2. 逐个入场点下单 / Place one order per entry
            for idx, entry in enumerate(signal.entries, 1):
                # 如果该入场点杠杆与已设置的不同，重新设置
                # Re-set leverage if this entry uses a different value
                if entry.leverage != leverage:
                    leverage = entry.leverage
                    self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
                    logger.info(f"更新杠杆: {symbol} x{leverage}")

                # 根据 margin_pct 或全局 size_usdt/size_pct 计算数量
                # Calc quantity from margin_pct or global size fields
                if entry.margin_pct is not None:
                    ref_price = entry.price if entry.price else mark
                    notional = balance * entry.margin_pct / 100 * entry.leverage
                    qty = self._floor_quantity(notional / ref_price, symbol)
                else:
                    qty = await self._calc_quantity(signal, symbol, leverage=entry.leverage)

                if not qty or qty <= 0:
                    logger.error(f"入场#{idx} 数量计算为 0，跳过")
                    continue

                order_params = {
                    "symbol": symbol,
                    "side": side,
                    "quantity": qty,
                }
                if position_side:
                    order_params["positionSide"] = position_side

                if entry.order_type == OrderType.LIMIT and entry.price:
                    order_params["type"] = "LIMIT"
                    order_params["price"] = entry.price
                    order_params["timeInForce"] = "GTC"
                    order_label = f"限价挂单 @{entry.price}"
                else:
                    order_params["type"] = "MARKET"
                    order_label = "市价单"

                order = self.client.futures_create_order(**order_params)
                logger.info(
                    f"入场#{idx} {order_label}: {side} {qty} {symbol}"
                    f" | orderId={order['orderId']}"
                )
                total_quantity += qty

            if total_quantity <= 0:
                logger.error("所有入场点下单失败")
                return False

            # TP/SL 使用全部入场合计数量 / TP/SL use total entry quantity
            quantity = total_quantity

        else:
            # ------------------------------------------------------------------
            # 旧字段单一入场兼容路径 / Legacy single-entry fallback
            # ------------------------------------------------------------------
            leverage = self.default_leverage
            self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
            logger.info(f"设置杠杆: {symbol} x{leverage}")

            quantity = await self._calc_quantity(signal, symbol)
            if quantity is None or quantity <= 0:
                logger.error("无法计算下单数量")
                return False

            order_params = {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
            }
            if position_side:
                order_params["positionSide"] = position_side

            if signal.order_type == OrderType.LIMIT and signal.entry_price:
                order_params["type"] = "LIMIT"
                order_params["price"] = signal.entry_price
                order_params["timeInForce"] = "GTC"
                order_label = f"限价挂单 @{signal.entry_price}"
            else:
                order_params["type"] = "MARKET"
                order_label = "市价单"

            order = self.client.futures_create_order(**order_params)
            logger.info(f"主单 {order_label}: {side} {quantity} {symbol} | orderId={order['orderId']}")

        # ------------------------------------------------------------------
        # 4. 挂 TP 单（支持多止盈 take_profits，兼容旧字段 tp）
        # Place TP order(s): prefer take_profits list, fall back to legacy tp
        #
        # Binance Futures 不支持 OTOCO 挂单组，但 reduceOnly=True + quantity 的
        # 条件单可以在无仓位时提前挂好：入场成交后自动生效；若价格未经过入场价
        # 而直接触发止损，因为 reduceOnly=True，无仓位时会自动撤单，不会乱开仓。
        # Binance Futures has no OTOCO. Use reduceOnly=True so orders placed before
        # entry fills are safe: they activate once entry fills; if triggered with no
        # position they auto-cancel (never create unintended positions).
        # ------------------------------------------------------------------

        tp_targets = []
        if signal.take_profits:
            # 将 close_pct=None 的档位平均分配剩余百分比
            # Distribute remaining pct evenly across None-pct TP levels
            explicit_pct = sum(tp.close_pct for tp in signal.take_profits if tp.close_pct is not None)
            none_count = sum(1 for tp in signal.take_profits if tp.close_pct is None)
            per_none_pct = (max(0.0, 100.0 - explicit_pct) / none_count) if none_count else 0.0
            for tp in signal.take_profits:
                pct = tp.close_pct if tp.close_pct is not None else per_none_pct
                tp_targets.append((tp.price, pct))
        elif signal.tp:
            tp_targets = [(signal.tp, None)]  # 旧字段：全平 / legacy: close all

        remaining_qty = quantity
        for tp_idx, (tp_price, close_pct) in enumerate(tp_targets, 1):
            is_last = (tp_idx == len(tp_targets))
            try:
                if is_last or close_pct is None:
                    tp_qty = remaining_qty  # 最后一档平剩余全部 / last TP: close remainder
                else:
                    tp_qty = self._floor_quantity(quantity * close_pct / 100, symbol)
                    remaining_qty -= tp_qty

                tp_params = {
                    "symbol": symbol,
                    "side": close_side,
                    "type": "TAKE_PROFIT_MARKET",
                    "stopPrice": tp_price,
                    "quantity": tp_qty,
                    "timeInForce": "GTE_GTC",
                    "workingType": "MARK_PRICE",
                }
                if position_side:
                    tp_params["positionSide"] = position_side
                else:
                    # one-way mode：统一用 reduceOnly=True，兼容限价入场未成交的情况
                    # reduceOnly=True works with pending limit entries too
                    tp_params["reduceOnly"] = True

                self.client.futures_create_order(**tp_params)
                pct_str = f"平{close_pct}%" if close_pct is not None else "全平"
                logger.info(f"TP#{tp_idx} 挂单: {tp_price}  {pct_str}  qty={tp_qty}")
            except BinanceAPIException as e:
                logger.warning(f"TP#{tp_idx} 挂单失败（不影响主单）: {e.message}")

        # ------------------------------------------------------------------
        # 5. 挂 SL 单 / Place SL order
        # 同样使用 reduceOnly=True + quantity，与限价入场单配合不会乱开仓
        # Also use reduceOnly=True so it's safe with pending limit entries
        # ------------------------------------------------------------------
        if signal.sl:
            try:
                sl_params = {
                    "symbol": symbol,
                    "side": close_side,
                    "type": "STOP_MARKET",
                    "stopPrice": signal.sl,
                    "quantity": quantity,
                    "timeInForce": "GTE_GTC",
                    "workingType": "MARK_PRICE",
                }
                if position_side:
                    sl_params["positionSide"] = position_side
                else:
                    sl_params["reduceOnly"] = True
                self.client.futures_create_order(**sl_params)
                logger.info(f"SL 挂单: {signal.sl}")
            except BinanceAPIException as e:
                logger.warning(f"SL 挂单失败（不影响主单）: {e.message}")

        return True

    async def _close_position(self, signal: Signal) -> bool:
        """全平仓：查询当前持仓 → 市价反向全平 / Close: get position → market reverse close"""
        symbol = signal.symbol
        ps = getattr(signal, "position_side", None)  # 可选字段，hedge mode 需要
        position = self._get_position(symbol, position_side=ps)

        if position is None:
            logger.warning(f"没有找到 {symbol} 的持仓")
            return False

        amt = float(position["positionAmt"])
        if amt == 0:
            logger.warning(f"{symbol} 当前无持仓")
            return False

        close_side = "SELL" if amt > 0 else "BUY"
        quantity = abs(amt)

        order_params = {
            "symbol": symbol,
            "side": close_side,
            "type": "MARKET",
            "quantity": quantity,
        }
        if self.hedge_mode:
            # hedge mode 用 positionSide 代替 reduceOnly
            order_params["positionSide"] = position["positionSide"]
        else:
            order_params["reduceOnly"] = True

        order = self.client.futures_create_order(**order_params)
        logger.info(f"全平仓: {close_side} {quantity} {symbol} | orderId={order['orderId']}")

        # 取消未成交的 TP/SL / Cancel open TP/SL
        self._cancel_open_orders(symbol)
        return True

    async def _reduce_position(self, signal: Signal) -> bool:
        """减仓：按百分比减少当前持仓 / Reduce: decrease position by pct"""
        symbol = signal.symbol
        pct = signal.reduce_pct or 50.0  # 默认减仓 50% / default 50%

        ps = getattr(signal, "position_side", None)
        position = self._get_position(symbol, position_side=ps)
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

        order_params = {
            "symbol": symbol,
            "side": close_side,
            "type": "MARKET",
            "quantity": quantity,
        }
        if self.hedge_mode:
            order_params["positionSide"] = position["positionSide"]
        else:
            order_params["reduceOnly"] = True

        order = self.client.futures_create_order(**order_params)
        logger.info(f"减仓 {pct}%: {close_side} {quantity} {symbol} | orderId={order['orderId']}")
        return True

    # ------------------------------------------------------------------
    # 工具方法 / Utilities
    # ------------------------------------------------------------------

    async def _calc_quantity(
        self,
        signal: Signal,
        symbol: str,
        leverage: Optional[int] = None,
    ) -> Optional[float]:
        """根据 size_usdt 或 size_pct 计算合约数量 / Calc quantity from size_usdt or size_pct
        leverage 参数优先于 default_leverage（entries 路径传入 entry.leverage）"""
        lev = leverage if leverage is not None else self.default_leverage
        mark = float(self.client.futures_mark_price(symbol=symbol)["markPrice"])

        if signal.size_usdt:
            notional = signal.size_usdt * lev
        else:
            pct = signal.size_pct if signal.size_pct else self.default_size_pct
            balance = self._get_usdt_balance()
            notional = balance * pct / 100 * lev

        quantity = notional / mark
        return self._floor_quantity(quantity, symbol)

    def _get_position(self, symbol: str, position_side: Optional[str] = None) -> Optional[dict]:
        """获取指定 symbol 的持仓信息
        hedge mode 下传入 position_side('LONG'/'SHORT') 精确匹配；
        one-way mode 下直接返回第一条非零持仓或首条记录。
        """
        positions = self.client.futures_position_information(symbol=symbol)
        if self.hedge_mode:
            for p in positions:
                if p["symbol"] == symbol and p.get("positionSide") == position_side:
                    return p
            return None
        # one-way mode: 优先返回有仓位的条目
        for p in positions:
            if p["symbol"] == symbol and float(p["positionAmt"]) != 0:
                return p
        for p in positions:
            if p["symbol"] == symbol:
                return p
        return None

    def _get_usdt_balance(self) -> float:
        """获取合约账户可用 USDT 余额 / Get futures account USDT balance"""
        balances = self.client.futures_account_balance()
        for b in balances:
            if b["asset"] == "USDT":
                return float(b["availableBalance"])
        return 0.0

    def _cancel_open_orders(self, symbol: str):
        """取消指定 symbol 的所有未成交订单 / Cancel all open orders for symbol"""
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            logger.info(f"已取消 {symbol} 的所有挂单")
        except BinanceAPIException as e:
            logger.warning(f"取消挂单失败: {e.message}")

    def _get_exchange_info(self) -> dict:
        """获取 exchange info，带本地缓存（1小时刷新一次，避免频繁高权重请求）
        Get exchange info with local cache (1h TTL, avoid rate limit)"""
        now = time.time()
        if self._exchange_info_cache is None or (now - self._exchange_info_ts) > self._exchange_info_ttl:
            self._exchange_info_cache = self.client.futures_exchange_info()
            self._exchange_info_ts = now
            logger.debug("已刷新 exchange_info 缓存")
        return self._exchange_info_cache

    def _floor_quantity(self, quantity: float, symbol: str) -> float:
        """根据交易所精度规则向下取整合约数量 / Floor quantity per exchange LOT_SIZE"""
        try:
            info = self._get_exchange_info()
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
