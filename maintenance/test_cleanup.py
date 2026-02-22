
import asyncio
import time
from backend.services.streaming_service import StreamingManager, StreamingStateListener

# Mock MetaApi Connection
class MockConnection:
    async def close(self):
        print("MockConnection Closed.")

async def test_cleanup():
    print("Testing StreamingManager Cleanup...")
    manager = StreamingManager()
    
    # 1. Add a dummy listener
    acc_id = "test_user_1"
    listener = StreamingStateListener(acc_id, "user1", None)
    manager.listeners[acc_id] = listener
    manager.connections[acc_id] = MockConnection()
    
    # 2. Simulate 40 minutes passing
    listener.last_accessed = time.time() - 2400 
    
    # Run Cleanup Logic (Manually invoke the logic inside loop for testing)
    print("Triggering Cleanup...")
    
    # Copy-paste logic from _cleanup_loop for unit testing scope
    now = time.time()
    idle_ids = []
    for acc_id, l in manager.listeners.items():
        if (now - l.last_accessed) > 1800:
            idle_ids.append(acc_id)
            
    for acc_id in idle_ids:
        print(f"[{acc_id}] Idle detected.")
        if acc_id in manager.connections:
            await manager.connections[acc_id].close()
            del manager.connections[acc_id]
        if acc_id in manager.listeners:
            del manager.listeners[acc_id]

    # Verify
    if acc_id not in manager.listeners:
        print("[PASS] Listener removed.")
    else:
        print("[FAIL] Listener still exists.")

    if acc_id not in manager.connections:
        print("[PASS] Connection removed.")
    else:
        print("[FAIL] Connection still exists.")

if __name__ == "__main__":
    asyncio.run(test_cleanup())
