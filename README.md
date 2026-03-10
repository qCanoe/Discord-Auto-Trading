# Autotrade

Discord 消息 → AI 解析 → Binance U本位合约跟单

## 流程

1. 监听指定 Discord 频道的交易信号
2. 使用 OpenRouter AI 解析自然语言为结构化信号
3. 调用 Binance 合约 API 执行开多/开空/平仓/减仓
4. 成交结果通过 Webhook 推送到交易日志频道

## 环境变量

| 变量 | 说明 |
|-----|------|
| `DISCORD_TOKEN` | Discord 用户 Token（监听信号频道） |
| `CHANNEL_ID` | 监听频道的 ID |
| `TRADE_LOG_WEBHOOK_URL` | 交易日志 Webhook URL |
| `OPENROUTER_API_KEY` | OpenRouter API Key |
| `OPENROUTER_MODEL` | 解析模型，如 `anthropic/claude-sonnet-4.6` |
| `BINANCE_API_KEY` | Binance 合约 API Key |
| `BINANCE_API_SECRET` | Binance 合约 API Secret |
| `DEFAULT_LEVERAGE` | 默认杠杆倍数 |
| `DEFAULT_SIZE_PCT` | 默认开仓占余额百分比 |
| `DRY_RUN` | `true` 只解析不下单，`false` 正式交易 |

## 本地运行

```bash
pip install -r requirements.txt
# 创建 .env 并填入配置
python main.py
```

## 部署到 Render

1. 连接 GitHub 仓库
2. 选择 Blueprint，Render 自动读取 `render.yaml`
3. 在 Dashboard 填写 `sync: false` 的密钥
4. 将服务部署到 **Singapore** 区域（避免 Binance 美国限制）
5. Binance API 需添加 Render 出口 IP 白名单

## 测试

```bash
python test/bn_test_api.py      # Binance API 连通性
python test/test_parser.py      # 信号解析（需 VPN，OpenRouter 地区限制）
```
