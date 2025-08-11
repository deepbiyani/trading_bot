import os
import sys
from kiteconnect import KiteConnect, KiteTicker
from datetime import datetime
from dotenv import load_dotenv
import logging
from bot.services import db_modal
import yaml


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Create logs directory if not exists
os.makedirs("logs", exist_ok=True)
# Configure logging
logging.basicConfig(
    filename="logs/kite_service.log",
    level=logging.INFO,  # could be DEBUG for more details
    format="%(asctime)s - %(levelname)s - %(message)s"
)
# Load variables from .env file
load_dotenv()

CONFIG_FILE = "config/settings.yaml"

def load_config():
    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f)

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        yaml.safe_dump(config, f)

def get_access_token(kite):
    access_token = None

    kite_config = db_modal.get_kite_config()

    # Get today's date
    today = datetime.utcnow().date()
    token_is_valid = False

    if kite_config:
        last_updated = kite_config.get("last_updated")

        if last_updated and last_updated.date() == today:
            # Access token is from today
            access_token = kite_config.get("access_token")
            token_is_valid = True

    if not token_is_valid:
        # Prompt for manual login and request_token
        print("\n🔐 Token not updated today. Please log in:")
        print(kite.login_url())
        request_token = input("Enter request token: ")

        # Exchange request token for access token
        data = kite.generate_session(request_token, api_secret=os.getenv("API_SECRET"))
        access_token = data['access_token']
        print(f"[INFO] New access token: {access_token}")
        db_modal.update_token_in_db(access_token)
    return access_token

def get_kite_client():
    kite = KiteConnect(api_key=os.getenv("API_KEY"))
    print(os.getenv("API_KEY"))

    access_token = get_access_token(kite)
    kite.set_access_token(access_token)
    return kite

def get_kite_ticker():
    kite = KiteConnect(api_key=os.getenv("API_KEY"))
    access_token = get_access_token(kite)
    kws = KiteTicker(os.getenv("API_KEY"), access_token)
    return kws

def update_access_token(request_token):
    logging.info("Initializing update_access_token...")
    logging.info(f"Token = {request_token}")
    kite = KiteConnect(api_key=os.getenv("API_KEY"))
    data = kite.generate_session(request_token, api_secret=os.getenv("API_SECRET"))

    logging.info("generate_session Response...")
    logging.info(data)
    access_token = data['access_token']
    return db_modal.update_token_in_db(access_token)




