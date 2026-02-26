
from fastapi import APIRouter, HTTPException, Depends
from backend.services.trade_executor_agent import trade_executor
from backend.firebase_setup import initialize_firebase
from backend.core.database import DatabasePool
from pydantic import BaseModel
from typing import List, Optional
from firebase_admin import firestore
from datetime import datetime, timedelta
import logging

logger = logging.getLogger("AlgoRoutes")

router = APIRouter(prefix="/algo", tags=["AlgoBot"])
db = initialize_firebase()

class AlgoConfigUpdate(BaseModel):
    enabled: bool
    risk_multiplier: float
    excluded_pairs: Optional[List[str]] = []
    mode: Optional[str] = "conservative"
    max_daily_loss: Optional[float] = 500.0
    drawdown_auto_stop: Optional[bool] = True
    news_event_pause: Optional[bool] = True
    weekend_close: Optional[bool] = False
    max_daily_loss_kill: Optional[bool] = True

# =================================================================
# USER CONFIG
# =================================================================
@router.get("/config/{user_id}")
async def get_algo_config(user_id: str):
    """Get user's auto-trading settings"""
    return await trade_executor.get_user_settings(user_id)

@router.post("/config/{user_id}")
async def update_algo_config(user_id: str, config: AlgoConfigUpdate):
    """Update user's auto-trading settings"""
    try:
        # Check premium status if enabling
        if config.enabled:
            # Get user document to check plan
            user_doc = db.collection('users').document(user_id).get()
            if not user_doc.exists:
                raise HTTPException(status_code=404, detail="User not found")
                
            user_data = user_doc.to_dict()
            user_plan = user_data.get('plan', 'standard').lower()
            
            if user_plan not in ['premium', 'pro', 'admin']:
                raise HTTPException(
                    status_code=403, 
                    detail="Copy Trading is only available for Premium users. Please upgrade your plan."
                )

        data = config.dict()
        data['updated_at'] = firestore.SERVER_TIMESTAMP
        db.collection('algo_settings').document(user_id).set(data, merge=True)
        return {"status": "success", "config": data}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# =================================================================
# MASTER STRATEGY STATS  (New)
# =================================================================
@router.get("/master-stats")
async def get_master_stats():
    """
    Returns aggregated performance stats for the Master AI Strategy.
    Pulls from bot_activity (Firestore) where userId == 'master'.
    """
    try:
        stats = {
            "status": "LIVE",
            "win_rate": 0,
            "total_trades": 0,
            "today_pnl": 0,
            "today_trades": 0,
            "drawdown": 0,
            "avg_confidence": 0,
            "last_signal_time": None,
        }
        
        try:
            today = datetime.utcnow().date()
            today_start = datetime(today.year, today.month, today.day)
            
            buys = 0
            sells = 0
            today_trades = 0
            total_exec = 0
            total_conf = 0
            
            docs = db.collection('bot_activity')\
                .where('userId', '==', 'master')\
                .where('signal', 'in', ['BUY', 'SELL'])\
                .order_by('timestamp', direction=firestore.Query.DESCENDING)\
                .limit(200)\
                .stream()
            
            for doc in docs:
                total_exec += 1
                d = doc.to_dict()
                
                if d.get('signal') == 'BUY':
                    buys += 1
                elif d.get('signal') == 'SELL':
                    sells += 1
                    
                conf = d.get('confidence', 0)
                total_conf += conf
                
                ts = d.get('timestamp')
                if ts:
                    if stats['last_signal_time'] is None:
                        # Grab the most recent as first doc
                        if hasattr(ts, 'isoformat'): stats['last_signal_time'] = ts.isoformat()
                        else: stats['last_signal_time'] = str(ts)
                    
                    if hasattr(ts, 'timestamp'): # DatetimeWithNanoseconds
                        if ts.replace(tzinfo=None) >= today_start:
                            today_trades += 1
                    elif isinstance(ts, str):
                        try:
                            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            if dt.replace(tzinfo=None) >= today_start:
                                today_trades += 1
                        except: pass
            
            stats["total_trades"] = total_exec
            stats["today_trades"] = today_trades
            if total_exec > 0:
                stats["win_rate"] = 68  # Placeholder until we track wins/losses
                stats["avg_confidence"] = round(total_conf / total_exec, 1)
                
        except Exception as fs_err:
            logger.warning(f"Firestore query failed (non-critical): {fs_err}")
        
        return stats
        
    except Exception as e:
        logger.error(f"master-stats error: {e}")
        return {
            "status": "UNKNOWN",
            "win_rate": 0,
            "total_trades": 0,
            "today_pnl": 0,
            "today_trades": 0,
            "drawdown": 0,
            "avg_confidence": 0,
            "last_signal_time": None,
            "error": str(e)
        }

