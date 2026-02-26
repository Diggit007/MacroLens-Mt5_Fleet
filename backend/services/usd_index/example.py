
# Example: Using the USD Index Service

import os
# Ensure FRED API Key is set
# os.environ["FRED_API_KEY"] = "your_key"

from backend.services.usd_index.index_engine import USDIndexEngine
import matplotlib.pyplot as plt

def main():
    print("Initializing Engine...")
    engine = USDIndexEngine()
    
    print("Running Pipeline (Fetching FRED data)...")
    df = engine.run_pipeline()
    
    if df is None or df.empty:
        print("No data returned. Check API Key or Log.")
        return

    print("Pipeline Complete.")
    print(f"Latest Index Value ({df.index[-1].date()}): {df['composite_index'].iloc[-1]:.4f}")
    print(f"Signal: {df['signal_label'].iloc[-1]}")
    
    # Plotting
    plt.figure(figsize=(12, 6))
    plt.plot(df.index, df['composite_index'], label='USD Fundamental Index')
    
    # Add thresholds
    plt.axhline(1.0, color='g', linestyle='--', label='Buy Threshold')
    plt.axhline(-1.0, color='r', linestyle='--', label='Sell Threshold')
    plt.axhline(0, color='k', linestyle='-', alpha=0.3)
    
    plt.title("USD Composite Fundamental Index")
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    main()
