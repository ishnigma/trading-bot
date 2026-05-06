# telegram_bot.py
import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


def check_telegram_commands():
    # Safe placeholder so app.py does not crash if Telegram is not ready
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
        requests.get(url, timeout=10)
    except Exception as error:
        print(f"[TELEGRAM ERROR] {error}")
