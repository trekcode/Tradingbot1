import os
import time
from tickerall import Tickerall

# ---- Load secrets from environment ----
API_KEY = os.getenv("TICKERALL_API_KEY")
SERVER = os.getenv("EXNESS_SERVER")
ACCOUNT = int(os.getenv("EXNESS_ACCOUNT", "0"))
PASSWORD = os.getenv("EXNESS_PASSWORD")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))

# ---- Symbols & Strategy Config ----
SYMBOLS = ["XAUUSDm", "EURUSDm", "GBPUSDm", "USDJPYm", "AUDUSDm", "USDCADm"]
TIMEFRAME = "M5"
FAST_MA = 10
SLOW_MA = 30
VOLUME = 0.01

MAX_TOTAL_POSITIONS = 5
MAX_PER_SYMBOL = 1

GOLD_SL = 5.0
GOLD_TP = 10.0
FX_SL_PIPS = 10
FX_TP_PIPS = 20

def connect():
    client = Tickerall(api_key=API_KEY)
    session = client.sessions.keep_alive(
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

def get_signal(client, account_id, symbol):
    try:
        bars = client.candles.get(
            account_id, symbol=symbol, hours=2, timeframe=TIMEFRAME
        )
        closes = [bar.close for bar in bars[:-1]]
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
        print(f"❌ Signal error for {symbol}: {e}", flush=True)
        return None

def get_pip_size(spec):
    return spec.point * 10

def calculate_sl_tp(symbol, side, entry_price, spec):
    if symbol == "XAUUSDm":
        sl_dist, tp_dist = GOLD_SL, GOLD_TP
    else:
        pip = get_pip_size(spec)
        sl_dist, tp_dist = FX_SL_PIPS * pip, FX_TP_PIPS * pip
    if side == "BUY":
        return round(entry_price - sl_dist, spec.digits), round(entry_price + tp_dist, spec.digits)
    else:
        return round(entry_price + sl_dist, spec.digits), round(entry_price - tp_dist, spec.digits)

def count_positions(detail):
    if not detail.positions:
        return 0, set()
    symbols_open = {p.symbol for p in detail.positions}
    return len(detail.positions), symbols_open

def main():
    print("🚀 Multi-Symbol Scalping Bot starting...", flush=True)
    client, session = connect()
    print(f"✅ Connected: {session.account_id} (demo={session.is_demo})", flush=True)

    # ⬇️ FIXED LINE: use .name instead of .symbol
    try:
        specs = {s.name: s for s in client.accounts.symbol_specs(session.account_id)}
    except Exception as e:
        print(f"❌ Failed to fetch symbol specs: {e}", flush=True)
        return

    for sym in SYMBOLS:
        if sym not in specs:
            print(f"⚠️ Spec not found for {sym} — will skip", flush=True)

    while True:
        try:
            detail = client.accounts.get(session.account_id)
            total_open, symbols_open = count_positions(detail)

            if total_open >= MAX_TOTAL_POSITIONS:
                print(f"🛑 Max positions ({MAX_TOTAL_POSITIONS}) reached — monitoring only", flush=True)
                time.sleep(POLL_SECONDS)
                continue

            for symbol in SYMBOLS:
                if symbol not in specs:
                    continue
                if symbol in symbols_open:
                    print(f"⏸️ {symbol}: position already open — skipping", flush=True)
                    continue

                signal = get_signal(client, session.account_id, symbol)
                if signal is None:
                    print(f"⏳ {symbol}: no signal", flush=True)
                    continue

                bars = client.candles.get(
                    session.account_id, symbol=symbol, hours=1, timeframe=TIMEFRAME
                )
                entry_price = bars[-1].close
                sl, tp = calculate_sl_tp(symbol, signal, entry_price, specs[symbol])

                print(f"🎯 {symbol}: {signal} @ {entry_price} | SL={sl} TP={tp}", flush=True)
                order = client.orders.place(
                    session.account_id,
                    type="market",
                    symbol=symbol,
                    side=signal,
                    volume=VOLUME,
                    stop_loss=sl,
                    take_profit=tp,
                )
                print(f"✅ Order placed! {symbol} Ticket: {order.ticket}", flush=True)

                detail = client.accounts.get(session.account_id)
                total_open, symbols_open = count_positions(detail)
                if total_open >= MAX_TOTAL_POSITIONS:
                    print(f"🛑 Reached {MAX_TOTAL_POSITIONS} positions — stopping this cycle", flush=True)
                    break
                time.sleep(2)

        except Exception as e:
            print(f"❌ Main loop error: {e}", flush=True)

        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()