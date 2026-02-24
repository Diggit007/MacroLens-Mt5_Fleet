
import asyncio
import logging
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from typing import Optional
from fastapi import HTTPException 
import uuid
from datetime import datetime

# Add parent directory to path to allow imports from 'backend'
# Assumes this file is in c:\MacroLens\Backend
sys.path.append(str(Path(__file__).resolve().parent.parent))

# Load Environment
BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(BACKEND_DIR / ".env") 

# Setup Logging
from backend.core.logger import setup_logger
logger = setup_logger("WORKER_TRADE")

# Firebase
from backend.firebase_setup import initialize_firebase
FIRESTORE_DB = initialize_firebase()

# Caches for Worker
USER_MAPPING_CACHE = {} 
from backend.core.cache import cache
from backend.config import settings
DEFAULT_ACCOUNT_ID = settings.META_API_ACCOUNT_ID or None

# Services
from backend.services.trade_manager_service import trade_manager
from backend.workers.firestore_listener import start_firestore_listener
from backend.services.metaapi_service import (
    execute_trade as meta_execute_trade,
    fetch_candles as meta_fetch_candles
)
from backend.core.database import DatabasePool

# --- Helper Functions (Duplicated from main.py for Isolation) ---

def is_valid_uuid(val):
    try:
        uuid.UUID(str(val))
        return True
    except ValueError:
        return False

async def resolve_account_id(user_id: str, requested_id: Optional[str] = None, strict: bool = False) -> str:
    """
    Intelligent Account Resolver (Worker Version)
    """
    # 0. Validate Request Param
    if requested_id:
        if is_valid_uuid(requested_id):
            return requested_id
        else:
            logger.warning(f"Invalid requested account_id: {requested_id}. Falling back to default resolution.")

    cache_key = f"user_mapping:{user_id}"

    # 1. Check Cache
    if user_id in USER_MAPPING_CACHE:
        cached = USER_MAPPING_CACHE[user_id]
        c_id = cached.get("account_id")
        if c_id != DEFAULT_ACCOUNT_ID and not is_valid_uuid(c_id):
             logger.warning(f"Invalid Cached Account ID {c_id} for user {user_id}. Ignoring.")
        else:
            # Strict Mode Check
            if strict and c_id == DEFAULT_ACCOUNT_ID:
                if not cached.get("is_privileged"): 
                    # For worker, we raise exception or return None
                    raise Exception("No linked trading account found (Strict Mode).")
            return c_id

    # Lookup in Firestore
    account_id = None
    is_privileged = False
    
    if FIRESTORE_DB:
        try:
            doc = FIRESTORE_DB.collection('users').document(user_id).get()
            if doc.exists:
                data = doc.to_dict()
                if data.get('role') == 'admin':
                    is_privileged = True
                found = data.get('activeAccountId') or data.get('metaapiAccountId') or data.get('accountId')
                
                # --- AUTO-HEAL: Validate Account Exists ---
                valid_account_id = None
                
                # 1. If we have a candidate, check if it exists in mt5_accounts
                if found and found != "2oCCIawGhcpflqdPguRl":
                    # Query mt5_accounts where accountId == found
                    acct_query = FIRESTORE_DB.collection('mt5_accounts').where('accountId', '==', found).limit(1).get()
                    if len(acct_query) > 0:
                        valid_account_id = found
                    else:
                        logger.warning(f"Active Account {found} not found in DB. Searching for alternative...")
                
                # 2. If no valid account yet, search for ANY valid account for this user
                if not valid_account_id:
                     user_accounts = FIRESTORE_DB.collection('mt5_accounts').where('userId', '==', user_id).limit(1).get()
                     if len(user_accounts) > 0:
                         valid_account_id = user_accounts[0].to_dict().get('accountId')
                         logger.info(f"Full-Healing: Switching User {user_id} to Account {valid_account_id}")
                         # Update User Doc asynchronously-ish
                         FIRESTORE_DB.collection('users').document(user_id).update({"activeAccountId": valid_account_id})
                
                if valid_account_id:
                     account_id = valid_account_id
                     
        except Exception as e:
            logger.error(f"Firestore Account Lookup Failed for {user_id}: {e}")
            
    if not account_id and is_privileged:
        account_id = DEFAULT_ACCOUNT_ID

    # If found (either specific or privileged default), return and cache
    if account_id:
        cache_data = {
            "account_id": account_id,
            "is_privileged": is_privileged
        }
        # Update local cache
        USER_MAPPING_CACHE[user_id] = cache_data
        return account_id

    # Fallback
    if strict:
        raise Exception("No linked trading account found.")

    # Lazy Fallback
    fallback_data = {
        "account_id": DEFAULT_ACCOUNT_ID,
        "is_privileged": False
    }
    USER_MAPPING_CACHE[user_id] = fallback_data
    return DEFAULT_ACCOUNT_ID

