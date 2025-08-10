"""
mf_automation.py

Usage:
  export MONGO_URI="mongodb://localhost:27017"
  export DB_NAME="trading_bot"
  export KITE_API_KEY="your_api_key"
  export KITE_API_SECRET="your_api_secret"
  export KITE_ACCESS_TOKEN="user_access_token"   # if required
  export TELEGRAM_BOT_TOKEN="..."
  export TELEGRAM_CHAT_ID="..."
  python3 mf_automation.py
"""

import os
import sys
import math
import traceback
from datetime import datetime, timedelta, date

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bot.services.kite_service import get_kite_client

from pymongo import MongoClient
from dotenv import load_dotenv

# Try to import pykiteconnect; if unavailable, script still works for DB-only flow
try:
    from kiteconnect import KiteConnect
    HAVE_KITE = True
except Exception:
    HAVE_KITE = False

load_dotenv()

### CONFIG
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "trading_bot")
KITE_API_KEY = os.getenv("API_KEY")
KITE_API_SECRET = os.getenv("API_SECRET")
KITE_ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN")  # user token
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SCRIPT_NAME = "mf_automation"

# --- MongoDB setup ---
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
logs_col = db.script_logs
status_col = db.script_status
stats_col = db.mf_stats   # store computed stats per run

# --- Utilities: logging to MongoDB ---
def log(level, msg, extra=None):
    entry = {
        "script_name": SCRIPT_NAME,
        "level": level,
        "message": msg,
        "extra": extra,
        "timestamp": datetime.utcnow()
    }
    try:
        logs_col.insert_one(entry)
    except Exception:
        print("Failed to write log to mongo:", traceback.format_exc())
    print(f"{datetime.utcnow().isoformat()} [{level}] {msg}")

def log_exception(prefix="Exception"):
    tb = traceback.format_exc()
    log("ERROR", f"{prefix}: {tb}")

# --- Status checker ---
def set_status(status):
    status_col.update_one(
        {"script_name": SCRIPT_NAME},
        {"$set": {"status": status, "last_updated": datetime.utcnow()}},
        upsert=True
    )

def is_already_running():
    doc = status_col.find_one({"script_name": SCRIPT_NAME})
    return doc and doc.get("status") == "RUNNING"

# --- Telegram helper (simple) ---
import requests
def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("WARN", "Telegram not configured; skipping send", {"text": text})
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            log("ERROR", f"Telegram send failed: {r.status_code} {r.text}", {"text": text})
    except Exception:
        log_exception("Telegram send error")

# --- Kite client wrapper (if available) ---
class KiteClientWrapper:
    def __init__(self):
        if not HAVE_KITE:
            raise RuntimeError("pykiteconnect not installed")
        # if not (KITE_API_KEY and KITE_ACCESS_TOKEN):
        #     raise RuntimeError("Kite API key or access token missing")
        # self.kite = KiteConnect(api_key=KITE_API_KEY)
        # self.kite.set_access_token(KITE_ACCESS_TOKEN)
        self.kite = get_kite_client()

    def get_mf_orders(self):
        # returns list of order dicts (depends on Kite response)
        return self.kite.mf_orders()

    def get_mf_holdings(self):
        return self.kite.mf_holdings()

    def get_instruments(self):
        return self.kite.mf_instruments()

    def place_mf_order(self, fund_isin, amount=None, units=None, order_type="BUY"):
        """
        Try to place MF order. Many Kite accounts can't place MF orders due to gateway/payment limitations.
        Here we call the endpoint if available — wrap in try/except in caller.
        """
        # Note: pykiteconnect method names vary; adjust to your installed version.
        # Some installations might not support placing MF orders. This is a best-effort call.
        return self.kite.place_mf_order(isin=fund_isin, transaction_type=order_type, amount=amount, units=units)

