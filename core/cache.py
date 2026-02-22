
import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import json
from backend.config import settings

logger = logging.getLogger("Cache")

class BaseCache:
    async def get(self, key: str) -> Optional[Any]:
        raise NotImplementedError
    
    async def set(self, key: str, data: Any, ttl: int = 60):
        raise NotImplementedError
        
    async def close(self):
        pass

class MemoryCache(BaseCache):
    def __init__(self):
        self._store: Dict[str, Dict] = {}
    
    async def get(self, key: str) -> Optional[Any]:
        if key in self._store:
            item = self._store[key]
            if datetime.utcnow() < item["expires"]:
                return item["data"]
            else:
                del self._store[key]
        return None

    async def set(self, key: str, data: Any, ttl: int = 60):
        expires = datetime.utcnow() + timedelta(seconds=ttl)
        self._store[key] = {"data": data, "expires": expires}


class RedisCache(BaseCache):
    def __init__(self, url: str):
        try:
            import redis.asyncio as redis
            self.redis = redis.from_url(url, encoding="utf-8", decode_responses=True)
            logger.info(f"Redis Cache Initialized: {url}")
        except ImportError:
            logger.error("redis-py not installed. Install with: pip install redis")
            raise

    async def get(self, key: str) -> Optional[Any]:
        try:
            val = await self.redis.get(key)
            if val:
                return json.loads(val)
        except Exception as e:
            logger.error(f"Redis Get Error: {e}")
        return None

    async def set(self, key: str, data: Any, ttl: int = 60):
        try:
            val = json.dumps(data)
            await self.redis.set(key, val, ex=ttl)
        except Exception as e:
            logger.error(f"Redis Set Error: {e}")

    async def close(self):
        await self.redis.close()


# Factory
_cache_instance = None

def get_cache() -> BaseCache:
    global _cache_instance
    if _cache_instance:
        return _cache_instance
        
    if settings.USE_REDIS:
        try:
            _cache_instance = RedisCache(settings.REDIS_URL)
        except Exception as e:
            logger.error(f"Failed to init Redis, falling back to Memory: {e}")
            _cache_instance = MemoryCache()
    else:
        _cache_instance = MemoryCache()
        
    return _cache_instance

# Global Accessor
cache = get_cache()
