import os
import asyncio
import httpx
from dotenv import load_dotenv
from pathlib import Path

# Load env
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

api_key = os.getenv("OPENAI_API_KEY")

print(f"API Key loaded: {api_key[:10]}...{api_key[-5:] if api_key else 'None'}")

async def test_conn():
    if not api_key:
        print("ERROR: No API Key found in environment.")
        return

    print("Attempting request to OpenAI...")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "temperature": 0.7
                },
                timeout=10.0
            )
            print(f"Status Code: {resp.status_code}")
            if resp.status_code == 200:
                print("SUCCESS: OpenAI is reachable.")
                print(resp.json()['choices'][0]['message']['content'])
            else:
                print(f"FAILURE: {resp.text}")
    except Exception as e:
        print(f"EXCEPTION: {e}")

if __name__ == "__main__":
    asyncio.run(test_conn())
