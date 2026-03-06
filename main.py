"""
Discord 自动交易系统入口
流程：Discord 消息 → AI 解析 → 信号校验 → Binance 合约下单
"""

import os
import asyncio
import logging

import discord
from dotenv import load_dotenv

from src.parser import SignalParser
from src.executor import BinanceExecutor

load_dotenv()

# ------------------------------------------------------------------
# 日志配置
# ------------------------------------------------------------------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

# ------------------------------------------------------------------
# 配置读取
# ------------------------------------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

# ------------------------------------------------------------------
# 全局实例
# ------------------------------------------------------------------
parser = SignalParser()
executor = BinanceExecutor()
client = discord.Client()


# ------------------------------------------------------------------
# Discord 事件
# ------------------------------------------------------------------

@client.event
async def on_ready():
    channel = client.get_channel(CHANNEL_ID)
    name = f"#{channel.name}" if channel else str(CHANNEL_ID)
    logger.info(f"已连接 Discord，监听频道: {name}")
    logger.info("-" * 50)


@client.event
async def on_message(message: discord.Message):
    if message.channel.id != CHANNEL_ID:
        return

    # 忽略自己发送的消息
    if message.author.id == client.user.id:
        return

    text = message.content.strip()
    author = message.author.name

    if not text:
        return

    logger.info(f"收到消息 [{author}]: {text[:120]}")

    # 1. AI 解析
    signal = await parser.parse(text)
    if signal is None:
        return

    # 2. 执行交易
    logger.info(f"准备执行: {signal.action.value} {signal.symbol}")
    success = await executor.execute(signal)

    if success:
        logger.info(f"执行成功: {signal.action.value} {signal.symbol}")
    else:
        logger.error(f"执行失败: {signal.action.value} {signal.symbol}")


# ------------------------------------------------------------------
# 启动
# ------------------------------------------------------------------

if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("请在 .env 中设置 DISCORD_TOKEN")
    if not CHANNEL_ID:
        raise ValueError("请在 .env 中设置 CHANNEL_ID")

    client.run(TOKEN)
