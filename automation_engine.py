"""
Automation Engine - Stop Loss, Take Profit, Trailing Stop

Gestione automatica delle posizioni con:
- Stop Loss (chiusura su perdita massima)
- Take Profit (chiusura su profitto target)
- Trailing Stop (protegge profitto seguendo il massimo raggiunto)
"""

import logging
import threading
from typing import Dict, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class AutomationType(Enum):
    STOP_LOSS = "SL"
    TAKE_PROFIT = "TP"
    TRAILING = "TR"


@dataclass
class PositionState:
    """Stato di una posizione con automazioni."""
    bet_id: str
    selection_id: int
    market_id: str
    entry_price: float
    stake: float
    side: str
    
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    trailing_amount: Optional[float] = None
    
    best_pnl_seen: float = 0.0
    trailing_active: bool = False
    
    sl_triggered: bool = False
    tp_triggered: bool = False
    tr_triggered: bool = False


class TrailingStopEngine:
    """Engine per trailing stop dinamico."""
    
    def __init__(self, default_trail_amount: float = 0.50):
        self.default_trail_amount = default_trail_amount
        self.positions: Dict[str, PositionState] = {}
        self.lock = threading.RLock()
    
    def add_position(self, state: PositionState):
        """Aggiunge una posizione da monitorare."""
        with self.lock:
            self.positions[state.bet_id] = state
            logger.info(f"[TRAILING] Aggiunta posizione {state.bet_id}")
    
    def remove_position(self, bet_id: str):
        """Rimuove una posizione."""
        with self.lock:
            self.positions.pop(bet_id, None)
    
    def update(self, bet_id: str, current_pnl: float) -> bool:
        """
        Aggiorna P&L e verifica se triggerare trailing stop.
        
        Returns:
            True se deve triggerare green-up
        """
        with self.lock:
            state = self.positions.get(bet_id)
            if not state or not state.trailing_amount:
                return False
            
            if current_pnl > state.best_pnl_seen:
                state.best_pnl_seen = current_pnl
                if current_pnl > 0:
                    state.trailing_active = True
                return False
            
            if state.trailing_active:
                if state.best_pnl_seen - current_pnl >= state.trailing_amount:
                    state.tr_triggered = True
                    logger.info(f"[TRAILING] TRIGGER bet_id={bet_id} best={state.best_pnl_seen:.2f} current={current_pnl:.2f}")
                    return True
            
            return False
    
    def reset(self, bet_id: str):
        """Reset trailing dopo esecuzione."""
        with self.lock:
            if bet_id in self.positions:
                self.positions[bet_id].best_pnl_seen = 0.0
                self.positions[bet_id].trailing_active = False
                self.positions[bet_id].tr_triggered = False


class SLTPEngine:
    """Engine per Stop Loss e Take Profit."""
    
    def __init__(self):
        self.positions: Dict[str, PositionState] = {}
        self.lock = threading.RLock()
    
    def set_limits(self, bet_id: str, stop_loss: Optional[float] = None, 
                   take_profit: Optional[float] = None):
        """Imposta limiti SL/TP per una posizione."""
        with self.lock:
            if bet_id in self.positions:
                if stop_loss is not None:
                    self.positions[bet_id].stop_loss = stop_loss
                if take_profit is not None:
                    self.positions[bet_id].take_profit = take_profit
                logger.info(f"[SL/TP] Set bet_id={bet_id} SL={stop_loss} TP={take_profit}")
    
    def add_position(self, state: PositionState):
        """Aggiunge posizione con limiti."""
        with self.lock:
            self.positions[state.bet_id] = state
    
    def check_stop_loss(self, bet_id: str, current_pnl: float) -> bool:
        """Verifica se SL è triggerato."""
        with self.lock:
            state = self.positions.get(bet_id)
            if not state or state.stop_loss is None:
                return False
            
            if current_pnl <= state.stop_loss:
                state.sl_triggered = True
                logger.info(f"[SL] TRIGGER bet_id={bet_id} pnl={current_pnl:.2f} limit={state.stop_loss:.2f}")
                return True
            return False
    
    def check_take_profit(self, bet_id: str, current_pnl: float) -> bool:
        """Verifica se TP è triggerato."""
        with self.lock:
            state = self.positions.get(bet_id)
            if not state or state.take_profit is None:
                return False
            
            if current_pnl >= state.take_profit:
                state.tp_triggered = True
                logger.info(f"[TP] TRIGGER bet_id={bet_id} pnl={current_pnl:.2f} target={state.take_profit:.2f}")
                return True
            return False
    
    def get_flags(self, bet_id: str) -> Dict[str, bool]:
        """Ritorna flag automazioni per UI."""
        with self.lock:
            state = self.positions.get(bet_id)
            if not state:
                return {"SL": False, "TP": False, "TR": False}
            
            return {
                "SL": state.stop_loss is not None,
                "TP": state.take_profit is not None,
                "TR": state.trailing_amount is not None
            }


