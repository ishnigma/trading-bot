# strategy.py - FIXED VERSION (More signals, better debug, works 24/7 with override)
from datetime import datetime, timedelta

from config import (
    FAST_EMA_PERIOD,
    SLOW_EMA_PERIOD,
    RSI_PERIOD,
    RSI_OVERSOLD,
    RSI_OVERBOUGHT,
    STRATEGY_TIMEFRAME,
    STRATEGY_TYPE,
    STRATEGY_ENABLED,
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
    if len(prices) < period:
        return prices
    multiplier = 2 / (period + 1)
    ema = [prices[0]]
    for price in prices[1:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])
    return ema


def calculate_rsi(prices, period=14):
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


def get_candles(symbol, count=150, granularity=None):
    if granularity is None:
        granularity = f"M{STRATEGY_TIMEFRAME}"
    try:
        return client.get_candle_data(symbol, count=count, granularity=granularity)
    except Exception as e:
        write_log(f"Failed to get candles for {symbol}: {e}")
        return []


def check_ema_crossover(symbol):
    candles = get_candles(symbol, count=150)
    if len(candles) < 50:
        return None, None, "Insufficient data"
    
    closes = [c['close'] for c in candles]
    fast_ema = calculate_ema(closes, FAST_EMA_PERIOD)
    slow_ema = calculate_ema(closes, SLOW_EMA_PERIOD)
    
    if fast_ema[-2] <= slow_ema[-2] and fast_ema[-1] > slow_ema[-1]:
        return "buy", closes[-1], f"EMA {FAST_EMA_PERIOD}/{SLOW_EMA_PERIOD} bullish crossover"
    elif fast_ema[-2] >= slow_ema[-2] and fast_ema[-1] < slow_ema[-1]:
        return "sell", closes[-1], f"EMA {FAST_EMA_PERIOD}/{SLOW_EMA_PERIOD} bearish crossover"
    
    return None, None, "No crossover"


def check_autonomous_signals(symbol):
    # EMA Crossover first
    action, price, reason = check_ema_crossover(symbol)
    if action:
        return action, price, reason
    
    # RSI fallback (more frequent signals)
    candles = get_candles(symbol, count=100)
    if len(candles) >= RSI_PERIOD + 5:
        closes = [c['close'] for c in candles]
        rsi = calculate_rsi(closes, RSI_PERIOD)
        current_rsi = rsi[-1]
        
        if current_rsi < RSI_OVERSOLD:
            return "buy", closes[-1], f"RSI oversold at {current_rsi:.1f}"
        if current_rsi > RSI_OVERBOUGHT:
            return "sell", closes[-1], f"RSI overbought at {current_rsi:.1f}"
    
    return None, None, "No signal"


def execute_autonomous_trade():
    state = load_state()
    
    # === DEBUG LOGGING (this will show exactly why it's not trading) ===
    market_open_status = is_market_open()
    force_override = state.get("force_forex_trading", False)
    write_log(f"DEBUG EXEC: trading_enabled={state.get('trading_enabled')}, "
              f"market_open={market_open_status}, override={force_override}, "
              f"position_open={state.get('position_open')}, trades_today={state.get('trade_count')}, "
              f"daily_pnl={get_daily_pnl():.2f}")

    if not STRATEGY_ENABLED:
        write_log("DEBUG: STRATEGY_ENABLED is False in config")
        return
    if not state.get("trading_enabled", False):
        write_log("DEBUG: trading_enabled = False in state")
        return
    if not force_override and not market_open_status:
        write_log("DEBUG: Market is closed and no override active")
        return
    if state.get("position_open", False):
        write_log("DEBUG: Position already open - skipping")
        return
    if int(state.get("trade_count", 0)) >= int(state.get("max_trades_per_day", 5)):
        write_log("DEBUG: Daily trade limit reached")
        return
    if get_daily_pnl() <= -float(state.get("daily_loss_limit", 50.0)):
        write_log("DEBUG: Daily loss limit hit")
        return

    allowed_symbols = state.get("allowed_symbols", ["EUR_USD", "GBP_USD", "USD_JPY"])
    write_log(f"DEBUG: Scanning {len(allowed_symbols)} symbols for signals...")

    for symbol in allowed_symbols:
        try:
            action, price, reason = check_autonomous_signals(symbol)
            
            if action and price:
                write_log(f"✅ SIGNAL: {action.upper()} {symbol} @ {price} | {reason}")
                send_telegram_message(f"📊 SIGNAL DETECTED\n{symbol} {action.upper()} @ {price}\n{reason}")

                if has_open_position(symbol):
                    write_log(f"Position already open on {symbol}")
                    continue

                units = int(state.get("max_units_per_trade", 10000))

                validate_trade(state, symbol, action, price, units, "ema_crossover_auto")
                
                order_result = build_oanda_order(symbol, units, action, price, state, "ema_crossover_auto")
                order_id = order_result.get("id", "unknown")

                handle_successful_trade(state, symbol, action, units, order_id, "ema_crossover_auto")
                
                review = create_trade_review(symbol, action, price, "ema_crossover_auto", reason)
                trade_score = create_trade_score(symbol, action, price, "ema_crossover_auto", reason, state)
                save_trade_to_db(symbol, action, units, price, order_id, "ema_crossover_auto", reason, review, trade_score)

                state["position_open"] = True
                state["current_position_symbol"] = symbol
                save_state(state)

                send_telegram_message(f"🚀 TRADE EXECUTED\n{symbol} {action.upper()} {units} units @ {price}")
                return  # Only trade once per cycle
                
        except ValueError as e:
            write_log(f"Validation skipped {symbol}: {e}")
            continue
        except Exception as e:
            write_log(f"Error checking {symbol}: {e}")
            continue

    
    write_log("DEBUG: No trading signals this cycle")