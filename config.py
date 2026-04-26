# config.py
# Reads app settings from Render environment variables or a local .env file.

from dotenv import load_dotenv
import os

load_dotenv()

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "mysecret123")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "change_this_password")
TRADING_MODE = os.getenv("TRADING_MODE", "paper")

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")

STATE_FILE = "state.json"
DB_FILE = "trading_bot.db"
LOG_FILE = "trades.log"

DEFAULT_STATE = {
    "trading_enabled": True,
    "maintenance_mode": False,
    "asset_mode": "stocks",  # stocks, crypto, or both
    "allowed_symbols": ["AAPL", "TSLA", "SPY", "BTC/USD", "ETH/USD"],
    "blocked_symbols": [],
    "trade_count": 0,
    "last_trade_time": None,
    "last_reset_date": None,
    "last_webhook_time": None,
    "last_webhook_symbol": None,
    "last_webhook_action": None,
    "error_count": 0,
    "last_error_time": None,
    "last_error_message": None,
    "max_dollars_per_trade": 200.0,
    "max_shares_per_trade": 5,
    "max_trades_per_day": 5,
    "daily_loss_limit": 50.0,
    "take_profit_percent": 1.0,
    "stop_loss_percent": 1.0,
}
