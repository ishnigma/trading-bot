# strategy.py
# Full copy/paste version for OANDA forex-only autonomous trading

from datetime import datetime, timedelta

from config import (
    FAST_EMA_PERIOD,
    SLOW_EMA_PERIOD,
    RSI_PERIOD,
    RSI_OVERSOLD,
    RSI_OVERBOUGHT,
    STRATEGY_ENABLED,
    STRATEGY_TIMEFRAME,
    STRATEGY_TYPE,
)

from helpers import (
    client,
    load_state,
    save_state,
    write_log,
    send_telegram_message,
    build_oanda_order,
    handle_successful_trade,
    validate_trade,
    get_daily_pnl,
    is_market_open,
    has_open_position,
    create_trade_review,
    create_trade_score,
    save_trade_to_db,
)


def calculate_ema(prices, period):
    # Calculate EMA for strategy signals
    if len(prices) < period:
        return prices

    multiplier = 2 / (period + 1)
    ema = [prices[0]]

    for price in prices[1:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])

    return ema


def calculate_rsi(prices, period=14):
    # Calculate RSI for strategy signals
    if len(prices) < period + 1:
        return [50] * len(prices)

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


def get_candles(symbol, count=100, granularity="M5"):
    # Get candles from OANDA
    try:
        return client.get_candle_data(symbol, count=count, granularity=granularity)
    except Exception as error:
        write_log(f"Failed to get candles for {symbol}: {error}")
        return []


def check_ema_crossover(symbol, granularity="M5"):
    # Check EMA crossover signal
    candles = get_candles(symbol, count=100, granularity=granularity)

    if len(candles) < SLOW_EMA_PERIOD + 5:
        return None, None, "Insufficient candle data"

    closes = [candle["close"] for candle in candles]

    fast_ema = calculate_ema(closes, FAST_EMA_PERIOD)
    slow_ema = calculate_ema(closes, SLOW_EMA_PERIOD)

    current_fast = fast_ema[-1]
    current_slow = slow_ema[-1]
    previous_fast = fast_ema[-2]
    previous_slow = slow_ema[-2]
    current_price = closes[-1]

    if previous_fast <= previous_slow and current_fast > current_slow:
        return "buy", current_price, f"EMA {FAST_EMA_PERIOD} crossed above EMA {SLOW_EMA_PERIOD}"

    if previous_fast >= previous_slow and current_fast < current_slow:
        return "sell", current_price, f"EMA {FAST_EMA_PERIOD} crossed below EMA {SLOW_EMA_PERIOD}"

    return None, None, "No EMA crossover signal"


def check_rsi_signal(symbol, granularity="M5"):
    # Check RSI signal
    candles = get_candles(symbol, count=100, granularity=granularity)

    if len(candles) < RSI_PERIOD + 5:
        return None, None, "Insufficient candle data"

    closes = [candle["close"] for candle in candles]
    rsi = calculate_rsi(closes, RSI_PERIOD)

    current_rsi = rsi[-1]
    previous_rsi = rsi[-2]
    current_price = closes[-1]

    if previous_rsi <= RSI_OVERSOLD and current_rsi > RSI_OVERSOLD:
        return "buy", current_price, f"RSI crossed above oversold level {RSI_OVERSOLD}"

    if previous_rsi >= RSI_OVERBOUGHT and current_rsi < RSI_OVERBOUGHT:
        return "sell", current_price, f"RSI crossed below overbought level {RSI_OVERBOUGHT}"

    return None, None, "No RSI signal"


def check_autonomous_signals(symbol):
    # Choose strategy type
    granularity = f"M{STRATEGY_TIMEFRAME}"

    if STRATEGY_TYPE == "rsi":
        return check_rsi_signal(symbol, granularity)

    return check_ema_crossover(symbol, granularity)


