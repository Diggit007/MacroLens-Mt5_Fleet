"""
Event Analyzer Service (v2 - Optimized)
=======================================
Performs quantitative analysis on historical economic events.

OPTIMIZATIONS:
1. Caching - Stats are cached for 1 hour (configurable)
2. Weighted Recency - Last 6 months count 2x more
3. Async-ready - Prepared for async DB operations

Core Capabilities:
- Classify historical outcomes (Beat/Miss)
- Calculate deviation statistics (mean, std, z-score)
- Generate event playbooks for specific symbol reactions
"""

import sqlite3
import re
from pathlib import Path
from typing import Dict, List, Optional, Literal
from datetime import datetime, timedelta
from dataclasses import dataclass
import logging
import statistics
import hashlib
import time

logger = logging.getLogger("EventAnalyzer")

# Database path
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "market_data.db"

# Cache configuration
CACHE_TTL_SECONDS = 3600  # 1 hour
RECENCY_MONTHS = 6        # Last 6 months get 2x weight


@dataclass
class EventOutcome:
    """Classified outcome of an economic event."""
    event_name: str
    event_date: str
    forecast: float
    actual: float
    previous: float
    deviation: float           # actual - forecast
    deviation_pct: float       # (actual - forecast) / |forecast| * 100
    momentum: float            # forecast - previous
    category: str              # BIG_BEAT, SMALL_BEAT, IN_LINE, SMALL_MISS, BIG_MISS
    z_score: Optional[float]   # Normalized deviation


class StatsCache:
    """
    Simple in-memory cache for deviation stats.
    Falls back to Redis if available via the global cache.
    """
    def __init__(self, ttl: int = CACHE_TTL_SECONDS):
        self._store: Dict[str, Dict] = {}
        self._ttl = ttl
        
    def _make_key(self, event_name: str, currency: str, weighted: bool) -> str:
        raw = f"{event_name}:{currency or 'ALL'}:w{weighted}"
        return hashlib.md5(raw.encode()).hexdigest()[:16]
    
    def get(self, event_name: str, currency: str = None, weighted: bool = False) -> Optional[Dict]:
        key = self._make_key(event_name, currency, weighted)
        if key in self._store:
            item = self._store[key]
            if time.time() < item["expires"]:
                logger.debug(f"Cache HIT for {event_name}")
                return item["data"]
            else:
                del self._store[key]
        return None
    
    def set(self, event_name: str, currency: str, weighted: bool, data: Dict):
        key = self._make_key(event_name, currency, weighted)
        self._store[key] = {
            "data": data,
            "expires": time.time() + self._ttl
        }
        logger.debug(f"Cache SET for {event_name}")
        
    def clear(self):
        """Clear all cached stats."""
        self._store = {}
        logger.info("Stats cache cleared")


# Global cache instance
_stats_cache = StatsCache()


