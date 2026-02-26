import os
import sys
import zipfile
import requests
import io
import datetime
import logging
from pathlib import Path

# Add macro lens to path
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("CFTC_Downloader")

def download_and_extract():
    current_year = datetime.datetime.now().year
    data_dir = os.path.join(Path(__file__).resolve().parent.parent, "data", "cot")
    os.makedirs(data_dir, exist_ok=True)
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    # 1. Download Legacy (Commercials)
    # URL format: https://www.cftc.gov/files/dea/history/deacot2024.zip
    legacy_url = f"https://www.cftc.gov/files/dea/history/deacot{current_year}.zip"
    logger.info(f"Downloading Legacy COT Data: {legacy_url}")
    
    try:
        response = requests.get(legacy_url, headers=headers)
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            # Usually the file inside is named annual.txt
            file_names = z.namelist()
            if "annual.txt" in file_names:
                content = z.read("annual.txt")
                target_path = os.path.join(data_dir, f"deacot_{current_year}.txt")
                with open(target_path, "wb") as f:
                    f.write(content)
                logger.info(f"Successfully extracted to: {target_path}")
            else:
                logger.warning(f"Could not find 'annual.txt' in legacy zip. Found: {file_names}")
    except Exception as e:
        logger.error(f"Failed to download/extract Legacy Data: {e}")

    # 2. Download Financial (Hedge Funds)
    # URL format: https://www.cftc.gov/files/dea/history/fut_fin_txt_2024.zip
    financial_url = f"https://www.cftc.gov/files/dea/history/fut_fin_txt_{current_year}.zip"
    logger.info(f"Downloading Financial COT Data: {financial_url}")
    
    try:
        response = requests.get(financial_url, headers=headers)
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            # Usually the file inside is named FinFutYY.txt
            file_names = z.namelist()
            target_file = next((f for f in file_names if f.lower().endswith('.txt')), None)
            
            if target_file:
                content = z.read(target_file)
                target_path = os.path.join(data_dir, f"financial_{current_year}.txt")
                with open(target_path, "wb") as f:
                    f.write(content)
                logger.info(f"Successfully extracted '{target_file}' to: {target_path}")
            else:
                logger.warning(f"Could not find any '.txt' file in financial zip. Found: {file_names}")
    except Exception as e:
        logger.error(f"Failed to download/extract Financial Data: {e}")

    # 3. Update Cache automatically
    logger.info("Triggering COT Cache Update...")
    from backend.scripts.update_cot import update_cache
    update_cache()

if __name__ == "__main__":
    download_and_extract()
    print("\n[SUCCESS] CFTC Data downloaded and JSON cache updated. You do not need to restart the servers.")
