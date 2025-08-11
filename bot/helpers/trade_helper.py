import math
from datetime import datetime, timedelta

import pandas as pd
from dateutil.relativedelta import relativedelta
from kiteconnect import KiteConnect

def calculate_dema(df, span=5):
    df['EMA1'] = df['close'].ewm(span=span, adjust=False).mean()
    df['EMA2'] = df['EMA1'].ewm(span=span, adjust=False).mean()
    df['DEMA'] = 2 * df['EMA1'] - df['EMA2']
    return df

def fetch_gold_candles(kite, days=5):
    from_date = (datetime.now() - timedelta(days=days)).date()
    to_date = datetime.now().date()

    candles = kite.historical_data(
        instrument_token=118628615,  # Replace with correct instrument token
        interval="day",
        from_date=from_date,
        to_date=to_date
    )
    return pd.DataFrame(candles)

def calculate_daily_from_vix(kite, spot_price=None):
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

def get_days_to_expiry():
    initial_expiry = datetime.today() + relativedelta(days=8)

    # Step 2: Get last day of the expiry month
    next_month = initial_expiry.replace(day=1) + relativedelta(months=1)
    last_day_of_month = next_month - timedelta(days=1)

    # Step 3: Find the last Thursday of that month
    days_to_subtract = (last_day_of_month.weekday() - 3) % 7  # 3 = Thursday
    last_thursday = last_day_of_month - timedelta(days=days_to_subtract)

    # Step 4: Get difference from today
    today = datetime.today()
    difference_days = (last_thursday.date() - today.date()).days

    # Output
    # print("Today:                 ", today.strftime("%Y-%m-%d"))
    # print("Initial expiry:        ", initial_expiry.strftime("%Y-%m-%d"))
    # print("Last Thursday of month:", last_thursday.strftime("%Y-%m-%d"))
    # print("Days until last Thursday:", difference_days)
    return difference_days

def decrease_by_20_percent_fixed(g1, n_terms):
    result = [g1]
    for _ in range(1, n_terms):
        result.append(int(result[-1] * 0.75))
    return result

def get_aug7_weekly_options(kite: KiteConnect):
    instruments = kite.instruments("NFO")
    result = [
        inst for inst in instruments
        if inst['name'] == 'NIFTY'
        and inst['instrument_type'] in ['CE', 'PE']
        # and inst['expiry'].date() == date(2025, 8, 7)
    ]
    return result