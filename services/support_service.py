import sqlite3
import logging
from typing import List, Dict, Optional
from backend.core.database import DatabasePool, DB_PATH

logger = logging.getLogger("API")

class SupportService:
    def __init__(self):
        self.db_path = DB_PATH
        self._initialize_table()

    def _initialize_table(self):
        """Creates the support_messages table if it doesn't exist."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS support_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
                        message TEXT NOT NULL,
                        sender TEXT NOT NULL, -- 'user' or 'support'
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        read BOOLEAN DEFAULT 0
                    )
                """)
                conn.commit()
                logger.info("Support Messages Table Initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize support_messages table: {e}")

    async def create_message(self, user_id: str, message: str, sender: str = 'user') -> Optional[Dict]:
        """Inserts a new message into the database."""
        try:
            # Using DatabasePool for async execution if possible, or direct sqlite for simplicity
            # Given DatabasePool structure in this project, we can use run_in_executor or direct connect
            # For simplicity and low volume, direct connect is fine, but let's be async-friendly
            import asyncio
            loop = asyncio.get_event_loop()
            
            def _insert():
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO support_messages (user_id, message, sender)
                        VALUES (?, ?, ?)
                    """, (user_id, message, sender))
                    msg_id = cursor.lastrowid
                    conn.commit()
                    
                    # Return the created message
                    cursor.execute("SELECT * FROM support_messages WHERE id = ?", (msg_id,))
                    row = cursor.fetchone()
                    return {
                        "id": row[0],
                        "user_id": row[1],
                        "message": row[2],
                        "sender": row[3],
                        "timestamp": row[4],
                        "read": bool(row[5])
                    }

            return await loop.run_in_executor(None, _insert)

        except Exception as e:
            logger.error(f"Error creating support message: {e}")
            try:
                with open("backend/debug_support.log", "a") as f:
                    f.write(f"Error creating message: {e}\n")
            except:
                pass
            return None

    async def get_messages(self, user_id: str, limit: int = 50) -> List[Dict]:
        """Retrieves chat history for a user."""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            
            def _fetch():
                with sqlite3.connect(self.db_path) as conn:
                    # Return dicts
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT * FROM support_messages
                        WHERE user_id = ?
                        ORDER BY timestamp ASC
                        LIMIT ?
                    """, (user_id, limit))
                    rows = cursor.fetchall()
                    return [dict(row) for row in rows]

            return await loop.run_in_executor(None, _fetch)

        except Exception as e:
            logger.error(f"Error fetching support messages: {e}")
            return []

    async def get_unread_count(self, user_id: str) -> int:
        """Counts unread messages from support for a specific user."""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            
            def _count():
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM support_messages WHERE user_id = ? AND sender = 'support' AND read = 0", (user_id,))
                    return cursor.fetchone()[0]

            return await loop.run_in_executor(None, _count)
        except Exception as e:
            logger.error(f"Error counting unread messages: {e}")
            return 0

    async def mark_messages_as_read(self, user_id: str) -> bool:
        """Marks all support messages for a user as read."""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            
            def _update():
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE support_messages SET read = 1 WHERE user_id = ? AND sender = 'support' AND read = 0", (user_id,))
                    conn.commit()
                    return True

            return await loop.run_in_executor(None, _update)
        except Exception as e:
            logger.error(f"Error marking messages as read: {e}")
            return False

    async def get_all_conversations(self) -> List[Dict]:
        """
        Retrieves a list of users who have chatted, with their last message info.
        For Admin Dashboard.
        """
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            
            def _fetch_conversations():
                with sqlite3.connect(self.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    # Get distinct users by grouping
                    # We want the latest message for each user to show preview
                    cursor.execute("""
                        SELECT user_id, message, timestamp, sender, read
                        FROM support_messages
                        WHERE id IN (
                            SELECT MAX(id)
                            FROM support_messages
                            GROUP BY user_id
                        )
                        ORDER BY timestamp DESC
                    """)
                    rows = cursor.fetchall()
                    return [dict(row) for row in rows]

            return await loop.run_in_executor(None, _fetch_conversations)

        except Exception as e:
            logger.error(f"Error fetching conversations: {e}")
            return []

support_service = SupportService()
