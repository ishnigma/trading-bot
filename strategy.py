# strategy.py - Autonomous trading strategy
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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


def get_candles(symbol, count=100, granularity="M5"):
    try:
        return client.get_candle_data(symbol, count=count, granularity=granularity)
    except Exception as e:
        write_log(f"Failed to get candles for {symbol}: {e}")
        return []


def check_ema_crossover(symbol, granularity="M5"):
    candles = get_candles(symbol, count=100, granularity=granularity)
    if len(candles) < SLOW_EMA_PERIOD + 5:
        return None, None, "Insufficient data"
    
    closes = [c['close'] for c in candles]
    fast_ema = calculate_ema(closes, FAST_EMA_PERIOD)
    slow_ema = calculate_ema(closes, SLOW_EMA_PERIOD)
    
    current_fast = fast_ema[-1]
    current_slow = slow_ema[-1]
    prev_fast = fast_ema[-2]
    prev_slow = slow_ema[-2]
    current_price = closes[-1]
    
    if prev_fast <= prev_slow and current_fast > current_slow:
        return "buy", current_price, f"EMA {FAST_EMA_PERIOD} crossed above EMA {SLOW_EMA_PERIOD}"
    elif prev_fast >= prev_slow and current_fast < current_slow:
        return "sell", current_price, f"EMA {FAST_EMA_PERIOD} crossed below EMA {SLOW_EMA_PERIOD}"
    
    return None, None, "No signal"


def check_rsi_signal(symbol, granularity="M5"):
    candles = get_candles(symbol, count=100, granularity=granularity)
    if len(candles) < RSI_PERIOD + 5:
        return None, None, "Insufficient data"
    
    closes = [c['close'] for c in candles]
    rsi = calculate_rsi(closes, RSI_PERIOD)
    
    current_rsi = rsi[-1]
    prev_rsi = rsi[-2]
    current_price = closes[-1]
    
    if prev_rsi <= RSI_OVERSOLD and current_rsi > RSI_OVERSOLD:
        return "buy", current_price, f"RSI crossed above {RSI_OVERSOLD} (oversold)"
    elif prev_rsi >= RSI_OVERBOUGHT and current_rsi < RSI_OVERBOUGHT:
        return "sell", current_price, f"RSI crossed below {RSI_OVERBOUGHT} (overbought)"
    
    return None, None, "No signal"


def check_autonomous_signals(symbol):
    if STRATEGY_TYPE == "ema_crossover":
        return check_ema_crossover(symbol, f"M{STRATEGY_TIMEFRAME}")
    elif STRATEGY_TYPE == "rsi":
        return check_rsi_signal(symbol, f"M{STRATEGY_TIMEFRAME}")
    else:
        return check_ema_crossover(symbol, f"M{STRATEGY_TIMEFRAME}")


def execute_autonomous_trade():
    from config import STRATEGY_ENABLED
    
    state = load_state()
    
    if not STRATEGY_ENABLED:
        write_log("Autonomous strategy skipped: STRATEGY_ENABLED is false")
        return
    if not state.get("trading_enabled", False):
        write_log("Autonomous strategy skipped: trading_enabled is false. Use dashboard Enable or Telegram /on")
        return
    if not state.get("force_forex_trading", False) and not is_market_open():
        write_log("Autonomous strategy skipped: forex market is closed")
        return
    if state.get("position_open", False):
        write_log(f"Autonomous strategy skipped: state says position is open on {state.get('current_position_symbol')}")
        return
    if int(state.get("trade_count", 0)) >= int(state.get("max_trades_per_day", 5)):
        write_log("Autonomous strategy skipped: max trades per day reached")
        return
    if get_daily_pnl() <= -float(state.get("daily_loss_limit", 50.0)):
        write_log("Autonomous strategy skipped: daily loss limit reached")
        return

    last_trade_time = state.get("last_trade_time")
    if last_trade_time:
        last_time = datetime.fromisoformat(last_trade_time)
        now = datetime.now(last_time.tzinfo) if last_time.tzinfo else datetime.now(timezone.utc)
        if now - last_time < timedelta(minutes=15):
            write_log("Autonomous strategy skipped: 15 minute cooldown active")
            return
    
    allowed_symbols = state.get("allowed_symbols", ["EUR_USD", "GBP_USD", "USD_JPY"])
    
    for symbol in allowed_symbols:
        try:
            action, price, reason = check_autonomous_signals(symbol)
            
            state["last_strategy_check"] = datetime.now(timezone.utc).isoformat()
            state["last_signal"] = f"{symbol}: {reason}"
            save_state(state)

            if action and price:
                write_log(f"Autonomous signal: {action} {symbol} at {price} - {reason}")
                send_telegram_message(f"📊 Strategy Signal\n{symbol}\n{action.upper()} at {price}\n{reason}")
                
                if has_open_position(symbol):
                    write_log(f"Skipping {symbol} - position already open")
                    continue
                
                units = state.get("max_units_per_trade", 10000)
                
                try:
                    validate_trade(state, symbol, action, price, units, "ema_crossover_auto")
                    
                    order_result = build_oanda_order(symbol, units, action, price, state, "ema_crossover_auto")
                    order_id = order_result.get("id")
                    
                    handle_successful_trade(state, symbol, action, units, order_id, "ema_crossover_auto")
                    
                    review = create_trade_review(symbol, action, price, "ema_crossover_auto", reason)
                    trade_score = create_trade_score(symbol, action, price, "ema_crossover_auto", reason, state)
                    save_trade_to_db(symbol, action, units, price, order_id, "ema_crossover_auto", reason, review, trade_score)
                    
                    state["position_open"] = True
                    state["current_position_symbol"] = symbol
                    save_state(state)
                    
                    send_telegram_message(f"✅ AUTONOMOUS TRADE EXECUTED\nSymbol: {symbol}\nAction: {action}\nUnits: {units}\nPrice: {price}")
                    break
                    
                except ValueError as e:
                    record_message = f"Trade failed for {symbol}: {e}"
                    write_log(record_message)
                    send_telegram_message(f"❌ {record_message}")
                    continue
                    
        except Exception as e:
            write_log(f"Error checking {symbol}: {e}")
            continue


def monitor_open_positions():
    state = load_state()
    
    if not state.get("position_open", False):
        return
    
    symbol = state.get("current_position_symbol")
    if not symbol:
        return
    
    try:
        positions = client.get_open_positions()
        current_position = None
        for pos in positions:
            if pos['symbol'] == symbol:
                current_position = pos
                break
        
        if not current_position:
            state["position_open"] = False
            state["current_position_symbol"] = None
            save_state(state)
            write_log(f"Position {symbol} closed - resetting state")
            send_telegram_message(f"📉 Position closed: {symbol}")
            
    except Exception as e:
        write_log(f"Error monitoring positions: {e}")
