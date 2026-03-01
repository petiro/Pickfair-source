import time
import logging

logger = logging.getLogger("CB")

class CircuitBreaker:
    def __init__(self, max_failures=3, reset_timeout=30):
        self.max_failures = max_failures
        self.reset_timeout = reset_timeout
        self.failures = 0
        self.opened_at = None

    def call(self, fn, *args, **kwargs):
        if self.is_open():
            raise RuntimeError("Circuit breaker OPEN")

        try:
            result = fn(*args, **kwargs)
            self._reset()
            return result
        except Exception:
            self._record_failure()
            raise

    def is_open(self):
        if self.opened_at is None:
            return False
        if time.time() - self.opened_at > self.reset_timeout:
            self._reset()
            return False
        return True

    def _record_failure(self):
        self.failures += 1
        logger.warning("[CB] Failure count=%s", self.failures)
        if self.failures >= self.max_failures:
            self.opened_at = time.time()
            logger.error("[CB] OPENED")

    def _reset(self):
        self.failures = 0
        self.opened_at = None