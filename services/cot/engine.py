
import pandas as pd
import logging
import os

logger = logging.getLogger("COT_Engine")

# Mapping of Symbol to CFTC Contract Names
# Note: These names must match EXACTLY what is in the CFTC files.
# We will need to verify them from the loaded data.
CFTC_NAMES = {
    "EURUSD": ["EURO FX", "EURO FX - CHICAGO MERCANTILE EXCHANGE"],
    "GBPUSD": ["BRITISH POUND STERLING", "BRITISH POUND - CHICAGO MERCANTILE EXCHANGE"],
    "USDJPY": ["JAPANESE YEN", "JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE"],
    "AUDUSD": ["AUSTRALIAN DOLLAR", "AUSTRALIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE"],
    "NZDUSD": ["NZ DOLLAR", "NZ DOLLAR - CHICAGO MERCANTILE EXCHANGE"],
    "USDCAD": ["CANADIAN DOLLAR", "CANADIAN DOLLAR - CHICAGO MERCANTILE EXCHANGE"],
    "USDCHF": ["SWISS FRANC", "SWISS FRANC - CHICAGO MERCANTILE EXCHANGE"],
    "DXY": ["U.S. DOLLAR INDEX", "USD INDEX", "U.S. DOLLAR INDEX - ICE FUTURES U.S."],
    "GOLD": ["GOLD", "GOLD - COMMODITY EXCHANGE INC."],
    "SILVER": ["SILVER", "SILVER - COMMODITY EXCHANGE INC."],
    "OIL": ["CRUDE OIL, LIGHT SWEET", "CRUDE OIL, LIGHT SWEET - NEW YORK MERCANTILE EXCHANGE"],
    "SPX": ["E-MINI S&P 500", "E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE"]
}

