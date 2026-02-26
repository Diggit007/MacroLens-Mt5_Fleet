
import sqlite3

conn = sqlite3.connect('C:/MacroLens/backend/market_data.db')
cursor = conn.cursor()
cursor.execute('PRAGMA table_info(event_reactions)')
for col in cursor.fetchall():
    print(col[1])
conn.close()