class EventAnalyzer:
    """
    Analyzes historical economic events to generate quantitative signals.
    
    Optimizations:
    - Caches historical stats for 1 hour
    - Applies recency weighting (last 6 months = 2x weight)
    """
    
    # Classification thresholds (percentage deviation)
    BEAT_BIG_THRESHOLD = 0.15      # >15% above forecast = Big Beat
    BEAT_SMALL_THRESHOLD = 0.05   # 5-15% above = Small Beat
    MISS_SMALL_THRESHOLD = -0.05  # 5-15% below = Small Miss
    MISS_BIG_THRESHOLD = -0.15    # >15% below = Big Miss
    
    def __init__(self, db_path: Path = DB_PATH, use_cache: bool = True):
        self.db_path = db_path
        self.use_cache = use_cache
        self.cache = _stats_cache
        
    def _get_connection(self):
        return sqlite3.connect(self.db_path)
    
    def get_historical_events(self, event_name: str, currency: str = None, 
                               limit: int = 50, date_before: str = None) -> List[Dict]:
        """
        Fetch the last N occurrences of a specific event.
        
        Args:
            event_name: Partial or full event name (uses LIKE matching)
            currency: Filter by currency (e.g., "USD")
            limit: Maximum number of results
            
        Returns:
            List of event dictionaries with actual, forecast, previous values
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        query = """
            SELECT e.event_name, e.event_date, e.event_time, e.currency,
                   e.actual_value, e.forecast_value, e.previous_value, e.impact_level,
                   r.h1_change_pips
            FROM economic_events e
            LEFT JOIN event_reactions r 
              ON e.event_name = r.event_name 
              AND e.event_date = r.event_date
            WHERE e.event_name = ?
            AND e.actual_value IS NOT NULL
            AND e.forecast_value IS NOT NULL
        """
        params = [event_name]
        
        if currency:
            query += " AND e.currency = ?"
            params.append(currency)
            
        if date_before:
            query += " AND e.event_date < ?"
            params.append(date_before)
            
        query += " ORDER BY e.event_date DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        return [
            {
                "event_name": r[0],
                "event_date": r[1],
                "event_time": r[2],
                "currency": r[3],
                "actual": r[4],
                "forecast": r[5],
                "previous": r[6],
                "impact": r[7],
                "pips": r[8] if r[8] is not None else 0.0 # Handle missing price data
            }
            for r in rows
        ]
    
    def classify_outcome(self, forecast: float, actual: float, 
                         previous: float = None) -> Dict:
        """
        Classify an event outcome into categories.
        
        Categories:
        - BIG_BEAT: Actual significantly exceeds forecast (>15%)
        - SMALL_BEAT: Actual moderately exceeds forecast (5-15%)
        - IN_LINE: Actual within 5% of forecast
        - SMALL_MISS: Actual moderately below forecast (5-15%)
        - BIG_MISS: Actual significantly below forecast (>15%)
        
        Returns:
            Dict with deviation, deviation_pct, momentum, category
        """
        if forecast == 0:
            # Handle edge case
            deviation_pct = 0 if actual == 0 else (1 if actual > 0 else -1)
        else:
            deviation_pct = (actual - forecast) / abs(forecast)
        
        deviation = actual - forecast
        momentum = (forecast - previous) if previous is not None else 0
        
        # Classify
        if deviation_pct > self.BEAT_BIG_THRESHOLD:
            category = "BIG_BEAT"
        elif deviation_pct > self.BEAT_SMALL_THRESHOLD:
            category = "SMALL_BEAT"
        elif deviation_pct < self.MISS_BIG_THRESHOLD:
            category = "BIG_MISS"
        elif deviation_pct < self.MISS_SMALL_THRESHOLD:
            category = "SMALL_MISS"
        else:
            category = "IN_LINE"
            
        return {
            "deviation": deviation,
            "deviation_pct": deviation_pct * 100,  # Convert to percentage
            "momentum": momentum,
            "category": category
        }
    
    def _calculate_recency_weight(self, event_date: str) -> float:
        """
        Calculate recency weight for an event.
        
        Events in the last 6 months get 2x weight.
        Events older than 6 months get 1x weight.
        
        Args:
            event_date: Date string in YYYY-MM-DD format
            
        Returns:
            Weight multiplier (1.0 or 2.0)
        """
        try:
            dt = datetime.strptime(event_date, "%Y-%m-%d")
            cutoff = datetime.now() - timedelta(days=RECENCY_MONTHS * 30)
            return 2.0 if dt >= cutoff else 1.0
        except:
            return 1.0
    
    def calculate_deviation_stats(self, event_name: str, currency: str = None,
                                   lookback: int = 50, use_weighted: bool = True,
                                   date_before: str = None) -> Dict:
        """
        Calculate historical deviation statistics for an event.
        
        OPTIMIZATIONS:
        1. Results are cached for 1 hour
        2. If use_weighted=True, applies recency weighting (last 6 months = 2x)
        
        Returns:
            Dict with mean, std, positive_rate, category_distribution
        """
        # Check cache first
        # Note: We disable cache if date_before is set (backtesting mode)
        if self.use_cache and not date_before:
            cached = self.cache.get(event_name, currency, use_weighted)
            if cached:
                return cached
        
        events = self.get_historical_events(event_name, currency, lookback, date_before)
        
        if len(events) < 3:
            return {
                "sample_size": len(events),
                "effective_sample": len(events),
                "mean_deviation": 0,
                "std_deviation": 0,
                "positive_rate": 0.5,
                "avg_pips": 0.0, # <-- Fix KeyError
                "categories": {},
                "sufficient_data": False,
                "weighted": use_weighted,
                "cached": False
            }
        
        deviations = []
        weights = []
        pips_list = [] # <-- New List
        categories = {"BIG_BEAT": 0, "SMALL_BEAT": 0, "IN_LINE": 0, 
                      "SMALL_MISS": 0, "BIG_MISS": 0}
        positive_weighted = 0
        total_weight = 0
        
        for e in events:
            result = self.classify_outcome(e["forecast"], e["actual"], e["previous"])
            weight = self._calculate_recency_weight(e["event_date"]) if use_weighted else 1.0
            
            deviations.append(result["deviation"])
            weights.append(weight)
            pips_list.append(e["pips"]) # <-- Store pips
            categories[result["category"]] += 1
            total_weight += weight
            
            if result["deviation"] > 0:
                positive_weighted += weight
        
        # Weighted statistics
        if use_weighted and total_weight > 0:
            # Weighted mean
            weighted_mean = sum(d * w for d, w in zip(deviations, weights)) / total_weight
            
            # Weighted standard deviation
            weighted_variance = sum(w * (d - weighted_mean)**2 for d, w in zip(deviations, weights)) / total_weight
            weighted_std = weighted_variance ** 0.5
            
            # Weighted positive rate
            weighted_positive_rate = positive_weighted / total_weight
            
            mean_dev = weighted_mean
            std_dev = weighted_std
            positive_rate = weighted_positive_rate
            effective_sample = total_weight
            
            # Weighted Pips
            avg_pips = sum(p * w for p, w in zip(pips_list, weights)) / total_weight
        else:
            mean_dev = statistics.mean(deviations)
            std_dev = statistics.stdev(deviations) if len(deviations) > 1 else 0
            positive_rate = sum(1 for d in deviations if d > 0) / len(deviations)
            effective_sample = len(events)
            avg_pips = statistics.mean(pips_list)
        
        result = {
            "sample_size": len(events),
            "effective_sample": round(effective_sample, 1),
            "mean_deviation": round(mean_dev, 4),
            "std_deviation": round(std_dev, 4),
            "positive_rate": round(positive_rate, 2),
            "avg_pips": round(avg_pips, 1), # <-- New Metric
            "categories": categories,
            "sufficient_data": len(events) >= 10,
            "weighted": use_weighted,
            "cached": False
        }
        
        # Cache the result
        if self.use_cache:
            self.cache.set(event_name, currency, use_weighted, result)
            result["cached"] = True
        
        return result
    
    def calculate_z_score(self, current_deviation: float, 
                           historical_std: float) -> float:
        """
        Calculate z-score for a deviation.
        
        Z-Score > 1.5: Statistically significant (likely to cause trend)
        Z-Score < 1.0: Statistical noise (likely whipsaw)
        """
        if historical_std == 0:
            return 0
        return round(current_deviation / historical_std, 2)
    
    def analyze_upcoming_event(self, event_name: str, forecast: float,
                                previous: float, currency: str = None,
                                simulation_date: str = None) -> Dict:
        """
        Generate a quantitative analysis for an upcoming event.
        
        Args:
            simulation_date: If provided, pretends today is this date (for backtesting)
        """
        # Get historical stats (with weighted recency)
        stats = self.calculate_deviation_stats(event_name, currency, use_weighted=True, date_before=simulation_date)
        
        # Calculate momentum (forecast vs previous)
        momentum = forecast - previous if previous else 0
        momentum_direction = "IMPROVING" if momentum > 0 else "DETERIORATING" if momentum < 0 else "FLAT"
        
        # Historical bias
        beat_rate = stats["positive_rate"]
        historical_tendency = "BEAT" if beat_rate > 0.55 else "MISS" if beat_rate < 0.45 else "NEUTRAL"
        
        # Expected magnitude (using historical std as proxy)
        expected_deviation = stats["mean_deviation"]
        
        return {
            "event_name": event_name,
            "currency": currency or "Unknown",
            "forecast": forecast,
            "previous": previous,
            
            # Momentum Analysis
            "momentum": round(momentum, 4),
            "momentum_direction": momentum_direction,
            "momentum_significance": abs(momentum) > stats["std_deviation"] if stats["std_deviation"] > 0 else False,
            
            # Historical Analysis (with recency weighting)
            "historical_beat_rate": beat_rate,
            "historical_tendency": historical_tendency,
            "historical_std": stats["std_deviation"],
            "sample_size": stats["sample_size"],
            "effective_sample": stats["effective_sample"],
            "category_distribution": stats["categories"],
            "avg_pips": stats["avg_pips"], # <-- New
            
            # Prediction Context
            "sufficient_data": stats["sufficient_data"],
            "expected_deviation": expected_deviation,
            "recency_weighted": stats["weighted"],
            
            # Composite Score
            "bias_score": self._calculate_bias_score(momentum_direction, historical_tendency, beat_rate)
        }
    
    def _calculate_bias_score(self, momentum_dir: str, tendency: str, 
                               beat_rate: float) -> int:
        """
        Calculate a simple bias score (-3 to +3).
        
        Positive = Bullish for the currency
        Negative = Bearish for the currency
        """
        score = 0
        
        # Factor 1: Momentum direction
        if momentum_dir == "IMPROVING":
            score += 1
        elif momentum_dir == "DETERIORATING":
            score -= 1
            
        # Factor 2: Historical tendency
        if tendency == "BEAT":
            score += 1
        elif tendency == "MISS":
            score -= 1
            
        # Factor 3: Strong beat rate
        if beat_rate > 0.7:
            score += 1
        elif beat_rate < 0.3:
            score -= 1
            
        return score
    
    def analyze_released_event(self, event_name: str, actual: float,
                                forecast: float, previous: float,
                                currency: str = None) -> Dict:
        """
        Analyze an event after its release (for execution decisions).
        
        Returns:
            Dict with deviation, z_score, signal_strength
        """
        # Get historical stats for z-score calculation
        stats = self.calculate_deviation_stats(event_name, currency, use_weighted=True)
        
        # Classify the outcome
        outcome = self.classify_outcome(forecast, actual, previous)
        
        # Calculate z-score
        z_score = self.calculate_z_score(outcome["deviation"], stats["std_deviation"])
        
        # Determine signal strength
        if abs(z_score) >= 2.0:
            signal_strength = "VERY_STRONG"
        elif abs(z_score) >= 1.5:
            signal_strength = "STRONG"
        elif abs(z_score) >= 1.0:
            signal_strength = "MODERATE"
        else:
            signal_strength = "WEAK"
        
        return {
            "event_name": event_name,
            "actual": actual,
            "forecast": forecast,
            "previous": previous,
            
            # Deviation Analysis
            "deviation": outcome["deviation"],
            "deviation_pct": outcome["deviation_pct"],
            "category": outcome["category"],
            
            # Statistical Significance
            "z_score": z_score,
            "signal_strength": signal_strength,
            "is_significant": abs(z_score) >= 1.5,
            
            # Direction
            "direction": "BULLISH" if outcome["deviation"] > 0 else "BEARISH",
            
            # Action Recommendation
            "recommendation": self._get_recommendation(outcome["category"], z_score)
        }
    
    def _get_recommendation(self, category: str, z_score: float) -> str:
        """Generate trading recommendation based on outcome."""
        if abs(z_score) < 1.0:
            return "WAIT - Noise, expect whipsaw"
        
        if category in ["BIG_BEAT", "SMALL_BEAT"]:
            if z_score >= 1.5:
                return "ENTER - Strong positive surprise, likely trend"
            return "MONITOR - Positive but not significant"
            
        if category in ["BIG_MISS", "SMALL_MISS"]:
            if z_score <= -1.5:
                return "ENTER - Strong negative surprise, likely trend"
            return "MONITOR - Negative but not significant"
            
        return "HOLD - In-line with expectations"
    
    def clear_cache(self):
        """Clear the stats cache (call after new data is imported)."""
        self.cache.clear()
    
    def format_for_prompt(self, analysis: Dict, is_released: bool = False) -> str:
        """
        Format analysis for LLM prompt injection.
        
        Args:
            analysis: Output from analyze_upcoming_event or analyze_released_event
            is_released: True if event has occurred
        """
        if not is_released:
            # Pre-release format
            weighted_note = " (Recency Weighted)" if analysis.get('recency_weighted') else ""
            return f"""📊 **EVENT ANALYSIS: {analysis['event_name']}**