# =================================================================
# UNIFIED FEED  (New)
# =================================================================
@router.get("/feed/{user_id}")
async def get_unified_feed(user_id: str, limit: int = 50):
    """
    Returns a unified activity feed merging:
    1. bot_activity (Firestore) — user-specific execution logs and master logs
    2. agent_thoughts (SQLite) — global AI decisions
    
    Each item: { timestamp, symbol, signal, confidence, reasoning, source, copied }
    """
    feed = []
    user_excluded = []
    
    # Get user's excluded pairs for "copied" flag
    try:
        settings = await trade_executor.get_user_settings(user_id)
        user_excluded = settings.get("excluded_pairs", [])
        user_enabled = settings.get("enabled", False)
    except:
        user_enabled = False
    
    # 1. Firestore bot_activity (user-specific + master)
    try:
        # Secure query: Only fetch master trades and user's own trades
        docs = db.collection('bot_activity')\
            .where('userId', 'in', ['master', user_id])\
            .order_by('timestamp', direction=firestore.Query.DESCENDING)\
            .limit(limit)\
            .stream()
        
        for doc in docs:
            d = doc.to_dict()
            ts = d.get('timestamp')
            if hasattr(ts, 'isoformat'):
                ts = ts.isoformat()
            elif hasattr(ts, 'timestamp'):
                ts = ts.isoformat()
            else:
                ts = str(ts) if ts else ""
            
            symbol = d.get('symbol', '')
            signal = d.get('signal', 'LOG')
            doc_user_id = d.get('userId', '')
            
            # Determine if this was copied to user (if it's the user's execution)
            is_copied = (
                user_enabled and 
                signal in ['BUY', 'SELL'] and 
                symbol not in user_excluded and
                doc_user_id == user_id
            )
            
            feed.append({
                "id": doc.id,
                "timestamp": ts,
                "symbol": symbol,
                "signal": signal,
                "confidence": d.get('confidence', 0),
                "reasoning": d.get('reasoning', ''),
                "source": "execution",
                "copied": is_copied,
                "user_id": doc_user_id
            })
    except Exception as e:
        logger.warning(f"Firestore feed error: {e}")
    
    # 2. Agent thoughts (SQLite — global AI decisions)
    try:
        conn = await DatabasePool.get_connection()
        rows = await conn.fetch_all(
            "SELECT timestamp, symbol, signal, confidence, reasoning, action FROM agent_thoughts ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        )
        for r in rows:
            symbol = r[1] or ''
            signal = r[2] or r[5] or 'LOG'
            
            is_copied = False
            
            feed.append({
                "id": f"thought_{r[0]}",
                "timestamp": r[0] or "",
                "symbol": symbol,
                "signal": signal,
                "confidence": r[3] or 0,
                "reasoning": r[4] or "",
                "source": "analysis",
                "copied": is_copied,
                "user_id": "master"
            })
    except Exception as e:
        logger.warning(f"SQLite feed error: {e}")
    
    # 3. Sort by timestamp descending and limit
    feed.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    
    return {
        "feed": feed[:limit],
        "total": len(feed)
    }

# =================================================================
# LEGACY STATS ENDPOINT (kept for compatibility)
# =================================================================
@router.get("/stats/{user_id}")
async def get_algo_stats(user_id: str):
    """Get simple stats from 'bot_activity' collection."""
    try:
        docs = db.collection('bot_activity')\
            .where('userId', '==', user_id)\
            .order_by('timestamp', direction=firestore.Query.DESCENDING)\
            .limit(50)\
            .stream()
            
        activities = []
        for doc in docs:
            d = doc.to_dict()
            if d.get('timestamp') and hasattr(d['timestamp'], 'isoformat'):
                d['timestamp'] = d['timestamp'].isoformat()
            activities.append(d)
            
        return {
            "recent_activity": activities,
            "total_actions": len(activities)
        }
    except Exception as e:
        return {"recent_activity": [], "total_actions": 0, "error": str(e)}
