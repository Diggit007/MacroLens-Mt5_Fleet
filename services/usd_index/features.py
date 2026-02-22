import pandas as pd
import numpy as np
from scipy import stats
import logging

logger = logging.getLogger(__name__)

def robust_zscore(series: pd.Series, window: int = 126) -> pd.Series:
    """
    Calculate Rolling Robust Z-Score.
    
    Z = (X - Median) / (k * MAD)
    k = 1.4826 (consistency constant for normal distribution)
    """
    rolling = series.rolling(window=window, min_periods=window//2)
    median = rolling.median()
    
    # MAD = Median Absolute Deviation
    # Panda's rolling doesn't have a direct 'mad' method in older versions, 
    # or it behaves differently. 
    # We can use apply, but it's slow.
    # Faster robust approx: rolling quantile spread? 
    # Let's stick to median/std if window is large, but requirements say MAD.
    # Optimization: median is fast. MAD is (X - median).abs().median()
    
    # Implementing rolling MAD efficiently is tricky without full loop or apply.
    # For production speed, we can trust 'apply' on rolling for now, 
    # as window=126 isn't huge and typical history is ~3000 pts.
    
    def mad_func(x):
        return np.median(np.abs(x - np.median(x)))
    
    mad = rolling.apply(mad_func, raw=True)
    
    k = 1.4826
    z = (series - median) / (k * mad)
    
    # Handle division by zero (if MAD is 0) -> unlikely for financial time series unless pegged
    z = z.replace([np.inf, -np.inf], 0)
    
    return z

def calculate_rate_of_change(series: pd.Series, periods: list = [1, 5, 21]) -> pd.DataFrame:
    """
    Calculate % change over N periods.
    """
    res = pd.DataFrame(index=series.index)
    for p in periods:
        res[f"roc_{p}d"] = series.pct_change(p)
    return res

def optimize_lag(feature_series: pd.Series, target_driver: pd.Series, max_lag: int = 20) -> int:
    """
    Find the lag L (0 to max_lag) that maximizes the correlation 
    between feature_series(t) and target_driver(t + L).
    
    i.e., does the feature predict the future target?
    """
    best_lag = 0
    best_corr = 0
    
    # Align common index
    common_idx = feature_series.index.intersection(target_driver.index)
    if len(common_idx) < 100:
        return 0
        
    s = feature_series.loc[common_idx]
    t = target_driver.loc[common_idx]
    
    for l in range(1, max_lag + 1):
        # Shift target BACK by L to align (t+L) with t
        # Or shift feature FORWARD?
        # We want corr(Feature_t, Target_{t+L})
        # So we align Feature_t with Target_{t+L}.
        
        shifted_target = t.shift(-l) # Future target value at current time? No.
        # t.shift(-l) at time T is value at T+L. Correct.
        
        corr = s.corr(shifted_target)
        if abs(corr) > abs(best_corr):
            best_corr = corr
            best_lag = l
            
    return best_lag

def normalize_features(df_features: pd.DataFrame, clip: float = 4.0) -> pd.DataFrame:
    """
    Clips extreme Z-scores to +/- clip.
    """
    return df_features.clip(-clip, clip)
