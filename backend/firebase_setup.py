import firebase_admin
from firebase_admin import credentials, firestore, auth
import os
import logging
from pathlib import Path

# Force native DNS resolver to avoid gRPC hangs on some Windows environments
os.environ["GRPC_DNS_RESOLVER"] = "native"

logger = logging.getLogger("Firebase")

def initialize_firebase():
    """
    Initializes Firebase Admin SDK.
    Expects 'serviceAccountKey.json' in the backend directory.
    """
    try:
        if not firebase_admin._apps:
            # Look for key in current directory
            # Modified to look in the parent directory if running from 'redundant' or subfolders
            # Logic: Try relative to this file, then parent.
            current_dir = Path(__file__).parent
            
            # Paths to check
            paths = [
                current_dir / "serviceAccountKey.json",
                current_dir.parent / "serviceAccountKey.json"
            ]
            
            cred_path = None
            for p in paths:
                if p.exists():
                    cred_path = str(p)
                    break
            
            if cred_path:
                cred = credentials.Certificate(cred_path)
                firebase_admin.initialize_app(cred)
                logger.info(f"Firebase Admin Initialized with {cred_path}")
            else:
                logger.warning(f"serviceAccountKey.json not found in search paths. Firebase will NOT work.")
                return None
                
        return firestore.client()
    except Exception as e:
        logger.error(f"Failed to initialize Firebase: {e}")
        return None

def verify_token(token: str):
    """
    Verifies a Firebase ID Token.
    Returns the decoded token (dict) if valid, or raises Exception.
    """
    return auth.verify_id_token(token)
