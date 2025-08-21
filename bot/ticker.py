import time
import sys
import os
import datetime

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bot.trading_alerts import send_telegram_message
from bot.services.kite_service import get_kite_ticker, get_kite_client
from bot.trade_logic import reset_option_short_orders

last_processed_time = 0
interval_seconds = 1
ltp_dict = {}

exchange = 'NFO'

kite = get_kite_client()
kws = get_kite_ticker()

# Cached positions to avoid calling kite.positions() repeatedly
position_cache = {}
pos_dict = {}  # To store trailing targets
all_orders = {}

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
    position_cache.clear()
    open_positions = fetch_open_positions()
    position_cache = {pos['instrument_token']: pos for pos in open_positions}
    return list(position_cache.keys())

def reset_current_data(kite, ws):
    reset_option_short_orders(kite)
    global ltp_dict, pos_dict, all_orders
    ltp_dict.clear()
    pos_dict.clear()
    all_orders.clear()
    tokens = update_position_cache()
    all_orders = kite.orders()
    if tokens:
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_LTP, tokens)

def on_ticks(ws, ticks):
    global last_processed_time, ltp_dict, pos_dict, all_orders
    stop_loss = -5000
    trail_trigger = 4000
    trail_gap = 500

    now = time.time()
    # if now - last_processed_time < interval_seconds:
        # return
    if now - last_processed_time < 60:
        update_position_cache()

    clear_console()

    total_pnl = 0
    total_day_pnl = 0
    premium = 0

    for tick in ticks:
        token = tick['instrument_token']
        ltp = tick['last_price']
        ltp_dict[token] = ltp  # Always update latest price

    print(f"\nTime : {datetime.datetime.now()}")

    for token, pos in position_cache.items():
        ltp = ltp_dict.get(token)
        if ltp is None:
            continue

        matching_orders = [o for o in all_orders if o['tradingsymbol'] == pos['tradingsymbol'] and o['transaction_type'] == 'SELL']
        matching_orders.sort(key=lambda x: x['order_timestamp'])
        latest_order = matching_orders[-1] if matching_orders else None

        average_price = latest_order['average_price'] if latest_order else pos['average_price']

        symbol = pos['exchange'] + ':' + pos['tradingsymbol']
        # pos_dict['NFO:NIFTY25AUG24650PE'] = {'trail': pos_dict.get(symbol, {}).get('trail', stop_loss)}

        pnl = (ltp - average_price) * pos['quantity']
        unrealised = pnl
        quantity = abs(pos['quantity'])

        transaction = kite.TRANSACTION_TYPE_BUY if pos['quantity'] < 0 else kite.TRANSACTION_TYPE_SELL
        total_pnl += pnl
        premium += ltp * quantity
        symbol_sl = pos_dict.get(symbol, {}).get('trail', stop_loss)
        pos_dict[symbol] = {'trail': pos_dict.get(symbol, {}).get('trail', pos['quantity'] * average_price * 0.2)}
        # print(f"{symbol} PnL → {pnl_color}{int(pnl)}\033[0m")
        color = "\033[92m" if pnl > 0 else "\033[91m"
        print(f"{pos['tradingsymbol']} - \tQty: {pos['quantity']}\t  Avg: {average_price:.2f} \t \t LTP: {ltp} \t \tP&L: {color}{int(pnl)}\033[0m \t SL: {pos_dict.get(symbol, {}).get('trail', '')}")

        # Check if existing SL order is complete
        if symbol in pos_dict and pos_dict[symbol].get('order_id'):
            order_id = pos_dict[symbol]['order_id']
            order_info = next((o for o in all_orders if o['order_id'] == order_id), None)
            if order_info:
                if order_info['status'] in ['COMPLETE', 'CANCELLED', 'REJECTED']:
                    print(f"ℹ️ Previous SL order for {symbol} was {order_info['status']}. Resetting tracking.")
                    send_telegram_message(f"ℹ️ SL order for {symbol} marked as {order_info['status']}")
                    pos_dict.pop(symbol)
                    # continue  # Skip current loop; wait for next tick
                else:
                    print(f"⏳ SL order for {symbol} is still OPEN.")
                    # Don't place or modify again while it's open
                    # continue

        # 🔴 Stop-Loss Hit
        if unrealised < symbol_sl:
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
                # reset_current_data(kite, ws)
            except Exception as e:
                print(f"❌ Error placing order for {symbol}: {e}")
                # continue

        # 🟢 Trailing Target Logic
        if unrealised > trail_trigger:
            # First time hitting trail level
            if symbol not in pos_dict:
                trail_level = int(unrealised - trail_gap)
                pos_dict[symbol] = {'trail': trail_level}
                print(f"📈 {symbol} hit ₹{trail_trigger} profit. Setting SL at ₹{trail_level}.")
                send_telegram_message(f"📈 {symbol} profit > ₹{trail_trigger}. Setting SL at ₹{trail_level}. LTP: ({unrealised})")
            else:
                prev_trail = pos_dict[symbol]['trail']
                if unrealised > (prev_trail + trail_gap):
                    # Raise trailing level
                    new_trail = int(unrealised - trail_gap)
                    print(f"🔄 {symbol} trailing target raised from ₹{prev_trail} to ₹{new_trail}.")
                    # send_telegram_message(f"🔄 Trailing target for {symbol} raised to ₹{new_trail}. LTP: ({unrealised})")
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
                        # reset_current_data(kite, ws)
                    except Exception as e:
                        print(f"❌ Error placing order for {symbol}: {e}")
                        # continue

    print(f"_________________________________________________________________________________________________")
    total_color = "\033[92m" if total_pnl > 0 else "\033[91m"
    print(f"Maximum Possible Profit: \033[93m{int(premium)}\033[0m \t \t \t \t 💰 Total P&L: {total_color}{int(total_pnl)}\033[0m \t ")

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


