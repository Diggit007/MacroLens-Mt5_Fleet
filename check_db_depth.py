import sqlite3
from datetime import datetime, timedelta

DB_PATH = "C:/MacroLens/backend/market_data.db"

def check_history():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    indicators = ["CPI", "GDP", "Unemployment", "Interest Rate"]
    
    print(f"Checking Data Depth for: {', '.join(indicators)}\n")
    print(f"{'INDICATOR':<20} | {'COUNT':<10} | {'EARLIEST DATE':<15} | {'LATEST DATE':<15}")
    print("-" * 70)
    
    for ind in indicators:
        try:
            c.execute("SELECT count(*), min(event_date), max(event_date) FROM economic_events WHERE event_name LIKE ?", (f"%{ind}%",))
            res = c.fetchone()
            count = res[0]
            earliest = res[1] if res[1] else "N/A"
            latest = res[2] if res[2] else "N/A"
            
            print(f"{ind:<20} | {count:<10} | {earliest:<15} | {latest:<15}")
        except Exception as e:
            print(f"Error checking {ind}: {e}")
            
    conn.close()

if __name__ == "__main__":
    check_history()
