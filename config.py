# config.py
import os
from dotenv import load_dotenv

load_dotenv()

# OANDA
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
OANDA_TRADING_MODE = os.getenv("OANDA_TRADING_MODE", "demo").lower()

if OANDA_TRADING_MODE == "live":
    OANDA_BASE_URL = "https://api-fxtrade.oanda.com/v3"
else:
    OANDA_BASE_URL = "https://api-fxpractice.oanda.com/v3"

# Security
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "mysecret123")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "change_this_password")

# Trading mode
TRADING_MODE = os.getenv("TRADING_MODE", "paper").lower()

# Strategy settings
STRATEGY_ENABLED = os.getenv("STRATEGY_ENABLED", "true").lower() == "true"
STRATEGY_TYPE = os.getenv("STRATEGY_TYPE", "ema_crossover")
STRATEGY_TIMEFRAME = os.getenv("STRATEGY_TIMEFRAME", "5")

FAST_EMA_PERIOD = int(os.getenv("FAST_EMA_PERIOD", "9"))
SLOW_EMA_PERIOD = int(os.getenv("SLOW_EMA_PERIOD", "21"))

# These fix your RSI import error
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
RSI_OVERSOLD = int(os.getenv("RSI_OVERSOLD", "30"))
RSI_OVERBOUGHT = int(os.getenv("RSI_OVERBOUGHT", "70"))

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Email
EMAIL_FROM = os.getenv("EMAIL_FROM", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_TO = os.getenv("EMAIL_TO", "")

# Files
STATE_FILE = "state.json"
LOG_FILE = "trades.log"
DB_FILE = "trading_bot.db"
BOT_VERSION = "2.0.0"

# Default bot state
DEFAULT_STATE = {
    "trade_count": 0,
    "last_trade_time": None,
    "last_reset_date": None,
    "trading_enabled": False,
    "maintenance_mode": False,
    "live_confirmed": False,
    "last_telegram_update_id": 0,
    "error_count": 0,
    "last_error_time": None,
    "last_error_message": None,
    "last_webhook_time": None,
    "last_webhook_symbol": None,
    "last_webhook_action": None,
    "heartbeat_warning_sent": False,
    "last_market_status": None,
    "paused_until": None,
    "asset_mode": "forex",
    "allowed_symbols": ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF"],
    "blocked_symbols": [],
    "max_units_per_trade": 10000,
    "max_trades_per_day": 5,
    "daily_loss_limit": 50.0,
    "take_profit_pips": 50,
    "stop_loss_pips": 50,
    "minimum_trade_score": 70,
    "last_strategy_check": None,
    "last_signal": None,
    "position_open": False,
    "current_position_symbol": None,
    "force_forex_trading": False,
}
