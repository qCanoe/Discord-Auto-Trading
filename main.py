"""
Discord 自动交易系统入口 / Discord Auto-Trading Entry
流程 / Flow: Discord 消息 → AI 解析 → 信号校验 → Binance 合约下单
             Discord msg → AI parse → signal validate → Binance futures order

DRY_RUN=true 时只解析打印信号，不连接交易所
When DRY_RUN=true: parse and log only, no exchange connection
"""

import os
import asyncio
import hashlib
import logging
import time

import discord
from dotenv import load_dotenv

from src.parser import SignalParser
from src.position_tracker import PositionTracker
from src.trade_logger import TradeLogger

load_dotenv()

# ------------------------------------------------------------------
# 日志配置 / Logging config
# ------------------------------------------------------------------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("discord.gateway").setLevel(logging.WARNING)
logger = logging.getLogger("main")

# ------------------------------------------------------------------
# 配置读取 / Config
# ------------------------------------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# ------------------------------------------------------------------
# 全局实例 / Global instances
# ------------------------------------------------------------------
parser = SignalParser()
tracker = PositionTracker()

if not DRY_RUN:
    from src.executor import BinanceExecutor
    executor = BinanceExecutor()
else:
    executor = None

logger.info("=" * 50)
if DRY_RUN:
    logger.info("  模式: DRY RUN  ——  只解析信号，不执行下单")
else:
    logger.info("  模式: LIVE      ——  真实下单模式已启用")
logger.info("=" * 50)

client = discord.Client()
trade_logger = TradeLogger()

# 去重缓存：content_hash -> 首次收到的时间戳 / Dedup cache: hash -> first-seen timestamp
_DEDUP_TTL = 60          # 秒内相同原始文本视为重复 / Seconds within which identical text is a duplicate
_seen_hashes: dict[str, float] = {}

# 信号指纹去重：防止不同措辞的相同策略重复执行 / Signal-level dedup: same strategy, different wording
_SIGNAL_DEDUP_TTL = int(os.getenv("SIGNAL_DEDUP_TTL", "86400"))  # 默认 24 小时 / Default 24 h
_seen_signal_fps: dict[str, float] = {}


def _signal_fingerprint(signal) -> str:
    """
    基于信号关键字段生成指纹，用于去重。
    Generate fingerprint from key signal fields for deduplication.
    使用入场价列表（排序后）+ SL + action + symbol，忽略止盈差异（止盈可能逐步补充）。
    Uses sorted entry prices + SL + action + symbol; ignores TPs (may be added incrementally).
    """
    entry_prices = sorted(e.price or 0 for e in signal.entries)
    key = f"{signal.action.value}|{signal.symbol}|{entry_prices}|{signal.sl}"
    return hashlib.md5(key.encode()).hexdigest()


# ------------------------------------------------------------------
# Discord 事件 / Discord events
# ------------------------------------------------------------------

@client.event
async def on_ready():
    channel = client.get_channel(CHANNEL_ID)
    name = f"#{channel.name}" if channel else str(CHANNEL_ID)
    mode = "[DRY RUN]" if DRY_RUN else "[LIVE]"
    logger.info(f"{mode} 已连接 Discord，监听频道: {name}")
    logger.info("-" * 50)


@client.event
async def on_message(message: discord.Message):
    if message.channel.id != CHANNEL_ID:
        return

    if message.author.id == client.user.id:
        return

    text = message.content.strip()
    author = message.author.name

    if not text:
        return

    # 原始文本去重 / Raw-text dedup
    _now = time.monotonic()
    _expired = [h for h, t in _seen_hashes.items() if _now - t > _DEDUP_TTL]
    for h in _expired:
        del _seen_hashes[h]
    _hash = hashlib.md5(text.encode()).hexdigest()
    if _hash in _seen_hashes:
        logger.info(f"[去重] 忽略重复消息 [{author}]: {text[:60]}")
        return
    _seen_hashes[_hash] = _now

    logger.info(f"收到消息 [{author}]: {text[:120]}")

    # 1. AI 解析 / AI parse
    signal = await parser.parse(text)
    if signal is None:
        return

    # 2. 无币种信号 → 从持仓推断 symbol / No symbol → infer from positions
    if signal.symbol == "UNKNOWNUSDT":
        from src.models import Action as _Action
        resolved = await tracker.resolve_symbol(text, parser)
        if resolved is None:
            if signal.action in (_Action.OPEN_LONG, _Action.OPEN_SHORT):
                signal.symbol = "BTCUSDT"
                logger.info("[持仓] 开仓信号无币种且无持仓记录，默认使用 BTCUSDT")
            else:
                logger.warning("无法推断币种，当前无持仓记录，跳过该信号")
                return
        else:
            signal.symbol = resolved

    # 3. 打印解析结果 / Log parsed result
    logger.info(f"\n{signal.summary()}")

    # 信号指纹去重（仅对开仓信号）：防止不同措辞的相同策略重复执行
    # Signal-level dedup (open signals only): same strategy, different wording
    if signal.is_open():
        _sig_now = time.monotonic()
        _sig_expired = [fp for fp, t in _seen_signal_fps.items() if _sig_now - t > _SIGNAL_DEDUP_TTL]
        for fp in _sig_expired:
            del _seen_signal_fps[fp]
        _fp = _signal_fingerprint(signal)
        if _fp in _seen_signal_fps:
            elapsed = int(_sig_now - _seen_signal_fps[_fp])
            logger.warning(
                f"[去重] 忽略重复策略信号（{elapsed}s 前已执行过相同策略）: "
                f"{signal.action.value} {signal.symbol}"
            )
            return
        _seen_signal_fps[_fp] = _sig_now
    logger.info(tracker.summary())

    if DRY_RUN:
        # DRY RUN 下同步更新持仓状态，方便后续信号推断 / Sync tracker for later inference
        _update_tracker(signal)
        logger.info("[DRY RUN] 信号已解析，跳过下单")
        return

    # 4. 执行交易（仅 DRY_RUN=false 时）/ Execute trade (when DRY_RUN=false)
    success = await executor.execute(signal)
    if success:
        logger.info(f"执行成功: {signal.action.value} {signal.symbol}")
        _update_tracker(signal)
    else:
        logger.error(f"执行失败: {signal.action.value} {signal.symbol}")

    # 5. 推送交易记录到 Discord 日志频道 / Push trade log to Discord via Webhook
    if trade_logger:
        await trade_logger.log(signal, success)


# ------------------------------------------------------------------
# 持仓状态同步 / Position tracker sync
# ------------------------------------------------------------------

def _update_tracker(signal) -> None:
    """根据信号结果更新持仓记录 / Update tracker from signal result"""
    from src.models import Action
    if signal.action in (Action.OPEN_LONG, Action.OPEN_SHORT):
        tracker.open(signal)
    elif signal.action == Action.CLOSE:
        tracker.close(signal.symbol)
    elif signal.action == Action.REDUCE:
        if signal.reduce_pct is not None:
            tracker.reduce(signal.symbol, signal.reduce_pct)


# ------------------------------------------------------------------
# 启动 / Startup
# ------------------------------------------------------------------

if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("请在 .env 中设置 DISCORD_TOKEN")
    if not CHANNEL_ID:
        raise ValueError("请在 .env 中设置 CHANNEL_ID")

    client.run(TOKEN)
