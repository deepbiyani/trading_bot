from trading_alerts import send_telegram_message
import time
from kite_client import get_kite_ticker, get_kite_client

last_processed_time = 0
interval_seconds = 15
ltp_dict = {}

exchange = 'NFO'

kite = get_kite_client()
kws = get_kite_ticker()

# Cached positions to avoid calling kite.positions() repeatedly
position_cache = {}

def fetch_open_positions():
    try:
        positions = kite.positions().get("net", [])
        return [pos for pos in positions if pos['exchange'] == exchange and pos['quantity'] != 0]
    except Exception as e:
        print(f"❌ Error fetching positions: {e}")
        return []

def update_position_cache():
    global position_cache
    open_positions = fetch_open_positions()
    position_cache = {pos['instrument_token']: pos for pos in open_positions}
    return list(position_cache.keys())

def on_ticks(ws, ticks):
    global last_processed_time, ltp_dict

    now = time.time()
    if now - last_processed_time < interval_seconds:
        return

    total_pnl = 0

    for tick in ticks:
        token = tick['instrument_token']
        ltp = tick['last_price']
        ltp_dict[token] = ltp  # Always update latest price

    for token, pos in position_cache.items():
        ltp = ltp_dict.get(token)
        if ltp is None:
            continue

        pnl = (ltp - pos['average_price']) * pos['quantity']
        color = "\033[92m" if pnl > 0 else "\033[91m"
        print(f"{pos['tradingsymbol']} -\tQty: {pos['quantity']}\t  Avg: {pos['average_price']} \tLTP: {ltp}\tP&L: {color}{int(pnl)}\033[0m")

        if pnl > 7000:
            send_telegram_message(f"{pos['tradingsymbol']} profit crossed 7000. Current P&L → {int(pnl)}")
        total_pnl += pnl

    total_color = "\033[92m" if total_pnl > 0 else "\033[91m"
    print(f"Total P&L → {total_color}{int(total_pnl)}\033[0m")

    last_processed_time = now

def on_connect(ws, response):
    print("✅ Connected to WebSocket.")
    tokens = update_position_cache()
    print("📡 Subscribing to tokens:", tokens)

    if tokens:
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_LTP, tokens)

def on_close(ws, code, reason):
    print(f"❌ WebSocket closed. Code: {code}, Reason: {reason}")

def on_error(ws, code, reason):
    print(f"❌ WebSocket error. Code: {code}, Reason: {reason}")

def on_noreconnect(ws):
    print("🚫 WebSocket won't reconnect.")

# Assign WebSocket handlers
kws.on_ticks = on_ticks
kws.on_connect = on_connect
kws.on_close = on_close
kws.on_error = on_error
kws.on_noreconnect = on_noreconnect

try:
    print("🔌 Connecting to WebSocket...")
    kws.connect(threaded=True)
except Exception as e:
    print(f"❌ Failed to connect to WebSocket: {e}")

# Keep the main thread alive
while True:
    time.sleep(1)


