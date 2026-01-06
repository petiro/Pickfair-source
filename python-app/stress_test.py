"""
StressTestController
=====================
Simula condizioni di stress REALI per testing:
- API-Football down / slow
- Betfair stream lag
- Desync temporale
- Thread zombie

USO:
    Solo in DEBUG mode (PICKFAIR_DEBUG=1)
    
    stress = StressTestController(api, stream, hard_sync)
    stress.enable()
    stress.chaos()  # scenario random
"""

import os
import time
import random
import logging
import threading
from typing import Optional, Callable, List

log = logging.getLogger("StressTest")


class StressTestController:
    """
    Controller per stress testing integrato.
    
    Attivabile solo con PICKFAIR_DEBUG=1
    Zero impatto in produzione.
    """
    
    def __init__(
        self,
        api_football=None,
        betfair_stream=None,
        hard_sync=None,
        safe_mode=None
    ):
        self.api = api_football
        self.stream = betfair_stream
        self.hard_sync = hard_sync
        self.safe_mode = safe_mode
        
        self.enabled = False
        self._scenarios_run: List[str] = []
        self._last_scenario: Optional[str] = None
        
    def enable(self):
        """Abilita stress testing (solo debug)."""
        if os.environ.get("PICKFAIR_DEBUG") != "1":
            log.warning("[STRESS] Cannot enable - PICKFAIR_DEBUG not set")
            return False
            
        self.enabled = True
        log.warning("[STRESS] ⚠️ STRESS TEST ENABLED ⚠️")
        return True
        
    def disable(self):
        """Disabilita stress testing."""
        self.enabled = False
        self._reset_all()
        log.warning("[STRESS] Disabled - all conditions reset")
        
    def _reset_all(self):
        """Reset tutte le condizioni simulate."""
        if self.api:
            if hasattr(self.api, 'force_timeout'):
                self.api.force_timeout = False
            if hasattr(self.api, 'forced_delay'):
                self.api.forced_delay = 0
                
        if self.hard_sync:
            if hasattr(self.hard_sync, 'force_desync'):
                self.hard_sync.force_desync = False
                
    def simulate_api_down(self):
        """Simula API-Football completamente down."""
        if not self.enabled:
            return
            
        log.warning("[STRESS] 🔌 API-Football DOWN")
        self._last_scenario = "API_DOWN"
        self._scenarios_run.append("API_DOWN")
        
        if self.api and hasattr(self.api, 'force_timeout'):
            self.api.force_timeout = True
            
    def simulate_api_latency(self, seconds: int = 15):
        """Simula latenza API-Football."""
        if not self.enabled:
            return
            
        log.warning(f"[STRESS] ⏱️ API latency {seconds}s")
        self._last_scenario = f"API_LATENCY_{seconds}s"
        self._scenarios_run.append(self._last_scenario)
        
        if self.api and hasattr(self.api, 'forced_delay'):
            self.api.forced_delay = seconds
            
    def simulate_stream_lag(self, seconds: int = 20):
        """Simula lag Betfair stream."""
        if not self.enabled:
            return
            
        log.warning(f"[STRESS] 📡 Betfair stream lag {seconds}s")
        self._last_scenario = f"STREAM_LAG_{seconds}s"
        self._scenarios_run.append(self._last_scenario)
        
        if self.stream and hasattr(self.stream, 'pause'):
            def _pause():
                self.stream.pause(seconds)
            threading.Thread(target=_pause, daemon=True).start()
            
    def simulate_time_desync(self):
        """Simula desync tempo partita."""
        if not self.enabled:
            return
            
        log.warning("[STRESS] ⏰ Time desync forced")
        self._last_scenario = "TIME_DESYNC"
        self._scenarios_run.append("TIME_DESYNC")
        
        if self.hard_sync and hasattr(self.hard_sync, 'force_desync'):
            self.hard_sync.force_desync = True
            
    def simulate_goal_no_suspend(self):
        """Simula goal senza sospensione Betfair."""
        if not self.enabled:
            return
            
        log.warning("[STRESS] ⚽ Goal without Betfair SUSPENDED")
        self._last_scenario = "GOAL_NO_SUSPEND"
        self._scenarios_run.append("GOAL_NO_SUSPEND")
        
        if self.hard_sync:
            self.hard_sync._goal_pending = True
            self.hard_sync._goal_pending_ts = time.time()
            
    def simulate_safe_mode_trigger(self):
        """Forza attivazione safe mode."""
        if not self.enabled:
            return
            
        log.warning("[STRESS] 🛡️ Forcing safe mode")
        self._last_scenario = "SAFE_MODE_TRIGGER"
        self._scenarios_run.append("SAFE_MODE_TRIGGER")
        
        if self.safe_mode:
            for _ in range(5):
                if hasattr(self.safe_mode, 'increment_error'):
                    self.safe_mode.increment_error("STRESS_TEST")
                elif hasattr(self.safe_mode, 'record_error'):
                    self.safe_mode.record_error()
                    
    def chaos(self):
        """Scenario casuale misto."""
        if not self.enabled:
            return
            
        scenario = random.choice([
            self.simulate_api_down,
            lambda: self.simulate_api_latency(10),
            lambda: self.simulate_stream_lag(15),
            self.simulate_time_desync,
            self.simulate_goal_no_suspend
        ])
        
        scenario()
        
    def run_full_suite(self, interval: int = 30):
        """Esegue tutti gli scenari in sequenza."""
        if not self.enabled:
            return
            
        log.warning("[STRESS] 🧪 Starting FULL stress test suite")
        
        scenarios = [
            ("API_DOWN", self.simulate_api_down),
            ("API_LATENCY", lambda: self.simulate_api_latency(10)),
            ("STREAM_LAG", lambda: self.simulate_stream_lag(15)),
            ("TIME_DESYNC", self.simulate_time_desync),
            ("GOAL_NO_SUSPEND", self.simulate_goal_no_suspend),
        ]
        
        def _run():
            for name, fn in scenarios:
                log.warning(f"[STRESS] Running scenario: {name}")
                fn()
                time.sleep(interval)
                self._reset_all()
                log.warning(f"[STRESS] Scenario {name} complete, reset")
                
            log.warning("[STRESS] 🧪 Full suite COMPLETE")
            
        threading.Thread(target=_run, daemon=True, name="StressTestSuite").start()
        
    def get_report(self) -> dict:
        """Report scenari eseguiti."""
        return {
            "enabled": self.enabled,
            "scenarios_run": self._scenarios_run,
            "last_scenario": self._last_scenario,
            "total_runs": len(self._scenarios_run)
        }


def create_stress_controller(app) -> Optional[StressTestController]:
    """
    Factory per creare StressTestController con componenti app.
    
    Args:
        app: PickfairApp instance
        
    Returns:
        StressTestController o None se non in debug
    """
    if os.environ.get("PICKFAIR_DEBUG") != "1":
        return None
        
    controller = StressTestController(
        api_football=getattr(app, 'api_worker', None),
        betfair_stream=getattr(app, 'betfair_stream', None),
        hard_sync=getattr(app, 'hard_sync', None),
        safe_mode=getattr(app, 'safe_mode', None)
    )
    
    return controller
