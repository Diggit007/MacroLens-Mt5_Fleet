import logging
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime
import json
import os

# Allow standalone execution
sys.path.append(str(Path(__file__).parent.parent.parent))

from backend.models.macro_health import CountryHealth

logger = logging.getLogger("MacroDataEngine")

# ─── Factor Weights (Must sum to 1.0) ───
WEIGHTS = {
    "growth":    0.25,   # GDP + Unemployment
    "inflation": 0.20,   # CPI direction / hawkish pressure
    "monetary":  0.25,   # Rate level + policy direction
    "real_rate": 0.15,   # Rate - Inflation (carry attractiveness)
    "cot":       0.15,   # Institutional positioning (Willco)
}

class MacroDataEngine:
    """
    Institutional-Grade Macro Scoring Engine.
    Aggregates economic data + COT positioning to build a weighted 
    'CountryHealth' profile for each major currency.
    
    Factor Breakdown:
      - Growth (25%): GDP momentum + labor market strength
      - Inflation (20%): CPI direction → hawkish/dovish pressure
      - Monetary Policy (25%): Rate level + implied policy direction
      - Real Rate (15%): Nominal rate − inflation → carry attractiveness
      - COT Positioning (15%): Willco Index → institutional conviction
    """
    
    def __init__(self, db_path: str = "C:/MacroLens/backend/market_data.db"):
        self.db_path = db_path
        self.major_currencies = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]
        self._cot_engine = None
        
        # Mapping Event Names to Standard Fields
        self.institutional_cache = {}
        self._load_institutional_data()
        
        self.indicators = {
            "inflation_rate": [
                "CPI (YoY)", "Core CPI (YoY)", "CPIH (YoY)", "Inflation (YoY)", 
                "Monthly CPI Indicator (YoY)", "BoJ Core CPI (YoY)", 
                "German CPI (YoY)", "French CPI (YoY)", "Spanish CPI (YoY)", "Italian CPI (YoY)",
                "National Core CPI (YoY)"
            ],
            "unemployment_rate": [
                "Unemployment Rate", "German Unemployment Rate", "French Unemployment Rate", 
                "Italian Monthly Unemployment Rate", "Spanish Unemployment Rate", "Chinese Unemployment Rate"
            ],
            "gdp_growth": [
                "GDP (YoY)", "GDP Annualized (QoQ)", "German GDP (YoY)", "French GDP (YoY)", 
                "Italian GDP (YoY)", "Spanish GDP (YoY)", "GDP (QoQ)"
            ],
            "interest_rate": [
                "Fed Interest Rate Decision", "Interest Rate Decision", "ECB Interest Rate Decision", 
                "BoE Interest Rate Decision", "RBA Interest Rate Decision", "RBNZ Interest Rate Decision", 
                "BoC Interest Rate Decision", "SNB Interest Rate Decision", "BoJ Interest Rate Decision"
            ]
        }
    
    def _get_cot_engine(self):
        """Lazy-load COTEngine to avoid circular imports."""
        if self._cot_engine is None:
            try:
                from backend.services.cot.engine import COTEngine
                self._cot_engine = COTEngine()
                self._cot_engine.load_data()
                logger.info("COTEngine loaded successfully")
            except Exception as e:
                logger.warning(f"Could not load COTEngine: {e}")
        return self._cot_engine

    def _load_institutional_data(self):
        """Loads scraped TE data from JSON cache."""
        try:
            cache_path = "C:/MacroLens/backend/institutional_data.json"
            if os.path.exists(cache_path):
                with open(cache_path, "r") as f:
                    self.institutional_cache = json.load(f)
                logger.info("Loaded institutional data cache.")
        except Exception as e:
            logger.warning(f"Could not load institutional data: {e}")
            
    def get_all_country_health(self) -> Dict[str, CountryHealth]:
        """Returns a dictionary of { "USD": CountryHealth(...), "EUR": ... }"""
        profiles = {}
        conn = sqlite3.connect(self.db_path)
        
        try:
            for currency in self.major_currencies:
                profiles[currency] = self._build_profile(conn, currency)
        except Exception as e:
            logger.error(f"Error building macro profiles: {e}")
        finally:
            conn.close()
            
        return profiles

    def _build_profile(self, conn, currency: str) -> CountryHealth:
        profile = CountryHealth(currency=currency)
        
        # ── 1. Fetch Raw Indicators ──
        profile.inflation_rate = self._get_latest_value(conn, currency, self.indicators["inflation_rate"])
        profile.unemployment_rate = self._get_latest_value(conn, currency, self.indicators["unemployment_rate"])
        profile.gdp_growth = self._get_latest_value(conn, currency, self.indicators["gdp_growth"])
        profile.interest_rate = self._get_latest_value(conn, currency, self.indicators["interest_rate"])
        
        # New: Trade Balance (if available in DB, simplistic check)
        # We don't have a strict mapping yet, so we'll try generic likelihood
        profile.trade_balance = self._get_latest_value(conn, currency, ["Trade Balance", "Balance of Trade"])
        
        # ── 2. Derived: Real Rate & Momentum ──
        if profile.interest_rate is not None and profile.inflation_rate is not None:
            profile.real_rate = round(profile.interest_rate - profile.inflation_rate, 2)
            
        # calculate 3-month momentum (Slope)
        profile.cpi_momentum = self._get_3m_momentum(conn, currency, self.indicators["inflation_rate"])
        profile.unemployment_momentum = self._get_3m_momentum(conn, currency, self.indicators["unemployment_rate"])
        profile.gdp_momentum = self._get_3m_momentum(conn, currency, self.indicators["gdp_growth"])
        
        # ── 2b. Institutional Data (TE Scrape) ──
        if currency in self.institutional_cache:
            te_data = self.institutional_cache[currency]
            profile.pmi_manufacturing = te_data.get("pmi_manufacturing")
            profile.pmi_services = te_data.get("pmi_services")
            profile.core_inflation = te_data.get("core_inflation")
            profile.govt_debt_gdp = te_data.get("debt_to_gdp") # Key mapping check: TE scraper uses debt_to_gdp
            profile.current_account_gdp = te_data.get("current_account") 
            # Note: TE scraper keys must match what we look for here. 
            # Scraper produces: gdp_growth_yoy, interest_rate, etc.
            # And details: pmi_manufacturing, pmi_services, core_inflation.
            
            # If DB missing core fields, fallback to TE (Real-time snapshot)
            if profile.inflation_rate is None: profile.inflation_rate = te_data.get("inflation_rate")
            if profile.unemployment_rate is None: profile.unemployment_rate = te_data.get("unemployment_rate")
            if profile.gdp_growth is None: profile.gdp_growth = te_data.get("gdp_growth_yoy")
            if profile.interest_rate is None: profile.interest_rate = te_data.get("interest_rate")
            
        # ── 3. COT Positioning ──
        self._attach_cot(profile, currency)
        
        # ── 3b. Central Bank Sentiment (AI Analysis) ──
        # We'll use a simplified version here to avoid massive latency during a scan.
        # Ideally this is pre-calculated/cached in DB. For now, we'll placeholder it 
        # or IF we had the DB field populated, we'd read it.
        # FUTURE: Read 'central_bank_analysis' table.
        # Current: derived from interest rate trend?
        # Actually, let's use the momentum of the Interest Rate as a proxy for the Cycle
        rate_momentum = self._get_3m_momentum(conn, currency, self.indicators["interest_rate"])
        if rate_momentum:
             if rate_momentum > 0: profile.policy_bias = "HAWKISH"
             elif rate_momentum < 0: profile.policy_bias = "DOVISH"
             else: profile.policy_bias = "NEUTRAL"
        
        # ── 4. Calculate Factor Sub-Scores (each 0-10) ──
        profile.growth_score = self._score_growth(profile)
        profile.inflation_score = self._score_inflation(profile)
        profile.monetary_score = self._score_monetary(profile)
        profile.real_rate_score = self._score_real_rate(profile)
        profile.cot_score = self._score_cot(profile)
        
        # ── 5. Weighted Composite ──
        composite = (
            profile.growth_score    * WEIGHTS["growth"] +
            profile.inflation_score * WEIGHTS["inflation"] +
            profile.monetary_score  * WEIGHTS["monetary"] +
            profile.real_rate_score * WEIGHTS["real_rate"] +
            profile.cot_score       * WEIGHTS["cot"]
        )
        profile.health_score = round(max(0.0, min(10.0, composite)), 2)
        profile.last_updated = datetime.utcnow().isoformat()
        
        return profile

    # ─── Momentum Logic ───────────────────────────────────────────────

    def _get_3m_momentum(self, conn, currency: str, event_names: list) -> Optional[float]:
        """
        Calculates the 3-month slope/momentum of a data series.
        Returns: (Current Value - Value 3 Months Ago)
        Positive = Rising Trend. Negative = Falling Trend.
        """
        try:
            conditions = " OR ".join([f"event_name LIKE ?" for _ in event_names])
            params = [f"%{name}%" for name in event_names]
            full_params = [currency] + params
            
            # Fetch last 4 months to get a reliable trail
            query = f"""
                SELECT actual_value, event_date
                FROM economic_events 
                WHERE currency = ? 
                AND ({conditions})
                AND actual_value IS NOT NULL
                ORDER BY event_date DESC, event_time DESC
                LIMIT 5
            """
            cursor = conn.cursor()
            cursor.execute(query, full_params)
            rows = cursor.fetchall()
            
            if not rows or len(rows) < 2:
                return None
                
            current_val = float(rows[0][0])
            
            # Find a value ~3 months ago (index 2 or 3 usually)
            # Simple approximation: Compare vs 3rd record back
            prev_val = float(rows[min(len(rows)-1, 3)][0])
            
            return round(current_val - prev_val, 2)
            
        except Exception as e:
            return None

    # ─── Factor Scoring Functions ───────────────────────────────────────

    def _score_growth(self, p: CountryHealth) -> float:
        """GDP momentum + labor market. Higher = stronger economy."""
        score = 5.0
        
        if p.gdp_growth is not None:
            if p.gdp_growth > 3.0: score += 2.5
            elif p.gdp_growth > 2.0: score += 1.5
            elif p.gdp_growth > 1.0: score += 0.5
            elif p.gdp_growth > 0: pass  # neutral
            elif p.gdp_growth > -1.0: score -= 1.5
            else: score -= 3.0  # Recession territory
            
        # Momentum Bonus: Accelerating Growth is VERY Bullish
        if p.gdp_momentum and p.gdp_momentum > 0.2: score += 1.0
        if p.gdp_momentum and p.gdp_momentum < -0.2: score -= 1.0
        
        if p.unemployment_rate is not None:
            if p.unemployment_rate < 3.5: score += 1.5
            elif p.unemployment_rate < 4.5: score += 0.5
            elif p.unemployment_rate > 7.0: score -= 2.0
            elif p.unemployment_rate > 5.5: score -= 1.0
            
        # Labor Momentum: Rising Unemployment is Bearish
        if p.unemployment_momentum and p.unemployment_momentum > 0.3: score -= 1.0 # Cooling fast
        if p.unemployment_momentum and p.unemployment_momentum < -0.2: score += 0.5 # Heating up
        
        # Phase 8: PMI (Leading Indicator) - High Impact
        if p.pmi_manufacturing:
            if p.pmi_manufacturing > 55: score += 2.0      # Booming
            elif p.pmi_manufacturing > 52: score += 1.0    # Solid Expansion
            elif p.pmi_manufacturing > 50: score += 0.5    # Growth
            elif p.pmi_manufacturing < 45: score -= 2.0    # Deep Contraction
            elif p.pmi_manufacturing < 48: score -= 1.0    # Contraction
            elif p.pmi_manufacturing < 50: score -= 0.5    # Slight Contraction

        if p.pmi_services:
            # Services is often stickier / larger part of DM economies
            if p.pmi_services > 55: score += 1.5
            elif p.pmi_services < 45: score -= 1.5
        
        return max(0.0, min(10.0, score))

    def _score_inflation(self, p: CountryHealth) -> float:
        """
        Inflation scoring from a SWING TRADER perspective:
        - High inflation → hawkish CB pressure → bullish currency (short-term)
        - Falling inflation (Disinflation) → dovish pivot → bearish currency
        """
        score = 5.0
        
        if p.inflation_rate is not None:
            if p.inflation_rate > 5.0: score += 1.0    # Too high → stagflation risk tempers benefit
            elif p.inflation_rate > 3.5: score += 2.5   # Sweet spot: hawkish, not dangerous
            elif p.inflation_rate > 2.5: score += 1.5   # Mildly hawkish
            elif p.inflation_rate > 1.5: score -= 0.5   # At target, no urgency 
            elif p.inflation_rate > 0: score -= 1.5     # Disinflation → dovish
            else: score -= 3.0                           # Deflation → deeply dovish
            
        # Momentum: Falling inflation kills the carry trade idea faster than low levels
        if p.cpi_momentum and p.cpi_momentum < -0.5: score -= 1.5 # Disinflation accelerating -> Dovish
        if p.cpi_momentum and p.cpi_momentum > 0.5: score += 1.0  # Re-acceleration -> Hawkish

        # Phase 8: Core Inflation (Structural Stickiness)
        if p.core_inflation is not None:
            # If Core is significantly higher than Headline, inflation is sticky (Hawkish)
            if p.inflation_rate and p.core_inflation > (p.inflation_rate + 0.5):
                score += 1.0  # Sticky / Underlying pressure
            
            # Absolute Core Levels
            if p.core_inflation > 4.0: score += 1.0
            elif p.core_inflation < 2.0: score -= 1.0
        
        return max(0.0, min(10.0, score))

    def _score_monetary(self, p: CountryHealth) -> float:
        """
        Current rate level → yield attractiveness.
        Higher rates attract capital flows.
        + Policy Bias (Hawkish = Good for Yield)
        """
        score = 5.0
        
        if p.interest_rate is not None:
            if p.interest_rate >= 5.0: score += 3.0     # Very attractive yield
            elif p.interest_rate >= 4.0: score += 2.0
            elif p.interest_rate >= 3.0: score += 1.0
            elif p.interest_rate >= 1.0: score -= 0.5
            elif p.interest_rate >= 0: score -= 1.5
            else: score -= 3.0                           # Negative rates (JPY/CHF)
            
        # Policy Bias Adjustment
        if p.policy_bias == "HAWKISH": score += 1.5
        elif p.policy_bias == "DOVISH": score -= 1.5
        
        # Phase 8: Debt/GDP Constraint (Fiscal Dominance)
        # Verify if the country can afford high rates. 
        # High Debt (>120%) + High Rates = Risk of financial instability / forced cuts.
        if p.govt_debt_gdp and p.govt_debt_gdp > 120:
             # If rates are already high (>3%), high debt is a weight on further hikes
             if p.interest_rate and p.interest_rate > 3.0:
                 score -= 1.0 # Cap the hawkishness due to fiscal fragility
                 
        return max(0.0, min(10.0, score))

    def _score_real_rate(self, p: CountryHealth) -> float:
        """
        Real rate = nominal - inflation.
        Positive real rate → currency strength (carry attractiveness).
        Negative real rate → value erosion.
        """
        score = 5.0
        
        if p.real_rate is not None:
            if p.real_rate > 2.0: score += 3.0      # Strong positive carry
            elif p.real_rate > 1.0: score += 2.0
            elif p.real_rate > 0: score += 1.0
            elif p.real_rate > -1.0: score -= 1.0
            elif p.real_rate > -2.0: score -= 2.0
            else: score -= 3.0                        # Deep negative real rate
        
        return max(0.0, min(10.0, score))

    def _score_cot(self, p: CountryHealth) -> float:
        """
        COT Willco Index (0-100) → institutional conviction.
        High Willco → commercials are structurally long → bullish.
        Low Willco → commercials are structurally short → bearish.
        """
        score = 5.0
        
        if p.cot_willco is not None:
            # Willco 0-100, centered at 50
            if p.cot_willco > 80: score += 3.0       # Extreme institutional conviction
            elif p.cot_willco > 65: score += 1.5
            elif p.cot_willco > 45: pass               # Neutral zone
            elif p.cot_willco > 30: score -= 1.5
            else: score -= 3.0                          # Extreme institutional avoidance
        
        return max(0.0, min(10.0, score))

    # ─── COT Integration ───────────────────────────────────────────────

    def _attach_cot(self, profile: CountryHealth, currency: str):
        """Pulls COT data for the currency's primary pair and attaches it."""
        cot = self._get_cot_engine()
        if not cot:
            return
        
        # Map currency to its primary CFTC pair
        cot_symbol_map = {
            "EUR": "EURUSD", "GBP": "GBPUSD", "JPY": "USDJPY",
            "AUD": "AUDUSD", "NZD": "NZDUSD", "CAD": "USDCAD",
            "CHF": "USDCHF", "USD": "DXY"
        }
        
        symbol = cot_symbol_map.get(currency)
        if not symbol:
            return
        
        try:
            data = cot.get_latest_sentiment(symbol)
            if not data:
                return
            
            willco = data.get('willco_index', 50)
            
            # For USD-quoted pairs (USDJPY, USDCAD, USDCHF), invert the signal
            # because commercials being long JPY futures = bearish USDJPY = bullish JPY
            is_usd_base = symbol.startswith("USD") and symbol != "DXY"
            if is_usd_base:
                willco = 100 - willco  # Invert for quote currency perspective
            
            profile.cot_willco = round(willco, 1)
            profile.cot_smart_net = data.get('smart_net', 0)
            profile.cot_hedge_willco = data.get('hedge_willco', 0)
            
            # Classify bias
            if willco > 80: profile.cot_bias = "EXTREME_LONG"
            elif willco > 60: profile.cot_bias = "BULLISH"
            elif willco < 20: profile.cot_bias = "EXTREME_SHORT"
            elif willco < 40: profile.cot_bias = "BEARISH"
            else: profile.cot_bias = "NEUTRAL"
            
        except Exception as e:
            logger.warning(f"COT attach failed for {currency}: {e}")

    # ─── DB Query ──────────────────────────────────────────────────────

    def _get_latest_value(self, conn, currency: str, event_names: list) -> Optional[float]:
        """Queries DB for the most recent actual_value of a specific event type."""
        conditions = " OR ".join([f"event_name LIKE ?" for _ in event_names])
        params = [f"%{name}%" for name in event_names]
        full_params = [currency] + params
        
        query = f"""
            SELECT actual_value 
            FROM economic_events 
            WHERE currency = ? 
            AND ({conditions})
            AND actual_value IS NOT NULL
            ORDER BY event_date DESC, event_time DESC
            LIMIT 1
        """
        
        cursor = conn.cursor()
        cursor.execute(query, full_params)
        row = cursor.fetchone()
        
        if row:
            try:
                return float(row[0])
            except:
                return None
        return None

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    engine = MacroDataEngine()
    profiles = engine.get_all_country_health()
    
    print(f"\n{'CCY':<5} | {'SCORE':<6} | {'GROWTH':<6} | {'INFL':<6} | {'MONET':<6} | {'REAL_R':<6} | {'COT':<6} | {'GDP':<6} | {'CPI':<6} | {'RATE':<6} | {'R.RATE':<6} | {'WILLCO':<6}")
    print("-" * 120)
    for ccy, p in sorted(profiles.items(), key=lambda x: x[1].health_score, reverse=True):
        print(f"{ccy:<5} | {p.health_score:<6} | {p.growth_score:<6} | {p.inflation_score:<6} | {p.monetary_score:<6} | {p.real_rate_score:<6} | {p.cot_score:<6} | {str(p.gdp_growth):<6} | {str(p.inflation_rate):<6} | {str(p.interest_rate):<6} | {str(p.real_rate):<6} | {str(p.cot_willco):<6}")
