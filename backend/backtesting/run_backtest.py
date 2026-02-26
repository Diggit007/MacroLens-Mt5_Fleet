import json
import os
import sys
import pandas as pd
from datetime import datetime
from pathlib import Path

# Add backend to path
sys.path.append(str(Path(__file__).parent.parent.parent))

from backend.services.technical_analysis import TechnicalAnalyzer

DATA_DIR = Path(__file__).parent / "data"

def load_data(symbol, tf):
    path = DATA_DIR / f"{symbol}_{tf}.json"
    if not path.exists():
        print(f"File not found: {path}")
        return []
    with open(path, "r") as f:
        data = json.load(f)
    # Convert time to datetime objects for easy comparison
    for c in data:
        # formats: '2026-01-23T17:30:00+00:00' or similar
        try:
             c['dt'] = pd.to_datetime(c['time'])
        except:
             c['dt'] = datetime.fromisoformat(c['time'].replace('Z', '+00:00'))
    return data

def get_snapshot(candles, current_time, count=100):
    """
    Returns the last 'count' candles that closed ON or BEFORE current_time.
    """
    # Filter: candle time <= current_time
    # Optimization: assume sorted? fetch_candles sorts usually.
    # We'll just filter for correctness first.
    
    # Bisect or simple filter? Simple filter is O(N) but safer.
    # subset = [c for c in candles if c['dt'] <= current_time] # SLOW for loop
    
    # Let's use index if possible, but list comprehension is fast enough for 1000 items.
    # Actually, 1000 items is tiny.
    subset = [c for c in candles if c['dt'] <= current_time]
    
    if not subset: return []
    return subset[-count:]

def main():
    symbol = "XAUUSD"
    print(f"--- Running Backtest for {symbol} ---")
    
    # 1. Load Data
    tfs = ["M5", "M15", "H1", "H4", "D1"]
    data = {}
    for tf in tfs:
        data[tf] = load_data(symbol, tf)
        print(f"Loaded {len(data[tf])} candles for {tf}")

    if not data["M5"]:
        print("No M5 data found. Exiting.")
        return

    # 2. Simulation Loop (Iterate M5)
    # Start from index 100 to allow history for indicators
    m5_data = data["M5"]
    start_idx = 100
    end_idx = len(m5_data)
    
    print(f"\n{'TIME':<25} | {'PRICE':<10} | {'MACRO':<10} | {'H1 STRUCT':<15} | {'H1 RSI':<6} | {'M5 RSI':<6} | {'SIGNAL'}")
    print("-" * 100)
    
    for i in range(start_idx, end_idx):
        current_candle = m5_data[i]
        curr_time = current_candle['dt']
        curr_price = current_candle['close']
        
        # Build Snapshot for each TF
        snapshots = {}
        analyzers = {}
        
        for tf in tfs:
            snapshots[tf] = get_snapshot(data[tf], curr_time)
            analyzers[tf] = TechnicalAnalyzer(snapshots[tf])
            
        # --- ANALYSIS LOGIC ---
        
        # 1. D1 Trend
        d1_struct = analyzers["D1"].get_market_structure()
        
        # 2. H1 Analysis
        h1_ta = analyzers["H1"]
        h1_struct = h1_ta.get_market_structure()
        h1_rsi = h1_ta.get_rsi()
        h1_bb = h1_ta.get_bollinger_bands()
        
        # 3. M5 (Entry) Analysis
        m5_ta = analyzers["M5"]
        m5_rsi = m5_ta.get_rsi()
        m5_patt = m5_ta.get_candle_patterns()
        
        # --- SIGNAL LOGIC: OPTION A (AGGRESSIVE TREND FOLLOWING) ---
        signal = "WAIT"
        
        # 1. Trend Direction (D1 is King)
        is_bullish_trend = "BULLISH" in d1_struct
        is_bearish_trend = "BEARISH" in d1_struct
        
        # 2. Aggressive Entry (Pullback)
        # If D1 is Bullish, we buy dips even if H1 is ranging.
        # We relax M5 RSI entry to < 45 (capture shallow pullbacks)
        
        if is_bullish_trend:
            if m5_rsi < 45: 
                signal = "BUY (Aggressive Dip)"
            elif m5_rsi < 30:
                signal = "BUY (Deep Oversold)"
                
        elif is_bearish_trend:
            if m5_rsi > 55:
                signal = "SELL (Aggressive Rally)"
            elif m5_rsi > 70:
                signal = "SELL (Deep Overbought)"

        # Print Row
        # Only print interesting rows (every 12th M5 candle = 1 hour, or on signal)
        is_hourly = (i % 12 == 0)
        
        if is_hourly or "BUY" in signal or "SELL" in signal:
            print(f"{str(curr_time):<25} | {curr_price:<10.2f} | {d1_struct:<10} | {h1_struct:<15} | {h1_rsi:<6.1f} | {m5_rsi:<6.1f} | {signal}")

if __name__ == "__main__":
    main()
