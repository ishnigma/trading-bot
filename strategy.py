# strategy.py - Autonomous trading strategy with EMA crossover
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import time

from oanda_client import OandaClient
from config import (
    FAST_EMA_PERIOD,
    SLOW_EMA_PERIOD,
    RSI_PERIOD,
    RSI_OVERSOLD,
    RSI_OVERBOUGHT,
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
)


def calculate_ema(prices, period):
    """Calculate Exponential Moving Average"""
    if len(prices) < period:
        return prices
    
    multiplier = 2 / (period + 1)
    ema = [prices[0]]
    for price in prices[1:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])
    return ema


def calculate_rsi(prices, period=14):
    """Calculate RSI indicator"""
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


def calculate_macd(prices, fast=12, slow=26, signal=9):
    """Calculate MACD indicator"""
    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)
    macd_line = [ema_fast[i] - ema_slow[i] for i in range(len(prices))]
    signal_line = calculate_ema(macd_line, signal)
    histogram = [macd_line[i] - signal_line[i] for i in range(len(prices))]
    return macd_line, signal_line, histogram


def get_candles(symbol, count=100, granularity="M5"):
    """Get historical candles from OANDA"""
    try:
        candles = client.get_candle_data(symbol, count=count, granularity=granularity)
        return candles
    except Exception as e:
        write_log(f"Failed to get candles for {symbol}: {e}")
        return []


def check_ema_crossover(symbol, granularity="M5"):
    """Check for EMA crossover signals"""
    candles = get_candles(symbol, count=100, granularity=granularity)
    if len(candles) < SLOW_EMA_PERIOD + 5:
        return None, None, "Insufficient data"
    
    closes = [c['close'] for c in candles]
    
    # Calculate EMAs
    fast_ema = calculate_ema(closes, FAST_EMA_PERIOD)
    slow_ema = calculate_ema(closes, SLOW_EMA_PERIOD)
    
    # Get current and previous values
    current_fast = fast_ema[-1]
    current_slow = slow_ema[-1]
    prev_fast = fast_ema[-2]
    prev_slow = slow_ema[-2]
    
    current_price = closes[-1]
    
    # Check for crossover
    if prev_fast <= prev_slow and current_fast > current_slow:
        return "buy", current_price, f"EMA {FAST_EMA_PERIOD} crossed above EMA {SLOW_EMA_PERIOD}"
    elif prev_fast >= prev_slow and current_fast < current_slow:
        return "sell", current_price, f"EMA {FAST_EMA_PERIOD} crossed below EMA {SLOW_EMA_PERIOD}"
    
    return None, None, "No signal"


def check_rsi_signal(symbol, granularity="M5"):
    """Check for RSI oversold/overbought signals"""
    candles = get_candles(symbol, count=100, granularity=granularity)
    if len(candles) < RSI_PERIOD + 5:
        return None, None, "Insufficient data"
    
    closes = [c['close'] for c in candles]
    rsi = calculate_rsi(closes, RSI_PERIOD)
    
    current_rsi = rsi[-1]
    prev_rsi = rsi[-2]
    current_price = closes[-1]
    
    # Buy signal: RSI crosses above oversold level
    if prev_rsi <= RSI_OVERSOLD and current_rsi > RSI_OVERSOLD:
        return "buy", current_price, f"RSI crossed above {RSI_OVERSOLD} (oversold)"
    # Sell signal: RSI crosses below overbought level
    elif prev_rsi >= RSI_OVERBOUGHT and current_rsi < RSI_OVERBOUGHT:
        return "sell", current_price, f"RSI crossed below {RSI_OVERBOUGHT} (overbought)"
    
    return None, None, "No signal"


def check_macd_signal(symbol, granularity="M5"):
    """Check for MACD crossover signals"""
    candles = get_candles(symbol, count=100, granularity=granularity)
    if len(candles) < 50:
        return None, None, "Insufficient data"
    
    closes = [c['close'] for c in candles]
    macd_line, signal_line, histogram = calculate_macd(closes)
    
    current_macd = macd_line[-1]
    current_signal = signal_line[-1]
    prev_macd = macd_line[-2]
    prev_signal = signal_line[-2]
    current_price = closes[-1]
    
    # Buy signal: MACD crosses above signal line
    if prev_macd <= prev_signal and current_macd > current_signal:
        return "buy", current_price, "MACD crossed above signal line"
    # Sell signal: MACD crosses below signal line
    elif prev_macd >= prev_signal and current_macd < current_signal:
        return "sell", current_price, "MACD crossed below signal line"
    
    return None, None, "No signal"


def check_multi_timeframe_signal(symbol):
    """Check signals across multiple timeframes"""
    timeframes = ["M5", "M15", "M30", "H1"]
    buy_signals = 0
    sell_signals = 0
    
    for tf in timeframes:
        action, price, reason = check_ema_crossover(symbol, tf)
        if action == "buy":
            buy_signals += 1
        elif action == "sell":
            sell_signals += 1
    
    # Require 3 out of 4 timeframes to agree
    if buy_signals >= 3:
        candles = get_candles(symbol, count=5, granularity="M1")
        current_price = candles[-1]['close'] if candles else 0
        return "buy", current_price, f"Multi-timeframe signal: {buy_signals}/4 timeframes bullish"
    elif sell_signals >= 3:
        candles = get_candles(symbol, count=5, granularity="M1")
        current_price = candles[-1]['close'] if candles else 0
        return "sell", current_price, f"Multi-timeframe signal: {sell_signals}/4 timeframes bearish"
    
    return None, None, "No consensus"


