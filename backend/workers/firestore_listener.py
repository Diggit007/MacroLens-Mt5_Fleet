import asyncio
import logging
from datetime import datetime, timedelta
from backend.firebase_setup import initialize_firebase
from backend.services.agent_service import AgentFactory
import numpy as np

def convert_numpy(obj):
    if isinstance(obj, dict):
        return {k: convert_numpy(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_numpy(v) for v in obj]
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return convert_numpy(obj.tolist())
    else:
        return obj


logger = logging.getLogger("FirestoreListener")

async def start_firestore_listener(fetch_bridge_candles_func, execute_trade_func=None, progress_callback=None):
    """
    Background worker that listens for PENDING analysis requests AND trading commands in Firestore.
    
    Args:
        fetch_bridge_candles_func: Async callback to fetch data from MT5.
        execute_trade_func: Async callback to execute trades via MT5 Bridge.
        progress_callback: Optional async callback for progress updates.
    """
    db = initialize_firebase()
    if not db:
        logger.error("Firestore DB not available. Listener disabled.")
        return

    analysis_ref = db.collection("analysis_requests")
    commands_ref = db.collection("commands")
    
    print("\n\n" + "="*50)
    print("   FIRESTORE LISTENER WORKER ALIVE (HEARTBEAT)")
    print("   Polling for Analysis & Trade Commands...")
    print("="*50 + "\n\n")

    logger.info("Firestore Listener Started. Polling for 'PENDING' requests & commands...")
    
    while True:
        try:
            found = False
            
            # --- 1. Process Analysis Requests ---
            docs = analysis_ref.where("status", "==", "PENDING").limit(5).stream()
            for doc in docs:
                found = True
                data = doc.to_dict()
                request_id = doc.id
                symbol = data.get("symbol")
                timeframe = data.get("timeframe", "H1")
                model_name = data.get("model", "MLens-Market Scout")
                
                logger.info(f"Processing Analysis Request {request_id}: {symbol} {timeframe}")
                

                doc.reference.update({"status": "IN_PROGRESS", "started_at": datetime.utcnow()})
                
                # [SCALABILITY FIX] Fire-and-Forget Task
                # Defined as inner function to capture context safely
                async def process_analysis_task(doc_ref, data, req_id):
                    try:
                        user_id = data.get("userId", "default")
                        sym = data.get("symbol")
                        tf = data.get("timeframe", "H1")
                        model_n = data.get("model", "MLens-Market Scout")
                        
                        # Define Callback 
                        async def fetch_callback(account_id_ignored, s, t):
                            return await fetch_bridge_candles_func(user_id, s, t, 100)

                        # Create ephemeral agent instance (lightweight) OR use factory
                        agent = AgentFactory.get_agent(model_n)
                        
                        result = await agent.process_single_request(
                            sym, tf, 
                            fetch_callback=fetch_callback, 
                            user_id=user_id,
                            progress_callback=progress_callback
                        )
                        result['model'] = model_n
                        
                        if result.get("status") == "error": raise Exception(result.get("message"))
                        
                        # Sanitize result for Firestore (convert numpy types)
                        result = convert_numpy(result)
                            
                        doc_ref.update({
                            "status": "COMPLETED",
                            "completed_at": datetime.utcnow(),
                            "result": result,
                            "analysis": result.get("analysis", {}),
                            "recommendation": result.get("recommendation", "HOLD"),
                            "model": model_n
                        })
                        
                        # Sync Signal logic...
                        try:
                            expiry_time = datetime.utcnow() + timedelta(hours=4)
                            signal_doc = {
                                "pair": sym,
                                "symbol": sym,
                                "type": result.get("direction", "WAIT").upper().replace("STRONG_", ""),
                                "price": float(result.get("entry", 0) or 0),
                                "sl": float(result.get("sl_suggested", 0) or 0),
                                "tp": float(result.get("tp_suggested", 0) or 0),
                                "confidence": int(result.get("confidence", 0) or 0),
                                "time": datetime.utcnow().isoformat(),
                                "requesterId": data.get("requesterId", user_id),
                                "model": model_n,
                                "analysisId": req_id,
                                "timeframe": tf,
                                "expiryTime": expiry_time.isoformat(),
                                "is_generated": True,
                                "is_real_time": True
                            }
                            db.collection("signals").document(req_id).set(signal_doc)
                        except Exception as e: logger.error(f"Signal Sync Error: {e}")

                        logger.info(f"Analysis {req_id} Completed.")
                        await agent.close()
                        
                    except Exception as e:
                        import traceback
                        tb = traceback.format_exc()
                        logger.error(f"Analysis {req_id} Failed: {e}\n{tb}")
                        
                        # Ensure error message is never empty for UI
                        error_msg = f"{type(e).__name__}: {str(e)}" or "Unknown internal error."
                        
                        # [DEBUG] Write Traceback to Firestore
                        doc_ref.update({
                            "status": "ERROR", 
                            "error_message": error_msg,
                            "debug_trace": tb
                        })

                # Launch in Background
                asyncio.create_task(process_analysis_task(doc.reference, data, request_id))

            # --- 2. Process Trade Commands ---
            if execute_trade_func:
                cmd_docs = commands_ref.where("status", "==", "PENDING").limit(5).stream()
                for doc in cmd_docs:
                    found = True
                    data = doc.to_dict()
                    cmd_id = doc.id
                    cmd_type = data.get("type", "UNKNOWN")
                    payload = data.get("payload", {})
                    user_id = data.get("createdBy") or data.get("userId") or "default"
                    
                    logger.info(f"Processing Trade Command {cmd_id}: {cmd_type}")
                    doc.reference.update({"status": "IN_PROGRESS", "started_at": datetime.utcnow()})
                    
                    try:
                        # Execute Callback
                        res = await execute_trade_func(user_id, cmd_type, payload)
                        
                        doc.reference.update({
                            "status": "COMPLETED",
                            "execution_result": res,
                            "completed_at": datetime.utcnow()
                        })
                        logger.info(f"Command {cmd_id} Executed.")
                        
                    except Exception as e:
                        logger.error(f"Command {cmd_id} Failed: {e}")
                        doc.reference.update({"status": "ERROR", "error": str(e)})

            # --- 3. Process New MT5 Accounts (Provisioning via Fleet Manager) ---
            accounts_ref = db.collection("mt5_accounts")
            account_docs = accounts_ref.where("status", "==", "PENDING").limit(5).stream()
            
            for doc in account_docs:
                found = True
                data = doc.to_dict()
                doc_id = doc.id
                user_id = data.get("userId", "default")
                
                logger.info(f"Provisioning MT5 Account for User {user_id}...")
                doc.reference.update({"status": "PROVISIONING"})
                
                try:
                    # 1. Validate Input
                    login = data.get("login")
                    password = data.get("password")
                    server = data.get("server")
                    
                    if not all([login, password, server]):
                        raise Exception("Missing login, password, or server")

                    # 2. Connect via Fleet Manager API
                    import httpx
                    import os
                    fleet_url = os.getenv("FLEET_MANAGER_URL", "http://158.220.82.187:8000")
                    
                    # 2a. Pre-flight: Check if Fleet Manager is reachable
                    async with httpx.AsyncClient(timeout=5.0) as probe:
                        try:
                            health = await probe.get(f"{fleet_url}/health")
                        except Exception:
                            raise Exception(f"Fleet Manager at {fleet_url} is offline. Will retry later.")
                    
                    async with httpx.AsyncClient(timeout=30.0) as http_client:
                        resp = await http_client.post(f"{fleet_url}/connect", json={
                            "account_id": str(login),
                            "password": password,
                            "server": server
                        })
                    
                    if resp.status_code != 200:
                        raise Exception(f"Fleet Manager connection failed: {resp.text}")
                    
                    fleet_result = resp.json()
                    logger.info(f"Fleet Manager connected account {login}: {fleet_result.get('status')}")
                    
                    # Handle "pending" status (MT5 still booting)
                    if fleet_result.get("status") == "pending":
                        logger.info(f"Account {login} is still booting on Fleet Manager. Will check again later.")
                        doc.reference.update({"status": "PENDING"})  # Reset to PENDING to retry
                        continue
                    
                    # 3. Success - Update Firestore
                    # Use the Firestore doc ID as the account reference, store login for Fleet Manager lookups
                    doc.reference.update({
                        "status": "COMPLETED",
                        "accountId": doc_id,
                        "fleetLogin": str(login),
                        "provisionedAt": datetime.utcnow().isoformat()
                    })
                    
                    # 4. Auto-Set as Active for User
                    try:
                        user_ref = db.collection("users").document(user_id)
                        user_ref.update({"activeAccountId": doc_id})
                    except: pass
                    
                except Exception as e:
                    logger.error(f"Account Provisioning Failed: {e}")
                    doc.reference.update({"status": "ERROR", "error": str(e)})

            if not found:
                # [QUOTA SAFETY] 5s Interval = ~34k reads/day (Fits in 50k Free Tier)
                await asyncio.sleep(5)
            else:
                await asyncio.sleep(0.1)
                
        except Exception as e:
            logger.error(f"Firestore Listener Loop Error: {e}")
            # [QUOTA FIX] Sleep longer on error to let quota recover
            await asyncio.sleep(60)
