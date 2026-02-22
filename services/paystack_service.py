import httpx
import os
import logging
import uuid
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class PaystackService:
    def __init__(self):
        self.secret_key = os.getenv("PAYSTACK_SECRET_KEY", "")
        self.base_url = "https://api.paystack.co"
        
        # Lazy loaded client
        self.client: Optional[httpx.AsyncClient] = None
        
        if not self.secret_key:
            logger.warning("PAYSTACK_SECRET_KEY not set in environment")

    def _get_headers(self) -> Dict:
        return {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json"
        }

    def get_client(self) -> httpx.AsyncClient:
        """Get or create the async client"""
        if self.client is None or self.client.is_closed:
             self.client = httpx.AsyncClient(timeout=30.0)
        return self.client

    async def close(self):
        """Close the underlying http client"""
        if self.client and not self.client.is_closed:
            await self.client.aclose()

    async def initialize_transaction(
        self, 
        email: str, 
        amount: float, 
        reference: str, 
        callback_url: str,
        metadata: Optional[Dict] = None
    ) -> Dict:
        """
        Initialize a Paystack transaction.
        Amount should be in MAJOR unit (e.g. NGN), converted to Kobo (x100) internally.
        """
        url = f"{self.base_url}/transaction/initialize"
        
        # Paystack expects amount in Kobo (integer)
        amount_kobo = int(amount * 100)
        
        payload = {
            "email": email,
            "amount": amount_kobo,
            "reference": reference,
            "callback_url": callback_url,
            "currency": "NGN", # Default to NGN
            "metadata": metadata or {},
            "channels": ['card', 'bank', 'ussd', 'qr', 'mobile_money', 'bank_transfer']
        }
        
        logger.info(f"Initializing Paystack Transaction: {reference} for {email}")
        
        try:
            # Get lazy client
            client = self.get_client()
            response = await client.post(url, json=payload, headers=self._get_headers())
            result = response.json()
            
            if response.status_code == 200 and result.get("status"):
                data = result.get("data", {})
                return {
                    "success": True,
                    "authorization_url": data.get("authorization_url"),
                    "access_code": data.get("access_code"),
                    "reference": data.get("reference")
                }
            else:
                logger.error(f"Paystack Init Error: {result}")
                return {
                    "success": False,
                    "error": result.get("message", "Payment initialization failed")
                }
        except Exception as e:
            logger.error(f"Paystack Connection Error: {e}")
            return {"success": False, "error": str(e)}

    async def verify_transaction(self, reference: str) -> Dict:
        """
        Verify a transaction by reference.
        """
        url = f"{self.base_url}/transaction/verify/{reference}"
        
        try:
            # Get lazy client
            client = self.get_client()
            response = await client.get(url, headers=self._get_headers())
            result = response.json()
            
            if response.status_code == 200 and result.get("status"):
                data = result.get("data", {})
                status = data.get("status")
                
                if status == "success":
                    return {
                        "success": True,
                        "status": "success",
                        "amount": float(data.get("amount", 0)) / 100, # Convert back to Major
                        "currency": data.get("currency"),
                        "customer_email": data.get("customer", {}).get("email"),
                        "metadata": data.get("metadata")
                    }
                else:
                    return {
                        "success": False, 
                        "status": status,
                        "message": data.get("gateway_response", "Transaction failed")
                    }
            else:
                return {
                    "success": False, 
                    "error": result.get("message", "Verification failed")
                }
        except Exception as e:
            logger.error(f"Paystack Verification Error: {e}")
            return {"success": False, "error": str(e)}

paystack_service = PaystackService()
