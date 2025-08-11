import logging
import argparse
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bot.services.kite_service import get_kite_client
from bot.services import trade_service
from bot.trade_logic import reset_option_short_orders

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def main():
    kite = get_kite_client()
    # reset_option_short_orders(kite)
    # exit()

    parser = argparse.ArgumentParser(description="Kite Trading Bot CLI")
    parser.add_argument('--choice', type=str, help='Action to perform (e.g. pnl, trade, order)', required=False)
    args = parser.parse_args()
    #
    if args.choice:
        choice = args.choice
    else:
        # UI prompt
        print("Select Script")
        print("1. demo_cross_indicator")
        print("2. trail_target_and_exit (MCX)")
        print("3. check_sl_on_open_positions (FnO)")
        print("4. Analyze Positions")
        print("5. Add Sl and Target on FnO Short Positions")
        print("6. get_expected_positions_by_premium")

        choice = input("Enter your choice (1-5): ").strip()

    # Match-case requires Python 3.10+
    match choice:
        case "1": #GOLD demo_cross_indicator
            trade_service.demo_cross_indicator(kite, 5)
        # case "2": #trail_target_and_exit (MCX)
        #     trade_logic.trail_target_and_exit(kite)
        case "3": # check_sl_on_open_positions (FnO)
            trade_service.check_sl_on_open_positions(kite)
        case "4": #Analyze Positions
            trade_service.analyze_positions(kite)
        # case "5": #Add Sl and Target on FnO Short Positions
        #     trade_logic.add_sl_and_target_on_fno_positions(kite)
        case "6": #Add Sl and Target on FnO Short Positions
            trade_service.get_expected_positions_by_premium(kite)
        case _:
            print("❌ Invalid selection. Please choose between 1 and 4.")

if __name__ == "__main__":
    try:
        print("in")
        main()
    except Exception as e:
        print(f"❌ Error in main(): {e}")



