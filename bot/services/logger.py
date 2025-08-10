import logging
from datetime import datetime
from pymongo import MongoClient
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "trading_bot")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

class MongoLogHandler(logging.Handler):
    def __init__(self, script_name):
        super().__init__()
        self.script_name = script_name

    def emit(self, record):
        log_entry = {
            "script_name": self.script_name,
            "level": record.levelname,
            "message": self.format(record),
            "timestamp": datetime.utcnow()
        }
        db.script_logs.insert_one(log_entry)

def get_logger(script_name):
    logger = logging.getLogger(script_name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:  # Avoid duplicate handlers
        mongo_handler = MongoLogHandler(script_name)
        mongo_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(mongo_handler)

    return logger
