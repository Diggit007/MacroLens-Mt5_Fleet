"""
Scanner Dashboard API Routes (Admin Only)
==========================================
Aggregates ALL analysis pipeline data into a single endpoint
for the admin Scanner Dashboard.
"""

import asyncio
import logging
from typing import Dict, List, Optional
from fastapi import APIRouter

logger = logging.getLogger("ScannerRoutes")

from fastapi import APIRouter, Depends
from backend.middleware.auth import get_current_user

router = APIRouter(prefix="/api/scanner", tags=["Scanner Dashboard"])


def _get_scanner_cache():
    try:
        from backend.services.market_scanner import scanner_cache
        return scanner_cache
    except ImportError:
        return None


def _get_cot_all() -> List[Dict]:
    """Get COT data for ALL available CFTC instruments."""
    try:
        # Use singleton engine to avoid reloading data on every request
        from backend.services.cot.api import engine
        from backend.services.cot.engine import CFTC_NAMES

        results = []
        for symbol in CFTC_NAMES.keys():
            try:
                data = engine.get_latest_sentiment(symbol)
                if data:
                    # Add color coding hints
                    willco = data.get("willco_index", 50)
                    if willco > 75:
                        data["willco_zone"] = "EXTREME_BULLISH"
                    elif willco > 60:
                        data["willco_zone"] = "BULLISH"
                    elif willco < 25:
                        data["willco_zone"] = "EXTREME_BEARISH"
                    elif willco < 40:
                        data["willco_zone"] = "BEARISH"
                    else:
                        data["willco_zone"] = "NEUTRAL"

                    hedge_willco = data.get("hedge_willco", 50)
                    if hedge_willco > 75:
                        data["hedge_zone"] = "EXTREME_BULLISH"
                    elif hedge_willco > 60:
                        data["hedge_zone"] = "BULLISH"
                    elif hedge_willco < 25:
                        data["hedge_zone"] = "EXTREME_BEARISH"
                    elif hedge_willco < 40:
                        data["hedge_zone"] = "BEARISH"
                    else:
                        data["hedge_zone"] = "NEUTRAL"

                    results.append(data)
            except Exception as e:
                logger.warning(f"COT fetch failed for {symbol}: {e}")
                continue

        return results
    except Exception as e:
        logger.error(f"COT fetch failed: {e}")
        return []


def _get_macro_divergence() -> List[Dict]:
    """Get macro divergence data for all pairs."""
    try:
        from backend.services.macro_divergence import MacroDivergence
        scanner = MacroDivergence()
        return scanner.scan_for_divergence()
    except Exception as e:
        logger.error(f"Macro divergence fetch failed: {e}")
        return []


async def _get_events() -> List[Dict]:
    """Get upcoming economic events from SQLite."""
    try:
        from backend.core.database import DatabasePool
        from datetime import datetime, timedelta

        now = datetime.now()
        future = now + timedelta(hours=24)

        query = """
            SELECT event_id, event_name, event_date, event_time, currency, 
                   forecast_value, previous_value, impact_level
            FROM economic_events
            WHERE event_date BETWEEN ? AND ?
            ORDER BY event_date ASC, event_time ASC
        """

        rows = await DatabasePool.fetch_all(
            query,
            (now.strftime("%Y-%m-%d"), future.strftime("%Y-%m-%d"))
        )

        events = []
        for row in rows:
            impact = str(row[7] or "").upper()
            events.append({
                "id": row[0],
                "name": row[1],
                "date": row[2],
                "time": row[3],
                "currency": row[4],
                "forecast": row[5],
                "previous": row[6],
                "impact": impact,
                "is_high": "HIGH" in impact or "3" in str(row[7]),
            })

        return events
    except Exception as e:
        logger.error(f"Events fetch failed: {e}")
        return []


def _get_usd_index() -> Optional[Dict]:
    """Get USD Index data."""
    try:
        from backend.services.usd_index.index_engine import USDIndexEngine
        engine = USDIndexEngine()
        return engine.get_latest()
    except Exception as e:
        logger.error(f"USD Index fetch failed: {e}")
        return None


def _get_candle_dna(cache) -> List[Dict]:
    """Extract candle DNA data from scanner cache."""
    dna_results = []
    if not cache:
        return dna_results

    all_data = cache.get_all()
    for symbol, data in all_data.items():
        dna = data.get("behavior_dna", {})
        if not dna or "error" in dna:
            continue

        symbol_dna = {"symbol": symbol, "timeframes": {}}

        for tf in ["H1", "H4"]:
            tf_dna = dna.get(tf, {})
            if "error" in tf_dna:
                continue

            power = tf_dna.get("power_analysis", {})
            wick = tf_dna.get("wick_pressure", {})
            streak = tf_dna.get("streak_analysis", {})

            symbol_dna["timeframes"][tf] = {
                "dominance": power.get("dominance", "—"),
                "bull_pct": power.get("bull_pct", 0),
                "bear_pct": power.get("bear_pct", 0),
                "avg_bull_body": power.get("avg_bull_body", 0),
                "avg_bear_body": power.get("avg_bear_body", 0),
                "wick_pressure": wick.get("pressure", "—"),
                "wick_ratio": wick.get("ratio", 1.0),
                "upper_avg": wick.get("avg_upper", 0),
                "lower_avg": wick.get("avg_lower", 0),
                "current_streak": streak.get("current_streak", 0),
                "streak_type": streak.get("streak_type", "—"),
                "max_streak": streak.get("max_streak", 0),
            }

        if symbol_dna["timeframes"]:
            dna_results.append(symbol_dna)

    return dna_results


