# config.py - UPDATED WITH BETTER DEFAULTS
import os
from dotenv import load_dotenv

load_dotenv()

# ========== OANDA CREDENTIALS ==========
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
OANDA_TRADING_MODE = os.getenv("OANDA_TRADING_MODE", "demo")

# ========== SECURITY ==========
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD")

# ========== TRADING MODE ==========
TRADING_MODE = os.getenv("TRADING_MODE", "paper").lower()

# ========== AUTONOMOUS STRATEGY SETTINGS ==========
STRATEGY_ENABLED = os.getenv("STRATEGY_ENABLED", "true").lower() == "true"
STRATEGY_TYPE = os.getenv("STRATEGY_TYPE", "ema_crossover")
STRATEGY_TIMEFRAME = os.getenv("STRATEGY_TIMEFRAME", "5")
FAST_EMA_PERIOD = int(os.getenv("FAST_EMA_PERIOD", "5"))
SLOW_EMA_PERIOD = int(os.getenv("SLOW_EMA_PERIOD", "13"))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
RSI_OVERSOLD = int(os.getenv("RSI_OVERSOLD", "30"))
RSI_OVERBOUGHT = int(os.getenv("RSI_OVERBOUGHT", "70"))

# Validate required variables
required_vars = {
    "WEBHOOK_SECRET": WEBHOOK_SECRET,
    "DASHBOARD_PASSWORD": DASHBOARD_PASSWORD,
    "OANDA_API_KEY": OANDA_API_KEY,
    "OANDA_ACCOUNT_ID": OANDA_ACCOUNT_ID,
}

missing = [name for name, value in required_vars.items() if not value]
if missing:
    raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

if TRADING_MODE not in ["paper", "live"]:
    raise ValueError(f"TRADING_MODE must be 'paper' or 'live', got '{TRADING_MODE}'")

if OANDA_TRADING_MODE not in ["demo", "live"]:
    raise ValueError(f"OANDA_TRADING_MODE must be 'demo' or 'live', got '{OANDA_TRADING_MODE}'")

# ========== TELEGRAM, EMAIL, FILES (unchanged) ==========
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_TO = os.getenv("EMAIL_TO", "")

STATE_FILE = "state.json"
LOG_FILE = "trades.log"
DB_FILE = "trading_bot.db"
BOT_VERSION = "2.1.0"

# ========== DEFAULT STATE ==========
DEFAULT_STATE = {
    "trade_count": 0,
    "last_trade_time": None,
    "last_reset_date": None,
    "trading_enabled": True,           # ← Changed to True
    "maintenance_mode": False,
    "live_confirmed": False,
    "asset_mode": "forex",
    "allowed_symbols": ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF"],
    "max_units_per_trade": 10000,
    "max_trades_per_day": 6,
    "daily_loss_limit": 50.0,
    "take_profit_pips": 40,
    "stop_loss_pips": 40,
    "minimum_trade_score": 60,
    "position_open": False,
    "current_position_symbol": None,
    "force_forex_trading": True,       # ← Force override ON by default
    # ... (keep all your other default keys from original file)
}