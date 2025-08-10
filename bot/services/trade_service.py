import os
import sys
import time
from bot.trading_alerts import send_telegram_message
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from bot.helpers import trade_helper

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def demo_cross_indicator(kite, days=5, instrument = 118628615):

    while True:
        df = trade_helper.fetch_gold_candles(kite, days)
        df = trade_helper.calculate_dema(df, days)

        latest_dema = df.iloc[-1]['DEMA']
        gold_ltp = kite.ltp([instrument])[str(instrument)]['last_price']
        print(f"Latest {days} DEMA for GOLDM: {round(latest_dema, 2)}  LTP: {gold_ltp}")
        # send_telegram_message(f"GOLD {days} DEMA breached. \t LPT: {gold_ltp}")

        # if gold_ltp > latest_dema:
            # send_telegram_message(f"GOLD {days} DEMA crossed. \t LPT: {gold_ltp}")
            # break
        # if gold_ltp < latest_dema:
        #     send_telegram_message(f"GOLD {days} DEMA breached. \t LPT: {gold_ltp}")
        #     break
        time.sleep(30)

def get_expected_positions(kite, step=250, count=4):
    # Get NIFTY 50 spot price
    quote_key = "NSE:NIFTY 50"
    try:
        nifty_ltp = kite.quote([quote_key])[quote_key]['last_price']
    except Exception as e:
        print(f"❌ Error fetching NIFTY quote: {e}")
        return []

    vix_data = trade_helper.calculate_daily_from_vix(kite, nifty_ltp)

    step = int(vix_data['daily_points'] / 50) * 100

    # Round up to next 250 strike
    spot_price = ((int(nifty_ltp / step) + 1) * step)
    print(f"{quote_key} => {nifty_ltp}")
    print(f"🔍 OTM Spot => {spot_price}")

    # Generate strike prices
    otm_calls = [spot_price + step * i for i in range(0, count)]
    otm_puts = [spot_price - step * i for i in range(1, count + 1)]

    # Format expiry as YYMON (e.g., 25AUG)
    expiry = (datetime.today() + relativedelta(days=8)).strftime('%y%b').upper()
    # expiry ="25814" #for 07 AUG 2025 weekly expirty

    # Generate option symbols
    call_symbols = [f"NIFTY{expiry}{strike}CE" for strike in otm_calls]
    put_symbols = [f"NIFTY{expiry}{strike}PE" for strike in otm_puts]

    expected_positions = call_symbols + put_symbols
    return expected_positions

def get_expected_positions_by_premium(kite, premium_targets=None, quote_key = "NSE:NIFTY 50", range_limit=20):
    if premium_targets is None:
        res = trade_helper.calculate_daily_from_vix(kite)
        days = trade_helper.get_days_to_expiry()
        g1 = res.get('vix', 1) * days * 0.75
        premium_targets = trade_helper.decrease_by_20_percent_fixed(g1, 4)
        # premium_targets = [200, 160, 125, 95]

    try:
        nifty_ltp = kite.quote([quote_key])[quote_key]['last_price']
    except Exception as e:
        print(f"❌ Error fetching NIFTY quote: {e}")
        return []

    spot_price = round(nifty_ltp / 50) * 50  # NIFTY strikes are in 50-point intervals
    print(f"🔍 NIFTY LTP => {nifty_ltp}, Rounded Spot => {spot_price}")

    # Format expiry (next Thursday)
    today = datetime.today()
    days_ahead = (3 - today.weekday()) % 7  # 3 = Thursday
    expiry_date = today + timedelta(days=days_ahead or 7)
    expiry = expiry_date.strftime('%y%b').upper()

    # Generate strike list around spot
    strikes = [spot_price + 50 * i for i in range(-range_limit, range_limit + 1)]
    call_symbols = [f"NFO:NIFTY{expiry}{strike}CE" for strike in strikes]
    put_symbols = [f"NFO:NIFTY{expiry}{strike}PE" for strike in strikes]

    all_symbols = call_symbols + put_symbols

    try:
        ltp_data = kite.ltp(all_symbols)
    except Exception as e:
        print(f"❌ Error fetching LTPs: {e}")
        return []

    selected = []
    options = []

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

        # if closest_ce[0]:
        #     selected.append((closest_ce[0], ltp_data[closest_ce[0]]['last_price']))
        #     options.append(closest_ce[0][4:])
        # if closest_pe[0]:
        #     options.append(closest_pe[0][4:])

        if closest_ce[0] and abs(ltp_data[closest_ce[0]]['last_price'] - target) <= range_limit:
            selected.append((closest_ce[0], ltp_data[closest_ce[0]]['last_price']))
            options.append(closest_ce[0][4:])
        if closest_pe[0] and abs(ltp_data[closest_pe[0]]['last_price'] - target) <= range_limit:
            options.append(closest_pe[0][4:])

    print("\n🎯 Selected Options Near Target Premiums:")
    for symbol, premium in selected:
        print(f"{symbol} -> ₹{premium}")

    return options

def analyze_positions(kite):
    # Get positions from Kite
    positions = kite.positions()
    net_positions = positions.get("net", [])
    print(f"Time : {datetime.now()}")

    # Filter NFO positions with non-zero quantity
    current_positions = [
        pos['tradingsymbol']
        for pos in net_positions
        if pos['exchange'] == 'NFO' and pos['quantity'] != 0
    ]

    # expected_positions = get_expected_positions(kite)
    expected_positions = get_expected_positions_by_premium(kite)
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
