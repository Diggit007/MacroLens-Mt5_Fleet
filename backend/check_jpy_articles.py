import sqlite3

conn = sqlite3.connect('C:/MacroLens/backend/market_data.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

c.execute("SELECT title, summary, content, currency, source, publish_date, url FROM articles WHERE currency = 'JPY' ORDER BY scraped_at DESC LIMIT 10")
rows = c.fetchall()

print(f"=== JPY Articles Found: {len(rows)} ===\n")

for i, r in enumerate(rows, 1):
    print(f"[{i}] {r['title']}")
    print(f"    Source: {r['source']} | Date: {r['publish_date']}")
    summary = (r['summary'] or 'N/A')[:200]
    content = (r['content'] or 'N/A')[:300]
    print(f"    Summary: {summary}")
    print(f"    Content: {content}")
    print(f"    URL: {r['url']}")
    print("-" * 80)

conn.close()
