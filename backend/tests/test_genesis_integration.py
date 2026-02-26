import asyncio
import aiohttp
import sqlite3
import os
import sys

# Configuration
DB_PATH = 'c:/MacroLens/backend/market_data.db'
API_URL = "http://localhost:8000"

async def test_database():
    """Test 1: Verify market_data.db creation and population."""
    print(f"\n[TEST 1] Checking Database: {DB_PATH}")
    if not os.path.exists(DB_PATH):
        print("❌ FAILED: Database file not found.")
        return False
        
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check for economic_events table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='economic_events';")
        if not cursor.fetchone():
            print("❌ FAILED: Table 'economic_events' missing.")
            conn.close()
            return False
            
        # Check for population
        cursor.execute("SELECT COUNT(*) FROM economic_events;")
        count = cursor.fetchone()[0]
        print(f"✅ PASSED: Table 'economic_events' exists. Record count: {count}")
        
        if count == 0:
            print("⚠️ WARNING: Table exists but is empty.")
            
        conn.close()
        return True
    except Exception as e:
        print(f"❌ FAILED: Database error: {e}")
        return False

async def test_backend_process():
    """Test 2: Verify Backend Process and internal health."""
    print(f"\n[TEST 2] Checking Backend Health: {API_URL}/health")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{API_URL}/health") as response:
                if response.status == 200:
                    data = await response.json()
                    print(f"✅ PASSED: Health Check OK. Status: {data.get('status')}")
                    print(f"   Components: {data.get('components')}")
                    return True
                else:
                    print(f"❌ FAILED: Health Check returned {response.status}")
                    print(await response.text())
                    return False
    except aiohttp.ClientConnectorError:
        print("❌ FAILED: Could not connect to backend (Connection Refused). Is it running?")
        return False
    except Exception as e:
        print(f"❌ FAILED: Request error: {e}")
        return False

async def test_port_configuration():
    """Test 3: Verify Port 8000 connectivity."""
    print(f"\n[TEST 3] Checking Port 8000 Configuration")
    # If Test 2 passed, this is implicitly true, but we can do a socket check to be specific
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('localhost', 8000))
    sock.close()
    
    if result == 0:
        print("✅ PASSED: Port 8000 is open and listening.")
        return True
    else:
        print("❌ FAILED: Port 8000 is closed or unreachable.")
        return False

async def main():
    print("=== GENESIS INTEGRATION TESTS ===")
    
    results = [
        await test_database(),
        await test_port_configuration(),
        await test_backend_process()
    ]
    
    if all(results):
        print("\n🎉 ALL TESTS PASSED. System is ready for Genesis control.")
        sys.exit(0)
    else:
        print("\n⛔ SOME TESTS FAILED.")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
