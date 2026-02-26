from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import logging
import asyncio
from datetime import datetime

from backend.middleware.auth import get_current_user
from backend.services import metaapi_service
from backend.core.cache import cache
from backend.firebase_setup import initialize_firebase

router = APIRouter(prefix="/api/trade", tags=["trade"])
logger = logging.getLogger(__name__)

# Initialize Firestore
db = initialize_firebase()

# --- Models ---
class TradeExecutionRequest(BaseModel):
    user_id: str
    action: str
    symbol: Optional[str] = ""
    volume: Optional[float] = 0.0
    sl: Optional[float] = 0.0
    tp: Optional[float] = 0.0
    ticket: Optional[int] = 0
    value: Optional[float] = 0.0
    comment: Optional[str] = "MacroLens"

# --- Endpoints ---

# --- New Import ---
from backend.services.mt5_data_fetcher import mt5_fetcher

@router.post("/execute")
async def execute_trade(
    req: TradeExecutionRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user)
):
    """
    Execute a trade action.
    Target symbol is automatically resolved from Standard (e.g. EURUSD) to Broker (e.g. EURUSD.m).
    """
    try:
        # 1. Get User's Active Account & Symbol Map
        user_ref = db.collection("users").document(user['uid'])
        user_doc = user_ref.get()
        if not user_doc.exists:
             raise HTTPException(status_code=404, detail="User Profile Not Found")
             
        profile = user_doc.to_dict()
        account_id = profile.get('activeAccountId')
        
        if not account_id:
             raise HTTPException(status_code=400, detail="No Active Trading Account Linked")
             
        # Resolve Symbol from Map
        target_symbol = req.symbol
        if req.symbol:
            symbol_map = profile.get('settings', {}).get('symbolMapping', {})
            # If map exists, try to find the broker symbol
            if symbol_map and req.symbol in symbol_map:
                target_symbol = symbol_map[req.symbol]
                # logger.info(f"Resolved Symbol: {req.symbol} -> {target_symbol}")
            else:
                # If not in map, maybe it's already a broker symbol? Or map is missing.
                # We proceed with original.
                pass

        # 2. Execute Trade (Sync or Async)
        action = req.action.upper()
        # [OPTIMIZATION] Bulk actions run in background immediately
        is_bulk = action in ['CLOSE_ALL', 'DELETE_PENDING', 'LOCK_PROFIT', 'LOCK_MONEY']
        
        if is_bulk:
            background_tasks.add_task(
                metaapi_service.execute_trade, 
                account_id, target_symbol, action, req.volume, req.sl, req.tp, req.comment, req.ticket, req.value
            )
            return {"success": True, "message": f"{action} Queued", "status": "queued"}
            
        else:
            logger.info(f"Executing {action} {target_symbol} for {user['uid']} on {account_id}")
            
            result = await metaapi_service.execute_trade(
                account_id, target_symbol, action, req.volume, req.sl, req.tp, req.comment, req.ticket, req.value
            )
            
            if not result.get("success"):
                raise HTTPException(status_code=400, detail=result.get("error", "Execution Failed"))
                
            return result

    except Exception as e:
        logger.error(f"Trade Execution Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/status")
async def get_trade_status(user_id: str, user: dict = Depends(get_current_user)):
    """
    Get Open Positions and Account Info.
    """
    try:
        if user_id != user['uid']:
             raise HTTPException(status_code=403, detail="Unauthorized")
             
        user_ref = db.collection("users").document(user['uid'])
        user_doc = user_ref.get()
        if not user_doc.exists:
             return {"positions": [], "balance": 0, "equity": 0}
             
        profile = user_doc.to_dict()
        account_id = profile.get('activeAccountId')
        
        if not account_id:
            return {"positions": [], "balance": 0, "equity": 0}
            
        info = await metaapi_service.get_account_information(account_id)
        return info
        
    except Exception as e:
        logger.error(f"Trade Status Error: {e}")
        return {"positions": [], "error": str(e)}

@router.get("/history")
async def get_trade_history(
    user_id: str, 
    period: str = "today", 
    user: dict = Depends(get_current_user)
):
    """
    Get Trade History.
    """
    try:
        user_ref = db.collection("users").document(user['uid'])
        user_doc = user_ref.get()
        if not user_doc.exists: return []
        
        profile = user_doc.to_dict()
        account_id = profile.get('activeAccountId')
        
        if not account_id: return []
        
        history = await metaapi_service.fetch_history(account_id, period)
        return history.get("history", [])
        
    except Exception as e:
        logger.error(f"History Error: {e}")
        return []
        
