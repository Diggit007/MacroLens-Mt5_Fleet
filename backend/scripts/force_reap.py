import asyncio
import sys
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Setup paths (Assume script is in backend/scripts/ or similar)
BASE_DIR = Path(__file__).resolve().parent.parent # backend/
ROOT_DIR = BASE_DIR.parent # MacroLens/
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

# Load Env
load_dotenv(BASE_DIR / ".env.local")
load_dotenv(BASE_DIR / ".env")

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ForceReaper")

async def main():
    logger.info("Executing Force Reaper...")
    
    try:
        from backend.services.metaapi_service import reconcile_deployments
        
        # Passing empty list [] means "No users online" -> Undeploy ALL (except Master)
        await reconcile_deployments([])
        
        logger.info("Reaper Task Logic Completed Successfully.")
        
    except Exception as e:
        logger.error(f"Reaper Failed: {e}")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
