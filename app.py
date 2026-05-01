# app.py - QUICK FIX VERSION WITH SIMPLIFIED DASHBOARD
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import secrets

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Cookie, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from config import DASHBOARD_PASSWORD, TRADING_MODE, WEBHOOK_SECRET
from config import STRATEGY_ENABLED, STRATEGY_TIMEFRAME
from helpers import (
    build_oanda_order,
    calculate_units_from_price,
    check_heartbeat_warning,
    check_market_status_alert,
    clean_old_backups,
    client,
    close_all_positions,
    create_trade_review,
    create_trade_score,
    disable_trading_end_of_day,
    enable_trading_morning,
    get_daily_pnl,
    get_heartbeat_status,
    get_open_positions,
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
from strategy import execute_autonomous_trade, monitor_open_positions

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
    if datetime.now() - active_sessions[session] > timedelta(hours=8):
        del active_sessions[session]
        return False
    return True

def validate_config():
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

    # Existing scheduler jobs
    scheduler.add_job(check_telegram_commands, "interval", seconds=5)
    scheduler.add_job(sync_recent_orders, "interval", minutes=2)
    scheduler.add_job(check_heartbeat_warning, "interval", minutes=5)
    scheduler.add_job(check_market_status_alert, "interval", minutes=5)
    scheduler.add_job(send_scheduled_daily_report, "cron", hour=16, minute=10, timezone=market_timezone)
    scheduler.add_job(make_daily_backup, "cron", hour=16, minute=20, timezone=market_timezone)
    scheduler.add_job(clean_old_backups, "cron", hour=16, minute=30, timezone=market_timezone)
    scheduler.add_job(disable_trading_end_of_day, "cron", hour=15, minute=45, timezone=market_timezone)
    scheduler.add_job(enable_trading_morning, "cron", hour=9, minute=35, timezone=market_timezone)
    
    # Autonomous trading strategy jobs
    if STRATEGY_ENABLED:
        interval_minutes = int(STRATEGY_TIMEFRAME)
        scheduler.add_job(execute_autonomous_trade, "interval", minutes=interval_minutes)
        scheduler.add_job(monitor_open_positions, "interval", minutes=1)
        write_log(f"✅ Autonomous strategy enabled - checking every {interval_minutes} minutes")

    scheduler.start()
    write_log("✅ APScheduler started successfully")
    yield
    if scheduler.running:
        scheduler.shutdown()
    write_log("Trading Bot shutdown complete")

app = FastAPI(title="Trading Bot", lifespan=lifespan)

# TrustedHost middleware with wildcard
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


# ========== API ENDPOINTS ==========

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
            "strategy_enabled": STRATEGY_ENABLED,
            "timestamp": datetime.now().isoformat(),
            "scheduler_running": scheduler.running
        }
    except Exception as e:
        write_log(f"Health check failed: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "unhealthy", "error": str(e)}
        )


# ========== API ENDPOINTS FOR DASHBOARD ==========

@app.get("/api/notifications")
def get_notifications(session: str = Cookie(default="")):
    if not is_logged_in(session):
        return {"notifications": []}
    
    state = load_state()
    notifications = []
    
    status = "🟢" if state.get("trading_enabled") else "🔴"
    status_text = "TRADING ACTIVE" if state.get("trading_enabled") else "TRADING PAUSED"
    notifications.append({"type": "status", "message": f"{status} {status_text}", "priority": "high"})
    
    try:
        market_open = is_market_open()
        market_icon = "🟢" if market_open else "🔴"
        market_text = "MARKET OPEN" if market_open else "MARKET CLOSED"
        notifications.append({"type": "market", "message": f"{market_icon} {market_text}", "priority": "high"})
    except:
        pass
    
    return {"notifications": notifications}

@app.get("/api/profit-metrics")
def get_profit_metrics(session: str = Cookie(default="")):
    if not is_logged_in(session):
        return {"daily_pnl": 0, "weekly_pnl": 0, "daily_percent": 0}
    
    daily_pnl = get_daily_pnl()
    return {
        "daily_pnl": round(daily_pnl, 2),
        "daily_percent": 0,
        "weekly_pnl": 0,
        "usdt_balance": 0,
        "best_trade": 0,
        "best_trade_symbol": "N/A"
    }

@app.get("/api/symbols")
def get_api_symbols(session: str = Cookie(default="")):
    if not is_logged_in(session):
        return {"symbols": []}
    state = load_state()
    return {"symbols": state.get("allowed_symbols", [])}

@app.get("/api/weekly-breakdown")
def get_weekly_breakdown(session: str = Cookie(default="")):
    if not is_logged_in(session):
        return {}
    return {}


# ========== DASHBOARD CONTROLS ==========

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

@app.post("/enable")
def enable_trading(session: str = Cookie(default="")):
    if not is_logged_in(session): 
        raise HTTPException(401, "Not logged in")
    state = load_state()
    state["trading_enabled"] = True
    save_state(state)
    write_log("Trading enabled from dashboard")
    return RedirectResponse("/dashboard", 303)

