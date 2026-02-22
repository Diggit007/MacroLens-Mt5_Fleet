
import asyncio
import aiosqlite
import random
import os
from backend.core.database import DatabasePool

# Ensure we use a test DB file
os.environ["DB_PATH"] = "test_concurrency.db"

async def reader_task(i):
    """Simulates a user reading data"""
    try:
        # DB Pool handles connection reuse
        conn = await DatabasePool.get_connection()
        async with conn.execute("SELECT count(*) FROM test_table") as cursor:
            await cursor.fetchone()
        # await asyncio.sleep(random.random() * 0.1) # Simulate think time
        return "OK"
    except Exception as e:
        return f"READ_ERROR: {e}"

async def writer_task(i):
    """Simulates a background worker writing data"""
    try:
        conn = await DatabasePool.get_connection()
        await conn.execute("INSERT INTO test_table (data) VALUES (?)", (f"data_{i}",))
        await conn.commit()
        return "OK"
    except Exception as e:
        return f"WRITE_ERROR: {e}"

async def main():
    print("--- Testing SQLite WAL Concurrency ---")
    
    # 1. Setup
    conn = await DatabasePool.get_connection()
    await conn.execute("CREATE TABLE IF NOT EXISTS test_table (id INTEGER PRIMARY KEY, data TEXT)")
    await conn.execute("DELETE FROM test_table") # Clean start
    await conn.commit()
    
    # Check WAL Mode
    async with conn.execute("PRAGMA journal_mode") as cursor:
        mode = await cursor.fetchone()
        print(f"Current Journal Mode: {mode[0]}")
        if mode[0].upper() != 'WAL':
             print("[FAIL] WAL Mode not active!")
             return

    # 2. Simulate High Load (20 Readers + 5 Writers concurrent)
    print("Launching 50 concurrent operations (40 Reads, 10 Writes)...")
    
    tasks = []
    for i in range(40):
        tasks.append(reader_task(i))
    for i in range(10):
        tasks.append(writer_task(i))
        
    random.shuffle(tasks) # Mix them up
    
    results = await asyncio.gather(*tasks)
    
    errors = [r for r in results if r != "OK"]
    
    print(f"\nTotal Ops: {len(results)}")
    print(f"Errors: {len(errors)}")
    
    if len(errors) == 0:
        print("[PASS] No locking errors under load.")
    else:
        print("[FAIL] Encontered errors:")
        for e in errors[:5]: print(e)

    await DatabasePool.close()
    
    # Cleanup
    try:
        os.remove("test_concurrency.db")
        os.remove("test_concurrency.db-wal")
        os.remove("test_concurrency.db-shm")
    except: pass

if __name__ == "__main__":
    asyncio.run(main())
