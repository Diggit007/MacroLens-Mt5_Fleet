"""
Macro Data Router
=================
Exposes MacroDataEngine country health profiles and MacroDivergence scanner
for the Dashboard redesign. Cached for 15 minutes to avoid expensive recalculations.
"""
import logging
import time
from typing import Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("API")

router = APIRouter(prefix="/api/macro", tags=["Macro Intelligence"])

# --- In-Memory Cache (simple TTL) ---
_cache: Dict[str, dict] = {}
CACHE_TTL = 900  # 15 minutes


def _get_cached(key: str):
    entry = _cache.get(key)
    if entry and time.time() - entry["ts"] < CACHE_TTL:
        return entry["data"]
    return None


def _set_cached(key: str, data):
    _cache[key] = {"data": data, "ts": time.time()}


# --- Response Models ---

class CountryHealthResponse(BaseModel):
    currency: str
    health_score: float
    growth_score: float
    inflation_score: float
    monetary_score: float
    real_rate: Optional[float] = None
    cot_willco: Optional[float] = None
    gdp_value: Optional[float] = None
    gdp_momentum: Optional[float] = None
    cpi_value: Optional[float] = None
    cpi_momentum: Optional[float] = None
    rate_value: Optional[float] = None


class DivergenceResponse(BaseModel):
    symbol: str
    divergence_score: float
    recommendation: str
    conviction: str
    carry_spread: float
    momentum_delta: float
    cot_aligned: bool
    base_score: float
    quote_score: float
    rationale: str


# --- Endpoints ---

@router.get("/health", response_model=List[CountryHealthResponse])
def get_country_health():
    """Returns macro health profiles for 8 major currencies, sorted by score."""
    cached = _get_cached("health")
    if cached:
        return cached

    try:
        from backend.services.macro_data_engine import MacroDataEngine
        engine = MacroDataEngine()
        profiles = engine.get_all_country_health()

        result = []
        for currency, p in profiles.items():
            result.append({
                "currency": currency,
                "health_score": round(p.health_score, 2),
                "growth_score": round(p.growth_score, 2),
                "inflation_score": round(p.inflation_score, 2),
                "monetary_score": round(p.monetary_score, 2),
                "real_rate": round(p.real_rate, 2) if p.real_rate else None,
                "cot_willco": round(p.cot_willco, 1) if p.cot_willco else None,
                "gdp_value": p.gdp_growth,
                "gdp_momentum": round(p.gdp_momentum, 3) if p.gdp_momentum else None,
                "cpi_value": p.inflation_rate,
                "cpi_momentum": round(p.cpi_momentum, 3) if p.cpi_momentum else None,
                "rate_value": p.interest_rate,
            })

        # Sort strongest first
        result.sort(key=lambda x: x["health_score"], reverse=True)
        _set_cached("health", result)
        return result

    except Exception as e:
        logger.error(f"Macro health endpoint error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/divergence", response_model=List[DivergenceResponse])
def get_macro_divergence():
    """Returns top divergent pairs sorted by score, with conviction and rationale."""
    cached = _get_cached("divergence")
    if cached:
        return cached

    try:
        from backend.services.macro_divergence import MacroDivergence
        scanner = MacroDivergence()
        results = scanner.scan_for_divergence()

        # Return top 10
        top = results[:10]
        _set_cached("divergence", top)
        return top

    except Exception as e:
        logger.error(f"Macro divergence endpoint error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/strength")
def get_currency_strength():
    """Returns currencies ranked by health score (strongest first)."""
    cached = _get_cached("strength")
    if cached:
        return cached

    try:
        from backend.services.macro_data_engine import MacroDataEngine
        engine = MacroDataEngine()
        profiles = engine.get_all_country_health()

        ranking = []
        for currency, p in profiles.items():
            ranking.append({
                "currency": currency,
                "score": round(p.health_score, 2),
                "growth": round(p.growth_score, 2),
                "inflation": round(p.inflation_score, 2),
                "monetary": round(p.monetary_score, 2),
                "cot": round(p.cot_willco, 1) if p.cot_willco else 50.0,
            })

        ranking.sort(key=lambda x: x["score"], reverse=True)
        _set_cached("strength", ranking)
        return ranking

    except Exception as e:
        logger.error(f"Currency strength endpoint error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
