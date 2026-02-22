import sqlite3
from pathlib import Path
from datetime import datetime

# DB Path
db_path = Path("C:/MacroLens/backend/market_data.db")

try:
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    # Check max date
    c.execute("SELECT MAX(event_date) FROM economic_events")
    max_date = c.fetchone()[0]
    print(f"Max Date in DB: {max_date}")
    
    # Check for today/future
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"Today is: {today}")
    
    c.execute("SELECT event_date, event_time, event_name, forecast_value FROM economic_events WHERE event_date >= ? ORDER BY event_date ASC LIMIT 10", (today,))
    rows = c.fetchall()
    
    print("\nUpcoming/Recent Events:")
    for r in rows:
        print(r)
        
    conn.close()
except Exception as e:
    print(f"Error: {e}")
