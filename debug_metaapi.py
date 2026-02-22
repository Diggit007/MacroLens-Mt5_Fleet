import asyncio
import os
from datetime import datetime, timedelta, timezone
from backend.core.meta_api_client import meta_api_singleton
from backend.config import settings

ACCOUNT_ID = "d68d7eff-3751-48cd-923f-27314fab1e64"
SYMBOL = "EURUSD"

async def test_connection():
    print(f"--- Testing MetaApi Connection (v2) ---")
    
    try:
        connection = await meta_api_singleton.get_rpc_connection(ACCOUNT_ID)
        print("   Connected.")
        
        # 1. Check Symbol Spec (Does it exist?)
        print(f"1. Checking {SYMBOL} specification...")
        try:
            spec = await connection.get_symbol_specification(SYMBOL)
            if spec:
                print(f"   ✅ Symbol Found: {spec.get('symbol')} (Digits: {spec.get('digits')})")
            else:
                print(f"   ❌ Symbol {SYMBOL} not found in broker list.")
        except Exception as e:
            print(f"   ⚠️ Spec Fetch Failed: {e}")

        # 2. Fetch Historical Candles
        print(f"2. Fetching Historical Candles for {SYMBOL}...")
        
        start_time = datetime.now(timezone.utc) - timedelta(hours=24)
        print(f"   Start Time: {start_time}")
        
        if hasattr(connection, 'get_historical_candles'):
            candles = await connection.get_historical_candles(SYMBOL, "1h", start_time, 10)
            print(f"   Result: {len(candles)} candles fetched.")
            if candles:
                print(f"   Sample: {candles[0]}")
        else:
            print("   ❌ Connection object has no 'get_historical_candles' method.")
            print(f"   Methods: {dir(connection)}")
            
    except Exception as e:
        print(f"❌ ERROR: {e}")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(test_connection())
