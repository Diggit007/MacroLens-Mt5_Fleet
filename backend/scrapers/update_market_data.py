import asyncio
import logging
import sys
from pathlib import Path
from dotenv import load_dotenv

# Setup paths
BASE_DIR = Path(__file__).resolve().parent.parent # backend/
sys.path.append(str(BASE_DIR))

# Load Env
load_dotenv(BASE_DIR / ".env.local")
load_dotenv(BASE_DIR / ".env")

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("market_data_update.log")
    ]
)
logger = logging.getLogger("MasterUpdater")

# Import Scrapers
try:
    # Try package import (if running from backend root)
    from scrapers.calendar_scraper import CalendarScraper
    from scrapers.marctomarket_scraper import MarcToMarketScraper
    from scrapers.retail_sentiment import RetailSentimentAgent
    from scrapers.institutional_researcher import main as run_institutional_research
    from scrapers.sentiment_history_miner import SentimentHistoryMiner
except ImportError:
    # Try directory import (if running from inside scrapers folder)
    try:
        from calendar_scraper import CalendarScraper
        from marctomarket_scraper import MarcToMarketScraper
        from retail_sentiment import RetailSentimentAgent
        from institutional_researcher import main as run_institutional_research
        from sentiment_history_miner import SentimentHistoryMiner
    except ImportError as e:
        logger.error(f"Import Error: {e}")
        sys.exit(1)

async def run_calendar():
    logger.info("[TASK] Starting Economic Calendar Update...")
    try:
        scraper = CalendarScraper()
        await scraper.run()
        logger.info("[OK] Calendar Update Complete.")
    except Exception as e:
        logger.error(f"[ERROR] Calendar Failed: {e}")

async def run_marctomarket():
    logger.info("[TASK] Starting MarcToMarket News Update...")
    try:
        scraper = MarcToMarketScraper()
        await scraper.run()
        logger.info("[OK] MarcToMarket Update Complete.")
    except Exception as e:
        logger.error(f"[ERROR] MarcToMarket Failed: {e}")

async def run_retail():
    logger.info("[TASK] Starting Retail Sentiment Update...")
    try:
        agent = RetailSentimentAgent()
        results = await agent.fetch_sentiment()
        
        # GUARD: Never overwrite with empty data
        if not results or len(results) == 0:
            logger.warning("[SKIP] Retail Sentiment returned 0 results. Keeping existing data.")
            return
        
        # Save to JSON
        import json
        output_file = BASE_DIR / "retail_sentiment.json"
        with open(output_file, "w") as f:
            json.dump(results, f, indent=2)
            
        logger.info(f"[OK] Retail Sentiment Saved ({len(results)} items).")
    except Exception as e:
        logger.error(f"[ERROR] Retail Sentiment Failed: {e}")

async def run_institutional():
    logger.info("[TASK] Starting Institutional Researcher (Deep Dive)...")
    try:
        # This function runs its own logic
        await run_institutional_research()
        logger.info("[OK] Institutional Research Complete.")
    except Exception as e:
        logger.error(f"[ERROR] Institutional Research Failed: {e}")

async def run_history_miner_task():
    logger.info("[TASK] Starting Sentiment History Miner...")
    try:
        miner = SentimentHistoryMiner()
        await miner.run()
        logger.info("[OK] Sentiment History Complete.")
    except Exception as e:
        logger.error(f"[ERROR] Sentiment History Failed: {e}")

async def update_hourly_tasks():
    """Tasks that run every hour"""
    logger.info("[START] Running HOURLY market data updates...")
    await asyncio.gather(
        run_calendar(),
        run_marctomarket(),
        run_institutional(),
        run_history_miner_task()
    )
    logger.info("[DONE] Hourly updates finished.")

async def update_retail_tasks():
    """Tasks that run every 4 hours (Retail Sentiment)"""
    logger.info("[START] Running RETAIL SENTIMENT update...")
    await run_retail()
    logger.info("[DONE] Retail update finished.")

async def main():
    logger.info("[START] STARTING MANUAL FULL MARKET DATA UPDATE")
    logger.info(f"Database Target: {BASE_DIR / 'market_data.db'}")
    
    # Run everything
    await asyncio.gather(
        update_hourly_tasks(),
        update_retail_tasks()
    )
    
    logger.info("[DONE] ALL UPDATES FINISHED")
    
# Expose functions for main.py import
async def update_all_market_data():
    await main()

if __name__ == "__main__":
    # if sys.platform == 'win32':
    #     asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
