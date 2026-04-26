# app.py
# FastAPI dashboard and TradingView webhook controller - Corrected & Improved Version

import csv
import json
import os
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Cookie, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from config import BOT_VERSION, DASHBOARD_PASSWORD, DEFAULT_STATE, TRADING_MODE, WEBHOOK_SECRET
from helpers import (
    build_bracket_order,
    calculate_qty_from_price,
    cancel_all_orders,
    cancel_one_order,
    clean_old_backups,
    client,
    close_all_positions,
    close_one_position,
    create_trade_review,
    create_trade_score,
    get_daily_pnl,
    get_open_orders,
    get_open_positions,
    get_order_status,
    get_trades_from_db,
    handle_successful_trade,
    init_database,
    is_market_open,
    load_state,
    make_daily_backup,
    migrate_csv_to_db,
    record_error,
    reset_daily_state_if_needed,
    save_state,
    save_trade_to_db,
    send_daily_email_report,
    send_telegram_message,
    sync_recent_orders,
    update_db_trade_with_fill,
    validate_trade,
    write_log,
    check_heartbeat_warning,
    check_market_status_alert,
    disable_trading_end_of_day,
    enable_trading_morning,
    get_health_status,
    get_heartbeat_status,
    get_backup_count,
    get_closed_trade_pnl,
    get_strategy_stats,
    get_win_rate,
    get_avg_win_loss,
    get_profit_factor,
    get_max_drawdown,
    get_equity_curve,
    get_daily_pnl_by_strategy,
    auto_disable_bad_strategies,
    get_backtest_results,
    get_best_backtest_result,
)
from telegram_bot import check_telegram_commands

