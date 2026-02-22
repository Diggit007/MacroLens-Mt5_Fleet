import sqlite3
from pathlib import Path

# DB is in backend/
DB_PATH = Path(__file__).parent.parent / "market_data.db"

def main():
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    print("=" * 60)
    print("LATEST MARCTOMARKET ARTICLES")
    print("=" * 60)
    
    try:
        c.execute("""
            SELECT title, publish_date, content, url 
            FROM articles 
            WHERE source='FXStreet' 
            ORDER BY scraped_at DESC 
            LIMIT 1
        """)
        
        row = c.fetchone()
        if not row:
            print("No articles found from FXStreet.")
            return
        
        print(f"\nTITLE: {row[0]}")
        print(f"DATE:  {row[1]}")
        print(f"URL:   {row[3]}")
        print("-" * 60)
        print(row[2]) # Full Content
        print("=" * 60)
            
    except Exception as e:
        print(f"Query Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
