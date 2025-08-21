"""
Refactored Kite Ticker Bot
- Class-based
- Order & position caching with time-based refresh
- Safe global resets using .clear()
- Reconnect logic and resubscribe
- Decimal usage for monetary math
- Structured logging
- Config at top (easy to change)

NOTE: this file assumes `get_kite_client`, `get_kite_ticker`, and
`send_telegram_message` are available from your project (as before).
"""

import time
import threading
import logging
import signal
from decimal import Decimal, getcontext
from typing import Dict, List, Optional

# set precision for Decimal money math
getcontext().prec = 12

# ====== Configuration ======
REFRESH_POSITIONS_SECONDS = 60
REFRESH_ORDERS_SECONDS = 5
PROCESS_INTERVAL_SECONDS = 15
STOP_LOSS = Decimal(-5000)
TRAIL_TRIGGER = Decimal(3750)
TRAIL_GAP = Decimal(250)
EXCHANGE = "NFO"
LOT_SIZE = 75  # adjust if needed

# ====== Logging ======
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    filename="logs/kite_ticker.log",
)
logger = logging.getLogger(__name__)

# ====== External dependencies ======
# These should exist in your repo as before
from bot.trading_alerts import send_telegram_message
from bot.services.kite_service import get_kite_ticker, get_kite_client
from bot.trade_logic import reset_option_short_orders


