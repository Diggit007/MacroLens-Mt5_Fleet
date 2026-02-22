
import asyncio
from backend.services.agent_service import AgentFactory

async def main():
    try:
        agent = AgentFactory.get_agent("chat")
        print("Chat Agent Loaded.")
        response = await agent.chat("Hello, who are you?")
        print(f"RESPONSE: {response}")
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(main())
