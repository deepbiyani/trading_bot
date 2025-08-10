from datetime import datetime
from pymongo import MongoClient
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "trading_bot")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

def set_status(script_name, status):
    db.script_status.update_one(
        {"script_name": script_name},
        {
            "$set": {
                "status": status,
                "last_updated": datetime.utcnow()
            }
        },
        upsert=True
    )

def is_already_running(script_name):
    status = db.script_status.find_one({"script_name": script_name})
    return status and status["status"] == "RUNNING"
