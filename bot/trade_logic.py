import datetime
import time
from trading_alerts import send_telegram_message
import math

def check_sl_on_open_positions(kite, stop_loss = -10000, exchange = 'NFO'):

    now = datetime.datetime.now().time()
    cutoff = datetime.time(15, 30) if exchange == 'MCX' else datetime.time(15, 30)

    while now < cutoff:

        positions = kite.positions()
        net_positions = positions.get("net", [])
        net_positions = [
            pos for pos in net_positions
            if pos['exchange'] == 'NFO'
        ]
        topLoss = 0
        totalPnl = 0

        for pos in net_positions:
            symbol = pos['exchange'] + ':' + pos['tradingsymbol']
            totalPnl = totalPnl + pos['pnl']

            if pos['exchange'] == 'NFO' and pos['quantity'] != 0:
                topLoss = pos['pnl'] if pos['pnl'] < topLoss else topLoss
                color = "\033[92m" if pos['pnl'] > 0 else "\033[91m"  # Green if profit, Red if loss
                print(f"{symbol}    \tQty:{pos['quantity']} \tAvg: {pos['average_price']}   \tP&L: {color}{pos['pnl']}\033[0m")

                if pos['unrealised'] < stop_loss:
                    quantity = -pos['quantity']
                    transaction = kite.TRANSACTION_TYPE_BUY if pos['quantity'] < 0 else kite.TRANSACTION_TYPE_SELL
                    print(f"Stop-Loss hit for {symbol} → Exiting position.")
                    send_telegram_message(f"Stop-Loss hit for {symbol} → Exiting position.")
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

                        # order_id = 0000

                        print(f"✅ Exit order placed for {symbol} | Order ID: {order_id}")
                        send_telegram_message(f"✅ Exit order placed for {symbol} | Order ID: {order_id}")

                        reset_option_short_orders(kite)
                    except Exception as e:
                        print(f"❌ Error placing order {symbol}: {e}")
                        continue
        color = "\033[92m" if totalPnl > 0 else "\033[91m"  # Green if profit, Red if loss
        print(f"Total P&L: {color}{int(totalPnl)}\033[0m")

        time.sleep(30)
        print()

    print(f"⏰ It's past 11:30 PM. {exchange} Market closed...")

def get_expected_positions(kite, step=250, count=4):
    # Get NIFTY 50 spot price
    quote_key = "NSE:NIFTY 50"
    try:
        nifty_ltp = kite.quote([quote_key])[quote_key]['last_price']
    except Exception as e:
        print(f"❌ Error fetching NIFTY quote: {e}")
        return []

    vix_data = calculate_daily_from_vix(kite, nifty_ltp)

    step = int(vix_data['daily_points'] / 50) * 100

    # Round up to next 250 strike
    spot_price = ((int(nifty_ltp / step) + 1) * step)
    print(f"{quote_key} => {nifty_ltp}")
    print(f"🔍 OTM Spot => {spot_price}")

    # Generate strike prices
    otm_calls = [spot_price + step * i for i in range(0, count)]
    otm_puts = [spot_price - step * i for i in range(1, count + 1)]

    # Format expiry as YYMON (e.g., 25AUG)
    expiry = (datetime.datetime.today() + relativedelta(days=8)).strftime('%y%b').upper()
    # expiry ="25814" #for 07 AUG 2025 weekly expirty

    # Generate option symbols
    call_symbols = [f"NIFTY{expiry}{strike}CE" for strike in otm_calls]
    put_symbols = [f"NIFTY{expiry}{strike}PE" for strike in otm_puts]

    expected_positions = call_symbols + put_symbols
    return expected_positions

def analyze_positions(kite):
    # Get positions from Kite
    positions = kite.positions()
    net_positions = positions.get("net", [])

    # Filter NFO positions with non-zero quantity
    current_positions = [
        pos['tradingsymbol']
        for pos in net_positions
        if pos['exchange'] == 'NFO' and pos['quantity'] != 0
    ]

    expected_positions = get_expected_positions(kite)
    # expected_positions = get_expected_positions_by_premium(kite)
    # Determine positions to take and clear
    position_to_take = [opt for opt in expected_positions if opt not in current_positions]
    position_to_clear = [opt for opt in current_positions if opt not in expected_positions]

    print("✅ Expected positions:")
    print(expected_positions)
    print("\n📌 Current positions:")
    print(current_positions)
    print("\n➕ Position(s) to ADD:")
    print(position_to_take)
    print("\n❌ Position(s) to EXIT:")
    print(position_to_clear)

    # Calculate total premium of expected positions
    total_premium = 0

    for pos in expected_positions:
        try:
            quote = kite.quote(f"NFO:{pos}")
            data = quote.get(f"NFO:{pos}", {})
            last_price = data.get('last_price')
            last_quantity = data.get('last_quantity')
            if last_price is not None and last_quantity is not None:
                total_premium += last_price * 75
                print(f"{pos}:   \tltp: {last_price}")
            else:
                print(f"⚠️ Quote data missing for {pos}")
        except Exception as e:
            print(f"❌ Error fetching quote for {pos}: {e}")

    print(f"\n💰 Total Premium: {total_premium:.2f}")

    return {
        "positions_to_take": position_to_take,
        "positions_to_clear": position_to_clear
    }

