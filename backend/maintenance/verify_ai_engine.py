import sys
import os
import logging
from unittest.mock import MagicMock

# Ensure backend in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# FIX: Set Env Vars for Pydantic Settings
os.environ["META_API_TOKEN"] = "dummy_meta_token"
os.environ["META_API_ACCOUNT_ID"] = "dummy_account_id"
os.environ["OPENAI_API_KEY"] = "dummy_openai_key"

from backend.services.ai_engine import AIEngine

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("AIVerifier")

def test_ai_prompt_generation():
    logger.info(">>> STARTING AI ENGINE VERIFICATION <<<")
    
    # 1. Setup Mock Engine
    mock_client = MagicMock()
    ai_engine = AIEngine("dummy_key", mock_client)
    
    # 2. Prepare Mock Data
    symbol = "EURUSD"
    current_price = 1.1050
    
    # Multi-TF Data matrix (simplified)
    tf_data = {
        "D1": {"structure": "BULLISH", "rsi": 60, "patterns": []},
        "H4": {"structure": "BULLISH", "rsi": 55, "patterns": []},
        "H1": {"structure": "RANGING", "rsi": 50, "patterns": []},
        "M15": {"structure": "BULLISH", "rsi": 45, "patterns": []},
        "M5": {"structure": "BULLISH", "rsi": 40, "patterns": []}
    }
    
    calendar = [] # Empty for now
    behavior_report = "TEST_BEHAVIOR_REPORT_CONTENT"
    institutional_bias = "TEST_INSTITUTIONAL_BIAS_CONTENT"
    retail_sentiment = "TEST_RETAIL_SENTIMENT_CONTENT"
    
    risk_params = {
        "atr_val": 0.0020,
        "buy_sl": 1.1020, "buy_tp": 1.1100,
        "sell_sl": 1.1080, "sell_tp": 1.1000
    }
    
    # Mock PA Score
    pa_score = {
        "score": 5,
        "bias": "BUY",
        "reason": "Perfect Bullish Setup"
    }
    
    # 3. Generate Prompt (Using construct_prompt)
    logger.info("\n[1] Testing Prompt Construction...")
    try:
        prompt = ai_engine.construct_prompt(
            symbol, tf_data, calendar, behavior_report,
            institutional_bias, retail_sentiment, risk_params,
            confluence_score=None,
            price_action_score=pa_score
        )
        
        logger.info(f"    Prompt Length: {len(prompt)} chars")
        
        # 4. Assertions
        required_strings = [
            "Symbol: EURUSD",
            "TEST_BEHAVIOR_REPORT_CONTENT",
            "TEST_INSTITUTIONAL_BIAS_CONTENT",
            "TEST_RETAIL_SENTIMENT_CONTENT",
            "PRICE ACTION ENGINE (Backtested Strategy)",
            "SCORE: 5/5",
            "BIAS: BUY",
            "!!! PRICE ACTION OVERRIDE ACTIVE !!!" # Should be present for score 5
        ]
        
        all_passed = True
        for s in required_strings:
            if s in prompt:
                logger.info(f"    PASSED: Found '{s}'")
            else:
                logger.error(f"    FAILED: Missing '{s}'")
                all_passed = False
                
        if all_passed:
            logger.info("    SUCCESS: Prompt structure valid.")
            
    except Exception as e:
        logger.error(f"    CRITICAL ERROR: {e}")

    logger.info("\n>>> VERIFICATION COMPLETE <<<")

if __name__ == "__main__":
    test_ai_prompt_generation()
