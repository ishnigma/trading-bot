# app.py
# Full copy/paste version for Render + OANDA forex-only bot

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
import secrets
import requests

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Cookie, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from config import (
    DASHBOARD_PASSWORD,
    WEBHOOK_SECRET,
    TRADING_MODE,
    BOT_VERSION,
    STRATEGY_ENABLED,
    STRATEGY_TIMEFRAME,
    STRATEGY_TYPE,
    PUBLIC_BASE_URL,
)

from helpers import (
    init_database,
    load_state,
    save_state,
    write_log,
    reset_daily_state_if_needed,
    get_daily_pnl,
    get_open_positions,
    close_all_positions,
    get_trades_from_db,
    is_market_open,
    sync_recent_orders,
    check_heartbeat_warning,
    check_market_status_alert,
    send_scheduled_daily_report,
    make_daily_backup,
    clean_old_backups,
    disable_trading_end_of_day,
    enable_trading_morning,
    get_heartbeat_status,
)

from strategy import execute_autonomous_trade, monitor_open_positions

try:
    from telegram_bot import check_telegram_commands
except Exception:
    def check_telegram_commands():
        # Telegram is optional. This keeps the app alive if telegram_bot.py is missing.
        return


scheduler = BackgroundScheduler()
market_timezone = ZoneInfo("America/New_York")
active_sessions = {}


def create_session():
    # Create simple dashboard login session
    token = secrets.token_urlsafe(32)
    active_sessions[token] = datetime.now()
    return token


def is_logged_in(session: str = ""):
    # Check dashboard login session
    if session not in active_sessions:
        return False

    if datetime.now() - active_sessions[session] > timedelta(hours=8):
        del active_sessions[session]
        return False

    return True


def keep_alive_self():
    # Keeps Render warm when PUBLIC_BASE_URL is set
    if not PUBLIC_BASE_URL:
        return

    try:
        requests.get(f"{PUBLIC_BASE_URL}/keep-alive", timeout=20)
        write_log("Keep-alive ping sent")
    except Exception as error:
        write_log(f"Keep-alive ping failed: {error}")


def validate_config():
    # Basic safety check
    missing = []

    if not DASHBOARD_PASSWORD:
        missing.append("DASHBOARD_PASSWORD")

    if not WEBHOOK_SECRET:
        missing.append("WEBHOOK_SECRET")

    if missing:
        write_log(f"Missing required config values: {missing}")
        return False

    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs when Render starts the bot
    write_log("Trading bot starting")

    if not validate_config():
        raise RuntimeError("Invalid configuration")

    init_database()

    scheduler.add_job(check_telegram_commands, "interval", seconds=10)
    scheduler.add_job(sync_recent_orders, "interval", minutes=2)
    scheduler.add_job(check_heartbeat_warning, "interval", minutes=5)
    scheduler.add_job(check_market_status_alert, "interval", minutes=5)
    scheduler.add_job(keep_alive_self, "interval", minutes=10)

    scheduler.add_job(send_scheduled_daily_report, "cron", hour=16, minute=10, timezone=market_timezone)
    scheduler.add_job(make_daily_backup, "cron", hour=16, minute=20, timezone=market_timezone)
    scheduler.add_job(clean_old_backups, "cron", hour=16, minute=30, timezone=market_timezone)

    scheduler.add_job(disable_trading_end_of_day, "cron", hour=15, minute=45, timezone=market_timezone)
    scheduler.add_job(enable_trading_morning, "cron", hour=9, minute=35, timezone=market_timezone)

    if STRATEGY_ENABLED:
        scheduler.add_job(execute_autonomous_trade, "interval", minutes=int(STRATEGY_TIMEFRAME))
        scheduler.add_job(monitor_open_positions, "interval", minutes=1)
        write_log(f"Autonomous strategy enabled every {STRATEGY_TIMEFRAME} minutes")

    scheduler.start()
    write_log("Scheduler started")

    yield

    if scheduler.running:
        scheduler.shutdown()

    write_log("Trading bot stopped")


