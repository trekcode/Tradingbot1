import os
import time
from tickerall import Tickerall

# ---- Secrets from Deplexo Env Vars ----
API_KEY = os.getenv("TICKERALL_API_KEY")
SERVER = os.getenv("EXNESS_SERVER")
ACCOUNT = int(os.getenv("EXNESS_ACCOUNT", "0"))
PASSWORD = os.getenv("EXNESS_PASSWORD")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))

# ---- Symbols & Strategy Config ----
SYMBOLS = [
    "XAUUSDm",   # Gold
    "EURUSDm",
    "GBPUSDm",
    "USDJPYm",
    "AUDUSDm",
    "USDCADm",
]
TIMEFRAME = "M5"
FAST_MA = 10
SLOW_MA = 30
VOLUME = 0.01

# ---- Risk Rules ----
MAX_TOTAL_POSITIONS = 5      # Hard cap across all symbols
MAX_PER_SYMBOL = 1           # Only one position per pair/gold

# Gold risk (price units)
GOLD_SL = 5.0
GOLD_TP = 10.0

# FX risk (pips)
FX_SL_PIPS = 10
FX_TP_PIPS = 20

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

def get_signal(client, account_id, symbol):
    """Returns 'BUY', 'SELL', or None based on closed M5 candles."""
    try:
        bars = client.candles.get(
            account_id,
            symbol=symbol,
            hours=24,
            timeframe=TIMEFRAME,
        )
        # Use only CLOSED bars (ignore the current forming bar)
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
    """Return pip size in price units for a symbol spec."""
    # For 5-digit FX and 3-digit JPY pairs, pip = 10 * point.
    # For gold (digits=3), we handle risk separately.
    return spec.point * 10

def calculate_sl_tp(symbol, side, entry_price, spec):
    """Return (stop_loss, take_profit) price levels."""
    if symbol == "XAUUSDm":
        sl_dist = GOLD_SL
        tp_dist = GOLD_TP
    else:
        pip = get_pip_size(spec)
        sl_dist = FX_SL_PIPS * pip
        tp_dist = FX_TP_PIPS * pip

    if side == "BUY":
        return round(entry_price - sl_dist, spec.digits), round(entry_price + tp_dist, spec.digits)
    else:
        return round(entry_price + sl_dist, spec.digits), round(entry_price - tp_dist, spec.digits)

def count_positions(detail):
    """Return (total_open, set_of_symbols_with_open_positions)."""
    if not detail.positions:
        return 0, set()
    symbols_open = {p.symbol for p in detail.positions}
    return len(detail.positions), symbols_open

def main():
    print("🚀 Multi-Symbol Scalping Bot starting...", flush=True)
    client, session = connect()
    print(f"✅ Connected: {session.account_id} (demo={session.is_demo})", flush=True)

    # Fetch symbol specs once
    specs = {s.symbol: s for s in client.accounts.symbol_specs(session.account_id)}
    for sym in SYMBOLS:
        if sym not in specs:
            print(f"⚠️ Spec not found for {sym} — will skip this symbol", flush=True)

    while True:
        try:
            detail = client.accounts.get(session.account_id)
            total_open, symbols_open = count_positions(detail)

            # Hard cap check
            if total_open >= MAX_TOTAL_POSITIONS:
                print(f"🛑 Max total positions ({MAX_TOTAL_POSITIONS}) reached — monitoring only", flush=True)
                time.sleep(POLL_SECONDS)
                continue

            for symbol in SYMBOLS:
                if symbol not in specs:
                    continue

                # Per-symbol cap
                if symbol in symbols_open:
                    print(f"⏸️ {symbol}: position already open — skipping", flush=True)
                    continue

                signal = get_signal(client, session.account_id, symbol)
                if signal is None:
                    print(f"⏳ {symbol}: no signal", flush=True)
                    continue

                # Get latest price for entry
                bars = client.candles.get(
                    session.account_id,
                    symbol=symbol,
                    hours=1,
                    timeframe=TIMEFRAME,
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
                print(f"✅ Order placed! {symbol} Ticket: {order.ticket} | Status: {order.status}", flush=True)

                # Refresh positions after placing to respect the cap immediately
                detail = client.accounts.get(session.account_id)
                total_open, symbols_open = count_positions(detail)
                if total_open >= MAX_TOTAL_POSITIONS:
                    print(f"🛑 Reached {MAX_TOTAL_POSITIONS} positions — stopping signal scan this cycle", flush=True)
                    break

                # Small pause between orders to avoid rate spikes
                time.sleep(2)

        except Exception as e:
            print(f"❌ Main loop error: {e}", flush=True)

        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()