import os
import time
from tickerall import Tickerall

# ---- Load secrets from environment ----
API_KEY = os.getenv("TICKERALL_API_KEY")
SERVER = os.getenv("EXNESS_SERVER")
ACCOUNT = int(os.getenv("EXNESS_ACCOUNT", "0"))
PASSWORD = os.getenv("EXNESS_PASSWORD")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "5"))

# ---- Symbols & Strategy Config (scalper) ----
SYMBOLS = ["XAUUSDm", "EURUSDm", "GBPUSDm", "USDJPYm", "AUDUSDm", "USDCADm"]
TIMEFRAME = "M1"
FAST_MA = 5
SLOW_MA = 13
LOOKBACK_HOURS = 4          # 240 M1 bars, far more than SLOW_MA + 2 needed
MIN_BARS = SLOW_MA + 2      # closed bars needed for current + previous MA
VOLUME = 0.01

MAX_TOTAL_POSITIONS = 5
MAX_PER_SYMBOL = 1

GOLD_SL = 2.0
GOLD_TP = 3.0
FX_SL_PIPS = 5
FX_TP_PIPS = 8

# Optional: skip trading when the bot hasn't seen a new closed bar
last_bar_traded = {}


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


def bar_id_of(bar):
    """Identify a bar by its timestamp; fall back to a 1-minute bucket."""
    for attr in ("time", "timestamp", "t", "open_time"):
        val = getattr(bar, attr, None)
        if val is not None:
            return str(val)
    return str(int(time.time() // 60))


def get_signal(client, account_id, symbol):
    """
    Returns (signal, entry_price, bar_id) or (None, None, None).
    Crossover is evaluated on CLOSED bars only (forming bar excluded).
    """
    try:
        bars = client.candles.get(
            account_id, symbol=symbol, hours=LOOKBACK_HOURS, timeframe=TIMEFRAME
        )
        if not bars or len(bars) < MIN_BARS + 1:
            print(f"⚠️ {symbol}: only {len(bars) if bars else 0} bars", flush=True)
            return None, None, None

        closed = bars[:-1]
        closes = [b.close for b in closed]

        fast_now = get_ma(closes, FAST_MA)
        slow_now = get_ma(closes, SLOW_MA)
        fast_prev = get_ma(closes[:-1], FAST_MA)
        slow_prev = get_ma(closes[:-1], SLOW_MA)
        if None in (fast_now, slow_now, fast_prev, slow_prev):
            return None, None, None

        print(
            f"📈 {symbol}: fast={fast_now:.5f} slow={slow_now:.5f} "
            f"(prev {fast_prev:.5f}/{slow_prev:.5f})",
            flush=True,
        )

        signal = None
        if fast_prev <= slow_prev and fast_now > slow_now:
            signal = "BUY"
        elif fast_prev >= slow_prev and fast_now < slow_now:
            signal = "SELL"

        if signal is None:
            return None, None, None

        entry_price = bars[-1].close  # latest price from the forming bar
        return signal, entry_price, bar_id_of(closed[-1])

    except Exception as e:
        print(f"❌ Signal error for {symbol}: {e}", flush=True)
        return None, None, None


def get_pip_size(spec):
    return spec.point * 10


def calculate_sl_tp(symbol, side, entry_price, spec):
    if symbol == "XAUUSDm":
        sl_dist, tp_dist = GOLD_SL, GOLD_TP
    else:
        pip = get_pip_size(spec)
        sl_dist, tp_dist = FX_SL_PIPS * pip, FX_TP_PIPS * pip
    if side == "BUY":
        return (
            round(entry_price - sl_dist, spec.digits),
            round(entry_price + tp_dist, spec.digits),
        )
    return (
        round(entry_price + sl_dist, spec.digits),
        round(entry_price - tp_dist, spec.digits),
    )


def count_positions(detail):
    if not detail.positions:
        return 0, {}
    per_symbol = {}
    for p in detail.positions:
        per_symbol[p.symbol] = per_symbol.get(p.symbol, 0) + 1
    return len(detail.positions), per_symbol


def main():
    print("🚀 Multi-Symbol Scalping Bot starting...", flush=True)
    client, session = connect()
    print(f"✅ Connected: {session.account_id} (demo={session.is_demo})", flush=True)

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
            total_open, per_symbol = count_positions(detail)

            if total_open >= MAX_TOTAL_POSITIONS:
                print(f"🛑 Max positions ({MAX_TOTAL_POSITIONS}) reached — monitoring only", flush=True)
                time.sleep(POLL_SECONDS)
                continue

            for symbol in SYMBOLS:
                if symbol not in specs:
                    continue
                if per_symbol.get(symbol, 0) >= MAX_PER_SYMBOL:
                    continue

                signal, entry_price, bar_id = get_signal(client, session.account_id, symbol)
                if signal is None:
                    continue

                # Don't re-enter on the same crossover bar
                if last_bar_traded.get(symbol) == bar_id:
                    continue

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
                last_bar_traded[symbol] = bar_id
                print(f"✅ Order placed! {symbol} Ticket: {order.ticket}", flush=True)

                detail = client.accounts.get(session.account_id)
                total_open, per_symbol = count_positions(detail)
                if total_open >= MAX_TOTAL_POSITIONS:
                    print(f"🛑 Reached {MAX_TOTAL_POSITIONS} positions — stopping this cycle", flush=True)
                    break
                time.sleep(1)

        except Exception as e:
            print(f"❌ Main loop error: {e}", flush=True)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
