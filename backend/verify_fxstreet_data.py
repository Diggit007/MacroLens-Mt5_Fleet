
import sqlite3

def check_fxstreet():
    conn = sqlite3.connect('C:/MacroLens/backend/market_data.db')
    cursor = conn.cursor()
    
    print("--- Checking FXStreet Articles ---")
    cursor.execute("SELECT COUNT(*), currency FROM articles WHERE source = 'FXStreet' GROUP BY currency")
    rows = cursor.fetchall()
    
    if not rows:
        print("No articles found from FXStreet yet.")
    else:
        for r in rows:
            print(f"Currency: {r[1]} | Count: {r[0]}")
            
    print("\n--- Sample Article ---")
    cursor.execute("SELECT title, url, scraped_at, source FROM articles WHERE source = 'FXStreet' ORDER BY scraped_at DESC LIMIT 5")
    rows = cursor.fetchall()
    for row in rows:
        print(f"Title: {row[0]}")
        print(f"URL: {row[1]}")
        print(f"Scraped At: {row[2]}")
        print(f"Source: {row[3]}")
        print("-" * 20)
            
    conn.close()

if __name__ == "__main__":
    check_fxstreet()
