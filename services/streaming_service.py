import asyncio
import logging
import time
from datetime import datetime
from backend.services.websocket_manager import websocket_manager
from backend.services.metaapi_service import get_account_information, _get_credentials
from backend.firebase_setup import initialize_firebase

db = initialize_firebase()
logger = logging.getLogger(__name__)

class FleetPollingListener:
    """
    Replaces MetaAPI WebSockets. Polls the internal Windows Fleet Manager
    via our new metaapi_service wrapper.
    """
    def __init__(self, account_id, user_id):
        self.account_id = account_id
        self.user_id = user_id
        self.state = {
            "balance": 0.0,
            "equity": 0.0,
            "margin": 0.0,
            "freeMargin": 0.0,
            "leverage": 0,
            "currency": "USD",
            "profit": 0.0, 
            "positions": [],
            "status": "connecting",
            "is_real": False
        }
        self.last_accessed = time.time()
        self.last_firestore_sync = 0
        self.running = False

    async def _sync_to_firestore(self):
        """Syncs key stats to Firestore 'users/{uid}' for mobile offline fallback."""
        if not self.user_id or not db: return
        now = time.time()
        if now - self.last_firestore_sync < 60: return

        try:
            payload = {
                "balance": self.state["balance"],
                "equity": self.state["equity"],
                "margin": self.state["margin"],
                "free_margin": self.state["freeMargin"],
                "daily_pnl": self.state["profit"],
                "open_positions": len(self.state["positions"]),
                "active_positions_cache": self.state["positions"],
                "last_updated": datetime.utcnow().isoformat(),
                "is_real": self.state["is_real"]
            }
            db.collection("users").document(self.user_id).set(payload, merge=True)
            self.last_firestore_sync = now
        except Exception as e:
            logger.warning(f"Firestore Sync Error: {e}")

    async def start(self):
        self.running = True
        logger.info(f"[{self.account_id}] Starting Fleet Manager Polling Stream...")
        asyncio.create_task(self._poll_loop())

    async def _poll_loop(self):
        while self.running:
            try:
                # 1. Fetch complete account info & positions directly from our wrapper
                # This hits the Fleet Manager API
                result = await get_account_information(self.account_id)
                
                if result.get("status") == "error":
                    logger.warning(f"[{self.account_id}] Polling error: {result.get('error')}")
                    self.state["status"] = "disconnected"
                else:
                    self.state.update(result)
                    
                    # Compute profit if not provided directly
                    if self.state["balance"] > 0:
                        self.state["profit"] = self.state["equity"] - self.state["balance"]
                        
                    self.state["status"] = "streaming" # Fake streaming status for frontend
                
                # 2. Push to WebSocket clients
                if self.user_id:
                    await websocket_manager.emit_update(self.user_id, self.state)
                    await self._sync_to_firestore()
                    
            except Exception as e:
                logger.error(f"[{self.account_id}] Poll Loop Exception: {e}")
                self.state["status"] = "disconnected"
            
            # Poll every 2 seconds (Fleet manager runs locally, so fast polling is fine)
            await asyncio.sleep(2.0)

class StreamingManager:
    def __init__(self):
        self.listeners = {}
        self.default_account_id = "b2cf8a7d-2d81-477e-9bdf-3cc4dd1832df"
        self._lock = asyncio.Lock()
        self._cleanup_task = None

    async def start(self):
        if not self._cleanup_task:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("StreamingManager Cleanup Task Started.")

    async def _cleanup_loop(self):
        while True:
            await asyncio.sleep(300) # 5 minutes
            try:
                now = time.time()
                idle_ids = []
                for acc_id, listener in self.listeners.items():
                    # 30 Minutes Timeout
                    if (now - listener.last_accessed) > 1800:
                        idle_ids.append(acc_id)
                
                for acc_id in idle_ids:
                    logger.info(f"[{acc_id}] Idle for 30m. Closing stream...")
                    listener = self.listeners.pop(acc_id, None)
                    if listener:
                        listener.running = False
                        
            except Exception as e:
                logger.error(f"[StreamingManager] Cleanup Error: {e}")

    async def start_stream(self, account_id, user_id=None):
        async with self._lock:
            if account_id in self.listeners:
                listener = self.listeners[account_id]
                listener.last_accessed = time.time()
                
                if user_id and listener.user_id != user_id:
                    listener.user_id = user_id
                    try: 
                        await websocket_manager.emit_update(user_id, listener.state)
                    except: pass
                
                return listener

            logger.info(f"Starting Poll Stream for {account_id}...")
            listener = FleetPollingListener(account_id, user_id)
            await listener.start()
            self.listeners[account_id] = listener
            return listener

    async def reset_stream(self, account_id):
        logger.info(f"[{account_id}] FORCE RESETTING STREAM...")
        async with self._lock:
             listener = self.listeners.pop(account_id, None)
             if listener:
                 listener.running = False
        return True

    def get_latest_state(self, account_id):
        if account_id == "2oCCIawGhcpflqdPguRl": account_id = self.default_account_id
        if account_id in self.listeners:
            listener = self.listeners[account_id]
            listener.last_accessed = time.time()
            return listener.state
        return None

    async def stop_all(self):
        if self._cleanup_task:
            self._cleanup_task.cancel()
            
        for listener in self.listeners.values():
            listener.running = False
        self.listeners.clear()

stream_manager = StreamingManager()
