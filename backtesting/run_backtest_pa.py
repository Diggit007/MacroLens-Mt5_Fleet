import json
import os
import sys
import pandas as pd
from datetime import datetime
from pathlib import Path

# Add backend to path
sys.path.append(str(Path(__file__).parent.parent.parent))

from backend.services.technical_analysis import TechnicalAnalyzer, SymbolBehaviorAnalyzer

DATA_DIR = Path(__file__).parent / "data"

def load_data(symbol, tf):
    path = DATA_DIR / f"{symbol}_{tf}.json"
    if not path.exists():
        return []
    with open(path, "r") as f:
        data = json.load(f)
    for c in data:
        try:
             c['dt'] = pd.to_datetime(c['time'])
        except:
             c['dt'] = datetime.fromisoformat(c['time'].replace('Z', '+00:00'))
    return data

def get_snapshot(candles, current_time, count=100):
    subset = [c for c in candles if c['dt'] <= current_time]
    if not subset: return []
    return subset[-count:]

def main():
    symbol = "EURUSD"
    print(f"--- Pure Price Action Backtest (No Indicators) ---")
    
    tfs = ["M5", "M15", "H1", "H4", "D1"]
    data = {}
    for tf in tfs:
        data[tf] = load_data(symbol, tf)

    if not data["M5"]:
        print("No M5 data. Run fetch_data.py first.")
        return

    m5_data = data["M5"]
    start_idx = 100
    end_idx = len(m5_data)
    
    behavior_engine = SymbolBehaviorAnalyzer()
    
    print(f"\n{'TIME':<25} | {'PRICE':<10} | {'D1 TREND':<10} | {'M5 WICK PRES':<15} | {'M5 DOMINANCE':<15} | {'SIGNAL'}")
    print("-" * 110)
    
    for i in range(start_idx, end_idx):
        current_candle = m5_data[i]
        curr_time = current_candle['dt']
        curr_price = current_candle['close']
        
        snapshots = {}
        analyzers = {}
        dna = {}
        
        for tf in tfs:
            snapshots[tf] = get_snapshot(data[tf], curr_time)
            analyzers[tf] = TechnicalAnalyzer(snapshots[tf])
            # DNA Analysis (Last 24 candles by default)
            dna[tf] = behavior_engine.analyze(snapshots[tf], symbol, tf)
            
        # --- PURE PRICE ACTION LOGIC ---
        
        # 1. Trend (D1 Structure + Momentum)
        d1_struct = analyzers["D1"].get_market_structure()
        d1_dna = dna["D1"]
        is_bullish_trend = "BULLISH" in d1_struct or d1_dna['power_analysis']['dominance'] == 'BUYERS'
        is_bearish_trend = "BEARISH" in d1_struct or d1_dna['power_analysis']['dominance'] == 'SELLERS'
        
        # 2. Entry Trigger (M5 DNA)
        m5_dna = dna["M5"]
        wick_pres = m5_dna['wick_pressure']['pressure'] # BULLISH or BEARISH
        wick_ratio = m5_dna['wick_pressure']['ratio']
        dominance = m5_dna['power_analysis']['dominance']
        
        signal = "WAIT"
        
        # LOGIC: 
        # BUY IF: Trend is Bullish AND (M5 showed Bullish Wick Rejection OR Buyer Dominance)
        # We want to see "Demand" entering.
        
        if is_bullish_trend:
            # Setup A: Price Rejection (Long Bottom Wicks absorbing selling)
            if wick_pres == 'BULLISH' and wick_ratio < 0.6: # Bottom wicks > Top wicks significantly
                 signal = "BUY (Wick Rejection)"
            
            # Setup B: Momentum Continuation (Buyers stronger than Sellers)
            elif dominance == 'BUYERS':
                 signal = "BUY (Flow Continuation)"
                 
        if is_bearish_trend:
             # Setup A: Price Rejection (Long Top Wicks rejecting high prices)
             if wick_pres == 'BEARISH' and wick_ratio > 1.4: # Top wicks > Bottom wicks significantly
                 signal = "SELL (Wick Rejection)"
             
             # Setup B: Momentum Continuation
             elif dominance == 'SELLERS':
                 signal = "SELL (Flow Continuation)"

        # Only print hourly or signals
        is_hourly = (i % 12 == 0)
        
        if is_hourly or "BUY" in signal or "SELL" in signal:
            print(f"{str(curr_time):<25} | {curr_price:<10.2f} | {d1_struct:<10} | {wick_pres + f' ({wick_ratio})':<15} | {dominance:<15} | {signal}")

if __name__ == "__main__":
    main()
