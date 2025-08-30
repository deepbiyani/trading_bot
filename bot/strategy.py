# Strategy	Description	Risk Level
# Short Strangle	OTM Call + OTM Put (like above)	Moderate
# Short Straddle	ATM Call + ATM Put (higher premium)	High
# Iron Condor	Short Strangle + Buy wings (hedged)	Low
# Calendar Spread	Sell near expiry, buy far expiry
import logging
from pymongo import MongoClient
import datetime
import sys
import os
import requests
import pandas as pd
from io import StringIO

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bot.trading_alerts import send_telegram_message

# -------- Logger Setup --------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/averaging_trades.log"),  # File log
        logging.StreamHandler(sys.stdout)             # Console log
    ]
)

RED = "\033[91m"
GREEN = "\033[92m"
RESET = "\033[0m"

today_cnc_orders = []
holding_cached = []

def check_and_average(kite):

    global today_cnc_orders, holding_cached

    if not holding_cached:
        print("\n📌 Loading holding... till Today:\n" + "-" * 70)
        holding_cached = kite.holdings()

    if not today_cnc_orders:
        print("\n📌 Loading order for Today:\n" + "-" * 70)
        fetch_today_orders(kite)

    # -------- MongoDB Setup --------
    client = MongoClient("mongodb://localhost:27017/")
    db = client["trade_bot"]
    collection = db["averaging_trades"]

    #Clear Console
    os.system('cls' if os.name == 'nt' else 'clear')
    print(f"\nTime : {datetime.datetime.now()}")

    # ✅ Batch fetch all LTPs in ONE request
    symbols = [f"NSE:{s['tradingsymbol']}" for s in holding_cached if not s['tradingsymbol'].upper().startswith("SGB")]
    if not symbols:
        print("⚠️ No valid symbols found in holdings")
        return

    ltp_data = kite.ltp(symbols)

    for stock in holding_cached:
        symbol = stock["tradingsymbol"]
        qty = int(stock["opening_quantity"])

        # ✅ Skip unwanted symbols
        if symbol.upper().startswith("SGB"):
            # print(f"⏩ Skipping {symbol} (dummy symbol)")
            continue
        if symbol.upper().startswith("NACLIND"):
            continue

        # Get LTP
        # ltp_data = kite.ltp(f"NSE:{symbol}")
        ltp = float(ltp_data[f"NSE:{symbol}"]["last_price"])

        # Fetch existing record
        record = collection.find_one({"tradingsymbol": symbol})

        # If no record yet → create initial entry
        if not record:
            collection.insert_one({
                "tradingsymbol": symbol,
                "last_buy_price": ltp,
                "ltp": ltp,
                "quantity": qty,
                "last_buy_qty": 0,
                "averaging_rise": 5,
                "averaging_fall": 5,
                "averaging_qnt": 5,
                "order_id": None,
                "order_logs": [],   # ✅ New field to store order history
                "updated_at": datetime.datetime.now()
            })
            continue

        last_buy_price = record["last_buy_price"]
        averaging_fall = record.get("averaging_fall", 5)
        averaging_rise = record.get("averaging_rise", 10)
        averaging_qnt = record.get("averaging_qnt", 5)

        # 📊 Calculate fall/rise %
        diff_pct = round(((ltp - last_buy_price) / last_buy_price) * 100, 2)
        if diff_pct < 0:
            status = f"{RED}🔻 Fell{RESET}"
            diff = f"{RED}🔻 {diff_pct} {RESET}"
        else:
            status = f"{GREEN}🔼 Rose{RESET}"
            diff = f"{GREEN}🔻 {diff_pct} {RESET}"

        # status = "🔻 Fell" if diff_pct < 0 else "🔼 Rose"

        if diff_pct < -2 or diff_pct > 4 or True:
            # ❌ Do not update DB if no buy order triggered
            msg = (f"✅ {symbol.ljust(15)}:  \t LTP = {ltp}, \t Qnt = {qty} \t Last Buy = {last_buy_price} \t {status} = {diff}")
            print(msg)
            logging.info(msg)


        buy_qty = 0
        # Check if stock fell more than 5% from last buy price
        if ltp < last_buy_price * (1 - (averaging_fall/100)):
            buy_qty = max(1, int(qty * (averaging_qnt/100)))

        # Check if stock fell more than 5% from last buy price
        if ltp > last_buy_price * (1 + (averaging_rise/100)):
            buy_qty = max(1, int(qty * (averaging_qnt/100)/2))

        if buy_qty > 0:

            msg = f"{symbol}: {status} {round(diff_pct, 2)}% | " f"Buying {buy_qty}"
            logging.info(msg)
            print(msg)
            send_telegram_message(msg)

            order_id = 000

            # Place Buy Order
            order_id = kite.place_order(
                variety=kite.VARIETY_REGULAR,
                exchange=kite.EXCHANGE_NSE,
                tradingsymbol=symbol,
                transaction_type=kite.TRANSACTION_TYPE_BUY,
                quantity=buy_qty,
                order_type=kite.ORDER_TYPE_MARKET,
                product=kite.PRODUCT_CNC
            )

            # ✅ Update main fields + append log
            collection.update_one(
                {"tradingsymbol": symbol},
                {
                    "$set": {
                        "last_buy_price": ltp,
                        "ltp": ltp,
                        "quantity": qty + buy_qty,
                        "last_buy_qty": buy_qty,
                        "order_id": order_id,
                        "updated_at": datetime.datetime.now()
                    },
                    "$push": {
                        "order_logs": {
                            "order_id": order_id,
                            "buy_price": ltp,
                            "buy_qty": buy_qty,
                            "executed_at": datetime.datetime.now()
                        }
                    }
                }
            )

            add_new_order(symbol, buy_qty, ltp, 'PLACED', kite.TRANSACTION_TYPE_BUY)
        else:
            collection.update_one(
                {"tradingsymbol": symbol},
                {
                    "$set": {
                        "ltp": ltp,
                        "quantity": qty,
                        "updated_at": datetime.datetime.now()
                    }
                }
            )
    show_today_cnc_orders(kite)


