"""
Cycle Manager for Follower Copy Trading
========================================
Gestisce cicli di replica con target profit e stop loss.
Ferma automaticamente la replica quando si raggiungono i limiti.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Optional, Callable, Dict, Any


class CycleManager:
    """
    Gestisce cicli di money management per Copy Trading Follower.
    
    - Traccia P&L del ciclo corrente
    - Ferma replica quando target raggiunto (+X%)
    - Ferma replica quando stop loss raggiunto (-X%)
    - Permette reset manuale
    """
    
    def __init__(self, db=None, on_cycle_end: Optional[Callable[[str, float], None]] = None):
        """
        Initialize CycleManager.
        
        Args:
            db: Database instance for persistence
            on_cycle_end: Callback when cycle ends (reason, final_pnl)
        """
        self.db = db
        self.on_cycle_end = on_cycle_end
        self._lock = threading.RLock()
        
        # Cycle state (in-memory, synced with DB)
        self._active = False
        self._status = 'INACTIVE'  # INACTIVE, ACTIVE, TARGET_HIT, STOPPED
        self._start_bankroll = 0.0
        self._current_pnl = 0.0
        self._target_pct = 5.0
        self._stop_pct = 3.0
        self._bets_count = 0
        self._wins_count = 0
        self._started_at = None
        
        # Load from DB if available
        self._load_state()
    
    def _load_state(self):
        """Load cycle state from database."""
        if not self.db:
            return
            
        try:
            state = self.db.get_cycle_state()
            if state:
                self._status = state.get('status', 'INACTIVE')
                self._active = self._status == 'ACTIVE'
                self._start_bankroll = state.get('start_bankroll', 0.0)
                self._current_pnl = state.get('current_pnl', 0.0)
                self._target_pct = state.get('target_pct', 5.0)
                self._stop_pct = state.get('stop_pct', 3.0)
                self._bets_count = state.get('bets_count', 0)
                self._wins_count = state.get('wins_count', 0)
                self._started_at = state.get('started_at')
                logging.info(f"[CYCLE] Loaded state: {self._status}, P&L: {self._current_pnl:.2f}")
        except Exception as e:
            logging.warning(f"[CYCLE] Could not load state: {e}")
    
    def _save_state(self):
        """Save cycle state to database."""
        if not self.db:
            return
            
        try:
            self.db.save_cycle_state(
                status=self._status,
                start_bankroll=self._start_bankroll,
                current_pnl=self._current_pnl,
                target_pct=self._target_pct,
                stop_pct=self._stop_pct,
                bets_count=self._bets_count,
                wins_count=self._wins_count,
                started_at=self._started_at
            )
        except Exception as e:
            logging.error(f"[CYCLE] Could not save state: {e}")
    
    @property
    def is_active(self) -> bool:
        """True if cycle is active and accepting bets."""
        with self._lock:
            return self._active and self._status == 'ACTIVE'
    
    @property
    def status(self) -> str:
        """Current cycle status."""
        with self._lock:
            return self._status
    
    @property
    def stats(self) -> Dict[str, Any]:
        """Get cycle statistics for UI display."""
        with self._lock:
            pnl_pct = 0.0
            if self._start_bankroll > 0:
                pnl_pct = (self._current_pnl / self._start_bankroll) * 100
            
            return {
                'status': self._status,
                'active': self._active,
                'start_bankroll': self._start_bankroll,
                'current_pnl': self._current_pnl,
                'pnl_pct': pnl_pct,
                'target_pct': self._target_pct,
                'stop_pct': self._stop_pct,
                'bets_count': self._bets_count,
                'wins_count': self._wins_count,
                'win_rate': (self._wins_count / self._bets_count * 100) if self._bets_count > 0 else 0,
                'started_at': self._started_at
            }
    
    def can_place_bet(self) -> tuple:
        """
        Check if a bet can be placed in current cycle.
        
        Returns:
            (allowed: bool, reason: str)
        """
        with self._lock:
            if not self._active:
                return (True, "CYCLE_DISABLED")  # Cycle not enabled, allow all
            
            if self._status == 'ACTIVE':
                return (True, "CYCLE_ACTIVE")
            elif self._status == 'TARGET_HIT':
                return (False, f"TARGET_RAGGIUNTO (+{self._target_pct}%)")
            elif self._status == 'STOPPED':
                return (False, f"STOP_LOSS (-{self._stop_pct}%)")
            else:
                return (True, "CYCLE_INACTIVE")
    
    def start_cycle(self, bankroll: float, target_pct: float = 5.0, stop_pct: float = 3.0):
        """
        Start a new cycle.
        
        Args:
            bankroll: Starting bankroll for percentage calculations
            target_pct: Target profit percentage (positive)
            stop_pct: Stop loss percentage (positive, will be applied as negative)
        """
        with self._lock:
            self._active = True
            self._status = 'ACTIVE'
            self._start_bankroll = bankroll
            self._current_pnl = 0.0
            self._target_pct = abs(target_pct)
            self._stop_pct = abs(stop_pct)
            self._bets_count = 0
            self._wins_count = 0
            self._started_at = datetime.now().isoformat()
            
            self._save_state()
            
            logging.info(
                f"[CYCLE] Started: bankroll={bankroll:.2f}, "
                f"target=+{self._target_pct}%, stop=-{self._stop_pct}%"
            )
    
    def record_bet_result(self, profit: float, won: bool = None):
        """
        Record a bet result and check thresholds.
        
        Args:
            profit: P&L from the bet (positive = profit, negative = loss)
            won: True if bet won (optional, inferred from profit if None)
        """
        with self._lock:
            if not self._active or self._status != 'ACTIVE':
                return
            
            self._current_pnl += profit
            self._bets_count += 1
            
            if won is None:
                won = profit > 0
            if won:
                self._wins_count += 1
            
            # Check thresholds
            if self._start_bankroll > 0:
                pnl_pct = (self._current_pnl / self._start_bankroll) * 100
                
                if pnl_pct >= self._target_pct:
                    self._end_cycle('TARGET_HIT', f"Target +{self._target_pct}% raggiunto")
                elif pnl_pct <= -self._stop_pct:
                    self._end_cycle('STOPPED', f"Stop Loss -{self._stop_pct}% raggiunto")
                else:
                    self._save_state()
                    logging.info(f"[CYCLE] Bet recorded: profit={profit:.2f}, total P&L={self._current_pnl:.2f} ({pnl_pct:.2f}%)")
            else:
                self._save_state()
    
    def _end_cycle(self, status: str, reason: str):
        """End the current cycle (must hold lock)."""
        self._status = status
        self._save_state()
        
        logging.warning(f"[CYCLE] ENDED: {reason} | Final P&L: {self._current_pnl:.2f}")
        
        if self.on_cycle_end:
            try:
                self.on_cycle_end(reason, self._current_pnl)
            except Exception as e:
                logging.error(f"[CYCLE] Callback error: {e}")
    
    def reset(self, new_bankroll: float = None):
        """
        Reset cycle for a new round.
        
        Args:
            new_bankroll: New starting bankroll (optional, uses previous if None)
        """
        with self._lock:
            bankroll = new_bankroll if new_bankroll else self._start_bankroll
            
            logging.info(f"[CYCLE] Reset: previous P&L={self._current_pnl:.2f}, new bankroll={bankroll:.2f}")
            
            self._status = 'ACTIVE'
            self._start_bankroll = bankroll
            self._current_pnl = 0.0
            self._bets_count = 0
            self._wins_count = 0
            self._started_at = datetime.now().isoformat()
            
            self._save_state()
    
    def disable(self):
        """Disable cycle tracking (allow all bets)."""
        with self._lock:
            self._active = False
            self._status = 'INACTIVE'
            self._save_state()
            logging.info("[CYCLE] Disabled")
    
    def enable(self, bankroll: float, target_pct: float = 5.0, stop_pct: float = 3.0):
        """Enable and start a new cycle."""
        self.start_cycle(bankroll, target_pct, stop_pct)
