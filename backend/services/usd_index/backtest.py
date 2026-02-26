import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
import logging
from .index_engine import USDIndexEngine

logger = logging.getLogger(__name__)

class Backtester:
    def __init__(self, engine: USDIndexEngine):
        self.engine = engine
        
    def fetch_benchmark(self, start_date: str) -> pd.Series:
        """
        Fetch DXY data from Yahoo Finance.
        DX-Y.NYB is the ticker for US Dollar Index.
        """
        logger.info("Fetching DXY benchmark data...")
        try:
            dxy = yf.download("DX-Y.NYB", start=start_date, progress=False)
            if dxy.empty:
                logger.warning("DXY fetch failed/empty.")
                return pd.Series()
            
            # Handle MultiIndex
            if isinstance(dxy.columns, pd.MultiIndex):
                if 'Adj Close' in dxy.columns.get_level_values(0):
                    return dxy.xs('Adj Close', level=0, axis=1).iloc[:, 0]
                elif 'Close' in dxy.columns.get_level_values(0):
                    return dxy.xs('Close', level=0, axis=1).iloc[:, 0]

            # Handle Single Index
            if 'Adj Close' in dxy.columns:
                return dxy['Adj Close']
            if 'Close' in dxy.columns:
                return dxy['Close']
            
            return dxy.iloc[:, 0]
        except Exception as e:
            logger.error(f"Failed to fetch DXY: {e}")
            return pd.Series()

    def run_backtest(self, forward_window: int = 5):
        """
        Compare Index vs Future DXY Returns.
        forward_window: Days to look ahead for predictive power.
        """
        # 1. Get Index History
        index_df = self.engine.run_pipeline()
        if index_df is None or index_df.empty:
            logger.error("Index generation failed.")
            return
            
        # 2. Get Benchmark
        start_date = index_df.index.min().strftime('%Y-%m-%d')
        dxy = self.fetch_benchmark(start_date)
        
        if dxy.empty:
            logger.error("No benchmark data.")
            return

        # Align
        common_idx = index_df.index.intersection(dxy.index)
        idx_aligned = index_df.loc[common_idx]['composite_index']
        dxy_aligned = dxy.loc[common_idx]
        
        # 3. Calculate Forward Returns of DXY
        # Return(t) = (Price(t+k) / Price(t)) - 1
        dxy_fwd_ret = dxy_aligned.shift(-forward_window) / dxy_aligned - 1
        
        # 4. Correlation Analysis
        corr = idx_aligned.corr(dxy_fwd_ret)
        logger.info(f"Correlation (Index vs {forward_window}d Fwd DXY): {corr:.4f}")
        
        # 5. Signal Performance (Simple Strategy)
        # Buy if Index > 1, Sell if Index < -1
        signals = index_df.loc[common_idx]['signal_val']
        
        # Strategy Return = Signal * FwdReturn
        # (Assuming we enter at Close(t) and exit at Close(t+k))
        # Note: 'signals' is aligned with 'dxy_fwd_ret' at time t.
        strat_ret = signals * dxy_fwd_ret
        
        # Cumulative Return
        cum_ret = (1 + strat_ret.fillna(0)).cumprod()
        
        # Metrics
        hit_rate = (np.sign(signals) == np.sign(dxy_fwd_ret)).mean()
        
        return {
            "correlation": corr,
            "hit_rate": hit_rate,
            "cumulative_return": cum_ret.iloc[-1] if not cum_ret.empty else 0
        }

if __name__ == "__main__":
    eng = USDIndexEngine()
    bt = Backtester(eng)
    res = bt.run_backtest()
    print("Backtest Results:", res)
