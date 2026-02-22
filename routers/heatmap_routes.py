"""
Heatmap API Routes
==================
Exposes the Market Scanner's pre-computed data to the frontend.
"""

from fastapi import APIRouter
from typing import Optional

router = APIRouter(prefix="/heatmap", tags=["Heatmap"])


def _get_cache():
    """Lazy import to avoid circular dependencies at module level."""
    from backend.services.market_scanner import scanner_cache
    return scanner_cache


@router.get("")
async def get_heatmap():
    """
    Returns the full heatmap: all symbols sorted by score.
    Used by the frontend HeatmapGrid component.
    """
    cache = _get_cache()
    heatmap = cache.get_heatmap()
    
    return {
        "symbols": heatmap,
        "count": len(heatmap),
        "last_scan": cache.last_scan_time.isoformat() if cache.last_scan_time else None,
    }


@router.get("/top/{count}")
async def get_top_symbols(count: int = 5):
    """
    Returns top N scoring symbols.
    Used by autonomous worker to pick best opportunities.
    """
    cache = _get_cache()
    heatmap = cache.get_heatmap()
    top = heatmap[:count]
    
    return {
        "symbols": top,
        "count": len(top),
        "last_scan": cache.last_scan_time.isoformat() if cache.last_scan_time else None,
    }


@router.get("/{symbol}")
async def get_symbol_detail(symbol: str):
    """
    Returns the full pre-computed analysis for one symbol.
    Includes technical data, behavior DNA, confluence, COT, macro.
    """
    cache = _get_cache()
    data = cache.get(symbol.upper())
    
    if not data:
        return {
            "status": "not_found",
            "message": f"Symbol {symbol} not in scanner watchlist or not yet scanned.",
            "available": [s["symbol"] for s in cache.get_heatmap()],
        }
    
    return {
        "status": "ok",
        "data": data,
    }
