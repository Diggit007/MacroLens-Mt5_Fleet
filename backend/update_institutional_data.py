import asyncio
import json
import logging
import sys
from pathlib import Path

# Allow standalone execution
sys.path.append(str(Path(__file__).parent.parent))

from backend.scrapers.trading_economics_scraper import TradingEconomicsScraper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("InstitutionalUpdater")

async def update_cache():
    """
    Runs the Trading Economics scraper and saves data to institutional_data.json
    """
    scraper = TradingEconomicsScraper()
    
    logger.info("🚀 Starting Institutional Data Update...")
    
    # 1. Fetch Matrix (Global Overview)
    data = await scraper.fetch_macro_matrix()
    logger.info(f"✅ Fetched Matrix for {len(data)} currencies.")
    
    # 2. Fetch Details (PMI, Core CPI)
    for ccy, profile in data.items():
        country_name = scraper.CURRENCY_TO_COUNTRY.get(ccy)
        if country_name:
            logger.info(f"   Enriching {ccy} ({country_name})...")
            try:
                details = await scraper.fetch_details(ccy, country_name)
                profile.update(details)
            except Exception as e:
                logger.error(f"Failed to enrich {ccy}: {e}")
                
    # 3. Save to JSON Cache
    output_path = "C:/MacroLens/backend/institutional_data.json"
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        logger.info(f"💾 Saved institutional data to {output_path}")
    except Exception as e:
        logger.error(f"❌ Failed to save cache: {e}")

if __name__ == "__main__":
    asyncio.run(update_cache())
