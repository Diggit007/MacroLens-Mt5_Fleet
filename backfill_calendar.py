
import os
from bs4 import BeautifulSoup
import sqlite3
import pandas as pd
import re

# Paths
BASE_DIR = r"C:\Users\Administrator\OneDrive\Desktop\web_App[MacroLen]\MacroLens\version_3"
DB_PATH = os.path.join(BASE_DIR, "market_data.db")
HISTORY_FILE_1 = os.path.join(BASE_DIR, "calendar analyzer", "History.html")
HISTORY_FILE_2 = os.path.join(BASE_DIR, "calendar analyzer", "History2.html")

def setup_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS economic_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_date TEXT,
            event_time TEXT,
            currency TEXT,
            impact_level TEXT,
            event_name TEXT,
            actual_value REAL,
            forecast_value REAL,
            previous_value REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(event_date, event_time, currency, event_name)
        )
    """)
    conn.commit()
    return conn

def parse_value(val_str):
    if not val_str or val_str.strip() == '':
        return None
    # Remove non-numeric chars except . and -
    clean_val = re.sub(r'[^\d.-]', '', val_str)
    try:
        return float(clean_val)
    except ValueError:
        return None

def parse_html_file(file_path, conn, dry_run=False):
    print(f"Parsing {file_path}...")
    try:
        with open(file_path, "rb") as f:
            soup = BeautifulSoup(f, "html.parser")
    except Exception as e:
        print(f"Failed to read file: {e}")
        return

    table = soup.find("table", {"id": "economicCalendarData"})
    if not table:
        print("Table 'economicCalendarData' not found. Searching for 'ecoCalTbl' class...")
        table = soup.find("table", class_="ecoCalTbl")
        
    if not table:
        print("No suitable table found.")
        return

    # If HTML is malformed (unclosed tr), rows might be nested. 
    # recursive=False only finds the top row. We need all trs.
    rows = table.find_all("tr")
        
    print(f"Found {len(rows)} rows.")

    current_date = None
    count = 0
    
    cursor = conn.cursor()

    for row in rows:
        # Get direct columns only
        cols = row.find_all("td", recursive=False)
        
        # Check for Date Header
        # Patterns: id="theDay...", OR explicit colspan (usually spanning all cols)
        # OR text contains a weekday and date
        row_id = row.get("id", "")
        row_text = row.get_text(" ", strip=True)
        

        # Heuristic for Date Row:
        # 1. contains "theDay" in ID
        # 2. OR only 1 column and text looks like a date (has comma, year)
        if "theDay" in row_id or (len(cols) == 1 and "," in row_text and any(c.isdigit() for c in row_text)):
             # Clean up text to just the date part if it has extra noise
             # Usually "Monday, January 1, 2024"
             # Sometimes followed by "All Day Holiday..." if mashed.
             # We'll take the first part split by some known delimiter if needed, but usually get_text is fine if id is date.
             
             # If date row has meaningful text, use it.
             if cols:
                 raw_date = cols[0].get_text(strip=True)
                 # Format: "Monday, January 1, 2024"
                 # Remove "All Day..." if attached
                 # Investing.com date is usually clean in the cell, but let's be safe
                 # Try to parse
                 try:
                     # Remove potential trailing noise
                     # Typical format: "%A, %B %d, %Y"
                     # We can try fuzzy parsing or exact match
                     from datetime import datetime
                     # Split by comma, expect 3 parts: "Day", " Month DD", " YYYY..."
                     # Take the first 3 parts if there's extra text
                     parts = raw_date.split(',')
                     if len(parts) >= 3:
                         date_str = ",".join(parts[:3])
                         # Remove any trailing non-date chars (like "Holiday...")
                         # Assume year is last 4 digits
                         import re
                         match = re.search(r'(\w+), (\w+) (\d+), (\d{4})', date_str)
                         if match:
                             clean_date_str = match.group(0)
                             dt = datetime.strptime(clean_date_str, "%A, %B %d, %Y")
                             current_date = dt.strftime("%Y-%m-%d")
                         else:
                             # Fallback, maybe use dateutil if installed, but strptime is safer standard
                             pass
                 except ImportError:
                     pass
                 except Exception as e:
                     print(f"Date parse error: {raw_date} - {e}")
             continue

        # If it's not a date row, check if it's an event row
        # Event rows usually have multiple columns: Time, Cur, Imp, Event, Actual, Forecast, Prev
        if len(cols) < 5:
            continue

        # Extract Time
        time_cell = row.find("td", class_="time")
        if time_cell:
            event_time = time_cell.get_text(strip=True)
        else:
            # Fallback by index 0
            event_time = cols[0].get_text(strip=True)

        # Extract Currency
        curr_cell = row.find("td", class_="flagCur")
        if curr_cell:
            currency = curr_cell.get_text(strip=True)
        else:
            # Fallback index 1
            currency = cols[1].get_text(strip=True)
        currency = currency.replace("\xa0", "").strip()

        # Extract Impact
        impact_cell = row.find("td", class_="sentiment")
        impact = "Low"
        if impact_cell:
            # Check for high volatility icons
            bulls = impact_cell.find_all(class_="grayFullBullishIcon")
            if len(bulls) == 3: impact = "High"
            elif len(bulls) == 2: impact = "Medium"
        elif len(cols) > 2:
             # Fallback index 2 logic if needed, but classes are reliable on investing.com usually
             pass

        # Extract Event Name
        event_cell = row.find("td", class_="event")
        if event_cell:
            event_name = event_cell.get_text(strip=True)
        else:
            # Fallback index 3
            event_name = cols[3].get_text(strip=True)
            
        # Clean event name
        event_name = event_name.strip()

        # Extract Data: Actual, Forecast, Previous
        # Indices in standar ecoCalTbl: 
        # 0: Time, 1: Cur, 2: Imp, 3: Event, 4: Actual, 5: Forecast, 6: Previous, 7: Diamond?
        
        # Verify columns count again
        if len(cols) >= 7:
            actual_str = cols[4].get_text(strip=True)
            forecast_str = cols[5].get_text(strip=True)
            previous_str = cols[6].get_text(strip=True)
            
            actual = parse_value(actual_str)
            forecast = parse_value(forecast_str)
            previous = parse_value(previous_str)
            
            # Skip empty events if they have no useful data or name
            if not event_name:
                continue

            if dry_run and count < 10:
                print(f"Date: {current_date} | Time: {event_time} | {currency} | {impact} | {event_name} | Act: {actual} / Fcst: {forecast} / Prev: {previous}")
            
            if not dry_run and current_date:
                try:
                    cursor.execute("""
                        INSERT OR IGNORE INTO economic_events 
                        (event_date, event_time, currency, impact_level, event_name, actual_value, forecast_value, previous_value)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (current_date, event_time, currency, impact, event_name, actual, forecast, previous))
                    count += 1
                except Exception as e:
                    # Ignore duplicates
                    pass

    if not dry_run:
        conn.commit()
        print(f"Inserted {count} events from {os.path.basename(file_path)}.")

if __name__ == "__main__":
    conn = setup_db()
    # Dry run first to verify
    print("--- DRY RUN HISTORY 1 ---")
    parse_html_file(HISTORY_FILE_1, conn, dry_run=True)
    
    # Real run
    print("--- REAL RUN ---")
    parse_html_file(HISTORY_FILE_1, conn, dry_run=False)
    
    if os.path.exists(HISTORY_FILE_2):
        parse_html_file(HISTORY_FILE_2, conn, dry_run=False)
        
    conn.close()
