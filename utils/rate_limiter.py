"""Rate limiter for API calls."""
from __future__ import annotations

import time
import threading
from collections import deque
from typing import Optional


class RateLimiter:
    """Thread-safe rate limiter using sliding window."""
    
    def __init__(self, max_calls: int, period: float):
        """
        Initialize rate limiter.
        
        Args:
            max_calls: Maximum number of calls allowed in the period.
            period: Time period in seconds.
        """
        self.max_calls = max_calls
        self.period = period
        self.calls: deque[float] = deque()
        self.lock = threading.Lock()
    
    def acquire(self, block: bool = True, timeout: Optional[float] = None) -> bool:
        """
        Acquire permission to make a call.
        
        Args:
            block: If True, wait until permission is available.
            timeout: Maximum time to wait (if block=True).
        
        Returns:
            True if permission acquired, False otherwise.
        """
        with self.lock:
            now = time.time()
            
            # Remove old calls outside the window
            while self.calls and self.calls[0] < now - self.period:
                self.calls.popleft()
            
            # Check if we can make a call
            if len(self.calls) < self.max_calls:
                self.calls.append(now)
                return True
            
            if not block:
                return False
            
            # Calculate wait time
            wait_time = self.calls[0] + self.period - now
            if timeout and wait_time > timeout:
                return False
        
        # Wait outside the lock
        if wait_time > 0:
            time.sleep(wait_time)
        
        # Try again
        return self.acquire(block=False)
    
    def __call__(self, func):
        """Use as decorator."""
        def wrapper(*args, **kwargs):
            self.acquire()
            return func(*args, **kwargs)
        return wrapper


# Binance rate limiter: 1200 requests per minute
binance_rate_limiter = RateLimiter(max_calls=1200, period=60.0)
