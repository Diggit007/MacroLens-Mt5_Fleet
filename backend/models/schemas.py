
from pydantic import BaseModel
from typing import Optional, List, Dict

class LoginRequest(BaseModel):
    user_id: Optional[str] = None
    mt5_login: str = ""
    mt5_password: str = ""
    mt5_server: str = ""

class AnalysisRequest(BaseModel):
    user_id: Optional[str] = None
    symbol: str
    timeframe: str = "1h"

class TradeRequest(BaseModel):
    user_id: Optional[str] = None
    action: str 
    symbol: str = ""
    volume: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    ticket: int = 0
    value: Optional[float] = None
    comment: str = "MacroLens"
