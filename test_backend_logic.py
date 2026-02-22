import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from backend.main import fetch_bridge_candles
from backend.config import settings

USER_ID = "0AoEssZoiOVVFZBLmKFXGE5Z1M82"
SYMBOL = "EURUSD"

async def test_logic():
    print(f"--- Testing Backend Logic for {USER_ID} ---")
    print(f"Settings Account ID: {settings.META_API_ACCOUNT_ID}")
    
    try:
        print(f"Fetching candles for {SYMBOL}...")
        candles = await fetch_bridge_candles(USER_ID, SYMBOL, "H1", 10)
        
        if candles:
            print(f"✅ Success! Fetched {len(candles)} candles.")
            print(f"Sample: {candles[0]}")
        else:
            print("❌ Fetched 0 candles.")
            
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    # Initialize verify mode? No, main.py init should handle basics?
    # Actually main.py initialization might attempt to start servers/listeners which is bad.
    # But checking fetch_bridge_candles only depends on resolution & metaapi.
    
    # We need to manually initialize settings/MetaApi because main.py does it globally
    # but we are importing a function.
    
    loop = asyncio.get_event_loop()
    loop.run_until_complete(test_logic())
