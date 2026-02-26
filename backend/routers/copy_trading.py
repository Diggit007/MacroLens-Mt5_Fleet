from fastapi import APIRouter, HTTPException, Depends
from backend.services.copy_trading_service import copy_trading_service
from backend.core.database import DatabasePool
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

router = APIRouter(prefix="/api/copy-trading", tags=["Copy Trading"])

class SubscribeRequest(BaseModel):
    master_account_id: str
    risk_multiplier: float

class SubscriptionStatus(BaseModel):
    status: str
    master_account_id: Optional[str]
    risk_multiplier: float

class AgentThought(BaseModel):
    timestamp: str
    symbol: str
    signal: str
    confidence: int
    reasoning: str
    action: str

@router.get("/status/{user_account_id}", response_model=SubscriptionStatus)
async def get_subscription_status(user_account_id: str):
    """Checks if the user is currently subscribed."""
    try:
        db = await DatabasePool.get_connection()
        row = await db.fetch_one(
            "SELECT status, master_account_id, risk_multiplier FROM copy_subscriptions WHERE user_account_id = ?", 
            (user_account_id,)
        )
        if row:
            return {
                "status": row[0],
                "master_account_id": row[1],
                "risk_multiplier": row[2]
            }
        return {"status": "INACTIVE", "master_account_id": None, "risk_multiplier": 1.0}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/subscribe/{user_account_id}")
async def subscribe(user_account_id: str, req: SubscribeRequest):
    """Enable Copy Trading."""
    result = await copy_trading_service.subscribe_user(user_account_id, req.master_account_id, req.risk_multiplier)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result

@router.post("/unsubscribe/{user_account_id}")
async def unsubscribe(user_account_id: str):
    """Disable Copy Trading."""
    success = await copy_trading_service.unsubscribe_user(user_account_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to unsubscribe")
    return {"status": "unsubscribed"}

@router.get("/feed", response_model=List[AgentThought])
async def get_agent_feed(limit: int = 50):
    """Get the latest thoughts from the AI Agent."""
    try:
        db = await DatabasePool.get_connection()
        rows = await db.fetch_all(
            "SELECT timestamp, symbol, signal, confidence, reasoning, action FROM agent_thoughts ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        )
        return [
            {
                "timestamp": r[0],
                "symbol": r[1],
                "signal": r[2],
                "confidence": r[3],
                "reasoning": r[4],
                "action": r[5]
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
