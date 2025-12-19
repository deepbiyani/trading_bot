import time
import sys
import os
import datetime

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bot.trading_alerts import send_telegram_message
from bot.services.kite_service import get_kite_ticker, get_kite_client
from bot.trade_logic import reset_option_short_orders
from bot.services.trade_service import calculate_charges
from bot.helpers.trade_helper import fetch_day_low

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
day_low_dict = {}

# Risk/Reward parameters (tune as per strategy)
risk_pct = 5      # 40% capital risk (stop loss)
reward_pct = 0.4    # Trail starts after 80% profit
trail_pct = 0.2    # 10% trail gap

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

def swap_positions(kite, sl_hit):

    positions = kite.positions().get("net", [])
    positions = [pos['tradingsymbol'] for pos in positions if pos['exchange'] == 'NFO' and pos['quantity'] != 0]

    calls = {}
    puts = {}
    res = {}

    for sym in positions:
        # strike is the numeric part before CE/PE
        if sym.endswith("CE"):
            strike = int(sym.replace("CE", "")[-5:])  # last 5 chars before CE
            calls[strike] = sym
        elif sym.endswith("PE"):
            strike = int(sym.replace("PE", "")[-5:])
            puts[strike] = sym

    call_strikes = sorted(calls.keys())
    put_strikes = sorted(puts.keys(), reverse=True)

    quote_key = "NSE:NIFTY 50"
    nifty_ltp = kite.quote([quote_key])[quote_key]['last_price']
    # atm_strike = math.ceil((nifty_ltp / 50)) * 50
    step = 250

    if sl_hit.endswith("CE"):
        # strike = (sl_hit.replace("CE", "")[-5:])
        call_strike = int(call_strikes[-1]) + step
        put_strike = int(put_strikes[0])
        res.setdefault("sell", []).append(sl_hit[0:10]+ str(call_strike) + "CE")
        res.setdefault("sell", []).append(sl_hit[0:10]+ str(put_strike + step) + "PE")
        res.setdefault("buy", []).append(sl_hit[0:10] + str(put_strikes[-1]) + "PE")
    elif sl_hit.endswith("PE"):
        # strike = (sl_hit.replace("PE", "")[-5:])
        put_strike = int(put_strikes[-1] - step)
        call_strike = int(call_strikes[0])
        res.setdefault("sell", []).append(sl_hit[0:10]+ str(put_strike) + "PE")
        res.setdefault("sell", []).append(sl_hit[0:10]+ str(call_strike - step) + "CE")
        res.setdefault("buy", []).append(sl_hit[0:10] + str(call_strikes[-1]) + "CE")

    return {
        "positions_to_take": res['buy'],
        "positions_to_clear": res['sell']
    }

