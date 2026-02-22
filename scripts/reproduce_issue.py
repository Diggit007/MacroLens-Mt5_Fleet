
import asyncio
import logging
from backend.services.agent_service import MacroLensAgentV2
from backend.services.metaapi_service import fetch_candles

# Setup minimal logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ReproduceIssue")

async def reproduce():
    agent = MacroLensAgentV2()
    symbol = "EURUSD"
    
    # Mocking the fetch_callback to print arguments
    async def mock_fetch_candles(arg1, arg2):
        print(f"DEBUG: fetch_candles called with arg1='{arg1}', arg2='{arg2}'")
        # Simulate the error if arg1 is not an account ID
        if arg1 == symbol:
            print("FAILURE: arg1 is the symbol, expected account_id!")
        else:
            print("SUCCESS: arg1 appears to be an account ID.")
        return []

    print("--- Starting Reproduction ---")
    # We invoke the logic that calls fetch_callback
    # In agent_service.py line 1052: fetch_callback(symbol, tf)
    # We expect it to fail or print FAILURE
    
    try:
        # We need to bypass some checks to get to the fetch call
        # agent.process_single_request calls _process_analysis_logic
        await agent._process_analysis_logic(
            symbol=symbol, 
            timeframe="1h", 
            fetch_callback=mock_fetch_candles,
            user_id="test_user"
        )
    except Exception as e:
        print(f"Caught expected exception or finish: {e}")

if __name__ == "__main__":
    asyncio.run(reproduce())
