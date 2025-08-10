import os
import sys
import time
import traceback

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bot.trading_alerts import get_recent_messages, send_telegram_message
from bot.services import kite_service
from bot.services.logger import get_logger
from bot.services.status_checker import set_status, is_already_running


def send_menu():
    menu = ("Select Script \n "
            "1. run ticker")
    send_telegram_message(menu)

def run_ticker():
    SCRIPT_NAME = "run_ticker"
    logger = get_logger(SCRIPT_NAME)

    print("checking status")
    if is_already_running(SCRIPT_NAME):
        send_telegram_message("⚠️ Warning  Script already running. Exiting.")
        logger.warning("Script already running. Exiting.")
        print("Script already running. Exiting.")
        return

    try:
        set_status(SCRIPT_NAME, "RUNNING")
        logger.info("Telegram bot started " + SCRIPT_NAME)
        print("Telegram bot started " + SCRIPT_NAME)

        os.system("python3 bot/ticker.py")
        send_telegram_message("✅ ticker started ...")

    except Exception as e:
        print(traceback.format_exc())
        logger.exception(f"Error occurred: {e}")

    finally:
        set_status(SCRIPT_NAME, "STOPPED")
        logger.info("Telegram bot stopped")
        print("Telegram bot stopped")

# python3 -m bot.telegram_bot

# run_ticker()
#
# exit()
# Keep the main thread alive
while True:

    try:
        TOKEN ="8056773259:AAHOszstUfFRyXtnUTeY3SSJf4YkqZWDJ6c"
        seconds = 10
        now = int(time.time())  # current Unix time (seconds)
        resp = get_recent_messages()

        if "result" in resp:
            for update in resp["result"]:
                if "message" in update:
                    msg_time = update["message"]["date"]  # timestamp
                    if now - msg_time <= seconds:
                        # text = update["message"]["text"].lower()
                        text = update["message"]["text"]
                        print(text)

                        #Now Add script to automate the scripts
                        if text.lower() == 'hi':
                            send_menu()

                        #update access token
                        elif text.startswith("request_token"):
                            request_token = text.replace("request_token=", "", 1)  # only replace the first match
                            res = kite_service.update_access_token(request_token)
                            if res:
                                send_telegram_message("✅ Token updated ...")

                        elif text.lower() == "run ticker":
                            run_ticker()

    except Exception as e:
        print(e)
        print(traceback.format_exc())
        send_telegram_message(f"❌ Error reading messages : {e}")
    time.sleep(10)

