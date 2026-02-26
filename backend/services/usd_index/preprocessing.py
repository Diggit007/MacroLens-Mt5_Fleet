import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

def align_to_business_days(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aligns a DataFrame (with DatetimeIndex) to strict Business Day frequency ('B').
    
    1. Resamples to 'B'.
    2. Forward fills missing values (obs carried forward).
    3. Backfills limited initial gaps (optional, small).
    """
    if df.empty:
        return df
        
    # Ensure index is sorted
    df = df.sort_index()

    # Create a business day range covering the full data period
    start_date = df.index.min()
    end_date = df.index.max()
    
    # We use resample('B') which handles the frequency conversion
    # 'last' takes the last observation in the bin (if multiple) or NaN if empty
    resampled = df.resample('B').last()
    
    # Forward fill to propagate last known values
    # Limit ffill to avoid carrying very stale data forever? 
    # For now, let's assume we want continuous series.
    filled = resampled.ffill()
    
    # Check for remaining NaNs (at the start)
    if filled.isna().any().any():
        # Backfill a bit for reasonable starting gaps?
        filled = filled.bfill(limit=5) 
        
    return filled

def check_data_quality(df: pd.DataFrame, threshold_nan: float = 0.2):
    """
    Warns if any column has > threshold_nan percentage of missing values.
    """
    missing_pct = df.isna().mean()
    for col, pct in missing_pct.items():
        if pct > threshold_nan:
            logger.warning(f"Series {col} has {pct:.1%} missing values.")
