from kiteconnect import KiteConnect, KiteTicker
from pymongo import MongoClient
import yaml
from datetime import datetime

CONFIG_FILE = "config/settings.yaml"

def load_config():
    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f)

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        yaml.safe_dump(config, f)

def get_access_token(kite):
    config = load_config()  # Load API key/secret and existing token if any

    # Connect to MongoDB
    client = MongoClient("mongodb://localhost:27017/")
    db = client["trade_bot"]
    collection = db["kite_config"]

    # Fetch the document
    kite_config = collection.find_one({"_id": "kite_user"})

    # Get today's date
    today = datetime.utcnow().date()
    token_is_valid = False

    if kite_config:
        last_updated = kite_config.get("last_updated")

        if last_updated and last_updated.date() == today:
            # Access token is from today
            config['access_token'] = kite_config.get("access_token")
            token_is_valid = True

    if not token_is_valid:
        # Prompt for manual login and request_token
        print("\n🔐 Token not updated today. Please log in:")
        print(kite.login_url())
        request_token = input("Enter request token: ")

        # Exchange request token for access token
        data = kite.generate_session(request_token, api_secret=config['api_secret'])
        config['access_token'] = data['access_token']
        print(f"[INFO] New access token: {config['access_token']}")

        # Update in DB
        collection.update_one(
            {"_id": "kite_user"},
            {
                "$set": {
                    "api_key": config['api_key'],
                    "access_token": config['access_token'],
                    "last_updated": datetime.utcnow()
                }
            },
            upsert=True
        )
    return config['access_token']


def get_kite_client():
    config = load_config()  # Load API key/secret and existing token if any
    kite = KiteConnect(api_key=config['api_key'])

    access_token = get_access_token(kite)

    # Set the access token in Kite client
    kite.set_access_token(access_token)
    return kite

def get_kite_ticker():
    config = load_config()
    kite = KiteConnect(api_key=config['api_key'])

    access_token = get_access_token(kite)

    print(config['api_key'], access_token)
    kws = KiteTicker(config['api_key'], access_token)
    return kws

