"""
Discord 频道消息监听器 - 最小测试版
监听指定频道的新消息并打印到控制台
"""

import os
import discord
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

client = discord.Client()


@client.event
async def on_ready():
    channel = client.get_channel(CHANNEL_ID)
    if channel:
        print(f"[OK] 已连接，正在监听频道: #{channel.name}  (ID: {CHANNEL_ID})")
    else:
        print(f"[WARN] 已连接，但找不到频道 {CHANNEL_ID}，请检查 CHANNEL_ID 是否正确")
    print("-" * 50)


@client.event
async def on_message(message):
    # 只处理目标频道的消息
    if message.channel.id != CHANNEL_ID:
        return

    # 格式化输出
    author = f"{message.author.name}"
    content = message.content or "[无文字内容]"

    # 处理附件（图片/文件）
    attachments = ""
    if message.attachments:
        urls = [a.url for a in message.attachments]
        attachments = f"\n  附件: {', '.join(urls)}"

    # 处理 Embed（嵌入卡片）
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

    print(f"[{message.created_at.strftime('%H:%M:%S')}] {author}: {content}{attachments}{embeds}")


client.run(TOKEN)