def _get_score_breakdown(cache) -> List[Dict]:
    """Get sub-score breakdown for each symbol from the heatmap scorer."""
    if not cache:
        return []

    try:
        from backend.services.market_scanner import HeatmapScorer
    except ImportError:
        return []

    all_data = cache.get_all()
    breakdowns = []

    for symbol, data in all_data.items():
        technical = data.get("technical", {})
        confluence = data.get("confluence", {})
        macro = data.get("macro_divergence")
        cot = data.get("cot")

        scores = {
            "symbol": symbol,
            "composite": data.get("heatmap_score", 0),
            "direction": data.get("direction", "NEUTRAL"),
            "trend": HeatmapScorer.score_trend(technical),
            "momentum": HeatmapScorer.score_momentum(technical),
            "mtf_alignment": HeatmapScorer.score_mtf_alignment(technical),
            "confluence": HeatmapScorer.score_confluence(confluence),
            "macro": HeatmapScorer.score_macro(macro),
            "cot": HeatmapScorer.score_cot(cot),
            "event_risk": HeatmapScorer.score_event_risk(False),
        }
        breakdowns.append(scores)

    breakdowns.sort(key=lambda x: x["composite"], reverse=True)
    return breakdowns


# ===========================================================================
# Main Dashboard Endpoint
# ===========================================================================
@router.get("/dashboard")
async def get_scanner_dashboard(user: dict = Depends(get_current_user)):
    """
    Returns the full analysis pipeline in a single API call.
    Used by the admin Scanner Dashboard page.
    """
    try:
        cache = _get_scanner_cache()
        
        # 1. Fetch data concurrently (non-blocking)
        # Use asyncio.to_thread for synchronous generic engines to avoid blocking event loop
        
        # Events (already async)
        task_events = _get_events()
        
        # COT (Sync -> Async Thread)
        task_cot = asyncio.to_thread(_get_cot_all)
        
        # Macro (Sync -> Async Thread)
        task_macro = asyncio.to_thread(_get_macro_divergence)
        
        # USD Index (Sync -> Async Thread)
        task_usd = asyncio.to_thread(_get_usd_index)
        
        # Candle DNA (CPU bound -> Async Thread)
        task_dna = asyncio.to_thread(_get_candle_dna, cache)
        
        # Score Breakdown (CPU bound -> Async Thread)
        task_score = asyncio.to_thread(_get_score_breakdown, cache)

        # Execute all with timeout safety (5 seconds max for auxiliary data)
        results = await asyncio.gather(
            task_events, task_cot, task_macro, task_usd, task_dna, task_score,
            return_exceptions=True
        )
        
        # Unpack results with error handling
        events = results[0] if not isinstance(results[0], Exception) else []
        cot_data = results[1] if not isinstance(results[1], Exception) else []
        macro_data = results[2] if not isinstance(results[2], Exception) else []
        usd_index = results[3] if not isinstance(results[3], Exception) else None
        candle_dna = results[4] if not isinstance(results[4], Exception) else []
        score_breakdown = results[5] if not isinstance(results[5], Exception) else []
        
        # Heatmap from scanner cache (fast, in-memory)
        heatmap = cache.get_heatmap() if cache else []
        last_scan = cache.last_scan_time.isoformat() if cache and cache.last_scan_time else None

        return {
            "heatmap": {
                "symbols": heatmap,
                "count": len(heatmap),
                "last_scan": last_scan,
            },
            "score_breakdown": score_breakdown,
            "cot": {
                "instruments": cot_data,
                "count": len(cot_data),
            },
            "macro_divergence": {
                "pairs": macro_data,
                "count": len(macro_data),
            },
            "candle_dna": {
                "symbols": candle_dna,
                "count": len(candle_dna),
            },
            "events": {
                "upcoming": events,
                "count": len(events),
                "high_impact": sum(1 for e in events if e.get("is_high")),
            },
            "usd_index": usd_index,
        }
    except Exception as e:
        logger.error(f"Scanner Dashboard Critical Error: {e}")
        # Return empty structure instead of 500 to keep page alive
        return {
            "heatmap": {"symbols": [], "count": 0, "last_scan": None},
            "score_breakdown": [],
            "cot": {"instruments": [], "count": 0},
            "macro_divergence": {"pairs": [], "count": 0},
            "candle_dna": {"symbols": [], "count": 0},
            "events": {"upcoming": [], "count": 0, "high_impact": 0},
            "usd_index": None,
            "error": str(e)
        }
