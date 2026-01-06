"""
Test UI Watchdog
================
Verifica rilevamento freeze e dump thread.
"""

import pytest
import sys
import os
import time
import threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui_watchdog import UIWatchdog


class TestUIWatchdog:
    """Test watchdog freeze detection."""
    
    def test_initial_state(self):
        """Watchdog parte correttamente."""
        wd = UIWatchdog(timeout=10)
        assert wd.running is True
        
    def test_tick_updates_heartbeat(self):
        """tick() aggiorna last_tick."""
        wd = UIWatchdog(timeout=10)
        
        old_tick = wd.last_tick
        time.sleep(0.1)
        wd.tick()
        
        assert wd.last_tick > old_tick
        
    def test_thread_starts(self):
        """Thread daemon parte."""
        wd = UIWatchdog(timeout=10)
        wd.start()
        
        time.sleep(0.5)
        assert wd.thread.is_alive()
        
        wd.stop()
        
    def test_no_crash_on_freeze(self):
        """Nessun crash su freeze detection."""
        wd = UIWatchdog(timeout=1)
        wd.start()
        
        time.sleep(3)
        
        assert wd._dump_count >= 1
        wd.stop()
        
    def test_dump_threads_no_crash(self):
        """dump_threads non crasha."""
        wd = UIWatchdog(timeout=10)
        
        wd.dump_threads()
