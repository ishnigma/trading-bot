# app.py
# FastAPI dashboard + webhook for stock/crypto trading bot.

from datetime import datetime

from fastapi import Cookie, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from config import DASHBOARD_PASSWORD, TRADING_MODE, WEBHOOK_SECRET
from helpers import (
    build_bracket_order,
    calculate_qty_from_price,
    client,
    get_daily_pnl,
    get_trades_from_db,
    init_database,
    load_state,
    record_error,
    record_successful_trade,
    save_state,
    save_trade_to_db,
    send_telegram_message,
    validate_trade,
    write_log,
)

app = FastAPI()


def is_logged_in(session: str = ""):
    # Simple dashboard login cookie check.
    return session == "logged_in"


@app.on_event("startup")
def startup_event():
    # Initialize database when app starts.
    init_database()
    load_state()
    write_log("App started")


@app.get("/")
def home():
    # Public keep-alive endpoint for UptimeRobot.
    return {"ok": True, "message": "Trading bot is running"}


@app.post("/login")
def login(response: Response, password: str = Form(...)):
    # Dashboard login.
    if password != DASHBOARD_PASSWORD:
        raise HTTPException(status_code=401, detail="Wrong password")

    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(key="session", value="logged_in", httponly=True)
    return response


@app.post("/logout")
def logout():
    # Dashboard logout.
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.delete_cookie(key="session")
    return response


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(session: str = Cookie(default="")):
    # Main dashboard.
    if not is_logged_in(session):
        return """
        <html>
            <head><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
            <body>
                <h2>Trading Bot Login</h2>
                <form action="/login" method="post">
                    <input type="password" name="password" placeholder="Dashboard password" />
                    <button type="submit">Login</button>
                </form>
            </body>
        </html>
        """

    state = load_state()
    trades = get_trades_from_db(20)

    try:
        daily_pnl = get_daily_pnl()
    except Exception as error:
        daily_pnl = 0
        write_log(f"Dashboard daily P/L error: {error}")

    asset_mode = state.get("asset_mode", "stocks")
    last_updated = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")

    trade_rows = "".join(
        [
            f"<tr><td>{t.get('time')}</td><td>{t.get('symbol')}</td><td>{t.get('action')}</td><td>{t.get('qty')}</td><td>{t.get('price')}</td><td>{t.get('strategy')}</td></tr>"
            for t in trades
        ]
    )

    return f"""
    <html>
        <head>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                .card {{ border: 1px solid #ddd; border-radius: 10px; padding: 15px; margin: 15px 0; }}
                button {{ padding: 10px; margin: 5px 0; }}
                input {{ padding: 8px; margin: 5px 0; }}
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ border: 1px solid #ddd; padding: 6px; font-size: 13px; }}
                @media (max-width: 700px) {{ table {{ display: block; overflow-x: auto; }} button, input {{ width: 100%; }} }}
            </style>
        </head>
        <body>
            <h1>Trading Bot Dashboard</h1>
            <p><b>Last Updated:</b> {last_updated}</p>
            <p><b>Trading Mode:</b> {TRADING_MODE}</p>
            <p><b>Daily P/L:</b> ${daily_pnl:.2f}</p>

            <div class="card">
                <h2>Asset Mode</h2>
                <p><b>Current Asset Mode:</b> {asset_mode}</p>

                <form action="/update-asset-mode" method="post">
                    <label>
                        <input type="radio" name="asset_mode" value="stocks" {'checked' if asset_mode == 'stocks' else ''}>
                        Stocks Only
                    </label><br>

                    <label>
                        <input type="radio" name="asset_mode" value="crypto" {'checked' if asset_mode == 'crypto' else ''}>
                        Crypto Only
                    </label><br>

                    <label>
                        <input type="radio" name="asset_mode" value="both" {'checked' if asset_mode == 'both' else ''}>
                        Both Stocks + Crypto
                    </label><br><br>

                    <button type="submit">Save Asset Mode</button>
                </form>
            </div>

            <div class="card">
                <h2>Controls</h2>
                <form action="/enable" method="post"><button type="submit">Turn Trading ON</button></form>
                <form action="/disable" method="post"><button type="submit">Turn Trading OFF</button></form>
                <form action="/logout" method="post"><button type="submit">Logout</button></form>
            </div>

            <div class="card">
                <h2>Status</h2>
                <p><b>Trading Enabled:</b> {state.get('trading_enabled', True)}</p>
                <p><b>Maintenance Mode:</b> {state.get('maintenance_mode', False)}</p>
                <p><b>Allowed Symbols:</b> {', '.join(state.get('allowed_symbols', []))}</p>
                <p><b>Error Count:</b> {state.get('error_count', 0)}</p>
                <p><b>Last Error:</b> {state.get('last_error_message')}</p>
            </div>

            <div class="card">
                <h2>Recent Trades</h2>
                <table>
                    <tr><th>Time</th><th>Symbol</th><th>Action</th><th>Qty</th><th>Price</th><th>Strategy</th></tr>
                    {trade_rows}
                </table>
            </div>
        </body>
    </html>
    """


@app.post("/update-asset-mode")
def update_asset_mode(
    asset_mode: str = Form(...),
    session: str = Cookie(default=""),
):
    # Save stock/crypto/both mode from dashboard radio buttons.
    if not is_logged_in(session):
        raise HTTPException(status_code=401, detail="Not logged in")

    if asset_mode not in ["stocks", "crypto", "both"]:
        raise HTTPException(status_code=400, detail="Invalid asset mode")

    state = load_state()
    state["asset_mode"] = asset_mode
    save_state(state)
    write_log(f"Asset mode updated: {asset_mode}")
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/enable")
def enable_trading(session: str = Cookie(default="")):
    # Turn trading on.
    if not is_logged_in(session):
        raise HTTPException(status_code=401, detail="Not logged in")

    state = load_state()
    state["trading_enabled"] = True
    save_state(state)
    write_log("Trading enabled")
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/disable")
def disable_trading(session: str = Cookie(default="")):
    # Turn trading off.
    if not is_logged_in(session):
        raise HTTPException(status_code=401, detail="Not logged in")

    state = load_state()
    state["trading_enabled"] = False
    save_state(state)
    write_log("Trading disabled")
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/state")
def get_state():
    # JSON state for debugging.
    return load_state()


@app.post("/webhook")
async def webhook(request: Request):
    # TradingView-compatible webhook endpoint.
    state = load_state()
    data = await request.json()

    if data.get("secret") != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Wrong secret")

    symbol = str(data.get("symbol", "")).upper()
    action = str(data.get("action", "")).lower()
    strategy = data.get("strategy", "unknown")
    reason = data.get("reason", "")
    price = float(data.get("price", 0))

    state["last_webhook_time"] = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
    state["last_webhook_symbol"] = symbol
    state["last_webhook_action"] = action
    save_state(state)

    if action == "heartbeat":
        write_log("Heartbeat received")
        return {"ok": True, "message": "Heartbeat received"}

    qty = data.get("qty")
    if qty is None:
        qty = calculate_qty_from_price(price, state)
    else:
        qty = float(qty)

    try:
        validate_trade(state, symbol, action, price, qty)
        order = build_bracket_order(symbol, qty, action, price, state)
        placed_order = client.submit_order(order_data=order)
        record_successful_trade(state)
        save_trade_to_db(symbol, action, qty, price, placed_order.id, strategy, reason)
        send_telegram_message(f"Trade placed: {symbol} {action} qty={qty}")
        return {"ok": True, "order_id": str(placed_order.id)}
    except ValueError as error:
        record_error(state, str(error))
        raise HTTPException(status_code=400, detail=str(error))
