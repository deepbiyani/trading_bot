import requests
import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def send_telegram_message(message):
    BOT_TOKEN = "8056773259:AAHOszstUfFRyXtnUTeY3SSJf4YkqZWDJ6c"
    USER_ID = "6117044035"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": USER_ID,
        "text": message
    }
    response = requests.post(url, data=data)
    # print(response.json())

# Usage


# send_telegram_message("✅ Test trading alert from trading bot")