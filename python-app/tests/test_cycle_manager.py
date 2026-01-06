"""
Test CycleManager
=================
Verifica gestione cicli target/stop per Follower.
"""

import pytest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cycle_manager import CycleManager


class TestCycleManager:
    """Test cycle management functionality."""
    
    def test_initial_state_inactive(self):
        """Ciclo inizialmente inattivo."""
        cm = CycleManager()
        assert cm.is_active is False
        assert cm.status == 'INACTIVE'
        
    def test_enable_activates_cycle(self):
        """Enable attiva il ciclo."""
        cm = CycleManager()
        cm.enable(1000.0, target_pct=5.0, stop_pct=3.0)
        
        assert cm.is_active is True
        assert cm.status == 'ACTIVE'
        
    def test_can_place_bet_when_active(self):
        """Permette bet quando ciclo attivo."""
        cm = CycleManager()
        cm.enable(1000.0, target_pct=5.0, stop_pct=3.0)
        
        allowed, reason = cm.can_place_bet()
        assert allowed is True
        assert reason == "CYCLE_ACTIVE"
        
    def test_can_place_bet_when_disabled(self):
        """Permette bet quando ciclo disabilitato."""
        cm = CycleManager()
        
        allowed, reason = cm.can_place_bet()
        assert allowed is True
        assert "DISABLED" in reason or "INACTIVE" in reason or "NO_CYCLE" in reason
        
    def test_target_hit_blocks_bets(self):
        """Blocca bet quando target raggiunto."""
        cm = CycleManager()
        cm.enable(100.0, target_pct=5.0, stop_pct=3.0)
        
        cm.record_bet_result(5.0, won=True)
        
        allowed, reason = cm.can_place_bet()
        assert allowed is False
        assert "TARGET" in reason.upper()
        
    def test_stop_loss_blocks_bets(self):
        """Blocca bet quando stop loss raggiunto."""
        cm = CycleManager()
        cm.enable(100.0, target_pct=5.0, stop_pct=3.0)
        
        cm.record_bet_result(-3.0, won=False)
        
        allowed, reason = cm.can_place_bet()
        assert allowed is False
        assert "STOP" in reason.upper()
        
    def test_reset_reactivates_cycle(self):
        """Reset riattiva il ciclo."""
        cm = CycleManager()
        cm.enable(100.0, target_pct=5.0, stop_pct=3.0)
        
        cm.record_bet_result(5.0, won=True)
        assert cm.can_place_bet()[0] is False
        
        cm.reset(100.0)
        
        allowed, reason = cm.can_place_bet()
        assert allowed is True
        assert reason == "CYCLE_ACTIVE"
        
    def test_stats_tracking(self):
        """Traccia statistiche correttamente."""
        cm = CycleManager()
        cm.enable(100.0, target_pct=10.0, stop_pct=5.0)
        
        cm.record_bet_result(2.0, won=True)
        cm.record_bet_result(-1.0, won=False)
        cm.record_bet_result(1.5, won=True)
        
        stats = cm.stats
        assert stats['bets_count'] == 3
        assert stats['wins_count'] == 2
        assert abs(stats['current_pnl'] - 2.5) < 0.01
        assert abs(stats['pnl_pct'] - 2.5) < 0.01
        
    def test_disable_stops_cycle(self):
        """Disable ferma il ciclo."""
        cm = CycleManager()
        cm.enable(100.0)
        assert cm.is_active is True
        
        cm.disable()
        assert cm.is_active is False
        assert cm.status == 'INACTIVE'
        
    def test_callback_on_target(self):
        """Chiama callback quando target raggiunto."""
        callback_data = {}
        
        def on_end(reason, pnl):
            callback_data['reason'] = reason
            callback_data['pnl'] = pnl
        
        cm = CycleManager(on_cycle_end=on_end)
        cm.enable(100.0, target_pct=5.0, stop_pct=3.0)
        
        cm.record_bet_result(5.0, won=True)
        
        assert 'reason' in callback_data
        assert 'TARGET' in callback_data['reason'].upper()
        assert callback_data['pnl'] == 5.0
