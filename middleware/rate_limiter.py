
import time
from typing import List
from backend.core.cache import cache

class DistributedRateLimiter:
    """
    Rate Limiter using Shared Cache (Redis or Memory).
    """
    async def is_allowed(self, user_id: str, key: str, limit: int, window: int = 60) -> bool:
        """
        Checks if request is allowed.
        """
        identifier = f"ratelimit:{user_id}:{key}"
        now = time.time()
        
        # Get history from cache
        history = await cache.get(identifier) or []
        
        # Filter old requests
        cutoff = now - window
        valid_history = [t for t in history if t > cutoff]
        
        if len(valid_history) >= limit:
            return False
        
        # Add new request and save
        valid_history.append(now)
        await cache.set(identifier, valid_history, ttl=window)
        return True

# Global Instance
rate_limiter = DistributedRateLimiter()