def reset_option_short_orders(kite, lot_size=75):
    """
    Executes exit and entry orders for options.

    :param kite: Kite Connect API object
    :param lot_size: Quantity per order (e.g., 75 for NIFTY)
    """

    position_diff = analyze_positions(kite)

    # Exit positions (BUY to cover)
    for symbol in position_diff.get("positions_to_clear", []):
        try:
            order_id = kite.place_order(
                variety=kite.VARIETY_REGULAR,
                exchange="NFO",
                tradingsymbol=symbol,
                transaction_type=kite.TRANSACTION_TYPE_BUY,
                quantity=lot_size,
                product=kite.PRODUCT_NRML,
                order_type=kite.ORDER_TYPE_MARKET
            )
            print(f"✅ Exit order placed (BUY): {symbol} | Order ID: {order_id}")
            send_telegram_message(f"✅ Exit order placed (BUY): {symbol} | Order ID: {order_id}")
        except Exception as e:
            print(f"❌ Failed to place BUY order for {symbol}: {e}")
            send_telegram_message(f"❌ Failed to place BUY order for {symbol}: {e}")

    # Enter new positions (SELL to short OTM)
    for symbol in position_diff.get("positions_to_take", []):
        try:
            order_id = kite.place_order(
                variety=kite.VARIETY_REGULAR,
                exchange="NFO",
                tradingsymbol=symbol,
                transaction_type=kite.TRANSACTION_TYPE_SELL,
                quantity=lot_size,
                product=kite.PRODUCT_NRML,
                order_type=kite.ORDER_TYPE_MARKET
            )
            print(f"✅ Entry order placed (SELL): {symbol} | Order ID: {order_id}")
            send_telegram_message(f"✅ Entry order placed (SELL): {symbol} | Order ID: {order_id}")
        except Exception as e:
            print(f"❌ Failed to place SELL order for {symbol}: {e}")
            send_telegram_message(f"❌ Failed to place SELL order for {symbol}: {e}")

def trail_target_and_exit(kite, exchange='MCX', trail_buffer=10, sl_gap=100, minimum_profit_cap=3900):
    """
    Trails SL and exits short positions when SL is hit.
    """
    now = datetime.datetime.now().time()
    cutoff = datetime.time(23, 31) if exchange == 'MCX' else datetime.time(15, 30)

    stop_losses = {}         # Symbol-wise stop loss
    orders_dict = {}         # Symbol-wise order IDs

    # Cancel any open orders first
    for order in filter(lambda o: o['status'] == 'OPEN' and o['exchange'] == 'MCX', kite.orders()):
        try:
            kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order['order_id'])
        except Exception as e:
            print(f"⚠️ Error cancelling order {order['order_id']}: {e}")

    oldPnl = 0

    while now < cutoff:
        now = datetime.datetime.now().time()

        try:
            net_positions = kite.positions().get("net", [])
        except Exception as e:
            print(f"❌ Failed to fetch positions: {e}")
            time.sleep(15)
            continue

        open_positions = [
            pos for pos in net_positions
            if pos['exchange'] == exchange and pos['quantity'] != 0
        ]

        for pos in open_positions:
            symbol = pos['tradingsymbol']
            quote_symbol = f"{pos['exchange']}:{symbol}"
            product = pos['product']
            exchange = pos['exchange']
            quantity = abs(pos['quantity'])
            avg_price = pos['average_price']

            try:
                ltp = kite.quote(quote_symbol)[quote_symbol]['last_price']
            except Exception as e:
                print(f"❌ Error fetching quote for {symbol}: {e}")
                continue

            pnl = (ltp - avg_price) * pos.get('multiplier', 1)

            if oldPnl != pnl:
                oldPnl = pnl
                if pnl <= 0:
                    print(f" PnL: \033[91m{int(pnl)}:\033[0m")
                else:
                    print(f" PnL: \033[92m{int(pnl)}:\033[0m")

            if pnl <= minimum_profit_cap:
                continue


            sl = stop_losses.get(symbol)
            if sl is None:
                sl = ltp - sl_gap
                stop_losses[symbol] = sl

            print(f"LTP => {ltp} \t Stop Loss => {sl}" )

            # Trail SL
            if ltp > sl + sl_gap + trail_buffer:
                new_sl = max(sl + trail_buffer, 1)
                print(f"🔄 Trailing SL for {symbol}: LTP={ltp} | Old SL={sl}, New SL={new_sl}")
                send_telegram_message(f"🔄 Trailing SL for {symbol}: LTP={ltp} | Old SL={sl}, New SL={new_sl}")
                stop_losses[symbol] = new_sl

            # SL Hit
            elif ltp <= sl:
                print(f"🚨 SL HIT for {symbol} | LTP={ltp} <= SL={sl} | Exiting...")
                send_telegram_message(f"🚨 SL HIT for {symbol} | LTP={ltp} <= SL={sl} | Placing exit order...")
                #
                # order_id = update_order(
                #     kite=kite,
                #     symbol=symbol,
                #     exchange=exchange,
                #     price=sl - trail_buffer,
                #     trigger_price=None,
                #     quantity=quantity,
                #     product=product,
                #     order_type='MARKET',
                #     order_id=orders_dict.get(symbol)
                # )

                order_id = 111

                if order_id:
                    orders_dict[symbol] = order_id
                else:
                    orders_dict.pop(symbol, None)
                    stop_losses.pop(symbol, None)  # Reset SL if failed

        time.sleep(15)

    print("⏰ It's past 11:30 PM. MCX Market closed...")

