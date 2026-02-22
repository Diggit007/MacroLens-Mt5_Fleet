import logging
import json
import httpx
from typing import Dict, List, Optional, Literal
from pydantic import BaseModel, Field, ValidationError
from backend.config import settings

# Lazy import to avoid circular dependency
_event_predictor = None

def get_event_predictor():
    """Lazy load EventPredictor to avoid import cycles."""
    global _event_predictor
    if _event_predictor is None:
        try:
            from backend.services.event_predictor import EventPredictor
            _event_predictor = EventPredictor()
        except Exception as e:
            logger.warning(f"Could not load EventPredictor: {e}")
    return _event_predictor

_usd_engine = None

def get_usd_engine():
    """Lazy load USDIndexEngine."""
    global _usd_engine
    if _usd_engine is None:
        try:
            from backend.services.usd_index.index_engine import USDIndexEngine
            _usd_engine = USDIndexEngine()
        except Exception as e:
            logger.warning(f"Could not load USDIndexEngine: {e}")
    return _usd_engine

_cot_engine = None

def get_cot_engine():
    """Lazy load COTEngine."""
    global _cot_engine
    if _cot_engine is None:
        try:
            from backend.services.cot.engine import COTEngine
            _cot_engine = COTEngine()
        except Exception as e:
            logger.warning(f"Could not load COTEngine: {e}")
    return _cot_engine

logger = logging.getLogger("AIEngine")

# =============================================================================
# PYDANTIC MODELS (Validation)
# =============================================================================

class TradingSignal(BaseModel):
    """Validated LLM response structure"""
    symbol: str
    direction: Literal["BUY", "SELL", "WAIT"]
    entry: Optional[float] = None
    entry_zone: Optional[str] = None # New Field: Price range for entry (e.g. "108.80 - 109.00")
    tp_suggested: Optional[float] = None
    sl_suggested: Optional[float] = None
    confidence: int = Field(ge=0, le=100)
    checklist_score: Optional[str] = None
    order_type: Optional[Literal["MARKET", "LIMIT", "STOP"]] = "MARKET" # New Field
    summary: Optional[str] = None  # New: One-sentence technical setup summary
    reasons: Optional[List[str]] = None  # New: 4-5 detailed bullet points
    base_analysis: Optional[str] = None # Fundamental analysis for BASE currency
    quote_analysis: Optional[str] = None # Fundamental analysis for QUOTE currency
    macro_thesis: Optional[str] = None # New: Synthesis of Base vs Quote divergence
    economic_analysis: Optional[str] = None # Event Analysis
    economic_influence: Optional[str] = None # NEW: Causal Influence Statement



