import asyncio
import json
import os
import sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Add backend to path
sys.path.append(str(Path(__file__).parent.parent.parent))

# Load Env
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

from backend.core.meta_api_client import meta_api_singleton
# Import get_account from metaapi_service to handle the wrapper dict logic
from backend.services.metaapi_service import fetch_candles, get_account
from backend.config import settings

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = ["XAUUSD"]
TIMEFRAMES = ["M5", "H1", "D1"] # only needed ones
LIMIT = 1000 

async def main():
    target_date = datetime(2025, 11, 15, 12, 0, 0)
    
    print(f"--- BLIND TEST DATA FETCHER ---")
    print(f"Target End Date: {target_date}")
    
    account_id = "b2cf8a7d-2d81-477e-9bdf-3cc4dd1832df"

    for symbol in SYMBOLS:
        print(f"\nProcessing {symbol}...")
        for tf in TIMEFRAMES:
            print(f"  Fetching {tf} ending {target_date}...", end="", flush=True)
            try:
                candles = await fetch_candles_custom(account_id, symbol, tf, target_date, LIMIT)
                
                if candles:
                    filename = DATA_DIR / f"{symbol}_{tf}.json"
                    serializable_candles = []
                    for c in candles:
                        c_dict = c if isinstance(c, dict) else c.to_dict()
                        if 'time' in c_dict and not isinstance(c_dict['time'], str):
                             if hasattr(c_dict['time'], 'isoformat'):
                                 c_dict['time'] = c_dict['time'].isoformat()
                        serializable_candles.append(c_dict)

                    with open(filename, "w") as f:
                        json.dump(serializable_candles, f, indent=2)
                    print(f" Done. Saved {len(candles)} records.")
                else:
                    print(" Failed.")
            except Exception as e:
                print(f" Error: {e}")
    
    # Close singleton - tricky as get_account might have opened it
    # But meta_api_client usually handles this.
    try:
        await meta_api_singleton.close()
    except:
        pass

async def fetch_candles_custom(account_id, symbol, timeframe, end_time, limit):
    # Use service wrapper to get correct objects
    data = await get_account(account_id)
    connection = data['connection']
    account = data.get('account') 
    
    tf_map = {"M5": "5m", "H1": "1h", "D1": "1d"}
    meta_tf = tf_map.get(timeframe, "1h")
    
    # Try Multiple Methods (Robust)
    try:
        if hasattr(connection, 'get_historical_candles'):
            return await connection.get_historical_candles(symbol, meta_tf, end_time, limit)
        elif hasattr(connection, 'get_candles'):
            return await connection.get_candles(symbol, meta_tf, end_time, limit)
        # Fallback to REST
        elif account and hasattr(account, 'get_historical_candles'):
             return await account.get_historical_candles(symbol, meta_tf, end_time, limit)
    except Exception as e:
         print(f"Fetch Method Failed: {e}")
         return []
    return []

if __name__ == "__main__":
    asyncio.run(main())