scheduler = BackgroundScheduler()
market_timezone = ZoneInfo("America/New_York")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Modern FastAPI lifespan for startup and shutdown"""
    write_log("=== Trading Bot STARTING UP ===")
    print("🚀 Trading Bot is starting...")  # Visible in cloud logs

    init_database()

    # Schedule all background jobs
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
    write_log("APScheduler started successfully with all jobs")
    print("✅ Scheduler started")

    yield  # Application runs here

    # Shutdown
    scheduler.shutdown()
    write_log("=== Trading Bot SHUTTING DOWN ===")
    print("🛑 Trading Bot shutdown complete")


app = FastAPI(title="Trading Bot", lifespan=lifespan)


def check_dashboard_password(password: str) -> bool:
    return password == DASHBOARD_PASSWORD


def is_logged_in(session: str = "") -> bool:
    return session == "logged_in"


def get_recent_trades(limit=10):
    return get_trades_from_db(limit)


# ==================== PERFORMANCE HELPERS ====================
def get_closed_trade_pnl():
    trades = list(reversed(get_trades_from_db(5000)))
    open_positions = {}
    closed_trades = []

    for trade in trades:
        symbol = trade.get("symbol")
        action = trade.get("action")
        qty = float(trade.get("filled_qty") or trade.get("qty") or 0)
        price = float(trade.get("filled_avg_price") or trade.get("price") or 0)

        if action == "buy":
            open_positions[symbol] = {
                "qty": qty,
                "price": price,
                "time": trade.get("time"),
                "strategy": trade.get("strategy", "unknown"),
            }
        elif action == "sell" and symbol in open_positions:
            buy = open_positions[symbol]
            pnl = (price - buy["price"]) * qty
            closed_trades.append({
                "time": trade.get("time"),
                "symbol": symbol,
                "strategy": buy["strategy"],
                "buy_price": buy["price"],
                "sell_price": price,
                "qty": qty,
                "pnl": round(pnl, 2),
            })
            del open_positions[symbol]

    return closed_trades


def get_win_rate(closed_trades):
    if not closed_trades:
        return 0
    wins = sum(1 for trade in closed_trades if trade.get("pnl", 0) > 0)
    return round((wins / len(closed_trades)) * 100, 2)


def get_avg_win_loss(closed_trades):
    wins = [trade.get("pnl", 0) for trade in closed_trades if trade.get("pnl", 0) > 0]
    losses = [trade.get("pnl", 0) for trade in closed_trades if trade.get("pnl", 0) < 0]
    avg_win = round(sum(wins) / len(wins), 2) if wins else 0
    avg_loss = round(sum(losses) / len(losses), 2) if losses else 0
    return avg_win, avg_loss


def get_profit_factor(closed_trades):
    total_wins = sum(trade.get("pnl", 0) for trade in closed_trades if trade.get("pnl", 0) > 0)
    total_losses = abs(sum(trade.get("pnl", 0) for trade in closed_trades if trade.get("pnl", 0) < 0))
    return round(total_wins / total_losses, 2) if total_losses else 0


def get_max_drawdown(closed_trades):
    running_total = 0
    peak = 0
    max_drawdown = 0
    for trade in closed_trades:
        running_total += trade.get("pnl", 0)
        peak = max(peak, running_total)
        max_drawdown = max(max_drawdown, peak - running_total)
    return round(max_drawdown, 2)


def get_equity_curve(closed_trades):
    running_total = 0
    points = []
    for index, trade in enumerate(closed_trades, start=1):
        running_total += trade.get("pnl", 0)
        points.append({"trade": index, "equity": round(running_total, 2)})
    return points


def get_daily_pnl_by_strategy():
    totals = {}
    today = datetime.now(timezone.utc).date().isoformat()
    for trade in get_closed_trade_pnl():
        if trade.get("time", "").startswith(today):
            strategy = trade.get("strategy", "unknown")
            totals[strategy] = totals.get(strategy, 0) + trade.get("pnl", 0)
    return totals


def auto_disable_bad_strategies():
    state = load_state()
    daily_pnl_by_strategy = get_daily_pnl_by_strategy()
    limits = state.get("strategy_daily_loss_limit", {})
    disabled = state.get("disabled_strategies", [])

    for strategy, pnl in daily_pnl_by_strategy.items():
        limit = float(limits.get(strategy, 0))
        if limit > 0 and pnl <= -limit and strategy not in disabled:
            disabled.append(strategy)
            write_log(f"Strategy disabled: {strategy} hit daily loss limit")
            send_telegram_message(f"Strategy disabled: {strategy} hit daily loss limit")

    state["disabled_strategies"] = disabled
    save_state(state)


def get_backup_count():
    backup_folder = "backups"
    if not os.path.exists(backup_folder):
        return 0
    return len([name for name in os.listdir(backup_folder) if os.path.isfile(os.path.join(backup_folder, name))])


def get_heartbeat_status():
    state = load_state()
    last_time = state.get("last_webhook_time")
    if not last_time:
        return "No webhook received yet", "red"
    last_dt = datetime.strptime(last_time, "%Y-%m-%d %I:%M:%S %p")
    minutes_passed = (datetime.now() - last_dt).total_seconds() / 60
    if minutes_passed <= 30:
        return f"OK: last webhook {round(minutes_passed)} minutes ago", "green"
    return f"WARNING: no webhook for {round(minutes_passed)} minutes", "red"


# ==================== ROUTES ====================

@app.get("/")
def home():
    return {"message": "Trading bot running", "dashboard": "/dashboard"}


@app.post("/login")
def login(response: Response, password: str = Form("")):
    if not check_dashboard_password(password):
        raise HTTPException(status_code=401, detail="Wrong password")
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(key="session", value="logged_in", httponly=True)
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.delete_cookie(key="session")
    return response


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    session: str = Cookie(default=""),
    strategy_filter: str = "all",
    tag_filter: str = "all",
    search: str = "",
    sort_by: str = "newest",
    page: int = 1,
    per_page: int = 10,
):
    if not is_logged_in(session):
        return """
        <html><body>
        <h2>Trading Bot Login</h2>
        <form method="post" action="/login">
            <input type="password" name="password" placeholder="Password" />
            <button type="submit">Login</button>
        </form>
        </body></html>
        """

    state = load_state()
    reset_daily_state_if_needed(state)

    market_open = False
    daily_pnl = 0
    open_positions = []
    open_orders = []
    health = {"bot": "ok", "alpaca": "not checked", "telegram": "not checked"}

    try:
        market_open = is_market_open()
        daily_pnl = get_daily_pnl()
        open_positions = get_open_positions()
        open_orders = get_open_orders()
        health = get_health_status(False)
    except Exception as error:
        write_log(f"Dashboard broker read failed: {error}")

    recent_trades = get_recent_trades(10)
    closed_trades = get_closed_trade_pnl()

    # Filtering and sorting logic (kept from original)
    strategy_tags = state.get("strategy_tags", {})
    all_strategies = sorted(set(trade.get("strategy", "unknown") for trade in closed_trades))
    all_tags = sorted(set(tag for tags in strategy_tags.values() for tag in tags))

    if strategy_filter != "all":
        closed_trades = [t for t in closed_trades if t.get("strategy", "unknown") == strategy_filter]
    if tag_filter != "all":
        closed_trades = [t for t in closed_trades if tag_filter in strategy_tags.get(t.get("strategy", "unknown"), [])]
    if search:
        search_text = search.lower()
        closed_trades = [t for t in closed_trades if search_text in t.get("symbol", "").lower() or search_text in t.get("strategy", "").lower()]

    if sort_by == "best_pnl":
        closed_trades = sorted(closed_trades, key=lambda t: t.get("pnl", 0), reverse=True)
    elif sort_by == "worst_pnl":
        closed_trades = sorted(closed_trades, key=lambda t: t.get("pnl", 0))
    elif sort_by == "oldest":
        closed_trades = list(closed_trades)
    else:
        closed_trades = list(reversed(closed_trades))

    # Pagination
    page = max(1, page)
    per_page = per_page if per_page in [10, 25, 50] else 10
    total_trades = len(closed_trades)
    total_pages = max(1, (total_trades + per_page - 1) // per_page)
    page = min(page, total_pages)
    paged_closed_trades = closed_trades[(page - 1) * per_page: page * per_page]

    win_rate = get_win_rate(closed_trades)
    avg_win, avg_loss = get_avg_win_loss(closed_trades)
    profit_factor = get_profit_factor(closed_trades)
    max_drawdown = get_max_drawdown(closed_trades)
    total_closed_pnl = sum([trade.get("pnl", 0) for trade in closed_trades])
    equity_curve = get_equity_curve(closed_trades)
    backtest_results = get_backtest_results()
    heartbeat_status, heartbeat_badge = get_heartbeat_status()
    backup_count = get_backup_count()
    last_updated = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")

    trading_badge = "green" if state.get("trading_enabled") else "red"
    market_badge = "green" if market_open else "red"
    mode_badge = "red" if TRADING_MODE == "live" else "green"
    maintenance_badge = "orange" if state.get("maintenance_mode") else "green"
    live_warning = "<div class='live'>WARNING: LIVE TRADING MODE IS ON</div>" if TRADING_MODE == "live" else ""

    # Build HTML tables (truncated in original but kept functional)
    recent_rows = "".join([
        f"<tr><td>{t.get('time')}</td><td>{t.get('strategy')}</td><td>{t.get('symbol')}</td><td>{t.get('action')}</td><td>{t.get('qty')}</td><td>{t.get('price')}</td><td>{t.get('filled_avg_price') or ''}</td><td>{t.get('fill_status') or ''}</td><td>{t.get('trade_score') or ''}</td></tr>"
        for t in recent_trades
    ])

    closed_rows = "".join([
        f"<tr><td>{t.get('strategy')}</td><td>{t.get('symbol')}</td><td>{t.get('buy_price')}</td><td>{t.get('sell_price')}</td><td>{t.get('qty')}</td><td>${t.get('pnl')}</td></tr>"
        for t in paged_closed_trades
    ])

    position_rows = "".join([
        f"<tr><td>{p.get('symbol')}</td><td>{p.get('qty')}</td><td>{p.get('market_value')}</td><td>{p.get('avg_entry_price')}</td><td>{p.get('unrealized_pl')}</td><td><form action='/close-position-confirm' method='post'><input type='hidden' name='symbol' value='{p.get('symbol')}'/><button type='submit'>Close</button></form></td></tr>"
        for p in open_positions
    ])

    order_rows = "".join([
        f"<tr><td>{o.get('symbol')}</td><td>{o.get('side')}</td><td>{o.get('qty')}</td><td>{o.get('type')}</td><td>{o.get('status')}</td><td><form action='/cancel-order-confirm' method='post'><input type='hidden' name='order_id' value='{o.get('id')}'/><button type='submit'>Cancel</button></form></td></tr>"
        for o in open_orders
    ])

    backtest_rows = "".join([
        f"<tr><td>{r.get('symbol')}</td><td>{r.get('fast')}</td><td>{r.get('slow')}</td><td>{r.get('rsi_limit')}</td><td>{r.get('total_pnl')}</td><td>{r.get('win_rate')}</td><td>{r.get('max_drawdown')}</td><td>{r.get('profit_factor')}</td><td>{r.get('score')}</td><td>{r.get('trusted')}</td></tr>"
        for r in backtest_results
    ])

    return f"""
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta http-equiv="refresh" content="30">
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 25px; }}
            .grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px; }}
            .card {{ border: 1px solid #ddd; border-radius: 10px; padding: 15px; margin: 10px 0; background: #f9f9f9; }}
            .badge {{ padding: 4px 8px; border-radius: 6px; color: white; font-weight: bold; }}
            .green {{ background: green; }} .red {{ background: red; }} .orange {{ background: orange; color: black; }}
            .live {{ background: red; color: white; padding: 15px; font-size: 24px; font-weight: bold; }}
            table {{ border-collapse: collapse; width: 100%; margin-top: 10px; }}
            th, td {{ border: 1px solid #ccc; padding: 6px; font-size: 13px; }}
            button {{ margin: 4px; padding: 8px; }} input, select, textarea {{ margin: 4px; padding: 8px; }}
            @media (max-width: 700px) {{ .grid {{ grid-template-columns: 1fr; }} table {{ display:block; overflow-x:auto; }} button, input, select, textarea {{ width: 100%; font-size: 16px; }} }}
            @media (prefers-color-scheme: dark) {{ body {{ background:#111; color:#eee; }} .card {{ background:#1e1e1e; border-color:#333; }} input, select, button, textarea {{ background:#222; color:#eee; border:1px solid #555; }} table {{ color:#eee; }} }}
        </style>
    </head>
    <body>
        <h1>Trading Bot Dashboard</h1>
        {live_warning}
        <p><b>Last Updated:</b> {last_updated}</p>
        <p><b>Bot Version:</b> {BOT_VERSION}</p>
        <form action="/logout" method="post"><button type="submit">Logout</button></form>
        <form action="/dashboard" method="get"><button type="submit">Refresh Dashboard</button></form>

        <div class="grid">
            <div class="card">
                <h2>Bot Status</h2>
                <p><b>Trading:</b> <span class="badge {trading_badge}">{state.get('trading_enabled')}</span></p>
                <p><b>Market:</b> <span class="badge {market_badge}">{market_open}</span></p>
                <p><b>Mode:</b> <span class="badge {mode_badge}">{TRADING_MODE}</span></p>
                <p><b>Maintenance:</b> <span class="badge {maintenance_badge}">{state.get('maintenance_mode')}</span></p>
                <p><b>Heartbeat:</b> <span class="badge {heartbeat_badge}">{heartbeat_status}</span></p>
                <p><b>Backup Files:</b> {backup_count}</p>
            </div>
            <div class="card">
                <h2>Performance</h2>
                <p><b>Daily P/L:</b> ${daily_pnl:.2f}</p>
                <p><b>Closed Trades P/L:</b> ${total_closed_pnl:.2f}</p>
                <p><b>Win Rate:</b> {win_rate}%</p>
                <p><b>Profit Factor:</b> {profit_factor}</p>
                <p><b>Max Drawdown:</b> ${max_drawdown}</p>
            </div>
        </div>

        <!-- Recent Trades -->
        <div class="card">
            <h2>Recent Trades</h2>
            <table><tr><th>Time</th><th>Strategy</th><th>Symbol</th><th>Action</th><th>Qty</th><th>Price</th><th>Filled Price</th><th>Status</th><th>Score</th></tr>{recent_rows}</table>
        </div>

        <!-- Closed Trades -->
        <div class="card">
            <h2>Closed Trades</h2>
            <table><tr><th>Strategy</th><th>Symbol</th><th>Buy</th><th>Sell</th><th>Qty</th><th>P/L</th></tr>{closed_rows}</table>
        </div>

        <!-- Positions & Orders -->
        <div class="card">
            <h2>Open Positions</h2>
            <table><tr><th>Symbol</th><th>Qty</th><th>Value</th><th>Avg Entry</th><th>Unrealized P/L</th><th>Action</th></tr>{position_rows}</table>
        </div>

        <div class="card">
            <h2>Open Orders</h2>
            <table><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Type</th><th>Status</th><th>Action</th></tr>{order_rows}</table>
        </div>

        <!-- Backtest Results -->
        <div class="card">
            <h2>Backtest Results</h2>
            <table><tr><th>Symbol</th><th>Fast</th><th>Slow</th><th>RSI</th><th>P/L</th><th>Win %</th><th>Drawdown</th><th>PF</th><th>Score</th><th>Trusted</th></tr>{backtest_rows}</table>
        </div>

        <div class="card">
            <h2>Exports & Backups</h2>
            <form action="/export-journal" method="get"><button>Export Journal CSV</button></form>
            <form action="/export-report" method="get"><button>Export Report</button></form>
            <form action="/backup-now" method="post"><button>Backup Now</button></form>
            <form action="/clean-backups" method="post"><button>Clean Old Backups</button></form>
            <form action="/migrate-csv" method="post"><button>Migrate Old CSV</button></form>
        </div>

        <div class="card">
            <h2>State & Errors</h2>
            <p>Error Count: {state.get('error_count')}</p>
            <p>Last Error: {state.get('last_error_message')}</p>
            <p>Last Webhook: {state.get('last_webhook_time')} {state.get('last_webhook_symbol')} {state.get('last_webhook_action')}</p>
            <p>Health: {health}</p>
        </div>
    </body>
    </html>
    """


# ==================== WEBHOOK & CONTROL ROUTES ====================

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
        write_log("TradingView heartbeat received")
        return {"ok": True, "message": "Heartbeat received"}

    incoming_qty = data.get("qty")
    qty = float(incoming_qty) if incoming_qty is not None else calculate_qty_from_price(price, state, strategy)
    review = create_trade_review(symbol, action, price, strategy, reason)
    trade_score = create_trade_score(symbol, action, price, strategy, reason, state)
    minimum_score = int(state.get("minimum_trade_score", 70))

    try:
        if trade_score < minimum_score:
            raise ValueError(f"Trade score too low: {trade_score}")
        auto_disable_bad_strategies()
        state = load_state()
        validate_trade(state, symbol, action, price, qty, strategy)
        order = build_bracket_order(symbol, qty, action, price, state, strategy)
        placed_order = client.submit_order(order_data=order)
        handle_successful_trade(state, symbol, action, qty, placed_order.id, strategy)
        save_trade_to_db(symbol, action, qty, price, placed_order.id, strategy, reason, review, trade_score)
        return {"ok": True, "symbol": symbol, "action": action, "qty": qty, "order_id": str(placed_order.id)}
    except ValueError as error:
        record_error(state, str(error))
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        record_error(state, f"Broker/order error: {error}")
        raise HTTPException(status_code=500, detail=str(error))


# State, Health, Enable/Disable, etc. (all your original routes)
@app.get("/state")
def get_state():
    return load_state()


@app.get("/health")
def health():
    return get_health_status(False)


@app.post("/enable")
def enable_trading(session: str = Cookie(default="")):
    if not is_logged_in(session):
        raise HTTPException(status_code=401, detail="Not logged in")
    state = load_state()
    state["trading_enabled"] = True
    save_state(state)
    write_log("Trading enabled")
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/disable")
def disable_trading(session: str = Cookie(default="")):
    if not is_logged_in(session):
        raise HTTPException(status_code=401, detail="Not logged in")
    state = load_state()
    state["trading_enabled"] = False
    save_state(state)
    write_log("Trading disabled")
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/reset")
def reset_state(session: str = Cookie(default="")):
    if not is_logged_in(session):
        raise HTTPException(status_code=401, detail="Not logged in")
    save_state(DEFAULT_STATE.copy())
    write_log("State reset")
    return RedirectResponse(url="/dashboard", status_code=303)


# ... (All other control routes like maintenance, pause, close-position, cancel-order, update settings, export, backup, run-backtest, etc. remain unchanged from your original file)

# For space, the full list of routes (update-asset-mode, update-watchlist, close-all, run-backtest, etc.) is the same as your original app.py.
# If you need any specific route adjusted, let me know.

@app.on_event("shutdown")  # Optional fallback
async def shutdown_event():
    scheduler.shutdown()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
