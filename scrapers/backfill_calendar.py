import sqlite3
import re
from datetime import datetime
from pathlib import Path
import logging
from bs4 import BeautifulSoup
import hashlib

# Config
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR.parent / "market_data.db"
HISTORY_FILES = ["History.html", "History2.html"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CalendarBackfill")

class CalendarBackfill:
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

    def clean_value(self, val):
        if not val: return None
        # Handle "1.2K", "5%", "10.0B"
        val = str(val).strip().replace(',', '').replace('%', '')
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

    def parse_impact(self, row_soup):
        # Look for sentiment icons
        try:
            sent_cell = row_soup.find("td", class_="sentiment")
            if not sent_cell: return "Low"
            
            # Check title
            title = sent_cell.get("title", "").lower() or sent_cell.get("data-img_key", "").lower()
            if "high" in title: return "High"
            if "moderate" in title or "medium" in title: return "Moderate"
            
            # Check icons count if no title
            icons = sent_cell.find_all("i")
            if len(icons) == 3: return "High"
            if len(icons) == 2: return "Moderate"
            return "Low"
        except:
            return "Low"

    def clean_event_name(self, text):
        return re.sub(r"\s*\((?!MoM|YoY|QoQ)[^)]+\)", "", text).strip()

    def parse_html_file(self, file_path: Path):
        logger.info(f"Parsing {file_path}...")
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                html = f.read()
                
            soup = BeautifulSoup(html, 'html.parser')

            # Detect V2 (React)
            if soup.find("tr", class_=lambda x: x and "datatable-v2_row" in x):
                logger.info("Detected V2 (React) calendar format.")
                return self.parse_html_v2(soup)
                
            table = soup.find("table", {"id": "economicCalendarData"})
            if not table:
                logger.warning("Main table not found, trying ecEventsTable...")
                table = soup.find("table", {"id": "ecEventsTable"})
                
            if not table:
                logger.error(f"No calendar table found in {file_path}")
                return []

            events = []
            current_date = None
            
            rows = table.find_all("tr")
            for row in rows:
                # Date Row
                if "theDay" in row.get("class", []) or row.find("td", class_="theDay"):
                    text = row.get_text(strip=True)
                    try:
                        clean_date = re.sub(r"^[A-Za-z]+,\s*", "", text)
                        try:
                            dt = datetime.strptime(clean_date, "%B %d, %Y")
                        except:
                            dt = datetime.strptime(clean_date, "%b %d, %Y")
                            
                        current_date = dt.strftime("%Y-%m-%d")
                        if dt < datetime(2024, 1, 1):
                            current_date = None
                    except Exception as e:
                        pass
                    continue

                if not current_date:
                    continue

                # Event Row
                if not row.get("id", "").startswith("eventRowId"):
                    continue

                try:
                    time_cell = row.find("td", class_="time")
                    time_str = time_cell.get_text(strip=True) if time_cell else "00:00"
                    if "Day" in time_str: time_str = "00:00"
                    
                    curr_cell = row.find("td", class_="flagCur")
                    currency = curr_cell.get_text(strip=True).split()[0].strip() if curr_cell else ""
                    if not currency: continue

                    impact = self.parse_impact(row)

                    event_cell = row.find("td", class_="event")
                    raw_name = event_cell.get_text(strip=True) if event_cell else "Unknown"
                    event_name = self.clean_event_name(raw_name)

                    act_cell = row.find("td", class_="act")
                    fore_cell = row.find("td", class_="fore")
                    prev_cell = row.find("td", class_="prev")

                    actual = self.clean_value(act_cell.get_text(strip=True)) if act_cell else None
                    forecast = self.clean_value(fore_cell.get_text(strip=True)) if fore_cell else None
                    previous = self.clean_value(prev_cell.get_text(strip=True)) if prev_cell else None

                    events.append({
                        "date": current_date,
                        "time": time_str,
                        "currency": currency,
                        "event": event_name,
                        "impact": impact,
                        "actual": actual,
                        "forecast": forecast,
                        "previous": previous
                    })
                except Exception as e:
                    continue
                    
            return events
        except Exception as e:
            logger.error(f"Failed to parse {file_path}: {e}")
            return []

    def parse_html_v2(self, soup):
        events = []
        rows = soup.find_all("tr")
        
        for row in rows:
            # Only process event rows (with datatable-v2 class and an id)
            if "datatable-v2_row" not in str(row.get("class", [])) or not row.has_attr("id"):
                continue

            try:
                text = row.get_text(" ", strip=True)
                
                # Extract date from this row's text
                # Format 1: "Thursday, January 15, 2026" (US)
                event_date_str = None
                match = re.search(r"([A-Za-z]+, [A-Za-z]+ \d{1,2}, \d{4})", text)
                if match:
                    try:
                        dt = datetime.strptime(match.group(1), "%A, %B %d, %Y")
                        event_date_str = dt.strftime("%Y-%m-%d")
                    except:
                        pass
                
                # Format 2: "Thursday, 1 January 2026" (UK/EU)
                if not event_date_str:
                    match2 = re.search(r"([A-Za-z]+, \d{1,2} [A-Za-z]+ \d{4})", text)
                    if match2:
                        try:
                            dt = datetime.strptime(match2.group(1), "%A, %d %B %Y")
                            event_date_str = dt.strftime("%Y-%m-%d")
                        except:
                            pass
                
                if not event_date_str:
                    continue
                
                cols = row.find_all("td", recursive=False)
                
                time_div = row.find(string=re.compile(r"^\d{2}:\d{2}$"))
                time_str = time_div.strip() if time_div else "00:00"

                curr_node = row.find("span", string=re.compile(r"^[A-Z]{3}$"))
                currency = curr_node.get_text(strip=True) if curr_node else "UNK"

                name_node = row.find("a", href=True)
                if not name_node: continue
                
                raw_name = name_node.get_text(strip=True)
                event_name = self.clean_event_name(raw_name)

                impact_svgs = row.find_all("svg")
                stars = 0
                for svg in impact_svgs:
                    if "opacity-20" not in svg.get("class", []):
                        stars += 1
                
                impact = "Low"
                if stars >= 3: impact = "High"
                elif stars == 2: impact = "Moderate"

                values = []
                for td in cols:
                    cell_text = td.get_text(strip=True)
                    if cell_text == time_str or cell_text == currency or cell_text in raw_name:
                        continue
                    if re.match(r"^-?[\d,.]+[KMB%]?$", cell_text):
                        values.append(cell_text)
                
                actual = self.clean_value(values[0]) if len(values) > 0 else None
                forecast = self.clean_value(values[1]) if len(values) > 1 else None
                previous = self.clean_value(values[2]) if len(values) > 2 else None

                events.append({
                    "date": event_date_str,
                    "time": time_str,
                    "currency": currency,
                    "event": event_name,
                    "impact": impact,
                    "actual": actual,
                    "forecast": forecast,
                    "previous": previous
                })
            except:
                continue
                
        return events

    def save_to_db(self, events):
        if not events: return
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        count = 0
        for ev in events:
            # ID Generation
            base = f"{ev['event']}-{ev['date']}-{ev['time']}-{ev['currency']}"
            ev_id = hashlib.sha1(base.encode()).hexdigest()
            
            try:
                cursor.execute("""
                    INSERT INTO economic_events 
                    (event_id, event_name, event_date, event_time, currency, forecast_value, actual_value, previous_value, impact_level)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO UPDATE SET
                    actual_value=excluded.actual_value,
                    forecast_value=excluded.forecast_value,
                    previous_value=excluded.previous_value
                """, (
                    ev_id, ev['event'], ev['date'], ev['time'], ev['currency'],
                    ev['forecast'], ev['actual'], ev['previous'], ev['impact']
                ))
                count += 1
            except Exception as e:
                logger.error(f"Insert error: {e}")
                
        conn.commit()
        conn.close()
        logger.info(f"Saved {count} events.")

    def run(self):
        total_events = 0
        for fname in HISTORY_FILES:
            fpath = BASE_DIR / fname
            if fpath.exists():
                events = self.parse_html_file(fpath)
                logger.info(f"Found {len(events)} events in {fname} (from Jan 2024)")
                self.save_to_db(events)
                total_events += len(events)
            else:
                logger.error(f"File not found: {fpath}")
        
        logging.info("Backfill Complete.")

if __name__ == "__main__":
    backfill = CalendarBackfill()
    backfill.run()
