
import asyncio
import logging
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add parent directory to path to allow imports from 'backend'
sys.path.append(str(Path(__file__).resolve().parent.parent))

# Load Environment
BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(BACKEND_DIR / ".env") 

# Setup Logging
from backend.core.logger import setup_logger
logger = setup_logger("WORKER_AI")

# Services
from backend.services.event_monitor import event_monitor
from backend.services.cognitive_loop import cognitive_engine
from backend.services.ai_engine import get_usd_engine
from backend.core.database import DatabasePool
from backend.services.agent_service import MacroLensAgentV2
from backend.services.metaapi_service import fetch_candles
from backend.services.trade_executor_agent import trade_executor
from backend.config import settings
from backend.firebase_setup import initialize_firebase
from firebase_admin import firestore
import traceback

# Initialize Agent
agent = MacroLensAgentV2()

async def schedule_autonomous_trading():
    """
    Autonomous Trading Loop: Scans, Decides, and Executes/Logs for ALL Users.
    Runs every 5 minutes.
    Now uses Market Scanner heatmap to pick top opportunities instead of hardcoded watchlist.
    """
    logger.info("Initializing Autonomous Trading Loop for ALL Users...")
    await asyncio.sleep(30) # Wait for boot + first scanner cycle

    # REMOVED: Hardcoded watchlist. Now uses heatmap scores.
    # Old: watchlist = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD", "US30"]
    FALLBACK_WATCHLIST = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD", "US30"]
    
    # Global Semaphore to prevent API Rate Limits (5 Concurrent User-Symbol Tasks)
    api_semaphore = asyncio.Semaphore(5)

    while True:
        try:
            # Get top symbols from Market Scanner heatmap
            try:
                from backend.services.market_scanner import scanner_cache
                heatmap = scanner_cache.get_heatmap()
                if heatmap:
                    # Pick top 5 symbols with score >= 50
                    watchlist = [s["symbol"] for s in heatmap[:5] if s["score"] >= 50]
                    if watchlist:
                        logger.info(f"Heatmap Top Picks: {[(s['symbol'], s['score']) for s in heatmap[:5]]}")
                    else:
                        watchlist = FALLBACK_WATCHLIST
                        logger.info("Heatmap: No symbols scored >= 50. Using fallback.")
                else:
                    watchlist = FALLBACK_WATCHLIST
                    logger.info("Scanner cache empty. Using fallback watchlist.")
            except Exception as e:
                watchlist = FALLBACK_WATCHLIST
                logger.warning(f"Heatmap unavailable: {e}. Using fallback.")

            logger.info(f"Starting Autonomous Scan Cycle for {watchlist}...")
            
            # The Master Account is where the AI actually trades
            master_account_id = settings.META_API_ACCOUNT_ID
            master_user_id = "master"
            
            if not master_account_id:
                logger.error("No META_API_ACCOUNT_ID defined. Cannot run autonomous trading.")
                await asyncio.sleep(60)
                continue

            # 2. Define Per-Symbol Task for Master
            async def process_master_symbol(symbol):
                async with api_semaphore:
                    try:
                        logger.info(f"[Auto] Master AI Analyzing {symbol}...")
                        
                        # A. Analyze (Uses Master's Data)
                        analysis_result = await agent.process_single_request(
                            symbol=symbol,
                            timeframe="1h",
                            user_id=master_user_id,
                            account_id=master_account_id
                        )
                        
                        if analysis_result.get('status') == 'error':
                            return

                        signal_data = analysis_result
                        direction = signal_data.get("direction", "WAIT")
                        
                        # C. Master Execution Logic (Handles broadcasting to followers inside)
                        execution_result = await trade_executor.execute_master_strategy(
                            master_account_id=master_account_id,
                            signal=signal_data
                        )
                        
                        if execution_result.get('status') == 'executed':
                            logger.info(f"[Auto] Master {symbol}: EXECUTED {direction} (Ticket: {execution_result.get('ticket')})")
                        elif execution_result.get('status') == 'skipped':
                            logger.info(f"[Auto] Master {symbol}: SKIPPED ({execution_result.get('reason')})")
                        else:
                            logger.warning(f"[Auto] Master {symbol}: EXECUTION FAILED ({execution_result.get('message')})")
                            
                    except Exception as ex:
                        logger.error(f"[Auto] Error for Master {symbol}: {ex}")

            # 3. Batch Process
            tasks = [process_master_symbol(symbol) for symbol in watchlist]
            
            if tasks:
                await asyncio.gather(*tasks)
            
            logger.info("Autonomous Master Cycle Complete.")

        except Exception as e:
            logger.error(f"Auto Loop Critical Failure: {e}")
            traceback.print_exc()
        
        # Wait 5 minutes
        await asyncio.sleep(300)