**MOMENTUM ANALYSIS:**
- Forecast: {analysis['forecast']} | Previous: {analysis['previous']}
- Delta: {analysis['momentum']:+.4f} ({analysis['momentum_direction']})
- Significance: {'YES' if analysis['momentum_significance'] else 'No'}

**HISTORICAL PATTERN{weighted_note} (n={analysis['sample_size']}, eff={analysis.get('effective_sample', analysis['sample_size'])}):**
- Beat Rate: {analysis['historical_beat_rate']:.0%}
- Tendency: {analysis['historical_tendency']}
- Std Dev: {analysis['historical_std']:.4f}

**PREDICTION:**
- Bias Score: {analysis['bias_score']:+d} (Range: -3 to +3)
- Expected Direction: {'POSITIVE' if analysis['bias_score'] > 0 else 'NEGATIVE' if analysis['bias_score'] < 0 else 'NEUTRAL'}

**DATA QUALITY:** {'✅ Sufficient' if analysis['sufficient_data'] else '⚠️ Limited sample'}"""
        
        else:
            # Post-release format
            return f"""🚨 **EVENT RELEASED: {analysis['event_name']}**

**RESULT:**
- Actual: {analysis['actual']} | Forecast: {analysis['forecast']}
- Deviation: {analysis['deviation']:+.4f} ({analysis['deviation_pct']:+.1f}%)
- Category: {analysis['category']}

