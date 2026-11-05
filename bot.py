import os
import time
import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np
from tickerall import Tickerall

# ==========================================
# 1. CONFIGURATION (Load from Environment Variables)
# ==========================================
API_KEY = os.getenv("TICKERALL_API_KEY")
SERVER = os.getenv("EXNESS_SERVER")
ACCOUNT = int(os.getenv("EXNESS_ACCOUNT", "0"))
PASSWORD = os.getenv("EXNESS_PASSWORD")

# Risk Management
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.01")) # 1% of account
MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", "0.05")) # 5% max daily loss
GOTTOBI_AMPLIFIER = float(os.getenv("GOTTOBI_AMPLIFIER", "1.5")) # Increase size by 50% on Gotobi

# General Loop Settings
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "60")) # Check every 60 seconds

# Tokyo Timezone Setup (Crucial for Strategy 1)
TOKYO_TZ = ZoneInfo("Asia/Tokyo")

# ==========================================
# 2. RISK MANAGER
# ==========================================
class RiskManager:
    @staticmethod
    def calculate_atr(df, period=14):
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        return true_range.rolling(period).mean().iloc[-1]

    @staticmethod
    def calculate_volume(balance, risk_pct, sl_distance, spec, amplifier=1.0):
        # Value per point movement for 1 lot
        point_value = spec.tick_value / spec.tick_size
        risk_amount = balance * risk_pct
        
        # Volume = Risk $ / (SL Distance in points * Point Value)
        sl_points = sl_distance / spec.point
        raw_volume = risk_amount / (sl_points * point_value)
        
        # Apply amplifier (e.g., Gotobi) and round to nearest 0.01
        adjusted_volume = raw_volume * amplifier
        volume = max(spec.volume_min, round(adjusted_volume, 2))
        return min(volume, spec.volume_max)

# ==========================================
# 3. DATA FEED
# ==========================================
class DataFeed:
    def __init__(self, client, account_id):
        self.client = client
        self.account_id = account_id

    def get_candles_df(self, symbol, timeframe="M5", hours=24):
        try:
            bars = self.client.candles.get(
                self.account_id, symbol=symbol, hours=hours, timeframe=timeframe
            )
            data = [{"time": b.time, "open": b.open, "high": b.high, "low": b.low, "close": b.close} for b in bars]
            df = pd.DataFrame(data)
            return df
        except Exception as e:
            print(f"❌ Data fetch error for {symbol}: {e}", flush=True)
            return pd.DataFrame()

# ==========================================
# 4. STRATEGIES
# ==========================================
class StrategyTokyoFix:
    @staticmethod
    def is_gotobi(dt):
        # Japanese dates ending in 0 or 5
        return dt.day % 10 in [0, 5]

    @staticmethod
    def check_signal(df, current_time):
        """
        Expects M1 (1-minute) data for USDJPY or GBPJPY.
        Checks if it's time to trade.
        """
        if df.empty:
            return None, 1.0
            
        # Filter for today's data
        df['dt'] = pd.to_datetime(df['time'], unit='s', utc=True).dt.tz_convert(TOKYO_TZ)
        today = current_time.date()
        today_df = df[df['dt'].dt.date == today]
        
        if today_df.empty:
            return None, 1.0

        # Get 08:30 and 09:55 prices
        price_0830 = today_df[today_df['dt'].dt.strftime('%H:%M') == '08:30']['close']
        price_0955 = today_df[today_df['dt'].dt.strftime('%H:%M') == '09:55']['close']
        
        if price_0830.empty or price_0955.empty:
            return None, 1.0

        p_0830 = price_0830.iloc[0]
        p_0955 = price_0955.iloc[0]
        
        # Only trigger exactly at 09:55
        if current_time.strftime('%H:%M') == '09:55':
            if p_0955 > p_0830:
                # Price went up, we fade (SELL)
                amplifier = GOTTOBI_AMPLIFIER if StrategyTokyoFix.is_gotobi(current_time) else 1.0
                print(f"🎯 Tokyo Fix Signal: SELL (0830: {p_0830}, 0955: {p_0955}) | Gotobi: {amplifier > 1.0}", flush=True)
                return "SELL", amplifier
                
        return None, 1.0

class StrategyGoldMA:
    @staticmethod
    def check_signal(df):
        """
        Expects M5 data. Checks 44/332 SMA crossover.
        """
        if len(df) < 333:
            return None # Not enough data
            
        df['SMA_44'] = df['close'].rolling(window=44).mean()
        df['SMA_332'] = df['close'].rolling(window=332).mean()
        
        # Check crossover on the last closed candle
        prev_fast = df['SMA_44'].iloc[-2]
        prev_slow = df['SMA_332'].iloc[-2]
        curr_fast = df['SMA_44'].iloc[-1]
        curr_slow = df['SMA_332'].iloc[-1]
        
        if prev_fast <= prev_slow and curr_fast > curr_slow:
            print("🎯 Gold MA Signal: BUY (44 crossed above 332)", flush=True)
            return "BUY"
        elif prev_fast >= prev_slow and curr_fast < curr_slow:
            print("🎯 Gold MA Signal: SELL (44 crossed below 332)", flush=True)
            return "SELL"
            
        return None