app = FastAPI(title="OANDA Forex Trading Bot", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])


@app.get("/")
def home():
    return RedirectResponse("/dashboard")


@app.get("/health")
def health_check():
    # Render health check
    try:
        state = load_state()
        return {
            "status": "healthy",
            "bot_version": BOT_VERSION,
            "trading_enabled": state.get("trading_enabled", False),
            "forex_enabled": state.get("forex_trading_enabled", True),
            "strategy_enabled": STRATEGY_ENABLED,
            "scheduler_running": scheduler.running,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as error:
        return JSONResponse(status_code=500, content={"status": "unhealthy", "error": str(error)})


@app.get("/keep-alive")
def keep_alive():
    # Used by Render keep-alive ping
    return {
        "status": "alive",
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Trading Bot Login</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                background: #f4f6f8;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
            }
            .box {
                background: white;
                padding: 30px;
                border-radius: 12px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.15);
                width: 340px;
            }
            input, button {
                width: 100%;
                padding: 12px;
                margin-top: 12px;
                border-radius: 8px;
                border: 1px solid #ccc;
                font-size: 16px;
            }
            button {
                background: #0d6efd;
                color: white;
                border: none;
                cursor: pointer;
            }
        </style>
    </head>
    <body>
        <form class="box" method="post" action="/login">
            <h2>Trading Bot Login</h2>
            <input type="password" name="password" placeholder="Dashboard Password" required>
            <button type="submit">Login</button>
        </form>
    </body>
    </html>
    """


@app.post("/login")
def login(password: str = Form(...)):
    if password != DASHBOARD_PASSWORD:
        return HTMLResponse("Wrong password. Go back and try again.", status_code=401)

    session = create_session()
    response = RedirectResponse("/dashboard", status_code=302)
    response.set_cookie("session", session, httponly=True, max_age=28800)
    return response


@app.get("/logout")
def logout(session: str = Cookie(default="")):
    if session in active_sessions:
        del active_sessions[session]

    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("session")
    return response


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(session: str = Cookie(default="")):
    if not is_logged_in(session):
        return RedirectResponse("/login", status_code=302)

    state = load_state()
    reset_daily_state_if_needed(state)

    # OANDA is forex only
    state["asset_mode"] = "forex"

    if "forex_trading_enabled" not in state:
        state["forex_trading_enabled"] = True

    save_state(state)

    trading_enabled = state.get("trading_enabled", False)
    forex_enabled = state.get("forex_trading_enabled", True)
    market_open = is_market_open()
    daily_pnl = get_daily_pnl()
    positions = get_open_positions()
    trades = get_trades_from_db(20)
    heartbeat = get_heartbeat_status()

    trading_status = "ON" if trading_enabled else "OFF"
    forex_status = "Enabled" if forex_enabled else "Disabled"
    market_status = "Open" if market_open else "Closed"
    keep_alive_status = "ON" if PUBLIC_BASE_URL else "OFF"

    checked_enable = "checked" if forex_enabled else ""
    checked_disable = "" if forex_enabled else "checked"

    position_rows = ""
    for position in positions:
        position_rows += f"""
        <tr>
            <td>{position.get('symbol', '')}</td>
            <td>{position.get('side', '')}</td>
            <td>{position.get('qty', '')}</td>
            <td>{position.get('avg_entry_price', '')}</td>
            <td>{position.get('unrealized_pl', '')}</td>
        </tr>
        """

    if not position_rows:
        position_rows = "<tr><td colspan='5'>No open positions</td></tr>"

    trade_rows = ""
    for trade in trades:
        trade_rows += f"""
        <tr>
            <td>{trade.get('time', '')}</td>
            <td>{trade.get('symbol', '')}</td>
            <td>{trade.get('action', '')}</td>
            <td>{trade.get('units', '')}</td>
            <td>{trade.get('price', '')}</td>
            <td>{trade.get('strategy', '')}</td>
        </tr>
        """

    if not trade_rows:
        trade_rows = "<tr><td colspan='6'>No trades yet</td></tr>"

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>OANDA Forex Trading Bot</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #f4f6f8;
                margin: 0;
                padding: 20px;
                color: #222;
            }}
            .top {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 20px;
            }}
            .grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
                gap: 16px;
            }}
            .card {{
                background: white;
                padding: 18px;
                border-radius: 12px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.08);
            }}
            .big {{
                font-size: 30px;
                font-weight: bold;
            }}
            .green {{
                color: #0a8f3c;
            }}
            .red {{
                color: #c62828;
            }}
            .yellow {{
                color: #a66b00;
            }}
            button {{
                padding: 10px 14px;
                border: none;
                border-radius: 8px;
                cursor: pointer;
                font-size: 15px;
                margin: 4px 0;
            }}
            .btn-green {{
                background: #0a8f3c;
                color: white;
            }}
            .btn-red {{
                background: #c62828;
                color: white;
            }}
            .btn-blue {{
                background: #0d6efd;
                color: white;
            }}
            .btn-gray {{
                background: #555;
                color: white;
            }}
            input {{
                padding: 10px;
                border-radius: 8px;
                border: 1px solid #ccc;
                width: 100%;
                box-sizing: border-box;
                margin-bottom: 8px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                background: white;
            }}
            th, td {{
                border-bottom: 1px solid #ddd;
                padding: 8px;
                text-align: left;
                font-size: 14px;
            }}
            .radio-box {{
                display: flex;
                gap: 20px;
                margin: 12px 0;
            }}
            .radio-box label {{
                background: #f0f2f5;
                padding: 12px;
                border-radius: 8px;
                flex: 1;
                cursor: pointer;
            }}
            .small {{
                color: #666;
                font-size: 13px;
            }}
        </style>
    </head>
    <body>
        <div class="top">
            <h1>OANDA Forex Trading Bot</h1>
            <a href="/logout">Logout</a>
        </div>

        <div class="grid">
            <div class="card">
                <h3>Trading Status</h3>
                <div class="big {'green' if trading_enabled else 'red'}">{trading_status}</div>
                <p>Forex market setting: <strong>{forex_status}</strong></p>
                <p>Market: <strong>{market_status}</strong></p>
                <form method="post" action="/enable-trading">
                    <button class="btn-green" type="submit">Enable Trading</button>
                </form>
                <form method="post" action="/disable-trading">
                    <button class="btn-red" type="submit">Disable Trading</button>
                </form>
            </div>

            <div class="card">
                <h3>Market Settings</h3>
                <p>OANDA is forex only, so stock market options were removed.</p>
                <form method="post" action="/update-market-settings">
                    <div class="radio-box">
                        <label>
                            <input type="radio" name="forex_trading_enabled" value="true" {checked_enable}>
                            Enable Forex
                        </label>
                        <label>
                            <input type="radio" name="forex_trading_enabled" value="false" {checked_disable}>
                            Disable Forex
                        </label>
                    </div>
                    <button class="btn-blue" type="submit">Save Market Settings</button>
                </form>
            </div>

            <div class="card">
                <h3>Profit / Loss</h3>
                <div class="big {'green' if daily_pnl >= 0 else 'red'}">${daily_pnl:.2f}</div>
                <p>Daily P&L</p>
            </div>

            <div class="card">
                <h3>Keep Alive</h3>
                <div class="big {'green' if PUBLIC_BASE_URL else 'yellow'}">{keep_alive_status}</div>
                <p class="small">Set PUBLIC_BASE_URL in Render to your app URL so it can ping itself.</p>
                <p class="small">Example: https://your-app-name.onrender.com</p>
            </div>

            <div class="card">
                <h3>Strategy</h3>
                <p>Strategy Enabled: <strong>{STRATEGY_ENABLED}</strong></p>
                <p>Strategy Type: <strong>{STRATEGY_TYPE}</strong></p>
                <p>Timeframe: <strong>{STRATEGY_TIMEFRAME} min</strong></p>
            </div>

            <div class="card">
                <h3>Heartbeat</h3>
                <p>{heartbeat}</p>
            </div>
        </div>

        <div class="card" style="margin-top: 16px;">
            <h3>Risk Settings</h3>
            <form method="post" action="/update-risk-settings">
                <div class="grid">
                    <div>
                        <label>Max Units Per Trade</label>
                        <input type="number" name="max_units_per_trade" value="{state.get('max_units_per_trade', 10000)}">
                    </div>
                    <div>
                        <label>Max Trades Per Day</label>
                        <input type="number" name="max_trades_per_day" value="{state.get('max_trades_per_day', 5)}">
                    </div>
                    <div>
                        <label>Stop Loss Pips</label>
                        <input type="number" name="stop_loss_pips" value="{state.get('stop_loss_pips', 50)}">
                    </div>
                    <div>
                        <label>Take Profit Pips</label>
                        <input type="number" name="take_profit_pips" value="{state.get('take_profit_pips', 50)}">
                    </div>
                    <div>
                        <label>Daily Loss Limit</label>
                        <input type="number" name="daily_loss_limit" value="{state.get('daily_loss_limit', 50)}">
                    </div>
                </div>
                <button class="btn-blue" type="submit">Save Risk Settings</button>
            </form>
        </div>

        <div class="card" style="margin-top: 16px;">
            <h3>Positions</h3>
            <form method="post" action="/close-all-positions">
                <button class="btn-red" type="submit">Close All Positions</button>
            </form>
            <table>
                <thead>
                    <tr>
                        <th>Symbol</th>
                        <th>Side</th>
                        <th>Units</th>
                        <th>Entry</th>
                        <th>P&L</th>
                    </tr>
                </thead>
                <tbody>
                    {position_rows}
                </tbody>
            </table>
        </div>

        <div class="card" style="margin-top: 16px;">
            <h3>Recent Trades</h3>
            <table>
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Symbol</th>
                        <th>Action</th>
                        <th>Units</th>
                        <th>Price</th>
                        <th>Strategy</th>
                    </tr>
                </thead>
                <tbody>
                    {trade_rows}
                </tbody>
            </table>
        </div>
    </body>
    </html>
    """


