
import asyncio
import logging
import sys
from pathlib import Path

# Setup path
sys.path.append(str(Path(__file__).parent.parent))

from backend.services.event_monitor import EventMonitorService
from backend.core.database import DatabasePool

# Configure logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

async def test_run():
    print("=== STARTING QUANTITATIVE TRADER TEST ===")
    
    # 1. Initialize DB (Auto-init on first use)
    # await DatabasePool.get_connection() 
    
    # 2. Init Service
    service = EventMonitorService()
    
    # 3. Force Check (Bypass loop)
    print("\n[scan] Checking for Upcoming Events...")
    await service._check_upcoming_events()
    
    print("\n=== TEST COMPLETE ===")
    await DatabasePool.close()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test_run())