# --- Trade Manager Settings Endpoints ---

class TradeSettingsUpdate(BaseModel):
    settings: Dict[str, Any]

@router.get("/settings")
async def get_trade_settings(user: dict = Depends(get_current_user)):
    """Get Trade Manager Settings"""
    try:
        from backend.services.trade_manager_service import trade_manager
        return await trade_manager.get_settings(user['uid'])
    except Exception as e:
        logger.error(f"Error getting settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/settings")
async def update_trade_settings(req: TradeSettingsUpdate, user: dict = Depends(get_current_user)):
    """Update Trade Manager Settings"""
    try:
        from backend.services.trade_manager_service import trade_manager
        success = await trade_manager.update_settings(user['uid'], req.settings)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update settings")
        return {"status": "success", "settings": req.settings}
    except Exception as e:
        logger.error(f"Error updating settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/candles")
async def get_candles(
    symbol: str,
    timeframe: str = "H1",
    limit: int = 1000,
    user: dict = Depends(get_current_user)
):
    """
    Get Historical Candles for Charting.
    Resolves symbol and account automatically.
    """
    try:
        # 1. Resolve Account
        user_ref = db.collection("users").document(user['uid'])
        user_doc = user_ref.get()
        if not user_doc.exists:
             raise HTTPException(status_code=404, detail="User Profile Not Found")
             
        profile = user_doc.to_dict()
        account_id = profile.get('activeAccountId')
        
        if not account_id:
             raise HTTPException(status_code=400, detail="No Active Trading Account Linked")

        # 2. Resolve Symbol (Optional, but good practice if frontend sends 'clean' names)
        target_symbol = symbol
        symbol_map = profile.get('settings', {}).get('symbolMapping', {})
        if symbol_map and symbol in symbol_map:
            target_symbol = symbol_map[symbol]

        # 3. Fetch Candles
        # metaapi_service.fetch_candles handles checking local terminal or cloud
        candles = await metaapi_service.fetch_candles(account_id, target_symbol, timeframe, limit)
        
        return candles

    except Exception as e:
        logger.error(f"Candle Fetch Failed: {e}")
        # Return empty list on failure to not break chart
        return []

@router.post("/sync-assets")
async def sync_assets(user: dict = Depends(get_current_user)):
    """
    Fetches all tradable symbols from the user's active broker account
    and saves them to their profile for the Frontend to use.
    Also clears any stale 'symbolMapping' to force re-resolution.
    """
    try:
        user_id = user['uid']
        user_ref = db.collection("users").document(user_id)
        user_doc = user_ref.get()
        
        if not user_doc.exists:
             raise HTTPException(status_code=404, detail="User Profile Not Found")
             
        profile = user_doc.to_dict()
        account_id = profile.get('activeAccountId')
        
        if not account_id:
             raise HTTPException(status_code=400, detail="No Active Trading Account Linked")
             
        # Fetch Symbols from Broker
        # PRIORITIZE LOCAL MT5 (Server Terminal) as requested
        from backend.services.mt5_data_fetcher import mt5_fetcher
        
        symbols = []
        if mt5_fetcher.initialize():
            logger.info("Syncing assets from Local MT5 Terminal...")
            symbols = mt5_fetcher.get_all_symbols()
        
        # Fallback to MetaApi if local fetch returns nothing or fails
        if not symbols:
            logger.warning("Local MT5 symbols empty. Falling back to MetaApi Cloud...")
            symbols = await metaapi_service.get_all_symbols(account_id)
        
        if not symbols:
             raise HTTPException(status_code=500, detail="Failed to fetch symbols from broker (Local & Cloud failed).")
             
        # Update User Profile
        # 1. Update availableSymbols
        # 2. CLEAR symbolMapping to remove stale entries
        user_ref.update({
             "settings.availableSymbols": symbols,
             "settings.symbolMapping": {}, # Clear mapping logic
             "settings.lastSymbolSync": datetime.utcnow().isoformat()
        })
        
        return {
            "status": "success", 
            "count": len(symbols), 
            "message": f"Synced {len(symbols)} assets from Server MT5. Mappings cleared."
        }
        
    except Exception as e:
        logger.error(f"Asset Sync Failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
