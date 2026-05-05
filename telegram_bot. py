# telegram_bot.py
# Telegram command polling for /on, /off, /state, and /help.

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from helpers import (
    get_daily_pnl,
    is_market_open,
    load_state,
    save_state,
    send_telegram_message,
    write_log,
)


def check_telegram_commands():
    # Skip if Telegram is not configured.
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    state = load_state()
    offset = int(state.get("last_telegram_update_id", 0)) + 1
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"

    try:
        response = requests.get(url, params={"offset": offset, "timeout": 1}, timeout=10)
        payload = response.json()
    except Exception as error:
        write_log(f"Telegram check failed: {error}")
        return

    if not payload.get("ok"):
        return

    for update in payload.get("result", []):
        state["last_telegram_update_id"] = update.get("update_id", offset)
        message = update.get("message", {})
        text = message.get("text", "")
        chat_id = str(message.get("chat", {}).get("id", ""))

        # Ignore other chats.
        if chat_id != str(TELEGRAM_CHAT_ID):
            continue

        if text == "/off":
            state["trading_enabled"] = False
            save_state(state)
            write_log("Trading disabled from Telegram")
            send_telegram_message("Trading is now OFF")
        elif text == "/on":
            state["trading_enabled"] = True
            save_state(state)
            write_log("Trading enabled from Telegram")
            send_telegram_message("Trading is now ON")
        elif text == "/state":
            send_telegram_message(
                f"Trading enabled: {state.get('trading_enabled')}\n"
                f"Maintenance: {state.get('maintenance_mode')}\n"
                f"Trades today: {state.get('trade_count')}\n"
                f"Market open: {is_market_open()}\n"
                f"Daily P/L: ${get_daily_pnl():.2f}"
            )
        elif text == "/help":
            send_telegram_message("Commands:\n/on\n/off\n/state\n/help")

    save_state(state)
