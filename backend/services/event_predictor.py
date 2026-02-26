"""
Event Predictor Service
=======================
Generates probability-weighted forecasts for upcoming economic events.

Uses historical pattern matching from EventAnalyzer to:
1. Predict Beat/Miss probability
2. Estimate expected price movement
3. Generate actionable trade signals
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Literal
from datetime import datetime, timedelta
from dataclasses import dataclass

from backend.services.event_analyzer import EventAnalyzer

logger = logging.getLogger("EventPredictor")

# Database path
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "market_data.db"


@dataclass
class EventPrediction:
    """Structured prediction output."""
    event_name: str
    currency: str
    predicted_outcome: str          # BEAT, MISS, NEUTRAL
    probability: float              # 0.0 to 1.0
    confidence: str                 # HIGH, MEDIUM, LOW
    expected_direction: str         # BULLISH, BEARISH, NEUTRAL
    bias_score: int                 # -3 to +3
    historical_sample: int          # Number of historical events used
    recommendation: str             # Action recommendation
    avg_pips: float = 0.0           # Forecast move size
    trend_forecast: str = "NEUTRAL" # 3-Day Trend Prediction

class EventPredictor:
    """
    Generates probabilistic forecasts for economic events.
    
    Prediction Logic:
    1. Fetch historical playbook for the event
    2. Analyze momentum (forecast vs previous)
    3. Calculate probability based on historical beat rate
    4. Generate directional bias and recommendation
    """
    
    # Currency -> Symbol mapping for common pairs
    CURRENCY_IMPACT = {
        "USD": {"positive": ["USDJPY", "USDCHF", "USDCAD"], 
                "negative": ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"]},
        "EUR": {"positive": ["EURUSD", "EURJPY", "EURGBP"],
                "negative": ["USDEUR"]},  # Inverse
        "GBP": {"positive": ["GBPUSD", "GBPJPY"],
                "negative": ["EURGBP"]},
        "JPY": {"positive": [],  # JPY strength = pairs go down
                "negative": ["USDJPY", "EURJPY", "GBPJPY"]},
        "AUD": {"positive": ["AUDUSD", "AUDJPY"],
                "negative": []},
        "NZD": {"positive": ["NZDUSD"],
                "negative": []},
        "CAD": {"positive": [],
                "negative": ["USDCAD"]},
        "CHF": {"positive": [],
                "negative": ["USDCHF"]}
    }
    
    def __init__(self, db_path: Path = DB_PATH):
        self.analyzer = EventAnalyzer(db_path)
        
    def predict_event(self, event_name: str, forecast: float, 
                      previous: float, currency: str = "USD",
                      simulation_date: str = None) -> EventPrediction:
        """
        Generate a prediction for an upcoming event.
        
        Args:
            event_name: Name of the economic event
            forecast: Consensus forecast value
            previous: Previous release value
            currency: Event currency (e.g., USD, EUR)
            
        Returns:
            EventPrediction dataclass
        """
        # Get full analysis from EventAnalyzer
        analysis = self.analyzer.analyze_upcoming_event(
            event_name, forecast, previous, currency, simulation_date
        )
        
        # Determine predicted outcome (Simplified Logic "Historical Bias")
        beat_rate = analysis["historical_beat_rate"]
        # DEBUG: Print beat rate to see distribution
        # print(f"DEBUG: {event_name} Beat Rate: {beat_rate:.2f}")
        
        # Logic V8: "Trend Sniper" Logic (FINAL - Clean Data Verified)
        # With Clean Data (= match), Trend holds true (66% implied).
        if beat_rate > 0.65:
            predicted_outcome = "BEAT"
            probability = beat_rate
            
        elif beat_rate < 0.35:
            predicted_outcome = "MISS"
            probability = 1 - beat_rate
            
        else:
            predicted_outcome = "NEUTRAL"
            probability = 0.5
            
        # Optional: Momentum boost if it ALIGNS (but don't fail if it disagrees)
        momentum_dir = analysis["momentum_direction"]
        if predicted_outcome == "BEAT" and momentum_dir == "IMPROVING":
            probability = min(probability + 0.1, 0.95)
        elif predicted_outcome == "MISS" and momentum_dir == "DETERIORATING":
            probability = min(probability + 0.1, 0.95)
            
        # Determine confidence
        sample_size = analysis["sample_size"]
        if sample_size >= 20 and abs(beat_rate - 0.5) > 0.2:
            confidence = "HIGH"
        elif sample_size >= 10:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"
            
        # Direction for currency
        bias_score = analysis["bias_score"]
        if bias_score > 0:
            expected_direction = "BULLISH"
        elif bias_score < 0:
            expected_direction = "BEARISH"
        else:
            expected_direction = "NEUTRAL"
            
        # Part 2: Trend Forecast (D1-D3)
        # Logic: If Forecast > Previous, what is the trend?
        # We assume standard logic (Higher=Stronger) unless inverted manually.
        trend_forecast = "NEUTRAL"
        if forecast > previous:
            trend_forecast = "BULLISH (3 Days)"
        elif forecast < previous:
            trend_forecast = "BEARISH (3 Days)"
            
        # Generate recommendation
        recommendation = self._generate_recommendation(
            predicted_outcome, probability, confidence, currency
        )
        
        return EventPrediction(
            event_name=event_name,
            currency=currency,
            predicted_outcome=predicted_outcome,
            probability=round(probability, 2),
            confidence=confidence,
            expected_direction=expected_direction,
            bias_score=bias_score,
            historical_sample=sample_size,
            recommendation=recommendation,
            avg_pips=analysis.get("avg_pips", 0.0), # Part 1 Feature
            trend_forecast=trend_forecast           # Part 2 Feature
        )
    
    def _generate_recommendation(self, outcome: str, probability: float,
                                  confidence: str, currency: str) -> str:
        """Generate actionable recommendation."""
        if confidence == "LOW":
            return f"CAUTION: Insufficient historical data for {currency}"
        
        if probability < 0.55:
            return "NEUTRAL: Toss-up, wait for actual release"
        
        if outcome == "BEAT":
            pairs = self.CURRENCY_IMPACT.get(currency, {})
            long_pairs = pairs.get("positive", [])
            short_pairs = pairs.get("negative", [])
            
            if probability >= 0.7:
                return f"HIGH PROB BEAT: Consider LONG {long_pairs[0] if long_pairs else currency}"
            return f"LEAN BEAT: Watch for LONG {long_pairs[0] if long_pairs else currency}"
            
        elif outcome == "MISS":
            pairs = self.CURRENCY_IMPACT.get(currency, {})
            short_pairs = pairs.get("negative", [])
            
            if probability >= 0.7:
                return f"HIGH PROB MISS: Consider SHORT {short_pairs[0] if short_pairs else currency}"
            return f"LEAN MISS: Watch for SHORT {short_pairs[0] if short_pairs else currency}"
            
        return "NEUTRAL: No strong directional bias"
    
    def get_symbol_impact(self, currency: str, outcome: str, 
                          symbol: str) -> Dict:
        """
        Determine how a currency event outcome would impact a specific symbol.
        
        Args:
            currency: Event currency (USD, EUR, etc.)
            outcome: BEAT or MISS
            symbol: Trading pair (EURUSD, USDJPY, etc.)
            
        Returns:
            Dict with direction and reasoning
        """
        # Determine if symbol contains currency as base or quote
        base = symbol[:3]
        quote = symbol[3:]
        
        is_base = (currency == base)
        is_quote = (currency == quote)
        
        if not (is_base or is_quote):
            return {
                "impact": "NONE",
                "direction": "NEUTRAL",
                "reason": f"{currency} not in {symbol}"
            }
        
        # BEAT = Currency strength
        # MISS = Currency weakness
        currency_direction = "STRONG" if outcome == "BEAT" else "WEAK"
        
        if is_base:
            # Currency is base (e.g., USD in USDJPY)
            # Strong USD = USDJPY UP
            pair_direction = "BULLISH" if outcome == "BEAT" else "BEARISH"
        else:
            # Currency is quote (e.g., USD in EURUSD)
            # Strong USD = EURUSD DOWN
            pair_direction = "BEARISH" if outcome == "BEAT" else "BULLISH"
            
        return {
            "impact": "DIRECT",
            "direction": pair_direction,
            "reason": f"{currency} is {'base' if is_base else 'quote'} in {symbol}. {currency_direction} {currency} = {pair_direction} {symbol}"
        }
    
    def generate_playbook(self, event_name: str, symbol: str,
                          currency: str = None) -> Dict:
        """
        Generate a full "playbook" for trading an event on a specific symbol.
        
        Returns:
            Dict with scenarios (BEAT, MISS, IN_LINE) and expected actions
        """
        # Auto-detect currency if not provided
        if not currency:
            # Try to infer from event name or use USD as default
            currency = "USD"
            
        # Get historical stats
        stats = self.analyzer.calculate_deviation_stats(event_name, currency)
        
        # Generate scenarios
        playbook = {
            "event": event_name,
            "symbol": symbol,
            "currency": currency,
            "historical_sample": stats["sample_size"],
            "scenarios": {}
        }
        
        for outcome in ["BEAT", "MISS", "IN_LINE"]:
            impact = self.get_symbol_impact(currency, outcome, symbol)
            
            # Calculate historical probability
            if outcome == "BEAT":
                prob = stats["categories"].get("BIG_BEAT", 0) + stats["categories"].get("SMALL_BEAT", 0)
            elif outcome == "MISS":
                prob = stats["categories"].get("BIG_MISS", 0) + stats["categories"].get("SMALL_MISS", 0)
            else:
                prob = stats["categories"].get("IN_LINE", 0)
                
            prob_pct = (prob / stats["sample_size"] * 100) if stats["sample_size"] > 0 else 0
            
            playbook["scenarios"][outcome] = {
                "probability": f"{prob_pct:.0f}%",
                "symbol_direction": impact["direction"],
                "action": self._get_scenario_action(impact["direction"], outcome),
                "reason": impact["reason"]
            }
            
        return playbook
    
    def _get_scenario_action(self, direction: str, outcome: str) -> str:
        """Get action for a scenario."""
        if direction == "BULLISH":
            return "BUY on confirmation" if outcome != "IN_LINE" else "HOLD"
        elif direction == "BEARISH":
            return "SELL on confirmation" if outcome != "IN_LINE" else "HOLD"
        return "WAIT for clarity"
    
    def format_for_prompt(self, prediction: EventPrediction, 
                          playbook: Dict = None) -> str:
        """
        Format prediction for LLM prompt injection.
        """
        output = f"""📈 **EVENT PREDICTION: {prediction.event_name}**

