import os
from datetime import datetime
from pymongo import MongoClient
import logging

from bot.services.logger import MongoLogHandler

client = MongoClient("mongodb://localhost:27017/")
db = client["trade_bot"]

def get_kite_config():
    collection = db["kite_config"]
    return collection.find_one({"_id": "kite_user"})

def update_token_in_db(access_token):

    collection = db["kite_config"]

    # Update in DB
    return collection.update_one(
        {"_id": "kite_user"},
        {
            "$set": {
                "api_key": os.getenv("API_KEY"),
                "access_token": access_token,
                "last_updated": datetime.utcnow()
            }
        },
        upsert=True
    )

def get_logger(script_name):
    logger = logging.getLogger(script_name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:  # Avoid duplicate handlers
        mongo_handler = MongoLogHandler(script_name)
        mongo_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(mongo_handler)

    return logger