async def schedule_market_data_updates():
    """Runs scrapers every 1 hour in background (AI Worker)"""
    # Wait 30s after startup to not slow down boot
    await asyncio.sleep(30)
    
    # Static counter simulation
    if not hasattr(schedule_market_data_updates, "counter"):
        schedule_market_data_updates.counter = 0

    while True:
        try:
            logger.info("Starting Scheduled Market Data Refresh...")
            from backend.scrapers.update_market_data import update_hourly_tasks, update_retail_tasks
            
            # 1. Run Hourly Tasks (Calendar, News, Institutional, History)
            await update_hourly_tasks()
            
            # 2. Run 24-Hour Tasks (Retail Sentiment) - ONCE PER DAY
            if schedule_market_data_updates.counter % 24 == 0:
                logger.info("Running Daily Tasks (Retail Sentiment)...")
                await update_retail_tasks()
            
            schedule_market_data_updates.counter += 1
            
            # 3. USD Index Pulse (Every Hour)
            try:
                usd_engine = get_usd_engine()
                if usd_engine:
                    data = usd_engine.get_latest()
                    if data:
                        from backend.core.system_state import world_state
                        strength = "STRONG" if data['signal_value'] > 0 else "WEAK"
                        msg = f"USD Index Update: The Dollar remains {data['signal']} (Score: {data['composite_index']:.2f}). Market structure suggests {strength} USD performance."
                        world_state.add_log("Global Macro", msg, "MACRO")
                        logger.info(f"Scheduled USD Index Update: {msg}")
            except Exception as e:
                logger.error(f"Scheduled USD Field: {e}")

            # 4. COT Pulse (Every 4 Hours - concurrent with Retail)
            if schedule_market_data_updates.counter % 4 == 0:
                try:
                    from backend.services.cot.api import engine as cot_engine
                    # Flash COT for major pairs
                    for sym in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]:
                         cot_data = cot_engine.get_latest_sentiment(sym)
                         if cot_data:
                             bias = "Bullish" if cot_data['smart_sentiment'] > 0 else "Bearish"
                             if cot_data['willco_index'] > 80 or cot_data['willco_index'] < 20: 
                                 bias += " (Extreme)"
                             bias_desc = "Accumulating" if cot_data['smart_sentiment'] > 0 else "Distributing"
                             msg = f"Institutional Flow ({sym}): Smart Money is {bias_desc} {sym}. Net positioning is {bias} ({cot_data['smart_sentiment']:.1f}%). Willco Index: {cot_data['willco_index']:.1f}."
                             from backend.core.system_state import world_state
                             world_state.add_log("Institutional", msg, "COT")
                except Exception as e:
                    logger.error(f"Scheduled COT Pulse Failed: {e}")

            # 5. Weekly CFTC Data Download (Saturdays at Noon)
            import datetime
            now = datetime.datetime.now()
            # Saturday is 5 in Python. We trigger precisely during the noon hour tick to avoid multiple runs on Saturday.
            if now.weekday() == 5 and now.hour == 12:
                try:
                    logger.info("Executing Weekly Scheduled CFTC Download...")
                    from backend.scripts.download_cftc import download_and_extract
                    # Run this in a distinct thread/async wrapper if it takes too long, but it's quick enough for asyncio background task
                    import threading
                    threading.Thread(target=download_and_extract, daemon=True).start()
                except Exception as e:
                    logger.error(f"Weekly CFTC Download Failed: {e}")

            logger.info("Scheduled Data Refresh Success.")
        except Exception as e:
            logger.error(f"Scheduled Data Refresh Failed: {e}")
            
        # Wait 1 hour (3600 seconds)
        await asyncio.sleep(3600)

