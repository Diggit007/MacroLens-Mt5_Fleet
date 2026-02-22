
import asyncio
import logging
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from pathlib import Path
import sys

# Add backend to path
sys.path.append(str(Path(__file__).parent.parent.parent))

from backend.scrapers.calendar_scraper import CalendarScraper

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("HistoryMiner")

async def mine_history():
    scraper = CalendarScraper()
    
    # Range: Jan 2024 to Present (Feb 2026)
    start_date = datetime(2024, 1, 1)
    end_date = datetime(2026, 2, 1) # Scan up to current
    
    current = start_date
    
    print("------------------------------------------------")
    print("   MACROLENS CALENDAR HISTORY MINER (2024-2026)")
    print("------------------------------------------------")
    
    while current < end_date:
        # Process 1 Month at a time
        month_end = current + relativedelta(months=1) - timedelta(days=1)
        
        d_from = current.strftime("%Y-%m-%d")
        d_to = month_end.strftime("%Y-%m-%d")
        
        print(f"\n[MINING] {current.strftime('%B %Y')} ({d_from} to {d_to})...")
        
        try:
            await scraper.run(d_from, d_to)
            print(f"[SUCCESS] Processed {current.strftime('%B %Y')}")
        except Exception as e:
            print(f"[ERROR] Failed {current.strftime('%B %Y')}: {e}")
            
        # Move to next month
        current += relativedelta(months=1)
        
        # Respect Rate Limits (Important for scraping)
        import random
        sleep_time = random.uniform(2.0, 5.0)
        print(f"Sleeping {sleep_time:.1f}s...")
        await asyncio.sleep(sleep_time)
        
    print("\n[DONE] History Mining Complete.")

if __name__ == "__main__":
    # Ensure pip install python-dateutil
    try:
        import dateutil
    except ImportError:
        print("Please install dateutil: pip install python-dateutil")
        sys.exit(1)
        
    asyncio.run(mine_history())
