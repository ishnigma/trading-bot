# config.py - FIXED VERSION
import os
from dotenv import load_dotenv

load_dotenv()

# Require these variables - no defaults for security
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD")
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

# Validate required variables
required_vars = {
    "WEBHOOK_SECRET": WEBHOOK_SECRET,
    "DASHBOARD_PASSWORD": DASHBOARD_PASSWORD,
    "ALPACA_API_KEY": ALPACA_API_KEY,
    "ALPACA_SECRET_KEY": ALPACA_SECRET_KEY
}

missing = [name for name, value in required_vars.items() if not value]
if missing:
    raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

TRADING_MODE = os.getenv("TRADING_MODE", "paper").lower()
if TRADING_MODE not in ["paper", "live"]:
    raise ValueError(f"TRADING_MODE must be 'paper' or 'live', got '{TRADING_MODE}'")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_TO = os.getenv("EMAIL_TO", "")

STATE_FILE = "state.json"
LOG_FILE = "trades.log"
DB_FILE = "trading_bot.db"
BOT_VERSION = "1.0.0"

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
    "asset_mode": "stocks",
    "allowed_symbols": ["AAPL", "TSLA", "SPY", "BTC/USD", "ETH/USD"],
    "blocked_symbols": [],
    "max_dollars_per_trade": 50.0,
    "max_shares_per_trade": 5,
    "max_trades_per_day": 5,
    "daily_loss_limit": 50.0,
    "take_profit_percent": 1.0,
    "stop_loss_percent": 1.0,
    "minimum_trade_score": 70,
    "paper_only_strategies": ["test_strategy"],
    "approved_live_strategies": ["ema_rsi_v1"],
    "strategy_max_dollars": {"ema_rsi_v1": 50.0},
    "strategy_bracket_settings": {
        "ema_rsi_v1": {"take_profit_percent": 1.0, "stop_loss_percent": 1.0}
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
