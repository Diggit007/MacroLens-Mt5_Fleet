import os
import time
import pandas as pd
from fredapi import Fred
from datetime import datetime, timedelta
import logging
from pathlib import Path
import yaml
from dotenv import load_dotenv

# Setup basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FredDataFetcher:
    def __init__(self, api_key: str = None, cache_dir: str = ".fred_cache"):
        """
        Initialize the FRED Data Fetcher.
        
        Args:
            api_key: FRED API Key. If None, tries to load from env var FRED_API_KEY.
            cache_dir: Directory to store cached CSV files.
        """
        # Load .env from project backend root if not already loaded
        # Attempt to find backend/.env relative to this file
        try:
            env_path = Path(__file__).resolve().parents[2] / '.env'
            if env_path.exists():
                load_dotenv(env_path)
            else:
                 # Try one level up (if running from backend root context differently)
                 # Or try standard location c:\MacroLens\backend\.env
                 alt_path = Path("c:/MacroLens/backend/.env")
                 if alt_path.exists():
                     load_dotenv(alt_path)
        except Exception:
            pass
            
        self.api_key = api_key or os.environ.get("FRED_API_KEY")
        if not self.api_key:
            logger.warning("FRED_API_KEY not found. Data fetching will fail unless key is provided.")
        
        self.fred = Fred(api_key=self.api_key) if self.api_key else None
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch_series(self, series_id: str, start_date: str = None, end_date: str = None, force_refresh: bool = False) -> pd.Series:
        """
        Fetch a FRED series, using cache if available and fresh.
        
        Args:
            series_id: FRED Series ID (e.g., 'DGS10')
            start_date: Start date string 'YYYY-MM-DD'
            end_date: End date string 'YYYY-MM-DD'
            force_refresh: If True, ignore cache and re-fetch.
            
        Returns:
            pd.Series with DatetimeIndex
        """
        cache_path = self.cache_dir / f"{series_id}.csv"
        
        # Check cache
        if not force_refresh and cache_path.exists():
            # Check freshness - let's say cache is valid for 24 hours
            mtime = datetime.fromtimestamp(cache_path.stat().st_mtime)
            if datetime.now() - mtime < timedelta(hours=24):
                logger.info(f"Loading {series_id} from cache.")
                df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
                # Ensure it's a Series
                if not df.empty:
                     data = df.squeeze()
                     # If we have data, filter by date range if provided
                     if start_date:
                         data = data[data.index >= start_date]
                     if end_date:
                         data = data[data.index <= end_date]
                     return data

        # If we are here, we need to fetch from API
        if not self.fred:
            raise ValueError("FRED API Key is missing.")

        logger.info(f"Fetching {series_id} from FRED API...")
        try:
            # We fetch as much history as possible/reasonable to populate cache
            # But respecting start_date if it's very old? 
            # Actually, standard practice: fetch from specific start_date or default (e.g. 1950)
            fetch_start = start_date if start_date else "1980-01-01"
            
            data = self.fred.get_series(series_id, observation_start=fetch_start, observation_end=end_date)
            
            if data is None or data.empty:
                logger.warning(f"No data returned for {series_id}")
                return pd.Series(dtype=float)

            # Save to cache (full fetch)
            # We overwrite the cache with the new fetch
            data.to_csv(cache_path)
            
            return data
            
        except Exception as e:
            logger.error(f"Error fetching {series_id}: {e}")
            # If fetch fails, try to fallback to cache even if stale?
            if cache_path.exists():
                logger.warning(f"Fallback to stale cache for {series_id}")
                df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
                return df.squeeze()
            raise

    def fetch_multiple(self, series_ids: list, start_date: str = None) -> pd.DataFrame:
        """
        Fetch multiple series and align them on a common date index (outer join).
        Returns a DataFrame.
        """
        results = {}
        for sid in series_ids:
            try:
                s = self.fetch_series(sid, start_date=start_date)
                results[sid] = s
            except Exception as e:
                logger.error(f"Failed to fetch {sid}: {e}")
        
        if not results:
            return pd.DataFrame()
        
        # Combine into DataFrame
        df = pd.DataFrame(results)
        return df

if __name__ == "__main__":
    # Simple test
    # Ensure you have FRED_API_KEY set in env or passed here
    fetcher = FredDataFetcher()
    try:
        # Test with 10Y Treasury
        s = fetcher.fetch_series("DGS10", start_date="2020-01-01")
        print(f"Fetched DGS10: {len(s)} rows")
        print(s.tail())
    except Exception as e:
        print(f"Test failed: {e}")
