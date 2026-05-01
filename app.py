# app.py - COMPLETE FINAL VERSION WITH ALL FEATURES (FIXED)
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
            "timestamp": datetime.now().isoformat(),
            "scheduler_running": scheduler.running
        }
    except Exception as e:
        write_log(f"Health check failed: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "unhealthy", "error": str(e)}
        )


# ========== PROFIT METRICS API ==========

@app.get("/api/profit-metrics")
def get_profit_metrics(session: str = Cookie(default="")):
    if not is_logged_in(session):
        raise HTTPException(401, "Not logged in")
    
    trades = get_trades_from_db(500)
    daily_pnl = get_daily_pnl()
    
    try:
        account = client.get_account()
        current_equity = float(account.equity)
        buying_power = float(account.buying_power)
    except:
        current_equity = 10000
        buying_power = 10000
    
    previous_equity = current_equity - daily_pnl
    daily_percent = (daily_pnl / previous_equity * 100) if previous_equity > 0 else 0
    
    today = datetime.now().date()
    weekly_pnl = 0
    weekly_trades = 0
    weekly_wins = 0
    monthly_pnl = 0
    monthly_trades = 0
    best_trade = 0
    worst_trade = 0
    best_trade_symbol = ""
    worst_trade_symbol = ""
    
    for trade in trades:
        if trade.get('action') == 'sell':
            try:
                trade_date = datetime.fromisoformat(trade.get('time', '')).date()
                pnl = float(trade.get('qty', 0)) * float(trade.get('price', 0))
                
                if (today - trade_date).days <= 7:
                    weekly_pnl += pnl
                    weekly_trades += 1
                    if pnl > 0:
                        weekly_wins += 1
                
                if (today - trade_date).days <= 30:
                    monthly_pnl += pnl
                    monthly_trades += 1
                    if pnl > best_trade:
                        best_trade = pnl
                        best_trade_symbol = trade.get('symbol', '')
                    if pnl < worst_trade:
                        worst_trade = pnl
                        worst_trade_symbol = trade.get('symbol', '')
            except:
                pass
    
    weekly_win_rate = (weekly_wins / weekly_trades * 100) if weekly_trades > 0 else 0
    
    usdt_balance = buying_power
    try:
        positions = get_open_positions()
        for pos in positions:
            if "/" in pos.get("symbol", ""):
                usdt_balance += float(pos.get("market_value", 0))
    except:
        pass
    
    return {
        "daily_pnl": round(daily_pnl, 2),
        "daily_percent": round(daily_percent, 2),
        "weekly_pnl": round(weekly_pnl, 2),
        "weekly_trades": weekly_trades,
        "weekly_win_rate": round(weekly_win_rate, 1),
        "monthly_pnl": round(monthly_pnl, 2),
        "monthly_trades": monthly_trades,
        "current_equity": round(current_equity, 2),
        "buying_power": round(buying_power, 2),
        "usdt_balance": round(usdt_balance, 2),
        "best_trade": round(best_trade, 2),
        "best_trade_symbol": best_trade_symbol,
        "worst_trade": round(worst_trade, 2),
        "worst_trade_symbol": worst_trade_symbol,
    }

@app.get("/api/weekly-breakdown")
def get_weekly_breakdown(session: str = Cookie(default="")):
    if not is_logged_in(session):
        raise HTTPException(401, "Not logged in")
    
    trades = get_trades_from_db(500)
    daily_breakdown = {}
    today = datetime.now().date()
    
    for i in range(7):
        date = today - timedelta(days=i)
        daily_breakdown[date.isoformat()] = {"pnl": 0, "trades": 0, "wins": 0}
    
    for trade in trades:
        if trade.get('action') == 'sell':
            try:
                trade_date = datetime.fromisoformat(trade.get('time', '')).date()
                if trade_date >= today - timedelta(days=7):
                    pnl = float(trade.get('qty', 0)) * float(trade.get('price', 0))
                    date_str = trade_date.isoformat()
                    if date_str in daily_breakdown:
                        daily_breakdown[date_str]["pnl"] += pnl
                        daily_breakdown[date_str]["trades"] += 1
                        if pnl > 0:
                            daily_breakdown[date_str]["wins"] += 1
            except:
                pass
    
    for date in daily_breakdown:
        trades_count = daily_breakdown[date]["trades"]
        daily_breakdown[date]["win_rate"] = round(
            (daily_breakdown[date]["wins"] / trades_count * 100), 1
        ) if trades_count > 0 else 0
    
    return dict(sorted(daily_breakdown.items(), reverse=True))

