"""
Test StressTestController
=========================
Verifica scenari di stress.
"""

import pytest
import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["PICKFAIR_DEBUG"] = "1"

from stress_test import StressTestController
from api_football import APIFootballClient
from hard_sync import HardSyncController


class MockStream:
    """Mock Betfair stream per test."""
    def __init__(self):
        self.paused = False
        self.pause_duration = 0
        
    def pause(self, seconds):
        self.paused = True
        self.pause_duration = seconds


class TestStressController:
    """Test stress scenarios."""
    
    @pytest.fixture
    def controller(self):
        api = APIFootballClient()
        stream = MockStream()
        sync = HardSyncController()
        return StressTestController(api, stream, sync)
        
    def test_enable_requires_debug(self, controller):
        """Enable richiede PICKFAIR_DEBUG."""
        result = controller.enable()
        assert result is True
        assert controller.enabled is True
        
    def test_api_down_sets_flag(self, controller):
        """simulate_api_down imposta flag."""
        controller.enable()
        controller.simulate_api_down()
        
        assert controller.api.force_timeout is True
        assert controller._last_scenario == "API_DOWN"
        
    def test_api_latency_sets_delay(self, controller):
        """simulate_api_latency imposta delay."""
        controller.enable()
        controller.simulate_api_latency(10)
        
        assert controller.api.forced_delay == 10
        
    def test_stream_lag_calls_pause(self, controller):
        """simulate_stream_lag chiama pause."""
        controller.enable()
        controller.simulate_stream_lag(15)
        
        time.sleep(0.5)
        assert controller.stream.paused is True
        
    def test_time_desync_sets_flag(self, controller):
        """simulate_time_desync imposta flag."""
        controller.enable()
        controller.simulate_time_desync()
        
        assert controller.hard_sync.force_desync is True
        
    def test_chaos_runs_scenario(self, controller):
        """chaos esegue scenario random."""
        controller.enable()
        controller.chaos()
        
        assert controller._last_scenario is not None
        
    def test_report_tracks_scenarios(self, controller):
        """get_report traccia scenari."""
        controller.enable()
        controller.simulate_api_down()
        controller.simulate_time_desync()
        
        report = controller.get_report()
        assert len(report["scenarios_run"]) == 2
        
    def test_disable_resets_all(self, controller):
        """disable resetta tutte le condizioni."""
        controller.enable()
        controller.simulate_api_down()
        controller.simulate_api_latency(10)
        
        controller.disable()
        
        assert controller.api.force_timeout is False
        assert controller.api.forced_delay == 0
