import asyncio
import logging
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Configure logging
logging.basicConfig(level=logging.ERROR) # Sustain quiet

from backend.services.agent_service import MacroLensAgentV2
from backend.services.ai_engine import AIEngine
from backend.core.system_state import world_state

async def test_intelligence():
    print("\n" + "="*60)
    print("🧠 TESTING AGENT INTELLIGENCE CONNECTION")
    print("="*60 + "\n")

    # 1. SETUP: Mock the Cognitive State
    print("[SETUP] Injecting 'BEARISH / DEFENSIVE' state into Subconscious...")
    world_state.update(
        bias="BEARISH",
        risk="DEFENSIVE",
        session="NY"
    )
    world_state.logs = [] 
    world_state.add_log(
        agent="Cognitive Engine",
        message="Inflation data suggests further downside.",
        type="THOUGHT"
    )

    # 2. ASK THE AGENT (With Mocked AI Engine)
    
    # Futures for Async returns
    f_router = asyncio.Future()
    f_router.set_result('{"tool": "NONE"}') 
    
    f_chat = asyncio.Future()
    f_chat.set_result("CONFIRMED: Market Bias is BEARISH and Risk Mode is DEFENSIVE.") 

    # Patch the CLASS method to be sure
    with patch.object(AIEngine, 'get_completion', side_effect=[f_router, f_chat]) as mock_llm:
        agent = MacroLensAgentV2()
        response = await agent.ask("Status Report?")
        
        print(f"\n[AGENT RESPONSE]: {response['text']}")
        
        # Verify Context Injection
        if mock_llm.call_count >= 2:
            # The chat call is the second one
            # Check arguments of the call (kwargs)
            chat_call = mock_llm.call_args_list[1]
            # .call_args returns a tuple (args, kwargs) or similar structure depending on version
            # Usually call_args[1] is kwargs, or .kwargs attribute
            prompt = chat_call.kwargs.get('user_prompt', '') if chat_call.kwargs else chat_call[1].get('user_prompt', '')
            
            print("\n[VERIFICATION] Checking Prompt for Cognitive Context...")
            success = True
            
            if "Market Bias: BEARISH" in prompt:
                print("✅ PASS: Market Bias found in prompt.")
            else:
                success = False; print("❌ Fail: Bias missing")
                
            if "Risk Mode: DEFENSIVE" in prompt:
                print("✅ PASS: Risk Mode found in prompt.")
            else:
                success = False; print("❌ Fail: Risk missing")
                
            if "Inflation data suggests" in prompt:
                print("✅ PASS: Internal Thought found in prompt.")
            else:
                success = False; print("❌ Fail: Thought missing")
            
            if success:
                print("\n🎉 SUCCESS: All Cognitive Contexts Injected!")
            else:
                print("\n⚠️ PARTIAL PASS")
        else:
             print(f"❌ FAIL: LLM called {mock_llm.call_count} times.")

    await agent.close()

if __name__ == "__main__":
    try:
        if sys.platform == 'win32':
             asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(test_intelligence())
    except KeyboardInterrupt:
        pass
