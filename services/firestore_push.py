
import time
import logging
import asyncio
from backend.firebase_setup import initialize_firebase
from functools import partial

logger = logging.getLogger("FirestorePush")
db = initialize_firebase()

class FirestorePushManager:
    """
    Manages debounced writes to Firestore to prevent Quota Exceeded errors.
    Buffers rapid updates and only writes significant changes or heartbeat updates.
    """
    def __init__(self, db_client=None):
        self.db_client = db_client if db_client else db
        self.cache = {} # {doc_id: {'time': float, 'data': dict, 'hash': int}}
        self.min_interval = 15.0 # Min seconds between writes (Was 3.0 - reduced to prevent quota exceeded)
        self.force_threshold_pct = 0.05 # 5% change forces immediate write (Spike capture)
        self.active_users = {} # {user_id: last_seen_timestamp}
        self.hibernation_timeout = 300 # 5 minutes

    def _write_sync(self, collection: str, doc_id: str, data: dict):
        """Blocking Firestore Write"""
        # Critical Check
        if not self.db_client:
            print("!!! CRITICAL: Firebase DB Client is NONE. Cannot write. Check serviceAccountKey.json !!!")
            logger.error("Firebase DB Not Initialized")
            return

        try:
            if self.db_client:
                print(f"DEBUG: Writing Firestore {collection}/{doc_id}")
                self.db_client.collection(collection).document(doc_id).set(data, merge=True)
                print(f"DEBUG: Write Success")
        except Exception as e:
            logger.error(f"Write Failed {collection}/{doc_id}: {e}")

    async def push_update(self, user_id: str, data: dict):
        """
        Smart Push: Decides whether to write to Firestore based on throttling rules.
        """
        # 1. Hibernation Check
        # If user hasn't hit the API in X mins, stop writing database updates to save money.
        last_seen = self.active_users.get(user_id, 0)
        if time.time() - last_seen > self.hibernation_timeout:
            return # User is idle/away, don't waste writes

        cache_key = f"users/{user_id}/realtime/account_stats"
        now = time.time()
        last_entry = self.cache.get(cache_key)

        should_write = False

        if not last_entry:
            should_write = True
        else:
            time_diff = now - last_entry['time']
            
            # Rule A: Time Heartbeat (e.g. every 3 seconds)
            if time_diff >= self.min_interval:
                should_write = True
            
            # Rule B: Significant Change (Volatility)
            # Check Profit or Equity change
            elif 'equity' in data and 'equity' in last_entry['data']:
                old_eq = float(last_entry['data'].get('equity', 0))
                new_eq = float(data.get('equity', 0))
                if old_eq > 0:
                    delta_pct = abs(new_eq - old_eq) / old_eq
                    if delta_pct > self.force_threshold_pct:
                        should_write = True

        if should_write:
            # Update Cache
            self.cache[cache_key] = {'time': now, 'data': data}
            
            # Run Blocking I/O in ThreadPool (Non-blocking for Main Loop)
            loop = asyncio.get_event_loop()
            # Wrapper for Frontend Compatibility
            payload = {
                'live_account_stats': data,
                'last_updated': now
            }
            
            await loop.run_in_executor(
                None, 
                partial(self._write_sync, 'users', user_id, payload)
            )

    def touch_user(self, user_id: str):
        """Updates last seen time for a user (called by API endpoints)"""
        self.active_users[user_id] = time.time()

push_manager = FirestorePushManager()
