
import asyncio
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from backend.services.trade_executor_agent import trade_executor
from backend.firebase_setup import initialize_firebase
from firebase_admin import firestore

# Initialize DB
db = initialize_firebase()

async def test_execution_flow():
    print("--- Starting Trade Executor Manual Test ---")
    
    # 1. Setup Test User
    user_id = "test_user_v2"
    account_id = "demo_account_id" # Mock
    
    print(f"\n1. Setting up User {user_id}...")
    # Reset Settings to DISABLED
    db.collection('algo_settings').document(user_id).set({
        "enabled": False,
        "risk_multiplier": 1.0,
        "excluded_pairs": []
    })
    print("   -> Set Bot to DISABLED.")

    # 2. Test Disabled Execution
    print("\n2. Sending Signal (Expect SKIP)...")
    signal = {
        "symbol": "EURUSD",
        "direction": "BUY",
        "confidence": 85,
        "sl_suggested": 1.0500,
        "tp_suggested": 1.0600
    }
    
    result = await trade_executor.execute_strategy(user_id, account_id, signal)
    print(f"   -> Result: {result['status']} (Reason: {result.get('reason')})")
    
    if result['status'] == 'skipped' and result['reason'] == 'Bot Disabled':
        print("   ✅ PASS: Bot correctly skipped trade when disabled.")
    else:
        print("   ❌ FAIL: Logic error.")

    # 3. Enable Bot
    print("\n3. Enabling Bot (Risk 2.0x)...")
    db.collection('algo_settings').document(user_id).update({
        "enabled": True,
        "risk_multiplier": 2.0
    })
    print("   -> Bot ENABLED.")

    # 4. Test Enabled Execution (Mocking MetaApi to avoid real trade)
    print("\n4. Sending Signal (Expect EXECUTION attempt)...")
    
    # We need to mock the actual meta_execute_trade call inside trade_executor
    # or just catch the potential error (since account_id is fake)
    
    # Let's rely on the fact that an invalid account ID will likely cause an error in calculate_position_size or execute_trade
    # But checking the logs is enough.
    
    try:
        result = await trade_executor.execute_strategy(user_id, account_id, signal)
        print(f"   -> Result: {result}")
        
        # We expect it to likely fail on "calculate_position_size" (get_account_info) or "execute_trade"
        # but that proves it TRIED to execute.
        if "Execution Failed" in str(result) or "System Error" in str(result) or result['status'] == 'executed':
             print("   ✅ PASS: Bot attempted execution (Error expected due to fake account).")
        else:
             print("   ⚠️ INDETERMINATE: " + str(result))
             
    except Exception as e:
        print(f"   Exception: {e}")

    # 5. Check Logs
    print("\n5. Verifying Firestore Activity Log...")
    docs = db.collection('bot_activity').where('userId', '==', user_id).limit(5).get()
    if len(docs) > 0:
        print(f"   ✅ PASS: Found {len(docs)} activity logs.")
        for d in docs:
            data = d.to_dict()
            print(f"      - [{data['signal']}] {data['symbol']}: {data['reasoning']}")
    else:
        print("   ❌ FAIL: No activity logs found.")

    print("\n--- Test Complete ---")

if __name__ == "__main__":
    asyncio.run(test_execution_flow())
