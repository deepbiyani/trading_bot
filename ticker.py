from kiteconnect import KiteConnect, KiteTicker
from trading_alerts import send_telegram_message
from trade_logic import reset_option_short_orders
import time
import datetime

api_key = "znvfi82o9j4dtoe9"
api_secret = "nocxr69ubpk26oz7gqluix8jurl9erd7"
access_token = "imrys79h8PFGIvwoc9AxFF0aAPnAtG25"

last_processed_time = 0
interval_seconds = 15
ltp_dict = {}

exchange = 'NFO'

kite = KiteConnect(api_key=api_key)
kite.set_access_token(access_token)

kws = KiteTicker(api_key, access_token)

# Cached positions to avoid calling kite.positions() repeatedly
position_cache = {}
pos_dict = {}  # To store trailing targets

import os

def clear_console():
    os.system('cls' if os.name == 'nt' else 'clear')

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
    stop_loss = -8000
    trail_trigger = 5000
    trail_gap = 500

    now = time.time()
    if now - last_processed_time < interval_seconds:
        return
    # clear_console()

    total_pnl = 0
    premium = 0

    for tick in ticks:
        token = tick['instrument_token']
        ltp = tick['last_price']
        ltp_dict[token] = ltp  # Always update latest price

    print(f"\nTime : {datetime.datetime.now()}")

    for token, pos in position_cache.items():

        ltp = ltp_dict.get(token)
        if ltp is None:
            print("None")
            continue

        all_orders = kite.orders()

        pnl = (ltp - pos['average_price']) * pos['quantity']

        symbol = pos['exchange'] + ':' + pos['tradingsymbol']
        unrealised = pos['unrealised']
        # unrealised = pnl
        quantity = abs(pos['quantity'])

        transaction = kite.TRANSACTION_TYPE_BUY if pos['quantity'] < 0 else kite.TRANSACTION_TYPE_SELL

        total_pnl += pnl
        premium += ltp * quantity

        # print(f"{symbol} PnL → {pnl_color}{int(pnl)}\033[0m")
        color = "\033[92m" if pnl > 0 else "\033[91m"
        print(f"{pos['tradingsymbol']} -\tQty: {pos['quantity']}\t  Avg: {pos['average_price']} \t \t LTP: {ltp} \t \tP&L: {color}{int(pnl)}\033[0m \t SL: {pos_dict[symbol] if symbol in pos_dict else None}")

        # Check if existing SL order is complete
        if symbol in pos_dict and pos_dict[symbol].get('order_id'):
            order_id = pos_dict[symbol]['order_id']
            order_info = next((o for o in all_orders if o['order_id'] == order_id), None)
            if order_info:
                if order_info['status'] in ['COMPLETE', 'CANCELLED', 'REJECTED']:
                    print(f"ℹ️ Previous SL order for {symbol} was {order_info['status']}. Resetting tracking.")
                    send_telegram_message(f"ℹ️ SL order for {symbol} marked as {order_info['status']}")
                    pos_dict.pop(symbol)
                    continue  # Skip current loop; wait for next tick
                else:
                    print(f"⏳ SL order for {symbol} is still OPEN.")
                    # Don't place or modify again while it's open
                    continue

        # 🔴 Stop-Loss Hit
        if unrealised < stop_loss:
            print(f"🚨 Stop-Loss hit for {symbol}. Exiting position...")
            send_telegram_message(f"🚨 Stop-Loss hit for {symbol}. Exiting position...")
            try:
                order_id = kite.place_order(
                    variety=kite.VARIETY_REGULAR,
                    exchange=pos['exchange'],
                    tradingsymbol=pos['tradingsymbol'],
                    transaction_type=transaction,
                    quantity=quantity,
                    order_type=kite.ORDER_TYPE_MARKET,
                    product=kite.PRODUCT_NRML
                )
                # order_id = 000
                pos_dict[symbol] = {"orders": [order_id]}
                print(f"✅ Exit order placed for {symbol} | Order ID: {order_id}")
                send_telegram_message(f"✅ Exit order placed for {symbol} | Order ID: {order_id}")
                reset_option_short_orders(kite)
            except Exception as e:
                print(f"❌ Error placing order for {symbol}: {e}")
                continue

        # 🟢 Trailing Target Logic
        if unrealised > trail_trigger:
            # First time hitting trail level
            if symbol not in pos_dict:
                trail_level = unrealised - trail_gap
                pos_dict[symbol] = {'trail': trail_level}
                print(f"📈 {symbol} hit ₹{trail_trigger} profit. Setting SL at ₹{trail_level}.")
                send_telegram_message(f"📈 {symbol} profit > ₹{trail_trigger}. Setting SL at ₹{trail_level}. LTP: ({unrealised})")
            else:
                prev_trail = pos_dict[symbol]['trail']
                # print(f"📈 {symbol} => unreleased: {unrealised} \t prev trail: ₹{prev_trail} \t trail gap:  {trail_gap}")
                if unrealised > (prev_trail + trail_gap):
                    # Raise trailing level
                    new_trail = unrealised - trail_gap
                    print(f"🔄 {symbol} trailing target raised from ₹{prev_trail} to ₹{new_trail}.")
                    send_telegram_message(f"🔄 Trailing target for {symbol} raised to ₹{new_trail}. LTP: ({unrealised})")
                    pos_dict[symbol]['trail'] = new_trail
                elif unrealised < prev_trail:
                    # 🔚 Trail level breached: exit
                    print(f"🚪 {symbol} breached trailing target (₹{prev_trail}). Exiting...")
                    send_telegram_message(f"🚪 {symbol} trailing SL hit. Exiting position at ₹{unrealised}.")
                    try:
                        order_id = kite.place_order(
                            variety=kite.VARIETY_REGULAR,
                            exchange=pos['exchange'],
                            tradingsymbol=pos['tradingsymbol'],
                            transaction_type=transaction,
                            quantity=quantity,
                            order_type=kite.ORDER_TYPE_MARKET,
                            product=kite.PRODUCT_NRML
                        )
                        pos_dict[symbol]['orders'] = [order_id]
                        print(f"✅ Exit order placed for {symbol} | Order ID: {order_id}")
                        reset_option_short_orders(kite)
                    except Exception as e:
                        print(f"❌ Error placing order for {symbol}: {e}")
                        continue

    print(f"_________________________________________________________________________________________________")
    total_color = "\033[92m" if total_pnl > 0 else "\033[91m"
    print(f"Maximum Possible Profit: \033[93m{int(premium)}\033[0m \t \t \t \t 💰 Total P&L: {total_color}{int(total_pnl)}\033[0m \t ")

    last_processed_time = now

def on_ticks_old(ws, ticks):
    global last_processed_time, ltp_dict

    now = time.time()
    if now - last_processed_time < interval_seconds:
        return

    total_pnl = 0

    for tick in ticks:
        token = tick['instrument_token']
        ltp = tick['last_price']
        ltp_dict[token] = ltp  # Always update latest price

    print(f"Time : {datetime.datetime.now()}")

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


