"""
Test Safe Mode Controller
=========================
Verifica attivazione/disattivazione safe mode automatico.
"""

import pytest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from safe_mode import SafeModeController


class TestSafeMode:
    """Test safe mode activation and blocking."""
    
    def test_initial_state_inactive(self):
        """Safe mode inizialmente inattivo."""
        sm = SafeModeController(max_errors=3, window_sec=10)
        assert sm.can_auto_bet() is True
        
    def test_triggers_on_threshold(self):
        """Safe mode si attiva dopo N errori."""
        sm = SafeModeController(max_errors=3, window_sec=60)
        
        sm.record_error("TEST", "error 1")
        sm.record_error("TEST", "error 2")
        assert sm.can_auto_bet() is True
        
        sm.record_error("TEST", "error 3")
        sm.record_error("TEST", "error 4")
        sm.record_error("TEST", "error 5")
        assert sm.can_auto_bet() is False
        
    def test_can_auto_bet_blocked(self):
        """can_auto_bet ritorna False quando attivo."""
        sm = SafeModeController(max_errors=2, window_sec=60)
        
        assert sm.can_auto_bet() is True
        
        for i in range(5):
            sm.record_error("TEST", f"error {i}")
        
        assert sm.can_auto_bet() is False
        
    def test_manual_reset(self):
        """Reset manuale disattiva safe mode."""
        sm = SafeModeController(max_errors=2, window_sec=60)
        
        for i in range(5):
            sm.record_error("TEST", f"error {i}")
        assert sm.can_auto_bet() is False
        
        sm.reset()
        assert sm.can_auto_bet() is True
        
    def test_error_count_tracking(self):
        """Conta errori correttamente."""
        sm = SafeModeController(max_errors=10, window_sec=60)
        
        for i in range(4):
            sm.record_error("TEST", f"error {i}")
            
        assert sm.error_count == 4