def swap_and_refresh(kite, ws, cleared_symbol):
    swap_positions(kite, cleared_symbol)
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

    now = time.time()
    # if now - last_processed_time < interval_seconds:
        # return
    if now - last_processed_time < 60:
        update_position_cache()

    clear_console()

    total_pnl = 0
    min_pnl = 0
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

        # Dynamic levels per symbol
        position_value = average_price * abs(pos['quantity'])
        pnl = (ltp - average_price) * pos['quantity']
        quantity = abs(pos['quantity'])

        transaction = kite.TRANSACTION_TYPE_BUY if pos['quantity'] < 0 else kite.TRANSACTION_TYPE_SELL
        total_pnl += pnl
        premium += ltp * quantity

        if token not in day_low_dict:
            try:
                day_low = fetch_day_low(kite, token)
                day_low_dict[token] = day_low if day_low else average_price
                print(f"📉 Day Low fetched for {pos['tradingsymbol']}: {day_low_dict[token]}")
            except Exception as e:
                print(f"❌ Failed to fetch day low for {pos['tradingsymbol']}: {e}")
                day_low_dict[token] = average_price

        symbol = pos['exchange'] + ':' + pos['tradingsymbol']
        quantity = abs(pos['quantity'])
        pnl = (ltp - average_price) * pos['quantity']
        unrealised = pnl

        day_low = day_low_dict.get(token, average_price)

        # ================================
        # 🔴 BASE STOP LOSS (NEW LOGIC)
        # SL = 2 × min(day low, avg price)
        # ================================
        ref_price = min(day_low, average_price)
        sl_price = ref_price * 2
        base_sl = (sl_price - average_price) * pos['quantity']

        # ================================
        # 🟢 TRAILING LOGIC (UNCHANGED)
        # ================================
        trail_trigger = position_value * reward_pct
        trail_gap = ltp * quantity * trail_pct

        # Init pos_dict safely
        if symbol not in pos_dict:
            pos_dict[symbol] = {
                "base_sl": base_sl,
                "trail_sl": None,
                "active_sl": base_sl,
                "ref_price": ref_price,
                "day_low": day_low,
            }
        else:
            pos_dict[symbol]["base_sl"] = base_sl

        # Activate tightest SL
        trail_sl = pos_dict[symbol].get("trail_sl")
        active_sl = max(base_sl, trail_sl) if trail_sl else base_sl
        pos_dict[symbol]["active_sl"] = active_sl
        buyCharge = calculate_charges("SELL", qty=abs(pos['quantity']), price=average_price, product="NRML")
        sellCharge = calculate_charges("BUY", qty=abs(pos['quantity']), price=ltp, product="NRML")
        transaction_charge = buyCharge['Total Charges'] + sellCharge['Total Charges']
        color = "\033[92m" if pnl > 0 else "\033[91m"
        print(f"{pos['tradingsymbol']} - \t Qty: {pos['quantity']}\t Avg: {average_price:.2f} \t LTP: {ltp} \t P&L: {color}{int(pnl)}\033[0m \t SL: {int(pos_dict[symbol]['active_sl'])} \t Charges: {transaction_charge:.2f}")
        # print(f"{pos['tradingsymbol']} - \t SL: {stop_loss:.2f} \t Trail Trigger: {trail_trigger:.2f} \t Trail gap: {trail_gap:.2f}")
        min_pnl += pos_dict[symbol]["active_sl"]
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
        # print(pos_dict[symbol])

        # 🔴 Stop-Loss Hit
        if unrealised < pos_dict[symbol]["active_sl"]:
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
                # swap_and_refresh(kite, ws, pos['tradingsymbol'])
            except Exception as e:
                print(f"❌ Error placing order for {symbol}: {e}")
                # continue

        # 🟢 Trailing Target Logic
        if unrealised > trail_trigger:
            if pos_dict[symbol]["trail_sl"] is None:
                trail_level = int(unrealised - trail_gap)
                pos_dict[symbol]["trail_sl"] = trail_level
                print(f"📈 {symbol} trail activated at ₹{trail_level}")
                send_telegram_message(
                    f"📈 {symbol} trailing SL set at ₹{trail_level} | PnL: {int(unrealised)}"
                )
            else:
                prev_trail = pos_dict[symbol]["trail_sl"]
                if unrealised > (prev_trail + trail_gap):
                    new_trail = int(unrealised - trail_gap)
                    pos_dict[symbol]["trail_sl"] = new_trail
                    print(f"🔄 {symbol} trailing SL moved to ₹{new_trail}")
                elif unrealised < prev_trail:
                    # 🔚 Trail level breached: exit
                    print(f"🚪 {symbol} breached trailing target (₹{prev_trail}). Exiting...")
                    send_telegram_message(f"🚪 {symbol} trailing SL hit. Exiting position at ₹{unrealised}.")
                    try:
                        order_id = 0000
                        # order_id = kite.place_order(
                        #     variety=kite.VARIETY_REGULAR,
                        #     exchange=pos['exchange'],
                        #     tradingsymbol=pos['tradingsymbol'],
                        #     transaction_type=transaction,
                        #     quantity=quantity,
                        #     order_type=kite.ORDER_TYPE_MARKET,
                        #     product=kite.PRODUCT_NRML
                        # )
                        pos_dict[symbol]['orders'] = [order_id]
                        print(f"✅ Exit order placed for {symbol} | Order ID: {order_id}")
                        # reset_current_data(kite, ws)
                        # swap_and_refresh(kite, ws, pos['tradingsymbol'])
                    except Exception as e:
                        print(f"❌ Error placing order for {symbol}: {e}")
                        # continue

    print(f"_________________________________________________________________________________________________")
    total_color = "\033[92m" if total_pnl > 0 else "\033[91m"
    min_pnl_color = "\033[92m" if min_pnl > 0 else "\033[91m"
    print(f"Max Profit: \033[93m{int(premium)}\033[0m \t \t Min Profit {min_pnl_color}{int(min_pnl)}\033[0m \t 💰 Total P&L: {total_color}{int(total_pnl)}\033[0m \t ")

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


