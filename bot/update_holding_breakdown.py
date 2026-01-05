from pymongo import MongoClient
from datetime import datetime
from dateutil import parser

# -------------------------
# Mongo Setup
# -------------------------
client = MongoClient("mongodb://localhost:27017")
db = client["trading"]
collection = db["positions_v2"]

# Fresh collection
collection.drop()

collection.create_index("tradingsymbol", unique=True)
collection.create_index("order_logs.order_id", unique=True, sparse=True)

# -------------------------
# Load holdings from Kite
# -------------------------
holding_cached = kite.holdings()

# Convert holdings to dict for fast lookup
holdings_map = {
    h["tradingsymbol"]: h for h in holding_cached
}

# -------------------------
# This will be PROVIDED by you per stock
# -------------------------
# trades_by_symbol = {
#   "BAJAJFINSV": [ {...}, {...} ],
#   "INFY": [ {...} ]
# }

# -------------------------
# Process each stock
# -------------------------
for symbol, trades in trades_by_symbol.items():

    if symbol not in holdings_map:
        print(f"Skipping {symbol} (not in holdings)")
        continue

    holding = holdings_map[symbol]
    holding_qty = holding["quantity"]

    # ---- Deduplicate orders ----
    seen_orders = set()
    order_logs = []

    # Sort trades oldest → newest
    trades.sort(
        key=lambda x: parser.isoparse(x["order_execution_time"])
    )

    for trade in trades:
        oid = trade["order_id"]

        if oid in seen_orders:
            continue

        seen_orders.add(oid)

        order_logs.append({
            "order_id": oid,
            "trade_id": trade["trade_id"],
            "price": trade["price"],
            "qty": trade["quantity"],
            "exchange": trade["exchange"],
            "trade_type": trade["trade_type"],
            "executed_at": parser.isoparse(trade["order_execution_time"])
        })

    if not order_logs:
        continue

    last_trade = order_logs[-1]

    # ---- Insert fresh document ----
    doc = {
        "tradingsymbol": symbol,
        "last_buy_price": last_trade["price"],
        "ltp": last_trade["price"],
        "quantity": holding_qty,          # 🔥 from Kite holdings
        "last_buy_qty": last_trade["qty"],
        "averaging_rise": 5,
        "averaging_fall": 5,
        "averaging_qnt": 5,
        "order_id": last_trade["order_id"],
        "order_logs": order_logs,
        "updated_at": datetime.now()
    }

    collection.insert_one(doc)

    print(f"Inserted {symbol} | Qty: {holding_qty} | Orders: {len(order_logs)}")

print("✅ Migration completed successfully")