@app.post("/enable-trading")
def enable_trading(session: str = Cookie(default="")):
    if not is_logged_in(session):
        return RedirectResponse("/login", status_code=302)

    state = load_state()
    state["trading_enabled"] = True
    state["asset_mode"] = "forex"
    save_state(state)
    write_log("Trading enabled from dashboard")
    return RedirectResponse("/dashboard", status_code=302)


@app.post("/disable-trading")
def disable_trading(session: str = Cookie(default="")):
    if not is_logged_in(session):
        return RedirectResponse("/login", status_code=302)

    state = load_state()
    state["trading_enabled"] = False
    save_state(state)
    write_log("Trading disabled from dashboard")
    return RedirectResponse("/dashboard", status_code=302)


@app.post("/update-market-settings")
def update_market_settings(
    forex_trading_enabled: str = Form(...),
    session: str = Cookie(default=""),
):
    if not is_logged_in(session):
        return RedirectResponse("/login", status_code=302)

    state = load_state()

    # OANDA is forex only
    state["asset_mode"] = "forex"
    state["forex_trading_enabled"] = forex_trading_enabled.lower() == "true"

    save_state(state)
    write_log(f"Forex trading setting changed to {state['forex_trading_enabled']}")
    return RedirectResponse("/dashboard", status_code=302)