def execute_autonomous_trade():
    # Main autonomous trading loop
    state = load_state()

    if not STRATEGY_ENABLED:
        write_log("Skipping trade: strategy disabled")
        return

    if not state.get("trading_enabled", False):
        write_log("Skipping trade: trading disabled")
        return

    if not state.get("forex_trading_enabled", True):
        write_log("Skipping trade: forex disabled in market settings")
        return

    # Force forex only because OANDA does not trade stocks
    state["asset_mode"] = "forex"
    save_state(state)

    if not is_market_open() and not state.get("force_forex_trading", False):
        write_log("Skipping trade: forex market closed")
        return

    if state.get("position_open", False):
        write_log("Skipping trade: position already marked open")
        return

    if int(state.get("trade_count", 0)) >= int(state.get("max_trades_per_day", 5)):
        write_log("Skipping trade: max daily trades reached")
        return

    if get_daily_pnl() <= -float(state.get("daily_loss_limit", 50.0)):
        write_log("Skipping trade: daily loss limit reached")
        return

    last_trade_time = state.get("last_trade_time")

    if last_trade_time:
        try:
            last_time = datetime.fromisoformat(last_trade_time)
            if datetime.now() - last_time < timedelta(minutes=15):
                write_log("Skipping trade: cooldown active")
                return
        except Exception:
            pass

    allowed_symbols = state.get(
        "allowed_symbols",
        ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF"],
    )

    for symbol in allowed_symbols:
        try:
            action, price, reason = check_autonomous_signals(symbol)

            if not action or not price:
                write_log(f"No signal for {symbol}: {reason}")
                continue

            if has_open_position(symbol):
                write_log(f"Skipping {symbol}: position already open")
                continue

            units = int(state.get("max_units_per_trade", 10000))

            validate_trade(state, symbol, action, price, units, "autonomous_strategy")

            order_result = build_oanda_order(
                symbol,
                units,
                action,
                price,
                state,
                "autonomous_strategy",
            )

            order_id = order_result.get("id")

            if not order_id:
                write_log(f"OANDA rejected order for {symbol}: {order_result}")
                send_telegram_message(f"❌ OANDA rejected order for {symbol}")
                continue

            handle_successful_trade(state, symbol, action, units, order_id, "autonomous_strategy")

            review = create_trade_review(symbol, action, price, "autonomous_strategy", reason)
            trade_score = create_trade_score(symbol, action, price, "autonomous_strategy", reason, state)

            save_trade_to_db(
                symbol,
                action,
                units,
                price,
                order_id,
                "autonomous_strategy",
                reason,
                review,
                trade_score,
            )

            state["position_open"] = True
            state["current_position_symbol"] = symbol
            state["last_signal"] = reason
            state["last_strategy_check"] = datetime.now().isoformat()

            save_state(state)

            send_telegram_message(
                f"✅ AUTONOMOUS TRADE EXECUTED\n"
                f"Symbol: {symbol}\n"
                f"Action: {action.upper()}\n"
                f"Units: {units}\n"
                f"Price: {price}\n"
                f"Reason: {reason}"
            )

            write_log(f"Autonomous trade executed: {action} {symbol} at {price}")
            break

        except Exception as error:
            write_log(f"Error checking {symbol}: {error}")
            continue


def monitor_open_positions():
    # Reset position flag if OANDA no longer shows the position
    state = load_state()

    if not state.get("position_open", False):
        return

    symbol = state.get("current_position_symbol")

    if not symbol:
        state["position_open"] = False
        save_state(state)
        return

    try:
        positions = client.get_open_positions()

        found_position = False

        for position in positions:
            if position.get("symbol") == symbol:
                found_position = True
                break

        if not found_position:
            state["position_open"] = False
            state["current_position_symbol"] = None
            save_state(state)
            write_log(f"Position closed or missing: {symbol}")
            send_telegram_message(f"Position closed or missing: {symbol}")

    except Exception as error:
        write_log(f"Error monitoring open positions: {error}")
