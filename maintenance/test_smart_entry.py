from backend.services.event_executor import EventExecutor
from backend.services.event_predictor import EventPrediction

def test_smart_entry():
    executor = EventExecutor()
    
    # Mock Prediction (BUY Signal for USD -> SELL EURUSD)
    # BEAT on USD = SELL EURUSD
    pred_beat = EventPrediction(
        event_name="CPI", currency="USD", predicted_outcome="BEAT",
        probability=0.8, confidence="HIGH", expected_direction="BULLISH",
        bias_score=2, historical_sample=10, recommendation="BUY",
        avg_pips=20, trend_forecast="BULLISH"
    )
    
    # Mock Prediction (MISS on USD -> BUY EURUSD)
    pred_miss = EventPrediction(
        event_name="CPI", currency="USD", predicted_outcome="MISS",
        probability=0.8, confidence="HIGH", expected_direction="BEARISH",
        bias_score=-2, historical_sample=10, recommendation="SELL",
        avg_pips=20, trend_forecast="BEARISH"
    )
    
    print("----- Scenario 1: SELL SIGNAL (USD Strength) -----")
    
    # Test A: SELL at RSI 50 (Neutral) -> MARKET
    print("\n[TEST A] SELL + RSI 50")
    sig = executor.generate_signal(pred_beat, "EURUSD", technicals={"rsi": 50})
    print(f"Order: {sig.order_type}")
    assert sig.order_type == "MARKET"

    # Test B: SELL at RSI 75 (Selling High) -> MARKET (Good!)
    print("\n[TEST B] SELL + RSI 75 (Selling High)")
    sig = executor.generate_signal(pred_beat, "EURUSD", technicals={"rsi": 75})
    print(f"Order: {sig.order_type}")
    assert sig.order_type == "MARKET"
    
    # Test C: SELL at RSI 20 (Selling Low) -> LIMIT (Pullback expected)
    print("\n[TEST C] SELL + RSI 20 (Selling Low)")
    sig = executor.generate_signal(pred_beat, "EURUSD", technicals={"rsi": 20})
    print(f"Order: {sig.order_type}")
    assert "LIMIT" in sig.order_type
    
    print("\n----- Scenario 2: BUY SIGNAL (USD Weakness) -----")
    
    # Test D: BUY at RSI 80 (Buying High) -> LIMIT
    print("\n[TEST D] BUY + RSI 80 (Buying High)")
    sig = executor.generate_signal(pred_miss, "EURUSD", technicals={"rsi": 80})
    print(f"Order: {sig.order_type}")
    assert "LIMIT" in sig.order_type
    
    print("\n✅ Smart Entry Logic Verified!")

if __name__ == "__main__":
    test_smart_entry()