def show_today_cnc_orders(kite):
    # Get all orders
    global today_cnc_orders

    if not today_cnc_orders:
        print("❌ No CNC orders placed today.")
        return

    print("\n📌 CNC Orders for Today:\n" + "-"*50)

    total_buy_value = 0
    for order in today_cnc_orders:
        print(
            f"🟢 Symbol: {order['tradingsymbol']:10} | \t"
            f"Qty: {order['quantity']:4} | \t"
            f"Price: ₹{order['average_price']:.2f} | \t"
            f"Status: {order['status']:10} | \t " 
            f"Type: {order['transaction_type']} | \t"
            f"Value: ₹{(order['average_price']*order['quantity']):.2f}"
        )
        if order['transaction_type'] == 'BUY':
            total_buy_value = total_buy_value + order['quantity'] * order['average_price']
    print(f"\nTotal Buy for the day : ₹{total_buy_value}")
    # print(f"Today Buy Value for Today: {total_buy_value::.2f}")

def load_collateral_data():
    url = "https://zerodha.com/margin/collateral.csv"
    headers = {"User-Agent": "Mozilla/5.0"}  # pretend like browser
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return pd.read_csv(StringIO(resp.text))

def fetch_today_orders(kite):
    """Fetch today's CNC orders once and store them locally."""
    global today_cnc_orders
    orders = kite.orders()

    today_cnc_orders = [
        {
            "tradingsymbol": o["tradingsymbol"],
            "quantity": o["quantity"],
            "average_price": round(o["average_price"], 2),
            "status": o["status"],
            "transaction_type": o["transaction_type"],
            "timestamp": o["order_timestamp"]
        }
        for o in orders
        if o["product"] == "CNC"
    ]

def add_new_order(symbol, qty, price, status, order_type):
    """Update local order list when a new CNC order is placed."""
    global today_cnc_orders
    today = datetime.date.today().strftime("%Y-%m-%d")

    new_order = {
        "tradingsymbol": symbol,
        "quantity": qty,
        "average_price": round(price, 2),
        "status": status,
        "transaction_type": order_type,
        "timestamp": f"{today} {datetime.datetime.now().strftime('%H:%M:%S')}"
    }

    today_cnc_orders.append(new_order)

def get_pledge_margin(kite):
    """
    Check UNPLEDGED holdings and calculate margin available if pledged.

    :param kite: KiteConnect instance (with access token set)
    :param collateral_csv_url: Zerodha collateral margin CSV link
    :return: dict with breakdown and total margin
    """

    # collateral_csv_url = "https://zerodha.com/margin/collateral.csv"
    #
    # # Load Zerodha haircut list
    # haircut_df = pd.read_csv(collateral_csv_url)
    haircut_df = load_collateral_data()
    haircut_map = dict(zip(haircut_df["Tradingsymbol"], haircut_df["Haircut%"]))

    # Fetch holdings
    holdings = kite.holdings()
    breakdown = []
    total_margin = 0

    for h in holdings:
        symbol = h["tradingsymbol"]
        qty = h["quantity"]
        pledged_qty = h.get("collateral_quantity", 0)  # already pledged
        unpledged_qty = qty - pledged_qty

        if unpledged_qty <= 0:
            breakdown.append({
                "symbol": symbol,
                "total_qty": qty,
                "unpledged_qty": 0,
                "ltp": None,
                "pledgeable": False,
                "haircut": None,
                "margin": 0
            })
            continue

        ltp = kite.ltp(f"NSE:{symbol}")[f"NSE:{symbol}"]["last_price"]

        haircut = haircut_map.get(symbol)
        if haircut is None:
            breakdown.append({
                "symbol": symbol,
                "total_qty": qty,
                "unpledged_qty": unpledged_qty,
                "ltp": ltp,
                "pledgeable": False,
                "haircut": None,
                "margin": 0
            })
            continue

        margin_value = unpledged_qty * ltp * (1 - (haircut / 100))
        total_margin += margin_value

        breakdown.append({
            "symbol": symbol,
            "total_qty": qty,
            "unpledged_qty": unpledged_qty,
            "ltp": ltp,
            "pledgeable": True,
            "haircut": haircut,
            "margin": round(margin_value, 2)
        })

    result = {"breakdown": breakdown, "total_margin": round(total_margin, 2)}

    print("📊 Pledge Margin Breakdown (Unpledged Only):")
    for r in result["breakdown"]:
        if r["pledgeable"]:
            print(f"✅ {r['symbol']:15} Total {r['total_qty']:5} | "
                  f"Unpledged {r['unpledged_qty']:5} | LTP {r['ltp']:8.2f} | "
                  f"Haircut {r['haircut']}% | Margin {r['margin']:10.2f}")
        else:
            print(f"❌ {r['symbol']:15} Total {r['total_qty']:5} | Unpledged {r['unpledged_qty']:5} | Not pledgeable")

    print(f"\n💰 Total Margin Available (from unpledged shares): {result['total_margin']:.2f}")


