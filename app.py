# app.py - COMPLETE FINAL CORRECTED VERSION
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import secrets

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Cookie, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from config import DASHBOARD_PASSWORD, TRADING_MODE, WEBHOOK_SECRET
from helpers import (
    build_bracket_order,
    calculate_qty_from_price,
    check_heartbeat_warning,
    check_market_status_alert,
    clean_old_backups,
    client,
    create_trade_review,
    create_trade_score,
    disable_trading_end_of_day,
    enable_trading_morning,
    get_daily_pnl,
    get_heartbeat_status,
    get_trades_from_db,
    handle_successful_trade,
    init_database,
    is_market_open,
    load_state,
    make_daily_backup,
    record_error,
    reset_daily_state_if_needed,
    save_state,
    save_trade_to_db,
    send_scheduled_daily_report,
    sync_recent_orders,
    validate_trade,
    write_log,
    auto_disable_bad_strategies,
)
from telegram_bot import check_telegram_commands

scheduler = BackgroundScheduler()
market_timezone = ZoneInfo("America/New_York")

# Session management with expiry
active_sessions = {}

def create_session():
    token = secrets.token_urlsafe(32)
    active_sessions[token] = datetime.now()
    return token

def is_logged_in(session: str = ""):
    if session not in active_sessions:
        return False
    # Expire sessions after 8 hours
    if datetime.now() - active_sessions[session] > timedelta(hours=8):
        del active_sessions[session]
        return False
    return True

def validate_config():
    """Validate critical configuration on startup"""
    required_vars = {
        "DASHBOARD_PASSWORD": DASHBOARD_PASSWORD,
        "WEBHOOK_SECRET": WEBHOOK_SECRET
    }
    missing = [v for v, val in required_vars.items() if not val]
    if missing:
        write_log(f"CRITICAL: Missing config vars: {missing}")
        return False
    write_log("✅ Configuration validation passed")
    return True

@asynccontextmanager
async def lifespan(app: FastAPI):
    write_log("=== Trading Bot STARTING UP on Render ===")
    
    if not validate_config():
        write_log("FATAL: Invalid configuration")
        raise RuntimeError("Invalid configuration")
    
    init_database()

    scheduler.add_job(check_telegram_commands, "interval", seconds=5)
    scheduler.add_job(sync_recent_orders, "interval", minutes=2)
    scheduler.add_job(check_heartbeat_warning, "interval", minutes=5)
    scheduler.add_job(check_market_status_alert, "interval", minutes=5)
    scheduler.add_job(send_scheduled_daily_report, "cron", hour=16, minute=10, timezone=market_timezone)
    scheduler.add_job(make_daily_backup, "cron", hour=16, minute=20, timezone=market_timezone)
    scheduler.add_job(clean_old_backups, "cron", hour=16, minute=30, timezone=market_timezone)
    scheduler.add_job(disable_trading_end_of_day, "cron", hour=15, minute=45, timezone=market_timezone)
    scheduler.add_job(enable_trading_morning, "cron", hour=9, minute=35, timezone=market_timezone)

    scheduler.start()
    write_log("✅ APScheduler started successfully")
    yield
    if scheduler.running:
        scheduler.shutdown()
    write_log("Trading Bot shutdown complete")

app = FastAPI(title="Trading Bot", lifespan=lifespan)

# TrustedHost middleware with wildcard for any Render URL
app.add_middleware(
    TrustedHostMiddleware, 
    allowed_hosts=["*"]
)

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "timestamp": datetime.now().isoformat(),
            "path": request.url.path
        }
    )

def check_dashboard_password(password):
    return password == DASHBOARD_PASSWORD

@app.get("/")
def home():
    return {"message": "Trading bot is running", "status": "healthy", "dashboard": "/dashboard"}

@app.get("/health")
def health_check():
    try:
        state = load_state()
        return {
            "status": "healthy",
            "trading_enabled": state.get("trading_enabled", False),
            "timestamp": datetime.now().isoformat(),
            "scheduler_running": scheduler.running
        }
    except Exception as e:
        write_log(f"Health check failed: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "unhealthy", "error": str(e)}
        )

@app.post("/login")
def login(response: Response, password: str = Form("")):
    if not check_dashboard_password(password):
        raise HTTPException(status_code=401, detail="Wrong password")
    
    session_token = create_session()
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        key="session",
        value=session_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=28800
    )
    return response