async def execute_trade_logic(user_id: str, action: str, symbol: str, volume: Optional[float], sl: Optional[float], tp: Optional[float], ticket: int, value: Optional[float] = None):
    try:
        target_account_id = await resolve_account_id(user_id, strict=True)
        res = await meta_execute_trade(
            account_id=target_account_id, 
            symbol=symbol, 
            action=action.upper(), 
            volume=volume, 
            sl=sl, 
            tp=tp,
            ticket=ticket,
            value=value
        )
        return res
    except Exception as e:
        logger.error(f"Trade Error: {e}")
        return {"success": False, "error": str(e)}

async def process_firestore_trade(user_id: str, cmd_type: str, payload: dict):
    logger.info(f"Worker Firestore Trade: {cmd_type} for {user_id}")
    try:
        if cmd_type == 'PLACE_ORDER':
            return await execute_trade_logic(user_id, payload.get('side', 'BUY'), payload.get('symbol', ''), 
                                             float(payload.get('lots', 0.01)), float(payload.get('sl', 0)), float(payload.get('tp', 0)), 0)
        elif cmd_type in ['CLOSE_TRADE', 'CLOSE_POS']:
            return await execute_trade_logic(user_id, 'close', '', None, None, None, int(payload.get('ticket', 0)))
        elif cmd_type == 'MODIFY_SL':
            return await execute_trade_logic(user_id, 'modify', '', None, float(payload.get('sl', 0)), None, int(payload.get('ticket', 0)))
        elif cmd_type == 'MODIFY_TP':
            return await execute_trade_logic(user_id, 'modify', '', None, None, float(payload.get('tp', 0)), int(payload.get('ticket', 0)))
        elif cmd_type == 'CLOSE_ALL':
            return await execute_trade_logic(user_id, 'close_all', '', None, None, None, 0)
    except Exception as he:
        logger.warning(f"Trade Blocked for {user_id}: {he}")
        return {"status": "error", "msg": str(he)}
    return {"status": "ignored", "msg": f"Unknown command type: {cmd_type}"}

async def fetch_bridge_candles(user_id: str, symbol: str, timeframe: str, count: int = 100):
    try:
        # Relaxed Strictness: Allow all users to see charts using Local MT5 data
        target_account_id = await resolve_account_id(user_id, strict=False)
        if not target_account_id: target_account_id = "shared_terminal"
        
        mt5_symbol = symbol.replace("/", "").replace("USDT", "USD")
        candles = await meta_fetch_candles(target_account_id, mt5_symbol, timeframe, count)
        formatted_candles = []
        if candles:
            for c in candles:
                candle = c if isinstance(c, dict) else c.__dict__
                if 'time' in candle:
                     if isinstance(candle['time'], datetime):
                         candle['datetime'] = candle['time'].isoformat()
                         candle['time'] = int(candle['time'].timestamp()) 
                     elif isinstance(candle['time'], str):
                         candle['datetime'] = candle['time'] 
                formatted_candles.append(candle)
        return formatted_candles
    except Exception as e:
        logger.error(f"Worker MetApi Candle Fetch Error: {e}")
        return None

# --- Main Worker Loop ---

async def main():
    logger.info("--------------------------------------------------")
    logger.info("!!! MACROLENS WORKER: TRADE MANAGER STARTING !!!")
    logger.info("--------------------------------------------------")

    # 1. DB Health Check
    db_ok = await DatabasePool.health_check()
    logger.info(f"Database Health Check: {'OK' if db_ok else 'FAIL'}")
    
    # 2. Start Trade Manager Service
    asyncio.create_task(trade_manager.start())
    logger.info("Trade Manager Service initialized (Worker Mode).")
    
    # 3. Start Firestore Listener
    asyncio.create_task(start_firestore_listener(fetch_bridge_candles, process_firestore_trade))
    logger.info("Firestore Listener initialized (Worker Mode).")

    # Keep alive
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logger.info("Worker Stopping...")
        await trade_manager.stop()
        logger.info("Worker Stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