# ==========================================
# 5. EXECUTION ENGINE
# ==========================================
class TradingEngine:
    def __init__(self):
        self.client = Tickerall(api_key=API_KEY)
        self.session = None
        self.specs = {}
        self.daily_start_balance = 0.0
        self.last_trade_date = None
        self.active_time_exits = {} # Track trades that need a time-based exit

    def connect(self):
        print("🚀 Connecting to Exness via TickerAll...", flush=True)
        self.session = self.client.sessions.keep_alive(
            broker="mt5", server=SERVER, account=ACCOUNT, password=PASSWORD
        )
        self.specs = {s.name: s for s in self.client.accounts.symbol_specs(self.session.account_id)}
        detail = self.client.accounts.get(self.session.account_id)
        self.daily_start_balance = detail.account.balance
        print(f"✅ Connected. Balance: {self.daily_start_balance} {detail.account.currency}", flush=True)

    def get_open_positions(self):
        detail = self.client.accounts.get(self.session.account_id)
        return detail.positions if detail.positions else []

    def check_daily_loss(self):
        detail = self.client.accounts.get(self.session.account_id)
        current_equity = detail.account.equity
        loss_pct = (self.daily_start_balance - current_equity) / self.daily_start_balance
        if loss_pct >= MAX_DAILY_LOSS:
            print(f"🛑 MAX DAILY LOSS REACHED ({loss_pct*100:.2f}%). Halting trading.", flush=True)
            return True
        return False

    def execute_trade(self, symbol, side, df, strategy_name, amplifier=1.0):
        if self.check_daily_loss():
            return

        # Prevent overlapping positions
        open_positions = self.get_open_positions()
        if any(p.symbol == symbol for p in open_positions):
            print(f"⏸️ {symbol}: Position already open. Skipping {strategy_name}.", flush=True)
            return

        spec = self.specs[symbol]
        atr = RiskManager.calculate_atr(df, period=14)
        
        if pd.isna(atr) or atr == 0:
            print(f"❌ {symbol}: ATR calculation failed.", flush=True)
            return

        # Risk logic: SL = 1.5x ATR, TP = 2.0x ATR
        sl_dist = atr * 1.5
        tp_dist = atr * 2.0
        entry_price = df['close'].iloc[-1]

        if side == "BUY":
            sl = round(entry_price - sl_dist, spec.digits)
            tp = round(entry_price + tp_dist, spec.digits)
        else: # SELL
            sl = round(entry_price + sl_dist, spec.digits)
            tp = round(entry_price - tp_dist, spec.digits)

        volume = RiskManager.calculate_volume(self.daily_start_balance, RISK_PER_TRADE, sl_dist, spec, amplifier)

        print(f"📤 Placing {side} {volume} lots on {symbol} | Entry: {entry_price} | SL: {sl} | TP: {tp}", flush=True)
        
        try:
            order = self.client.orders.place(
                self.session.account_id,
                type="market",
                symbol=symbol,
                side=side,
                volume=volume,
                stop_loss=sl,
                take_profit=tp
            )
            print(f"✅ Order successful! Ticket: {order.ticket}", flush=True)
            
            # Register time-exit for Tokyo Fix (exit 60 mins later)
            if strategy_name == "TokyoFix":
                self.active_time_exits[symbol] = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=60)
                print(f"⏱️ Time-exit registered for {symbol} in 60 minutes.", flush=True)
                
        except Exception as e:
            print(f"❌ Order execution failed for {symbol}: {e}", flush=True)

    def manage_time_exits(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        to_remove = []
        open_positions = self.get_open_positions()

        for symbol, exit_time in self.active_time_exits.items():
            if now >= exit_time:
                print(f"⏰ Time exit reached for {symbol}. Closing position.", flush=True)
                for p in open_positions:
                    if p.symbol == symbol:
                        try:
                            self.client.positions.close(self.session.account_id, ticket=p.ticket)
                            print(f"✅ Closed {symbol} ticket {p.ticket}", flush=True)
                        except Exception as e:
                            print(f"❌ Failed to close {symbol}: {e}", flush=True)
                to_remove.append(symbol)
        
        for sym in to_remove:
            del self.active_time_exits[sym]

    def run(self):
        self.connect()
        feed = DataFeed(self.client, self.session.account_id)

        while True:
            try:
                now_jst = datetime.datetime.now(TOKYO_TZ)
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                
                # Reset daily balance tracker at JST midnight
                if self.last_trade_date != now_jst.date():
                    self.last_trade_date = now_jst.date()
                    self.daily_start_balance = self.client.accounts.get(self.session.account_id).account.balance
                    print(f"🌅 New Day. Reset daily loss limit. Balance: {self.daily_start_balance}", flush=True)

                # 1. Manage Exits First
                self.manage_time_exits()

                # 2. Strategy 1: Tokyo Fix (USDJPY, GBPJPY) - Only run Mon-Fri
                if now_jst.weekday() < 5: 
                    for fx_pair in ["USDJPYm", "GBPJPYm"]:
                        # We only need M1 data for this specific intraday strategy
                        df_m1 = feed.get_candles_df(fx_pair, timeframe="M1", hours=12)
                        signal, amplifier = StrategyTokyoFix.check_signal(df_m1, now_jst)
                        if signal:
                            self.execute_trade(fx_pair, signal, df_m1, "TokyoFix", amplifier)

                # 3. Strategy 2: Gold 44/332 MA (XAUUSDm)
                # We fetch M5 data. 332 periods * 5m = ~28 hours of data needed.
                df_m5 = feed.get_candles_df("XAUUSDm", timeframe="M5", hours=48)
                signal = StrategyGoldMA.check_signal(df_m5)
                if signal:
                    self.execute_trade("XAUUSDm", signal, df_m5, "GoldMA")

                print(f"💤 Cycle complete. Sleeping for {POLL_INTERVAL}s...", flush=True)
                time.sleep(POLL_INTERVAL)

            except Exception as e:
                print(f"❌ Main Loop Error: {e}", flush=True)
                time.sleep(10)
                # Attempt reconnect on main loop crash
                try:
                    self.connect()
                except:
                    pass

if __name__ == "__main__":
    engine = TradingEngine()
    engine.run()