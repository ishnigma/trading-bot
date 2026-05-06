# config.py
import os
from dotenv import load_dotenv

load_dotenv()

OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
OANDA_TRADING_MODE = os.getenv("OANDA_TRADING_MODE", "demo").lower()

# This fixes your missing OANDA_BASE_URL error
if OANDA_TRADING_MODE == "live":
    OANDA_BASE_URL = "https://api-fxtrade.oanda.com/v3"
else:
    OANDA_BASE_URL = "https://api-fxpractice.oanda.com/v3"

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "mysecret123")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "change_this_password")

TRADING_MODE = os.getenv("TRADING_MODE", "paper").lower()

STRATEGY_ENABLED = os.getenv("STRATEGY_ENABLED", "true").lower() == "true"
STRATEGY_TYPE = os.getenv("STRATEGY_TYPE", "ema_crossover")
STRATEGY_TIMEFRAME = os.getenv("STRATEGY_TIMEFRAME", "5")

FAST_EMA_PERIOD = int(os.getenv("FAST_EMA_PERIOD", "9"))
SLOW_EMA_PERIOD = int(os.getenv("SLOW_EMA_PERIOD", "21"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

EMAIL_FROM = os.getenv("EMAIL_FROM", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_TO = os.getenv("EMAIL_TO", "")

STATE_FILE = "state.json"
LOG_FILE = "trades.log"
DB_FILE = "trading_bot.db"
BOT_VERSION = "2.0.0"

DEFAULT_STATE = {
    "trade_count": 0,
    "trading_enabled": False,
    "maintenance_mode": False,
    "live_confirmed": False,
    "last_telegram_update_id": 0,
    "position_open": False,
    "current_position_symbol": None,
    "asset_mode": "forex",
    "max_trades_per_day": 5,
    "max_units_per_trade": 10000,
    "daily_loss_limit": 50.0,
    "take_profit_pips": 50,
    "stop_loss_pips": 50,
    "minimum_trade_score": 70,
    "last_strategy_check": None,
    "last_signal": None,
    "force_forex_trading": False,
}
