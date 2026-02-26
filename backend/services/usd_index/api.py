from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
import os
import sys

# Add project root to path to ensure imports work if run standalone
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from backend.services.usd_index.index_engine import USDIndexEngine
from backend.core.system_state import world_state  # Import World State

router = APIRouter(prefix="/api/usd_index", tags=["USD Index"])
# app = FastAPI(title="USD Composite Fundamental Index", version="1.0")

# Initialize Engine
# Ensure we map the config path correctly
base_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(base_dir, "config.yaml")

engine = USDIndexEngine(config_path=config_path)

class ComponentData(BaseModel):
    timestamp: str
    composite_index: float
    signal: str
    signal_value: int
    components: Dict[str, float]

class HistoryItem(BaseModel):
    timestamp: str
    composite_index: float
    signal_value: int
    signal_label: str

@router.get("/latest", response_model=ComponentData)
def get_latest_index():
    try:
        data = engine.get_latest()
        if not data:
            raise HTTPException(status_code=503, detail="Index calculation failed or no data available.")
        
        # STREAM INJECTION: Push to Neural Stream
        msg = f"USD Index: {data['signal']} ({data['composite_index']:.2f})."
        # Check if we should log (avoid spamming if same)
        # For now, just log on every poll to show "Heartbeat" or maybe restrict?
        # Let's log.
        world_state.add_log(agent="Global Macro", message=msg, type="MACRO")
        
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/history", response_model=List[HistoryItem])
def get_index_history():
    try:
        data = engine.get_history()
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/health")
def health_check():
    return {"status": "ok"}
