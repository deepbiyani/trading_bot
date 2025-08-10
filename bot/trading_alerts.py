import os
import sys
import time
import requests
import traceback

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
# BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

def send_telegram_message(message):
    USER_ID = "6117044035"
    BOT_TOKEN = "8056773259:AAHOszstUfFRyXtnUTeY3SSJf4YkqZWDJ6c"

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": USER_ID,
        "text": message
    }
    response = requests.post(url, data=data)

def get_recent_messages():

    try:
        BOT_TOKEN = "8056773259:AAHOszstUfFRyXtnUTeY3SSJf4YkqZWDJ6c"

        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
        return requests.get(url).json()

    except Exception as e:
        print(e)
        send_telegram_message(f"❌ Error reading commands..... : {e}")


