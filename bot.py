import os
import time
from tickerall import Tickerall

# ---- Load secrets from environment (set in Deplexo) ----
API_KEY = os.getenv("TICKERALL_API_KEY")
SERVER = os.getenv("EXNESS_SERVER")
ACCOUNT = int(os.getenv("EXNESS_ACCOUNT", "0"))
PASSWORD = os.getenv("EXNESS_PASSWORD")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))
GOLD_SYMBOL = "XAUUSDm"  # Confirmed from your account

def connect():
    client = Tickerall(api_key=API_KEY)
    session = client.sessions.start(
        broker="mt5",
        server=SERVER,
        account=ACCOUNT,
        password=PASSWORD,
    )
    return client, session

def main():
    print("🚀 Bot starting...", flush=True)
    client, session = connect()
    print(f"✅ Connected: {session.account_id} (demo={session.is_demo})", flush=True)

    # ---- ONE-TIME GOLD TEST ORDER ----
    try:
        print(f"🎯 Placing test order: BUY {GOLD_SYMBOL} 0.01 lots...", flush=True)
        order = client.orders.place(
            session.account_id,
            type="market",
            symbol=GOLD_SYMBOL,
            side="BUY",
            volume=0.01,
        )
        print(f"🎯 Order placed! Ticket: {order.ticket} | Status: {order.status}", flush=True)
    except Exception as e:
        print(f"❌ Order failed: {e}", flush=True)

    # ---- BALANCE MONITORING LOOP ----
    while True:
        try:
            detail = client.accounts.get(session.account_id)
            if detail.account:
                print(
                    f"💰 Balance: {detail.account.balance} {detail.account.currency} "
                    f"| Equity: {detail.account.equity}",
                    flush=True,
                )
            else:
                print("⚠️ Account offline — no data this cycle", flush=True)
        except Exception as e:
            print(f"❌ Read error: {e}", flush=True)
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()