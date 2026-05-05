# app.py - FULL RESTORED DASHBOARD + FIXES (v2.1)
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import secrets

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Cookie, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from config import DASHBOARD_PASSWORD, TRADING_MODE, WEBHOOK_SECRET, BOT_VERSION
from config import STRATEGY_ENABLED, STRATEGY_TIMEFRAME, FAST_EMA_PERIOD, SLOW_EMA_PERIOD
from helpers import (
    build_oanda_order, calculate_units_from_price, check_heartbeat_warning,
    check_market_status_alert, clean_old_backups, client, close_all_positions,
    create_trade_review, create_trade_score, disable_trading_end_of_day,
    enable_trading_morning, get_daily_pnl, get_heartbeat_status,
    get_open_positions, get_trades_from_db, handle_successful_trade,
    init_database, is_market_open, load_state, make_daily_backup,
    record_error, reset_daily_state_if_needed, save_state, save_trade_to_db,
    send_scheduled_daily_report, sync_recent_orders, validate_trade,
    write_log, auto_disable_bad_strategies,
)
from telegram_bot import check_telegram_commands
from strategy import execute_autonomous_trade, monitor_open_positions

scheduler = BackgroundScheduler()
market_timezone = ZoneInfo("America/New_York")

active_sessions = {}

def create_session():
    token = secrets.token_urlsafe(32)
    active_sessions[token] = datetime.now()
    return token

def is_logged_in(session: str = ""):
    if session not in active_sessions:
        return False
    if datetime.now() - active_sessions[session] > timedelta(hours=8):
        del active_sessions[session]
        return False
    return True

def validate_config():
    required_vars = {"DASHBOARD_PASSWORD": DASHBOARD_PASSWORD, "WEBHOOK_SECRET": WEBHOOK_SECRET}
    missing = [v for v, val in required_vars.items() if not val]
    if missing:
        write_log(f"CRITICAL: Missing config vars: {missing}")
        return False
    write_log("✅ Configuration validation passed")
    return True

@asynccontextmanager
async def lifespan(app: FastAPI):
    write_log("=== Trading Bot STARTING UP (v2.1 - Full Dashboard) ===")
    if not validate_config():
        raise RuntimeError("Invalid configuration")
    
    init_database()
    write_log(f"Strategy: {'ENABLED' if STRATEGY_ENABLED else 'DISABLED'} | EMA {FAST_EMA_PERIOD}/{SLOW_EMA_PERIOD} | M{STRATEGY_TIMEFRAME}")

    scheduler.add_job(check_telegram_commands, "interval", seconds=5)
    scheduler.add_job(sync_recent_orders, "interval", minutes=2)
    scheduler.add_job(check_heartbeat_warning, "interval", minutes=5)
    scheduler.add_job(check_market_status_alert, "interval", minutes=5)
    scheduler.add_job(send_scheduled_daily_report, "cron", hour=16, minute=10, timezone=market_timezone)
    scheduler.add_job(make_daily_backup, "cron", hour=16, minute=20, timezone=market_timezone)
    scheduler.add_job(clean_old_backups, "cron", hour=16, minute=30, timezone=market_timezone)
    scheduler.add_job(disable_trading_end_of_day, "cron", hour=15, minute=45, timezone=market_timezone)
    scheduler.add_job(enable_trading_morning, "cron", hour=9, minute=35, timezone=market_timezone)
    
    if STRATEGY_ENABLED:
        scheduler.add_job(execute_autonomous_trade, "interval", minutes=int(STRATEGY_TIMEFRAME))
        scheduler.add_job(monitor_open_positions, "interval", minutes=1)
        write_log(f"✅ Autonomous strategy enabled every {STRATEGY_TIMEFRAME} min")

    scheduler.start()
    write_log("✅ APScheduler started successfully")
    yield
    if scheduler.running:
        scheduler.shutdown()
    write_log("Trading Bot shutdown complete")

app = FastAPI(title="Trading