async def process_analysis_queue():
    """
    Dedicated loop to process user-generated Analysis Requests from Firestore.
    """
    logger.info("Initializing Analysis Queue Processor...")
    
    # Ensure Firebase is initialized (idempotent)
    db = initialize_firebase()
    if not db:
        logger.error("Firebase Init Failed. Analysis Queue disabled.")
        return

    while True:
        try:
            # Poll for PENDING requests
            # Limit to 5 at a time to prevent rate limits
            docs = db.collection('analysis_requests').where('status', '==', 'PENDING').limit(5).stream()
            
            # Convert generator to list to avoid timeout issues if processing takes long
            pending_requests = []
            for doc in docs:
                pending_requests.append((doc.id, doc.to_dict()))
            
            if not pending_requests:
                await asyncio.sleep(2) # Short sleep if empty
                continue
                
            logger.info(f"Found {len(pending_requests)} pending analysis requests.")

            async def process_request(req_id, data):
                symbol = data.get('symbol')
                user_id = data.get('requesterId')
                
                if not symbol:
                    db.collection('analysis_requests').document(req_id).update({
                        'status': 'ERROR', 'error': 'Missing Symbol', 'completedAt': firestore.SERVER_TIMESTAMP
                    })
                    return

                try:
                    # Mark as PROCESSING
                    db.collection('analysis_requests').document(req_id).update({
                        'status': 'PROCESSING',
                        'startedAt': firestore.SERVER_TIMESTAMP
                    })
                    
                    logger.info(f"Processing Request {req_id} ({symbol})...")
                    
                    # [FIX] Resolve User's Active Account to ensure we analyze THEIR data stream
                    user_account_id = None
                    try:
                        user_doc = db.collection('users').document(user_id).get()
                        if user_doc.exists:
                            udata = user_doc.to_dict()
                            user_account_id = udata.get('activeAccountId') or udata.get('metaapiAccountId')
                    except Exception as ue:
                        logger.warning(f"Could not resolve account for user {user_id}: {ue}")
                    
                    # Call Agent Analysis
                    # process_single_request returns a Dict with result data
                    result = await agent.process_single_request(
                        symbol=symbol,
                        timeframe="1h", # Default
                        fetch_callback=fetch_candles,
                        user_id=user_id,
                        account_id=user_account_id # Pass explicitly
                    )
                    
                    if result.get('status') == 'error':
                        db.collection('analysis_requests').document(req_id).update({
                            'status': 'ERROR',
                            'error': result.get('message', 'Unknown Error'),
                            'completedAt': firestore.SERVER_TIMESTAMP
                        })
                    else:
                        # Success
                        db.collection('analysis_requests').document(req_id).update({
                            'status': 'COMPLETED',
                            'result': result,
                            'completedAt': firestore.SERVER_TIMESTAMP
                        })
                        logger.info(f"Request {req_id} Completed.")

                except Exception as e:
                    logger.error(f"Request {req_id} Failed: {e}")
                    # traceback.print_exc()
                    db.collection('analysis_requests').document(req_id).update({
                        'status': 'ERROR',
                        'error': str(e),
                        'completedAt': firestore.SERVER_TIMESTAMP
                    })

            # Use Semaphore to allow LIMITED concurrency (e.g. 2 parallel analyses)
            # This supports multi-user better than strict sequential, while protecting API limits.
            sem = asyncio.Semaphore(2) 
            
            async def protected_process(rid, d):
                async with sem:
                    await process_request(rid, d)
                    # Small cool-down after releasing semaphore to let API breathe
                    await asyncio.sleep(1)

            tasks = [protected_process(rid, d) for rid, d in pending_requests]
            if tasks:
                await asyncio.gather(*tasks)

        except Exception as e:
            logger.error(f"Analysis Queue Loop Error: {e}")
            await asyncio.sleep(5)
            
        # Wait a bit before next poll
        await asyncio.sleep(1)

async def main():
    logger.info("--------------------------------------------------")
    logger.info("!!! MACROLENS WORKER: AI ANALYST STARTING !!!")
    logger.info("--------------------------------------------------")

    # 1. DB Health Check
    db_ok = await DatabasePool.health_check()
    logger.info(f"Database Health Check: {'OK' if db_ok else 'FAIL'}")

    # 2. Start Quantitative Event Monitor
    await event_monitor.start()
    logger.info("Event Monitor Started.")

    # 3. Start Cognitive Engine (Self-Awareness Loop)
    asyncio.create_task(cognitive_engine.start_loop())
    logger.info("Cognitive Engine (OODA Loop) Started.")

    # 4. Start Market Data Scheduler
    asyncio.create_task(schedule_market_data_updates())
    logger.info("Market Data Scheduler Started.")

    # 5. Start Market Scanner (Pre-computes analysis for ALL symbols via MT5)
    from backend.services.market_scanner import market_scanner
    asyncio.create_task(market_scanner.run_scan_loop())
    logger.info("Market Scanner (Analytical Brain) Started.")

    # 6. Start Autonomous Trading Loop (uses Scanner heatmap for symbol selection)
    asyncio.create_task(schedule_autonomous_trading())
    logger.info("Autonomous Trading Loop Started.")

    # 6. Start Analysis Request Queue
    asyncio.create_task(process_analysis_queue())
    logger.info("Analysis Request Queue Started.")

    # Keep alive
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logger.info("AI Worker Stopping...")
        await event_monitor.stop()
        logger.info("AI Worker Stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