@app.post("/disable")
def disable_trading(session: str = Cookie(default="")):
    if not is_logged_in(session): 
        raise HTTPException(401, "Not logged in")
    state = load_state()
    state["trading_enabled"] = False
    save_state(state)
    write_log("Trading disabled from dashboard")
    return RedirectResponse("/dashboard", 303)

@app.post("/backup-now")
def backup_now(session: str = Cookie(default="")):
    if not is_logged_in(session): 
        raise HTTPException(401, "Not logged in")
    make_daily_backup()
    return RedirectResponse("/dashboard", 303)

@app.post("/toggle-asset-mode")
def toggle_asset_mode(session: str = Cookie(default="")):
    if not is_logged_in(session):
        raise HTTPException(401, "Not logged in")
    state = load_state()
    modes = ["forex", "stocks", "crypto", "both"]
    current = state.get("asset_mode", "forex")
    current_idx = modes.index(current) if current in modes else 0
    next_mode = modes[(current_idx + 1) % len(modes)]
    state["asset_mode"] = next_mode
    save_state(state)
    return RedirectResponse("/dashboard", 303)

@app.post("/reset-daily-stats")
def reset_daily_stats(session: str = Cookie(default="")):
    if not is_logged_in(session):
        raise HTTPException(401, "Not logged in")
    state = load_state()
    state["trade_count"] = 0
    state["strategy_trade_counts"] = {}
    save_state(state)
    return RedirectResponse("/dashboard", 303)

@app.post("/clear-errors")
def clear_errors(session: str = Cookie(default="")):
    if not is_logged_in(session):
        raise HTTPException(401, "Not logged in")
    state = load_state()
    state["error_count"] = 0
    state["last_error_message"] = None
    save_state(state)
    return RedirectResponse("/dashboard", 303)

@app.post("/close-all-positions")
def close_all_positions_endpoint(session: str = Cookie(default="")):
    if not is_logged_in(session):
        raise HTTPException(401, "Not logged in")
    close_all_positions()
    return RedirectResponse("/dashboard", 303)

@app.post("/update-allowed-symbols")
def update_allowed_symbols(
    session: str = Cookie(default=""),
    add_symbol: str = Form(None),
):
    if not is_logged_in(session):
        raise HTTPException(401, "Not logged in")
    state = load_state()
    current_symbols = state.get("allowed_symbols", ["EUR_USD", "GBP_USD", "USD_JPY"])
    
    if add_symbol and add_symbol.strip():
        new_symbol = add_symbol.strip().upper()
        if "_" not in new_symbol and len(new_symbol) == 6:
            new_symbol = f"{new_symbol[:3]}_{new_symbol[3:]}"
        if new_symbol not in current_symbols:
            current_symbols.append(new_symbol)
    
    state["allowed_symbols"] = current_symbols
    save_state(state)
    return RedirectResponse("/dashboard", 303)

@app.post("/update-risk-settings")
def update_risk_settings(
    session: str = Cookie(default=""),
    max_units_per_trade: int = Form(None),
    max_trades_per_day: int = Form(None),
):
    if not is_logged_in(session):
        raise HTTPException(401, "Not logged in")
    state = load_state()
    
    if max_units_per_trade is not None:
        state["max_units_per_trade"] = max_units_per_trade
    if max_trades_per_day is not None:
        state["max_trades_per_day"] = max_trades_per_day
    
    save_state(state)
    return RedirectResponse("/dashboard", 303)


