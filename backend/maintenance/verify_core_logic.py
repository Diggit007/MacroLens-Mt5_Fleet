import sys
import os
import logging
import pandas as pd
import numpy as np

# Ensure backend in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.technical_analysis import TechnicalAnalyzer, SymbolBehaviorAnalyzer

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("LogicVerifier")

def generate_trend_candles(start_price=1.1000, count=50, trend="up"):
    """Generates a perfect trend."""
    candles = []
    price = start_price
    for i in range(count):
        step = 0.0005 if trend == "up" else -0.0005
        candles.append({
            "open": price,
            "high": price + 0.0008,
            "low": price - 0.0002,
            "close": price + 0.0005,
            "volume": 100
        })
        price += step
    return candles

def generate_pinbar_candles(count=24):
    """Generates candles with long upper wicks (Shooting Stars)."""
    candles = []
    price = 1.1000
    for i in range(count):
        candles.append({
            "open": price,
            "high": price + 0.0010, # Long top wick
            "low": price - 0.0001,
            "close": price + 0.0001, # Small body
            "volume": 100
        })
        # Price stays flat
    return candles

def test_core_logic():
    logger.info(">>> STARTING CORE LOGIC VERIFICATION <<<")
    
    # 1. Test Technical Analyzer (Uptrend)
    logger.info("\n[1] Testing Technical Analyzer (Trend Detection)...")
    uptrend_data = generate_trend_candles(trend="up")
    ta = TechnicalAnalyzer(uptrend_data)
    
    structure = ta.get_market_structure()
    logger.info(f"    Structure Detected: {structure}")
    
    if "BULLISH" in structure:
        logger.info("    PASSED: Correctly identified Bullish Trend.")
    else:
        logger.error(f"    FAILED: Expected BULLISH, got {structure}")
        
    rsi = ta.get_rsi(14)
    logger.info(f"    RSI (14): {rsi}")
    if rsi > 60:
         logger.info("    PASSED: RSI reflects uptrend (> 60).")
    else:
         logger.warning(f"    WARNING: RSI {rsi} seems low for a perfect uptrend.")

    # 2. Test Pivot Points
    logger.info("\n[2] Testing Pivot Points...")
    pivots = ta.get_pivot_points()
    logger.info(f"    Pivots: {pivots}")
    if pivots["PP"] > 0:
        logger.info("    PASSED: Pivot Points calculated.")
    else:
        logger.error("    FAILED: Pivot Points are zero/invalid.")

    # 3. Test Behavior Analyzer (Wick Pressure)
    logger.info("\n[3] Testing Behavior Analyzer (Wick Pressure)...")
    pinbar_data = generate_pinbar_candles()
    ba = SymbolBehaviorAnalyzer()
    dna = ba.analyze(pinbar_data, "TEST", "H1")
    
    if "error" in dna:
        logger.error(f"    FAILED: {dna['error']}")
    else:
        pressure = dna['wick_pressure']['pressure']
        ratio = dna['wick_pressure']['ratio']
        logger.info(f"    Wick Pressure: {pressure} (Ratio: {ratio})")
        
        # We expect HIGH ratio (lots of top wick) -> Bearish Pressure?
        # Logic in code: "BEARISH" if wick_ratio > 1 else "BULLISH"
        # wick_ratio = total_top_wick / total_bot_wick.
        # Top wicks are huge -> Ratio > 1.
        
        if pressure == "BEARISH" and ratio > 1.0:
            logger.info("    PASSED: Correctly identified Selling Pressure (Top Wicks).")
        else:
            logger.error(f"    FAILED: Expected BEARISH pressure, got {pressure} with ratio {ratio}")

    # 4. Test Price Action Score (Re-using TA instance)
    logger.info("\n[4] Testing Price Action Score Strategy...")
    # Scenario: Bullish D1, Bullish H1, Bullish Wick Pressure (Dip Buying)
    # let's mock the input
    mock_dna_bullish = {
        "wick_pressure": {"pressure": "BULLISH", "ratio": 0.4}, # Buying pressure (small top wick, big bottom wick)
        "power_analysis": {"dominance": "BUYERS", "bull_candles": 15, "bear_candles": 9}
    }
    
    pa_score = ta.get_price_action_score(
        d1_struct="BULLISH (HH/HL)",
        h1_struct="BULLISH (HH/HL)",
        dna_report=mock_dna_bullish
    )
    
    logger.info(f"    Scenario 1 (All Bullish): Score {pa_score['score']} | Bias {pa_score['bias']}")
    
    if pa_score['bias'] == "BUY" and pa_score['score'] >= 4:
         logger.info("    PASSED: High Score for alignment.")
    else:
         logger.error("    FAILED: Strategy logic incorrect.")

    logger.info("\n>>> VERIFICATION COMPLETE <<<")

if __name__ == "__main__":
    test_core_logic()
