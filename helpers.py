# helpers.py - OANDA VERSION - COMPLETE
import csv
import json
import os
import shutil
import sqlite3
import smtplib
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import requests

from config import (
    OANDA_API_KEY,
    OANDA_ACCOUNT_ID,
    OANDA_TRADING_MODE,
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

from oanda_client import OandaClient

is_demo = (OANDA_TRADING_MODE == "demo") or (TRADING_MODE != "live")
client = OandaClient(OANDA_API_KEY, OANDA_ACCOUNT_ID, is_demo=is_demo)


def load_state():
    if not os.path.exists(STATE_FILE):
        save_state(DEFAULT_STATE.copy())
        return DEFAULT_STATE.copy()
    with open(STATE_FILE, "r") as file:
        state = json.load(file)
    changed = False
    for key, value in DEFAULT_STATE.items():
        if key not in state:
            state[key] = value
            changed = True
    if changed:
        save_state(state)
    return state


def save_state(state):
    with open(STATE_FILE, "w") as file:
        json.dump(state, file, indent=2)


def write_log(message):
    timestamp = datetime.now(timezone.utc).isoformat()
    with open(LOG_FILE, "a") as file:
        file.write(f"{timestamp} | {message}\n")


def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except Exception as e:
        write_log(f"Telegram send failed: {e}")


def send_daily_email_report(report_text):
    if not EMAIL_FROM or not EMAIL_PASSWORD or not EMAIL_TO:
        return
    msg = EmailMessage()
    msg["Subject"] = "Trading Bot Daily Report"
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.set_content(report_text)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(EMAIL_FROM, EMAIL_PASSWORD)
            smtp.send_message(msg)
    except Exception as e:
        write_log(f"Email send failed: {e}")


def init_database():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT,
            strategy TEXT,
            symbol TEXT,
            action TEXT,
            units REAL,
            price REAL,
            order_id TEXT UNIQUE,
            reason TEXT,
            review TEXT,
            trade_score INTEGER,
            fill_status TEXT,
            filled_units TEXT,
            filled_avg_price TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(time)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy)")
    conn.commit()
    conn.close()


def save_trade_to_db(symbol, action, units, price, order_id, strategy, reason, review, trade_score):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO trades (
            time, strategy, symbol, action, units, price, order_id,
            reason, review, trade_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now(timezone.utc).isoformat(),
        strategy,
        symbol,
        action,
        units,
        price,
        str(order_id),
        reason,
        review,
        int(trade_score),
    ))
    conn.commit()
    conn.close()


def get_trades_from_db(limit=1000):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_order_status(order_id):
    return client.get_order_status(order_id)