@app.get("/api/notifications")
def get_notifications(session: str = Cookie(default="")):
    if not is_logged_in(session):
        raise HTTPException(401, "Not logged in")
    
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
    
    asset_mode = state.get("asset_mode", "stocks")
    mode_icons = {"stocks": "📈", "crypto": "🪙", "both": "🌐"}
    notifications.append({"type": "mode", "message": f"{mode_icons.get(asset_mode, '📈')} Trading: {asset_mode.upper()}", "priority": "low"})
    
    trades = get_trades_from_db(3)
    for trade in trades:
        action_icon = "🟢" if trade.get("action") == "buy" else "🔴"
        notifications.append({"type": "trade", "message": f"{action_icon} {trade.get('symbol')} {trade.get('action', '').upper()}", "priority": "medium"})
    
    return {"notifications": notifications}

@app.get("/api/symbols")
def get_api_symbols(session: str = Cookie(default="")):
    if not is_logged_in(session):
        raise HTTPException(401, "Not logged in")
    state = load_state()
    return {"symbols": state.get("allowed_symbols", [])}

@app.get("/webhook-tester", response_class=HTMLResponse)
def webhook_tester(session: str = Cookie(default="")):
    if not is_logged_in(session):
        return RedirectResponse("/dashboard", 303)
    
    return """
    <html>
    <head><title>Webhook Tester</title>
    <style>
        body { font-family: monospace; background: #1a1a2e; color: white; padding: 20px; }
        input, select, textarea { width: 100%; padding: 10px; margin: 10px 0; background: #2a2a3e; color: white; border: 1px solid #00d4ff; border-radius: 5px; }
        button { background: #00d4ff; color: black; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; }
        .result { background: #0a0a1e; padding: 15px; border-radius: 5px; margin-top: 20px; }
    </style>
    </head>
    <body>
        <h2>📡 Webhook Test Tool</h2>
        <form id="testForm">
            <input type="text" id="symbol" placeholder="Symbol (e.g., AAPL)" value="AAPL">
            <select id="action"><option value="buy">BUY</option><option value="sell">SELL</option><option value="heartbeat">HEARTBEAT</option></select>
            <input type="text" id="strategy" placeholder="Strategy" value="ema_rsi_v1">
            <input type="number" id="price" placeholder="Price" value="150.00">
            <input type="password" id="secret" placeholder="Webhook Secret">
            <button type="button" onclick="sendWebhook()">🚀 Send Test</button>
        </form>
        <div class="result" id="result">Waiting...</div>
        <p><a href="/dashboard" style="color: #00d4ff;">← Back to Dashboard</a></p>
        <script>
            async function sendWebhook() {
                const resultDiv = document.getElementById('result');
                resultDiv.textContent = 'Sending...';
                const payload = {
                    secret: document.getElementById('secret').value,
                    symbol: document.getElementById('symbol').value,
                    action: document.getElementById('action').value,
                    strategy: document.getElementById('strategy').value,
                    reason: 'Manual test',
                    price: parseFloat(document.getElementById('price').value)
                };
                try {
                    const response = await fetch('/webhook', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    const data = await response.json();
                    resultDiv.textContent = JSON.stringify(data, null, 2);
                    resultDiv.style.color = response.ok ? '#00c853' : '#d32f2f';
                } catch (error) {
                    resultDiv.textContent = 'Error: ' + error.message;
                    resultDiv.style.color = '#d32f2f';
                }
            }
        </script>
    </body>
    </html>
    """

