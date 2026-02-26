
import unittest
import time
from backend.middleware.rate_limiter import InMemoryRateLimiter

class TestRateLimiter(unittest.TestCase):
    def setUp(self):
        self.limiter = InMemoryRateLimiter()

    def test_basic_limit(self):
        user = "user1"
        key = "test"
        # Limit 2 per 60s
        self.assertTrue(self.limiter.is_allowed(user, key, 2, 60))
        self.assertTrue(self.limiter.is_allowed(user, key, 2, 60))
        self.assertFalse(self.limiter.is_allowed(user, key, 2, 60))

    def test_window_expiry(self):
        user = "user1"
        key = "test_window"
        # Limit 1 per 1s
        self.assertTrue(self.limiter.is_allowed(user, key, 1, 1))
        self.assertFalse(self.limiter.is_allowed(user, key, 1, 1))
        
        # Wait for window to expire
        time.sleep(1.1)
        self.assertTrue(self.limiter.is_allowed(user, key, 1, 1))

    def test_independent_users(self):
        # User 1 exceeded
        self.assertTrue(self.limiter.is_allowed("u1", "k", 1, 60))
        self.assertFalse(self.limiter.is_allowed("u1", "k", 1, 60))
        
        # User 2 allowed
        self.assertTrue(self.limiter.is_allowed("u2", "k", 1, 60))

if __name__ == "__main__":
    unittest.main()
