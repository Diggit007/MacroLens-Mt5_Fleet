import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "market_data.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Delete the specific article
    c.execute("DELETE FROM articles WHERE url LIKE '%pound-dollar-drops-towards-1-37%'")
    rows = c.rowcount
    c.execute("DELETE FROM articles WHERE title LIKE '%Pound-Dollar Drops Towards 1.37%'")
    rows += c.rowcount
    
    conn.commit()
    print(f"Deleted {rows} rows.")
    conn.close()

if __name__ == "__main__":
    main()