@app.get("/export-trades")
def export_trades(session: str = Cookie(default="")):
    if not is_logged_in(session):
        raise HTTPException(401, "Not logged in")
    
    trades = get_trades_from_db(10000)
    import csv
    from io import StringIO
    
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=["time", "symbol", "action", "qty", "price", "strategy", "reason", "trade_score"])
    writer.writeheader()
    
    for trade in trades:
        writer.writerow({
            "time": trade.get("time", ""),
            "symbol": trade.get("symbol", ""),
            "action": trade.get("action", ""),
            "qty": trade.get("qty", ""),
            "price": trade.get("price", ""),
            "strategy": trade.get("strategy", ""),
            "reason": trade.get("reason", ""),
            "trade_score": trade.get("trade_score", "")
        })
    
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=trades_export.csv"}
    )


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

@app.post("/toggle-asset-mode")
def toggle_asset_mode(session: str = Cookie(default="")):
    if not is_logged_in(session):
        raise HTTPException(401, "Not logged in")
    state = load_state()
    modes = ["stocks", "crypto", "both"]
    current = state.get("asset_mode", "stocks")
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
    remove_symbol: str = Form(None),
):
    if not is_logged_in(session):
        raise HTTPException(401, "Not logged in")
    state = load_state()
    current_symbols = state.get("allowed_symbols", ["AAPL", "TSLA", "SPY"])
    
    if add_symbol and add_symbol.strip():
        new_symbol = add_symbol.strip().upper()
        if new_symbol not in current_symbols:
            current_symbols.append(new_symbol)
    
    if remove_symbol and remove_symbol.strip():
        rem_symbol = remove_symbol.strip().upper()
        if rem_symbol in current_symbols:
            current_symbols.remove(rem_symbol)
    
    state["allowed_symbols"] = current_symbols
    save_state(state)
    return RedirectResponse("/dashboard", 303)

@app.post("/toggle-symbol/{symbol}")
def toggle_symbol(session: str = Cookie(default=""), symbol: str = ""):
    if not is_logged_in(session):
        raise HTTPException(401, "Not logged in")
    state = load_state()
    current = state.get("allowed_symbols", ["AAPL", "TSLA", "SPY"])
    symbol = symbol.upper()
    
    if symbol in current:
        current.remove(symbol)
    else:
        current.append(symbol)
    
    state["allowed_symbols"] = current
    save_state(state)
    return RedirectResponse("/dashboard", 303)

@app.post("/update-risk-settings")
def update_risk_settings(
    session: str = Cookie(default=""),
    max_dollars_per_trade: float = Form(None),
    max_trades_per_day: int = Form(None),
    daily_loss_limit: float = Form(None),
    take_profit_percent: float = Form(None),
    stop_loss_percent: float = Form(None),
):
    if not is_logged_in(session):
        raise HTTPException(401, "Not logged in")
    state = load_state()
    
    if max_dollars_per_trade is not None:
        state["max_dollars_per_trade"] = max_dollars_per_trade
    if max_trades_per_day is not None:
        state["max_trades_per_day"] = max_trades_per_day
    if daily_loss_limit is not None:
        state["daily_loss_limit"] = daily_loss_limit
    if take_profit_percent is not None:
        state["take_profit_percent"] = take_profit_percent
    if stop_loss_percent is not None:
        state["stop_loss_percent"] = stop_loss_percent
    
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


