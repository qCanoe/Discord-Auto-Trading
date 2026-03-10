"""本地测试：解析下午那批消息，验证 DRY_RUN 模式下信号解析是否正常"""
import asyncio
import logging
import os

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from src.parser import SignalParser

messages = [
    "比特币68000现价全部平仓，距离成本仅200u，不再持有，坚定持有以太坊空单严格分批进场布局，不犹豫不墨迹。#ETH #BTC",
    "成本已经变了，68200，刚才拉到68100，现在68000跑了太磨叽了，不要墨迹。",
    "合约：在合约vip置顶查看，以太坊空单分两个点位布局，前期不懂的话先磨合用100油练习。\n\n现货：今年抄底比特币在5万和38000附近，那不用提前挂知道就行了。",
    "空军的牛市！空头趋势中一切的支撑只能剥头皮，67588今天测试后拉升了500U，估计就是我们这帮人集体挂单拉上去的所以变成了剥头皮支撑。\n\n2026年比特币的底部将在10个月内完成，三马哥提前预判比特币5万U和38888U之间抄底。#BTC",
]

parser = SignalParser()

async def test():
    for i, msg in enumerate(messages, 1):
        preview = msg[:80] + "..." if len(msg) > 80 else msg
        print(f"\n{'='*60}")
        print(f"消息 {i}: {preview}")
        print('='*60)
        signal = await parser.parse(msg)
        if signal:
            print(f"[SIGNAL] {signal.summary()}")
        else:
            print("[None] 非交易信号，parser 返回 None")

asyncio.run(test())