class COTEngine:
    def __init__(self, data_dir="backend/data/cot", cache_file="backend/data/cot/latest_cot_cache.json"):
        self.data_dir = data_dir
        self.cache_file = cache_file
        self.legacy_df = None
        self.tff_df = None
        self._cache = None

    def load_cache(self):
        import json
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r") as f:
                    self._cache = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load COT cache: {e}")
                self._cache = {}
        else:
            self._cache = {}

    def get_latest_sentiment(self, symbol: str):
        """Returns sentiment from the pre-calculated JSON cache."""
        if self._cache is None:
            self.load_cache()
        return self._cache.get(symbol)

        
    def load_data(self):
        """Loads and concatenates available yearly data (2024-Current) for Legacy and TFF."""
        # 1. Load Legacy (Commercials)
        legacy_frames = []
        tff_frames = []
        today = pd.Timestamp.now().year
        years = [today, today - 1, today - 2]
        
        for year in years:
            # Legacy
            legacy_path = os.path.join(self.data_dir, f"deacot_{year}.txt")
            if os.path.exists(legacy_path):
                try:
                    df = pd.read_csv(legacy_path, low_memory=False)
                    df.columns = [c.strip() for c in df.columns]
                    df['Date'] = pd.to_datetime(df['As of Date in Form YYYY-MM-DD'])
                    legacy_frames.append(df)
                    logger.info(f"Loaded Legacy COT {year}: {df.shape}")
                except Exception as e:
                    logger.error(f"Error loading Legacy {year}: {e}")

            # TFF (Financial Futures - Hedge Funds)
            tff_path = os.path.join(self.data_dir, f"financial_{year}.txt")
            if os.path.exists(tff_path):
                try:
                    df = pd.read_csv(tff_path, low_memory=False)
                    df.columns = [c.strip() for c in df.columns]
                    # TFF files often use underscores instead of spaces
                    if 'Report_Date_as_YYYY-MM-DD' in df.columns:
                        df['Date'] = pd.to_datetime(df['Report_Date_as_YYYY-MM-DD'])
                    else:
                        df['Date'] = pd.to_datetime(df['As of Date in Form YYYY-MM-DD'])
                    tff_frames.append(df)
                    logger.info(f"Loaded TFF COT {year}: {df.shape}")
                except Exception as e:
                    logger.error(f"Error loading TFF {year}: {e}")
        
        if legacy_frames:
            self.legacy_df = pd.concat(legacy_frames).sort_values('Date').reset_index(drop=True)
        else:
            self.legacy_df = pd.DataFrame() # Prevent reload loop

        if tff_frames:
            self.tff_df = pd.concat(tff_frames).sort_values('Date').reset_index(drop=True)
        else:
            self.tff_df = pd.DataFrame() # Prevent reload loop

    def calculate_willco(self, series: pd.Series, window=52):
        """Calculates Willco Index (Percentile of current value in last 52 weeks)."""
        if len(series) < window: return 0 
        current = series.iloc[-1]
        recent = series.iloc[-window:]
        low = recent.min()
        high = recent.max()
        return 50.0 if high == low else ((current - low) / (high - low)) * 100

    def _compute_latest_sentiment(self, symbol: str):
        """
        Computes sentiment (Hedge Funds & Commercials) from Pandas DataFrames.
        """
        if self.legacy_df is None or self.tff_df is None:
            self.load_data()
            
        names = CFTC_NAMES.get(symbol, [])
        if not names:
            # Synthetic Cross-Pair Calculation
            if len(symbol) == 6:
                base = symbol[:3]
                quote = symbol[3:]
                ISO_TO_MAJOR = {
                    'EUR': 'EURUSD', 'GBP': 'GBPUSD', 'JPY': 'USDJPY', 
                    'AUD': 'AUDUSD', 'NZD': 'NZDUSD', 'CAD': 'USDCAD', 
                    'CHF': 'USDCHF', 'USD': 'DXY'
                }
                if base in ISO_TO_MAJOR and quote in ISO_TO_MAJOR:
                    base_sent = self._compute_latest_sentiment(ISO_TO_MAJOR[base])
                    quote_sent = self._compute_latest_sentiment(ISO_TO_MAJOR[quote])
                    if base_sent and quote_sent:
                        # Blend the indices: For Willco, 100 on Base is bullish, 0 on Quote is bullish.
                        # Normalizing to a 0-100 Willco scale for the cross-pair.
                        willco_blend = (base_sent.get('willco_index', 50) + (100 - quote_sent.get('willco_index', 50))) / 2
                        hedge_willco_blend = (base_sent.get('hedge_willco', 50) + (100 - quote_sent.get('hedge_willco', 50))) / 2
                        
                        # For synthetic cross pairs like AUDCAD:
                        # Base% + (100 - Quote%) / 2 -> essentially averaging the Base bullishness and the Quote bearishness
                        smart_long_blend = (base_sent.get('smart_long_pct', 50) + (100 - quote_sent.get('smart_long_pct', 50))) / 2
                        smart_short_blend = (base_sent.get('smart_short_pct', 50) + (100 - quote_sent.get('smart_short_pct', 50))) / 2
                        hedge_long_blend = (base_sent.get('hedge_long_pct', 50) + (100 - quote_sent.get('hedge_long_pct', 50))) / 2
                        hedge_short_blend = (base_sent.get('hedge_short_pct', 50) + (100 - quote_sent.get('hedge_short_pct', 50))) / 2

                        return {
                            "symbol": symbol,
                            "date": base_sent.get('date') or quote_sent.get('date'),
                            "smart_sentiment": base_sent.get('smart_sentiment', 0) - quote_sent.get('smart_sentiment', 0),
                            "smart_net": base_sent.get('smart_net', 0) - quote_sent.get('smart_net', 0),
                            "smart_long_pct": smart_long_blend,
                            "smart_short_pct": smart_short_blend,
                            "willco_index": willco_blend,
                            "oi": (base_sent.get('oi', 0) + quote_sent.get('oi', 0)) / 2,
                            "hedge_net": base_sent.get('hedge_net', 0) - quote_sent.get('hedge_net', 0),
                            "hedge_sentiment": base_sent.get('hedge_sentiment', 0) - quote_sent.get('hedge_sentiment', 0),
                            "hedge_long_pct": hedge_long_blend,
                            "hedge_short_pct": hedge_short_blend,
                            "hedge_willco": hedge_willco_blend
                        }
            return None
        
        result = {
            "symbol": symbol,
            "date": None,
            "smart_sentiment": 0, "smart_net": 0, "smart_long_pct": 50, "smart_short_pct": 50, "willco_index": 0, "oi": 0,
            "hedge_net": 0, "hedge_sentiment": 0, "hedge_long_pct": 50, "hedge_short_pct": 50, "hedge_willco": 0
        }
        
        # 1. Process Legacy (Commercials)
        if self.legacy_df is not None and not self.legacy_df.empty:
             col_name = "Market and Exchange Names"
             if col_name in self.legacy_df.columns:
                 df = self.legacy_df[self.legacy_df[col_name].apply(lambda x: any(n in str(x) for n in names))].copy()
                 if not df.empty:
                     df = df.sort_values(by="Date")
                     latest = df.iloc[-1]
                     result['date'] = latest['Date'].strftime('%Y-%m-%d')
                     result['oi'] = latest['Open Interest (All)']
                     
                     # Commercials
                     comm_long = df['Commercial Positions-Long (All)']
                     comm_short = df['Commercial Positions-Short (All)']
                     comm_net = comm_long - comm_short
                     result['smart_net'] = comm_net.iloc[-1]
                     
                     # Calculate % Long vs Short of total directional commercial positions
                     total_comm = comm_long.iloc[-1] + comm_short.iloc[-1]
                     result['smart_long_pct'] = (comm_long.iloc[-1] / total_comm * 100) if total_comm > 0 else 50
                     result['smart_short_pct'] = (comm_short.iloc[-1] / total_comm * 100) if total_comm > 0 else 50

                     result['smart_sentiment'] = (result['smart_net'] / result['oi']) * 100 if result['oi'] else 0
                     result['willco_index'] = self.calculate_willco(comm_net)

        # 2. Process TFF (Hedge Funds / Leveraged Funds)
        if self.tff_df is not None and not self.tff_df.empty:
             col_name = "Market_and_Exchange_Names" if "Market_and_Exchange_Names" in self.tff_df.columns else "Market and Exchange Names"
             if col_name in self.tff_df.columns:
                 df = self.tff_df[self.tff_df[col_name].apply(lambda x: any(n in str(x) for n in names))].copy()
             if not df.empty:
                 df = df.sort_values(by="Date")
                 # Leveraged Funds columns (check both formats)
                 try:
                     lev_long_col = 'Leveraged_Funds_Long_All' if 'Leveraged_Funds_Long_All' in df.columns else ('Traders_Lev_Money_Long_All' if 'Traders_Lev_Money_Long_All' in df.columns else 'Leveraged Funds-Long (All)')
                     lev_short_col = 'Leveraged_Funds_Short_All' if 'Leveraged_Funds_Short_All' in df.columns else ('Traders_Lev_Money_Short_All' if 'Traders_Lev_Money_Short_All' in df.columns else 'Leveraged Funds-Short (All)')
                     oi_col = 'Open_Interest_All' if 'Open_Interest_All' in df.columns else 'Open Interest (All)'

                     # Ensure numeric parsing by stripping commas and coercing invalid strings
                     lev_long = pd.to_numeric(df[lev_long_col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                     lev_short = pd.to_numeric(df[lev_short_col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                     tff_oi_series = pd.to_numeric(df[oi_col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                     
                     lev_net = lev_long - lev_short
                     result['hedge_net'] = lev_net.iloc[-1]
                     
                     total_lev = lev_long.iloc[-1] + lev_short.iloc[-1]
                     result['hedge_long_pct'] = (lev_long.iloc[-1] / total_lev * 100) if total_lev > 0 else 50
                     result['hedge_short_pct'] = (lev_short.iloc[-1] / total_lev * 100) if total_lev > 0 else 50

                     # Use TFF OI for Hedge Sentiment if available, else Legacy OI
                     tff_oi = tff_oi_series.iloc[-1]
                     result['hedge_sentiment'] = (result['hedge_net'] / tff_oi) * 100 if tff_oi else 0
                     result['hedge_willco'] = self.calculate_willco(lev_net)
                 except KeyError as e:
                     logger.warning(f"Leveraged Funds columns not found for {symbol}: {e}")

        return result

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    engine = COTEngine()
    engine.load_data()
    print(engine._compute_latest_sentiment("EURUSD"))
    print(engine._compute_latest_sentiment("DXY"))