# ========== DASHBOARD HTML ==========

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
        heartbeat_status, heartbeat_color = get_heartbeat_status()
        positions = get_open_positions()
    except Exception as e:
        write_log(f"Dashboard error: {e}")
        market_open = False
        daily_pnl = 0
        heartbeat_status = "Error checking status"
        positions = []

    asset_mode = state.get("asset_mode", "stocks")
    allowed_symbols = state.get("allowed_symbols", ["AAPL", "TSLA", "SPY"])
    stock_symbols = [s for s in allowed_symbols if "/" not in s]
    crypto_symbols = [s for s in allowed_symbols if "/" in s]
    
    mode_display = {"stocks": "📈 Stocks Only", "crypto": "🪙 Crypto Only", "both": "🌐 Both Markets"}
    mode_text = mode_display.get(asset_mode, "📈 Stocks Only")

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Trading Bot Dashboard</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%);
                color: #eee;
                padding: 20px;
                min-height: 100vh;
            }}
            .container {{ max-width: 1400px; margin: 0 auto; }}
            h1 {{ font-size: 2rem; margin-bottom: 1rem; display: flex; align-items: center; gap: 10px; }}
            
            .notification-bar {{
                background: rgba(0,0,0,0.8);
                border-radius: 12px;
                padding: 12px 20px;
                margin-bottom: 20px;
                display: flex;
                flex-wrap: wrap;
                gap: 15px;
                align-items: center;
                backdrop-filter: blur(10px);
                border: 1px solid rgba(0,212,255,0.3);
            }}
            .notification {{
                display: inline-flex;
                align-items: center;
                gap: 8px;
                padding: 6px 14px;
                border-radius: 20px;
                font-size: 0.85rem;
            }}
            .notification-high {{ background: rgba(255,109,0,0.3); border-left: 3px solid #ff6d00; }}
            .notification-medium {{ background: rgba(0,212,255,0.2); border-left: 3px solid #00d4ff; }}
            .notification-low {{ background: rgba(255,255,255,0.1); }}
            
            .grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
                gap: 20px;
                margin-bottom: 20px;
            }}
            .card {{
                background: rgba(255,255,255,0.1);
                backdrop-filter: blur(10px);
                border-radius: 16px;
                padding: 20px;
                border: 1px solid rgba(255,255,255,0.2);
            }}
            .card h3 {{ margin-bottom: 15px; color: #00d4ff; font-size: 1.2rem; }}
            
            .status-badge {{
                display: inline-block;
                padding: 4px 12px;
                border-radius: 20px;
                font-size: 0.85rem;
                font-weight: bold;
            }}
            .status-enabled {{ background: #00c853; color: white; }}
            .status-disabled {{ background: #d32f2f; color: white; }}
            .status-open {{ background: #00c853; color: white; }}
            .status-closed {{ background: #ff6d00; color: white; }}
            .mode-stocks {{ background: #2196f3; }}
            .mode-crypto {{ background: #9c27b0; }}
            .mode-both {{ background: #00bcd4; }}
            
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
            button.warning {{ background: #ff6d00; color: white; }}
            button.success {{ background: #00c853; color: white; }}
            
            .metric {{ font-size: 2rem; font-weight: bold; margin: 10px 0; }}
            .metric-label {{ font-size: 0.85rem; opacity: 0.8; }}
            
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ padding: 8px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.1); }}
            
            .flex {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
            .symbol-tag {{
                display: inline-flex;
                align-items: center;
                gap: 8px;
                background: rgba(0,212,255,0.2);
                padding: 5px 12px;
                border-radius: 20px;
                font-size: 0.85rem;
            }}
            .symbol-tag button {{ background: none; color: #ff6d00; padding: 0; margin: 0; font-size: 1.1rem; }}
            hr {{ margin: 15px 0; border-color: rgba(255,255,255,0.1); }}
            .section-title {{ font-size: 0.9rem; color: #00d4ff; margin: 10px 0 5px 0; }}
            .live-badge {{ background: #d32f2f; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Trading Bot Dashboard <span class="live-badge">LIVE</span></h1>
            
            <div class="notification-bar">
                <span>📢 Notifications:</span>
                <div id="notifications">Loading...</div>
            </div>
            
            <div class="grid">
                <div class="card">
                    <h3>📊 Trading Status</h3>
                    <div class="metric" id="dailyPnl">${daily_pnl:.2f}</div>
                    <div class="metric-label">Daily P/L</div>
                    <hr>
                    <div>
                        <span class="status-badge {'status-enabled' if state.get('trading_enabled') else 'status-disabled'}">
                            {'🟢 ENABLED' if state.get('trading_enabled') else '🔴 DISABLED'}
                        </span>
                        <span class="status-badge {'status-open' if market_open else 'status-closed'}">
                            {'🟢 MARKET OPEN' if market_open else '🔴 MARKET CLOSED'}
                        </span>
                    </div>
                    <div style="margin-top: 10px;">
                        <span class="status-badge mode-{asset_mode}">{mode_text}</span>
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
                    <h3>💰 Profit Metrics</h3>
                    <div class="metric" id="weeklyPnlDisplay">$--</div>
                    <div class="metric-label">Weekly Total</div>
                    <div id="dailyPercentDisplay" style="font-size: 0.9rem;">--% today</div>
                    <hr>
                    <div>💰 USDT Balance: $<span id="usdtBalance">--</span></div>
                    <div>🏆 Best Trade: <span id="bestTrade">--</span></div>
                </div>
            </div>
            
            <div class="card">
                <h3>🎮 Controls</h3>
                <div class="flex">
                    <form method="post" action="/enable"><button class="success">▶️ Enable</button></form>
                    <form method="post" action="/disable"><button class="danger">⏸️ Disable</button></form>
                    <form method="post" action="/toggle-asset-mode"><button>🔄 Toggle Mode</button></form>
                    <form method="post" action="/reset-daily-stats"><button class="warning">📊 Reset Stats</button></form>
                    <form method="post" action="/clear-errors"><button>🗑️ Clear Errors</button></form>
                    <form method="post" action="/backup-now"><button>💾 Backup</button></form>
                    <a href="/webhook-tester"><button>📡 Test Webhook</button></a>
                    <a href="/export-trades"><button>📥 Export Trades</button></a>
                    <form method="post" action="/logout"><button>🚪 Logout</button></form>
                </div>
            </div>
            
            <div class="card">
                <h3>🔍 Symbol Filter</h3>
                <form method="post" action="/update-allowed-symbols" class="flex">
                    <input type="text" name="add_symbol" placeholder="Add symbol (e.g., MSFT, DOGE/USD)" style="flex:1; padding:10px; border-radius:8px; background:rgba(255,255,255,0.2); color:white; border:none;">
                    <button type="submit">➕ Add</button>
                </form>
                <div class="section-title">📈 Stocks:</div>
                <div class="flex">
                    {''.join([f'<div class="symbol-tag">{s}<form method="post" action="/toggle-symbol/{s}" style="display:inline;"><button type="submit">✕</button></form></div>' for s in stock_symbols])}
                </div>
                <div class="section-title">🪙 Crypto:</div>
                <div class="flex">
                    {''.join([f'<div class="symbol-tag">{s}<form method="post" action="/toggle-symbol/{s}" style="display:inline;"><button type="submit">✕</button></form></div>' for s in crypto_symbols])}
                </div>
            </div>
            
            <div class="card">
                <h3>⚙️ Risk Settings</h3>
                <form method="post" action="/update-risk-settings">
                    <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));">
                        <div><label>Max $/trade:</label><input type="number" name="max_dollars_per_trade" value="{state.get('max_dollars_per_trade', 50)}" step="10" style="width:100%; padding:8px; background:rgba(255,255,255,0.2); border:none; border-radius:6px; color:white;"></div>
                        <div><label>Max trades/day:</label><input type="number" name="max_trades_per_day" value="{state.get('max_trades_per_day', 5)}" step="1" style="width:100%; padding:8px; background:rgba(255,255,255,0.2); border:none; border-radius:6px; color:white;"></div>
                        <div><label>Stop Loss %:</label><input type="number" name="stop_loss_percent" value="{state.get('stop_loss_percent', 1.0)}" step="0.5" style="width:100%; padding:8px; background:rgba(255,255,255,0.2); border:none; border-radius:6px; color:white;"></div>
                    </div>
                    <button type="submit">💾 Save Settings</button>
                </form>
            </div>
            
            <div class="card">
                <h3>📅 Weekly Breakdown</h3>
                <div style="overflow-x: auto;">
                    <table id="weeklyTable">
                        <thead><tr><th>Date</th><th>P&L</th><th>Trades</th><th>Win Rate</th></tr></thead>
                        <tbody id="weeklyTableBody"><tr><td colspan="4">Loading...</td></tr></tbody>
                    </table>
                </div>
            </div>
            
            {f'''
            <div class="card">
                <h3>📊 Open Positions ({len(positions)})</h3>
                <form method="post" action="/close-all-positions" onsubmit="return confirm('Close ALL positions?');" style="text-align:right;">
                    <button type="submit" class="danger">🚨 Close All</button>
                </form>
                <table>
                    <tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>Unrealized P/L</th></tr>
                    {''.join([f'<tr><td>{p["symbol"]}</td><td>{p["qty"]}</td><td>${float(p["avg_entry_price"]):.2f}</td><td style="color: {"#00c853" if float(p["unrealized_pl"]) > 0 else "#d32f2f"}">${float(p["unrealized_pl"]):.2f}</td></tr>' for p in positions])}
                </table>
            </div>
            ''' if positions else ''}
            
            <div class="card">
                <h3>ℹ️ Info</h3>
                <div>Mode: {TRADING_MODE.upper()}</div>
                <div>Last Reset: {state.get('last_reset_date', 'Never')}</div>
                <div>Disabled Strategies: {', '.join(state.get('disabled_strategies', [])) or 'None'}</div>
            </div>
        </div>
        
        <script>
            function refreshNotifications() {{
                fetch('/api/notifications')
                    .then(r => r.json())
                    .then(data => {{
                        const container = document.getElementById('notifications');
                        if (container && data.notifications) {{
                            container.innerHTML = data.notifications.map(n => '<div class=\"notification notification-' + n.priority + '\">' + n.message + '</div>').join('');
                        }}
                    }})
                    .catch(err => console.log('Notification error:', err));
            }}
            
            function refreshProfitMetrics() {{
                fetch('/api/profit-metrics')
                    .then(r => r.json())
                    .then(data => {{
                        document.getElementById('weeklyPnlDisplay').innerHTML = '$' + data.weekly_pnl;
                        document.getElementById('weeklyPnlDisplay').style.color = data.weekly_pnl >= 0 ? '#00c853' : '#d32f2f';
                        var percentSign = data.daily_percent >= 0 ? '+' : '';
                        document.getElementById('dailyPercentDisplay').innerHTML = percentSign + data.daily_percent + '% today';
                        document.getElementById('usdtBalance').innerHTML = data.usdt_balance;
                        document.getElementById('bestTrade').innerHTML = '<span style=\"color:#00c853;\">+$' + data.best_trade + '</span> on ' + (data.best_trade_symbol || 'N/A');
                        
                        const pnlElement = document.getElementById('dailyPnl');
                        if (pnlElement) {{
                            pnlElement.textContent = '$' + data.daily_pnl;
                            pnlElement.style.color = data.daily_pnl >= 0 ? '#00c853' : '#d32f2f';
                        }}
                    }})
                    .catch(err => console.log('Profit metrics error:', err));
                
                fetch('/api/weekly-breakdown')
                    .then(r => r.json())
                    .then(data => {{
                        const tbody = document.getElementById('weeklyTableBody');
                        if (tbody) {{
                            let html = '';
                            for (const [date, stats] of Object.entries(data)) {{
                                const pnlColor = stats.pnl >= 0 ? '#00c853' : '#d32f2f';
                                const pnlSign = stats.pnl >= 0 ? '+' : '';
                                html += '<tr><td>' + date + '</td><td style=\"color: ' + pnlColor + ';\">' + pnlSign + '$' + stats.pnl.toFixed(2) + '</td><td>' + stats.trades + '</td><td>' + stats.win_rate + '%</td></tr>';
                            }}
                            if (Object.keys(data).length === 0) {{
                                html = '<tr><td colspan=\"4\">No trades this week</td></tr>';
                            }}
                            tbody.innerHTML = html;
                        }}
                    }})
                    .catch(err => console.log('Weekly breakdown error:', err));
            }}
            
            setInterval(() => {{
                refreshNotifications();
                refreshProfitMetrics();
            }}, 10000);
            
            refreshNotifications();
            refreshProfitMetrics();
        </script>
    </body>
    </html>
    """


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)