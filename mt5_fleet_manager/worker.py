import argparse
import asyncio
import logging
import os
import time as _time
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List, Set
from datetime import datetime, timedelta
import json
import uvicorn
from fastapi import FastAPI, HTTPException, Request
import MetaTrader5 as mt5

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] Worker: %(message)s')
logger = logging.getLogger("MT5Worker")

# --- ARGUMENTS ---
parser = argparse.ArgumentParser(description="MT5 Python Worker")
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--account", type=int, required=True)
parser.add_argument("--password", type=str, required=True)
parser.add_argument("--server", type=str, required=True)
parser.add_argument("--path", type=str, required=True)
parser.add_argument("--user_id", type=str, default="")  # Firebase UID for Firestore sync
args, _ = parser.parse_known_args()

# --- GLOBALS ---
MT5_READY = False
_firestore_db = None
_position_snapshot: Set[int] = set()  # Track open position tickets

# ============================================================
# FIRESTORE SETUP (Optional — only if user_id is provided)
# ============================================================
def _init_firestore():
    """Initialize Firebase Admin SDK for writing trade history"""
    global _firestore_db
    if not args.user_id:
        logger.info("No --user_id provided, Firestore sync disabled.")
        return None
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore as fs
        
        # Look for service account key in multiple locations
        key_paths = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "serviceAccountKey.json"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "serviceAccountKey.json"),
        ]
        key_path = None
        for p in key_paths:
            if os.path.exists(p):
                key_path = p
                break
        
        if not key_path:
            logger.warning("serviceAccountKey.json not found — Firestore sync disabled.")
            return None
        
        # Only initialize if not already done
        if not firebase_admin._apps:
            cred = credentials.Certificate(key_path)
            firebase_admin.initialize_app(cred)
        
        _firestore_db = fs.client()
        logger.info(f"✅ Firestore initialized for user {args.user_id}")
        return _firestore_db
    except Exception as e:
        logger.warning(f"Firestore init failed (non-fatal): {e}")
        return None


# ============================================================
# TRADE HISTORY SYNC
# ============================================================
def _deal_to_firestore_doc(entry_deal, exit_deal) -> dict:
    """Convert a matched pair of MT5 deals (entry+exit) into a Firestore document"""
    gross_profit = float(exit_deal.profit)
    swap = float(exit_deal.swap)
    commission = float(entry_deal.commission) + float(exit_deal.commission)
    net_pnl = gross_profit + swap + commission
    
    return {
        "positionId": str(exit_deal.position_id),
        "symbol": entry_deal.symbol,
        "type": "BUY" if entry_deal.type == 0 else "SELL",
        "volume": float(entry_deal.volume),
        "openPrice": float(entry_deal.price),
        "closePrice": float(exit_deal.price),
        "openTime": datetime.utcfromtimestamp(entry_deal.time).isoformat() + "Z",
        "closeTime": datetime.utcfromtimestamp(exit_deal.time).isoformat() + "Z",
        "profit": gross_profit,
        "swap": swap,
        "commission": commission,
        "netPnl": net_pnl,
        "mt5Login": str(args.account),
        "status": "CLOSED",
        "ticket": int(exit_deal.order),
        "magic": int(entry_deal.magic),
        "comment": exit_deal.comment or entry_deal.comment or "",
    }


