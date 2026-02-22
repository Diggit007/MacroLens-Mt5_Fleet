import sqlite3
conn = sqlite3.connect('c:/MacroLens/backend/market_data.db')
c = conn.cursor()
print("Events Jan 5-9 2026:", c.execute("SELECT COUNT(*) FROM economic_events WHERE event_date BETWEEN '2026-01-05' AND '2026-01-09'").fetchone()[0])
print("\nSample events for Jan 5-9:")
for r in c.execute("SELECT event_name, event_date, event_time, currency FROM economic_events WHERE event_date BETWEEN '2026-01-05' AND '2026-01-09' LIMIT 10").fetchall():
    print(r)
print("\nAll 2026 dates:")
for r in c.execute("SELECT DISTINCT event_date FROM economic_events WHERE event_date LIKE '2026%' ORDER BY event_date").fetchall():
    print(r[0])
conn.close()