def check_autonomous_signals(symbol):
    """Main signal checking function"""
    if STRATEGY_TYPE == "ema_crossover":
        return check_ema_crossover(symbol, f"M{STRATEGY_TIMEFRAME}")
    elif STRATEGY_TYPE == "rsi":
        return check_rsi_signal(symbol, f"M{STRATEGY_TIMEFRAME}")
    elif STRATEGY_TYPE == "macd":
        return check_macd_signal(symbol, f"M{STRATEGY_TIMEFRAME}")
    elif STRATEGY_TYPE == "multi_timeframe":
        return check_multi_timeframe_signal(symbol)
    else:
        return check_ema_crossover(symbol, f"M{STRATEGY_TIMEFRAME}")


def execute_autonomous_trade():
    """Main autonomous trading function - called by scheduler"""
    state = load_state()
    
    # Check if autonomous trading is enabled
    from config import STRATEGY_ENABLED
    if not STRATEGY_ENABLED:
        return
    
    # Check if trading is enabled
    if not state.get("trading_enabled", False):
        return
    
    # Check if market is open
    if not is_market_open():
        return
    
    # Check if we already have an open position
    if state.get("position_open", False):
        return
    
    # Check daily limits
    if int(state.get("trade_count", 0)) >= int(state.get("max_trades_per_day", 5)):
        return
    
    # Check daily loss limit
    if get_daily_pnl() <= -float(state.get("daily_loss_limit", 50.0)):
        return
    
    # Check cooldown
    last_trade_time = state.get("last_trade_time")
    if last_trade_time:
        last_time = datetime.fromisoformat(last_trade_time)
        if datetime.now() - last_time < timedelta(minutes=15):
            return
    
    # Check each allowed symbol for signals
    allowed_symbols = state.get("allowed_symbols", ["EUR_USD", "GBP_USD", "USD_JPY"])
    
    for symbol in allowed_symbols:
        try:
            # Get signal
            action, price, reason = check_autonomous_signals(symbol)
            
            if action and price:
                write_log(f"Autonomous signal: {action} {symbol} at {price} - {reason}")
                send_telegram_message(f"📊 Strategy Signal\n{symbol}\n{action.upper()} at {price}\n{reason}")
                
                # Check if we already have a position on this symbol
                from helpers import has_open_position
                if has_open_position(symbol):
                    write_log(f"Skipping {symbol} - position already open")
                    continue
                
                # Validate trade
                units = state.get("max_units_per_trade", 10000)
                
                try:
                    validate_trade(state, symbol, action, price, units, "ema_crossover_auto")
                    
                    # Execute trade
                    order_result = build_oanda_order(symbol, units, action, price, state, "ema_crossover_auto")
                    order_id = order_result.get("id", "unknown")
                    
                    # Record trade
                    handle_successful_trade(state, symbol, action, units, order_id, "ema_crossover_auto")
                    
                    from helpers import create_trade_review, create_trade_score, save_trade_to_db
                    review = create_trade_review(symbol, action, price, "ema_crossover_auto", reason)
                    trade_score = create_trade_score(symbol, action, price, "ema_crossover_auto", reason, state)
                    save_trade_to_db(symbol, action, units, price, order_id, "ema_crossover_auto", reason, review, trade_score)
                    
                    # Mark position as open
                    state["position_open"] = True
                    state["current_position_symbol"] = symbol
                    state["position_open_time"] = datetime.now().isoformat()
                    save_state(state)
                    
                    send_telegram_message(f"✅ AUTONOMOUS TRADE EXECUTED\nSymbol: {symbol}\nAction: {action}\nUnits: {units}\nPrice: {price}")
                    
                    # Only execute one trade per cycle
                    break
                    
                except ValueError as e:
                    write_log(f"Validation failed for {symbol}: {e}")
                    continue
                    
        except Exception as e:
            write_log(f"Error checking {symbol}: {e}")
            continue


def monitor_open_positions():
    """Monitor open positions and check for exit conditions"""
    state = load_state()
    
    if not state.get("position_open", False):
        return
    
    symbol = state.get("current_position_symbol")
    if not symbol:
        return
    
    # Get current position from OANDA
    try:
        positions = client.get_open_positions()
        current_position = None
        for pos in positions:
            if pos['symbol'] == symbol:
                current_position = pos
                break
        
        # If position is closed (not found), reset state
        if not current_position:
            state["position_open"] = False
            state["current_position_symbol"] = None
            state["last_signal"] = None
            save_state(state)
            write_log(f"Position {symbol} closed - resetting state")
            send_telegram_message(f"📉 Position closed: {symbol}")
            
    except Exception as e:
        write_log(f"Error monitoring positions: {e}")
