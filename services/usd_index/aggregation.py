import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

class IndexAggregator:
    def __init__(self, config):
        self.config = config

    def aggregate(self, feature_df: pd.DataFrame, weights: dict) -> pd.DataFrame:
        """
        Aggregate normalized features into a composite index.
        
        Args:
            feature_df: DataFrame where columns are series IDs (already normalized/z-scored/direction-adjusted)
            weights: Dictionary {series_id: weight}
        """
        # Ensure weights sum to 1 (or just normalize them here)
        w_series = pd.Series(weights)
        w_series = w_series / w_series.sum()
        
        # Align weights with columns
        # Filter columns that are present in weights
        common_cols = [c for c in feature_df.columns if c in w_series.index]
        
        if not common_cols:
            logger.error("No overlap between feature columns and configured weights.")
            return pd.DataFrame()
            
        weighted_sum = feature_df[common_cols].dot(w_series[common_cols])
        
        return weighted_sum.to_frame(name="composite_index")

    def generate_signal(self, index_series: pd.Series, threshold_buy: float = 1.0, threshold_sell: float = -1.0) -> pd.Series:
        """
        Generate Buy (1), Sell (-1), Neutral (0) signal based on thresholds.
        """
        signal = pd.Series(0, index=index_series.index, name="signal")
        signal[index_series > threshold_buy] = 1
        signal[index_series < threshold_sell] = -1
        return signal

    def get_signal_label(self, val):
        if val == 1: return "Buy"
        if val == -1: return "Sell"
        return "Neutral"
