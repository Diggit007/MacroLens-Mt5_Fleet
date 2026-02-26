
from fastapi import APIRouter, HTTPException, Query
from backend.services.cot.engine import COTEngine
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/cot", tags=["COT"])

engine = COTEngine()

class COTResponse(BaseModel):
    symbol: str
    date: str
    smart_sentiment: float
    smart_net: float
    willco_index: float
    oi: float
    hedge_net: float
    hedge_sentiment: float
    hedge_willco: float

@router.get("/latest", response_model=COTResponse)
def get_latest_cot(symbol: str = Query(..., description="Symbol (e.g., EURUSD)")):
    try:
        data = engine.get_latest_sentiment(symbol)
        if not data:
            raise HTTPException(status_code=404, detail=f"No COT data found for {symbol}")
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
