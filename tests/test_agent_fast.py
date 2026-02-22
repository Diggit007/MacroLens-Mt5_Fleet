import asyncio
import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent.parent))

from backend.services.agent_service import MacroLensAgentV2

async def test_agent_router_logic_fast():
    print("Initializing Agent (Fast Mode)...")
    agent = MacroLensAgentV2()
    
    # Mock AIEngine completion to force specific tool decisions
    agent.ai_engine.get_completion = AsyncMock(side_effect=[
        # 1. Search Query
        '{"tool": "SEARCH", "search_query": "Apple news"}',
        "Here is the news about Apple...", 
        
        # 2. Macro Query
        '{"tool": "MACRO"}',
        "The macro outlook is...",
        
        # 3. Trade Query
        '{"tool": "TRADE", "symbol": "EURUSD"}',
        "EURUSD analysis..."
    ])

    # Mock Services to avoid real API calls
    # Mock Research Tool (if real one fails or is slow)
    # But let's let research tool run if it's fast. 
    # Mock MetaAPI
    sys.modules['backend.services.metaapi_service'] = MagicMock()
    sys.modules['backend.services.metaapi_service'].get_symbol_price = AsyncMock(return_value={'bid': 1.05, 'ask': 1.0501})
    
    # Run Queries
    queries = [
        "Search for Apple news",
        "Check macro events",
        "Analyze EURUSD"
    ]
    
    for q in queries:
        print(f"\n--- Testing Query: '{q}' ---")
        try:
            response = await agent.ask(q, user_id="test", account_id="test_acc") 
            print(f"Tool Used: {response['tool_used']}")
            print(f"Agent Identity: {response['agent']}")
        except Exception as e:
            print(f"FAILED: {e}")

    await agent.close()

if __name__ == "__main__":
    asyncio.run(test_agent_router_logic_fast())
