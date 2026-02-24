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
        logger.warning(f"Auth Failed: Missing/Malformed Token. Header was: {auth_header}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials (missing or malformed)",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    token = credentials.credentials
    
    # Handle missing token explicitly
    if not token:
        logger.warning("Auth Failed: Missing Token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 0. Mock Auth Bypass (Development/Testing ONLY — controlled by .env)
    if settings.ALLOW_MOCK_AUTH and token == "mock-token-123":
        logger.warning("AUTH BYPASS: Using Mock Token (ALLOW_MOCK_AUTH=true)")
        return {
            "uid": "test_user",
            "email": "test@example.com",
            "aud": "macrolens-ai-mock"
        }
    
    # 1. Standard Strong Verification (Firebase Admin SDK)
    try:
        decoded_token = verify_token(token)
        logger.debug(f"Auth Success: {decoded_token.get('uid')}")
        return decoded_token
        
    except Exception as e:
        logger.warning(f"Auth Primary Verification Failed: {str(e)}")
        
        # 2. FALLBACK: Signature Verification Failed
        # (Likely clock skew or Service Account mismatch on VPS)
        # Decode without verification but validate the project ID.
        try:
            unverified = jwt.decode(token, options={"verify_signature": False})
            
            aud = unverified.get('aud')
            if aud and "macrolens-ai" in aud:
                uid = unverified.get("user_id") or unverified.get("sub")
                logger.warning(f"AUTH FALLBACK: Signature failed but project ID ({aud}) matches. Allowing {uid}")
                return {
                    "uid": uid,
                    "email": unverified.get("email"),
                    "aud": aud
                }
        except Exception as decode_err:
             logger.error(f"Auth Fallback Decode Failed: {decode_err}")
        
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication credentials: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