class AIEngine:
    """
    Handles the 'Brain' of the operation: Prompt Construction and LLM Interaction.
    """
    def __init__(self, api_key: str, http_client: httpx.AsyncClient):
        self.api_key = api_key
        self.http_client = http_client

    def analyze_event_math(self, ev: Dict) -> Dict:
        """
        Performs Quantitative Analysis on Event Data.
        Calculates Consensus Momentum and Surprise Deviation.
        """
        try:
            # Safely extract and convert
            def safe_float(v):
                if v is None or v == "": return None
                return float(str(v).replace('e3', '000').replace('e6', '000000'))

            prev = safe_float(ev.get('previous_value'))
            forc = safe_float(ev.get('forecast_value'))
            act = safe_float(ev.get('actual_value'))
            
            # Defaults for calc
            p_val = prev if prev is not None else 0.0
            f_val = forc if forc is not None else p_val # Assume flat if no forecast
            
            # 1. Momentum (Consensus Bias: Forecast vs Previous)
            momentum = f_val - p_val
            
            score = 0 # Initialize score
            
            # 2. Surprise (Actual vs Forecast)
            surprise = 0.0
            surprise_pct = 0.0
            
            if act is not None:
                surprise = act - f_val
                # Avoid division by zero or tiny numbers causing massive %
                denom = abs(f_val) if abs(f_val) > 0.0001 else 1.0
                surprise_pct = (surprise / denom) * 100
                
            # 3. Z-Score (The "True Shock" Value)
            z_score_str = "N/A"
            z_score_val = 0.0
            hist_sigma = ev.get('hist_std_dev')
            
            if hist_sigma and hist_sigma > 0 and act is not None:
                # Z = (Actual - Forecast) / Historical_StdDev
                z_score_val = (act - f_val) / hist_sigma
                z_score_str = f"{z_score_val:.2f} sigma"
                
                # Boost Score if Sigma is high (True Shock)
                if abs(z_score_val) > 1.5:
                    score += 2 if z_score_val > 0 else -2
            
            # 4. Streak & Reliability
            streak = ev.get('streak', 0)
            reliability = 0.0
            if hist_sigma:
                # Simple Reliability: Lower StdDev = Higher Reliability (Inverse)
                # Cap at 1.0 for perfect, decay for high volatility
                reliability = 1.0 / (1.0 + hist_sigma) * 100 # percentage-ish
            
            if abs(streak) >= 3:
                score += 1 if streak > 0 else -1

            return {
                "momentum": momentum,
                "surprise_val": surprise,
                "surprise_pct_str": f"{surprise_pct:.1f}%" if act is not None else "N/A",
                "z_score_str": z_score_str,
                "streak_str": f"{streak} in a row ({'Beat' if streak>0 else 'Miss'})" if streak != 0 else "None",
                "reliability_str": f"{reliability:.0f}% (Based on Hist Vol)" if hist_sigma else "Unknown",
                "qt_score": score,
                "bias": "BULLISH (+)" if score > 0 else "BEARISH (-)" if score < 0 else "NEUTRAL"
            }
        except Exception as e:
            return {"error": str(e)}

    def get_cot_context(self, symbol: str) -> str:
        """Fetches and formats COT Institutional Positioning data."""
        try:
            engine = get_cot_engine()
            if not engine: return "COT Data Unavailable."
            
            data = engine.get_latest_sentiment(symbol)
            if not data: return "COT Data Unavailable for this asset."
            
            # Interpret Willco (Smart Money)
            willco = data['willco_index']
            sentiment_desc = "Neutral"
            if willco > 80: sentiment_desc = "Extreme Long (Overcrowded?)"
            elif willco < 20: sentiment_desc = "Extreme Short (Overcrowded?)"
            elif willco > 60: sentiment_desc = "Bullish"
            elif willco < 40: sentiment_desc = "Bearish"
            
            # Hedge Fund Context
            hedge_willco = data.get('hedge_willco', 0)
            hedge_desc = "Neutral"
            if hedge_willco > 80: hedge_desc = "Crowded Long"
            elif hedge_willco < 20: hedge_desc = "Crowded Short"
            
            # Strategy (User Defined):
            # Hedge Funds (Leveraged) -> News/Momentum chasers. They drive retracements.
            # Commercials (Smart Money) -> Value/Structural. 
            
            return f"""
[INSTITUTIONAL POSITIONING (COT REPORT)]
> STRUCTURAL FLOW (Commercials/Smart Money):
  - Net: {data['smart_net']} Contracts
  - Willco (52wk): {willco:.1f}/100 ({sentiment_desc})
  - Role: Defines the Structural Trend / Value Reversals.
  
> MOMENTUM FLOW (Hedge Funds/Leveraged):
  - Net: {data.get('hedge_net', 0)} Contracts
  - Willco (52wk): {hedge_willco:.1f}/100 ({hedge_desc})
  - Role: News Reaction & Retracements. (buy=good news / sell=bad news).

> IMPLICATION: 
  1. If Hedge Funds are Extreme ({hedge_desc}) -> Expect Immediate Retracement.
  2. If Commercials are Extreme ({sentiment_desc}) -> Expect Structural Reversal.
  3. Compare Flows: Are Hedge Funds chasing news against the Structural Trend?
"""
        except Exception as e:
            logger.error(f"Error fetching COT: {e}")
            return "Error fetching COT data."

    def get_usd_macro_context(self) -> str:
        """Fetches and formats the latest USD Index data."""
        try:
            engine = get_usd_engine()
            if not engine: 
                return "USD Index Engine Unavailable."
            
            data = engine.get_latest()
            if not data: 
                return "USD Index Data Unavailable."
            
            # Format Component Drivers
            drivers = []
            components = data.get('components', {})
            for k, v in components.items():
                if abs(v) > 1.5: 
                    drivers.append(f"{k}: {v:.2f} (Huge)")
                elif abs(v) > 0.8:
                    drivers.append(f"{k}: {v:.2f} (Strong)")
            
            driver_str = ", ".join(drivers) if drivers else "None significant"
            
            # Pre-format values to avoid f-string complexity issues
            signal_upper = data['signal'].upper()
            score_val = f"{data['composite_index']:.2f}"
            signal_raw = data['signal']
            
            return f"""
[USD MACRO CYCLE]
> Composite Signal: {signal_upper} (Score: {score_val})
> Key Drivers: {driver_str}
> IMPLICATION: {signal_raw} USD.
"""
        except Exception as e:
            logger.error(f"Error fetching USD Index: {e}")
            return "Error fetching USD Index."

    def construct_prompt(self, symbol: str, multi_tf_data: Dict, calendar: List, 
                          behavior_report: str, institutional_bias: str, 
                          retail_sentiment: str, risk_params: Dict, 
                          confluence_score: Optional[Dict] = None,
                          price_action_score: Optional[Dict] = None) -> str:
        """
        Builds the enhanced prompt with strict checklist and data injection.
        """
        base_currency = symbol[:3]
        quote_currency = symbol[3:]
        
        current_price = multi_tf_data.get('M5', {}).get('price', 0)
        
        # Build Trend Table
        trend_table = "Timeframe | Structure | RSI(14) | Patterns | Zones (H1)\n"
        trend_table += "--- | --- | --- | --- | ---\n"
        
        for tf_name in ["D1", "H4", "H1", "M15", "M5"]:
            data = multi_tf_data.get(tf_name, {})
            structure = data.get('structure', 'N/A')
            rsi = data.get('rsi', 0)
            patterns = ", ".join(data.get('patterns', []))
            patterns = patterns if patterns else "None"
            
            zones = data.get('zones', {})
            supp = str(zones.get('support', [])[:1]) if zones.get('support') else "None"
            res = str(zones.get('resistance', [])[-1]) if zones.get('resistance') else "None"
            trend_table += f"{tf_name} | {structure} | {rsi} | {patterns} | S:{supp} R:{res}\n"

        # [NEW] Market Regime Context (Recommendation #4)
        d1_struct = multi_tf_data.get('D1', {}).get('structure', 'Ranges')
        h1_struct = multi_tf_data.get('H1', {}).get('structure', 'Ranges')
        
        regime_instructions = ""
        if "BULLISH" in d1_struct and "BULLISH" in h1_struct:
             regime_instructions = """
# MARKET REGIME: STRONG UPTREND (Trend Following)
- Prioritize PULLBACKS to EMA/Fib levels.
- IGNORE Counter-trend signals unless Score is 5/5.
- Breakouts are VALID entries.
"""
        elif "BEARISH" in d1_struct and "BEARISH" in h1_struct:
             regime_instructions = """
# MARKET REGIME: STRONG DOWNTREND (Trend Following)
- Prioritize PULLBACKS to EMA/Fib levels.
- IGNORE Counter-trend signals unless Score is 5/5.
- Breakdowns are VALID entries.
"""
        else:
             regime_instructions = """
# MARKET REGIME: RANGING / MIXED (Mean Reversion)
- Prioritize REVERSALS at Value Extremes (Bollinger Bands, S/R).
- IGNORE Breakouts until confirmed by a retest.
- Scalp targets are preferred over swing targets.
"""

        risk_block = f"""
# PRE-CALCULATED RISK MATH (Use these exactly)
ATR (Volatility): {risk_params.get('atr_val', 0.001)}
IF BUY -> SL: {risk_params.get('buy_sl')} | TP: {risk_params.get('buy_tp')}
IF SELL -> SL: {risk_params.get('sell_sl')} | TP: {risk_params.get('sell_tp')}
"""

        # Python Pre-Analysis Block (Confluence)
        pre_analysis_block = ""
        if confluence_score:
            c_score = confluence_score.get('score', 0)
            c_bias = confluence_score.get('bias', 'NEUTRAL')
            details = ", ".join(confluence_score.get('details', []))
            pre_analysis_block += f"""
# PYTHON CONFLUENCE ANALYSIS (Legacy)
Score: {c_score}/5 ({c_bias})
Details: {details}
"""

        # Python Price Action Block (The New Strategy)
        pa_block = ""
        pa_instructions = ""
        if price_action_score:
            pa_score = price_action_score.get('score', 0)
            pa_bias = price_action_score.get('bias', 'WAIT')
            pa_reason = price_action_score.get('reason', '')
            
            pa_block = f"""
# PRICE ACTION ENGINE (Backtested Strategy)
SCORE: {pa_score}/5
BIAS: {pa_bias}
REASON: {pa_reason}
"""
            if pa_score >= 4:
                pa_instructions = f"""
!!! PRICE ACTION OVERRIDE ACTIVE !!!
The Price Action Engine has detected a High Probability Setup ({pa_bias}).
You MUST prioritize this signal over RSI or Sentiment.
IGNORE missing Sentiment data.
IGNORE lagging indicators if they conflict.
Follow the Direction: {pa_bias}.
"""

        prompt = f"""# Role
    You are a Senior Quantitative Analyst. You trade based on PRICE ACTION CONFLUENCE.
    
# Market Context
Symbol: {symbol}
Current Price: {current_price}

# Multi-Timeframe Matrix
{trend_table}

{pre_analysis_block}
{pa_block}
{pa_instructions}
{regime_instructions}

# SYMBOL BEHAVIOR DNA (Self-Awareness)
{behavior_report}

# INSTITUTIONAL INTELLIGENCE (Fundamentals)
{institutional_bias}

# USD MACRO INTEGRATION
{self.get_usd_macro_context()}

# RETAIL SENTIMENT (Contrarian)
{retail_sentiment}

# Economic Calendar (QUANTITATIVE ANALYSIS)
"""
        if calendar:
            # Get predictor for enhanced analysis
            predictor = get_event_predictor()
            
            for ev in calendar:
                math_data = self.analyze_event_math(ev)
                
                # Enhanced: Add prediction context if predictor available
                prediction_block = ""
                if predictor and ev.get('forecast_value') is not None:
                    try:
                        # Generate prediction
                        pred = predictor.predict_event(
                            event_name=ev['event_name'],
                            forecast=float(ev.get('forecast_value', 0)),
                            previous=float(ev.get('previous_value', 0)),
                            currency=ev.get('currency', 'USD')
                        )
                        
                        # Generate playbook for the symbol
                        playbook = predictor.generate_playbook(
                            ev['event_name'], symbol, ev.get('currency', 'USD')
                        )
                        
                        prediction_block = f"""
[HISTORICAL PATTERN ANALYSIS]
> Sample Size: {pred.historical_sample} past releases
> Historical Beat Rate: {pred.probability:.0%}
> Predicted Outcome: {pred.predicted_outcome} (Confidence: {pred.confidence})
> Currency Direction: {pred.expected_direction}
> Bias Score: {pred.bias_score:+d}

[SCENARIO PLAYBOOK for {symbol}]"""
                        for scenario, details in playbook["scenarios"].items():
                            prediction_block += f"""
> If {scenario} ({details['probability']}): {details['action']}"""
                            
                        prediction_block += f"""

[RECOMMENDATION]: {pred.recommendation}
"""
                    except Exception as e:
                        logger.warning(f"Prediction failed for {ev['event_name']}: {e}")
                        prediction_block = "\n[PREDICTION]: Insufficient historical data\n"
                
                prompt += f"""
Event: {ev['event_name']} ({ev['event_time']}) | Impact: {ev['impact_level']}
"""
                if math_data:
                    bias_val = f"{math_data.get('momentum', 0):.2f}"
                    surprise_val = f"{math_data.get('surprise_pct', 0):.2f}"
                    z_val = f"{math_data.get('z_score', 0):.2f}"
                    
                    prompt += f"""
# IMPACT ANALYSIS (Probability Weighting)
> Consensus Bias: {bias_val} (Forecast vs Prev)
> Surprise Factor: {surprise_val}% (Actual vs Forecast)
> Historical Z-Score: {z_val} (Standard Deviations)
> Surprise Streak: {math_data.get('streak_str', 'N/A')}
> Analyst Reliability: {math_data.get('reliability_str', 'N/A')}
> QUANT SIGNAL: {math_data.get('bias', 'Neutral')} (Score: {math_data.get('qt_score', 0)})
{prediction_block}
"""
        else:
            prompt += "No Significant Economic Events (High/Medium Impact) in near term.\n"


        prompt += f"""

{risk_block}

# TRADING CHECKLIST (Updated for Price Action)
## BULLISH SETUP (BUY)
1. Price Action Score >= 4? (Primary Driver)
2. D1/H4 Trend is Bullish?
3. Valid Wick Rejection or Strong Buyer Flow?
4. RSI < 70 (Not Extreme Top)?
5. Sentiment is Net Short (Bonus)?

## BEARISH SETUP (SELL)
1. Price Action Score >= 4? (Primary Driver)
2. D1/H4 Trend is Bearish?
3. Valid Wick Rejection or Strong Seller Flow?
4. RSI > 30 (Not Extreme Bottom)?

"""
        prompt += f"""# INSTRUCTIONS
1. **Analyze Confluence**: Combine Technicals + Fundamentals + Sentiment.
2. **Review Checklist**: Walk through the checklist above.
3. **Signal Logic**:
4. **Output Requirements**:
    - **summary**: ONE concise sentence explaining the overall setup (mention key structure, levels, and bias)
    - **reasons**: Array of 4-5 SPECIFIC bullet points explaining WHY this trade makes sense.
    - **base_analysis** & **quote_analysis**: MUST include at least 3 distinct bullet points (use symbols like -, *, or •) covering Core Drivers, Central Bank Stance, and recent Economic Data.
    - **macro_thesis**: A specific paragraph explaining the DIVERGENCE between Base and Quote that justifies the trade. (e.g., "AUD is bullish due to hawkish RBA, while JPY is weak due to dovish BoJ, creating a strong divergence for AUDJPY Buy").
      * Include at least 1 technical reason (structure, patterns, levels)
      * Include at least 1 sentiment/positioning reason
      * **NEWS PREDICTION**: If news is imminent, cited the 'Quant Signal' and 'Surprise Streak' as justification.
      * Be SPECIFIC with numbers, levels, and technical terms

# NEWS PREDICTION PROTOCOL (Institutional Level)
If High Impact Events are listed in the Calendar ([QUANT LAB]):
1. **CHECK THE STREAK**: If Streak is >= 3 (Beat/Miss), Assume the market is Wrong/Biased and the Trend continues.
2. **CHECK CONSENSUS**: If Momentum is High but Streak is Opposite, Bet on the Reversion.
3. **PREDICT DIRECTION**: Use these statistics to form a directional bias (BUY/SELL) pre-event. 
4. **DO NOT OUTPUT 'WAIT'** solely because of news. Only 'WAIT' if the Quant Data is conflicting/Netural. Your job is to FORECAST the volatility.

If only Medium Impact Events are listed:
1. Use them as "Supporting Context" (e.g., "Medium impact PMI data aligns with bullish thesis").
2. Do NOT treat them as market-moving catalysts unless they deviate significantly (Z-Score > 2).

# MACRO ALIGNMENT RULE
- If [USD MACRO CYCLE] is **STRONG BUY/BUY**:
  - Favor **SHORT** EURUSD, GBPUSD, AUDUSD, NZDUSD.
  - Favor **LONG** USDJPY, USDCAD, USDCHF.
- If [USD MACRO CYCLE] is **STRONG SELL/SELL**:
  - Favor **LONG** EURUSD, GBPUSD, AUDUSD, NZDUSD.
  - Favor **SHORT** USDJPY, USDCAD, USDCHF.
- If Signal contradicts Macro, **REDUCE CONFIDENCE** or **WAIT** unless Price Action is score 5/5.


# JSON OUTPUT SCHEMA
{{
  "symbol": "str",
  "direction": "BUY|SELL|WAIT",
  "entry": float,
  "entry_zone": "str (A specific price range spanning the entry price, e.g. '1.0900 - 1.0920')",
  "tp_suggested": float,
  "sl_suggested": float,
  "confidence": "int (0-100)",
  "order_type": "MARKET|LIMIT|STOP",
  "reasons": ["str", "str", "str", "str", "str"],
  "summary": "str (ONE sentence - technical setup summary mentioning structure, key levels, bias)",
  "base_analysis": "str (MUST be a bulleted list string. Use newline + dash separator: '\\n- Point 1\\n- Point 2'. Cover drivers for {base_currency})",
  "quote_analysis": "str (MUST be a bulleted list string. Use newline + dash separator: '\\n- Point 1\\n- Point 2'. Cover drivers for {quote_currency})",
  "economic_analysis": "str (event prediction or 'None')",
  "economic_influence": "str (Casual statement: HOW the economic data above influenced your Buy/Sell/Wait decision)"
}}
"""
        return prompt

    def generate_mock_signal(self, symbol: str, current_price: float, atr: float) -> Dict:
        """Generates a synthetic signal for testing downstream systems."""
        import random
        
        # Decide direction randomly (40% Buy, 40% Sell, 20% Wait)
        rand = random.random()
        direction = "WAIT"
        if rand < 0.4: direction = "BUY"
        elif rand < 0.8: direction = "SELL"
        
        confidence = random.randint(70, 95)
        
        # Calculate Mock Levels
        entry = current_price
        sl = 0.0
        tp = 0.0
        
        if direction == "BUY":
            sl = entry - (1.5 * atr)
            tp = entry + (2.5 * atr)
        elif direction == "SELL":
            sl = entry + (1.5 * atr)
            tp = entry - (2.5 * atr)
            
        base_currency = symbol[:3]
        quote_currency = symbol[3:]

        return {
            "symbol": symbol,
            "direction": direction,
            "entry": round(entry, 5),
            "entry_zone": f"{round(entry - (0.1 * atr), 5)} - {round(entry + (0.1 * atr), 5)}",
            "tp_suggested": round(tp, 5),
            "sl_suggested": round(sl, 5),
            "confidence": confidence,
            "checklist_score": "4/5",
            "summary": f"[SIMULATION] Mock {direction} signal generated. Technicals align with bullish flow.",
            "base_analysis": f"{base_currency} showing strength due to hawkish central bank rhetoric.",
            "quote_analysis": f"{quote_currency} weakness observed amidst cooling inflation data.",
            "economic_analysis": "Upcoming GDP release expected to create short-term volatility.",
            "economic_influence": "High probability GDP beat supports local currency strength, aligning with technical breakout.",
            "reasons": [
                "Mock Technical: Price bounced off key support zone.",
                "Mock Sentiment: Retail is net short (counter-trend).",
                "Mock Pattern: Bullish Engulfing pattern detected.",
                f"Mock Fundamental: {base_currency} vs {quote_currency} divergence."
            ],
            "fundamental_reason": "Simulation Mode Active",
            "sentiment_reason": "Simulation Mode Active"
        }

    def get_provider_config(self, model_name: str) -> Dict:
        """
        Returns the API Configuration (Base URL, API Key, Model ID) based on the model name.
        Supported providers: DeepSeek, GLM 4.7 (Zhipu/Z.ai), Kimi K2.5 (NVIDIA NIM).
        """
        # 1. NVIDIA (Kimi K2.5)
        if "kimi" in model_name or "moonshot" in model_name:
            return {
                "base_url": "https://integrate.api.nvidia.com/v1/chat/completions",
                "api_key": settings.NVIDIA_API_KEY.get_secret_value() if settings.NVIDIA_API_KEY else "",
                "model_id": "moonshotai/kimi-k2.5",
                "provider": "nvidia"
            }
        
        # 2. GLM 4.7 (Zhipu AI / Z.ai)
        elif "glm" in model_name:
            return {
                "base_url": "https://api.z.ai/api/paas/v4/chat/completions",
                "api_key": settings.GLM_API_KEY.get_secret_value() if settings.GLM_API_KEY else "",
                "model_id": "glm-4.7",
                "provider": "glm"
            }
        
        # 3. DeepSeek (Default)
        else:
            return {
                "base_url": "https://api.deepseek.com/chat/completions",
                "api_key": settings.DEEPSEEK_API_KEY.get_secret_value() if settings.DEEPSEEK_API_KEY else "",
                "model_id": "deepseek-chat",
                "provider": "deepseek"
            }

    async def get_trading_signal(self, prompt: str, symbol: str, current_price: float, atr: float, model_override: Optional[str] = None) -> Dict:
        """
        Sends prompt to LLM, parses JSON, checks for hallucinations (Risk Params), and returns Validated Signal.
        Supports Multi-Provider Switching.
        """
        # MOCK MODE CHECK
        if settings.USE_MOCK_AI:
             logger.warning(f"Using MOCK AI Signal (Simulation Mode) for {symbol}")
             return self.generate_mock_signal(symbol, current_price, atr)

        try:
            # Determine Model & Provider
            target_model = model_override or settings.LLM_MODEL
            config = self.get_provider_config(target_model)
            
            logger.info(f"Calling {config['provider'].upper()} API ({config['model_id']}) for {symbol}...")
            
            if not config['api_key']:
                 logger.error(f"Missing API Key for {config['provider']}")
                 return {"status": "error", "message": f"Missing API Key for {config['provider']}"}

            payload = {
                "model": config['model_id'],
                "messages": [
                    {"role": "system", "content": "You are a JSON-only trading engine. Output pure JSON."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.2
            }

            # Provider-specific payload adjustments
            if config['provider'] == "deepseek":
                payload["response_format"] = {"type": "json_object"}
                payload["max_tokens"] = 4096
            elif config['provider'] == "nvidia":
                payload["max_tokens"] = 16384
                payload["chat_template_kwargs"] = {"thinking": True}
            elif config['provider'] == "glm":
                payload["max_tokens"] = 4096
                # GLM 4.7 supports JSON mode via prompt engineering
            else:
                payload["max_tokens"] = 4096

            resp = await self.http_client.post(
                config['base_url'],
                headers={
                    "Authorization": f"Bearer {config['api_key']}",
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                },
                json=payload,
                timeout=120.0
            )
            
            if resp.status_code != 200:
                logger.error(f"{config['provider']} API Error ({resp.status_code}): {resp.text}")
                return {"status": "error", "message": f"{config['provider']} API Error: {resp.status_code}"}

            data = resp.json()
            content = data['choices'][0]['message']['content']
            
            # Clean Markdown wrappers if present
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                 content = content.split("```")[1].split("```")[0]
            
            # PYDANTIC VALIDATION
            try:
                signal = TradingSignal.model_validate_json(content.strip())
                ai_data = signal.model_dump()
            except ValidationError as e:
                logger.error(f"LLM returned invalid JSON structure: {e}")
                logger.debug(f"Raw Content: {content}")
                return {
                    "symbol": symbol,
                    "direction": "WAIT",
                    "reason": f"Validation Error: {str(e)}",
                    "confidence": 0
                }
            
            # Validation (ATR Check & Direction Correction)
            bias = ai_data.get("direction", "WAIT").upper()
            
            # Safe float conversion with defaults
            entry_val = ai_data.get("entry")
            entry = float(entry_val) if entry_val is not None else current_price
            
            # Reset entry if too far (Hallucination check)
            if abs(entry - current_price) > (current_price * 0.01):
                entry = current_price
                ai_data["reason"] = ai_data.get("reason", "") + " [Entry Reset to Market]"

            sl_val = ai_data.get("sl_suggested")
            tp_val = ai_data.get("tp_suggested")
            sl = float(sl_val) if sl_val is not None else 0
            tp = float(tp_val) if tp_val is not None else 0

            # Guard Rails: Ensure SL/TP Respect Direction
            if bias == "BUY":
                if sl >= entry: 
                    sl = entry - (1.5 * atr)
                    ai_data["reason"] = ai_data.get("reason", "") + " [Auto-Fixed SL]"
                if tp <= entry: 
                    tp = entry + (2.5 * atr)
                    ai_data["reason"] = ai_data.get("reason", "") + " [Auto-Fixed TP]"
            elif bias == "SELL":
                if sl <= entry: 
                    sl = entry + (1.5 * atr)
                    ai_data["reason"] = ai_data.get("reason", "") + " [Auto-Fixed SL]"
                if tp >= entry: 
                    tp = entry - (2.5 * atr)
                    ai_data["reason"] = ai_data.get("reason", "") + " [Auto-Fixed TP]"
            
            ai_data['sl_suggested'] = round(sl, 5)
            ai_data['tp_suggested'] = round(tp, 5)
            ai_data['entry'] = round(entry, 5)
            
            return ai_data
            
        except Exception as e:
            logger.error(f"LLM Error: {e}")
            return {"status": "error", "message": str(e)}

    async def get_completion(self, user_prompt: str, system_prompt: str = "You are a helpful assistant.", max_tokens: int = 1000, temperature: float = 0.7) -> str:
        """
        Generic Chat Completion (Text-Only).
        Used by the Agent Router and General Chat.
        """
        try:
            # Use default model settings
            target_model = settings.LLM_MODEL
            config = self.get_provider_config(target_model)
            
            if not config['api_key']:
                 return "Error: Missing API Key."

            payload = {
                "model": config['model_id'],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": temperature,
                "max_tokens": max_tokens
            }
            
            # Provider adjustments
            if config['provider'] == "nvidia":
                payload["chat_template_kwargs"] = {"thinking": True} # Enable thought chain if supported
            
            resp = await self.http_client.post(
                config['base_url'],
                headers={
                    "Authorization": f"Bearer {config['api_key']}",
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                },
                json=payload,
                timeout=60.0
            )
            
            if resp.status_code != 200:
                logger.error(f"Chat Completion Error ({resp.status_code}): {resp.text}")
                return f"Error: Provider returned {resp.status_code}"

            data = resp.json()
            content = data['choices'][0]['message']['content']
            return content

        except Exception as e:
            logger.error(f"Chat Completion Failed: {e}")
            return f"Error generate response: {str(e)}"