def sync_history_to_firestore(days_back: int = 90):
    """Pull MT5 deal history and write completed trades to Firestore"""
    if not _firestore_db or not args.user_id:
        return 0
    
    try:
        now = datetime.utcnow()
        start = now - timedelta(days=days_back)
        deals = mt5.history_deals_get(start, now)
        
        if not deals:
            logger.info("No history deals found in MT5.")
            return 0
        
        # Group deals by position_id
        deals_by_pos = {}
        for d in deals:
            pid = d.position_id
            if pid == 0:
                continue  # Skip balance/credit operations
            if pid not in deals_by_pos:
                deals_by_pos[pid] = []
            deals_by_pos[pid].append(d)
        
        # Match entry (type IN=0) with exit (type OUT=1 or INOUT=2)
        batch_count = 0
        collection_ref = _firestore_db.collection("users").document(args.user_id).collection("trade_history")
        
        batch = _firestore_db.batch()
        for pid, pos_deals in deals_by_pos.items():
            entry = next((d for d in pos_deals if d.entry == 0), None)  # DEAL_ENTRY_IN
            exit_d = next((d for d in pos_deals if d.entry in (1, 2)), None)  # DEAL_ENTRY_OUT or INOUT
            
            if entry and exit_d:
                doc_data = _deal_to_firestore_doc(entry, exit_d)
                doc_ref = collection_ref.document(str(pid))
                batch.set(doc_ref, doc_data, merge=True)
                batch_count += 1
                
                # Firestore batch limit = 500
                if batch_count % 450 == 0:
                    batch.commit()
                    batch = _firestore_db.batch()
        
        if batch_count % 450 != 0:
            batch.commit()
        
        logger.info(f"✅ Synced {batch_count} historical trades to Firestore for user {args.user_id}")
        return batch_count
        
    except Exception as e:
        logger.error(f"History sync failed: {e}")
        return 0


async def _position_monitor_loop():
    """Background task: detect closed positions and write them to Firestore"""
    global _position_snapshot
    
    if not _firestore_db or not args.user_id:
        return
    
    logger.info("🔄 Position monitor started")
    
    # Initial snapshot
    await asyncio.sleep(5)
    positions = mt5.positions_get()
    if positions:
        _position_snapshot = {p.ticket for p in positions}
    
    while True:
        try:
            await asyncio.sleep(3)  # Check every 3 seconds
            
            if not MT5_READY:
                continue
            
            current_positions = mt5.positions_get()
            current_tickets = set()
            if current_positions:
                current_tickets = {p.ticket for p in current_positions}
            
            # Detect closed positions (were in snapshot, now gone)
            closed_tickets = _position_snapshot - current_tickets
            
            if closed_tickets:
                logger.info(f"🔔 Detected {len(closed_tickets)} closed position(s): {closed_tickets}")
                
                # Fetch the deals for these closed positions
                now = datetime.utcnow()
                start = now - timedelta(minutes=10)  # Look back 10 min for the deal
                deals = mt5.history_deals_get(start, now)
                
                if deals:
                    deals_by_pos = {}
                    for d in deals:
                        if d.position_id == 0:
                            continue
                        if d.position_id not in deals_by_pos:
                            deals_by_pos[d.position_id] = []
                        deals_by_pos[d.position_id].append(d)
                    
                    collection_ref = _firestore_db.collection("users").document(args.user_id).collection("trade_history")
                    
                    for pid, pos_deals in deals_by_pos.items():
                        entry = next((d for d in pos_deals if d.entry == 0), None)
                        exit_d = next((d for d in pos_deals if d.entry in (1, 2)), None)
                        
                        if exit_d:
                            # For the entry, also look further back if not found in 10-min window
                            if not entry:
                                all_deals = mt5.history_deals_get(now - timedelta(days=90), now)
                                if all_deals:
                                    entry = next((d for d in all_deals if d.position_id == pid and d.entry == 0), None)
                            
                            if entry and exit_d:
                                doc_data = _deal_to_firestore_doc(entry, exit_d)
                                collection_ref.document(str(pid)).set(doc_data, merge=True)
                                logger.info(f"✅ Wrote closed trade {pid} ({entry.symbol}) PnL: {doc_data['netPnl']:.2f}")
            
            # Update snapshot
            _position_snapshot = current_tickets
            
        except Exception as e:
            logger.error(f"Position monitor error: {e}")
            await asyncio.sleep(5)


