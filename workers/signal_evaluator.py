import asyncio
import logging
from datetime import datetime, timezone
from backend.firebase_setup import initialize_firebase
from backend.services.metaapi_service import get_symbol_price
from backend.config import settings

logger = logging.getLogger("SignalEvaluator")

async def start_signal_evaluator():
    """
    Background worker that continuously evaluates ACTIVE signals against live prices.
    Tracks expiration (hitting TP or SL) and records the total duration the signal was active.
    """
    db = initialize_firebase()
    if not db:
        logger.error("Firestore DB not available. Evaluator disabled.")
        return

    analysis_ref = db.collection("analysis_requests")
    default_account_id = settings.META_API_ACCOUNT_ID or None

    print("\n\n" + "="*50)
    print("   SIGNAL EVALUATOR WORKER ALIVE")
    print("   Tracking Active Signals for TP/SL hits...")
    print("="*50 + "\n\n")

    while True:
        try:
            # 1. Fetch completed signals (we filter in memory for active ones)
            # Firestore composite indexes for nested `result.status` are annoying to require dynamically.
            docs = analysis_ref.where("status", "==", "COMPLETED").stream()
            
            for doc in docs:
                data = doc.to_dict()
                result = data.get("result", {})
                
                # Check current signal status
                sig_status = result.get("status", "ACTIVE")
                if sig_status in ["HIT_TP", "HIT_SL", "EXPIRED"]:
                    continue # Already resolved
                    
                symbol = data.get("symbol")
                if not symbol: continue
                
                # Get the relevant prices
                # Frontend fallback chain: signal.tp || ar.tp || ar.tp_suggested || ar.tradePlan?.takeProfit
                tp_val = data.get("tp") or result.get("tp") or result.get("tp_suggested")
                sl_val = data.get("sl") or result.get("sl") or result.get("sl_suggested")
                bias = data.get("bias") or result.get("direction")
                
                if tp_val is None or sl_val is None or not bias:
                    # Attempt to extract from tradePlan dictionary if not at root
                    tp_str = result.get("tradePlan", {}).get("takeProfit")
                    sl_str = result.get("tradePlan", {}).get("stopLoss")
                    if tp_str is not None: tp_val = float(tp_str)
                    if sl_str is not None: sl_val = float(sl_str)
                    
                    if tp_val is None or sl_val is None:
                        continue # Cannot evaluate without targets
                
                try:
                    tp = float(tp_val)
                    sl = float(sl_val)
                except (ValueError, TypeError):
                    continue
                
                # 2. Fetch Live Price
                uid = data.get("userId")
                account_id = data.get("accountId") or default_account_id
                if not account_id:
                    continue  # No account available to fetch prices from
                
                price_data = await get_symbol_price(account_id, symbol)
                if not price_data:
                    continue
                
                current_price = price_data.get("bid") # or mid price, bid is standard
                if not current_price: continue
                
                # 3. Evaluate Status
                new_status = None
                if bias.upper() == "BUY":
                    if current_price >= tp: new_status = "HIT_TP"
                    elif current_price <= sl: new_status = "HIT_SL"
                elif bias.upper() == "SELL":
                    if current_price <= tp: new_status = "HIT_TP"
                    elif current_price >= sl: new_status = "HIT_SL"
                
                if new_status:
                    # 4. Calculate Duration
                    # Try to find exactly when the AI finished generating this signal
                    start_time_obj = data.get("completed_at") or data.get("timestamp")
                    duration_mins = -1
                    
                    if start_time_obj:
                        try:
                            # Firestore timestamps have method timestamp() or similar, or python datetime object
                            if hasattr(start_time_obj, 'timestamp'):
                                ts_secs = start_time_obj.timestamp()
                                start_dt = datetime.fromtimestamp(ts_secs, tz=timezone.utc)
                            else:
                                start_dt = start_time_obj # already datetime
                                if start_dt.tzinfo is None:
                                    start_dt = start_dt.replace(tzinfo=timezone.utc)
                                    
                            now_dt = datetime.now(timezone.utc)
                            diff = now_dt - start_dt
                            duration_mins = int(diff.total_seconds() / 60)
                        except Exception as e:
                            logger.error(f"Duration calculation error: {e}")
                    
                    # 5. Save Back to DB
                    logger.info(f"Signal Evaluator: {symbol} [{bias}] reached {new_status}! Duration: {duration_mins} mins.")
                    
                    # Update nested result object
                    result["status"] = new_status
                    result["analysis_to_expiration_duration_minutes"] = duration_mins
                    
                    doc.reference.update({
                        "result": result,
                        "evaluation_updated_at": datetime.utcnow()
                    })

        except Exception as e:
            logger.error(f"Evaluator Loop Error: {e}")
            
        # Run sweep every 60 seconds
        await asyncio.sleep(60)
