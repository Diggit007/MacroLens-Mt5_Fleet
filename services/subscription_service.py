import logging
import datetime
from backend.firebase_setup import initialize_firebase

logger = logging.getLogger(__name__)

# Initialize DB locally to avoid circular import with main.py
FIRESTORE_DB = initialize_firebase()

async def activate_user_subscription(user_id: str, tier: str, amount: float):
    """
    Activate user subscription after successful payment.
    Resides in a separate service to be callable by API routers and background workers.
    """
    try:
        tier_map = {
            "starter": 50,
            "standard": 1000,
            "premium": 3000,
            "enterprise": 10000
        }
        
        # Calculate Credits
        new_credits = tier_map.get(tier.lower(), 0)
        
        # Fallback: if custom amount? 
        if new_credits == 0 and amount > 0:
             # NGN estimate: 1 Credit = 50 NGN (2500/50 = 50 credits)
             new_credits = int(amount / 50)
        
        logger.info(f"Activating subscription for User {user_id}: {tier} (+{new_credits} credits)")
        
        # Update User in Firestore
        user_ref = FIRESTORE_DB.collection("users").document(user_id)
        
        # Atomic Increment behavior (Simulated with read-modify-write for now)
        doc = user_ref.get()
        current = 0
        if doc.exists:
            current = doc.to_dict().get("credits", 0)
            
        user_ref.update({
            "credits": current + new_credits,
            "tier": tier,
            "subscriptionStatus": "active",
            "lastPaymentDate": datetime.datetime.utcnow().isoformat()
        })
        
        return True
        
    except Exception as e:
        logger.error(f"Subscription Activation Failed for {user_id}: {e}")
        raise e
