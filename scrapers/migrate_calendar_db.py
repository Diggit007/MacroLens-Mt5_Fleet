import sqlite3
import asyncio
from pathlib import Path
from backend.scrapers.calendar_scraper import CalendarScraper

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "market_data.db"

async def main():
    print(f"Migrating DB at {DB_PATH}")
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 1. Drop old table
    print("Dropping old `economic_events` table...")
    c.execute("DROP TABLE IF EXISTS economic_events")
    conn.commit()
    conn.close()
    
    # 2. Re-Initialize (Creates new table)
    print("Initializing Scraper (Recreating Table)...")
    scraper = CalendarScraper()
    
    # 3. Trigger Scrape
    print("Triggering Fresh Scrape...")
    await scraper.run()
    
    print("Migration & Scrape Complete.")

if __name__ == "__main__":
    asyncio.run(main())
