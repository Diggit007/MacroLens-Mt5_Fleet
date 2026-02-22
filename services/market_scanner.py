"""
Market Scanner — The Analytical Brain
======================================
Pre-computes technical analysis, macro scores, COT, and events for ALL symbols
every 5 minutes using MT5 direct data. Zero LLM cost.

Results are cached in ScannerCache for:
  1. Frontend Heatmap display
  2. Agent Service cache hits (skip candle fetching)
  3. Autonomous Worker smart symbol selection
"""

import asyncio
import logging
import time
import traceback
from typing import Dict, List, Optional
from datetime import datetime, timezone, timedelta
from threading import Lock
import copy
from dataclasses import dataclass, field
from typing import ClassVar

logger = logging.getLogger("MarketScanner")

# ---------------------------------------------------------------------------
# Imports — analysis engines (all existing, no new deps)
# ---------------------------------------------------------------------------
from backend.services.technical_analysis import TechnicalAnalyzer, SymbolBehaviorAnalyzer
from backend.services.mt5_data_fetcher import mt5_fetcher

# Lazy imports to avoid circular dependencies
def _get_macro_divergence():
    try:
        from backend.services.macro_divergence import MacroDivergence
        return MacroDivergence()
    except Exception as e:
        logger.warning(f"MacroDivergence unavailable: {e}")
        return None

def _get_cot_engine():
    try:
        from backend.services.cot.api import engine as cot_engine
        return cot_engine
    except Exception as e:
        logger.warning(f"COT Engine unavailable: {e}")
        return None

def _get_usd_engine():
    try:
        from backend.services.usd_index.index_engine import USDIndexEngine
        return USDIndexEngine()
    except Exception as e:
        logger.warning(f"USD Index Engine unavailable: {e}")
        return None

def _get_retail_sentiment(symbol: str):
    try:
        from backend.services.agent_service import MacroLensAgentV2
        agent = MacroLensAgentV2.__new__(MacroLensAgentV2)
        return agent.get_cached_sentiment(symbol)
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WATCHLIST = [
    # Forex Majors
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
    
    # JPY Crosses
    "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY", "NZDJPY",
    
    # EUR Crosses
    "EURGBP", "EURAUD", "EURCAD", "EURCHF", "EURNZD",
    
    # GBP Crosses
    "GBPAUD", "GBPCAD", "GBPCHF", "GBPNZD",
    
    # Other Crosses
    "AUDCAD", "AUDCHF", "AUDNZD", "CADCHF", "NZDCAD", "NZDCHF",
    
    # Commodities
    "XAUUSD", "XAGUSD", "USOil", "UKOil",
    
    # Crypto
    "BTCUSD", "ETHUSD",
    
    # Indices
    "US30", "US100",
]

# Timeframes to scan (lightweight — no M5 in scanner, that's for on-demand)
SCAN_TIMEFRAMES = ["D1", "H4", "H1"]

# Scan interval (seconds)
SCAN_INTERVAL = 300  # 5 minutes


# ===========================================================================
# Configuration & Constants
# ===========================================================================
@dataclass
class ScoringConfig:
    RSI_CENTER: float = 50.0
    RSI_SCALE: float = 2.0
    MACD_SCALE: float = 5000.0
    MACD_MAX: float = 100.0
    TREND_STRONG: float = 33.0
    TREND_WEAK: float = 25.0
    TREND_RANGE: float = 5.0
    
    @classmethod
    def validate(cls):
        pass

