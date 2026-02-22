import asyncio
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add backend to path to import services
sys.path.append(str(Path(__file__).parent.parent.parent))

# Load Env BEFORE importing settings
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

from backend.core.meta_api_client import meta_api_singleton
from backend.services.metaapi_service import fetch_candles
from backend.config import settings

# Output Directory
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = ["EURUSD"]
TIMEFRAMES = ["M5", "M15", "H1", "H4", "D1"]
LIMIT = 1000  # Number of historical candles

async def main():
    print(f"--- Backtest Data Fetcher ---")
    print(f"Target: {DATA_DIR}")
    
    # 1. Initialize Singleton (Connects to MetaApi)
    # 1. Initialize Singleton (Connects to MetaApi)
    # Using Valid Account from Monitoring Logs
    account_id = "d68d7eff-3751-48cd-923f-27314fab1e64" 
    
    print(f"Connecting to Account {account_id}...")
    try:
        # Pre-warm connection
        await meta_api_singleton.get_account(account_id)
        print("Connected.")
    except Exception as e:
        print(f"Connection Failed: {e}")
        return

    for symbol in SYMBOLS:
        print(f"\nProcessing {symbol}...")
        for tf in TIMEFRAMES:
            print(f"  Fetching {tf} ({LIMIT} candles)...", end="", flush=True)
            try:
                # fetch_candles handles the API calls
                candles = await fetch_candles(account_id, symbol, tf, limit=LIMIT)
                
                if candles:
                    # Save to file
                    filename = DATA_DIR / f"{symbol}_{tf}.json"
                    
                    # Clean candles (remove non-serializable objects if any, though fetch_candles returns dicts?)
                    # fetch_candles currently returns MetApi objects or dicts. We need to ensure serialization.
                    serializable_candles = []
                    for c in candles:
                        c_dict = c if isinstance(c, dict) else c.to_dict()
                        # handle datetime
                        if 'time' in c_dict and not isinstance(c_dict['time'], str):
                             if hasattr(c_dict['time'], 'isoformat'):
                                 c_dict['time'] = c_dict['time'].isoformat()
                        serializable_candles.append(c_dict)

                    with open(filename, "w") as f:
                        json.dump(serializable_candles, f, indent=2)
                    print(f" Done. Saved {len(candles)} records.")
                else:
                    print(" Failed (No data returned).")
            except Exception as e:
                print(f" Error: {e}")

    print("\nAll Done.")
    # Explicitly close to prevent hanging
    await meta_api_singleton.close()

if __name__ == "__main__":
    asyncio.run(main())