# ========== WEBHOOK ==========

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

    # Convert symbol format if needed
    if "_" not in symbol and len(symbol) == 6:
        symbol = f"{symbol[:3]}_{symbol[3:]}"

    units = float(data.get("units") or calculate_units_from_price(price, state, strategy))

    try:
        review = create_trade_review(symbol, action, price, strategy, reason)
        trade_score = create_trade_score(symbol, action, price, strategy, reason, state)

        if trade_score < int(state.get("minimum_trade_score", 70)):
            raise ValueError(f"Trade score too low: {trade_score}")

        auto_disable_bad_strategies()
        state = load_state()
        validate_trade(state, symbol, action, price, units, strategy)

        order_result = build_oanda_order(symbol, units, action, price, state, strategy)
        placed_order = order_result
        order_id = placed_order.get('id', 'unknown')

        handle_successful_trade(state, symbol, action, units, order_id, strategy)
        save_trade_to_db(symbol, action, units, price, order_id, strategy, reason, review, trade_score)

        return {"ok": True, "symbol": symbol, "action": action, "units": units}
    except ValueError as e:
        record_error(state, str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        record_error(state, f"Broker error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ========== SIMPLIFIED DASHBOARD HTML ==========

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(session: str = Cookie(default="")):
    if not is_logged_in(session):
        return """
        <!DOCTYPE html>
        <html>
        <head><title>Trading Bot Login</title></head>
        <body style="background:#1a1a2e; color:white; font-family:Arial; display:flex; justify-content:center; align-items:center; height:100vh;">
            <div style="background:rgba(255,255,255,0.1); padding:40px; border-radius:10px;">
                <h2>Trading Bot Login</h2>
                <form method="post" action="/login">
                    <input type="password" name="password" placeholder="Password" style="padding:10px; width:100%; margin:10px 0;" />
                    <button type="submit" style="padding:10px 20px; background:#00d4ff; border:none; border-radius:5px;">Login</button>
                </form>
            </div>
        </body>
        </html>
        """

    state = load_state()
    
    try:
        market_open = is_market_open()
        daily_pnl = get_daily_pnl()
    except Exception as e:
        market_open = False
        daily_pnl = 0

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Trading Bot Dashboard</title>
        <meta http-equiv="refresh" content="30">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%);
                color: #eee;
                padding: 20px;
                min-height: 100vh;
            }}
            .container {{ max-width: 800px; margin: 0 auto; }}
            h1 {{ font-size: 2rem; margin-bottom: 1rem; }}
            .card {{
                background: rgba(255,255,255,0.1);
                backdrop-filter: blur(10px);
                border-radius: 16px;
                padding: 20px;
                margin-bottom: 20px;
                border: 1px solid rgba(255,255,255,0.2);
            }}
            .card h3 {{ margin-bottom: 15px; color: #00d4ff; }}
            .status-enabled {{ background: #00c853; color: white; padding: 4px 12px; border-radius: 20px; display: inline-block; }}
            .status-disabled {{ background: #d32f2f; color: white; padding: 4px 12px; border-radius: 20px; display: inline-block; }}
            .status-open {{ background: #00c853; color: white; padding: 4px 12px; border-radius: 20px; display: inline-block; }}
            .status-closed {{ background: #ff6d00; color: white; padding: 4px 12px; border-radius: 20px; display: inline-block; }}
            button {{
                background: #00d4ff;
                color: #1a1a2e;
                border: none;
                padding: 10px 20px;
                border-radius: 8px;
                cursor: pointer;
                font-weight: bold;
                margin: 5px;
            }}
            button:hover {{ transform: scale(1.02); }}
            button.danger {{ background: #d32f2f; color: white; }}
            button.success {{ background: #00c853; color: white; }}
            .metric {{ font-size: 2rem; font-weight: bold; margin: 10px 0; }}
            .metric-label {{ font-size: 0.85rem; opacity: 0.8; }}
            .flex {{ display: flex; gap: 10px; flex-wrap: wrap; }}
            hr {{ margin: 15px 0; border-color: rgba(255,255,255,0.1); }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Trading Bot Dashboard</h1>
            
            <div class="card">
                <h3>📊 Trading Status</h3>
                <div class="metric">${daily_pnl:.2f}</div>
                <div class="metric-label">Daily P/L</div>
                <hr>
                <div>
                    <span class="{'status-enabled' if state.get('trading_enabled') else 'status-disabled'}">
                        {'🟢 ENABLED' if state.get('trading_enabled') else '🔴 DISABLED'}
                    </span>
                    <span class="{'status-open' if market_open else 'status-closed'}">
                        {'🟢 MARKET OPEN' if market_open else '🔴 MARKET CLOSED'}
                    </span>
                </div>
            </div>
            
            <div class="card">
                <h3>📈 Today's Activity</h3>
                <div class="metric">{state.get('trade_count', 0)} / {state.get('max_trades_per_day', 5)}</div>
                <div class="metric-label">Trades Today</div>
                <hr>
                <div class="metric">{state.get('error_count', 0)}</div>
                <div class="metric-label">Errors Today</div>
            </div>
            
            <div class="card">
                <h3>🎮 Controls</h3>
                <div class="flex">
                    <form method="post" action="/enable" style="display: inline;">
                        <button type="submit" class="success">▶️ Enable Trading</button>
                    </form>
                    <form method="post" action="/disable" style="display: inline;">
                        <button type="submit" class="danger">⏸️ Disable Trading</button>
                    </form>
                    <form method="post" action="/reset-daily-stats" style="display: inline;">
                        <button type="submit">📊 Reset Stats</button>
                    </form>
                    <form method="post" action="/clear-errors" style="display: inline;">
                        <button type="submit">🗑️ Clear Errors</button>
                    </form>
                    <form method="post" action="/backup-now" style="display: inline;">
                        <button type="submit">💾 Backup</button>
                    </form>
                    <form method="post" action="/logout" style="display: inline;">
                        <button type="submit">🚪 Logout</button>
                    </form>
                </div>
            </div>
            
            <div class="card">
                <h3>ℹ️ Info</h3>
                <div>Mode: {TRADING_MODE.upper()}</div>
                <div>Strategy: {'AUTONOMOUS ON' if STRATEGY_ENABLED else 'AUTONOMOUS OFF'}</div>
                <div>Last Reset: {state.get('last_reset_date', 'Never')}</div>
            </div>
        </div>
    </body>
    </html>
    """


if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)