"""
OpenRouter API 连通性测试
运行: python test_api.py
"""
import os
import sys

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI

API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL   = os.getenv("OPENROUTER_MODEL")

print(f"API Key : {API_KEY[:12]}...")
print(f"Model   : {MODEL}")
print("-" * 40)

client = OpenAI(
    api_key=API_KEY,
    base_url="https://openrouter.ai/api/v1",
)

try:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "say hello in one word"}],
        max_tokens=50,
    )
    print(f"[OK]  回复: {resp.choices[0].message.content.strip()}")
except Exception as e:
    print(f"[ERR] {e}")
