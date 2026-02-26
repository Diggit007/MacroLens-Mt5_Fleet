import asyncio
import logging
import sys
import os
from datetime import datetime

# Ensure backend is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("DataVerifier")

# FIX: Set correct DB Path before importing database module
# MUST match the location of backend/market_data.db
db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_data.db")
os.environ["DB_PATH"] = db_path
logger.info(f"Setting DB_PATH to: {db_path}")

# Import AFTER setting env var
from backend.services.agent_service import MacroLensAgentV2
from backend.core.database import DatabasePool

async def mock_fetch_callback(symbol: str, timeframe: str):
    """
    Mock fetch callback to simulate MetaAPI data.
    Returns dummy OHLCV data.
    """
    logger.info(f"[-] fetch_callback triggered for {symbol} on {timeframe}")
    
    # Generate 50 dummy candles
    candles = []
    base_price = 1.1000
    for i in range(50):
        candles.append({
            "time": datetime.utcnow(), # simplified
            "open": base_price,
            "high": base_price + 0.0010,
            "low": base_price - 0.0010,
            "close": base_price + 0.0005,
            "volume": 100
        })
    return candles

async def test_data_integrity():
    logger.info(">>> STARTING DATA INTEGRITY CHECK <<<")
    
    agent = MacroLensAgentV2()
    
    try:
        # 1. Test Fetch Callback Integration
        logger.info("\n[1] Testing Fetch Callback Integration (M5, M15, H1, H4, D1)...")
        # We process a single request, which should trigger the callback for all TFs
        # We pass a user_id to avoid errors if progress reporting tries to run
        result = await agent.process_single_request(
            symbol="EURUSD", 
            timeframe="H1", 
            fetch_callback=mock_fetch_callback,
            user_id="test_user" 
        )
        
        # Check if technical data was populated (implies fetch worked)
        if "technical_data" in result:
            tfs = result["technical_data"].keys()
            logger.info(f"    SUCCESS: Fetched timeframes: {list(tfs)}")
            if all(tf in tfs for tf in ["M5", "M15", "H1", "H4", "D1"]):
                 logger.info("    PASSED: All required timeframes present.")
            else:
                 logger.error("    FAILED: Missing timeframes.")
        else:
            logger.error("    FAILED: No technical_data in result.")

        # 2. Test Economic Events (Database)
        logger.info("\n[2] Testing Economic Events Retrieval...")
        try:
             # Just check if it runs without crashing, data might be empty depending on DB state
             events = await agent.get_upcoming_events("USD")
             logger.info(f"    Result: Retrieved {len(events)} high-impact USD events.")
             if events:
                 logger.info(f"    Sample: {events[0]}")
             else:
                 logger.warning("    NOTE: No upcoming events found (Database likely empty or no events in 2h window).")
             logger.info("    PASSED: Function execution successful.")
        except Exception as e:
            logger.error(f"    FAILED: {e}")

        # 3. Test Institutional Bias (News Retriever)
        logger.info("\n[3] Testing Institutional Bias (News Retriever)...")
        try:
            # This calls external APIs (or prompts logic), might fail if no keys or data
            bias = await agent.get_institutional_bias("EURUSD")
            logger.info(f"    Result Length: {len(bias)} chars")
            logger.info(f"    Sample: {bias[:100]}...")
            logger.info("    PASSED: Function execution successful.")
        except Exception as e:
            logger.error(f"    FAILED: {e}")

    except Exception as e:
        logger.error(f"CRITICAL FAILURE: {e}")
    finally:
        await agent.close()
        await DatabasePool.close()
        logger.info("\n>>> VERIFICATION COMPLETE <<<")

if __name__ == "__main__":
    asyncio.run(test_data_integrity())
