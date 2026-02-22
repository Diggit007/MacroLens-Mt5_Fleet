import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path(__file__).parent.parent / "market_data.db"

def main():
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    print("=== Economic Calendar Continuity Check ===")
    
    # 1. Get Min/Max
    c.execute("SELECT MIN(event_date), MAX(event_date), COUNT(*) FROM economic_events")
    min_date, max_date, total = c.fetchone()
    
    print(f"Oldest Date: {min_date}")
    print(f"Newest Date: {max_date}")
    print(f"Total Events: {total}")
    
    # 2. Check for Gaps
    # Get all distinct dates ordered
    c.execute("SELECT DISTINCT event_date FROM economic_events ORDER BY event_date ASC")
    rows = c.fetchall()
    
    if not rows:
        print("No data found.")
        conn.close()
        return

    dates = []
    for r in rows:
        try:
            # Handle potential format variations, assumption is YYYY-MM-DD
            d = datetime.strptime(r[0], "%Y-%m-%d").date()
            dates.append(d)
        except ValueError:
            print(f"Skipping invalid date format: {r[0]}")
            
    print(f"\nScanning {len(dates)} unique days for gaps...")
    
    gaps_found = 0
    for i in range(1, len(dates)):
        prev = dates[i-1]
        curr = dates[i]
        diff =  curr - prev
        
        if diff.days > 1:
            print(f"⚠️  GAP FOUND: {prev} -> {curr} ({diff.days - 1} missing days)")
            gaps_found += 1
            
    if gaps_found == 0:
        print("\n✅ Verification Successful: No breaks in date sequence found.")
    else:
        print(f"\n❌ Verification Failed: {gaps_found} gaps found.")

    conn.close()

if __name__ == "__main__":
    main()