@app.post("/update-risk-settings")
def update_risk_settings(
    max_units_per_trade: int = Form(...),
    max_trades_per_day: int = Form(...),
    stop_loss_pips: int = Form(...),
    take_profit_pips: int = Form(...),
    daily_loss_limit: float = Form(...),
    session: str = Cookie(default=""),
):
    if not is_logged_in(session):
        return RedirectResponse("/login", status_code=302)

    state = load_state()
    state["max_units_per_trade"] = max_units_per_trade
    state["max_trades_per_day"] = max_trades_per_day
    state["stop_loss_pips"] = stop_loss_pips
    state["take_profit_pips"] = take_profit_pips
    state["daily_loss_limit"] = daily_loss_limit

    save_state(state)
    write_log("Risk settings updated from dashboard")
    return RedirectResponse("/dashboard", status_code=302)


@app.post("/close-all-positions")
def close_positions(session: str = Cookie(default="")):
    if not is_logged_in(session):
        return RedirectResponse("/login", status_code=302)

    close_all_positions()

    state = load_state()
    state["position_open"] = False
    state["current_position_symbol"] = None
    save_state(state)

    write_log("Close all positions requested from dashboard")
    return RedirectResponse("/dashboard", status_code=302)


@app.get("/api/notifications")
def get_notifications(session: str = Cookie(default="")):
    if not is_logged_in(session):
        raise HTTPException(401, "Not logged in")

    state = load_state()
    notifications = []

    if state.get("trading_enabled", False):
        notifications.append({
            "type": "trading",
            "message": "Trading is enabled",
            "priority": "low",
        })
    else:
        notifications.append({
            "type": "trading",
            "message": "Trading is disabled",
            "priority": "high",
        })

    if state.get("forex_trading_enabled", True):
        notifications.append({
            "type": "market",
            "message": "Forex trading is enabled",
            "priority": "low",
        })
    else:
        notifications.append({
            "type": "market",
            "message": "Forex trading is disabled",
            "priority": "high",
        })

    notifications.append({
        "type": "strategy",
        "message": f"Auto Strategy: {STRATEGY_TYPE} every {STRATEGY_TIMEFRAME} minutes",
        "priority": "low",
    })

    if not PUBLIC_BASE_URL:
        notifications.append({
            "type": "keep_alive",
            "message": "PUBLIC_BASE_URL is not set in Render. Keep-alive is off.",
            "priority": "medium",
        })

    return {"notifications": notifications}


