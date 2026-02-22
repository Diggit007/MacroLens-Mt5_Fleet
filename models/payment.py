import sqlite3
import json
from datetime import datetime
from typing import Dict, Optional, List
import logging

logger = logging.getLogger(__name__)

class PaymentTransaction:
    """
    Payment Transaction Model
    Stores payment records in SQLite database
    """
    
    DB_PATH = "payments.db"
    
    @staticmethod
    def init_db():
        """Initialize payment transactions table"""
        try:
            conn = sqlite3.connect(PaymentTransaction.DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS payment_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    reference TEXT UNIQUE NOT NULL,
                    order_no TEXT,
                    amount REAL NOT NULL,
                    currency TEXT DEFAULT 'NGN',
                    status TEXT DEFAULT 'pending',
                    payment_method TEXT DEFAULT 'paystack',
                    tier TEXT,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create index for faster lookups
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_transactions 
                ON payment_transactions(user_id, created_at DESC)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_reference 
                ON payment_transactions(reference)
            """)
            
            conn.commit()
            conn.close()
            logger.info("Payment database initialized successfully")
        except Exception as e:
            logger.error(f"Database initialization error: {e}")
    
    @staticmethod
    def create_transaction(
        user_id: str,
        reference: str,
        amount: float,
        tier: str,
        currency: str = "NGN",
        metadata: Optional[Dict] = None
    ) -> Optional[int]:
        """
        Create a new payment transaction record
        
        Returns:
            Transaction ID if successful, None otherwise
        """
        try:
            conn = sqlite3.connect(PaymentTransaction.DB_PATH)
            cursor = conn.cursor()
            
            metadata_json = json.dumps(metadata) if metadata else "{}"
            
            cursor.execute("""
                INSERT INTO payment_transactions 
                (user_id, reference, amount, currency, tier, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, reference, amount, currency, tier, metadata_json))
            
            transaction_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            return transaction_id
        except Exception as e:
            logger.error(f"Create transaction error: {e}")
            return None
    
    @staticmethod
    def update_transaction(
        reference: str,
        status: str,
        order_no: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> bool:
        """
        Update transaction status and details
        """
        try:
            conn = sqlite3.connect(PaymentTransaction.DB_PATH)
            cursor = conn.cursor()
            
            if metadata:
                metadata_json = json.dumps(metadata)
                cursor.execute("""
                    UPDATE payment_transactions 
                    SET status = ?, order_no = ?, metadata = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE reference = ?
                """, (status, order_no, metadata_json, reference))
            else:
                cursor.execute("""
                    UPDATE payment_transactions 
                    SET status = ?, order_no = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE reference = ?
                """, (status, order_no, reference))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Update transaction error: {e}")
            return False
    
    @staticmethod
    def get_transaction(reference: str) -> Optional[Dict]:
        """
        Get transaction by reference
        """
        try:
            conn = sqlite3.connect(PaymentTransaction.DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM payment_transactions WHERE reference = ?
            """, (reference,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return dict(row)
            return None
        except Exception as e:
            logger.error(f"Get transaction error: {e}")
            return None
    
    @staticmethod
    def get_user_transactions(user_id: str, limit: int = 10) -> List[Dict]:
        """
        Get user's payment transaction history
        """
        try:
            conn = sqlite3.connect(PaymentTransaction.DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM payment_transactions 
                WHERE user_id = ? 
                ORDER BY created_at DESC 
                LIMIT ?
            """, (user_id, limit))
            
            rows = cursor.fetchall()
            conn.close()
            
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Get user transactions error: {e}")
            return []


# Initialize database on module import
PaymentTransaction.init_db()
