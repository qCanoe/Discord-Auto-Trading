# Autotrade

Discord messages → AI parsing → Binance USDT-M futures copy trading

## Flow

1. Listen for trading signals in a Discord channel
2. Parse natural language into structured signals via OpenRouter AI
3. Execute long/short/open/close/reduce via Binance Futures API
4. Push trade results to a log channel via Webhook

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DISCORD_TOKEN` | Discord user token (for listening to signal channel) |
| `CHANNEL_ID` | ID of the channel to listen |
| `TRADE_LOG_WEBHOOK_URL` | Webhook URL for trade log channel |
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `OPENROUTER_MODEL` | Model for parsing, e.g. `anthropic/claude-sonnet-4.6` |
| `BINANCE_API_KEY` | Binance Futures API key |
| `BINANCE_API_SECRET` | Binance Futures API secret |
| `DEFAULT_LEVERAGE` | Default leverage |
| `DEFAULT_SIZE_PCT` | Default position size as % of balance |
| `DRY_RUN` | `true` = parse only, no orders; `false` = live trading |

## Local Run

```bash
pip install -r requirements.txt
# Create .env and fill in config
python main.py
```

## Deploy to Render

1. Connect your GitHub repo
2. Use Blueprint so Render reads `render.yaml` automatically
3. Fill in secrets for `sync: false` vars in Dashboard
4. Deploy to **Singapore** region (avoids Binance US restrictions)
5. Add Render outbound IP to Binance API whitelist

## Tests

```bash
python test/bn_test_api.py      # Binance API connectivity
python test/test_parser.py      # Signal parsing (VPN needed for OpenRouter region limits)
```
