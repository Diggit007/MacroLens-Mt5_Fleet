import sqlite3
import json
from pathlib import Path

# Find all .db files and check their tables
backend_dir = Path(__file__).parent.parent

db_files = list(backend_dir.rglob("*.db"))
print(f"Found {len(db_files)} database files:\n")

for db_path in db_files:
    print(f"=== {db_path.relative_to(backend_dir)} ===")
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in c.fetchall()]
        print(f"  Tables: {tables}")
        
        for table in tables:
            c.execute(f"SELECT COUNT(*) FROM {table}")
            count = c.fetchone()[0]
            print(f"    - {table}: {count} rows")
        
        conn.close()
    except Exception as e:
        print(f"  Error: {e}")
    print()