# ============================================================
# FASTAPI APP
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global MT5_READY
    logger.info(f"Initializing MT5 terminal from {args.path}")
    
    # 1. Initialize Firestore (non-blocking, optional)
    _init_firestore()
    
    # 2. Initialize MT5
    # The portable flag ensures it uses the local folder for data
    initialized = mt5.initialize(
        path=args.path,
        login=args.account,
        password=args.password,
        server=args.server,
        portable=True,
        timeout=120000 # 120 seconds timeout to prevent IPC crashes on slow VPS boots
    )
    
    if not initialized:
        logger.error(f"MT5 init failed, error code = {mt5.last_error()}")
        MT5_READY = False
    else:
        # 2. Re-login explicitly just to be safe
        authorized = mt5.login(
            login=args.account, 
            password=args.password, 
            server=args.server
        )
        if authorized:
            logger.info(f"MT5 Login Success for {args.account} on {args.server}")
            MT5_READY = True
            
            # 3. Minimize the GUI to save system rendering resources!
            try:
                import ctypes
                # Give the window a second to physically render before minimizing
                _time.sleep(2)
                user32 = ctypes.windll.user32
                # The internal Win32 class name for the MT5 terminal
                hwnd = user32.FindWindowW("MetaQuotes::MetaTrader::5.00", None)
                if hwnd:
                    user32.ShowWindow(hwnd, 6) # 6 = SW_MINIMIZE
                    logger.info("Terminal window successfully minimized.")
            except Exception as e:
                logger.warning(f"Could not minimize window: {e}")
            
            # 4. Sync full history to Firestore (runs once on connect)
            if _firestore_db and args.user_id:
                try:
                    sync_history_to_firestore(days_back=90)
                except Exception as e:
                    logger.error(f"Initial history sync failed (non-fatal): {e}")
                
                # 5. Start background position monitor
                asyncio.create_task(_position_monitor_loop())
                
        else:
            logger.error(f"MT5 login failed, error code = {mt5.last_error()}")
            MT5_READY = False
            
    yield
    
    # Shutdown
    logger.info("Shutting down MT5 terminal...")
    mt5.shutdown()

app = FastAPI(lifespan=lifespan)

# --- DEPENDENCY/CHECK ---
def check_mt5():
    if not MT5_READY:
        raise HTTPException(status_code=503, detail="MT5 is not initialized or failed to connect to broker")
    # Quick health check
    if mt5.terminal_info() is None:
         # Try to reconnect
         logger.warning("Terminal disconnected. Attempting reconnect...")
         mt5.login(login=args.account, password=args.password, server=args.server)
         if mt5.terminal_info() is None:
             raise HTTPException(status_code=503, detail="Terminal disconnected.")

# --- API ROUTES ---
@app.get("/health")
async def health():
    return {
        "status": "up" if MT5_READY else "down", 
        "account": args.account,
        "connected": mt5.terminal_info().connected if MT5_READY and mt5.terminal_info() else False,
        "firestore_sync": bool(_firestore_db and args.user_id),
    }

@app.get("/account_info")
async def get_account_info():
    check_mt5()
    info = mt5.account_info()
    if info is None:
        raise HTTPException(status_code=500, detail="Failed to get account info")
    return info._asdict()

@app.get("/positions")
async def get_positions():
    check_mt5()
    positions = mt5.positions_get()
    if positions is None:
        return []
    return [p._asdict() for p in positions]

@app.get("/orders")
async def get_orders():
    check_mt5()
    orders = mt5.orders_get()
    if orders is None:
        return []
    return [o._asdict() for o in orders]

def resolve_symbol_local(target: str) -> str:
    """Maps generic symbol (e.g. EURUSD) to broker-specific (e.g. EURUSDx)"""
    if not target:
        return target
    clean = target.replace("/", "").upper()
    symbols = mt5.symbols_get()
    if not symbols:
        return clean
    for s in symbols:
        if s.name == clean:
            return s.name
    for s in symbols:
        if clean in s.name and len(s.name) <= len(clean) + 4:
            return s.name
    return clean

