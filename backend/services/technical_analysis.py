import pandas as pd
import numpy as np
import logging
from typing import List, Dict, Optional

logger = logging.getLogger("TechnicalAnalyzer")

# =============================================================================
# TECHNICAL ANALYZER
# =============================================================================

class TechnicalAnalyzer:
    """
    Calculates predictive technical indicators before sending data to AI.
    Uses standard mathematical formulas for Pivot Points, ATR, RSI, etc.
    """

    def __init__(self, candles: list):
        df = pd.DataFrame(candles)
        # Ensure we have numeric data
        for col in ['open', 'high', 'low', 'close']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        df.dropna(inplace=True)
        if df.empty:
            logger.warning("Dataframe empty after cleaning!")
        self.df = df

    def get_atr(self, period: int = 14) -> float:
        """
        Average True Range: Measures market volatility.
        Used to set realistic Stop Loss distances.
        """
        if len(self.df) < period + 1:
            return 0.0010  # Default fallback (10 pips)
        
        high = self.df['high']
        low = self.df['low']
        close = self.df['close']
        
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean().iloc[-1]
        
        return round(float(atr), 5)

    def get_adr(self, period: int = 5) -> float:
        """
        Average Daily Range (ADR): Unsmoothed average of High - Low.
        Purest measure of how much an instrument moves per day.
        """
        if len(self.df) < period: return 0.0
        
        daily_range = self.df['high'] - self.df['low']
        return round(float(daily_range.rolling(window=period).mean().iloc[-1]), 5)

    def get_sma(self, period: int = 14) -> float:
        """Simple Moving Average"""
        if len(self.df) < period: return 0.0
        return self.df['close'].rolling(window=period).mean().iloc[-1]

    def get_ema(self, period: int = 14) -> float:
        """Exponential Moving Average"""
        if len(self.df) < period: return 0.0
        return self.df['close'].ewm(span=period, adjust=False).mean().iloc[-1]

    def get_rsi(self, period: int = 14) -> float:
        """Relative Strength Index"""
        if len(self.df) < period + 1: return 50.0
        delta = self.df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return round(100 - (100 / (1 + rs)).iloc[-1], 2)

    def get_bollinger_bands(self, period: int = 20, dev: float = 2.0) -> dict:
        """Bollinger Bands"""
        if len(self.df) < period: return {"upper": 0, "middle": 0, "lower": 0}
        sma = self.df['close'].rolling(window=period).mean()
        std = self.df['close'].rolling(window=period).std()
        upper = sma + (std * dev)
        lower = sma - (std * dev)
        return {
            "upper": round(upper.iloc[-1], 5),
            "middle": round(sma.iloc[-1], 5),
            "lower": round(lower.iloc[-1], 5)
        }

    def get_macd(self, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
        """MACD"""
        if len(self.df) < slow + signal: return {"macd": 0, "signal": 0, "hist": 0}
        exp1 = self.df['close'].ewm(span=fast, adjust=False).mean()
        exp2 = self.df['close'].ewm(span=slow, adjust=False).mean()
        macd = exp1 - exp2
        sig = macd.ewm(span=signal, adjust=False).mean()
        hist = macd - sig
        return {
            "macd": round(macd.iloc[-1], 5),
            "signal": round(sig.iloc[-1], 5),
            "hist": round(hist.iloc[-1], 5)
        }
    
    def get_pivot_points(self) -> dict:
        """
        Classic Pivot Points (High/Low/Close of previous candle).
        Assumes self.df contains at least 2 rows (current and previous).
        """
        try:
            prev = self.df.iloc[-2]
            high = prev['high']
            low = prev['low']
            close = prev['close']
            
            pp = (high + low + close) / 3
            r1 = (2 * pp) - low
            s1 = (2 * pp) - high
            r2 = pp + (high - low)
            s2 = pp - (high - low)
            
            return {
                "PP": round(pp, 5),
                "R1": round(r1, 5),
                "R2": round(r2, 5),
                "S1": round(s1, 5),
                "S2": round(s2, 5)
            }
        except Exception:
            return {"PP": 0, "R1": 0, "R2": 0, "S1": 0, "S2": 0}

    def get_market_structure(self) -> str:
        """
        Simple detection: Higher Highs/Higher Lows (Bullish) or vice versa.
        """
        if len(self.df) < 20: return "NEUTRAL"
        
        last_high = self.df['high'].tail(10).max()
        prev_high = self.df['high'].tail(20).head(10).max()
        
        last_low = self.df['low'].tail(10).min()
        prev_low = self.df['low'].tail(20).head(10).min()
        
        if last_high > prev_high and last_low > prev_low:
            return "BULLISH (HH/HL)"
        elif last_high < prev_high and last_low < prev_low:
            return "BEARISH (LH/LL)"
        else:
            return "RANGING"

    def get_candle_patterns(self) -> List[str]:
        """
        Detects significant Price Action patterns on the LAST completed candle.
        """
        if len(self.df) < 3: return []
        patterns = []
        
        curr = self.df.iloc[-1]
        prev = self.df.iloc[-2]
        
        body_size = abs(curr['close'] - curr['open'])
        wick_top = curr['high'] - max(curr['close'], curr['open'])
        wick_bottom = min(curr['close'], curr['open']) - curr['low']
        total_len = curr['high'] - curr['low']
        
        if total_len == 0: return []

        # PINBAR (Hammer/Shooting Star)
        if wick_bottom > (total_len * 0.6) and body_size < (total_len * 0.3):
            patterns.append("Bullish Pinbar (Hammer)")
        elif wick_top > (total_len * 0.6) and body_size < (total_len * 0.3):
            patterns.append("Bearish Pinbar (Shooting Star)")
            
        # ENGULFING
        if curr['close'] > curr['open'] and prev['close'] < prev['open']:
            if curr['close'] > prev['open'] and curr['open'] < prev['close']:
                patterns.append("Bullish Engulfing")
        
        if curr['close'] < curr['open'] and prev['close'] > prev['open']:
            if curr['close'] < prev['open'] and curr['open'] > prev['close']:
                patterns.append("Bearish Engulfing")

        return patterns

    def get_support_resistance(self) -> dict:
        """
        Identifies key Swing Highs and Lows from the last 60 candles.
        """
        if len(self.df) < 10: return {"support": [], "resistance": []}
        
        df = self.df.tail(60)
        highs = []
        lows = []
        tolerance = 0.001  # 0.1% tolerance for near-matches
        
        # Fractal with Tolerance: High > 2 left and 2 right (with tolerance)
        for i in range(2, len(df)-2):
            curr = df.iloc[i]
            curr_high = curr['high']
            curr_low = curr['low']
            
            # Swing High (with tolerance)
            if (df.iloc[i-1]['high'] < curr_high * (1 + tolerance) and 
                df.iloc[i-2]['high'] < curr_high * (1 + tolerance) and
                df.iloc[i+1]['high'] < curr_high * (1 + tolerance) and 
                df.iloc[i+2]['high'] < curr_high * (1 + tolerance)):
                highs.append(round(curr_high, 5))
                
            # Swing Low (with tolerance)
            if (df.iloc[i-1]['low'] > curr_low * (1 - tolerance) and 
                df.iloc[i-2]['low'] > curr_low * (1 - tolerance) and
                df.iloc[i+1]['low'] > curr_low * (1 - tolerance) and 
                df.iloc[i+2]['low'] > curr_low * (1 - tolerance)):
                lows.append(round(curr_low, 5))
        
        return {
            "support": list(set(lows)), # Unique
            "resistance": list(set(highs))
        }
        
    def get_price_action_score(self, d1_struct: str, h1_struct: str, dna_report: dict) -> dict:
        """
        Calculates a 'Pure Price Action' Score based on the Backtested Strategy.
        Win Rate Optimized Logic:
        1. Trend Alignment: H1 must not oppose D1.
        2. Wick Pressure: Ratio < 0.5 (Buy) or > 1.5 (Sell).
        3. Dominance: Candle Body Analysis.
        """
        score = 0
        bias = "WAIT"
        reason = "Indecisive Price Action"
        
        # 1. Trend Direction (D1 is King)
        # Note: We rely on passed D1 struct to avoid re-calculating inside M5 analyzer instance
        is_bullish_trend = "BULLISH" in d1_struct
        is_bearish_trend = "BEARISH" in d1_struct
        is_ranging = "RANGING" in d1_struct
        
        # 2. H1 Alignment Check
        h1_agrees_bull = "BEARISH" not in h1_struct
        h1_agrees_bear = "BULLISH" not in h1_struct
        
        # 3. DNA Extraction (M5 Data usually)
        if "error" in dna_report: return {"score": 0, "bias": "WAIT", "reason": "Insufficient Data"}
        
        # Defensive Check
        if "wick_pressure" not in dna_report or "power_analysis" not in dna_report:
             return {"score": 0, "bias": "WAIT", "reason": "Missing DNA Keys"}
        
        wick_pres = dna_report['wick_pressure']['pressure']
        wick_ratio = dna_report['wick_pressure']['ratio']
        dominance = dna_report['power_analysis']['dominance']
        bull_candles = dna_report['power_analysis']['bull_candles']
        bear_candles = dna_report['power_analysis']['bear_candles']
        range_pos = dna_report.get('range', {}).get('position_pct', 50) # 0-100
        
        # 4. Filter Logic (The "Selective" Strategy)
        
        # BULLISH SETUP (Trend Follow)
        if is_bullish_trend and h1_agrees_bull:
            # Setup A: Strong Wick Rejection (Dip Buying)
            if wick_pres == 'BULLISH' and wick_ratio < 0.6: # Relaxed slightly from 0.5
                score = 5
                bias = "BUY"
                reason = "Bullish Trend + Strong Wick Rejection (Demand Entering)"
                
            # Setup B: Flow Continuation (Momentum)
            elif dominance == 'BUYERS' and bull_candles >= 13: # Relaxed from 14
                score = 4
                bias = "BUY" 
                reason = "Bullish Trend + Strong Buyer Dominance (Flow Continuation)"

        # MEAN REVERSION SETUP (Ranging Market)
        elif is_ranging:
            # Setup C: Range Support Bounce (Buy Low)
            if range_pos < 20 and wick_pres == 'BULLISH':
                score = 4
                bias = "BUY"
                reason = "Range Support Bounce + Bullish Wicks"
            
            # Setup D: Range Resistance Rejection (Sell High)
            elif range_pos > 80 and wick_pres == 'BEARISH':
                score = 4
                bias = "SELL"
                reason = "Range Resistance Rejection + Bearish Wicks"
                
        # BEARISH SETUP (Trend Follow)
        elif is_bearish_trend and h1_agrees_bear:
            # Setup A: Strong Wick Rejection (Rally Selling)
            if wick_pres == 'BEARISH' and wick_ratio > 1.4: # Relaxed from 1.5
                score = 5
                bias = "SELL"
                reason = "Bearish Trend + Strong Wick Rejection (Supply Entering)"
                
            # Setup B: Flow Continuation
            elif dominance == 'SELLERS' and bear_candles >= 13: # Relaxed from 14
                 score = 4
                 bias = "SELL"
                 reason = "Bearish Trend + Strong Seller Dominance (Flow Continuation)"
                 
        return {
            "score": score,
            "bias": bias,
            "reason": reason
        }


# =============================================================================
# SYMBOL BEHAVIOR ANALYZER (Statistical DNA)
# =============================================================================

class SymbolBehaviorAnalyzer:
    """
    Generates a deep statistical report on symbol behavior.
    Provides 'Self-Awareness' to the agent about the asset's volatility, wicks, and streaks.
    Returns both structured data (for frontend) and text report (for AI prompt).
    """
    
    def analyze(self, candles: List[dict], symbol: str, timeframe: str) -> Dict:
        """
        Returns structured DNA data for frontend/JSON output.
        """
        if not candles or len(candles) < 24:
            return {"error": "Insufficient data", "timeframe": timeframe}
            
        df = pd.DataFrame(candles)
        cols = ['open', 'high', 'low', 'close', 'tick_volume', 'volume']
        for c in cols:
            if c in df.columns: 
                df[c] = pd.to_numeric(df[c], errors='coerce')
        
        curr = df.iloc[-1]
        close = float(curr['close'])
        
        lookback = min(24, len(df))
        subset = df.tail(lookback).copy()
        
        # Basic Stats
        subset['range'] = subset['high'] - subset['low']
        subset['body'] = (subset['close'] - subset['open']).abs()
        subset['upper_wick'] = subset['high'] - subset[['open', 'close']].max(axis=1)
        subset['lower_wick'] = subset[['open', 'close']].min(axis=1) - subset['low']
        subset['type'] = np.where(subset['close'] >= subset['open'], 'BULLISH', 'BEARISH')
        
        # Range Stats
        range_high = float(subset['high'].max())
        range_low = float(subset['low'].min())
        day_range = range_high - range_low
        position_in_range = round(((close - range_low) / day_range) * 100, 1) if day_range > 0 else 50
        
        # Bull/Bear Stats
        bulls = subset[subset['type'] == 'BULLISH']
        bears = subset[subset['type'] == 'BEARISH']
        
        bull_count = len(bulls)
        bear_count = len(bears)
        avg_bull_body = round(float(bulls['body'].mean()), 5) if not bulls.empty else 0
        avg_bear_body = round(float(bears['body'].mean()), 5) if not bears.empty else 0
        dominance = "BUYERS" if avg_bull_body > avg_bear_body else "SELLERS"
        
        # Wicks
        total_top_wick = float(subset['upper_wick'].sum())
        total_bot_wick = float(subset['lower_wick'].sum())
        wick_ratio = round(total_top_wick / total_bot_wick, 2) if total_bot_wick > 0 else 0
        wick_pressure = "BEARISH" if wick_ratio > 1 else "BULLISH"
        
        # Streaks
        subset['change_type'] = (subset['close'] > subset['open']).astype(int)
        streaks = (subset['change_type'] != subset['change_type'].shift()).cumsum()
        streak_counts = subset.groupby(streaks).size()
        max_streak = int(streak_counts.max()) if not streak_counts.empty else 0
        
        # Continuation vs Reversal
        subset['prev_type'] = subset['type'].shift(1)
        continuations = len(subset[subset['type'] == subset['prev_type']])
        reversals = len(subset[subset['type'] != subset['prev_type']])
        continuation_rate = round((continuations / lookback) * 100, 1) if lookback > 0 else 50
        
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "lookback_periods": lookback,
            "current_price": round(close, 5),
            "range": {
                "high": round(range_high, 5),
                "low": round(range_low, 5),
                "size": round(day_range, 5),
                "position_pct": position_in_range  # 0% = at low, 100% = at high
            },
            "power_analysis": {
                "bull_candles": bull_count,
                "bear_candles": bear_count,
                "avg_bull_body": avg_bull_body,
                "avg_bear_body": avg_bear_body,
                "dominance": dominance
            },
            "wick_pressure": {
                "top_wicks_total": round(total_top_wick, 5),
                "bottom_wicks_total": round(total_bot_wick, 5),
                "ratio": wick_ratio,
                "pressure": wick_pressure  # BULLISH = buyers absorbing, BEARISH = sellers rejecting
            },
            "momentum": {
                "max_streak": max_streak,
                "continuations": continuations,
                "reversals": reversals,
                "continuation_rate_pct": continuation_rate
            }
        }
    
    def generate_report(self, candles: List[dict], symbol: str, timeframe: str) -> str:
        """
        Returns text report for AI prompt consumption.
        """
        data = self.analyze(candles, symbol, timeframe)
        
        if "error" in data:
            return data["error"]
        
        r = []
        r.append(f"=== {symbol} BEHAVIOR DNA ({timeframe}) ===")
        r.append(f"Current Price: {data['current_price']}. Range: {data['range']['low']} - {data['range']['high']}.")
        r.append(f"Position in Range: {data['range']['position_pct']}% (0%=Low, 100%=High)")
        
        r.append(f"\n[POWER ANALYSIS]")
        pa = data['power_analysis']
        r.append(f"Bulls: {pa['bull_candles']} (Avg Body: {pa['avg_bull_body']})")
        r.append(f"Bears: {pa['bear_candles']} (Avg Body: {pa['avg_bear_body']})")
        r.append(f"Dominance: {pa['dominance']}")
        
        r.append(f"\n[WICK PRESSURE]")
        wp = data['wick_pressure']
        r.append(f"Top Wicks: {wp['top_wicks_total']} | Bottom Wicks: {wp['bottom_wicks_total']}")
        r.append(f"Ratio: {wp['ratio']} ({wp['pressure']} Pressure)")
        
        r.append(f"\n[MOMENTUM]")
        m = data['momentum']
        r.append(f"Max Streak: {m['max_streak']} | Continuation Rate: {m['continuation_rate_pct']}%")
        
        return "\n".join(r)