**STATISTICAL SIGNIFICANCE:**
- Z-Score: {analysis['z_score']:+.2f}
- Signal Strength: {analysis['signal_strength']}
- Significant: {'✅ YES' if analysis['is_significant'] else '❌ NO'}

**DIRECTION:** {analysis['direction']}
**RECOMMENDATION:** {analysis['recommendation']}"""


# =============================================================================
# STANDALONE TEST
# =============================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    
    analyzer = EventAnalyzer()
    
    # Test 1: Historical lookup
    print("=== Test 1: Historical CPI Events ===")
    events = analyzer.get_historical_events("CPI", "USD", limit=5)
    for e in events:
        print(f"  {e['event_date']}: {e['event_name']} - Actual: {e['actual']}, Forecast: {e['forecast']}")
    
    # Test 2: Deviation stats (with caching)
    print("\n=== Test 2: CPI Deviation Stats (Weighted) ===")
    import time
    start = time.time()
    stats = analyzer.calculate_deviation_stats("CPI", "USD", use_weighted=True)
    t1 = time.time() - start
    print(f"  Sample Size: {stats['sample_size']} (Effective: {stats['effective_sample']})")
    print(f"  Beat Rate: {stats['positive_rate']:.0%}")
    print(f"  Std Dev: {stats['std_deviation']:.4f}")
    print(f"  Weighted: {stats['weighted']}")
    print(f"  Time: {t1*1000:.1f}ms")
    
    # Test 3: Cache hit
    print("\n=== Test 3: Cache Hit Test ===")
    start = time.time()
    stats2 = analyzer.calculate_deviation_stats("CPI", "USD", use_weighted=True)
    t2 = time.time() - start
    print(f"  Time: {t2*1000:.1f}ms (vs {t1*1000:.1f}ms first call)")
    print(f"  Speedup: {t1/max(t2, 0.0001):.1f}x" if t2 > 0 else "  Speedup: Instant (cache)")
    
    # Test 4: Upcoming event analysis
    print("\n=== Test 4: Analyze Upcoming CPI ===")
    analysis = analyzer.analyze_upcoming_event("CPI", forecast=3.2, previous=3.1, currency="USD")
    print(analyzer.format_for_prompt(analysis, is_released=False))
