from fastapi import APIRouter, HTTPException, Depends, Request, Query
from pydantic import BaseModel, EmailStr
from typing import Optional
import uuid
import hmac
import hashlib
import logging
import os
import json
from datetime import datetime

from backend.middleware.auth import get_current_user
from backend.services.paystack_service import paystack_service
from backend.services.telegram_service import telegram_service
from backend.services.subscription_service import activate_user_subscription
from backend.models.payment import PaymentTransaction

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payment", tags=["payment"])

class PaymentInitRequest(BaseModel):
    tier: str  # "standard", "premium", "enterprise"
    amount: float
    email: EmailStr
    callback_url: Optional[str] = None

class ManualPaymentRequest(BaseModel):
    tier: str
    amount: float
    reference: str # User provided TX Hash / Ref
    sender_name: Optional[str] = None
    email: Optional[EmailStr] = None

@router.post("/paystack/initialize")
async def initialize_paystack_payment(
    request: PaymentInitRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Initialize Paystack payment. Returns authorization_url.
    """
    try:
        user_id = current_user.get("uid")
        email = request.email or current_user.get("email")
        
        logger.info(f"Init Paystack Request: User={user_id}, Tier={request.tier}, Amount={request.amount}")

        # Generate unique reference
        reference = f"ML-{user_id[:8]}-{uuid.uuid4().hex[:12]}".upper()
        
        # Callback URL (Where Paystack redirects after payment)
        if request.callback_url:
            # Append reference to provided callback
            if "?" in request.callback_url:
                callback_url = f"{request.callback_url}&reference={reference}"
            else:
                callback_url = f"{request.callback_url}?reference={reference}"
        else:
            # Fallback to Env
            app_base_url = os.getenv("APP_BASE_URL", "https://macrolens-ai3.web.app")
            callback_url = f"{app_base_url}/verify-payment?reference={reference}"

        # Create transaction record
        transaction_id = PaymentTransaction.create_transaction(
            user_id=user_id,
            reference=reference,
            amount=request.amount,
            tier=request.tier,
            currency="NGN",
            metadata={"email": email, "tier": request.tier, "gateway": "paystack"}
        )
        
        if not transaction_id:
            raise HTTPException(status_code=500, detail="Failed to create transaction record")

        # Call Paystack
        result = await paystack_service.initialize_transaction(
            email=email,
            amount=request.amount,
            reference=reference,
            callback_url=callback_url,
            metadata={"user_id": user_id, "tier": request.tier}
        )
        
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
            
        return {
            "success": True,
            "authorization_url": result["authorization_url"],
            "reference": reference,
            "access_code": result["access_code"]
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Paystack Init Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/paystack/verify")
async def verify_paystack_payment_endpoint(
    reference: str = Query(...),
    # current_user: dict = Depends(get_current_user) # Removed dependency
):
    """
    Verify payment status after redirect. Public endpoint (uses metadata for auth).
    """
    try:
        logger.info(f"Verifying Paystack Reference: {reference}")
        
        # 1. Verify with Service
        verification = await paystack_service.verify_transaction(reference)
        
        if not verification.get("success"):
            raise HTTPException(status_code=400, detail=verification.get("message", "Verification failed"))

        # 1b. Check Idempotency (Prevent double crediting)
        existing_txn = PaymentTransaction.get_transaction(reference)
        if existing_txn and existing_txn.get("status") == "completed":
             logger.info(f"Transaction {reference} already processed. Returning success.")
             metadata = verification.get("metadata", {})
             tier = metadata.get("tier", "standard")
             return {
                "success": True, 
                "status": "success",
                "message": "Payment verified", 
                "tier": tier,
                "amount": existing_txn.get("amount", 0),
                "reference": reference
             }

        # 2. Update Transaction in DB
        PaymentTransaction.update_transaction(
            reference=reference,
            status="completed",
            order_no=reference # Paystack uses reference as order ID
        )
        
        # 3. Activate Subscription
        metadata = verification.get("metadata", {})
        tier = metadata.get("tier", "standard")
        amount = verification.get("amount", 0) # Amount paid
        user_id = metadata.get("user_id") # Get user_id from stored metadata

        if not user_id:
             logger.warning(f"No user_id in metadata for ref {reference}")
             # Try to find transaction in DB to get user_id?
             # For now, if metadata fails, we can't activate. 
             # But we ensure metadata is set in init.
             raise HTTPException(status_code=400, detail="Transaction metadata missing user_id")
        
        # Call Activation Logic
        await activate_user_subscription(user_id, tier, amount)
        
        return {
            "success": True,
            "message": "Payment verified and subscription activated",
            "tier": tier
        }
        
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Verification Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/paystack/webhook")
async def paystack_webhook(request: Request):
    """
    Handle server-to-server notifications (e.g. for recurring billing or async success)
    """
    # Verify Signature (HMAC SHA512 of body using Secret Key)
    x_paystack_signature = request.headers.get("x-paystack-signature")
    if not x_paystack_signature:
        logger.warning("Paystack Webhook: Missing Signature")
        return {"status": "error", "message": "Missing Signature"}
        
    try:
        # Get raw body bytes
        body_bytes = await request.body()
        
        # Get Secret from Service
        secret = paystack_service.secret_key
        if not secret:
            logger.error("Paystack Webhook: Secret Key not configured")
            return {"status": "error"}
            
        # Compute Hash
        hash_calc = hmac.new(secret.encode('utf-8'), body_bytes, hashlib.sha512).hexdigest()
        
        if hash_calc != x_paystack_signature:
            logger.warning("Paystack Webhook: Invalid Signature")
            return {"status": "error", "message": "Invalid Signature"}
            
        # Parse JSON from bytes
        payload = await request.json()
        event = payload.get("event")
        data = payload.get("data", {})
        
        logger.info(f"Paystack Webhook Event: {event}")
        
        if event == "charge.success":
            reference = data.get("reference")
            logger.info(f"Payment Success Webhook for {reference}")
            
            # Idempotency Check
            existing_txn = PaymentTransaction.get_transaction(reference)
            if existing_txn and existing_txn.get("status") == "completed":
                 logger.info(f"Transaction {reference} already completed. Ignoring webhook.")
                 return {"status": "ok"}
            
            # Update DB
            PaymentTransaction.update_transaction(
                reference=reference,
                status="completed",
                order_no=reference
            )
            
            # Activate Subscription
            metadata = data.get("metadata", {})
            user_id = metadata.get("user_id")
            tier = metadata.get("tier", "standard")
            amount = float(data.get("amount", 0)) / 100
            
            if user_id:
                await activate_user_subscription(user_id, tier, amount)
            else:
                logger.error(f"Webhook: No user_id in metadata for {reference}")

        return {"status": "received"}
    except Exception as e:
        logger.error(f"Webhook Error: {e}")
        return {"status": "error"}


# --- Manual Payment Endpoints ---

@router.get("/methods")
async def get_payment_methods(current_user: dict = Depends(get_current_user)):
    """
    Get available payment methods (Bank/Crypto) - Secured Endpoint
    """
    # In a real app, these could be loaded from DB or Env
    return {
        "success": True,
        "methods": {
            "bank": {
                "bankName": "OPay",
                "accountNumber": "641-683-3561",
                "accountName": "DEETECH VECTOR"
            },
            "crypto": {
                "usdt_trc20": "T..." # Placeholder or Env
            }
        }
    }

TIER_PRICES = {
    "starter": 36250.0,
    "pro": 79750.0,
    "premium": 142100.0,
    # Map legacy/alternate names if needed
    "standard": 79750.0 
}

@router.post("/manual/create")
async def create_manual_payment(
    request: ManualPaymentRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Submit a manual payment (Crypto/Transfer) for verification
    """
    try:
        user_id = current_user.get("uid")
        email = request.email or current_user.get("email")
        
        # Enforce Server-Side Pricing
        tier_key = request.tier.lower()
        verified_amount = TIER_PRICES.get(tier_key)
        
        if not verified_amount:
            # Fallback for unknown tiers or custom logic? 
            # For strict security, we should reject or require admin override.
            # Here we default to the request amount but log a warning? 
            # BETTER: Reject if not standard.
            if tier_key not in ["enterprise", "custom"]: # Enterprise might be variable
                 return HTTPException(status_code=400, detail="Invalid Plan Tier")
            verified_amount = request.amount

        # Override the request amount with the verified amount
        final_amount = verified_amount

        logger.info(f"Manual Payment Init: User={user_id}, Ref={request.reference}, Tier={request.tier}, Amount={final_amount}")

        # Check for duplicate reference
        existing = PaymentTransaction.get_transaction(request.reference)
        if existing:
            raise HTTPException(status_code=400, detail="Transaction reference already exists")

        # Create Pending Transaction
        transaction_id = PaymentTransaction.create_transaction(
            user_id=user_id,
            reference=request.reference,
            amount=final_amount,
            tier=request.tier,
            currency="USD" if request.reference.startswith("0x") else "NGN",
            metadata={
                "email": email, 
                "tier": request.tier, 
                "gateway": "manual",
                "sender_name": request.sender_name,
                "method": "crypto_transfer" if request.reference.startswith("0x") else "bank_transfer"
            }
        )
        
        if not transaction_id:
            raise HTTPException(status_code=500, detail="Failed to record transaction")

        # Update status to pending_verification
        PaymentTransaction.update_transaction(
            reference=request.reference,
            status="pending_verification"
        )

        # Notify Admin via Telegram
        await telegram_service.send_verification_request({
            "reference": request.reference,
            "amount": final_amount,
            "tier": request.tier,
            "sender_name": request.sender_name,
            "email": email
        })
        
        return {
            "success": True,
            "message": "Payment submitted for verification",
            "reference": request.reference
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Manual Payment Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/manual/approve")
async def approve_manual_payment(
    reference: str = Query(...),
    current_user: dict = Depends(get_current_user)
):
    """
    Approve a manual payment and activate subscription (Admin Only)
    """
    try:
        # Admin Check
        if current_user.get("role") != "admin":
             raise HTTPException(status_code=403, detail="Admin privileges required")

        logger.info(f"Approving Manual Payment: {reference} by Admin {current_user.get('uid')}")
        
        txn = PaymentTransaction.get_transaction(reference)
        if not txn:
            raise HTTPException(status_code=404, detail="Transaction not found")
            
        if txn.get("status") == "completed":
            return {"success": True, "message": "Already completed"}

        # Activate Subscription
        user_id = txn.get("user_id")
        tier = txn.get("tier", "standard")
        amount = txn.get("amount", 0)
        
        await activate_user_subscription(user_id, tier, amount)
        
        # Update DB Status
        PaymentTransaction.update_transaction(
            reference=reference,
            status="completed",
            order_no=f"MANUAL-{reference}"
        )
        
        return {
            "success": True,
            "message": f"Payment approved and {tier} subscription activated for user {user_id}"
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Approval Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/manual/reject")
async def reject_manual_payment(
    reference: str = Query(...),
    current_user: dict = Depends(get_current_user)
):
    """
    Reject a manual payment (Admin Only)
    """
    try:
        if current_user.get("role") != "admin":
             raise HTTPException(status_code=403, detail="Admin privileges required")
             
        PaymentTransaction.update_transaction(
            reference=reference,
            status="rejected"
        )
        return {"success": True, "message": "Payment rejected"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@router.get("/status")
async def check_payment_status(
    reference: str = Query(...),
    current_user: dict = Depends(get_current_user)
):
    """
    Check status of any payment (Paystack or Manual)
    """
    try:
        txn = PaymentTransaction.get_transaction(reference)
        if not txn:
            raise HTTPException(status_code=404, detail="Transaction not found")
        
        # Verify ownership
        if txn.get("user_id") != current_user.get("uid") and current_user.get("role") != "admin":
             raise HTTPException(status_code=403, detail="Unauthorized")

        return {
            "success": True,
            "status": txn.get("status"),
            "amount": txn.get("amount"),
            "tier": txn.get("tier"),
            "description": txn.get("plan"),
            "reference": reference,
            "created_at": txn.get("created_at")
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Status Check Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/user/history")
async def get_payment_history(
    current_user: dict = Depends(get_current_user)
):
    """
    Get all payment history for the user
    """
    try:
        user_id = current_user.get("uid")
        # Optimization: We need a method to get lists. 
        # Since we use SQLite generic helper, we might need to add a list_transactions method 
        # or use a direct query if the model supports it.
        # For now, let's assume we need to add a method to PaymentTransaction model.
        
        transactions = PaymentTransaction.get_user_transactions(user_id)
        return {"success": True, "history": transactions}

    except Exception as e:
        logger.error(f"History Error: {e}")
        return {"success": False, "history": [], "error": str(e)}