@app.post("/logout")
def logout():
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.delete_cookie(key="session")
    return response

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(session: str = Cookie(default="")):
    if not is_logged_in(session):
        return """
        <html><body><h2>Trading Bot Login</h2>
        <form method="post" action="/login">
            <input type="password" name="password" placeholder="Password" />
            <button type="submit">Login</button>
        </form></body></html>
        """

    state = load_state()
    reset_daily_state_if_needed(state)

    try:
        market_open = is_market_open()
        daily_pnl = get_daily_pnl()
        heartbeat_status, _ = get_heartbeat_status()
    except Exception as e:
        write_log(f"Dashboard error: {e}")
        market_open = False
        daily_pnl = 0
        heartbeat_status = "Error checking status"

    return f"""
    <html>
    <head><meta http-equiv="refresh" content="30"></head>
    <body>
        <h1>Trading Bot Dashboard</h1>
        <p><b>Trading Enabled:</b> {state.get('trading_enabled')}</p>
        <p><b>Market Open:</b> {market_open}</p>
        <p><b>Daily P/L:</b> ${daily_pnl:.2f}</p>
        <p><b>Heartbeat:</b> {heartbeat_status}</p>
        <p><b>Mode:</b> {TRADING_MODE}</p>
        <hr>
        <form method="post" action="/enable"><button>Enable Trading</button></form>
        <form method="post" action="/disable"><button>Disable Trading</button></form>
        <form method="post" action="/backup-now"><button>Backup Now</button></form>
        <form method="post" action="/logout"><button>Logout</button></form>
    </body></html>
    """

@app.post("/webhook")
async def webhook(request: Request):
    state = load_state()
    reset_daily_state_if_needed(state)
    data = await request.json()

    if data.get("secret") != WEBHOOK_SECRET:
        record_error(state, "Wrong webhook secret")
        raise HTTPException(status_code=401, detail="Wrong secret")

    symbol = str(data.get("symbol", "")).upper()
    action = str(data.get("action", "")).lower()
    strategy = str(data.get("strategy", "unknown"))
    reason = str(data.get("reason", "no reason provided"))
    price = float(data.get("price", 0))

    state["last_webhook_time"] = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
    state["last_webhook_symbol"] = symbol
    state["last_webhook_action"] = action
    state["heartbeat_warning_sent"] = False
    save_state(state)

    if action == "heartbeat":
        return {"ok": True}

    qty = float(data.get("qty") or calculate_qty_from_price(price, state, strategy))

    try:
        review = create_trade_review(symbol, action, price, strategy, reason)
        trade_score = create_trade_score(symbol, action, price, strategy, reason, state)

        if trade_score < int(state.get("minimum_trade_score", 70)):
            raise ValueError(f"Trade score too low: {trade_score}")

        auto_disable_bad_strategies()
        state = load_state()
        validate_trade(state, symbol, action, price, qty, strategy)

        order = build_bracket_order(symbol, qty, action, price, state, strategy)
        placed_order = client.submit_order(order_data=order)

        handle_successful_trade(state, symbol, action, qty, placed_order.id, strategy)
        save_trade_to_db(symbol, action, qty, price, placed_order.id, strategy, reason, review, trade_score)

        return {"ok": True, "symbol": symbol, "action": action, "qty": qty}
    except ValueError as e:
        record_error(state, str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        record_error(state, f"Broker error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/enable")
def enable_trading(session: str = Cookie(default="")):
    if not is_logged_in(session): 
        raise HTTPException(401, "Not logged in")
    state = load_state()
    state["trading_enabled"] = True
    save_state(state)
    return RedirectResponse("/dashboard", 303)

@app.post("/disable")
def disable_trading(session: str = Cookie(default="")):
    if not is_logged_in(session): 
        raise HTTPException(401, "Not logged in")
    state = load_state()
    state["trading_enabled"] = False
    save_state(state)
    return RedirectResponse("/dashboard", 303)

@app.post("/backup-now")
def backup_now(session: str = Cookie(default="")):
    if not is_logged_in(session): 
        raise HTTPException(401, "Not logged in")
    make_daily_backup()
    return RedirectResponse("/dashboard", 303)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
