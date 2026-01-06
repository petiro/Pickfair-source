# ui_watchdog.py
# Se la UI si blocca → dump automatico dei thread
import threading
import time
import sys
import traceback
import logging

logger = logging.getLogger(__name__)

class UIWatchdog:
    """
    Watchdog per UI Tkinter.
    Se nessun tick UI per X secondi:
    - dump stack di TUTTI i thread
    - log errore critico
    """

    def __init__(self, timeout=15):
        self.timeout = timeout
        self.last_tick = time.time()
        self.running = True
        self._dump_count = 0

        self.thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="UIWatchdog"
        )

    def start(self):
        """Start the watchdog thread."""
        self.thread.start()
        logger.info("[WATCHDOG] Started with timeout=%ds", self.timeout)

    def stop(self):
        """Stop the watchdog."""
        self.running = False
        logger.info("[WATCHDOG] Stopped")

    def tick(self):
        """Call this from UI thread to signal it's alive."""
        self.last_tick = time.time()

    def _run(self):
        """Watchdog loop - runs in background thread."""
        while self.running:
            time.sleep(2)
            elapsed = time.time() - self.last_tick
            if elapsed > self.timeout:
                self._dump_count += 1
                logger.error("[WATCHDOG] UI FROZEN > %ds (dump #%d)", 
                           self.timeout, self._dump_count)
                self.dump_threads()
                # Reset tick to avoid continuous dumps
                self.last_tick = time.time()

    def dump_threads(self):
        """Dump stack traces of ALL threads."""
        logger.error("=" * 60)
        logger.error("[WATCHDOG] THREAD DUMP START")
        logger.error("=" * 60)
        
        frames = sys._current_frames()
        for tid, frame in frames.items():
            thread_name = "Unknown"
            for t in threading.enumerate():
                if t.ident == tid:
                    thread_name = t.name
                    break
            
            logger.error("\n--- Thread %s (id=%s) ---", thread_name, tid)
            stack = "".join(traceback.format_stack(frame))
            logger.error(stack)
        
        logger.error("=" * 60)
        logger.error("[WATCHDOG] THREAD DUMP END")
        logger.error("=" * 60)
