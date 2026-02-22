import json
import os
import sys
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

# Add backend to path
sys.path.append(str(Path(__file__).parent.parent.parent))

from backend.services.technical_analysis import TechnicalAnalyzer, SymbolBehaviorAnalyzer

DATA_DIR = Path(__file__).parent / "data"

class BacktestEngine:
    def __init__(self, symbol, balance=10000.0, lot_size=1.0):
        self.symbol = symbol
        self.initial_balance = balance
        self.balance = balance
        self.lot_size = lot_size
        self.trades = []
        self.open_trades = []
        self.history = [] # Equity curve
        self.data = {}
        self.behavior_engine = SymbolBehaviorAnalyzer()
        
    def load_data(self):
        tfs = ["M5", "M15", "H1", "H4", "D1"]
        print(f"Loading data for {self.symbol}...")
        found = False
        for tf in tfs:
            path = DATA_DIR / f"{self.symbol}_{tf}.json"
            if path.exists():
                with open(path, "r") as f:
                    raw = json.load(f)
                # Parse Dates
                for c in raw:
                    try:
                        c['dt'] = pd.to_datetime(c['time'])
                    except:
                        c['dt'] = datetime.fromisoformat(c['time'].replace('Z', '+00:00'))
                self.data[tf] = raw
                found = True
        return found
        
    def get_snapshot(self, tf, current_time, count=100):
        # Optimization: Last subset
        # In a real engine, we'd use indices. Here filter is OK for small data.
        candles = self.data.get(tf, [])
        subset = [c for c in candles if c['dt'] <= current_time]
        if not subset: return []
        return subset[-count:]

    def run(self):
        if not self.load_data():
            print(f"No data found for {self.symbol}. Run fetch_data.py first.")
            return

        m5_data = self.data["M5"]
        if not m5_data:
            print("No M5 data provided.")
            return

        start_time = m5_data[0]['dt']
        end_time = m5_data[-1]['dt']
        
        print(f"--- Backtest Start: {self.symbol} ---")
        print(f"Range: {start_time} to {end_time}")
        print(f"Strategy: Pure Price Action (Candle DNA)")
        print("-" * 60)
        
        # Simulation Loop
        for i in range(100, len(m5_data)):
            candle = m5_data[i]
            curr_time = candle['dt']
            curr_price = candle['close']
            high = candle['high']
            low = candle['low']
            
            # 1. Manage Open Trades (Check TP/SL)
            self._manage_trades(high, low, curr_time)
            
            # 2. Generate Signal
            signal, sl_price, tp_price = self._get_signal(curr_time, curr_price)
            
            # 3. Execute
            if signal:
                self._open_trade(signal, curr_time, curr_price, sl_price, tp_price)
                
            self.history.append({'time': curr_time, 'equity': self._get_equity(curr_price)})

        self._print_results()

    def _get_signal(self, curr_time, curr_price):
        # Multi-Timeframe Snapshot
        snapshots = {}
        analyzers = {}
        dna = {}
        
        # Added H1 for Alignment check
        for tf in ["D1", "H1", "M5"]: 
            snapshots[tf] = self.get_snapshot(tf, curr_time)
            analyzers[tf] = TechnicalAnalyzer(snapshots[tf])
            dna[tf] = self.behavior_engine.analyze(snapshots[tf], self.symbol, tf)
            
        # LOGIC (Stricter Price Action)
        d1_struct = analyzers["D1"].get_market_structure()
        h1_struct = analyzers["H1"].get_market_structure() # Filter 1
        
        d1_dna = dna["D1"]
        m5_dna = dna["M5"]
        
        # M5 RSI for Safety (Don't buy tops)
        m5_rsi = analyzers["M5"].get_rsi()
        
        is_bullish_trend = "BULLISH" in d1_struct or d1_dna['power_analysis']['dominance'] == 'BUYERS'
        is_bearish_trend = "BEARISH" in d1_struct or d1_dna['power_analysis']['dominance'] == 'SELLERS'
        
        # Filter 1: H1 Must NOT be opposing (Can be Ranging, but not Bearish in Uptrend)
        h1_agrees_bull = "BEARISH" not in h1_struct
        h1_agrees_bear = "BULLISH" not in h1_struct
        
        wick_pres = m5_dna['wick_pressure']['pressure']
        wick_ratio = m5_dna['wick_pressure']['ratio']
        dominance = m5_dna['power_analysis']['dominance']
        
        signal = None
        sl = 0.0
        tp = 0.0
        
        # Risk Settings (approx pips)
        sl_pips = 0.0
        if "XAU" in self.symbol: sl_pips = 3.0 
        elif "JPY" in self.symbol: sl_pips = 0.20
        else: sl_pips = 0.0020
        
        rr_ratio = 2.0
        
        if is_bullish_trend and h1_agrees_bull:
            # Filter 2: Don't buy if RSI > 70 (Overextended)
            if m5_rsi < 70:
                # Filter 3: Stricter Wick (0.5 instead of 0.6)
                if (wick_pres == 'BULLISH' and wick_ratio < 0.5):
                    signal = "BUY"
                # Filter 4: Stronger Flow (Must have high Bull candle count)
                elif (dominance == 'BUYERS' and m5_dna['power_analysis']['bull_candles'] >= 14): # >60%
                    signal = "BUY"
                
            if signal == "BUY":
                sl = curr_price - sl_pips
                tp = curr_price + (sl_pips * rr_ratio)
                
        elif is_bearish_trend and h1_agrees_bear:
            if m5_rsi > 30:
                if (wick_pres == 'BEARISH' and wick_ratio > 1.5): # 1.5 instead of 1.4
                    signal = "SELL"
                elif (dominance == 'SELLERS' and m5_dna['power_analysis']['bear_candles'] >= 14):
                    signal = "SELL"
            
            if signal == "SELL":
                sl = curr_price + sl_pips
                tp = curr_price - (sl_pips * rr_ratio)
                
        # Filter: Don't open if already open in same direction
        for t in self.open_trades:
            if t['type'] == signal: return None, 0, 0
            
        return signal, sl, tp

    def _open_trade(self, type_, time, price, sl, tp):
        trade = {
            'id': len(self.trades) + 1,
            'type': type_,
            'open_time': time,
            'open_price': price,
            'sl': sl,
            'tp': tp,
            'status': 'OPEN'
        }
        self.open_trades.append(trade)
        print(f"[{str(time)}] OPEN {type_} @ {price:.2f} | SL: {sl:.2f} TP: {tp:.2f}")

    def _manage_trades(self, high, low, time):
        # iterate copy
        for t in self.open_trades[:]:
            close_reason = None
            close_price = 0
            profit = 0
            
            if t['type'] == 'BUY':
                if low <= t['sl']:
                    close_reason = 'SL'
                    close_price = t['sl']
                elif high >= t['tp']:
                    close_reason = 'TP'
                    close_price = t['tp']
            elif t['type'] == 'SELL':
                if high >= t['sl']:
                    close_reason = 'SL'
                    close_price = t['sl']
                elif low <= t['tp']:
                    close_reason = 'TP'
                    close_price = t['tp']
            
            if close_reason:
                if t['type'] == 'BUY':
                    diff = close_price - t['open_price']
                else:
                    diff = t['open_price'] - close_price
                
                # Simple Profit Calc (Contract Size ignored for simplicity, just price diff * lots * 100 assumed)
                # Ideally fetch contract size. For XAUUSD, 1 lot = 100 oz. Diff $1 = $100.
                contract_size = 100 if "XAU" in self.symbol else 100000
                profit = diff * self.lot_size * contract_size
                
                self.balance += profit
                t['status'] = 'CLOSED'
                t['close_time'] = time
                t['close_price'] = close_price
                t['profit'] = profit
                t['reason'] = close_reason
                
                self.trades.append(t)
                self.open_trades.remove(t)
                print(f"[{str(time)}] CLOSE {t['type']} ({close_reason}) | PnL: ${profit:.2f} | Bal: ${self.balance:.2f}")

    def _get_equity(self, curr_price):
        floating = 0
        contract_size = 100 if "XAU" in self.symbol else 100000
        for t in self.open_trades:
             if t['type'] == 'BUY':
                 diff = curr_price - t['open_price']
             else:
                 diff = t['open_price'] - curr_price
             floating += diff * self.lot_size * contract_size
        return self.balance + floating

    def _print_results(self):
        print("\n=== BACKTEST RESULTS ===")
        total_trades = len(self.trades)
        wins = [t for t in self.trades if t['profit'] > 0]
        losses = [t for t in self.trades if t['profit'] <= 0]
        
        win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0
        total_profit = sum(t['profit'] for t in self.trades)
        
        print(f"Symbol: {self.symbol}")
        print(f"Total Trades: {total_trades}")
        print(f"Win Rate: {win_rate:.1f}%")
        print(f"Total Profit: ${total_profit:.2f}")
        print(f"Final Balance: ${self.balance:.2f} (Start: ${self.initial_balance})")
        
        if total_trades > 0:
            avg_win = sum(t['profit'] for t in wins) / len(wins) if wins else 0
            avg_loss = sum(t['profit'] for t in losses) / len(losses) if losses else 0
            print(f"Avg Win: ${avg_win:.2f} | Avg Loss: ${avg_loss:.2f}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol", nargs="?", default="XAUUSD")
    args = parser.parse_args()
    
    engine = BacktestEngine(args.symbol)
    engine.run()
