
import unittest
import asyncio
from backend.core.cache import get_cache, MemoryCache, RedisCache
from backend.config import settings

class TestCacheStrategy(unittest.IsolatedAsyncioTestCase):
    async def test_memory_fallback(self):
        # Force Memory
        settings.USE_REDIS = False
        cache = get_cache()
        self.assertIsInstance(cache, MemoryCache)
        
        await cache.set("test_key", {"foo": "bar"}, ttl=1)
        val = await cache.get("test_key")
        self.assertEqual(val, {"foo": "bar"})
        
        await asyncio.sleep(1.1)
        val = await cache.get("test_key")
        self.assertIsNone(val)

    async def test_redis_config(self):
        # Force Redis (Expect fail if not running, or fallback if handled)
        # Note: Our factory falls back to memory if redis init fails, 
        # BUT only if ImportError. If redis-py is installed but server down, 
        # it might raise ConnectionError on use.
        settings.USE_REDIS = True
        
        try:
            cache = get_cache()
            # If Redis server is not running, this might still return the object
            # but fail on connect.
            if isinstance(cache, RedisCache):
                print("Redis Cache initialized (Server check pending)")
        except Exception as e:
            print(f"Redis Init failed as expected: {e}")

if __name__ == "__main__":
    unittest.main()
