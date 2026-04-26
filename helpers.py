# helpers.py
# Worker functions for state, logs, Alpaca, risk checks, database, backups, and alerts.

import csv
import json
import os
import shutil
import sqlite3
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
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
    EMAIL_FROM,
    EMAIL_PASSWORD,
    EMAIL_TO,
    LOG_FILE,
    STATE_FILE,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TRADING_MODE,
)

# Create Alpaca client. paper=True unless TRADING_MODE is live.
client = TradingClient(
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    paper=(TRADING_MODE != "live")
)


def load_state():
    # Create state if missing.
    if not os.path.exists(STATE_FILE):
        save_state(DEFAULT_STATE.copy())
        return DEFAULT_STATE.copy()

    # Read saved state.
    with open(STATE_FILE, "r") as file:
        state = json.load(file)

    # Add missing new keys without overwriting existing settings.
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
    # Append one timestamped line to log file.
    timestamp = datetime.now(timezone.utc).isoformat()
    with open(LOG_FILE, "a") as file:
        file.write(f"{timestamp} | {message}\n")


def send_telegram_message(message):
    # Skip Telegram if not configured.
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    # Send a message using Telegram Bot API.
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(
        url,
        json={"chat_id": TELEGRAM_CHAT_ID, "text": message},
        timeout=10,
    )


def send_daily_email_report(report_text):
    # Skip email if not configured.
    if not EMAIL_FROM or not EMAIL_PASSWORD or not EMAIL_TO:
        return

    # Build and send email.
    msg = EmailMessage()
    msg["Subject"] = "Trading Bot Daily Report"
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.set_content(report_text)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_FROM, EMAIL_PASSWORD)
        smtp.send_message(msg)


def init_database():
    # Create database tables if they do not exist.
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
            order_id TEXT UNIQUE,
            reason TEXT,
            review TEXT,
            trade_score INTEGER,
            fill_status TEXT,
            filled_qty TEXT,
            filled_avg_price TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def save_trade_to_db(symbol, action, qty, price, order_id, strategy, reason, review, trade_score):
    # Save every trade into SQLite.
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR IGNORE INTO trades (
            time, strategy, symbol, action, qty, price, order_id,
            reason, review, trade_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            review,
            int(trade_score),
        ),
    )
    conn.commit()
    conn.close()


def get_trades_from_db(limit=1000):
    # Read recent trades from SQLite.
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_order_status(order_id):
    # Get one order from Alpaca.
    order = client.get_order_by_id(order_id)
    return {
        "id": str(order.id),
        "symbol": order.symbol,
        "status": str(order.status),
        "filled_qty": str(order.filled_qty),
        "filled_avg_price": str(order.filled_avg_price),
    }


