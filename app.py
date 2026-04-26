# app.py
# FastAPI dashboard and TradingView webhook controller.

import csv
import json
import os
import subprocess
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
)
from telegram_bot import check_telegram_commands

app = FastAPI(title="Trading Bot")
scheduler = BackgroundScheduler()
market_timezone = ZoneInfo("America/New_York")


def check_dashboard_password(password):
    # Compare typed password with saved password.
    return password == DASHBOARD_PASSWORD


def is_logged_in(session: str = ""):
    # Simple dashboard cookie check.
    return session == "logged_in"


def get_recent_trades(limit=10):
    # Read recent trades from database.
    return get_trades_from_db(limit)


def get_closed_trade_pnl():
    # Pair buy and sell rows to estimate closed P/L.
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
            closed_trades.append(
                {
                    "time": trade.get("time"),
                    "symbol": symbol,
                    "strategy": buy["strategy"],
                    "buy_price": buy["price"],
                    "sell_price": price,
                    "qty": qty,
                    "pnl": round(pnl, 2),
                }
            )
            del open_positions[symbol]

    return closed_trades


def get_strategy_stats():
    # Calculate simple stats by strategy.
    stats = {}
    for trade in get_trades_from_db(5000):
        strategy = trade.get("strategy", "unknown")
        stats.setdefault(strategy, {"trades": 0, "total_qty": 0.0, "total_price": 0.0})
        stats[strategy]["trades"] += 1
        stats[strategy]["total_qty"] += float(trade.get("qty") or 0)
        stats[strategy]["total_price"] += float(trade.get("price") or 0)
    for strategy, data in stats.items():
        data["avg_price"] = round(data["total_price"] / data["trades"], 2) if data["trades"] else 0
    return stats


def get_win_rate(closed_trades):
    # Calculate win rate percentage.
    if not closed_trades:
        return 0
    wins = sum(1 for trade in closed_trades if trade.get("pnl", 0) > 0)
    return round((wins / len(closed_trades)) * 100, 2)


def get_avg_win_loss(closed_trades):
    # Calculate average win and average loss.
    wins = [trade.get("pnl", 0) for trade in closed_trades if trade.get("pnl", 0) > 0]
    losses = [trade.get("pnl", 0) for trade in closed_trades if trade.get("pnl", 0) < 0]
    avg_win = round(sum(wins) / len(wins), 2) if wins else 0
    avg_loss = round(sum(losses) / len(losses), 2) if losses else 0
    return avg_win, avg_loss


def get_profit_factor(closed_trades):
    # Calculate profit factor.
    total_wins = sum(trade.get("pnl", 0) for trade in closed_trades if trade.get("pnl", 0) > 0)
    total_losses = abs(sum(trade.get("pnl", 0) for trade in closed_trades if trade.get("pnl", 0) < 0))
    return round(total_wins / total_losses, 2) if total_losses else 0


def get_max_drawdown(closed_trades):
    # Calculate max drawdown from closed trades.
    running_total = 0
    peak = 0
    max_drawdown = 0
    for trade in closed_trades:
        running_total += trade.get("pnl", 0)
        peak = max(peak, running_total)
        max_drawdown = max(max_drawdown, peak - running_total)
    return round(max_drawdown, 2)


def get_equity_curve(closed_trades):
    # Build equity curve points.
    running_total = 0
    points = []
    for index, trade in enumerate(closed_trades, start=1):
        running_total += trade.get("pnl", 0)
        points.append({"trade": index, "equity": round(running_total, 2)})
    return points


def get_daily_pnl_by_strategy():
    # Calculate today's closed P/L by strategy.
    totals = {}
    today = datetime.now(timezone.utc).date().isoformat()
    for trade in get_closed_trade_pnl():
        if trade.get("time", "").startswith(today):
            strategy = trade.get("strategy", "unknown")
            totals[strategy] = totals.get(strategy, 0) + trade.get("pnl", 0)
    return totals


def auto_disable_bad_strategies():
    # Disable strategies that hit daily loss limits.
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
    # Count backup files.
    backup_folder = "backups"
    if not os.path.exists(backup_folder):
        return 0
    return len([name for name in os.listdir(backup_folder) if os.path.isfile(os.path.join(backup_folder, name))])