# --- Core logic ---
def compute_12m_stats(orders, now=None):
    """
    orders: list of dicts similar to Kite's mf orders
    returns: dict keyed by fund_isin (or scheme_code) with computed stats:
      { "fund_key": {
           "last_buy_date": datetime,
           "last_buy_price": float,
           "last_buy_quantity": float,
           "avg_buy_nav_12m": float,
           "avg_buy_qty_12m": float,
           "total_buys_12m": int
         }, ...
      }
    """
    if now is None:
        now = datetime.utcnow()
    cutoff = now - timedelta(days=365)

    funds = {}
    for o in orders:
        # normalize fields depending on API response; try common names
        # typical fields: 'transaction_type', 'tradingsymbol'/'fund_name', 'order_timestamp', 'amount', 'units', 'price', 'isin'
        try:
            tx_type = o.get("transaction_type") or o.get("transactionType") or o.get("transaction")
            if not tx_type or tx_type.upper() != "BUY":
                continue
            # timestamp parsing (API often returns ISO strings)
            ts_str = o.get("order_timestamp") or o.get("orderTime") or o.get("created_at") or o.get("timestamp")

            if isinstance(ts_str, str):
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
            elif isinstance(ts_str, datetime):
                ts = ts_str
            else:
                ts = now
            if ts < cutoff:
                # skip buys older than 12 months for averages, but we still track last_buy separately
                pass

            fund_key = o.get("isin") or o.get("fund_isin") or o.get("tradingsymbol") or o.get("scheme_code") or o.get("fund_name")
            if not fund_key:
                continue

            price = None
            if o.get("average_price") is not None:
                price = float(o.get("average_price"))
            elif o.get("nav") is not None:
                price = float(o.get("nav"))
            elif o.get("amount") and o.get("units"):
                # nav = amount / units
                price = float(o.get("amount")) / float(o.get("units"))

            qty = None
            if o.get("units") is not None:
                qty = float(o.get("units"))
            elif o.get("quantity") is not None:
                qty = float(o.get("quantity"))

            rec = funds.setdefault(fund_key, {"buys": [], "last_buy": None})
            # Update last_buy if newer
            if rec["last_buy"] is None or ts > rec["last_buy"]["ts"]:
                rec["last_buy"] = {"ts": ts, "price": price, "qty": qty, "raw": o}

            if ts >= cutoff:
                # include in 12m stats
                rec["buys"].append({"ts": ts, "price": price, "qty": qty, "raw": o})

        except Exception:
            log_exception("Error parsing order")

    # compute averages
    results = {}
    for k, v in funds.items():
        buys = v["buys"]
        total_nav = 0.0
        total_qty = 0.0
        count = 0
        for b in buys:
            if b["price"] is not None:
                total_nav += b["price"]
            if b["qty"] is not None:
                total_qty += b["qty"]
            count += 1
        avg_nav = (total_nav / count) if count else None
        avg_qty = (total_qty / count) if count else None
        last = v["last_buy"]
        results[k] = {
            "last_buy_date": last["ts"] if last else None,
            "last_buy_price": last["price"] if last else None,
            "last_buy_quantity": last["qty"] if last else None,
            "avg_buy_nav_12m": avg_nav,
            "avg_buy_qty_12m": avg_qty,
            "total_buys_12m": count
        }

    return results

def round_to_int(x):
    if x is None:
        return None
    return max(1, int(math.floor(x + 0.5)))

def place_buy_or_alert(kite_wrapper, fund_key, stats):
    """
    Attempt to place buy. If Kite MF order not available, send telegram alert.
    fund_key: usually ISIN or tradingsymbol
    stats: computed stats dict
    """
    last_price = stats.get("last_buy_price")
    avg_qty = stats.get("avg_buy_qty_12m")
    if avg_qty is None or last_price is None:
        log("WARN", f"{fund_key}: insufficient data (last_price={last_price}, avg_qty={avg_qty})")
        return

    # determine current price: try fetching latest instrument or last trade (this part depends on availability)
    cur_price = None
    try:
        # For MF, Kite might return instruments with 'last_price' or 'nav'. This step is API dependent.
        # We'll try to look into instruments collection we cached earlier (stats_col may contain it), else fallback to last buy price.
        cached_instr = db.mf_instruments.find_one({"fund_key": fund_key})
        if cached_instr and cached_instr.get("last_price"):
            cur_price = float(cached_instr["last_price"])
    except Exception:
        log_exception("reading cached instrument")

    if cur_price is None:
        cur_price = last_price  # conservative fallback

    drop_pct = (last_price - cur_price) / last_price if last_price else 0.0
    log("INFO", f"{fund_key}: last_price={last_price}, cur_price={cur_price}, drop_pct={drop_pct:.4f}")

    if drop_pct >= -0.01 - 1e-9:  # 1.5%
        buy_units = round_to_int(avg_qty)
        # Try to place via kite wrapper if available
        if HAVE_KITE and kite_wrapper:
            try:
                # Many kite installations do not support mf buy via API; adjust parameters as required by your pykiteconnect version
                # tradingsymbol,
                # transaction_type,
                # quantity = None,
                # amount = None,
                # tag = None
                print(fund_key, stats)
                resp = kite_wrapper.place_mf_order(fund_isin=fund_key, units=buy_units, amount=None, order_type="BUY")
                log("INFO", f"Placed MF buy order via Kite for {fund_key}: units={buy_units}, resp={resp}")
                send_telegram_message(f"✅ Placed MF BUY for <b>{fund_key}</b>\nUnits: {buy_units}\nPrice(last buy): {last_price}\nCurrent: {cur_price}")
                return
            except Exception as e:
                log("WARN", f"Kite place MF order failed for {fund_key}: {e}")
                log_exception("place_mf_order")
                # fallthrough to alert
        # Fallback: send Telegram alert with buy suggestion
        msg = (f"⚠️ <b>MF Buy suggested for {fund_key}</b>\n"
               f"Last buy: {last_price}\nCurrent: {cur_price}\n"
               f"Drop: {drop_pct*100:.2f}% >= 1%\n"
               f"Suggested units: {buy_units}\n\n"
               f"Suggested buy value: {math.ceil((buy_units * cur_price) / 500) * 500}\n\n"
               "Unable to place order automatically — please review and place manually.")
        send_telegram_message(msg)
        log("INFO", f"Alerted for manual buy: {fund_key}")
    else:
        log("INFO", f"No buy: {fund_key} \t drop {drop_pct*100:.2f}% < 1.5%")

