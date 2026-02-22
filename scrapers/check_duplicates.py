import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "market_data.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    print("=== Cleaning Duplicates for 'Pound-Dollar Drops Towards 1.37' ===")
    
    c.execute("SELECT id, title, url FROM articles WHERE title LIKE '%Pound-Dollar Drops Towards 1.37%'")
    rows = c.fetchall()
    print(f"Total Rows Found: {len(rows)}")
    
    if len(rows) > 1:
        # Sort by ID descending (High ID = Newest)
        sorted_rows = sorted(rows, key=lambda x: x[0], reverse=True)
        keep_id = sorted_rows[0][0]
        delete_ids = [r[0] for r in sorted_rows[1:]]
        
        print(f"Keeping ID: {keep_id} (Newest)")
        print(f"Deleting IDs: {delete_ids}")
        
        placeholders = ','.join('?' * len(delete_ids))
        c.execute(f"DELETE FROM articles WHERE id IN ({placeholders})", delete_ids)
        conn.commit()
        print(f"Deleted {c.rowcount} rows.")
    else:
        print("No duplicates to clean.")
    
    conn.close()

if __name__ == "__main__":
    main()
