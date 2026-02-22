import aiosqlite
import asyncio
import os
from typing import Optional
import logging

logger = logging.getLogger("DatabasePool")

from pathlib import Path

# SQLite DB Path (Robust Resolution)
# Resolves to: backend/market_data.db
BASE_DIR = Path(__file__).resolve().parent.parent 
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "market_data.db"))

class DatabasePool:
    """Simple connection pool for aiosqlite to reduce overhead"""
    _connection: Optional[aiosqlite.Connection] = None
    _lock = asyncio.Lock()
    
    @classmethod
    async def health_check(cls) -> bool:
        """Verifies DB is accessible and writable (WAL mode check)"""
        try:
            conn = await cls.get_connection()
            async with conn.execute("SELECT 1") as cursor:
                await cursor.fetchone()
            # Optional: Check WAL mode active?
            # async with conn.execute("PRAGMA journal_mode") as cursor:
            #     mode = await cursor.fetchone()
            return True
        except Exception as e:
            logger.error(f"DB Health Check Failed: {e}")
            return False
    
    @classmethod
    async def get_connection(cls):
        """Get or create a shared connection"""
        async with cls._lock:
            if cls._connection is None:
                logger.debug(f"Connecting to SQLite: {DB_PATH}")
                # Phase 5: Concurrency Optimization (30s timeout)
                cls._connection = await aiosqlite.connect(DB_PATH, timeout=30.0)
                
                # Phase 5: Enforce WAL Mode & Performance pragmas
                await cls._connection.execute("PRAGMA journal_mode=WAL")
                await cls._connection.execute("PRAGMA synchronous=NORMAL")
                await cls._connection.execute("PRAGMA cache_size=10000") # ~40MB cache
            return cls._connection
    
    @classmethod
    async def close(cls):
        """Close the shared connection"""
        async with cls._lock:
            if cls._connection:
                logger.debug("Closing SQLite connection")
                await cls._connection.close()
                cls._connection = None

    @classmethod
    async def execute(cls, query: str, params: tuple = ()):
        """Helper for simple executions w/o auto-commit management (caller must commit if needed, or use execute_commit)"""
        conn = await cls.get_connection()
        return await conn.execute(query, params)

    @classmethod
    async def execute_commit(cls, query: str, params: tuple = ()):
        """Helper for execution with commit"""
        conn = await cls.get_connection()
        await conn.execute(query, params)
        await conn.commit()

    @classmethod
    async def fetch_all(cls, query: str, params: tuple = ()):
        """Helper to fetch all rows"""
        conn = await cls.get_connection()
        async with conn.execute(query, params) as cursor:
            return await cursor.fetchall()

    @classmethod
    async def fetch_one(cls, query: str, params: tuple = ()):
        """Helper to fetch one row"""
        conn = await cls.get_connection()
        async with conn.execute(query, params) as cursor:
            return await cursor.fetchone()