# ===========================================================================
# Scanner Cache — Thread-safe in-memory store
# ===========================================================================
class ScannerCache:
    """
    Stores pre-computed analysis data per symbol.
    Thread-safe for concurrent reads from API/worker.
    """

    def __init__(self):
        self._data: Dict[str, Dict] = {}
        self._lock = Lock()
        self._last_scan_time: Optional[datetime] = None

    def put(self, symbol: str, data: Dict, ttl_seconds: int = 1800):
        """Store scan result for a symbol with TTL."""
        with self._lock:
            # Add metadata for eviction
            expiry = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
            
            # Deep copy to prevent mutation of the input dict
            stored_data = copy.deepcopy(data)
            stored_data["_cached_at"] = datetime.now(timezone.utc).isoformat()
            stored_data["_expires_at"] = expiry.isoformat()
            
            self._data[symbol] = stored_data
            
            # Lazy eviction (probabilistic or on-write)
            if len(self._data) > 100: # Simple cap check
                 self._evict_expired()

    def get(self, symbol: str) -> Optional[Dict]:
        """
        Get cached data for a symbol.
        Returns deep copy to ensure thread safety.
        """
        with self._lock:
            entry = self._data.get(symbol)
            if entry is None:
                return None
            
            # calculate age inside lock
            cached_at = datetime.fromisoformat(entry["_cached_at"])
            age = (datetime.now(timezone.utc) - cached_at).total_seconds()
            
            # Deep copy for safety
            result = copy.deepcopy(entry)
            result["age_seconds"] = age
            return result

    def get_all(self) -> Dict[str, Dict]:
        """Get all cached symbol data (deep copy)."""
        with self._lock:
            return copy.deepcopy(self._data)

    def get_heatmap(self) -> List[Dict]:
        """
        Returns sorted list of {symbol, score, direction, ...} for frontend heatmap.
        Sorted by score descending.
        """
        with self._lock:
            heatmap = []
            # Iterate over a copy of items to avoid modification issues
            for symbol, data in self._data.items():
                heatmap.append({
                    "symbol": symbol,
                    "score": data.get("heatmap_score", 0),
                    "direction": data.get("direction", "NEUTRAL"),
                    "trend_d1": data.get("technical", {}).get("D1", {}).get("structure", "—"),
                    "trend_h4": data.get("technical", {}).get("H4", {}).get("structure", "—"),
                    "trend_h1": data.get("technical", {}).get("H1", {}).get("structure", "—"),
                    "rsi_h1": data.get("technical", {}).get("H1", {}).get("rsi", 50),
                    "price": data.get("price", 0),
                    "atr": data.get("atr", 0),
                    "confluence": data.get("confluence", {}),
                    "cot_bias": data.get("cot_bias", "—"),
                    "last_updated": data.get("_cached_at", ""),
                })
            heatmap.sort(key=lambda x: x["score"], reverse=True)
            return heatmap

    def _evict_expired(self):
        """Remove expired entries."""
        now = datetime.now(timezone.utc)
        keys_to_remove = [
            k for k, v in self._data.items() 
            if "_expires_at" in v and datetime.fromisoformat(v["_expires_at"]) < now
        ]
        for k in keys_to_remove:
            del self._data[k]

    @property
    def last_scan_time(self):
        with self._lock:
            return self._last_scan_time

    @last_scan_time.setter
    def last_scan_time(self, value):
        with self._lock:
            self._last_scan_time = value