@app.post("/execute")
async def execute_trade(req: Request):
    """
    Expects JSON:
    {
        "symbol": "EURUSD",
        "action": "BUY" | "SELL" | "CLOSE" | "MODIFY",
        "volume": 0.01,
        "sl": 1.1000,
        "tp": 1.1100,
        "ticket": 1234567, # only for close/modify
        "comment": "Trade context"
    }
    """
    check_mt5()
    body = await req.json()
    action = body.get("action", "").upper()
    symbol = resolve_symbol_local(body.get("symbol", ""))
    
    if action in ["BUY", "SELL"]:
        # Prepare Order
        order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
        price = mt5.symbol_info_tick(symbol).ask if action == "BUY" else mt5.symbol_info_tick(symbol).bid
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(body.get("volume", 0.01)),
            "type": order_type,
            "price": price,
            "sl": float(body.get("sl", 0.0)),
            "tp": float(body.get("tp", 0.0)),
            "deviation": 20,
            "magic": 1001,
            "comment": body.get("comment", ""),
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise HTTPException(status_code=400, detail=f"Order failed: {result.comment} (Code: {result.retcode})")
            
        return {"success": True, "ticket": result.order, "price": result.price}
        
    elif action == "CLOSE":
        ticket = body.get("ticket")
        if not ticket: raise HTTPException(status_code=400, detail="ticket required")
        pos = mt5.positions_get(ticket=ticket)
        if not pos: raise HTTPException(status_code=404, detail="Position not found")
        p = pos[0]
        
        order_type = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = mt5.symbol_info_tick(p.symbol).bid if p.type == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(p.symbol).ask
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": p.symbol,
            "volume": p.volume,
            "type": order_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": 1001,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise HTTPException(status_code=400, detail=f"Close failed: {result.comment}")
        return {"success": True, "ticket": ticket}
        
    elif action == "MODIFY":
        ticket = body.get("ticket")
        sl = float(body.get("sl", 0.0))
        tp = float(body.get("tp", 0.0))
        
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": sl,
            "tp": tp
        }
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise HTTPException(status_code=400, detail=f"Modify failed: {result.comment}")
        return {"success": True, "ticket": ticket}
        
    raise HTTPException(status_code=400, detail="Invalid action")

@app.post("/sync_history")
async def trigger_sync(req: Request):
    """Manual trigger to re-sync history to Firestore"""
    if not _firestore_db or not args.user_id:
        raise HTTPException(status_code=400, detail="Firestore sync not configured (missing --user_id)")
    body = await req.json() if req.headers.get("content-length", "0") != "0" else {}
    days = body.get("days_back", 90)
    count = sync_history_to_firestore(days_back=days)
    return {"status": "success", "synced_trades": count}

@app.get("/symbols")
async def get_symbols():
    check_mt5()
    symbols = mt5.symbols_get()
    if not symbols: return []
    return [s.name for s in symbols]

@app.get("/symbol/{symbol}")
async def get_symbol_info(symbol: str):
    check_mt5()
    info = mt5.symbol_info(symbol)
    if not info: raise HTTPException(status_code=404, detail="Symbol not found")
    return info._asdict()

@app.get("/candles/{symbol}")
async def fetch_candles(symbol: str, timeframe: str = "H1", limit: int = 500):
    check_mt5()
    tf_map = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1, "W1": mt5.TIMEFRAME_W1, "MN1": mt5.TIMEFRAME_MN1
    }
    tf = tf_map.get(timeframe.upper(), mt5.TIMEFRAME_H1)
    
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, limit)
    if rates is None:
        raise HTTPException(status_code=500, detail=f"Failed to fetch rates for {symbol}")
        
    # Convert numpy array to list of dicts
    result = []
    for r in rates:
        result.append({
            "time": datetime.utcfromtimestamp(r['time']).isoformat() + "Z",
            "open": float(r['open']),
            "high": float(r['high']),
            "low": float(r['low']),
            "close": float(r['close']),
            "tick_volume": int(r['tick_volume'])
        })
    return result

@app.get("/history")
async def get_history(start_ts: int, end_ts: int):
    check_mt5()
    start_dt = datetime.utcfromtimestamp(start_ts)
    end_dt = datetime.utcfromtimestamp(end_ts)
    deals = mt5.history_deals_get(start_dt, end_dt)
    if deals is None:
        return []
    return [d._asdict() for d in deals]

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