def update_order(kite, symbol, exchange, price, trigger_price, quantity, product, order_type, order_id):
    """
    Places or modifies an order.
    """
    try:
        print(f"📤 Placing/Modifying Order => Symbol: {symbol} | Price: {price} | Order ID: {order_id}")

        if order_id is None:
            order_id = kite.place_order(
                variety=kite.VARIETY_REGULAR,
                exchange=exchange,
                tradingsymbol=symbol,
                transaction_type=kite.TRANSACTION_TYPE_SELL,
                quantity=quantity,
                product=product,
                order_type=order_type,
                price=price,
                trigger_price=trigger_price
            )
            print(f"✅ Exit order placed for {symbol} | Order ID: {order_id}")
            send_telegram_message(f"✅ Exit order placed for {symbol} at price {price} | Order ID: {order_id}")
        else:
            order_id = kite.modify_order(
                variety=kite.VARIETY_REGULAR,
                order_id=order_id,
                quantity=quantity,
                price=price,
                order_type=order_type,
                trigger_price=trigger_price
            )
            print(f"✅ Exit order modified for {symbol} | Order ID: {order_id}")
            send_telegram_message(f"✅ Exit order modified for {symbol} at price {price} | Order ID: {order_id}")

        return order_id

    except Exception as e:
        print(f"❌ Failed to place/modify exit order for {symbol}: {e}")
        send_telegram_message(f"❌ Failed to place/modify exit order for {symbol}: {e}")

        if "Maximum allowed order modifications" in str(e):
            try:
                kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id)
                print(f"⚠️ Order modification limit reached. Cancelled order {order_id}.")
                send_telegram_message(f"⚠️ Order modification limit reached. Cancelled order for {symbol}.")
            except Exception as cancel_err:
                print(f"⚠️ Failed to cancel order: {cancel_err}")
        return False

def add_sl_and_target_on_fno_positions(kite, stop_loss=-7500, exchange='NFO', trail_trigger=7500, trail_gap=1000):
    now = datetime.datetime.now().time()
    cutoff = datetime.time(15, 30) if exchange == 'MCX' else datetime.time(15, 30)

    pos_dict = {}  # To store trailing targets

    while now < cutoff:
        now = datetime.datetime.now().time()
        positions = kite.positions().get("net", [])
        net_positions = [pos for pos in positions if pos['exchange'] == exchange and pos['quantity'] != 0]
        all_orders = kite.orders()

        total_pnl = 0
        top_loss = 0

        for pos in net_positions:
            symbol = pos['exchange'] + ':' + pos['tradingsymbol']
            unrealised = pos['unrealised']
            pnl = pos['pnl']
            quantity = abs(pos['quantity'])
            transaction = kite.TRANSACTION_TYPE_BUY if pos['quantity'] < 0 else kite.TRANSACTION_TYPE_SELL

            total_pnl += pnl
            top_loss = pnl if pnl < top_loss else top_loss

            color = "\033[92m" if pnl > 0 else "\033[91m"
            print(f"{symbol} PnL → {color}{int(pnl)}\033[0m")

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
                    print(f"📈 {symbol} hit ₹{trail_trigger} profit. Setting trail at ₹{int(trail_level)}.")
                    send_telegram_message(f"📈 {symbol} profit > ₹{trail_trigger}. Trail set at ₹{int(trail_level)}.")
                else:
                    prev_trail = pos_dict[symbol]['trail']
                    if unrealised > prev_trail + trail_gap:
                        # Raise trailing level
                        new_trail = unrealised - trail_gap
                        print(f"🔄 {symbol} trailing target raised from ₹{int(prev_trail)} to ₹{int(new_trail)}.")
                        send_telegram_message(f"🔄 Trailing target for {symbol} raised to ₹{int(new_trail)}.")
                        pos_dict[symbol]['trail'] = new_trail
                    elif unrealised < prev_trail:
                        # 🔚 Trail level breached: exit
                        print(f"🚪 {symbol} breached trailing target (₹{int(prev_trail)}). Exiting...")
                        send_telegram_message(f"🚪 {symbol} trailing SL hit. Exiting position at ₹{int(unrealised)}.")
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

        total_color = "\033[92m" if total_pnl > 0 else "\033[91m"
        print(f"💰 Total P&L: {total_color}{int(total_pnl)}\033[0m\n")
        time.sleep(30)

    print(f"⏰ It's past 3:30 PM. {exchange} market closed.")

