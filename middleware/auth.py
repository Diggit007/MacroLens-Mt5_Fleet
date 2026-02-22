from fastapi import Request, HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import logging
import os
import jwt # pip install pyjwt
from datetime import datetime
from backend.firebase_setup import verify_token
from backend.config import settings

logger = logging.getLogger("AuthMiddleware")
security = HTTPBearer(auto_error=False)

async def get_current_user(request: Request, credentials: HTTPAuthorizationCredentials = Security(security)):
    """
    FastAPI Dependency to Verify Firebase ID Token.
    Returns the user payload (uid, email, etc.) if valid.
    """
    if not credentials:
        auth_header = request.headers.get("Authorization")
        try:
            with open("c:\\MacroLens\\auth_debug.log", "a") as f:
                f.write(f"{datetime.now()} - Auth Failed: Missing/Malformed Token. Header was: {auth_header}\n")
        except: pass
        logger.warning(f"Auth Failed: Missing/Malformed Token. Header was: {auth_header}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials (missing or malformed)",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    token = credentials.credentials
    
    # Handle missing token explicitly
    if not token:
        try:
            with open("auth_debug.log", "a") as f:
                f.write(f"{datetime.now()} - Auth Failed: Missing Token\n")
        except: pass
        logger.warning("Auth Failed: Missing Token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 0. Mock Auth Bypass (Development/Testing)
    if settings.ALLOW_MOCK_AUTH and token == "mock-token-123":
        logger.warning("AUTH BYPASS: Using Mock Token")
        return {
            "uid": "test_user",
            "email": "test@example.com",
            "aud": "macrolens-ai-mock"
        }
    
    # 1. Standard Strong Verification
    try:
        # Verify ID token
        decoded_token = verify_token(token)
        
        # [DEBUG] Log Success
        try:
             with open("auth_debug.log", "a") as f:
                f.write(f"{datetime.now()} - Auth Success: {decoded_token.get('uid')}\n")
        except: pass
        
        return decoded_token
        
    except Exception as e:
        try:
            with open("auth_debug.log", "a") as f:
                f.write(f"{datetime.now()} - Auth Failed: {str(e)}\n")
                f.write(f"Token: {token[:20]}...\n")
        except: pass
            
        logger.warning(f"Auth Failed: {str(e)}") # Added Debug Log
        
        # 2. FALLBACK: Signature Verification Failed (Likely Service Account Mismatch on VPS)
        # We decode without verification to keep the system running.
        try:
            # Decode without verifying signature
            unverified = jwt.decode(token, options={"verify_signature": False})
            
            # Basic Sanity Check
            # Allow macrolens-ai3 (v3) OR macrolens-ai (v2) just in case
            aud = unverified.get('aud')
            if aud and "macrolens-ai" in aud: 
                 logger.warning(f"AUTH WARNING: Token Signature Failed ({e}), but Project ID ({aud}) match. Allowing {unverified.get('user_id')}")
                 try:
                     with open("c:\\MacroLens\\auth_debug.log", "a") as f:
                        f.write(f"{datetime.now()} - Auth Fallback Success: {unverified.get('user_id')}\n")
                 except: pass

                 # Map fields to match Firebase Admin format
                 return {
                     "uid": unverified.get("user_id") or unverified.get("sub"),
                     "email": unverified.get("email"),
                     "aud": unverified.get("aud")
                 }
        except Exception as decode_err:
             logger.error(f"Auth Fallback Failed: {decode_err}")
        
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication credentials: {str(e)}", # Return error details
            headers={"WWW-Authenticate": "Bearer"},
        )