def update_db_trade_with_fill(order_id):
    order_info = get_order_status(order_id)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE trades
        SET fill_status = ?, filled_units = ?, filled_avg_price = ?
        WHERE order_id = ?
    """, (
        order_info.get("order", {}).get("state", "UNKNOWN"),
        order_info.get("order", {}).get("units", "0"),
        order_info.get("order", {}).get("price", "0"),
        str(order_id),
    ))
    conn.commit()
    conn.close()


def sync_recent_orders(limit=10):
    rows = get_trades_from_db(limit)
    for row in rows:
        order_id = row.get("order_id")
        if order_id:
            try:
                update_db_trade_with_fill(order_id)
            except Exception as error:
                write_log(f"Order sync failed for {order_id}: {error}")


def is_market_open():
    return client.is_market_open()


def get_daily_pnl():
    return client.get_daily_pnl()


def calculate_units_from_price(price, state, strategy="unknown"):
    units_per_trade = float(state.get("max_units_per_trade", 10000))
    strategy_limits = state.get("strategy_max_units", {})
    if strategy in strategy_limits:
        units_per_trade = float(strategy_limits[strategy])
    max_units = int(state.get("max_units_per_trade", 10000))
    return int(min(units_per_trade, max_units))


def calculate_stop_loss_price(side, price, pips):
    pip_value = 0.0001
    if side == "buy":
        return price - (pips * pip_value)
    else:
        return price + (pips * pip_value)


def calculate_take_profit_price(side, price, pips):
    pip_value = 0.0001
    if side == "buy":
        return price + (pips * pip_value)
    else:
        return price - (pips * pip_value)


def build_oanda_order(symbol, units, action, price, state, strategy="unknown"):
    oanda_units = units if action == "buy" else -units
    take_profit_pips = float(state.get("take_profit_pips", 50))
    stop_loss_pips = float(state.get("stop_loss_pips", 50))
    
    strategy_brackets = state.get("strategy_bracket_settings", {})
    if strategy in strategy_brackets:
        take_profit_pips = float(strategy_brackets[strategy].get("take_profit_pips", take_profit_pips))
        stop_loss_pips = float(strategy_brackets[strategy].get("stop_loss_pips", stop_loss_pips))
    
    sl_price = None
    tp_price = None
    
    if action == "buy":
        if stop_loss_pips > 0:
            sl_price = calculate_stop_loss_price("buy", price, stop_loss_pips)
        if take_profit_pips > 0:
            tp_price = calculate_take_profit_price("buy", price, take_profit_pips)
    else:
        if stop_loss_pips > 0:
            sl_price = calculate_stop_loss_price("sell", price, stop_loss_pips)
        if take_profit_pips > 0:
            tp_price = calculate_take_profit_price("sell", price, take_profit_pips)
    
    result = client.place_market_order(symbol, oanda_units, sl_price, tp_price)
    order_id = result.get("id", "unknown")
    return {"id": order_id, "result": result}


def reset_daily_state_if_needed(state):
    today = datetime.now(timezone.utc).date().isoformat()
    if state.get("last_reset_date") != today:
        state["trade_count"] = 0
        state["strategy_trade_counts"] = {}
        state["disabled_strategies"] = []
        state["last_reset_date"] = today
        save_state(state)
        write_log("Daily counters reset")


def has_open_position(symbol):
    try:
        positions = client.get_open_positions()
        return any(p['symbol'] == symbol for p in positions)
    except Exception:
        return False


def validate_trade(state, symbol, action, price, units, strategy="unknown"):
    if state.get("maintenance_mode", False):
        raise ValueError("Maintenance mode is ON")
    if state.get("paused_until"):
        if datetime.now(timezone.utc) < datetime.fromisoformat(state.get("paused_until")):
            raise ValueError("Bot is paused")
    if not state.get("trading_enabled", False):
        raise ValueError("Trading is OFF")
    if TRADING_MODE == "live" and not state.get("live_confirmed", False):
        raise ValueError("Live mode not confirmed")
    if strategy in state.get("disabled_strategies", []):
        raise ValueError("Strategy is disabled")
    
    allowed_symbols = state.get("allowed_symbols", ["EUR_USD", "GBP_USD", "USD_JPY"])
    if symbol not in allowed_symbols:
        raise ValueError(f"Symbol {symbol} not allowed")
    if symbol in state.get("blocked_symbols", []):
        raise ValueError("Symbol is blocked")
    if action not in ["buy", "sell"]:
        raise ValueError("Bad action")
    if float(price) <= 0:
        raise ValueError("Missing or bad price")
    if float(units) < 1:
        raise ValueError("Units is less than 1")
    
    max_trades = int(state.get("max_trades_per_day", 5))
    if int(state.get("trade_count", 0)) >= max_trades:
        raise ValueError("Max trades reached today")
    
    last_trade_time = state.get("last_trade_time")
    if last_trade_time:
        seconds_passed = (datetime.now(timezone.utc) - datetime.fromisoformat(last_trade_time)).total_seconds()
        if seconds_passed < 15 * 60:
            raise ValueError("Cooldown active")
    
    if not is_market_open():
        raise ValueError("Forex market is closed (Friday 5pm - Sunday 5pm ET)")
    
    daily_loss_limit = float(state.get("daily_loss_limit", 50.0))
    if get_daily_pnl() <= -daily_loss_limit:
        state["trading_enabled"] = False
        save_state(state)
        send_telegram_message("Trading turned OFF: daily loss limit hit")
        raise ValueError("Daily loss limit hit")
    
    if action == "buy" and has_open_position(symbol):
        raise ValueError("Already have an open position on this pair")


def handle_successful_trade(state, symbol, action, units, order_id, strategy):
    state["trade_count"] = int(state.get("trade_count", 0)) + 1
    state["last_trade_time"] = datetime.now(timezone.utc).isoformat()
    state["error_count"] = 0
    
    strategy_counts = state.get("strategy_trade_counts", {})
    strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1
    state["strategy_trade_counts"] = strategy_counts
    
    save_state(state)
    write_log(f"Trade placed | {symbol} {action} units={units} order_id={order_id}")
    send_telegram_message(f"Trade placed\nSymbol: {symbol}\nAction: {action}\nUnits: {units}")


def record_error(state, error_message):
    state["error_count"] = int(state.get("error_count", 0)) + 1
    state["last_error_time"] = datetime.now(timezone.utc).isoformat()
    state["last_error_message"] = error_message
    if state["error_count"] >= 3:
        state["trading_enabled"] = False
        send_telegram_message("Trading turned OFF: too many errors")
    save_state(state)
    write_log(f"Error: {error_message}")


def create_trade_review(symbol, action, price, strategy, reason):
    return (f"Strategy: {strategy}. Action: {action}. Symbol: {symbol}. "
            f"Signal price: {price}. Reason: {reason}. "
            "Review: trade passed bot safety checks before order submission.")


def create_trade_score(symbol, action, price, strategy, reason, state=None):
    score = 50
    if strategy and strategy != "unknown":
        score += 10
    if reason and reason != "no reason provided":
        score += 10
    if float(price) > 0:
        score += 10
    if action in ["buy", "sell"]:
        score += 10
    return max(0, min(score, 100))


def get_open_positions():
    return client.get_open_positions()


def close_all_positions():
    result = client.close_all_positions()
    write_log("Close all positions requested")
    send_telegram_message("Emergency: close all positions requested")
    return result


def make_daily_backup():
    backup_folder = "backups"
    os.makedirs(backup_folder, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    files = ["trading_bot.db", "trades.log"]
    for file_name in files:
        if os.path.exists(file_name):
            shutil.copy(file_name, f"{backup_folder}/{today}_{file_name}")
    write_log("Daily backup completed")


def clean_