class KiteTickerBot:
    def __init__(self):
        # kite clients
        self.kite = get_kite_client()
        self.kws = get_kite_ticker()

        # caches and state
        self.position_cache: Dict[int, dict] = {}
        self.ltp_dict: Dict[int, Decimal] = {}
        self.pos_dict: Dict[str, dict] = {}  # trailing SL and order tracking keyed by "EX:SYMBOL"

        # order cache
        self._orders_cache: List[dict] = []
        self._last_orders_fetch = 0.0

        # position refresh timing
        self._last_positions_fetch = 0.0

        # processing throttle
        self._last_processed_time = 0.0

        # threading controls
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

        # attach handlers
        self._attach_handlers()

    # ---------------------- websocket handlers ----------------------
    def _attach_handlers(self):
        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = self.on_connect
        self.kws.on_close = self.on_close
        self.kws.on_error = self.on_error
        try:
            # some KiteTicker implementations support on_noreconnect
            self.kws.on_noreconnect = self.on_noreconnect
        except Exception:
            pass

    # ---------------------- cache utilities ----------------------
    def _fetch_orders(self) -> List[dict]:
        now = time.time()
        if now - self._last_orders_fetch > REFRESH_ORDERS_SECONDS:
            try:
                self._orders_cache = self.kite.orders()
                self._last_orders_fetch = now
            except Exception as e:
                logger.error("Error fetching orders: %s", e)
        return self._orders_cache

    def _update_position_cache(self) -> List[int]:
        now = time.time()
        if now - self._last_positions_fetch > REFRESH_POSITIONS_SECONDS:
            try:
                positions = self.kite.positions().get("net", [])
                open_positions = [p for p in positions if p.get("exchange") == EXCHANGE and p.get("quantity", 0) != 0]
                with self._lock:
                    # rebuild cache using instrument_token as key
                    self.position_cache = {p["instrument_token"]: p for p in open_positions}
                self._last_positions_fetch = now
                logger.info("Position cache refreshed (%d positions)", len(open_positions))
            except Exception as e:
                logger.error("Error updating position cache: %s", e)
        return list(self.position_cache.keys())

    def reset_current_data(self):
        """Reset runtime tracking data safely and resubscribe to tokens."""
        reset_option_short_orders(self.kite)  # keep existing behaviour
        with self._lock:
            # clear in-place to preserve references
            self.ltp_dict.clear()
            self.pos_dict.clear()
        tokens = self._update_position_cache()
        if tokens:
            try:
                self.kws.subscribe(tokens)
                self.kws.set_mode(self.kws.MODE_LTP, tokens)
                logger.info("Subscribed to tokens after reset: %s", tokens)
            except Exception as e:
                logger.error("Failed to subscribe after reset: %s", e)

    # ---------------------- main processing ----------------------
    def on_ticks(self, ws, ticks: List[dict]):
        """WebSocket tick handler. Throttles processing to PROCESS_INTERVAL_SECONDS."""
        now = time.time()
        if now - self._last_processed_time < PROCESS_INTERVAL_SECONDS:
            # still within throttle window; update LTP cache only
            with self._lock:
                for tick in ticks:
                    self.ltp_dict[tick["instrument_token"]] = Decimal(str(tick.get("last_price", 0)))
            return

        # update LTPs and then run processing
        with self._lock:
            for tick in ticks:
                self.ltp_dict[tick["instrument_token"]] = Decimal(str(tick.get("last_price", 0)))

        try:
            self._process_positions()
        except Exception as e:
            logger.exception("Error while processing positions: %s", e)
        finally:
            self._last_processed_time = now

    def _process_positions(self):
        """Core logic moved from original script with improvements."""
        # refresh position cache if needed
        self._update_position_cache()

        all_orders = self._fetch_orders()

        total_pnl = Decimal(0)
        premium = Decimal(0)

        logger.info("Processing positions at %s", time.strftime("%Y-%m-%d %H:%M:%S"))

        # iterate a snapshot of position cache to avoid mutation issues
        with self._lock:
            positions_snapshot = list(self.position_cache.values())

        for pos in positions_snapshot:
            token = pos.get("instrument_token")
            if token is None:
                continue

            ltp = self.ltp_dict.get(token)
            if ltp is None:
                # no price yet
                continue

            # only consider SELL orders for average price override
            matching_orders = [o for o in all_orders if o.get("tradingsymbol") == pos.get("tradingsymbol") and o.get("transaction_type") == "SELL"]
            matching_orders.sort(key=lambda x: x.get("order_timestamp") or 0)
            latest_order = matching_orders[-1] if matching_orders else None

            # prefer average_price from latest SELL order if present
            average_price = Decimal(str(latest_order.get("average_price"))) if latest_order and latest_order.get("average_price") is not None else Decimal(str(pos.get("average_price", 0)))

            symbol_key = f"{pos.get('exchange')}:{pos.get('tradingsymbol')}"

            quantity = Decimal(abs(pos.get("quantity", 0)))
            pnl = (ltp - average_price) * Decimal(pos.get("quantity", 0))
            unrealised = pnl

            transaction = self.kite.TRANSACTION_TYPE_BUY if pos.get("quantity", 0) < 0 else self.kite.TRANSACTION_TYPE_SELL

            total_pnl += pnl
            premium += ltp * quantity

            # friendly printing
            pnl_int = int(pnl)
            color = "\033[92m" if pnl > 0 else "\033[91m"
            avg_str = f"{average_price:.2f}"
            ltp_str = f"{ltp:.2f}"
            sl_info = self.pos_dict.get(symbol_key)
            logger.info("%s - Qty: %s  Avg: %s   LTP: %s   P&L: %s%d\033[0m   SL: %s", pos.get("tradingsymbol"), pos.get("quantity"), avg_str, ltp_str, color, pnl_int, sl_info)

            # Check SL order tracking
            if symbol_key in self.pos_dict and self.pos_dict[symbol_key].get("order_id"):
                order_id = self.pos_dict[symbol_key]["order_id"]
                order_info = next((o for o in all_orders if o.get("order_id") == order_id), None)
                if order_info:
                    if order_info.get("status") in ["COMPLETE", "CANCELLED", "REJECTED"]:
                        logger.info("Previous SL order for %s was %s. Resetting tracking.", symbol_key, order_info.get("status"))
                        send_telegram_message(f"ℹ️ SL order for {symbol_key} marked as {order_info.get('status')}")
                        with self._lock:
                            self.pos_dict.pop(symbol_key, None)
                        continue
                    else:
                        logger.debug("SL order for %s still OPEN.", symbol_key)
                        continue

            # STOP LOSS
            if unrealised < STOP_LOSS:
                logger.warning("Stop-Loss hit for %s. Exiting position...", symbol_key)
                send_telegram_message(f"🚨 Stop-Loss hit for {symbol_key}. Exiting position...")
                try:
                    order_id = self.kite.place_order(
                        variety=self.kite.VARIETY_REGULAR,
                        exchange=pos.get("exchange"),
                        tradingsymbol=pos.get("tradingsymbol"),
                        transaction_type=transaction,
                        quantity=int(quantity),
                        order_type=self.kite.ORDER_TYPE_MARKET,
                        product=self.kite.PRODUCT_NRML,
                    )
                    logger.info("Exit order placed for %s | Order ID: %s", symbol_key, order_id)
                    send_telegram_message(f"✅ Exit order placed for {symbol_key} | Order ID: {order_id}")
                    # refresh subscriptions & caches
                    self.reset_current_data()
                except Exception as e:
                    logger.error("Error placing stop-loss exit for %s: %s", symbol_key, e)
                continue

            # TRAILING TARGET
            if unrealised > TRAIL_TRIGGER:
                with self._lock:
                    if symbol_key not in self.pos_dict:
                        trail_level = unrealised - TRAIL_GAP
                        self.pos_dict[symbol_key] = {"trail": trail_level}
                        logger.info("%s hit %s profit. Setting SL at %s.", symbol_key, TRAIL_TRIGGER, trail_level)
                        send_telegram_message(f"📈 {symbol_key} profit > ₹{TRAIL_TRIGGER}. Setting SL at ₹{trail_level}. LTP: ({unrealised})")
                    else:
                        prev_trail = self.pos_dict[symbol_key].get("trail")
                        if unrealised > (prev_trail + TRAIL_GAP):
                            new_trail = unrealised - TRAIL_GAP
                            logger.info("Trailing target raised for %s: %s -> %s", symbol_key, prev_trail, new_trail)
                            send_telegram_message(f"🔄 Trailing target for {symbol_key} raised to ₹{new_trail}. LTP: ({unrealised})")
                            self.pos_dict[symbol_key]["trail"] = new_trail
                        elif unrealised < prev_trail:
                            logger.info("Trailing target breached for %s (prev %s). Exiting...", symbol_key, prev_trail)
                            send_telegram_message(f"🚪 {symbol_key} trailing SL hit. Exiting position at ₹{unrealised}.")
                            try:
                                order_id = self.kite.place_order(
                                    variety=self.kite.VARIETY_REGULAR,
                                    exchange=pos.get("exchange"),
                                    tradingsymbol=pos.get("tradingsymbol"),
                                    transaction_type=transaction,
                                    quantity=int(quantity),
                                    order_type=self.kite.ORDER_TYPE_MARKET,
                                    product=self.kite.PRODUCT_NRML,
                                )
                                with self._lock:
                                    self.pos_dict[symbol_key]["orders"] = [order_id]
                                logger.info("Exit order placed for %s | Order ID: %s", symbol_key, order_id)
                                self.reset_current_data()
                            except Exception as e:
                                logger.error("Error placing trailing exit for %s: %s", symbol_key, e)
                            continue

        logger.info("Maximum Possible Profit: %s   Total P&L: %s", int(premium), int(total_pnl))

    # ---------------------- connection lifecycle ----------------------
    def on_connect(self, ws, response):
        logger.info("Connected to WebSocket.")
        tokens = self._update_position_cache()
        if tokens:
            try:
                ws.subscribe(tokens)
                ws.set_mode(ws.MODE_LTP, tokens)
                logger.info("Subscribed to tokens: %s", tokens)
            except Exception as e:
                logger.error("Subscription failed on connect: %s", e)

    def on_close(self, ws, code, reason):
        logger.error("WebSocket closed. Code: %s Reason: %s", code, reason)
        # simple reconnect strategy: wait and call connect again
        if not self._stop_event.is_set():
            logger.info("Attempting reconnect in 5s...")
            time.sleep(5)
            try:
                self.kws.connect(threaded=True)
            except Exception as e:
                logger.error("Reconnect failed: %s", e)

    def on_error(self, ws, code, reason):
        logger.error("WebSocket error. Code: %s Reason: %s", code, reason)

    def on_noreconnect(self, ws):
        logger.warning("WebSocket indicated no auto-reconnect will occur.")

    # ---------------------- run & shutdown ----------------------
    def start(self):
        logger.info("Starting KiteTickerBot...")
        try:
            self.kws.connect(threaded=True)
        except Exception as e:
            logger.error("Failed to connect to WebSocket: %s", e)

        # install signal handler for graceful shutdown
        signal.signal(signal.SIGINT, self._shutdown_signal)
        signal.signal(signal.SIGTERM, self._shutdown_signal)

        # keep main thread alive until stopped
        while not self._stop_event.is_set():
            time.sleep(1)

    def stop(self):
        logger.info("Stopping KiteTickerBot...")
        self._stop_event.set()
        try:
            self.kws.close()
        except Exception:
            pass

    def _shutdown_signal(self, signum, frame):
        logger.info("Received shutdown signal: %s", signum)
        self.stop()


# ====== CLI Entrypoint ======
if __name__ == "__main__":
    bot = KiteTickerBot()
    bot.start()

