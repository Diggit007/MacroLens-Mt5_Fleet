import os
import sys
import json
import logging
from pathlib import Path

# Add macro lens to path
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

from backend.services.cot.engine import COTEngine, CFTC_NAMES

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Update_COT_Cache")

class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        import numpy as np
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super(NpEncoder, self).default(obj)

def update_cache():
    logger.info("Initializing COTEngine and loading raw Pandas DataFrames...")
    engine = COTEngine()
    
    # Load raw text/csv files into Pandas
    engine.load_data()
    
    cache = {}
    logger.info("Computing Willco and Sentiment metrics for all symbols...")
    
    CROSS_PAIRS = [
        "AUDCAD", "AUDCHF", "AUDJPY", "AUDNZD", "CADCHF", "CADJPY",
        "EURAUD", "EURCAD", "EURCHF", "EURGBP", "EURJPY", "EURNZD",
        "GBPAUD", "GBPCAD", "GBPCHF", "GBPJPY", "GBPNZD", "NZDCAD",
        "NZDCHF", "NZDJPY"
    ]
    
    all_symbols = list(CFTC_NAMES.keys()) + CROSS_PAIRS
    
    for symbol in all_symbols:
        try:
            res = engine._compute_latest_sentiment(symbol)
            if res:
                cache[symbol] = res
                logger.info(f"[{symbol}] Computed successfully.")
        except Exception as e:
            logger.error(f"Failed to compute COT for {symbol}: {e}")
    
    logger.info(f"Saving {len(cache)} symbols to {engine.cache_file}...")
    
    # Ensure directory exists
    os.makedirs(os.path.dirname(engine.cache_file), exist_ok=True)
    
    with open(engine.cache_file, "w") as f:
        json.dump(cache, f, indent=4, cls=NpEncoder)
        
    logger.info("COT Cache Update Complete!")

if __name__ == "__main__":
    update_cache()