def update_db_trade_with_fill(order_id):
    # Sync fill data from Alpaca into SQLite.
    order_info = get_order_status(order_id)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE trades
        SET fill_status = ?, filled_qty = ?, filled_avg_price = ?
        WHERE order_id = ?
        """,
        (
            order_info.get("status"),
            order_info.get("filled_qty"),
            order_info.get("filled_avg_price"),
            str(order_id),
        ),
    )
    conn.commit()
    conn.close()


def sync_recent_orders(limit=10):
    # Update recent orders with latest Alpaca fill status.
    rows = get_trades_from_db(limit)
    for row in rows:
        order_id = row.get("order_id")
        if order_id:
            try:
                update_db_trade_with_fill(order_id)
            except Exception as error:
                write_log(f"Order sync failed for {order_id}: {error}")


def is_market_open():
    # Ask Alpaca whether the market is currently open.
    clock = client.get_clock()
    return bool(clock.is_open)


def get_daily_pnl():
    # Calculate account daily P/L using Alpaca account equity fields.
    account = client.get_account()
    return float(account.equity) - float(account.last_equity)


def calculate_qty_from_price(price, state, strategy="unknown"):
    # Choose dollars per trade from default or strategy-specific settings.
    dollars_per_trade = float(state.get("max_dollars_per_trade", 50.0))
    strategy_limits = state.get("strategy_max_dollars", {})
    if strategy in strategy_limits:
        dollars_per_trade = float(strategy_limits[strategy])

    # Do not exceed buying power.
    account = client.get_account()
    buying_power = float(account.buying_power)
    max_dollars_to_use = min(dollars_per_trade, buying_power)

    # Use whole shares only.
    qty = int(max_dollars_to_use // float(price))

    # Apply max shares rule.
    max_shares = int(state.get("max_shares_per_trade", 5))
    return min(qty, max_shares)


def build_bracket_order(symbol, qty, action, price, state, strategy="unknown"):
    # Build a market bracket order with dashboard TP/SL settings.
    side = OrderSide.BUY if action == "buy" else OrderSide.SELL

    take_profit_percent = float(state.get("take_profit_percent", 1.0))
    stop_loss_percent = float(state.get("stop_loss_percent", 1.0))

    strategy_brackets = state.get("strategy_bracket_settings", {})
    if strategy in strategy_brackets:
        take_profit_percent = float(strategy_brackets[strategy].get("take_profit_percent", take_profit_percent))
        stop_loss_percent = float(strategy_brackets[strategy].get("stop_loss_percent", stop_loss_percent))

    if action == "buy":
        take_profit_price = round(float(price) * (1 + take_profit_percent / 100), 2)
        stop_loss_price = round(float(price) * (1 - stop_loss_percent / 100), 2)
    else:
        # Short side bracket logic is basic. Test in paper before any live use.
        take_profit_price = round(float(price) * (1 - take_profit_percent / 100), 2)
        stop_loss_price = round(float(price) * (1 + stop_loss_percent / 100), 2)

    return MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=side,
        time_in_force=TimeInForce.DAY,
        order_class=OrderClass.BRACKET,
        take_profit=TakeProfitRequest(limit_price=take_profit_price),
        stop_loss=StopLossRequest(stop_price=stop_loss_price),
    )


def reset_daily_state_if_needed(state):
    # Reset daily counters when UTC date changes.
    today = datetime.now(timezone.utc).date().isoformat()
    if state.get("last_reset_date") != today:
        state["trade_count"] = 0
        state["strategy_trade_counts"] = {}
        state["disabled_strategies"] = []
        state["last_reset_date"] = today
        save_state(state)
        write_log("Daily counters reset")


def is_inside_trading_window():
    # Use conservative New York market-time window.
    now_et = datetime.now(ZoneInfo("America/New_York"))
    now_minutes = now_et.hour * 60 + now_et.minute
    start_minutes = 9 * 60 + 35
    end_minutes = 15 * 60 + 45
    return start_minutes <= now_minutes <= end_minutes



def is_crypto_symbol(symbol):
    # Alpaca crypto symbols are usually formatted like BTC/USD or ETH/USD.
    return "/" in str(symbol)


def has_open_position(symbol):
    # Check if Alpaca account already holds this symbol.
    try:
        positions = client.get_all_positions()
        return any(position.symbol == symbol for position in positions)
    except Exception:
        return False


def validate_trade(state, symbol, action, price, qty, strategy="unknown"):
    # Stop trades when maintenance mode is on.
    if state.get("maintenance_mode", False):
        raise ValueError("Maintenance mode is ON")

    # Stop trades when paused.
    paused_until = state.get("paused_until")
    if paused_until:
        if datetime.now(timezone.utc) < datetime.fromisoformat(paused_until):
            raise ValueError("Bot is paused")

    # Stop all trades if disabled.
    if not state.get("trading_enabled", False):
        raise ValueError("Trading is OFF")

    # Extra live-mode lock.
    if TRADING_MODE == "live" and not state.get("live_confirmed", False):
        raise ValueError("Live mode not confirmed")

    # Block paper-only strategies in live mode.
    if TRADING_MODE == "live" and strategy in state.get("paper_only_strategies", []):
        raise ValueError("Strategy is paper-only")

    # Only approved live strategies may trade live.
    if TRADING_MODE == "live" and strategy not in state.get("approved_live_strategies", []):
        raise ValueError("Strategy not approved for live trading")

    # Block disabled strategies.
    if strategy in state.get("disabled_strategies", []):
        raise ValueError("Strategy is disabled")

    # Validate symbol/action/price/qty.
    allowed_symbols = state.get("allowed_symbols", ["AAPL", "TSLA", "SPY"])
    if symbol not in allowed_symbols:
        raise ValueError("Symbol not allowed")

    if symbol in state.get("blocked_symbols", []):
        raise ValueError("Symbol is blocked")

    if action not in ["buy", "sell"]:
        raise ValueError("Bad action")

    if float(price) <= 0:
        raise ValueError("Missing or bad price")

    if float(qty) < 1:
        raise ValueError("Qty is less than 1")

    # Limit trades per day.
    max_trades = int(state.get("max_trades_per_day", 5))
    if int(state.get("trade_count", 0)) >= max_trades:
        raise ValueError("Max trades reached today")

    # Limit trades per strategy.
    strategy_limits = state.get("strategy_max_trades_per_day", {})
    strategy_counts = state.get("strategy_trade_counts", {})
    if strategy in strategy_limits and strategy_counts.get(strategy, 0) >= int(strategy_limits[strategy]):
        raise ValueError("Strategy max trades reached")

    # Cooldown between trades.
    last_trade_time = state.get("last_trade_time")
    if last_trade_time:
        seconds_passed = (datetime.now(timezone.utc) - datetime.fromisoformat(last_trade_time)).total_seconds()
        if seconds_passed < 15 * 60:
            raise ValueError("Cooldown active")

    # Asset mode checks: stocks obey market hours, crypto can run 24/7.
    asset_mode = state.get("asset_mode", "stocks")
    crypto_trade = is_crypto_symbol(symbol)

    if asset_mode == "stocks" and crypto_trade:
        raise ValueError("Crypto blocked in stocks mode")

    if asset_mode == "crypto" and not crypto_trade:
        raise ValueError("Stock blocked in crypto mode")

    # Stock trades only during market hours and safe window.
    if not crypto_trade:
        if not is_market_open():
            raise ValueError("Stock market is closed")

        if not is_inside_trading_window():
            raise ValueError("Outside stock trading window")

    # Crypto trades skip stock market-hour checks.

    # Daily loss check.
    daily_loss_limit = float(state.get("daily_loss_limit", 50.0))
    if get_daily_pnl() <= -daily_loss_limit:
        state["trading_enabled"] = False
        save_state(state)
        send_telegram_message("Trading turned OFF: daily loss limit hit")
        raise ValueError("Daily loss limit hit")

    # Duplicate position check.
    if action == "buy" and has_open_position(symbol):
        raise ValueError("Already holding this symbol")


def handle_successful_trade(state, symbol, action, qty, order_id, strategy):
    # Update counters after successful order submission.
    state["trade_count"] = int(state.get("trade_count", 0)) + 1
    state["last_trade_time"] = datetime.now(timezone.utc).isoformat()
    state["error_count"] = 0

    strategy_counts = state.get("strategy_trade_counts", {})
    strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1
    state["strategy_trade_counts"] = strategy_counts

    save_state(state)
    write_log(f"Trade placed | {symbol} {action} qty={qty} order_id={order_id}")
    send_telegram_message(f"Trade placed\nSymbol: {symbol}\nAction: {action}\nQty: {qty}")


def record_error(state, error_message):
    # Record error and disable trading after too many errors.
    state["error_count"] = int(state.get("error_count", 0)) + 1
    state["last_error_time"] = datetime.now(timezone.utc).isoformat()
    state["last_error_message"] = error_message

    if state["error_count"] >= 3:
        state["trading_enabled"] = False
        send_telegram_message("Trading turned OFF: too many errors")

    save_state(state)
    write_log(f"Error: {error_message}")


def create_trade_review(symbol, action, price, strategy, reason):
    # Simple review text for the journal.
    return (
        f"Strategy: {strategy}. Action: {action}. Symbol: {symbol}. "
        f"Signal price: {price}. Reason: {reason}. "
        "Review: trade passed bot safety checks before order submission."
    )


def create_trade_score(symbol, action, price, strategy, reason, state=None):
    # Simple score from 0 to 100.
    score = 50
    if strategy and strategy != "unknown":
        score += 10
    if reason and reason != "no reason provided":
        score += 10
    if float(price) > 0:
        score += 10
    if action in ["buy", "sell"]:
        score += 10
    if state:
        tags = state.get("strategy_tags", {}).get(strategy, [])
        if "trend" in tags:
            score += 5
        if "scalp" in tags:
            score -= 5
        if int(state.get("trade_count", 0)) > 3:
            score -= 10
    return max(0, min(score, 100))


def get_open_positions():
    # Get current Alpaca positions.
    positions = client.get_all_positions()
    return [
        {
            "symbol": position.symbol,
            "qty": position.qty,
            "market_value": position.market_value,
            "avg_entry_price": position.avg_entry_price,
            "unrealized_pl": position.unrealized_pl,
        }
        for position in positions
    ]


def get_open_orders():
    # Get current open Alpaca orders.
    orders = client.get_orders()
    return [
        {
            "id": str(order.id),
            "symbol": order.symbol,
            "side": str(order.side),
            "qty": order.qty,
            "type": str(order.type),
            "status": str(order.status),
        }
        for order in orders
    ]


def close_all_positions():
    # Emergency close all positions and cancel orders.
    result = client.close_all_positions(cancel_orders=True)
    write_log("Close all positions requested")
    send_telegram_message("Emergency: close all positions requested")
    return result


def close_one_position(symbol):
    # Close one Alpaca position.
    result = client.close_position(symbol)
    write_log(f"Close position requested for {symbol}")
    send_telegram_message(f"Close position requested for {symbol}")
    return result


def cancel_all_orders():
    # Cancel all open Alpaca orders.
    result = client.cancel_orders()
    write_log("Cancel all orders requested")
    send_telegram_message("All open orders cancellation requested")
    return result


def cancel_one_order(order_id):
    # Cancel one Alpaca order.
    result = client.cancel_order_by_id(order_id)
    write_log(f"Cancel order requested: {order_id}")
    send_telegram_message(f"Cancel order requested: {order_id}")
    return result


def make_daily_backup():
    # Backup local files.
    backup_folder = "backups"
    os.makedirs(backup_folder, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    files = ["trading_bot.db", "trades.log", "RESTORE.txt", "CHECKLIST.txt", "CODE_REVIEW.txt"]
    for file_name in files:
        if os.path.exists(file_name):
            shutil.copy(file_name, f"{backup_folder}/{today}_{file_name}")
    write_log("Daily backup completed")


def clean_old_backups():
    # Delete backups older than 30 days.
    backup_folder = "backups"
    if not os.path.exists(backup_folder):
        return
    now = datetime.now(timezone.utc)
    for file_name in os.listdir(backup_folder):
        file_path = os.path.join(backup_folder, file_name)
        if os.path.isfile(file_path):
            file_time = datetime.fromtimestamp(os.path.getmtime(file_path), timezone.utc)
            if (now - file_time).days > 30:
                os.remove(file_path)
    write_log("Old backups cleaned")


def migrate_csv_to_db():
    # Optional migration from old journal.csv.
    if not os.path.exists("journal.csv"):
        return 0
    with open("journal.csv", "r") as file:
        rows = list(csv.DictReader(file))
    imported = 0
    for row in rows:
        save_trade_to_db(
            row.get("symbol"),
            row.get("action"),
            float(row.get("qty") or 0),
            float(row.get("price") or 0),
            row.get("order_id"),
            row.get("strategy", "unknown"),
            row.get("reason", ""),
            row.get("review", ""),
            int(row.get("trade_score") or 0),
        )
        imported += 1
    write_log(f"CSV migration completed. Imported {imported} rows")
    return imported