def get_health_status(send_test_message=False):
    # Check bot, Alpaca, Telegram config.
    health = {"bot": "ok", "alpaca": "unknown", "telegram": "unknown"}
    try:
        client.get_account()
        health["alpaca"] = "ok"
    except Exception as error:
        health["alpaca"] = f"error: {error}"

    try:
        if send_test_message:
            send_telegram_message("Health check: bot is alive")
        health["telegram"] = "configured" if os.getenv("TELEGRAM_BOT_TOKEN") else "not configured"
    except Exception as error:
        health["telegram"] = f"error: {error}"

    health["version"] = BOT_VERSION
    health["trading_mode"] = TRADING_MODE
    return health


def get_heartbeat_status():
    # Show warning if TradingView has been quiet.
    state = load_state()
    last_time = state.get("last_webhook_time")
    if not last_time:
        return "No webhook received yet", "red"
    last_dt = datetime.strptime(last_time, "%Y-%m-%d %I:%M:%S %p")
    minutes_passed = (datetime.now() - last_dt).total_seconds() / 60
    if minutes_passed <= 30:
        return f"OK: last webhook {round(minutes_passed)} minutes ago", "green"
    return f"WARNING: no webhook for {round(minutes_passed)} minutes", "red"


def check_heartbeat_warning():
    # Telegram warning if heartbeat is old.
    state = load_state()
    last_time = state.get("last_webhook_time")
    if not last_time:
        return
    last_dt = datetime.strptime(last_time, "%Y-%m-%d %I:%M:%S %p")
    minutes_passed = (datetime.now() - last_dt).total_seconds() / 60
    if minutes_passed > 30 and not state.get("heartbeat_warning_sent", False):
        send_telegram_message(f"Warning: TradingView quiet for {round(minutes_passed)} minutes")
        state["heartbeat_warning_sent"] = True
        save_state(state)
    if minutes_passed <= 30:
        state["heartbeat_warning_sent"] = False
        save_state(state)


def check_market_status_alert():
    # Notify Telegram if market status changes.
    state = load_state()
    market_open = is_market_open()
    current_status = "open" if market_open else "closed"
    last_status = state.get("last_market_status")
    if last_status and last_status != current_status:
        send_telegram_message(f"Market is now {current_status.upper()}")
        write_log(f"Market status changed to {current_status}")
    state["last_market_status"] = current_status
    save_state(state)


def disable_trading_end_of_day():
    # Turn trading off at end of day.
    state = load_state()
    state["trading_enabled"] = False
    save_state(state)
    write_log("Trading auto-disabled at end of day")
    send_telegram_message("Trading turned OFF for end of day")


def enable_trading_morning():
    # Turn trading on only if the market is open.
    if not is_market_open():
        write_log("Morning enable skipped: market is closed")
        return
    state = load_state()
    state["trading_enabled"] = True
    save_state(state)
    write_log("Trading auto-enabled in the morning")
    send_telegram_message("Trading turned ON for the morning")


def build_daily_report_text():
    # Build a plain text stats report.
    closed_trades = get_closed_trade_pnl()
    win_rate = get_win_rate(closed_trades)
    avg_win, avg_loss = get_avg_win_loss(closed_trades)
    profit_factor = get_profit_factor(closed_trades)
    max_drawdown = get_max_drawdown(closed_trades)
    total_pnl = sum([trade.get("pnl", 0) for trade in closed_trades])
    return f"""
Trading Bot Daily Report

Closed Trades: {len(closed_trades)}
Total Closed P/L: ${total_pnl:.2f}
Win Rate: {win_rate}%
Average Win: ${avg_win}
Average Loss: ${avg_loss}
Profit Factor: {profit_factor}
Max Drawdown: ${max_drawdown}
"""


def send_scheduled_daily_report():
    # Send daily email report if configured.
    send_daily_email_report(build_daily_report_text())
    write_log("Daily email report sent")


def get_backtest_results(limit=20):
    # Read ranked backtest results.
    file_name = "backtest_ranked_results.csv"
    if not os.path.exists(file_name):
        return []
    with open(file_name, "r") as file:
        return list(csv.DictReader(file))[:limit]


def get_best_backtest_result():
    # Return top row from ranked results.
    rows = get_backtest_results(1)
    return rows[0] if rows else None


