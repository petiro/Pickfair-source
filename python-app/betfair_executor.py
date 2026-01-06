# betfair_executor.py
# Executor serializzato per Betfair - TUTTE le chiamate API passano da qui
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import logging

logger = logging.getLogger(__name__)

class BetfairExecutor:
    """
    Executor serializzato per Betfair.
    - max_workers=1 → nessuna chiamata parallela
    - evita freeze UI
    - evita burst API
    - Betfair Exchange NON ama chiamate parallele sullo stesso session token
    """

    def __init__(self, max_workers=1):
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="BetfairWorker"
        )
        self._lock = threading.Lock()
        self.last_activity = time.time()
        self._job_count = 0

    def submit(self, fn, *args, **kwargs):
        """Submit a job to the executor. Returns a Future."""
        def wrapped():
            with self._lock:
                self.last_activity = time.time()
                self._job_count += 1
            job_id = self._job_count
            logger.debug("[EXECUTOR] Job #%d START: %s", job_id, fn.__name__)
            try:
                result = fn(*args, **kwargs)
                logger.debug("[EXECUTOR] Job #%d DONE: %s", job_id, fn.__name__)
                return result
            except Exception as e:
                logger.error("[EXECUTOR] Job #%d FAILED: %s - %s", job_id, fn.__name__, e)
                raise

        return self.executor.submit(wrapped)

    def idle_seconds(self):
        """Returns seconds since last activity."""
        return time.time() - self.last_activity

    def shutdown(self, wait=True):
        """Shutdown the executor."""
        self.executor.shutdown(wait=wait)


# Singleton globale
_betfair_executor = None
_executor_lock = threading.Lock()

def get_betfair_executor():
    """Get or create the singleton BetfairExecutor."""
    global _betfair_executor
    with _executor_lock:
        if _betfair_executor is None:
            _betfair_executor = BetfairExecutor(max_workers=1)
            logger.info("[EXECUTOR] BetfairExecutor initialized (max_workers=1)")
    return _betfair_executor
