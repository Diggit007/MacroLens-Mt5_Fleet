
import asyncio
import logging
from backend.services.agent_service import AgentFactory
from backend.services.ai_engine import AIEngine
from backend.config import settings

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Verification")

async def test_chat_agent():
    print("\n--- TEST: CHAT AGENT ---")
    try:
        agent = AgentFactory.get_agent("chat")
        print(f"Agent Class: {type(agent).__name__}")
        
        # We need to mock the HTTP client or ensure a real one is available
        # The agent usually needs an http_client passed or created in __init__
        # MacroLensAgentV2 creates its own AsyncClient if not passed.
        
        response = await agent.chat("How do I see my open trades?")
        print(f"User: How do I see my open trades?")
        print(f"ChatAgent: {response}")
        
        if "Trading Console" in response or "trades" in response:
            print("✅ ChatAgent responded coherently.")
        else:
            print("⚠️ ChatAgent response seems generic/empty.")
            
    except Exception as e:
        print(f"❌ ChatAgent Test Failed: {e}")

async def test_prompt_injection():
    print("\n--- TEST: CONTEXT-AWARE PROMPT ---")
    try:
        # Instantiate AIEngine (Mocking client)
        engine = AIEngine(api_key="verify_key", http_client=None)
        
        # Mock Data
        symbol = "EURUSD"
        multi_tf_data = {
            "D1": {"structure": "BULLISH"},
            "H1": {"structure": "BULLISH"},
            "M5": {"price": 1.1050}
        }
        
        # Generate Prompt
        prompt = engine.construct_prompt(
            symbol=symbol,
            multi_tf_data=multi_tf_data,
            calendar=[],
            behavior_report="Normal Behavior",
            institutional_bias="Neutral",
            retail_sentiment="Neutral",
            risk_params={"atr_val": 0.0020}
        )
        
        # Check for Regime Injection
        if "MARKET REGIME: STRONG UPTREND" in prompt:
            print(f"✅ FOUND: 'MARKET REGIME: STRONG UPTREND' in prompt.")
            # print snippet
            start = prompt.find("MARKET REGIME: STRONG UPTREND")
            print(f"Snippet:\n{prompt[start:start+200]}...")
        else:
            print("❌ FAILED: Regime instructions not found in prompt.")
            print("Prompt dump (partial):", prompt[:500])

    except Exception as e:
        print(f"❌ Prompt Test Failed: {e}")

async def main():
    await test_chat_agent()
    await test_prompt_injection()

if __name__ == "__main__":
    asyncio.run(main())