@app.get("/api/profit-metrics")
def get_profit_metrics(session: str = Cookie(default="")):
    if not is_logged_in(session):
        raise HTTPException(401, "Not logged in")

    daily_pnl = get_daily_pnl()
    trades = get_trades_from_db(500)

    return {
        "daily_pnl": round(daily_pnl, 2),
        "trade_count": len(trades),
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/weekly-breakdown")
def get_weekly_breakdown(session: str = Cookie(default="")):
    if not is_logged_in(session):
        raise HTTPException(401, "Not logged in")

    trades = get_trades_from_db(500)
    today = datetime.now().date()
    breakdown = {}

    for i in range(7):
        date = today - timedelta(days=i)
        breakdown[date.isoformat()] = {
            "pnl": 0,
            "trades": 0,
            "wins": 0,
            "win_rate": 0,
        }

    for trade in trades:
        try:
            trade_time = trade.get("time")
            if not trade_time:
                continue

            trade_date = datetime.fromisoformat(trade_time).date()
            date_key = trade_date.isoformat()

            if date_key not in breakdown:
                continue

            breakdown[date_key]["trades"] += 1

        except Exception:
            continue

    return breakdown


@app.get("/api/forex-override")
def get_forex_override(session: str = Cookie(default="")):
    if not is_logged_in(session):
        raise HTTPException(401, "Not logged in")

    state = load_state()

    return {
        "force_forex_trading": state.get("force_forex_trading", False),
        "forex_trading_enabled": state.get("forex_trading_enabled", True),
    }


@app.post("/toggle-forex-override")
def toggle_forex_override(session: str = Cookie(default="")):
    if not is_logged_in(session):
        return RedirectResponse("/login", status_code=302)

    state = load_state()
    state["force_forex_trading"] = not state.get("force_forex_trading", False)
    save_state(state)

    write_log(f"Forex override changed to {state['force_forex_trading']}")
    return RedirectResponse("/dashboard", status_code=302)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))

    # Required by Render
    uvicorn.run(app, host="0.0.0.0", port=port)
