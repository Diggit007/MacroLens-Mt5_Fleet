
import os
import sys
import pandas as pd
import yfinance as yf
import matplotlib.pyplot as plt
from datetime import datetime

# Path setup
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))
from backend.services.usd_index.index_engine import USDIndexEngine

def fetch_dxy(start_date):
    print(f"Fetching DXY from {start_date}...")
    try:
        dxy = yf.download("DX-Y.NYB", start=start_date, progress=False)
        if dxy.empty: return pd.Series()
        
        # Robust MultiIndex handling
        if isinstance(dxy.columns, pd.MultiIndex):
            if 'Adj Close' in dxy.columns.get_level_values(0):
                return dxy.xs('Adj Close', level=0, axis=1).iloc[:, 0]
            if 'Close' in dxy.columns.get_level_values(0):
                return dxy.xs('Close', level=0, axis=1).iloc[:, 0]
        
        if 'Adj Close' in dxy.columns: return dxy['Adj Close']
        if 'Close' in dxy.columns: return dxy['Close']
        return dxy.iloc[:, 0]
    except Exception as e:
        print(f"DXY Fetch Error: {e}")
        return pd.Series()

def main():
    # 1. Run Engine
    print("Running USD Index Engine...")
    engine = USDIndexEngine()
    df = engine.run_pipeline()
    
    if df.empty:
        print("Engine returned empty DataFrame.")
        return

    # 2. Get DXY
    start_date = df.index.min().strftime('%Y-%m-%d')
    dxy = fetch_dxy(start_date)
    
    # 3. Align
    common = df.index.intersection(dxy.index)
    index_series = df.loc[common, 'composite_index']
    dxy_series = dxy.loc[common]
    
    # 4. Plot
    fig, ax1 = plt.subplots(figsize=(14, 7))

    color = 'tab:red'
    ax1.set_xlabel('Date')
    ax1.set_ylabel('DXY Price', color=color)
    ax1.plot(dxy_series.index, dxy_series, color=color, label='Actual DXY', linewidth=1.5)
    ax1.tick_params(axis='y', labelcolor=color)

    ax2 = ax1.twinx()  # instantiate a second axes that shares the same x-axis
    color = 'tab:blue'
    ax2.set_ylabel('USD Composite Index (Z-Score)', color=color)  # we already handled the x-label with ax1
    ax2.plot(index_series.index, index_series, color=color, label='Composite Index', linewidth=1.5, linestyle='--')
    ax2.tick_params(axis='y', labelcolor=color)

    plt.title("Visual Comparison: Actual DXY vs Composite Fundamental Index (v2)")
    fig.tight_layout()  # otherwise the right y-label is slightly clipped
    
    output_file = "current_index_vs_dxy.png"
    plt.savefig(output_file)
    print(f"Comparison chart saved to {output_file}")

if __name__ == "__main__":
    main()
