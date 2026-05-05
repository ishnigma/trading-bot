services:
  - type: web
    name: trading-bot
    runtime: python
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn app:app --host 0.0.0.0 --port $PORT --workers 1 --log-level info
    envVars:
      # Paper/demo safety defaults.
      - key: TRADING_MODE
        value: paper
      - key: OANDA_TRADING_MODE
        value: demo

      # Add these secrets manually in Render.
      - key: OANDA_API_KEY
        sync: false
      - key: OANDA_ACCOUNT_ID
        sync: false
      - key: DASHBOARD_PASSWORD
        sync: false
      - key: WEBHOOK_SECRET
        sync: false

      # Optional Telegram commands.
      - key: TELEGRAM_BOT_TOKEN
        sync: false
      - key: TELEGRAM_CHAT_ID
        sync: false

      # Autonomous strategy.
      - key: STRATEGY_ENABLED
        value: "true"
      - key: STRATEGY_TYPE
        value: "ema_crossover"
      - key: STRATEGY_TIMEFRAME
        value: "5"
      - key: FAST_EMA_PERIOD
        value: "9"
      - key: SLOW_EMA_PERIOD
        value: "21"
      - key: RSI_PERIOD
        value: "14"
      - key: RSI_OVERSOLD
        value: "30"
      - key: RSI_OVERBOUGHT
        value: "70"
