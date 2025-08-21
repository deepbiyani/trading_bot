import pandas as pd
from datetime import datetime, timedelta
import time
import trading_alerts
import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bot.trading_alerts import send_telegram_message

def fetch_gold_candles(kite, days = 5, instrument_token=118628615):
    from_date = (datetime.now() - timedelta(days=days)).date()
    to_date = datetime.now().date()

    candles = kite.historical_data(
        instrument_token=instrument_token,  # Replace with correct instrument token
        interval="day",
        from_date=from_date,
        to_date=to_date
    )
    return pd.DataFrame(candles)

def calculate_dema(df, span = 5):
    df['EMA1'] = df['close'].ewm(span=span, adjust=False).mean()
    df['EMA2'] = df['EMA1'].ewm(span=span, adjust=False).mean()
    df['DEMA'] = 2 * df['EMA1'] - df['EMA2']
    return df

def demo_cross_indicator(kite, days = 5, instrument = 118628615):

    while True:
        df = fetch_gold_candles(kite, days)
        df = calculate_dema(df, days)

        latest_dema = df.iloc[-1]['DEMA']
        gold_ltp = kite.ltp([instrument])[str(instrument)]['last_price']
        print(f"Latest {days} DEMA for GOLDM: {round(latest_dema, 2)}  LTP: {gold_ltp}")

        # if gold_ltp > 98900:
        #     send_telegram_message(f"GOLD crossed 98900. \t LPT: {gold_ltp}")

        # if gold_ltp > latest_dema:
        #     trading_alerts.send_telegram_message(f"GOLD {days} DEMA crossed. \t LPT: {gold_ltp}")
        #     break

        # if gold_ltp < latest_dema:
        #     send_telegram_message(f"GOLD {days} DEMA breached. \t LPT: {gold_ltp}")
        #     break

        time.sleep(30)

