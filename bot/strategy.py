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

def check_and_average(kite):
    holdings = kite.holdings()

    # -------- MongoDB Setup --------
    client = MongoClient("mongodb://localhost:27017/")
    db = client["trade_bot"]
    collection = db["averaging_trades"]

    #Clear Console
    os.system('cls' if os.name == 'nt' else 'clear')
    print(f"\nTime : {datetime.datetime.now()}")

    for stock in holdings:
        print(stock)
        exit()
        symbol = stock["tradingsymbol"]
        qty = int(stock["quantity"])

        # ✅ Skip unwanted symbols
        if symbol.upper().startswith("SGB"):
            # print(f"⏩ Skipping {symbol} (dummy symbol)")
            continue

        # Get LTP
        ltp_data = kite.ltp(f"NSE:{symbol}")
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
                "averaging_fall": 5,
                "averaging_qnt": 5,
                "order_id": None,
                "order_logs": [],   # ✅ New field to store order history
                "updated_at": datetime.datetime.now()
            })
            continue

        last_buy_price = record["last_buy_price"]
        averaging_fall = record.get("averaging_fall", 5)
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

        # Check if stock fell more than 5% from last buy price
        if ltp < last_buy_price * (1 - (averaging_fall/100)):
            buy_qty = max(1, int(qty * (averaging_qnt/100)))  # 5% of current holding

            msg = f"🔻 {symbol}: Fell {round(((last_buy_price - ltp) / last_buy_price) * 100, 2)}% | " f"Buying {buy_qty}"
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
        else:
            if diff_pct < -1:
                # ❌ Do not update DB if no buy order triggered
                msg = (f"✅ {symbol.ljust(15)}:  \t LTP = {ltp}, \t Last Buy = {last_buy_price} \t {status} = {diff}")
                print(msg)
                logging.info(msg)

def load_collateral_data():
    url = "https://zerodha.com/margin/collateral.csv"
    headers = {"User-Agent": "Mozilla/5.0"}  # pretend like browser
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return pd.read_csv(StringIO(resp.text))

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

