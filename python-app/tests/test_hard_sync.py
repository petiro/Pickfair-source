"""
Test HardSyncController
=======================
Verifica sincronizzazione Betfair/API-Football.
"""

import pytest
import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hard_sync import HardSyncController


class TestHardSync:
    """Test sincronizzazione hard."""
    
    def test_initial_state(self):
        """Stato iniziale corretto."""
        hs = HardSyncController()
        
        assert hs.betfair_market_status == "UNKNOWN"
        assert hs.matched is False
        assert hs.trading_allowed() is False
        
    def test_betfair_open_allows_trading(self):
        """Trading permesso solo con Betfair OPEN."""
        hs = HardSyncController()
        
        hs.on_betfair_update("1.234", "OPEN", True)
        assert hs.trading_allowed() is True
        
    def test_betfair_suspended_blocks_trading(self):
        """Betfair SUSPENDED blocca trading."""
        hs = HardSyncController()
        
        hs.on_betfair_update("1.234", "SUSPENDED", True)
        assert hs.trading_allowed() is False
        
    def test_goal_pending_detected(self):
        """Goal pending rilevato da API update."""
        hs = HardSyncController()
        
        hs.on_api_update({
            "minute": 45,
            "extra_time": 0,
            "home_goals": 1,
            "away_goals": 0,
            "status": "1H",
            "goal_detected": True,
            "client_status": "OK"
        })
        
        assert hs.is_goal_pending() is True
        
    def test_goal_confirmed_by_suspended(self):
        """Goal confermato da Betfair SUSPENDED."""
        hs = HardSyncController()
        
        hs._goal_pending = True
        hs._goal_pending_ts = time.time()
        
        hs.on_betfair_update("1.234", "SUSPENDED", True)
        
        assert hs.is_goal_pending() is False
        
    def test_ui_context_danger_zones(self):
        """UI context mostra danger correttamente."""
        hs = HardSyncController()
        
        hs.on_api_update({
            "minute": 85,
            "extra_time": 0,
            "home_goals": 0,
            "away_goals": 0,
            "status": "2H",
            "goal_detected": False,
            "client_status": "OK"
        })
        
        ctx = hs.get_ui_context()
        assert ctx["danger"] is True
        
    def test_ui_context_normal(self):
        """UI context normale senza danger."""
        hs = HardSyncController()
        
        hs.betfair_market_status = "OPEN"
        hs.on_api_update({
            "minute": 30,
            "extra_time": 0,
            "home_goals": 0,
            "away_goals": 0,
            "status": "1H",
            "goal_detected": False,
            "client_status": "OK"
        })
        
        ctx = hs.get_ui_context()
        assert ctx["danger"] is False