def fix_dates(doc):
    for key, value in doc.items():
        if isinstance(value, date) and not isinstance(value, datetime):
            doc[key] = datetime(value.year, value.month, value.day)
        elif isinstance(value, dict):
            fix_dates(value)
    return doc

# --- Main run ---
def run():
    # if is_already_running():
    #     msg = "⚠️ Warning: MF automation already running. Exiting."
    #     log("WARN", msg)
    #     send_telegram_message(msg)
    #     return
    # set_status("RUNNING")
    try:
        log("INFO", "MF automation started")

        kite_wrapper = None
        if HAVE_KITE:
            try:
                kite_wrapper = KiteClientWrapper()
                log("INFO", "Kite client ready")
            except Exception as e:
                log("WARN", f"Kite client init failed: {e}")
                log_exception("kite_init")

        # 1) Fetch orders
        orders = []
        try:
            if kite_wrapper:
                orders = kite_wrapper.get_mf_orders() or []
                # Orders format depends on kite version; make sure it's a list of dicts
            else:
                log("WARN", "Kite not available; no orders fetched")
        except Exception:
            log_exception("fetch_orders")

        orders = [order for order in orders if order["tradingsymbol"] == 'INF879O01027']

        for order in orders:
            # Upsert (update if exists, otherwise insert)
            db.mf_orders_collection.update_one(
                {"order_id": order["order_id"]},  # match by order_id
                {"$set": order},  # update with latest data
                upsert=True  # insert if not found
            )

        print(f"{len(orders)} MF orders processed (added/updated).")

        orders = list(db.mf_orders_collection.find({}))

        # 2) Compute stats
        stats = compute_12m_stats(orders, now=datetime.utcnow())

        # store stats for inspection
        stats_col.insert_one({"computed_at": datetime.utcnow(), "stats": stats})
        log("INFO", f"Computed stats for {len(stats)} funds")

        # 3) Optionally cache instruments / holdings (to find current NAV)
        try:
            if kite_wrapper:
                instruments = kite_wrapper.get_instruments() or []
                # store mapping by ISIN or tradingsymbol
                for inst in instruments:
                    # typical fields: 'isin', 'tradingsymbol', 'last_price' or 'nav'
                    fund_key = inst.get("isin") or inst.get("tradingsymbol")
                    inst = fix_dates(inst)
                    db.mf_instruments.update_one(
                        {"fund_key": fund_key},
                        {"$set": {"meta": inst, "last_price": inst.get("last_price") or inst.get("nav")}},
                        upsert=True
                    )
        except Exception:
            log_exception("fetch_instruments")

        # 4) For each fund, evaluate condition and place order/alert
        for fund_key, s in stats.items():
            try:
                place_buy_or_alert(kite_wrapper, fund_key, s)
            except Exception:
                log_exception(f"processing fund {fund_key}")

        log("INFO", "MF automation run completed")

    except Exception:
        log_exception("Top-level run")
    finally:
        set_status("STOPPED")
        log("INFO", "MF automation stopped")


#Mutual fund automations
# read average buy value of last 12 months. Store all the stats of mutual fund portfolio, including last buy, average buy nav of last 1 year, average buy quantity of last one year
# also get the last buy price
# place the buy order if market falls 1.5% from the last buy price. Buy quantity quall to buy of last 1 year
if __name__ == "__main__":
    run()
