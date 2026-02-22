import asyncio
import os
from datetime import datetime, timedelta, timezone
from backend.core.meta_api_client import meta_api_singleton
from backend.config import settings

ACCOUNT_ID = "d68d7eff-3751-48cd-923f-27314fab1e64"
SYMBOL = "EURUSD"

async def test_account_methods():
    print(f"--- Testing Account Object (v3) ---")
    
    try:
        # Get Account Object (not RPC connection)
        account = await meta_api_singleton.get_account(ACCOUNT_ID)
        print(f"   Account Retrieved: {account.id} ({account.state})")
        
        # Check Methods
        print(f"   Checking 'get_historical_candles' on account object...")
        if hasattr(account, 'get_historical_candles'):
            print("   ✅ Method Exists!")
            
            start_time = datetime.now(timezone.utc) - timedelta(hours=24)
            candles = await account.get_historical_candles(SYMBOL, "1h", start_time, 10)
            print(f"   Result: {len(candles)} candles fetched.")
            if candles:
                print(f"   Sample: {candles[0]}")
        else:
            print("   ❌ Method 'get_historical_candles' NOT FOUND on account object.")
            print(f"   Available: {dir(account)}")

    except Exception as e:
        print(f"❌ ERROR: {e}")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(test_account_methods())