import datetime
from dateutil.relativedelta import relativedelta

def get_expected_positions_by_premium(kite, premium_targets=[250, 150, 100, 75], range_limit=20):
    """
    Generate option symbols (NIFTY) for both CE and PE sides whose premiums are close to the given targets.

    Parameters:
        kite (KiteConnect): Authenticated Kite client.
        premium_targets (list): Target premiums like [200, 150, 100].
        range_limit (int): Number of strikes above/below spot to search.

    Returns:
        list: List of selected option symbols with premium close to targets.
    """
    quote_key = "NSE:NIFTY 50"
    try:
        nifty_ltp = kite.quote([quote_key])[quote_key]['last_price']
    except Exception as e:
        print(f"❌ Error fetching NIFTY quote: {e}")
        return []

    spot_price = round(nifty_ltp / 50) * 50  # NIFTY strikes are in 50-point intervals
    print(f"🔍 NIFTY LTP => {nifty_ltp}, Rounded Spot => {spot_price}")

    # Format expiry (next Thursday)
    today = datetime.datetime.today()
    days_ahead = (3 - today.weekday()) % 7  # 3 = Thursday
    expiry_date = today + datetime.timedelta(days=days_ahead or 7)
    expiry = expiry_date.strftime('%y%b').upper()

    # Generate strike list around spot
    strikes = [spot_price + 50 * i for i in range(-range_limit, range_limit + 1)]
    call_symbols = [f"NFO:NIFTY{expiry}{strike}CE" for strike in strikes]
    put_symbols  = [f"NFO:NIFTY{expiry}{strike}PE" for strike in strikes]

    all_symbols = call_symbols + put_symbols

    try:
        ltp_data = kite.ltp(all_symbols)
    except Exception as e:
        print(f"❌ Error fetching LTPs: {e}")
        return []

    selected = []

    for target in premium_targets:
        closest_ce = min(
            [(s, abs(ltp_data[s]['last_price'] - target)) for s in call_symbols if s in ltp_data],
            key=lambda x: x[1],
            default=(None, float('inf'))
        )
        closest_pe = min(
            [(s, abs(ltp_data[s]['last_price'] - target)) for s in put_symbols if s in ltp_data],
            key=lambda x: x[1],
            default=(None, float('inf'))
        )

        if closest_ce[0]:
            selected.append((closest_ce[0], ltp_data[closest_ce[0]]['last_price']))
        if closest_pe[0]:
            selected.append((closest_pe[0], ltp_data[closest_pe[0]]['last_price']))

    print("\n🎯 Selected Options Near Target Premiums:")
    for symbol, premium in selected:
        print(f"{symbol} -> ₹{premium}")

    return selected

def calculate_daily_from_vix(kite, spot_price = None):
    """
    Fetch live India VIX and compute the expected 1-day move percentage and in index points.

    Args:
        kite: Authenticated KiteConnect client
        spot_price: Current underlying index price (i.e. NIFTY level), optional

    Returns:
        dict: {'vix':float, 'daily_pct':float, 'daily_points':float or None}
    """
    symbol = "NSE:INDIA VIX"
    try:
        data = kite.quote([symbol])[symbol]
        vix = data.get('last_price')
        if vix is None:
            raise ValueError("VIX returned as None")
    except Exception as e:
        raise RuntimeError(f"Error fetching India VIX: {e}")

    # approximate daily volatility = vix/100 * sqrt(1/365)
    daily_pct = (vix / 100.0) * math.sqrt(1 / 365)
    daily_points = round(daily_pct * spot_price, 2) if spot_price else None

    return {'vix': vix, 'daily_pct': daily_pct, 'daily_points': daily_points}