class AutomationEngine:
    """Engine principale che coordina tutte le automazioni."""
    
    def __init__(self, commission: float = 4.5, on_green_up: Optional[Callable] = None):
        """
        Args:
            commission: Commissione Betfair
            on_green_up: Callback quando deve eseguire green-up
        """
        self.commission = commission
        self.on_green_up = on_green_up
        
        self.trailing_engine = TrailingStopEngine()
        self.sltp_engine = SLTPEngine()
        self.lock = threading.RLock()
    
    def add_position(self, bet_id: str, selection_id: int, market_id: str,
                     entry_price: float, stake: float, side: str,
                     stop_loss: Optional[float] = None,
                     take_profit: Optional[float] = None,
                     trailing: Optional[float] = None):
        """Aggiunge posizione con tutte le automazioni."""
        state = PositionState(
            bet_id=bet_id,
            selection_id=selection_id,
            market_id=market_id,
            entry_price=entry_price,
            stake=stake,
            side=side,
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_amount=trailing
        )
        
        self.trailing_engine.add_position(state)
        self.sltp_engine.add_position(state)
        
        logger.info(f"[AUTOMATION] Posizione aggiunta bet_id={bet_id} SL={stop_loss} TP={take_profit} TR={trailing}")
    
    def remove_position(self, bet_id: str):
        """Rimuove posizione."""
        self.trailing_engine.remove_position(bet_id)
        with self.sltp_engine.lock:
            self.sltp_engine.positions.pop(bet_id, None)
    
    def evaluate(self, bet_id: str, current_pnl: float) -> Optional[str]:
        """
        Valuta P&L e ritorna azione da eseguire.
        
        Returns:
            'GREEN_UP', 'STOP_LOSS', 'TAKE_PROFIT' o None
        """
        if self.sltp_engine.check_stop_loss(bet_id, current_pnl):
            if self.on_green_up:
                self.on_green_up(bet_id, 'STOP_LOSS')
            return 'STOP_LOSS'
        
        if self.sltp_engine.check_take_profit(bet_id, current_pnl):
            if self.on_green_up:
                self.on_green_up(bet_id, 'TAKE_PROFIT')
            return 'TAKE_PROFIT'
        
        if self.trailing_engine.update(bet_id, current_pnl):
            if self.on_green_up:
                self.on_green_up(bet_id, 'TRAILING')
            self.trailing_engine.reset(bet_id)
            return 'TRAILING'
        
        return None
    
    def get_automation_badges(self, bet_id: str) -> str:
        """Ritorna stringa badge per UI (es. 'SL TP TR')."""
        flags = self.sltp_engine.get_flags(bet_id)
        badges = []
        if flags.get('SL'):
            badges.append('SL')
        if flags.get('TP'):
            badges.append('TP')
        if flags.get('TR'):
            badges.append('TR')
        return ' '.join(badges)


class PartialGreen:
    """Gestisce green-up parziale (hedge)."""
    
    @staticmethod
    def calculate_hedge_stake(original_stake: float, original_price: float,
                               lay_price: float, hedge_percent: float = 0.5) -> float:
        """
        Calcola stake per hedge parziale.
        
        Args:
            original_stake: Stake BACK originale
            original_price: Quota BACK originale
            lay_price: Quota LAY live
            hedge_percent: Percentuale da coprire (0.5 = 50%)
            
        Returns:
            Stake LAY per hedge
        """
        full_lay_stake = (original_stake * original_price) / lay_price
        return round(full_lay_stake * hedge_percent, 2)
