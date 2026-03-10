"""
Discord 频道消息监听器 / Discord Channel Listener
监听指定频道的新消息并打印到控制台
Listen to channel messages and print to console
断线重连后自动补偿丢失消息 / Catch up missed messages after reconnect
"""

import os
import logging
import discord
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("listener")

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

client = discord.Client()

# 记录最后处理的消息 ID，用于断线重连后补偿历史消息
# Last message ID for catch-up after reconnect
last_message_id = None
# 服务启动时间，last_message_id 为 None 时用此时间做补偿起点
# Start time, used as catch-up base when last_message_id is None
start_time: datetime = None


def format_message(message, prefix=""):
    author = message.author.name
    content = message.content or "[无文字内容]"

    attachments = ""
    if message.attachments:
        urls = [a.url for a in message.attachments]
        attachments = f"\n  附件: {', '.join(urls)}"

    embeds = ""
    if message.embeds:
        embed_texts = []
        for e in message.embeds:
            parts = []
            if e.title:
                parts.append(f"标题: {e.title}")
            if e.description:
                parts.append(f"描述: {e.description}")
            embed_texts.append(" | ".join(parts))
        embeds = f"\n  Embed: {' / '.join(embed_texts)}"

    return f"{prefix}[{message.created_at.strftime('%H:%M:%S')}] {author}: {content}{attachments}{embeds}"


async def catchup_missed_messages(channel):
    """断线重连后，拉取断线期间错过的消息 / Fetch missed messages after reconnect"""
    global last_message_id, start_time

    missed = []
    if last_message_id is not None:
        # 有已知消息 ID，从它之后拉取 / Fetch after known message ID
        async for msg in channel.history(after=discord.Object(id=last_message_id), limit=50):
            missed.append(msg)
    elif start_time is not None:
        # 从未收到过消息，用启动时间做起点（捕获启动后到现在的漏接消息）
        # No messages yet, use start time as base
        async for msg in channel.history(after=start_time, limit=50):
            missed.append(msg)

    if missed:
        log.warning("断线期间共漏接 %d 条消息：", len(missed))
        for msg in sorted(missed, key=lambda m: m.created_at):
            log.warning(format_message(msg, prefix="[漏] "))
            last_message_id = msg.id


@client.event
async def on_ready():
    global start_time
    channel = client.get_channel(CHANNEL_ID)
    if channel:
        log.info("已连接，正在监听频道: #%s  (ID: %s)", channel.name, CHANNEL_ID)
        if start_time is None:
            # 第一次连接，记录启动时间 / First connect, record start time
            start_time = datetime.now(timezone.utc)
        else:
            # 全新重连（RESUME 失败后降级），尝试补偿漏接消息
            # Full reconnect (after RESUME fail), try catch-up
            await catchup_missed_messages(channel)
    else:
        log.warning("已连接，但找不到频道 %s，请检查 CHANNEL_ID 是否正确", CHANNEL_ID)


@client.event
async def on_resumed():
    """WebSocket RESUME 成功后，主动补偿断线期间可能丢失的消息
    After RESUME success, catch up messages possibly lost during disconnect"""
    log.info("Gateway RESUMED，正在检查是否有漏接消息...")
    channel = client.get_channel(CHANNEL_ID)
    if channel:
        await catchup_missed_messages(channel)


@client.event
async def on_message(message):
    global last_message_id

    if message.channel.id != CHANNEL_ID:
        return

    last_message_id = message.id
    log.info(format_message(message))


@client.event
async def on_message_edit(before, after):
    """捕获消息编辑，避免漏掉被修改的信号 / Catch edits to avoid missing modified signals"""
    global last_message_id

    if after.channel.id != CHANNEL_ID:
        return
    if before.content == after.content:
        return

    last_message_id = after.id
    log.info("[编辑] %s", format_message(after))
    log.info("  原文: %s", before.content or "[无文字内容]")


client.run(TOKEN)