@app.get("/")
def home():
    # Simple home route.
    return {"message": "Trading bot running", "dashboard": "/dashboard"}


@app.post("/login")
def login(response: Response, password: str = Form("")):
    # Login using dashboard password.
    if not check_dashboard_password(password):
        raise HTTPException(status_code=401, detail="Wrong password")
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(key="session", value="logged_in", httponly=True)
    return response


@app.post("/logout")
def logout():
    # Clear login cookie.
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
    # Show login page if not logged in.
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
                <p><b>Total Closed P/L:</b> ${total_closed_pnl:.2f}</p>
                <p><b>Win Rate:</b> {win_rate}%</p>
                <p><b>Average Win:</b> ${avg_win}</p>
                <p><b>Average Loss:</b> ${avg_loss}</p>
                <p><b>Profit Factor:</b> {profit_factor}</p>
                <p><b>Max Drawdown:</b> ${max_drawdown}</p>
            </div>
        </div>

        <div class="card"><h2>Controls</h2>
            <form action="/enable" method="post"><button type="submit">Trading ON</button></form>
            <form action="/disable" method="post"><button type="submit">Trading OFF</button></form>
            <form action="/maintenance-on" method="post"><button type="submit">Maintenance ON</button></form>
            <form action="/maintenance-off" method="post"><button type="submit">Maintenance OFF</button></form>
            <form action="/pause-1-hour" method="post"><button type="submit">Pause 1 Hour</button></form>
            <form action="/pause-until-tomorrow" method="post"><button type="submit">Pause Until Tomorrow</button></form>
            <form action="/resume-now" method="post"><button type="submit">Resume Now</button></form>
            <form action="/clear-errors" method="post"><button type="submit">Clear Errors</button></form>
            <form action="/close-all-confirm" method="get"><button style="background:red;color:white;" type="submit">CLOSE ALL POSITIONS</button></form>
            <form action="/cancel-orders-confirm" method="get"><button style="background:orange;" type="submit">Cancel All Open Orders</button></form>
        </div>

        <div class="card"><h2>Risk Settings</h2>
            <p>Allowed: {', '.join(state.get('allowed_symbols', []))}</p>
            <p>Blocked: {', '.join(state.get('blocked_symbols', []))}</p>
            <p>Max Dollars: ${state.get('max_dollars_per_trade')}</p>
            <p>Max Shares: {state.get('max_shares_per_trade')}</p>
            <p>Max Trades/Day: {state.get('max_trades_per_day')}</p>
            <p>Daily Loss Limit: ${state.get('daily_loss_limit')}</p>
            <p>TP/SL: {state.get('take_profit_percent')}% / {state.get('stop_loss_percent')}%</p>
            <form action="/update-watchlist" method="post"><input name="symbols" placeholder="AAPL,TSLA,SPY"/><button>Update Watchlist</button></form>
            <form action="/update-blocked-symbols" method="post"><input name="symbols" placeholder="AMC,GME"/><button>Update Blocked</button></form>
            <form action="/update-max-dollars" method="post"><input name="amount" placeholder="50"/><button>Max Dollars</button></form>
            <form action="/update-max-shares" method="post"><input name="shares" placeholder="5"/><button>Max Shares</button></form>
            <form action="/update-max-trades" method="post"><input name="max_trades" placeholder="5"/><button>Max Trades</button></form>
            <form action="/update-daily-loss-limit" method="post"><input name="amount" placeholder="50"/><button>Daily Loss</button></form>
            <form action="/update-bracket-settings" method="post"><input name="take_profit_percent" placeholder="1.0"/><input name="stop_loss_percent" placeholder="1.0"/><button>Update TP/SL</button></form>
        </div>

        <div class="card"><h2>Open Positions</h2><table><tr><th>Symbol</th><th>Qty</th><th>Value</th><th>Avg Entry</th><th>Unrealized P/L</th><th>Action</th></tr>{position_rows}</table></div>
        <div class="card"><h2>Open Orders</h2><table><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Type</th><th>Status</th><th>Action</th></tr>{order_rows}</table></div>

        <div class="card"><h2>Recent Trades</h2><table><tr><th>Time</th><th>Strategy</th><th>Symbol</th><th>Action</th><th>Qty</th><th>Alert Price</th><th>Filled Price</th><th>Status</th><th>Score</th></tr>{recent_rows}</table></div>

        <div class="card"><h2>Filters</h2>
            <form method="get" action="/dashboard">
                <input name="search" placeholder="Search symbol or strategy" value="{search}" />
                <select name="strategy_filter"><option value="all">All Strategies</option>{''.join([f'<option value="{s}" {"selected" if s == strategy_filter else ""}>{s}</option>' for s in all_strategies])}</select>
                <select name="tag_filter"><option value="all">All Tags</option>{''.join([f'<option value="{t}" {"selected" if t == tag_filter else ""}>{t}</option>' for t in all_tags])}</select>
                <select name="sort_by"><option value="newest">Newest</option><option value="oldest">Oldest</option><option value="best_pnl">Best P/L</option><option value="worst_pnl">Worst P/L</option></select>
                <button type="submit">Apply</button>
            </form>
        </div>

        <div class="card"><h2>Closed Trade P/L</h2><p>Page {page} of {total_pages} | Total: {total_trades}</p><table><tr><th>Strategy</th><th>Symbol</th><th>Buy</th><th>Sell</th><th>Qty</th><th>P/L</th></tr>{closed_rows}</table>
            <form method="get" action="/dashboard"><input type="hidden" name="strategy_filter" value="{strategy_filter}"/><input type="hidden" name="tag_filter" value="{tag_filter}"/><input type="hidden" name="search" value="{search}"/><input type="hidden" name="sort_by" value="{sort_by}"/><input name="page" placeholder="{page}"/><select name="per_page"><option>10</option><option>25</option><option>50</option></select><button>Go</button></form>
        </div>

        <div class="card"><h2>Equity Curve</h2><canvas id="equityChart" width="600" height="300"></canvas></div>
        <script>
            const equityData = {json.dumps(equity_curve)};
            new Chart(document.getElementById('equityChart'), {{ type: 'line', data: {{ labels: equityData.map(p => p.trade), datasets: [{{ label: 'Closed P/L', data: equityData.map(p => p.equity), borderWidth: 2 }}] }} }});
        </script>

        <div class="card"><h2>Backtesting</h2>
            <p>Last Backtest: {state.get('last_backtest_time')} | {state.get('last_backtest_status')}</p>
            <form action="/run-backtest" method="post"><input name="symbols" placeholder="AAPL,SPY,TSLA"/><input name="start" placeholder="2024-01-01"/><input name="end" placeholder="2024-12-31"/><input name="fast" placeholder="5,9,12"/><input name="slow" placeholder="21,50"/><input name="rsi" placeholder="60,70"/><button>Run Backtest</button></form>
            <form action="/download-backtest-results" method="get"><button>Download Backtest CSV</button></form>
            <form action="/download-backtest-log" method="get"><button>Download Backtest Log</button></form>
            <form action="/save-best-backtest-preset" method="post"><button>Save Best Backtest Preset</button></form>
            <table><tr><th>Symbol</th><th>Fast</th><th>Slow</th><th>RSI</th><th>P/L</th><th>Win %</th><th>Drawdown</th><th>PF</th><th>Score</th><th>Trusted</th></tr>{backtest_rows}</table>
        </div>

        <div class="card"><h2>Exports & Backups</h2>
            <form action="/export-journal" method="get"><button>Export Journal CSV</button></form>
            <form action="/export-report" method="get"><button>Export Report</button></form>
            <form action="/backup-now" method="post"><button>Backup Now</button></form>
            <form action="/clean-backups" method="post"><button>Clean Old Backups</button></form>
            <form action="/migrate-csv" method="post"><button>Migrate Old CSV</button></form>
        </div>

        <div class="card"><h2>State & Errors</h2>
            <p>Error Count: {state.get('error_count')}</p><p>Last Error: {state.get('last_error_message')}</p>
            <p>Last Webhook: {state.get('last_webhook_time')} {state.get('last_webhook_symbol')} {state.get('last_webhook_action')}</p>
            <p>Health: {health}</p>
        </div>
    </body></html>
    """


@app.post("/webhook")
async def webhook(request: Request):
    # Main TradingView webhook route.
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
    state = load_state(); state["trading_enabled"] = True; save_state(state); write_log("Trading enabled")
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/disable")
def disable_trading(session: str = Cookie(default="")):
    if not is_logged_in(session):
        raise HTTPException(status_code=401, detail="Not logged in")
    state = load_state(); state["trading_enabled"] = False; save_state(state); write_log("Trading disabled")
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/reset")
def reset_state(session: str = Cookie(default="")):
    if not is_logged_in(session):
        raise HTTPException(status_code=401, detail="Not logged in")
    save_state(DEFAULT_STATE.copy()); write_log("State reset")
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/maintenance-on")
def maintenance_on(session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    state = load_state(); state["maintenance_mode"] = True; save_state(state); write_log("Maintenance ON")
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/maintenance-off")
def maintenance_off(session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    state = load_state(); state["maintenance_mode"] = False; save_state(state); write_log("Maintenance OFF")
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/pause-1-hour")
def pause_1_hour(session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    state = load_state(); state["paused_until"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(); save_state(state)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/pause-until-tomorrow")
def pause_until_tomorrow(session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    state = load_state(); state["paused_until"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(); save_state(state)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/resume-now")
def resume_now(session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    state = load_state(); state["paused_until"] = None; save_state(state)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/clear-errors")
def clear_errors(session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    state = load_state(); state["error_count"] = 0; state["last_error_time"] = None; state["last_error_message"] = None; save_state(state)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/confirm-live")
def confirm_live(session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    state = load_state(); state["live_confirmed"] = True; save_state(state); write_log("Live confirmed")
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/lock-live")
def lock_live(session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    state = load_state(); state["live_confirmed"] = False; save_state(state); write_log("Live locked")
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/close-all-confirm", response_class=HTMLResponse)
def close_all_confirm(session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    return """<html><body><h1 style='color:red;'>Confirm Close All Positions</h1><form action='/close-all' method='post'><button style='background:red;color:white;'>YES, CLOSE ALL</button></form><a href='/dashboard'>Cancel</a></body></html>"""


@app.post("/close-all")
def close_all(session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    close_all_positions(); state = load_state(); state["trading_enabled"] = False; save_state(state)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/cancel-orders-confirm", response_class=HTMLResponse)
def cancel_orders_confirm(session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    return """<html><body><h1 style='color:orange;'>Confirm Cancel All Orders</h1><form action='/cancel-orders' method='post'><button style='background:orange;'>YES, CANCEL ALL</button></form><a href='/dashboard'>Cancel</a></body></html>"""


@app.post("/cancel-orders")
def cancel_orders(session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    cancel_all_orders()
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/close-position-confirm", response_class=HTMLResponse)
def close_position_confirm(symbol: str = Form(...), session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    return f"<html><body><h1>Close {symbol}</h1><form action='/close-position' method='post'><input type='hidden' name='symbol' value='{symbol}'/><button>YES CLOSE</button></form><a href='/dashboard'>Cancel</a></body></html>"


@app.post("/close-position")
def close_position(symbol: str = Form(...), session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    close_one_position(symbol.upper())
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/cancel-order-confirm", response_class=HTMLResponse)
def cancel_order_confirm(order_id: str = Form(...), session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    return f"<html><body><h1>Cancel Order</h1><p>{order_id}</p><form action='/cancel-order' method='post'><input type='hidden' name='order_id' value='{order_id}'/><button>YES CANCEL</button></form><a href='/dashboard'>Cancel</a></body></html>"


@app.post("/cancel-order")
def cancel_order(order_id: str = Form(...), session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    cancel_one_order(order_id)
    return RedirectResponse(url="/dashboard", status_code=303)


# Dashboard settings update routes.
@app.post("/update-watchlist")
def update_watchlist(symbols: str = Form(...), session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    state = load_state(); state["allowed_symbols"] = [s.strip().upper() for s in symbols.split(",") if s.strip()]; save_state(state)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/update-blocked-symbols")
def update_blocked_symbols(symbols: str = Form(""), session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    state = load_state(); state["blocked_symbols"] = [s.strip().upper() for s in symbols.split(",") if s.strip()]; save_state(state)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/update-max-dollars")
def update_max_dollars(amount: float = Form(...), session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    if amount < 1 or amount > 10000: raise HTTPException(status_code=400, detail="Amount must be 1 to 10000")
    state = load_state(); state["max_dollars_per_trade"] = amount; save_state(state)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/update-max-shares")
def update_max_shares(shares: int = Form(...), session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    state = load_state(); state["max_shares_per_trade"] = shares; save_state(state)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/update-max-trades")
def update_max_trades(max_trades: int = Form(...), session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    state = load_state(); state["max_trades_per_day"] = max_trades; save_state(state)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/update-daily-loss-limit")
def update_daily_loss_limit(amount: float = Form(...), session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    state = load_state(); state["daily_loss_limit"] = amount; save_state(state)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/update-bracket-settings")
def update_bracket_settings(take_profit_percent: float = Form(...), stop_loss_percent: float = Form(...), session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    state = load_state(); state["take_profit_percent"] = take_profit_percent; state["stop_loss_percent"] = stop_loss_percent; save_state(state)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/export-journal")
def export_journal(session: str = Cookie(default=""), strategy_filter: str = "all", tag_filter: str = "all"):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    state = load_state(); rows = get_trades_from_db(10000); tags = state.get("strategy_tags", {})
    if strategy_filter != "all": rows = [r for r in rows if r.get("strategy") == strategy_filter]
    if tag_filter != "all": rows = [r for r in rows if tag_filter in tags.get(r.get("strategy", "unknown"), [])]
    if not rows: raise HTTPException(status_code=404, detail="No rows match filters")
    with open("filtered_journal.csv", "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
    return FileResponse("filtered_journal.csv", filename="filtered_journal.csv", media_type="text/csv")


@app.get("/export-report")
def export_report(session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    report = build_daily_report_text()
    with open("report.txt", "w") as file: file.write(report)
    return FileResponse("report.txt", filename="trading_bot_report.txt", media_type="text/plain")


@app.post("/backup-now")
def backup_now(session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    make_daily_backup(); return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/clean-backups")
def clean_backups(session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    clean_old_backups(); return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/migrate-csv")
def migrate_csv(session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    migrate_csv_to_db(); return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/order/{order_id}")
def order_status(order_id: str):
    return get_order_status(order_id)


@app.post("/order/{order_id}/sync")
def sync_order(order_id: str):
    update_db_trade_with_fill(order_id)
    return {"ok": True, "message": "Order synced"}


@app.post("/run-backtest")
def run_backtest(symbols: str = Form("AAPL,SPY,TSLA"), start: str = Form("2024-01-01"), end: str = Form("2024-12-31"), fast: str = Form("5,9,12,20"), slow: str = Form("13,21,26,50"), rsi: str = Form("60,65,70"), session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    result = subprocess.run(["python3", "backtest.py", "--symbols", symbols, "--start", start, "--end", end, "--fast", fast, "--slow", slow, "--rsi", rsi], cwd=os.getcwd(), check=False, capture_output=True, text=True)
    with open("backtest_log.txt", "w") as file:
        file.write("STDOUT:\n" + result.stdout + "\n\nSTDERR:\n" + result.stderr)
    state = load_state(); state["last_backtest_time"] = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p"); state["last_backtest_status"] = "completed" if result.returncode == 0 else f"failed with code {result.returncode}"; save_state(state)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/download-backtest-results")
def download_backtest_results(session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    if not os.path.exists("backtest_ranked_results.csv"): raise HTTPException(status_code=404, detail="No backtest results yet")
    return FileResponse("backtest_ranked_results.csv", filename="backtest_ranked_results.csv", media_type="text/csv")


@app.get("/download-backtest-log")
def download_backtest_log(session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    if not os.path.exists("backtest_log.txt"): raise HTTPException(status_code=404, detail="No backtest log yet")
    return FileResponse("backtest_log.txt", filename="backtest_log.txt", media_type="text/plain")


@app.post("/save-best-backtest-preset")
def save_best_backtest_preset(session: str = Cookie(default="")):
    if not is_logged_in(session): raise HTTPException(status_code=401, detail="Not logged in")
    best = get_best_backtest_result()
    if not best: raise HTTPException(status_code=404, detail="No backtest results")
    preset_name = f"{best.get('symbol')}_ema_{best.get('fast')}_{best.get('slow')}_rsi_{best.get('rsi_limit')}"
    state = load_state(); presets = state.get("strategy_presets", {}); presets[preset_name] = best; state["strategy_presets"] = presets; save_state(state)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.on_event("startup")
def startup_event():
    # Start database and scheduled jobs.
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

