
import sqlite3

def check_names():
    conn = sqlite3.connect('C:/MacroLens/backend/market_data.db')
    cursor = conn.cursor()
    
    keywords = ["CPI", "Consumer Price", "Inflation", "GDP", "Gross Domestic", "Unemployment", "Rate"]
    
    print("--- Searching for Event Names ---")
    for k in keywords:
        print(f"\n[Matches for '{k}']")
        cursor.execute(f"SELECT DISTINCT event_name FROM economic_events WHERE event_name LIKE '%{k}%'")
        rows = cursor.fetchall()
        for r in rows:
            print(f" - {r[0]}")
            
    conn.close()

if __name__ == "__main__":
    check_names()
