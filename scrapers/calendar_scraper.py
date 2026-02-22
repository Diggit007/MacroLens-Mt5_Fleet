import asyncio
import sqlite3
import re
from datetime import datetime
from pathlib import Path
import logging
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler

# Config
BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "market_data.db"
URL = "https://sslecal2.forexprostools.com/"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CalendarScraper")

class CalendarScraper:
    def __init__(self):
        self.db_path = DB_PATH
        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS economic_events (
                event_id TEXT PRIMARY KEY,
                event_name TEXT,
                event_date TEXT,
                event_time TEXT,
                currency TEXT,
                forecast_value REAL,
                actual_value REAL,
                previous_value REAL,
                impact_level TEXT
            )
        """)
        conn.commit()
        conn.close()
        
    def get_connection(self):
        return sqlite3.connect(self.db_path)

    async def fetch_calendar_html(self):
        logger.info(f"Fetching calendar from {URL}...")
        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(url=URL)
            if not result.success:
                logger.error(f"Failed to fetch content: {result.error_message}")
                return None
            return result.html

    def parse_impact(self, title_attr):
        title = (title_attr or "").lower()
        if "high" in title: return "High"
        if "moderate" in title or "medium" in title: return "Moderate"
        return "Low"

    def clean_value(self, val):
        if not val: return None
        # Handle "1.2K", "5%", "10.0B"
        val = val.strip().replace(',', '').replace('%', '')
        if 'K' in val:
            val = val.replace('K', '')
            mult = 1000
        elif 'M' in val:
            val = val.replace('M', '')
            mult = 1000000
        elif 'B' in val:
            val = val.replace('B', '')
            mult = 1000000000
        else:
            mult = 1
            
        try:
            return float(val) * mult
        except:
            return None

    def clean_event_name(self, text):
        import re
        return re.sub(r"\s*\((?!MoM|YoY|QoQ)[^)]+\)", "", text).strip()

    def parse_events(self, html):
        soup = BeautifulSoup(html, 'html.parser')
        table = soup.find("table", {"id": "ecEventsTable"})
        if not table:
            logger.error("Could not find #ecEventsTable")
            return []

        events = []
        current_date = None
        
        rows = table.find_all("tr")
        logger.info(f"Found {len(rows)} rows in calendar table.")
        
        for row in rows:
            # Check for Date Row
            if "theDay" in row.get("class", []) or row.find("td", class_="theDay"):
                text = row.get_text(strip=True)
                try:
                    clean_date = re.sub(r"^[A-Za-z]+,\s*", "", text)
                    dt = datetime.strptime(clean_date, "%B %d, %Y")
                    current_date = dt.strftime("%Y-%m-%d")
                except Exception as e:
                    pass
                continue

            # Check for Event Row
            if not row.get("id", "").startswith("eventRowId"):
                continue

            if not current_date:
                continue

            try:
                # Time
                time_cell = row.find("td", class_="time")
                time_str = time_cell.get_text(strip=True) if time_cell else "00:00"
                if "Day" in time_str: time_str = "00:00"
                
                # Currency
                curr_cell = row.find("td", class_="flagCur")
                currency = curr_cell.get_text(strip=True).split()[0] if curr_cell else ""
                
                # Sentiment (Impact)
                sent_cell = row.find("td", class_="sentiment")
                impact = "Low"
                if sent_cell:
                    title = sent_cell.get("title", "").lower()
                    if "high" in title: impact = "High"
                    elif "moderate" in title or "medium" in title: impact = "Moderate"

                # Event Name
                event_cell = row.find("td", class_="event")
                raw_name = event_cell.get_text(strip=True) if event_cell else "Unknown Event"
                event_name = self.clean_event_name(raw_name)

                # Values
                actual = row.find("td", class_="act").get_text(strip=True)
                forecast = row.find("td", class_="fore").get_text(strip=True)
                prev = row.find("td", class_="prev").get_text(strip=True)

                events.append({
                    "event_name": event_name,
                    "event_date": current_date,
                    "event_time": time_str,
                    "currency": currency,
                    "impact_level": impact,
                    "actual_value": self.clean_value(actual),
                    "forecast_value": self.clean_value(forecast),
                    "previous_value": self.clean_value(prev)
                })

            except Exception as e:
                logger.warning(f"Error parsing row: {e}")
                continue
                
        return events

    def save_to_db(self, events):
        if not events:
            logger.warning("No events to save.")
            return

        conn = self.get_connection()
        cursor = conn.cursor()
        
        count = 0
        for ev in events:
            # Generate ID (Standardized: Name-Date-Time-Currency)
            base_id = f"{ev['event_name']}-{ev['event_date']}-{ev['event_time'].replace(':00', '')}-{ev['currency']}"
            import hashlib
            event_id = hashlib.sha1(base_id.encode()).hexdigest()
            
            try:
                # Upsert
                cursor.execute("""
                    INSERT INTO economic_events 
                    (event_id, event_name, event_date, event_time, currency, forecast_value, actual_value, previous_value, impact_level)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO UPDATE SET
                    forecast_value=excluded.forecast_value,
                    actual_value=excluded.actual_value,
                    previous_value=excluded.previous_value,
                    impact_level=excluded.impact_level
                """, (
                    event_id, ev['event_name'], ev['event_date'], ev['event_time'], ev['currency'],
                    ev['forecast_value'], ev['actual_value'], ev['previous_value'], ev['impact_level']
                ))
                count += 1
            except Exception as e:
                logger.error(f"DB Error: {e}")

        conn.commit()
        conn.close()
        logger.info(f"Successfully updated {count} events in DB.")

    async def fetch_calendar_html(self, date_from=None, date_to=None):
        url = URL
        if date_from and date_to:
            # Widget params often use: ?dateFrom=2026-01-05&dateTo=2026-01-09
            # Verify exact param names for forexprostools. 
            # Commonly: analysis/economic-calendar-8907.php?dateFrom=...
            # For sslecal2.forexprostools.com, it typically embeds an iframe. 
            # We will try appending standard params.
            url = f"{URL}?dateFrom={date_from}&dateTo={date_to}"
            
        logger.info(f"Fetching calendar from {url}...")
        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(url=url)
            if not result.success:
                logger.error(f"Failed to fetch content: {result.error_message}")
                return None
            return result.html

    async def run(self, date_from=None, date_to=None):
        html = await self.fetch_calendar_html(date_from, date_to)
        if html:
            events = self.parse_events(html)
            self.save_to_db(events)

if __name__ == "__main__":
    import sys
    scraper = CalendarScraper()
    
    # CLI args: python calendar_scraper.py 2026-01-05 2026-01-09
    d_from = sys.argv[1] if len(sys.argv) > 2 else None
    d_to = sys.argv[2] if len(sys.argv) > 2 else None
    
    asyncio.run(scraper.run(d_from, d_to))