# ===========================================================================
# Heatmap Scorer — Composites all sub-scores into 0-100
# ===========================================================================
class HeatmapScorer:
    """
    Calculates a composite opportunity score (0-100) for each symbol
    using only deterministic, pre-computed data. No LLM involved.
    """

    # Weights for each factor
    WEIGHTS = {
        "trend":         0.20,  # Market structure strength
        "momentum":      0.15,  # RSI + MACD
        "mtf_alignment": 0.20,  # Multi-timeframe agreement
        "confluence":    0.15,  # Existing confluence score (0-5 → 0-100)
        "macro":         0.15,  # Macro divergence
        "cot":           0.10,  # COT Willco extremes
        "event_risk":    0.05,  # Calendar risk (penalty)
    }

    @staticmethod
    def score_trend(technical: Dict) -> float:
        """Score trend strength from market structure (0-100)."""
        structures = []
        for tf in ["D1", "H4", "H1"]:
            s = technical.get(tf, {}).get("structure", "Ranges")
            structures.append(s)

        score = 0
        for s in structures:
            if "STRONG" in s.upper() and ("BULLISH" in s.upper() or "BEARISH" in s.upper()):
                score += ScoringConfig.TREND_STRONG
            elif "BULLISH" in s.upper() or "BEARISH" in s.upper():
                score += ScoringConfig.TREND_WEAK
            elif "RANGES" in s.upper() or "RANGE" in s.upper():
                score += ScoringConfig.TREND_RANGE
        return min(score, 100)

    @staticmethod
    def score_momentum(technical: Dict) -> float:
        """Score momentum from RSI deviation + MACD (0-100)."""
        h1 = technical.get("H1", {})
        rsi = h1.get("rsi", 50)
        macd = h1.get("macd", {})
        
        # RSI component: distance from 50 = stronger momentum
        rsi_score = abs(rsi - ScoringConfig.RSI_CENTER) * ScoringConfig.RSI_SCALE  # 0-100
        
        # MACD component: histogram strength
        macd_score = 0
        if histogram:
            # Use Config
            macd_score = min(abs(histogram) * ScoringConfig.MACD_SCALE, ScoringConfig.MACD_MAX)
        
        return min((rsi_score * 0.6 + macd_score * 0.4), 100)

    @staticmethod
    def score_mtf_alignment(technical: Dict) -> float:
        """Score multi-timeframe alignment (0-100)."""
        directions = []
        for tf in ["D1", "H4", "H1"]:
            s = technical.get(tf, {}).get("structure", "")
            if "BULLISH" in s.upper():
                directions.append("BULLISH")
            elif "BEARISH" in s.upper():
                directions.append("BEARISH")
            else:
                directions.append("NEUTRAL")
        
        # All agree = 100, two agree = 65, mixed = 30
        if len(set(directions)) == 1 and directions[0] != "NEUTRAL":
            return 100
        elif directions.count(directions[0]) >= 2 and directions[0] != "NEUTRAL":
            return 65
        elif "NEUTRAL" not in directions and len(set(directions)) == 2:
            return 20  # Conflicting = low
        else:
            return 30

    @staticmethod
    def score_confluence(confluence_result: Dict) -> float:
        """Convert confluence score (0-5) to 0-100."""
        raw = confluence_result.get("score", 0)
        return min(raw * 20, 100)

    @staticmethod
    def score_macro(macro_data: Optional[Dict]) -> float:
        """Score macro divergence (0-100)."""
        if not macro_data:
            return 50  # neutral if unavailable
        dscore = macro_data.get("divergence_score", 0)
        return min(dscore * 20, 100)

    @staticmethod
    def score_cot(cot_data: Optional[Dict]) -> float:
        """Score COT positioning (0-100). Extremes score higher."""
        if not cot_data:
            return 50
        willco = cot_data.get("willco_index", 50)
        # Distance from middle (50) = higher score
        return min(abs(willco - 50) * 2, 100)

    @staticmethod
    def score_event_risk(has_imminent_event: bool) -> float:
        """Event risk is a penalty. No event = 100 (full score), event = 30 (penalized)."""
        return 30 if has_imminent_event else 100

    @classmethod
    def calculate(cls, technical: Dict, confluence: Dict,
                  macro_data: Optional[Dict] = None, cot_data: Optional[Dict] = None,
                  has_imminent_event: bool = False) -> float:
        """Calculate composite heatmap score (0-100)."""
        scores = {
            "trend": cls.score_trend(technical),
            "momentum": cls.score_momentum(technical),
            "mtf_alignment": cls.score_mtf_alignment(technical),
            "confluence": cls.score_confluence(confluence),
            "macro": cls.score_macro(macro_data),
            "cot": cls.score_cot(cot_data),
            "event_risk": cls.score_event_risk(has_imminent_event),
        }
        
        composite = sum(scores[k] * cls.WEIGHTS[k] for k in cls.WEIGHTS)
        return round(composite, 1)

    @staticmethod
    def determine_direction(technical: Dict, confluence: Dict) -> str:
        """Determine overall direction from technical data."""
        bias = confluence.get("bias", "NEUTRAL")
        if bias in ("BUY", "SELL"):
            return bias
        
        # Fallback to H4/D1 structure
        d1 = technical.get("D1", {}).get("structure", "")
        if "BULLISH" in d1.upper():
            return "BUY"
        elif "BEARISH" in d1.upper():
            return "SELL"
        return "NEUTRAL"


