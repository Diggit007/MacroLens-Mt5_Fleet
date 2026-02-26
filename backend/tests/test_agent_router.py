import asyncio
import sys
import os
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent.parent))

from backend.services.agent_service import MacroLensAgentV2

async def test_agent_router_logic():
    print("Initializing Agent...")
    agent = MacroLensAgentV2()
    
    # Mocking AIEngine to avoid real API calls for decision
    # We want to test the ROUTER LOGIC, not the LLM's intelligence yet.
    # But wait, the router logic DEPENDS on the LLM. 
    # Let's mock the 'get_completion' method of ai_engine to return PREDICTABLE decisions.
    
    async def mock_get_completion(system_prompt, user_prompt, max_tokens=1000, temperature=0.5):
        print(f"\n[MOCK LLM] Prompt: {user_prompt[:50]}...")
        if "available tools" in system_prompt.lower() or "routing system" in system_prompt.lower():
            # This is the ROUTER call
            if "search" in user_prompt.lower() or "news" in user_prompt.lower():
                return '{"tool": "SEARCH", "search_query": "latest news Apple"}'
            elif "risk" in user_prompt.lower() or "exposure" in user_prompt.lower():
                return '{"tool": "RISK"}'
            elif "macro" in user_prompt.lower() or "divergence" in user_prompt.lower():
                return '{"tool": "MACRO"}'
            elif "trade" in user_prompt.lower() or "buy" in user_prompt.lower():
                return '{"tool": "TRADE", "symbol": "EURUSD"}'
            else:
                return '{"tool": "NONE"}'
        else:
            # This is the FINAL ANSWER call
            return "This is a mock final response based on the tool output."

    # Init engine normally, then override method
    # agent.ai_engine.get_completion = mock_get_completion
    # Commented out override to try REAL call if keys are present, 
    # but for safety/speed let's use the mock if we want to just test code flow.
    # For now, let's TRY to rely on the real LLM to verify the prompt actually works!
    # If it fails, we know we have an issue.
    
    queries = [
        "What is the latest news on Apple?",
        "Check my account risk level.",
        "Any macro divergence opportunities?",
        "Should I buy EURUSD?",
        "Hello there!"
    ]
    
    for q in queries:
        print(f"\n--- Testing Query: '{q}' ---")
        try:
            # We pass a dummy user_id to trigger account logic if needed
            response = await agent.ask(q, user_id="test_user", account_id="5888a294-118e-49b0-942b-939414546452") 
            print(f"Agent Identity: {response['agent']}")
            print(f"Tool Used: {response['tool_used']}")
            print(f"Response Length: {len(response['text'])}")
        except Exception as e:
            print(f"FAILED: {e}")
            import traceback
            traceback.print_exc()

    await agent.close()

if __name__ == "__main__":
    asyncio.run(test_agent_router_logic())
