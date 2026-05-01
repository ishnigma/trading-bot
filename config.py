# config.py - UPDATED FOR OANDA
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

# ========== TELEGRAM (OPTIONAL) ==========
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ========== EMAIL (OPTIONAL) ==========
EMAIL_FROM = os.getenv("EMAIL_FROM", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_TO = os.getenv("EMAIL_TO", "")

# ========== FILE NAMES ==========
STATE_FILE = "state.json"
LOG_FILE = "trades.log"
DB_FILE = "trading_bot.db"
BOT_VERSION = "2.0.0"  # OANDA version

# ========== DEFAULT STATE ==========
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
    "asset_mode": "forex",  # Changed from stocks to forex
    "allowed_symbols": ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF"],
    "blocked_symbols": [],
    "max_units_per_trade": 10000,  # OANDA uses units instead of dollars/shares
    "max_trades_per_day": 5,
    "daily_loss_limit": 50.0,
    "take_profit_pips": 50,  # Pips instead of percent
    "stop_loss_pips": 50,
    "minimum_trade_score": 70,
    "paper_only_strategies": ["test_strategy"],
    "approved_live_strategies": ["ema_rsi_v1"],
    "strategy_max_units": {"ema_rsi_v1": 10000},
    "strategy_bracket_settings": {
        "ema_rsi_v1": {"take_profit_pips": 50, "stop_loss_pips": 50}
    },
    "strategy_trade_counts": {},
    "strategy_max_trades_per_day": {"ema_rsi_v1": 3},
    "disabled_strategies": [],
    "strategy_daily_loss_limit": {"ema_rsi_v1": 25.0},
    "strategy_notes": {"ema_rsi_v1": "EMA trend with RSI filter"},
    "strategy_tags": {"ema_rsi_v1": ["trend", "rsi"]},
    "strategy_presets": {},
    "last_backtest_time": None,
    "last_backtest_status": None
}