import sqlite3
import json
from pathlib import Path

# Script is in backend/scrapers/, DB is in backend/
DB_PATH = Path(__file__).parent.parent / "market_data.db"
SENTIMENT_PATH = Path(__file__).parent.parent / "retail_sentiment.json"

def main():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    print("=" * 60)
    print("ECONOMIC CALENDAR SUMMARY")
    print("=" * 60)
    
    # Count by impact
    c.execute("SELECT impact_level, COUNT(*) FROM economic_events GROUP BY impact_level")
    for row in c.fetchall():
        print(f"  {row[0]}: {row[1]} events")
    
    # Date range
    c.execute("SELECT MIN(event_date), MAX(event_date) FROM economic_events")
    dates = c.fetchone()
    print(f"  Date Range: {dates[0]} to {dates[1]}")
    
    # Recent High Impact
    print("\n  Recent High Impact Events:")
    c.execute("""
        SELECT event_name, event_date, event_time, currency 
        FROM economic_events 
        WHERE impact_level='High' 
        ORDER BY event_date DESC, event_time DESC 
        LIMIT 5
    """)
    for row in c.fetchall():
        print(f"    [{row[3]}] {row[0]} @ {row[1]} {row[2]}")
    
    print("\n" + "=" * 60)
    print("INSTITUTIONAL NEWS / ARTICLES SUMMARY")
    print("=" * 60)
    
    # Count by source
    c.execute("SELECT source, COUNT(*) FROM articles GROUP BY source")
    for row in c.fetchall():
        print(f"  {row[0]}: {row[1]} articles")
    
    # Recent articles
    print("\n  Recent Articles:")
    c.execute("""
        SELECT title, currency, source, publish_date 
        FROM articles 
        ORDER BY scraped_at DESC 
        LIMIT 5
    """)
    for row in c.fetchall():
        title = row[0][:60] + "..." if len(row[0]) > 60 else row[0]
        print(f"    [{row[2]}] ({row[1]}) {title}")
    
    conn.close()
    
    print("\n" + "=" * 60)
    print("RETAIL SENTIMENT SUMMARY (MyFxBook)")
    print("=" * 60)
    
    if SENTIMENT_PATH.exists():
        with open(SENTIMENT_PATH, "r") as f:
            data = json.load(f)
        
        print(f"  Total Symbols: {len(data)}")
        print("\n  Symbol Breakdown:")
        
        for item in data[:15]:
            signal_emoji = "BUY" if item["signal"] == "BUY" else "SELL" if item["signal"] == "SELL" else "---"
            print(f"    {item['symbol']:8} | {item['long_pct']:3}% Long | {item['short_pct']:3}% Short | {signal_emoji:4} ({item['strength']})")
    else:
        print("  [ERROR] retail_sentiment.json not found")
    
    print("\n" + "=" * 60)
    print("SCRAPER RUN COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    main()
