"""
Binance 合约 API 连通性测试（只读，不下单）
运行: python test/bn_test_api.py
"""
import os
import sys

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

from binance.client import Client
from binance.exceptions import BinanceAPIException

API_KEY    = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

print(f"API Key : {API_KEY[:12]}...")
print("-" * 50)

import time

client = Client(api_key=API_KEY, api_secret=API_SECRET)

# ── 1. 服务器时间 & 自动校准偏移 ──────────────────────
print("[1] 获取服务器时间（自动校准时间偏移）...")
try:
    server_ts = client.futures_time()["serverTime"]
    local_ts  = int(time.time() * 1000)
    offset    = server_ts - local_ts
    client.timestamp_offset = offset
    print(f"    [OK] serverTime = {server_ts}  | 本地时间偏差 = {offset} ms（已校准）")
except Exception as e:
    print(f"    [ERR] {e}")

# ── 2. 账户 USDT 余额 ────────────────────────────────
print("[2] 查询合约账户 USDT 余额...")
try:
    balances = client.futures_account_balance()
    usdt = next((b for b in balances if b["asset"] == "USDT"), None)
    if usdt:
        print(f"    [OK] 可用余额 = {usdt['availableBalance']} USDT"
              f"  | 总余额 = {usdt['balance']} USDT")
    else:
        print("    [WARN] 未找到 USDT 资产")
except BinanceAPIException as e:
    print(f"    [ERR] Binance [{e.status_code}]: {e.message}")
except Exception as e:
    print(f"    [ERR] {e}")

# ── 3. BTC 标记价格 ──────────────────────────────────
SYMBOL = "BTCUSDT"
print(f"[3] 获取 {SYMBOL} 标记价格...")
try:
    mp = client.futures_mark_price(symbol=SYMBOL)
    print(f"    [OK] markPrice = {mp['markPrice']}"
          f"  | indexPrice = {mp['indexPrice']}")
except Exception as e:
    print(f"    [ERR] {e}")

# ── 4. 当前持仓（BTC） ───────────────────────────────
print(f"[4] 查询 {SYMBOL} 持仓信息...")
try:
    positions = client.futures_position_information(symbol=SYMBOL)
    matched = [p for p in positions if p["symbol"] == SYMBOL]
    if matched:
        for p in matched:
            print(f"    [OK] positionAmt={p['positionAmt']}"
                  f"  | entryPrice={p['entryPrice']}"
                  f"  | unrealizedProfit={p['unRealizedProfit']}"
                  f"  | side={p['positionSide']}")
    else:
        print(f"    [OK] 当前无 {SYMBOL} 持仓记录")
except Exception as e:
    print(f"    [ERR] {e}")

# ── 5. 未成交挂单 ────────────────────────────────────
print(f"[5] 查询 {SYMBOL} 未成交订单...")
try:
    orders = client.futures_get_open_orders(symbol=SYMBOL)
    if orders:
        for o in orders:
            print(f"    [OK] orderId={o['orderId']}  type={o['type']}"
                  f"  side={o['side']}  price={o['price']}  qty={o['origQty']}")
    else:
        print(f"    [OK] 暂无未成交挂单")
except Exception as e:
    print(f"    [ERR] {e}")

print("-" * 50)
print("测试完成。")