**FORECAST:**
- Predicted Outcome: {prediction.predicted_outcome}
- Probability: {prediction.probability:.0%}
- Confidence: {prediction.confidence}

**DIRECTIONAL BIAS:**
- {prediction.currency} Direction: {prediction.expected_direction}
- Bias Score: {prediction.bias_score:+d} (Range: -3 to +3)
- Sample Size: {prediction.historical_sample} events

**RECOMMENDATION:** {prediction.recommendation}"""

        if playbook:
            output += f"""

**SCENARIO PLAYBOOK ({playbook['symbol']}):**"""
            for scenario, details in playbook["scenarios"].items():
                output += f"""
- {scenario} ({details['probability']}): {details['action']}
  └ {details['reason']}"""
                
        return output


# =============================================================================
# STANDALONE TEST
# =============================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    predictor = EventPredictor()
    
    # Test 1: Generate prediction
    print("=== Test 1: CPI Prediction ===")
    prediction = predictor.predict_event("CPI", forecast=3.2, previous=3.1, currency="USD")
    print(f"Outcome: {prediction.predicted_outcome}")
    print(f"Probability: {prediction.probability:.0%}")
    print(f"Direction: {prediction.expected_direction}")
    print(f"Recommendation: {prediction.recommendation}")
    
    # Test 2: Symbol impact
    print("\n=== Test 2: Symbol Impact ===")
    for symbol in ["EURUSD", "USDJPY", "GBPUSD"]:
        impact = predictor.get_symbol_impact("USD", "BEAT", symbol)
        print(f"  {symbol}: {impact['direction']} - {impact['reason']}")
    
    # Test 3: Generate playbook
    print("\n=== Test 3: CPI Playbook for EURUSD ===")
    playbook = predictor.generate_playbook("CPI", "EURUSD", "USD")
    for scenario, details in playbook["scenarios"].items():
        print(f"  {scenario}: {details['symbol_direction']} - {details['action']}")
    
    # Test 4: Full prompt format
    print("\n=== Test 4: Prompt Format ===")
    print(predictor.format_for_prompt(prediction, playbook))
