
import pandas as pd
import requests
import zipfile
import io
import os
import datetime
import logging

logger = logging.getLogger("COT_Fetcher")

# CFTC URLs (Financial Futures - "FinFut" and Legacy)
# Using historical ZIPs for backfill, and current text files for latest.
# TFF (Financial Futures) - Key for Hedge Funds
# The correct URL pattern for Financial Futures history is 'fin2026.zip', not 'financial2026.zip'
URL_TFF_HISTORY = "https://www.cftc.gov/files/dea/history/fin{year}.zip"
URL_LEGACY_HISTORY = "https://www.cftc.gov/files/dea/history/deacot{year}.zip"

# Mapping for COT Report columns (Ref: CFTC)
# We need to map raw columns to our standard "Long", "Short", "Oi"
# This requires careful mapping based on the report type.

class COTFetcher:
    def __init__(self, storage_dir="backend/data/cot"):
        self.storage_dir = storage_dir
        os.makedirs(self.storage_dir, exist_ok=True)
        
    def fetch_year(self, year: int, report_type="financial"):
        """
        Fetches zip file for a specific year and extracts it.
        report_type: 'financial' (TFF) or 'deacot' (Legacy)
        """
        # Determine URL based on type
        if report_type == "financial":
            url = f"https://www.cftc.gov/files/dea/history/fin{year}.zip"
        else:
            url = f"https://www.cftc.gov/files/dea/history/deacot{year}.zip"
            
        logger.info(f"Fetching {report_type} for {year}: {url}")
        
        try:
            r = requests.get(url)
            r.raise_for_status()
            
            z = zipfile.ZipFile(io.BytesIO(r.content))
            
            # Extract and rename
            files_found = False
            for file_info in z.infolist():
                logger.info(f"ZIP CONTENT ({report_type} {year}): {file_info.filename}")
                if file_info.filename.lower().endswith('.txt'):
                    target_path = os.path.join(self.storage_dir, f"{report_type}_{year}.txt")
                    with z.open(file_info) as source, open(target_path, "wb") as target:
                        target.write(source.read())
                    logger.info(f"Saved {report_type} {year} to {target_path}")
                    files_found = True
            
            if not files_found:
                 logger.warning(f"No .txt file found in {report_type} {year} zip!")

            return True
        except Exception as e:
            logger.error(f"Failed to fetch {year} {report_type}: {e}")
            return False

    def load_latest(self):
        """
        Loads and parses the downloaded data into a cleaned DataFrame.
        """
        # TODO: Implement loading logic for specific files
        pass

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    fetcher = COTFetcher()
    today = datetime.datetime.now()
    years = [today.year, today.year - 1, today.year - 2]
    
    for year in years:
        fetcher.fetch_year(year, "financial")
        fetcher.fetch_year(year, "deacot")
