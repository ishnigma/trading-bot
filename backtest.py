# backtest.py - ORIGINAL, WORKS FINE
# Simple EMA/RSI backtester using Alpaca historical bars.
# This script does not place orders.

import argparse
import csv
import os
from datetime import datetime

from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# Load API keys from .env.
load_dotenv()


def calculate_ema(prices, period):
    # Calculate EMA list for a price series.
    ema_values = []
    multiplier = 2 / (period + 1)
    for index, price in enumerate(prices):
        if index == 0:
            ema_values.append(price)
        else:
            previous_ema = ema_values[-1]
            ema_values.append((price - previous_ema) * multiplier + previous_ema)
    return ema_values


def calculate_rsi(prices, period=14):
    # Calculate a simple RSI series.
    gains = []
    losses = []
    rsi_values = [50] * len(prices)
    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))
        if i >= period:
            avg_gain = sum(gains[-period:]) / period
            avg_loss = sum(losses[-period:]) / period
            if avg_loss == 0:
                rsi_values[i] = 100
            else:
                rs = avg_gain / avg_loss
                rsi_values[i] = 100 - (100 / (1 + rs))
    return rsi_values


def calculate_max_drawdown(closed_trades):
    # Calculate max drawdown from a list of closed trade P/L values.
    running_total = 0
    peak = 0
    max_drawdown = 0
    for trade in closed_trades:
        running_total += trade["pnl"]
        peak = max(peak, running_total)
        max_drawdown = max(max_drawdown, peak - running_total)
    return max_drawdown


def run_backtest():
    # Read command-line options.
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="AAPL,TSLA,SPY")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--fast", default="5,9,12,20")
    parser.add_argument("--slow", default="13,21,26,50")
    parser.add_argument("--rsi", default="60,65,70")
    args = parser.parse_args()

    # Convert inputs.
    symbols_to_test = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    start_date = datetime.strptime(args.start, "%Y-%m-%d")
    end_date = datetime.strptime(args.end, "%Y-%m-%d")
    fast_periods = [int(x.strip()) for x in args.fast.split(",") if x.strip()]
    slow_periods = [int(x.strip()) for x in args.slow.split(",") if x.strip()]
    rsi_limits = [int(x.strip()) for x in args.rsi.split(",") if x.strip()]

    # Build all setting combinations.
    settings_to_test = []
    for fast in fast_periods:
        for slow in slow_periods:
            for rsi_limit in rsi_limits:
                if fast < slow:
                    settings_to_test.append({"fast": fast, "slow": slow, "rsi_limit": rsi_limit})

    # Create historical data client.
    client = StockHistoricalDataClient(
        os.getenv("ALPACA_API_KEY"),
        os.getenv("ALPACA_SECRET_KEY"),
    )

    all_results = []
    minimum_closed_trades = 5

    for symbol in symbols_to_test:
        # Request daily bars.
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start_date,
            end=end_date,
        )
        bars = client.get_stock_bars(request)
        if symbol not in bars.data:
            continue

        price_bars = bars[symbol]
        if len(price_bars) < 60:
            continue

        closes = [bar.close for bar in price_bars]

        for settings in settings_to_test:
            fast_period = settings["fast"]
            slow_period = settings["slow"]
            rsi_limit = settings["rsi_limit"]

            fast_ema = calculate_ema(closes, fast_period)
            slow_ema = calculate_ema(closes, slow_period)
            rsi = calculate_rsi(closes, 14)

            open_buy_price = None
            closed_trades = []

            for i in range(1, len(price_bars)):
                current_close = closes[i]
                previous_fast = fast_ema[i - 1]
                previous_slow = slow_ema[i - 1]
                current_fast = fast_ema[i]
                current_slow = slow_ema[i]

                # Buy on EMA cross up with RSI filter.
                if (
                    previous_fast <= previous_slow
                    and current_fast > current_slow
                    and rsi[i] < rsi_limit
                    and open_buy_price is None
                ):
                    open_buy_price = current_close

                # Sell on EMA cross down.
                if (
                    previous_fast >= previous_slow
                    and current_fast < current_slow
                    and open_buy_price is not None
                ):
                    pnl = current_close - open_buy_price
                    closed_trades.append({"pnl": pnl})
                    open_buy_price = None

            total_pnl = sum(trade["pnl"] for trade in closed_trades)
            wins = [trade for trade in closed_trades if trade["pnl"] > 0]
            win_rate = (len(wins) / len(closed_trades) * 100) if closed_trades else 0
            max_drawdown = calculate_max_drawdown(closed_trades)
            total_wins = sum(trade["pnl"] for trade in closed_trades if trade["pnl"] > 0)
            total_losses = abs(sum(trade["pnl"] for trade in closed_trades if trade["pnl"] < 0))
            profit_factor = round(total_wins / total_losses, 2) if total_losses > 0 else 0

            # Score rewards profit, win rate, profit factor, and penalizes drawdown.
            score = total_pnl + (profit_factor * 10) + (win_rate * 0.5) - (max_drawdown * 2)
            if len(closed_trades) < minimum_closed_trades:
                score -= 1000

            all_results.append({
                "symbol": symbol,
                "fast": fast_period,
                "slow": slow_period,
                "rsi_limit": rsi_limit,
                "closed_trades": len(closed_trades),
                "total_pnl": round(total_pnl, 2),
                "win_rate": round(win_rate, 2),
                "max_drawdown": round(max_drawdown, 2),
                "profit_factor": profit_factor,
                "score": round(score, 2),
                "trusted": len(closed_trades) >= minimum_closed_trades,
                "start_date": start_date.date().isoformat(),
                "end_date": end_date.date().isoformat(),
            })

    # Sort best score first.
    all_results = sorted(all_results, key=lambda x: x["score"], reverse=True)

    # Save ranked results.
    fieldnames = [
        "symbol", "fast", "slow", "rsi_limit", "closed_trades",
        "total_pnl", "win_rate", "max_drawdown", "profit_factor",
        "score", "trusted", "start_date", "end_date"
    ]
    with open("backtest_ranked_results.csv", "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_results)

    print("Backtest finished")
    print(f"Results created: {len(all_results)}")
    if all_results:
        print(f"Best result: {all_results[0]}")


if __name__ == "__main__":
    run_backtest()
    
