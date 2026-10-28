import os
import time
from tickerall import Tickerall

# ---- Load secrets from environment ----
API_KEY = os.getenv("TICKERALL_API_KEY")
SERVER = os.getenv("EXNESS_SERVER")
ACCOUNT = int(os.getenv("EXNESS_ACCOUNT", "0"))
PASSWORD = os.getenv("EXNESS_PASSWORD")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))

# ---- Strategy Settings ----
SYMBOL = "XAUUSDm"
TIMEFRAME = "M5"
FAST_MA = 10
SLOW_MA = 30
VOLUME = 0.01
SL_DISTANCE = 5.0
TP_DISTANCE = 10.0
MAX_POSITIONS = 5          # <-- allow up to 5 concurrent trades

def connect():
    client = Tickerall(api_key=API_KEY)
    session = client.sessions.start(
        broker="mt5",
        server=SERVER,
        account=ACCOUNT,
        password=PASSWORD,
    )
    return client, session

def get_ma(prices, period):
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period

def get_signal(client, account_id):
    try:
        bars = client.candles.get(
            account_id,
            symbol=SYMBOL,
            hours=24,
            timeframe=TIMEFRAME,
        )
        closes = [bar.close for bar in bars]
        if len(closes) < SLOW_MA + 1:
            return None

        fast_now = get_ma(closes, FAST_MA)
        slow_now = get_ma(closes, SLOW_MA)
        fast_prev = get_ma(closes[:-1], FAST_MA)
        slow_prev = get_ma(closes[:-1], SLOW_MA)

        if None in (fast_now, slow_now, fast_prev, slow_prev):
            return None

        if fast_prev <= slow_prev and fast_now > slow_now:
            return "BUY"
        if fast_prev >= slow_prev and fast_now < slow_now:
            return "SELL"
        return None
    except Exception as e:
        print(f"❌ Signal error: {e}", flush=True)
        return None

def count_open_positions(client, account_id):
    """Return number of open XAUUSDm positions."""
    try:
        detail = client.accounts.get(account_id)
        if detail.positions:
            return sum(1 for p in detail.positions if getattr(p, "symbol", "") == SYMBOL)
    except Exception as e:
        print(f"❌ Position check error: {e}", flush=True)
    return 0

def main():
    print("🚀 Scalping Bot starting (max 5 positions)...", flush=True)
    client, session = connect()
    print(f"✅ Connected: {session.account_id} (demo={session.is_demo})", flush=True)

    while True:
        try:
            open_count = count_open_positions(client, session.account_id)
            print(f"📊 Open {SYMBOL} positions: {open_count}/{MAX_POSITIONS}", flush=True)

            # Stop opening new trades once we reach 5
            if open_count >= MAX_POSITIONS:
                print("🛑 Max positions reached — monitoring only", flush=True)
                time.sleep(POLL_SECONDS)
                continue

            signal = get_signal(client, session.account_id)
            if signal is None:
                print("⏳ No signal yet — waiting...", flush=True)
                time.sleep(POLL_SECONDS)
                continue

            bars = client.candles.get(
                session.account_id,
                symbol=SYMBOL,
                hours=1,
                timeframe=TIMEFRAME,
            )
            last_price = bars[-1].close

            if signal == "BUY":
                sl = round(last_price - SL_DISTANCE, 3)
                tp = round(last_price + TP_DISTANCE, 3)
            else:
                sl = round(last_price + SL_DISTANCE, 3)
                tp = round(last_price - TP_DISTANCE, 3)

            print(f"🎯 Signal: {signal} @ {last_price} | SL={sl} TP={tp}", flush=True)
            order = client.orders.place(
                session.account_id,
                type="market",
                symbol=SYMBOL,
                side=signal,
                volume=VOLUME,
                stop_loss=sl,
                take_profit=tp,
            )
            print(f"✅ Order placed! Ticket: {order.ticket} | Status: {order.status}", flush=True)

        except Exception as e:
            print(f"❌ Main loop error: {e}", flush=True)

        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()