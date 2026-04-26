# helpers.py
# Worker functions for dashboard, state, trading safety, and Alpaca integration.

import json
import os
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest

from config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    DB_FILE,
    DEFAULT_STATE,
    LOG_FILE,
    STATE_FILE,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TRADING_MODE,
)

client = TradingClient(
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    paper=(TRADING_MODE != "live"),
)


def load_state():
    # Load saved bot memory, or create default memory.
    if not os.path.exists(STATE_FILE):
        save_state(DEFAULT_STATE.copy())
        return DEFAULT_STATE.copy()

    with open(STATE_FILE, "r") as file:
        state = json.load(file)

    # Add any new keys missing from old state files.
    changed = False
    for key, value in DEFAULT_STATE.items():
        if key not in state:
            state[key] = value
            changed = True
    if changed:
        save_state(state)

    return state


def save_state(state):
    # Save bot memory to disk.
    with open(STATE_FILE, "w") as file:
        json.dump(state, file, indent=2)


def write_log(message):
    # Append a line to the log file.
    timestamp = datetime.now(timezone.utc).isoformat()
    with open(LOG_FILE, "a") as file:
        file.write(f"{timestamp} | {message}\n")


def send_telegram_message(message):
    # Send Telegram alert if configured.
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=10,
        )
    except Exception as error:
        write_log(f"Telegram error: {error}")


def init_database():
    # Create SQLite tables if missing.
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT,
            strategy TEXT,
            symbol TEXT,
            action TEXT,
            qty REAL,
            price REAL,
            order_id TEXT,
            reason TEXT,
            fill_status TEXT,
            filled_qty TEXT,
            filled_avg_price TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def is_crypto_symbol(symbol):
    # Alpaca crypto symbols are usually BTC/USD, ETH/USD, etc.
    return "/" in str(symbol or "")


def is_market_open():
    # Ask Alpaca if the stock market is open.
    clock = client.get_clock()
    return bool(clock.is_open)


def is_inside_stock_trading_window():
    # Conservative stock window: 9:35 AM to 3:45 PM New York time.
    now_et = datetime.now(ZoneInfo("America/New_York"))
    now_minutes = now_et.hour * 60 + now_et.minute
    start_minutes = 9 * 60 + 35
    end_minutes = 15 * 60 + 45
    return start_minutes <= now_minutes <= end_minutes


def get_daily_pnl():
    # Estimate account daily P/L from Alpaca account fields.
    account = client.get_account()
    return float(account.equity) - float(account.last_equity)


def calculate_qty_from_price(price, state):
    # Calculate whole-share quantity from dashboard risk settings.
    account = client.get_account()
    buying_power = float(account.buying_power)
    dollars = float(state.get("max_dollars_per_trade", 200.0))
    dollars = min(dollars, buying_power)
    qty = int(dollars // float(price))
    max_shares = int(state.get("max_shares_per_trade", 5))
    return min(qty, max_shares)


def validate_trade(state, symbol, action, price, qty):
    # Validate trade request before sending to Alpaca.
    symbol = str(symbol or "").upper()
    action = str(action or "").lower()
    crypto_trade = is_crypto_symbol(symbol)
    asset_mode = state.get("asset_mode", "stocks")

    if not state.get("trading_enabled", True):
        raise ValueError("Trading is OFF")

    if state.get("maintenance_mode", False):
        raise ValueError("Maintenance mode is ON")

    if symbol in state.get("blocked_symbols", []):
        raise ValueError("Symbol is blocked")

    if symbol not in state.get("allowed_symbols", []):
        raise ValueError("Symbol not allowed")

    if action not in ["buy", "sell"]:
        raise ValueError("Bad action")

    if float(price) <= 0:
        raise ValueError("Missing or bad price")

    if float(qty) < 1:
        raise ValueError("Quantity is less than 1")

    if asset_mode == "stocks" and crypto_trade:
        raise ValueError("Crypto blocked in stocks mode")

    if asset_mode == "crypto" and not crypto_trade:
        raise ValueError("Stock blocked in crypto mode")

    # Stocks obey market hours. Crypto is allowed 24/7.
    if not crypto_trade:
        if not is_market_open():
            raise ValueError("Stock market is closed")
        if not is_inside_stock_trading_window():
            raise ValueError("Outside stock trading window")

    max_trades = int(state.get("max_trades_per_day", 5))
    if int(state.get("trade_count", 0)) >= max_trades:
        raise ValueError("Max trades reached today")

    daily_loss_limit = float(state.get("daily_loss_limit", 50.0))
    if get_daily_pnl() <= -daily_loss_limit:
        state["trading_enabled"] = False
        save_state(state)
        raise ValueError("Daily loss limit hit")


def build_bracket_order(symbol, qty, action, price, state):
    # Build simple bracket order with TP/SL percentages.
    action = action.lower()
    side = OrderSide.BUY if action == "buy" else OrderSide.SELL
    price = float(price)
    tp = float(state.get("take_profit_percent", 1.0))
    sl = float(state.get("stop_loss_percent", 1.0))

    if action == "buy":
        take_profit_price = round(price * (1 + tp / 100), 2)
        stop_loss_price = round(price * (1 - sl / 100), 2)
    else:
        take_profit_price = round(price * (1 - tp / 100), 2)
        stop_loss_price = round(price * (1 + sl / 100), 2)

    return MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=side,
        time_in_force=TimeInForce.DAY,
        order_class=OrderClass.BRACKET,
        take_profit=TakeProfitRequest(limit_price=take_profit_price),
        stop_loss=StopLossRequest(stop_price=stop_loss_price),
    )


def save_trade_to_db(symbol, action, qty, price, order_id, strategy="unknown", reason=""):
    # Save trade to SQLite.
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO trades (time, strategy, symbol, action, qty, price, order_id, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            strategy,
            symbol,
            action,
            qty,
            price,
            str(order_id),
            reason,
        ),
    )
    conn.commit()
    conn.close()


def get_trades_from_db(limit=50):
    # Read recent trades from SQLite.
    init_database()
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def record_successful_trade(state):
    # Update trade counters after successful order.
    state["trade_count"] = int(state.get("trade_count", 0)) + 1
    state["last_trade_time"] = datetime.now(timezone.utc).isoformat()
    save_state(state)


def record_error(state, message):
    # Save last error for dashboard.
    state["error_count"] = int(state.get("error_count", 0)) + 1
    state["last_error_time"] = datetime.now(timezone.utc).isoformat()
    state["last_error_message"] = str(message)
    save_state(state)
    write_log(f"Error: {message}")