# ===========================================================================
# Market Scanner — Background Loop
# ===========================================================================
@dataclass
class MT5CircuitBreaker:
    failure_threshold: int = 3
    recovery_timeout: int = 300  # 5 minutes
    failures: int = field(default=0)
    last_failure: Optional[datetime] = field(default=None)
    state: ClassVar[str] = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
    
    def record_failure(self):
        self.failures += 1
        self.last_failure = datetime.now(timezone.utc)
        if self.failures >= self.failure_threshold:
            self.state = "OPEN"
            logger.error(f"MT5 Circuit Breaker OPENED")
    
    def can_attempt(self) -> bool:
        if self.state == "CLOSED":
            return True
        if self.state == "OPEN":
            if self.last_failure and (datetime.now(timezone.utc) - self.last_failure).total_seconds() > self.recovery_timeout:
                self.state = "HALF_OPEN"
                return True
            return False
        return True  # HALF_OPEN - allow one test

    def reset(self):
        self.failures = 0
        self.state = "CLOSED"

class MarketScanner:
    """
    The Analytical Brain.
    Runs every 5 minutes, pre-computes analysis for ALL symbols using MT5 direct.
    """

    def __init__(self):
        self.cache = ScannerCache()
        self._behavior_analyzer = None # Lazy init
        self._running = False
        self._macro_divergence_cache: Optional[List[Dict]] = None
        self._macro_cache_time: Optional[datetime] = None
        self.circuit_breaker = MT5CircuitBreaker()

    @property
    def behavior_analyzer(self):
        if self._behavior_analyzer is None:
             self._behavior_analyzer = SymbolBehaviorAnalyzer()
        return self._behavior_analyzer

    async def run_scan_loop(self):
        """Main entry point — background task."""
        logger.info("🔬 Market Scanner starting...")
        await asyncio.sleep(15)  # Wait for boot

        # Initialize MT5
        if not mt5_fetcher.initialize():
            logger.error("MT5 failed to initialize. Scanner will retry each cycle.")

        self._running = True
        
        while self._running:
            try:
                await self._scan_cycle()
            except Exception as e:
                logger.error(f"Scanner cycle failed: {e}")
                traceback.print_exc()
            
            await asyncio.sleep(SCAN_INTERVAL)

    async def _scan_cycle(self):
        """One full scan cycle across all symbols."""
        cycle_start = time.time()
        logger.info(f"🔬 Scanner: Starting cycle for {len(WATCHLIST)} symbols...")

        # Circuit Breaker Check
        if not self.circuit_breaker.can_attempt():
             logger.warning(f"Scanner cycle skipped (MT5 Circuit Breaker OPEN). Retrying in 5 mins.")
             return

        # Ensure MT5 is connected
        if not mt5_fetcher.health_check():
            if not mt5_fetcher.initialize():
                logger.error("MT5 not available. Recording failure.")
                self.circuit_breaker.record_failure()
                return
            else:
                self.circuit_breaker.reset()


        # Pre-fetch macro data (shared across all symbols, expensive to compute per-symbol)
        macro_div_map = await self._fetch_macro_divergence()
        cot_engine = _get_cot_engine()
        usd_data = self._fetch_usd_index()

        # Scan each symbol
        scanned = 0
        failed = 0

        for symbol in WATCHLIST:
            try:
                result = await self._scan_symbol(symbol, macro_div_map, cot_engine, usd_data)
                if result:
                    self.cache.put(symbol, result)
                    scanned += 1
                else:
                    failed += 1
            except Exception as e:
                logger.warning(f"Scanner: {symbol} failed: {e}")
                failed += 1

        elapsed = time.time() - cycle_start
        self.cache.last_scan_time = datetime.now(timezone.utc)

        # Log summary
        heatmap = self.cache.get_heatmap()
        top3 = [(h["symbol"], h["score"], h["direction"]) for h in heatmap[:3]]
        logger.info(
            f"🔬 Scanner: Cycle complete in {elapsed:.1f}s | "
            f"{scanned} scanned, {failed} failed | "
            f"Top 3: {top3}"
        )

        # Push top movers to world_state live feed
        try:
            from backend.core.system_state import world_state
            if heatmap:
                top = heatmap[0]
                world_state.add_log(
                    "Scanner",
                    f"Top Opportunity: {top['symbol']} (Score: {top['score']}, "
                    f"Direction: {top['direction']}, H1 RSI: {top['rsi_h1']:.0f})",
                    "SCAN"
                )
        except Exception:
            pass

    async def _scan_symbol(self, symbol: str, macro_div_map: Dict,
                           cot_engine, usd_data: Optional[Dict]) -> Optional[Dict]:
        """
        Async wrapper to run the heavy synchronous scan in a separate thread.
        This prevents blocking the main event loop.
        """
        try:
            return await asyncio.to_thread(
                self._scan_symbol_sync, symbol, macro_div_map, cot_engine, usd_data
            )
        except Exception as e:
            # Use exception logging to capture stack trace for debugging
            logger.exception(f"Scan thread failed for {symbol}")
            return None

    def _scan_symbol_sync(self, symbol: str, macro_div_map: Dict,
                           cot_engine, usd_data: Optional[Dict]) -> Optional[Dict]:
        """
        Synchronous core logic for scanning a single symbol.
        Fetched via asyncio.to_thread to keep main loop free.
        """
        
        # 1. Fetch candles for each timeframe from MT5 (synchronous, 50-100ms)
        technical = {}
        raw_candles = {}

        for tf in SCAN_TIMEFRAMES:
            candles = mt5_fetcher.fetch_candles(symbol, tf, count=200)
            if not candles or len(candles) < 20:
                technical[tf] = {"structure": "Insufficient Data", "price": 0}
                continue

            raw_candles[tf] = candles

            # Run TechnicalAnalyzer (CPU bound)
            tech = TechnicalAnalyzer(candles)
            technical[tf] = {
                "price": candles[-1].get("close", 0),
                "atr": tech.get_atr(14),
                "adr": tech.get_adr(5),
                "pivots": tech.get_pivot_points(),
                "structure": tech.get_market_structure(),
                "rsi": tech.get_rsi(14),
                "macd": tech.get_macd(),
                "bollinger": tech.get_bollinger_bands(),
                "patterns": tech.get_candle_patterns(),
                "zones": tech.get_support_resistance(),
            }

        # Get current price
        current_price = (
            technical.get("H1", {}).get("price", 0)
            or technical.get("H4", {}).get("price", 0)
        )
        h1_atr = technical.get("H1", {}).get("atr", 0)

        if current_price <= 0:
            return None

        # 2. Behavior DNA (H1 and H4)
        behavior_dna = {}
        for tf in ["H1", "H4"]:
            candles = raw_candles.get(tf)
            if candles and len(candles) >= 24:
                try:
                    behavior_dna[tf] = self.behavior_analyzer.analyze(candles, symbol, tf)
                except Exception:
                    behavior_dna[tf] = {"error": "Analysis failed"}

        # 3. Confluence Score (reuse existing logic)
        confluence = self._calculate_confluence(technical)

        # 4. Price Action Score
        price_action = self._calculate_price_action(technical, behavior_dna)

        # 5. COT Sentiment
        cot_data = None
        cot_bias = "—"
        if cot_engine:
            try:
                # get_latest_sentiment is sync but fast if cached (which it is now)
                cot_data = cot_engine.get_latest_sentiment(symbol)
                if cot_data:
                    if cot_data.get("smart_sentiment", 0) > 0:
                        cot_bias = "BULLISH"
                    elif cot_data.get("smart_sentiment", 0) < 0:
                        cot_bias = "BEARISH"
                    else:
                        cot_bias = "NEUTRAL"
            except Exception:
                pass

        # 6. Macro Divergence (from pre-fetched map)
        macro_data = macro_div_map.get(symbol)

        # 7. Retail Sentiment (sync, fast)
        retail = _get_retail_sentiment(symbol)

        # 8. Composite Heatmap Score
        heatmap_score = HeatmapScorer.calculate(
            technical=technical,
            confluence=confluence,
            macro_data=macro_data,
            cot_data=cot_data,
            has_imminent_event=False,  # TODO: check calendar
        )

        direction = HeatmapScorer.determine_direction(technical, confluence)

        # 9. Build result
        return {
            "symbol": symbol,
            "price": current_price,
            "atr": h1_atr,
            "direction": direction,
            "heatmap_score": heatmap_score,
            "technical": technical,
            "behavior_dna": behavior_dna,
            "confluence": confluence,
            "price_action": price_action,
            "cot": cot_data,
            "cot_bias": cot_bias,
            "macro_divergence": macro_data,
            "retail_sentiment": retail,
            "usd_index": usd_data,
        }

    def _calculate_confluence(self, tf_data: Dict) -> Dict:
        """
        Replicates MacroLensAgentV2.calculate_confluence_score logic.
        Deterministic 0-5 scoring.
        """
        score_buy = 0
        score_sell = 0
        details = []

        # 1. Trend Alignment (D1/H4)
        d1_struct = tf_data.get("D1", {}).get("structure", "Ranges")
        h4_struct = tf_data.get("H4", {}).get("structure", "Ranges")
        if "BULLISH" in d1_struct or "BULLISH" in h4_struct:
            score_buy += 1
        if "BEARISH" in d1_struct or "BEARISH" in h4_struct:
            score_sell += 1

        # 2. RSI Extremes (H1)
        h1_rsi = tf_data.get("H1", {}).get("rsi", 50)
        if h1_rsi < 35:
            score_buy += 1
        if h1_rsi > 65:
            score_sell += 1

        # 3. Key Levels (S/R)
        current_price = tf_data.get("H1", {}).get("price", 0)
        h1_zones = tf_data.get("H1", {}).get("zones", {})
        supports = h1_zones.get("support", [])
        resistances = h1_zones.get("resistance", [])
        tolerance = current_price * 0.0015 if current_price else 0

        if any(abs(current_price - s) < tolerance for s in supports):
            score_buy += 1
            details.append("At Support")
        if any(abs(current_price - r) < tolerance for r in resistances):
            score_sell += 1
            details.append("At Resistance")

        # 4. Patterns
        patterns = tf_data.get("H1", {}).get("patterns", [])
        if any("Bullish" in p for p in patterns):
            score_buy += 1
        if any("Bearish" in p for p in patterns):
            score_sell += 1

        # Determine bias
        if score_buy == score_sell:
            final_bias = "NEUTRAL"
            final_score = score_buy
        elif score_buy > score_sell:
            final_bias = "BUY"
            final_score = score_buy
        else:
            final_bias = "SELL"
            final_score = score_sell

        if final_score <= 1:
            final_bias = "NEUTRAL"

        return {
            "bias": final_bias,
            "score": final_score,
            "buy_score": score_buy,
            "sell_score": score_sell,
            "details": details,
        }

    def _calculate_price_action(self, technical: Dict, behavior_dna: Dict) -> Optional[Dict]:
        """Calculate price action score using existing TechnicalAnalyzer method."""
        d1_struct = technical.get("D1", {}).get("structure", "Ranges")
        h1_struct = technical.get("H1", {}).get("structure", "Ranges")
        # Use H1 DNA as proxy for M5 (scanner doesn't fetch M5)
        h1_dna = behavior_dna.get("H1", {})

        if "error" in h1_dna:
            return None

        try:
            temp_ta = TechnicalAnalyzer([{"open": 1, "close": 1}])
            return temp_ta.get_price_action_score(d1_struct, h1_struct, h1_dna)
        except Exception:
            return None

    async def _fetch_macro_divergence(self) -> Dict:
        """
        Get macro divergence data. Cache for 1 hour (expensive to compute).
        Returns: {symbol: divergence_data}
        """
        now = datetime.now(timezone.utc)
        if (self._macro_divergence_cache is not None and 
            self._macro_cache_time and 
            (now - self._macro_cache_time).total_seconds() < 3600):
            return self._macro_divergence_cache

        macro_div = _get_macro_divergence()
        if not macro_div:
            return {}

        try:
            pairs = macro_div.scan_for_divergence()
            result = {}
            for p in pairs:
                sym = p.get("pair") or p.get("symbol", "")
                result[sym] = p
            self._macro_divergence_cache = result
            self._macro_cache_time = now
            return result
        except Exception as e:
            logger.warning(f"Macro divergence scan failed: {e}")
            return {}

    def _fetch_usd_index(self) -> Optional[Dict]:
        """Get latest USD Index data."""
        engine = _get_usd_engine()
        if not engine:
            return None
        try:
            return engine.get_latest()
        except Exception as e:
            logger.warning(f"USD Index fetch failed: {e}")
            return None

    def stop(self):
        """Stop the scanner loop."""
        self._running = False
        logger.info("Scanner stopped.")


# ===========================================================================
# Singleton instances
# ===========================================================================
market_scanner = MarketScanner()
scanner_cache = market_scanner.cache
