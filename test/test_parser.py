"""
信号解析模拟测试
- 不连接交易所
- 使用真实日志消息作为测试用例
- 通过 OpenRouter AI 解析后打印结构化 Signal 摘要

运行方式：
    python test_parser.py
"""

import asyncio
import logging
import os
import sys

# 强制 stdout 使用 UTF-8，避免 Windows GBK 乱码
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level="INFO",
    format="%(asctime)s [%(levelname)-8s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_parser")

# ── 测试用例：直接来自 3.7_log.txt 的真实消息 ───────────────────────────────
TEST_CASES = [
    {
        "name": "BTC 做空（多入场+更新撤单指令）",
        "text": (
            "BTC  做空（第2单）   仓位思路强平控制95000U及以上\n\n"
            "70500附近市价已经进场            100倍  2%保证金\n"
            "再挂71888（逃命点位）     100倍  3%保证金\n\n"
            "第一止盈68888（或者靠嘴喊70288） 止盈全部\n\n"
            "止损74100\n"
            "之前的单子更新 把之前的单子撤掉 更新这个"
        ),
    },
    {
        "name": "ETH 做空完整信号（市价+限价+多止盈+止损）",
        "text": (
            "ETH  做空   仓位思路强平控制2800以上，仅用50倍\n\n"
            "1975附近市价直接进       100倍  2%保证金\n"
            "再挂2023    100倍  3%保证金\n\n"
            "第一止盈1936（短线1955到了止盈一半上保本损）\n"
            "第二止盈1908\n\n"
            "止损2060"
        ),
    },
    {
        "name": "ETH 做空（同上，第二次发送）",
        "text": (
            "ETH  做空   仓位思路强平控制2800以上\n\n"
            "1975附近市价直接进       100倍  2%保证金\n"
            "再挂2023    100倍  3%保证金\n\n"
            "第一止盈1936（短线1955到了止盈一半上保本损）\n"
            "第二止盈1908\n\n"
            "止损2060"
        ),
    },
    {
        "name": "BTC 对冲评论（应被过滤，无下单信号）",
        "text": (
            "BTC的第二个点位本针对了 对冲ETH空单 双向持仓 严格执行策略 不要墨迹"
        ),
    },
    {
        "name": "BTC 行情分析（应被过滤）",
        "text": (
            "BTC本来可以睡醒逃命 但明显两次被针对差了一些  严格执行策略的止盈止损 不墨迹第二次"
        ),
    },
    {
        "name": "ETH + BTC 行情评论（应被过滤）",
        "text": (
            "以太坊比想象中跌幅更猛，昨天预判跌破2000后有个支撑可以快速回收，"
            "结果睡醒还在2000以下，不过睡醒的1908和1808支撑没有测试。#ETH\n"
            "BTC没有测试67588的支撑，而是差了一丢丢两次，进来看看。#BTC"
        ),
    },
]


async def run_tests():
    from src.parser import SignalParser

    parser = SignalParser()

    passed = 0
    failed = 0

    print("\n" + "=" * 65)
    print("  信号解析模拟测试")
    print("=" * 65)

    for i, case in enumerate(TEST_CASES, 1):
        print(f"\n[{i}/{len(TEST_CASES)}] {case['name']}")
        print("─" * 55)
        print("原始消息:")
        for line in case["text"].split("\n"):
            print(f"  {line}")
        print()

        try:
            signal = await parser.parse(case["text"])

            if signal is None:
                print("  [OK] 解析结果: 非交易信号（已过滤）")
                passed += 1
            else:
                print(signal.summary())
                passed += 1
        except Exception as e:
            print(f"  [ERR] 解析异常: {e}")
            failed += 1

    print("\n" + "=" * 65)
    print(f"  完成: {passed} 通过  {failed} 异常  共 {len(TEST_CASES)} 条")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    if not os.getenv("OPENROUTER_API_KEY"):
        print("[ERROR] 请在 .env 中设置 OPENROUTER_API_KEY")
        exit(1)
    asyncio.run(run_tests())
