import logging
from typing import Optional
from backend.firebase_setup import initialize_firebase
from backend.services.websocket_manager import websocket_manager
from google.cloud import firestore

logger = logging.getLogger("CreditService")
db = initialize_firebase()

class CreditService:
    """
    Centralized service for managing user credits.
    Handles deduction, balance checking, and real-time updates.
    """

    async def get_balance(self, user_id: str) -> int:
        """Get current credit balance for a user."""
        try:
            doc = db.collection("users").document(user_id).get()
            if doc.exists:
                return doc.to_dict().get("credits", 0)
            return 0
        except Exception as e:
            logger.error(f"Error fetching balance for {user_id}: {e}")
            return 0

    async def deduct_credits(self, user_id: str, amount: int, reason: str = "Service Usage") -> bool:
        """
        Deduct credits from user.
        Returns True if successful, False if insufficient funds.
        """
        if amount <= 0:
            return True

        ref = db.collection("users").document(user_id)
        
        try:
            # Transactional update to ensure atomic balance change
            @firestore.transactional
            def update_in_transaction(transaction, user_ref):
                snapshot = user_ref.get(transaction=transaction)
                if not snapshot.exists:
                    current_credits = 0
                else:
                    current_credits = snapshot.get("credits") or 0

                if current_credits < amount:
                    return False, current_credits

                new_credits = current_credits - amount
                transaction.update(user_ref, {"credits": new_credits})
                return True, new_credits

            transaction = db.transaction()
            success, new_balance = update_in_transaction(transaction, ref)

            if success:
                logger.info(f"Deducted {amount} credits from {user_id} for {reason}. New Balance: {new_balance}")
                # Emit real-time update
                await websocket_manager.emit_update(user_id, {"credits": new_balance})
                return True
            else:
                logger.warning(f"Insufficient credits for {user_id}: {new_balance} < {amount}")
                return False

        except Exception as e:
            logger.error(f"Credit deduction failed for {user_id}: {e}")
            return False

    async def refund_credits(self, user_id: str, amount: int, reason: str = "Service Refund") -> bool:
        """Refund credits to user."""
        if amount <= 0:
            return True

        try:
            ref = db.collection("users").document(user_id)
            
            # Simple increment (no strict transaction needed for refund usually, but safer)
            current = await self.get_balance(user_id)
            new_balance = current + amount
            ref.update({"credits": new_balance})
            
            logger.info(f"Refunded {amount} credits to {user_id} for {reason}. New Balance: {new_balance}")
            await websocket_manager.emit_update(user_id, {"credits": new_balance})
            return True
        except Exception as e:
            logger.error(f"Credit refund failed for {user_id}: {e}")
            return False

credit_service = CreditService()
