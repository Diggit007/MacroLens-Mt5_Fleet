import pandas as pd
import yaml
import logging
from pathlib import Path
from datetime import datetime, timedelta

from .data_fetcher import FredDataFetcher
from .preprocessing import align_to_business_days
from .features import robust_zscore, optimize_lag, normalize_features
from .aggregation import IndexAggregator

logger = logging.getLogger(__name__)

class USDIndexEngine:
    def __init__(self, config_path: str = "backend/services/usd_index/config.yaml"):
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        self.fetcher = FredDataFetcher(cache_dir=self.config['api'].get('cache_dir', '.fred_cache'))
        self.aggregator = IndexAggregator(self.config)
        self.series_config = {s['id']: s for s in self.config['series']}
        
        # Cache for the calculated index
        self._index_cache = None
        self._last_run = None

    def run_pipeline(self, force_refresh: bool = False):
        """
        Executes the full pipeline: Fetch -> Preprocess -> FeatureEng -> Aggregate.
        """
        # Simple in-memory cache check (1 hour)
        if not force_refresh and self._index_cache is not None and self._last_run:
            if datetime.now() - self._last_run < timedelta(hours=1):
                return self._index_cache

        logger.info("Starting USD Index Pipeline...")
        
        # 1. Fetch Data
        lookback = self.config['index_settings'].get('lookback_days', 3650)
        start_date = (datetime.now() - timedelta(days=lookback)).strftime('%Y-%m-%d')
        
        series_ids = [s['id'] for s in self.config['series']]
        raw_df = self.fetcher.fetch_multiple(series_ids, start_date=start_date)
        
        if raw_df.empty:
            logger.error("No data fetched.")
            return None

        # 2. Preprocessing
        aligned_df = align_to_business_days(raw_df)
        
        # 3. Feature Engineering
        feature_df = pd.DataFrame(index=aligned_df.index)
        zscore_window = self.config['index_settings'].get('zscore_window', 126)
        
        for sid in series_ids:
            s_conf = self.series_config[sid]
            series_data = aligned_df[sid]
            
            # Application of transformation
            trans = s_conf.get('transformation', 'level')
            if trans == 'diff':
                series_data = series_data.diff()
            elif trans == 'roc':
                series_data = series_data.pct_change()
            
            # Robust Z-Score
            z = robust_zscore(series_data, window=zscore_window)
            
            # Apply Direction
            z_directed = z * s_conf.get('direction', 1)
            
            # Clip outliers
            z_clipped = normalize_features(z_directed)
            
            feature_df[sid] = z_clipped

        # 4. Aggregation
        # Get weights
        weights = {s['id']: s.get('weight', 1.0) for s in self.config['series']}
        
        composite_df = self.aggregator.aggregate(feature_df, weights)
        
        # Generate Signal
        if not composite_df.empty:
            composite_df['signal_val'] = self.aggregator.generate_signal(composite_df['composite_index'])
            composite_df['signal_label'] = composite_df['signal_val'].apply(self.aggregator.get_signal_label)
            
            # Merge component z-scores for inspection?
            # Let's keep them separate or metadata
            self._component_data = feature_df # Store latest features

        self._index_cache = composite_df
        self._last_run = datetime.now()
        
        return composite_df

    def get_latest(self):
        df = self.run_pipeline()
        if df is None or df.empty:
            return None
            
        latest = df.iloc[-1]
        
        # Get component contributions for the latest date
        latest_date = df.index[-1]
        components = self._component_data.loc[latest_date].to_dict() if hasattr(self, '_component_data') else {}
        
        return {
            "timestamp": latest_date.isoformat(),
            "composite_index": float(latest['composite_index']),
            "signal": latest['signal_label'],
            "signal_value": int(latest['signal_val']),
            "components": components
        }

    def get_history(self):
        df = self.run_pipeline()
        if df is None: return []
        
        # Reset index to get date column
        out = df.reset_index()
        out['timestamp'] = out['index'].apply(lambda x: x.isoformat())
        return out[['timestamp', 'composite_index', 'signal_val', 'signal_label']].to_dict(orient='records')
