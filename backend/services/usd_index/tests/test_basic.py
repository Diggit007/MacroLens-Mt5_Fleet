import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np
from datetime import datetime
import sys
import os
from unittest.mock import MagicMock, patch

# Mock fredapi before importing modules that use it
sys.modules['fredapi'] = MagicMock()

# Add path to allow imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Verify path (debugging)
print(f"Project root added to path: {project_root}")

from backend.services.usd_index.preprocessing import align_to_business_days
from backend.services.usd_index.features import robust_zscore, normalize_features
from backend.services.usd_index.index_engine import USDIndexEngine
from backend.services.usd_index.aggregation import IndexAggregator

class TestUSDIndex(unittest.TestCase):
    
    def test_align_to_business_days(self):
        # Create a df with weekends and missing days
        idx = pd.to_datetime(['2023-01-01', '2023-01-02', '2023-01-04']) # Sun, Mon, Wed
        df = pd.DataFrame({'val': [1, 2, 3]}, index=idx)
        
        aligned = align_to_business_days(df)
        
        # 2023-01-02 is Mon (B), 2023-01-03 is Tue (B), 2023-01-04 is Wed (B)
        # 2023-01-01 is Sun, should be ignored or mapped to next business day if resample 'B' starts there?
        # Pandas resample('B') usually aligns to business days.
        
        self.assertTrue(pd.to_datetime('2023-01-03') in aligned.index)
        self.assertEqual(aligned.loc['2023-01-03', 'val'], 2.0) # Forward filled from Jan 2

    def test_robust_zscore(self):
        # Create series with outlier
        data = [10, 10, 10, 10, 100]
        s = pd.Series(data)
        z = robust_zscore(s, window=5)
        
        # Median is 10. MAD is 0 (median of abs diffs: 0,0,0,0,90 -> 0).
        # If MAD is 0, zscore handles div by zero -> 0.
        # Let's try data with variance
        data2 = [10, 12, 11, 9, 100] # Median 11. MAD approx 1.
        s2 = pd.Series(data2)
        z2 = robust_zscore(s2, window=5)
        
        self.assertNotEqual(z2.iloc[-1], 0)
        # Last point 100 should be high Z
        self.assertTrue(z2.iloc[-1] > 3)

    def test_aggregator(self):
        agg = IndexAggregator({})
        
        df = pd.DataFrame({
            'A': [1.0, 2.0],
            'B': [-1.0, -2.0]
        }, index=[0, 1])
        
        weights = {'A': 1.0, 'B': 1.0}
        res = agg.aggregate(df, weights)
        
        # Mean of 1 and -1 is 0.
        # Mean of 2 and -2 is 0.
        # Wait, I implemented weighted sum where weights are normalized.
        # w_series = weights / sum(weights) -> 0.5, 0.5
        # 0.5*1 + 0.5*(-1) = 0
        
        self.assertEqual(res['composite_index'].iloc[0], 0.0)

    @patch('backend.services.usd_index.data_fetcher.FredDataFetcher.fetch_multiple')
    def test_engine_pipeline(self, mock_fetch):
        # Mock fetching data
        idx = pd.date_range('2023-01-01', periods=10, freq='B')
        mock_df = pd.DataFrame({
            'T10Y2Y': np.random.randn(10),
            'DGS2': np.random.randn(10),
            # Add other required keys from config or ensure engine handles missing
            # Config has: T10Y2Y, DGS2, BAMLC0A0CM, T10YIE, NFCI, VIXCLS
            'BAMLC0A0CM': np.random.randn(10),
            'T10YIE': np.random.randn(10),
            'NFCI': np.random.randn(10),
            'VIXCLS': np.random.randn(10)
        }, index=idx)
        
        mock_fetch.return_value = mock_df
        
        # Mock Config Loading?
        # We can just rely on the real default config file existing
        
        engine = USDIndexEngine()
        res_df = engine.run_pipeline()
        
        self.assertIsNotNone(res_df)
        self.assertFalse(res_df.empty)
        self.assertIn('composite_index', res_df.columns)
        self.assertIn('signal_label', res_df.columns)

if __name__ == '__main__':
    unittest.main()
