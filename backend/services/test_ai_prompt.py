
import sys
import os
import asyncio
from datetime import datetime

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from backend.services.ai_engine import AIEngine

async def test_prompt():
    print("Initializing AI Engine...")
    engine = AIEngine(api_key="test_key", http_client=None)
    
    # Mock Data
    symbol = "EURUSD"
    calendar = []
    
    # Dummy Multi-TF Data
    multi_tf = {
        "D1": {"structure": "Bearish", "rsi": 45, "patterns": ["Double Top"]},
        "H1": {"structure": "Range", "rsi": 50, "zones": {"support": [1.0500], "resistance": [1.0600]}},
        "M5": {"price": 1.0550}
    }
    
    print("Constructing Prompt...")
    prompt = engine.construct_prompt(
        symbol=symbol,
        multi_tf_data=multi_tf,
        calendar=calendar,
        behavior_report="EURUSD tends to respect psychological levels.",
        institutional_bias="Bearish on Euro due to weak growth.",
        retail_sentiment="Retail is 70% Long (Contrarian Short).",
        risk_params={"atr_val": 0.005, "buy_sl": 1.0500, "buy_tp": 1.0700, "sell_sl": 1.0600, "sell_tp": 1.0400}
    )
    
    print("\n" + "="*50)
    print("GENERATED PROMPT SECTION (USD MACRO)")
    print("="*50)
    
    # Extract the relevant section
    if "[USD MACRO CYCLE]" in prompt:
        start = prompt.find("[USD MACRO CYCLE]")
        end = prompt.find("# RETAIL SENTIMENT")
        print(prompt[start:end])
    else:
        print("ERROR: [USD MACRO CYCLE] not found in prompt!")

    print("\n" + "="*50)
    print("GENERATED PROMPT SECTION (MACRO ALIGNMENT RULE)")
    print("="*50)
    if "# MACRO ALIGNMENT RULE" in prompt:
        start = prompt.find("# MACRO ALIGNMENT RULE")
        end = prompt.find("# JSON OUTPUT SCHEMA") # approx end
        print(prompt[start:start+500]) # Print first 500 chars of rule
    else:
        print("ERROR: Macro Alignment Rule not found!")

if __name__ == "__main__":
    asyncio.run(test_prompt())
