from pydantic import BaseModel
from typing import Optional

class CountryHealth(BaseModel):
    currency: str
    
    # Core Economic Indicators
    inflation_rate: Optional[float] = None  # CPI YoY
    unemployment_rate: Optional[float] = None
    gdp_growth: Optional[float] = None      # GDP YoY or QoQ
    
    # Phase 8: Trading Economics Data
    pmi_manufacturing: Optional[float] = None
    pmi_services: Optional[float] = None
    core_inflation: Optional[float] = None
    govt_debt_gdp: Optional[float] = None
    current_account_gdp: Optional[float] = None
    interest_rate: Optional[float] = None   # Central Bank Rate
    trade_balance: Optional[float] = None   # New: Trade Balance
    
    # Momentum (3-Month Slope)
    cpi_momentum: Optional[float] = None    # > 0 = Accelerating Inflation
    unemployment_momentum: Optional[float] = None # > 0 = Cooling Labor Market
    gdp_momentum: Optional[float] = None    # > 0 = Accelerating Growth
    
    # Derived
    real_rate: Optional[float] = None       # interest_rate - inflation_rate
    
    # COT / Institutional Positioning
    cot_willco: Optional[float] = None      # Willco Index (0-100)
    cot_bias: str = "NEUTRAL"               # BULLISH, BEARISH, EXTREME_LONG, EXTREME_SHORT, NEUTRAL
    cot_smart_net: Optional[float] = None   # Commercials net contracts
    cot_hedge_willco: Optional[float] = None # Hedge fund Willco
    
    # Sentiment / Policy
    central_bank_sentiment: float = 0.0     # -5 (Dovish) to +5 (Hawkish)
    policy_bias: str = "NEUTRAL"            # HAWKISH, DOVISH, NEUTRAL
    
    # Factor Sub-Scores (0-10 each, before weighting)
    growth_score: float = 5.0
    inflation_score: float = 5.0
    monetary_score: float = 5.0
    real_rate_score: float = 5.0
    cot_score: float = 5.0
    
    # Calculated Composite Score (0-10)
    health_score: float = 0.0
    
    # Metadata
    last_updated: str = ""
