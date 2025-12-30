"""
Order Manager PRO - Trading Engine Livello Bet Angel.

Architettura completa:
    Live market update
          |
    Order State Machine
          |
    Profit Delta Check
          |
    Priority Decision (front/back queue)
          |
    Tick Normalize
          |
    Anti-loop Protection
          |
    replaceOrders / cancel+place
          |
    Update State

Features:
    - State Machine per ogni betId (anti-bug fantasma)
    - Profit Engine netto reale
    - Replace solo se delta profit > soglia
    - Priority front/back queue
    - Tick ladder Betfair ufficiale
    - Anti-loop protection
    - Cashout multi-selezione (green book)
    - replaceOrders con fallback cancel+place
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)

try:
    from bet_logger import get_bet_logger
    _bet_logger = get_bet_logger()
except ImportError:
    _bet_logger = None
    logger.warning("[ORDER_MANAGER] BetLogger not available")


# ==============================================================================
# CONFIGURAZIONE
# ==============================================================================

PROFIT_THRESHOLD = 0.10  # EUR - Replace solo se delta profit >= soglia
MAX_REPLACES = 5         # Max replace per ordine
MIN_INTERVAL = 0.4       # Secondi minimi tra replace
RESET_AFTER = 10.0       # Reset contatore dopo N secondi inattivita
COMMISSION = 0.045       # Commissione Betfair Italia (4.5%)


# ==============================================================================
# TICK LADDER BETFAIR (UFFICIALE)
# ==============================================================================

TICK_LADDER = [
    (1.01, 2.0, 0.01),
    (2.0, 3.0, 0.02),
    (3.0, 4.0, 0.05),
    (4.0, 6.0, 0.1),
    (6.0, 10.0, 0.2),
    (10.0, 20.0, 0.5),
    (20.0, 30.0, 1.0),
    (30.0, 50.0, 2.0),
    (50.0, 100.0, 5.0),
    (100.0, 1000.0, 10.0),
]


def get_tick_size(price: float) -> float:
    """Restituisce il tick size per un dato prezzo."""
    for low, high, tick in TICK_LADDER:
        if low <= price < high:
            return tick
    return 10.0


def normalize_price(price: float) -> float:
    """Normalizza prezzo al tick Betfair valido piu vicino."""
    if price < 1.01:
        return 1.01
    if price >= 1000.0:
        return 1000.0
    
    tick = get_tick_size(price)
    return round(round(price / tick) * tick, 2)


def next_tick_up(price: float) -> float:
    """Prossimo tick superiore."""
    tick = get_tick_size(price)
    return normalize_price(price + tick)


def next_tick_down(price: float) -> float:
    """Prossimo tick inferiore."""
    tick = get_tick_size(price)
    return normalize_price(max(1.01, price - tick))


def ticks_difference(price1: float, price2: float) -> int:
    """Calcola quanti tick di differenza tra due prezzi."""
    if price1 == price2:
        return 0
    
    low, high = min(price1, price2), max(price1, price2)
    ticks = 0
    current = low
    
    while current < high and ticks < 1000:
        current = next_tick_up(current)
        ticks += 1
    
    return ticks if price1 < price2 else -ticks


# ==============================================================================
# STATE MACHINE PER betId (ANTI-BUG FANTASMA)
# ==============================================================================

class OrderStatus(Enum):
    """Stati possibili di un ordine."""
    PLACED = "PLACED"           # Piazzato, in attesa
    PARTIAL = "PARTIAL"         # Parzialmente matchato
    MATCHED = "MATCHED"         # Completamente matchato
    HEDGING = "HEDGING"         # In fase di hedge/cashout
    CASHED_OUT = "CASHED_OUT"   # Cashout completato
    REPLACING = "REPLACING"     # In fase di replace
    LOCKED = "LOCKED"           # Bloccato (no modifiche)
    CANCELLED = "CANCELLED"     # Cancellato
    ERROR = "ERROR"             # Errore


@dataclass
class OrderState:
    """Stato completo di un ordine."""
    bet_id: str
    market_id: str = ""
    selection_id: int = 0
    side: str = "BACK"
    status: OrderStatus = OrderStatus.PLACED
    
    # Prezzi e stake
    original_price: float = 0.0
    current_price: float = 0.0
    stake: float = 0.0
    size_matched: float = 0.0
    size_remaining: float = 0.0
    
    # Profitto
    last_profit: float = 0.0
    target_profit: float = 0.0
    
    # Tracking replace
    replace_count: int = 0
    last_action_ts: float = field(default_factory=time.time)
    
    # Mapping betId (se cambiato dopo replace)
    new_bet_id: Optional[str] = None


class OrderStateManager:
    """
    Gestore stati ordini - Previene bug fantasma.
    
    Ogni decisione passa da qui:
    - No doppio cashout
    - No replace duplicati
    - No broadcast multipli
    """
    
    def __init__(self):
        self.orders: Dict[str, OrderState] = {}
    
    def get(self, bet_id: str) -> Optional[OrderState]:
        """Ottieni stato ordine (segue mapping betId)."""
        # Segui catena di mapping
        current = bet_id
        while current in self.orders and self.orders[current].new_bet_id:
            current = self.orders[current].new_bet_id
        return self.orders.get(current)
    
    def get_or_create(self, bet_id: str, **kwargs) -> OrderState:
        """Ottieni o crea stato ordine."""
        state = self.get(bet_id)
        if state is None:
            state = OrderState(bet_id=bet_id, **kwargs)
            self.orders[bet_id] = state
        return state
    
    def update(self, bet_id: str, **kwargs) -> OrderState:
        """Aggiorna stato ordine."""
        state = self.get_or_create(bet_id)
        for key, value in kwargs.items():
            if hasattr(state, key):
                setattr(state, key, value)
        return state
    
    def set_status(self, bet_id: str, status: OrderStatus) -> bool:
        """Cambia stato ordine con validazione."""
        state = self.get(bet_id)
        if state is None:
            return False
        
        # Validazione transizioni
        if state.status == OrderStatus.LOCKED:
            logger.warning(f"[STATE] {bet_id} is LOCKED, cannot change status")
            return False
        
        if state.status == OrderStatus.CASHED_OUT:
            logger.warning(f"[STATE] {bet_id} already CASHED_OUT")
            return False
        
        old_status = state.status
        state.status = status
        logger.info(f"[STATE] {bet_id}: {old_status.value} -> {status.value}")
        return True
    
    def can_modify(self, bet_id: str) -> Tuple[bool, str]:
        """Verifica se ordine puo essere modificato."""
        state = self.get(bet_id)
        if state is None:
            return False, "Ordine non trovato"
        
        if state.status in [OrderStatus.LOCKED, OrderStatus.CASHED_OUT, 
                           OrderStatus.CANCELLED, OrderStatus.MATCHED]:
            return False, f"Status: {state.status.value}"
        
        if state.status == OrderStatus.REPLACING:
            return False, "Replace gia in corso"
        
        return True, "OK"
    
    def record_replace(self, bet_id: str, new_bet_id: Optional[str] = None):
        """Registra un replace effettuato."""
        state = self.get(bet_id)
        if state:
            state.replace_count += 1
            state.last_action_ts = time.time()
            if new_bet_id and new_bet_id != bet_id:
                state.new_bet_id = new_bet_id
                # Crea nuovo stato per nuovo betId
                self.orders[new_bet_id] = OrderState(
                    bet_id=new_bet_id,
                    market_id=state.market_id,
                    selection_id=state.selection_id,
                    side=state.side,
                    status=OrderStatus.PLACED,
                    original_price=state.original_price,
                    stake=state.stake
                )
    
    def get_all_active(self) -> List[OrderState]:
        """Tutti gli ordini attivi (non chiusi)."""
        closed = [OrderStatus.CASHED_OUT, OrderStatus.CANCELLED, OrderStatus.LOCKED]
        return [o for o in self.orders.values() if o.status not in closed and o.new_bet_id is None]
    
    def cleanup_old(self, max_age_hours: int = 24):
        """Rimuovi ordini vecchi."""
        cutoff = time.time() - (max_age_hours * 3600)
        to_remove = [k for k, v in self.orders.items() if v.last_action_ts < cutoff]
        for k in to_remove:
            del self.orders[k]


# Singleton globale
ORDER_STATES = OrderStateManager()


# ==============================================================================
# PROFIT ENGINE (CALCOLO PROFITTO NETTO)
# ==============================================================================

def calc_back_profit_net(
    stake: float, 
    price: float, 
    total_stake: float, 
    commission: float = COMMISSION
) -> float:
    """
    Calcola profitto netto BACK se vince.
    
    profit = (stake * price - total_stake) * (1 - commission)
    """
    gross = stake * price - total_stake
    if gross > 0:
        return gross * (1 - commission)
    return gross


def calc_lay_profit_net(
    stake: float, 
    price: float, 
    total_liability: float, 
    commission: float = COMMISSION
) -> float:
    """
    Calcola profitto netto LAY se perde (vinciamo).
    
    profit = (stake - loss) * (1 - commission)
    """
    profit = stake  # Incassiamo lo stake se perde
    if profit > 0:
        return profit * (1 - commission)
    return profit


def calc_cashout_profit(
    back_stake: float,
    back_price: float,
    lay_price: float,
    commission: float = COMMISSION
) -> Tuple[float, float, float]:
    """
    Calcola profitto cashout per singola posizione BACK.
    
    Returns:
        (lay_stake, profit_if_wins, profit_if_loses)
    """
    if lay_price <= 1:
        return 0, 0, 0
    
    lay_stake = (back_stake * back_price) / lay_price
    
    profit_win = back_stake * (back_price - 1) - lay_stake * (lay_price - 1)
    profit_lose = lay_stake - back_stake
    
    min_profit = min(profit_win, profit_lose)
    
    if min_profit > 0:
        min_profit *= (1 - commission)
    
    return round(lay_stake, 2), round(profit_win, 2), round(profit_lose, 2)


# ==============================================================================
# PRIORITY ENGINE (FRONT / BACK OF QUEUE)
# ==============================================================================

class QueuePriority(Enum):
    """Priorita coda ordini."""
    FRONT = "FRONT"  # Quota leggermente peggiore, match piu veloce
    BACK = "BACK"    # Quota migliore, match piu lento


def apply_priority(
    best_price: float, 
    side: str, 
    priority: QueuePriority
) -> float:
    """
    Applica priorita al prezzo target.
    
    FRONT: un tick peggiore per essere matchati prima
    BACK: prezzo migliore, in coda
    
    Args:
        best_price: Miglior prezzo disponibile
        side: 'BACK' o 'LAY'
        priority: FRONT o BACK
    
    Returns:
        Prezzo target normalizzato
    """
    if priority == QueuePriority.FRONT:
        if side == 'BACK':
            # BACK: offri meno (quota piu bassa) per essere matchato prima
            return next_tick_down(best_price)
        else:
            # LAY: offri di piu (quota piu alta) per essere matchato prima
            return next_tick_up(best_price)
    
    # BACK of queue: mantieni best price
    return normalize_price(best_price)


# ==============================================================================
# PROFIT DELTA CHECK (REPLACE SOLO SE CONVIENE)
# ==============================================================================

def should_replace_profit(
    old_profit: float,
    new_profit: float,
    threshold: float = PROFIT_THRESHOLD
) -> Tuple[bool, float]:
    """
    Verifica se conviene fare replace basato su delta profit.
    
    Elimina il 90% dei replace inutili!
    
    Args:
        old_profit: Profitto attuale
        new_profit: Profitto dopo replace
        threshold: Soglia minima (default 0.10 EUR)
    
    Returns:
        (should_replace, delta_profit)
    """
    delta = new_profit - old_profit
    return delta >= threshold, round(delta, 2)


def should_replace_full(
    current_price: float,
    best_price: float,
    side: str,
    old_profit: float,
    new_profit: float,
    min_ticks: int = 1,
    profit_threshold: float = PROFIT_THRESHOLD
) -> Tuple[bool, str]:
    """
    Check completo per replace: prezzo + profitto.
    
    Returns:
        (should_replace, reason)
    """
    if current_price == best_price:
        return False, "Stesso prezzo"
    
    normalized = normalize_price(best_price)
    
    if current_price == normalized:
        return False, "Gia normalizzato"
    
    ticks = abs(ticks_difference(current_price, normalized))
    
    if ticks < min_ticks:
        return False, f"< {min_ticks} tick"
    
    # Check direzione
    if side == 'BACK' and normalized < current_price:
        return False, "BACK: quota peggiore"
    if side == 'LAY' and normalized > current_price:
        return False, "LAY: quota peggiore"
    
    # Check profitto
    should_profit, delta = should_replace_profit(old_profit, new_profit, profit_threshold)
    if not should_profit:
        return False, f"Delta profit {delta:.2f} < {profit_threshold:.2f}"
    
    return True, f"+{delta:.2f} EUR, +{ticks} tick"


# ==============================================================================
# ANTI-LOOP PROTECTION
# ==============================================================================

class ReplaceGuard:
    """Protezione anti-loop per replaceOrders."""
    
    def __init__(
        self, 
        min_interval: float = MIN_INTERVAL, 
        max_replaces: int = MAX_REPLACES,
        reset_after: float = RESET_AFTER
    ):
        self.last_replace: Dict[str, float] = {}
        self.replace_count: Dict[str, int] = {}
        self.min_interval = min_interval
        self.max_replaces = max_replaces
        self.reset_after = reset_after
    
    def can_replace(self, bet_id: str) -> Tuple[bool, str]:
        """Verifica se replace e' permesso."""
        now = time.time()
        
        last = self.last_replace.get(bet_id, 0)
        count = self.replace_count.get(bet_id, 0)
        
        # Reset se inattivo
        if now - last > self.reset_after:
            self.replace_count[bet_id] = 0
            count = 0
        
        if now - last < self.min_interval:
            return False, f"Rate limit ({self.min_interval}s)"
        
        if count >= self.max_replaces:
            return False, f"Max replaces ({self.max_replaces})"
        
        return True, "OK"
    
    def record(self, bet_id: str):
        """Registra replace."""
        self.last_replace[bet_id] = time.time()
        self.replace_count[bet_id] = self.replace_count.get(bet_id, 0) + 1
    
    def reset(self, bet_id: str = None):
        """Reset contatori."""
        if bet_id:
            self.last_replace.pop(bet_id, None)
            self.replace_count.pop(bet_id, None)
        else:
            self.last_replace.clear()
            self.replace_count.clear()


# ==============================================================================
# CASHOUT MULTI-SELEZIONE (GREEN BOOK)
# ==============================================================================

def calculate_green_book(
    positions: List[Dict],
    live_prices: Dict[int, float],
    commission: float = COMMISSION
) -> Tuple[Dict[int, Dict], float]:
    """
    Cashout multi-selezione per profitto netto uniforme (Green Book).
    
    Risolve sistema per garantire stesso profitto su tutte le selezioni.
    
    Args:
        positions: Lista posizioni [{'selectionId', 'side', 'stake', 'price'}]
        live_prices: {selectionId: live_price}
        commission: Commissione
    
    Returns:
        (hedges, guaranteed_profit)
        hedges = {selectionId: {'side': 'LAY', 'stake': X, 'price': Y}}
    """
    if not positions or not live_prices:
        return {}, 0
    
    # Calcola P&L per ogni outcome
    selection_ids = set(p['selectionId'] for p in positions)
    pnl = {sel_id: 0.0 for sel_id in selection_ids}
    
    # Per ogni selezione, calcola P&L se quella vince
    for sel_id in selection_ids:
        for pos in positions:
            stake = pos['stake']
            price = pos['price']
            side = pos['side']
            pos_sel = pos['selectionId']
            
            if pos_sel == sel_id:
                # Questa posizione e' sulla selezione vincente
                if side == 'BACK':
                    pnl[sel_id] += stake * (price - 1)
                else:  # LAY
                    pnl[sel_id] -= stake * (price - 1)
            else:
                # Questa posizione e' su altra selezione
                if side == 'BACK':
                    pnl[sel_id] -= stake  # Perdiamo stake
                else:  # LAY
                    pnl[sel_id] += stake  # Vinciamo stake
    
    # Trova P&L minimo (profitto garantito attuale)
    min_pnl = min(pnl.values())
    
    # Calcola hedge necessari per uniformare
    hedges = {}
    
    for sel_id in selection_ids:
        live_price = live_prices.get(sel_id)
        if not live_price or live_price <= 1:
            continue
        
        excess_pnl = pnl[sel_id] - min_pnl
        
        if excess_pnl > 0.01:  # Soglia minima
            # Dobbiamo ridurre il P&L su questa selezione
            # Piazziamo LAY per ridurre il guadagno se vince
            hedge_stake = excess_pnl / (live_price - 1)
            
            hedges[sel_id] = {
                'selectionId': sel_id,
                'side': 'LAY',
                'stake': round(hedge_stake, 2),
                'price': live_price,
                'reducesProfit': round(excess_pnl, 2)
            }
    
    # Profitto garantito netto
    guaranteed = min_pnl * (1 - commission) if min_pnl > 0 else min_pnl
    
    logger.info(f"[GREEN BOOK] P&L by selection: {pnl}")
    logger.info(f"[GREEN BOOK] Guaranteed profit: {guaranteed:.2f}, Hedges: {len(hedges)}")
    
    return hedges, round(guaranteed, 2)


# ==============================================================================
# ORDER MANAGER PRO
# ==============================================================================

class OrderManagerPro:
    """
    Gestore ordini PRO con tutte le funzionalita avanzate.
    
    Flow completo:
    1. Live market update
    2. Order state machine check
    3. Profit delta check
    4. Priority decision
    5. Tick normalize
    6. Anti-loop check
    7. replaceOrders / cancel+place
    8. Update state
    """
    
    def __init__(
        self, 
        betfair_client,
        profit_threshold: float = PROFIT_THRESHOLD,
        min_interval: float = MIN_INTERVAL,
        max_replaces: int = MAX_REPLACES
    ):
        self.client = betfair_client
        self.profit_threshold = profit_threshold
        self.state_manager = ORDER_STATES
        self.guard = ReplaceGuard(min_interval, max_replaces)
        self.bet_id_map: Dict[str, str] = {}
        self.history: List[Dict] = []
    
    def get_current_bet_id(self, original: str) -> str:
        """Segui catena mapping betId."""
        current = original
        while current in self.bet_id_map:
            current = self.bet_id_map[current]
        return current
    
    def smart_replace(
        self,
        bet_id: str,
        market_id: str,
        selection_id: int,
        side: str,
        current_price: float,
        best_price: float,
        size_remaining: float,
        stake: float,
        total_stake: float,
        priority: QueuePriority = QueuePriority.BACK,
        min_ticks: int = 1
    ) -> Dict:
        """
        Replace intelligente con tutti i check.
        
        Flow:
        1. State machine check
        2. Profit delta check
        3. Priority application
        4. Tick normalize
        5. Anti-loop check
        6. replaceOrders
        7. Fallback cancel+place
        8. Update state
        """
        result = {
            'success': False,
            'action': None,
            'betId': bet_id,
            'newBetId': None,
            'newPrice': None,
            'deltaProfit': 0,
            'reason': None
        }
        
        current_bet_id = self.get_current_bet_id(bet_id)
        state = self.state_manager.get_or_create(
            current_bet_id,
            market_id=market_id,
            selection_id=selection_id,
            side=side,
            original_price=current_price,
            current_price=current_price,
            stake=stake
        )
        
        # 1. State machine check
        can_modify, reason = self.state_manager.can_modify(current_bet_id)
        if not can_modify:
            result['reason'] = f"State: {reason}"
            return result
        
        # 2. Applica priority
        target_price = apply_priority(best_price, side, priority)
        target_price = normalize_price(target_price)
        result['newPrice'] = target_price
        
        # 3. Calcola profitti
        old_profit = calc_back_profit_net(stake, current_price, total_stake) if side == 'BACK' else 0
        new_profit = calc_back_profit_net(stake, target_price, total_stake) if side == 'BACK' else 0
        
        # 4. Profit delta check
        should, reason = should_replace_full(
            current_price, target_price, side,
            old_profit, new_profit,
            min_ticks, self.profit_threshold
        )
        
        if not should:
            result['reason'] = reason
            return result
        
        result['deltaProfit'] = round(new_profit - old_profit, 2)
        
        # 5. Anti-loop check
        can_replace, guard_reason = self.guard.can_replace(current_bet_id)
        if not can_replace:
            result['reason'] = f"Guard: {guard_reason}"
            return result
        
        # 6. Set status REPLACING
        self.state_manager.set_status(current_bet_id, OrderStatus.REPLACING)
        
        logger.info(f"[REPLACE PRO] {side} {current_bet_id}: {current_price} -> {target_price} (delta +{result['deltaProfit']})")
        
        # 7. Try replaceOrders
        try:
            response = self.client.replace_orders(
                market_id=market_id,
                bet_id=current_bet_id,
                new_price=target_price
            )
            
            if response.get('status') == 'SUCCESS':
                self.guard.record(current_bet_id)
                
                reports = response.get('instructionReports', [])
                new_bet_id = None
                if reports:
                    new_bet_id = reports[0].get('newBetId')
                    if new_bet_id and new_bet_id != current_bet_id:
                        self.bet_id_map[current_bet_id] = new_bet_id
                        self.state_manager.record_replace(current_bet_id, new_bet_id)
                        result['newBetId'] = new_bet_id
                
                # Update state
                self.state_manager.update(
                    new_bet_id or current_bet_id,
                    current_price=target_price,
                    last_profit=new_profit,
                    status=OrderStatus.PLACED
                )
                
                result['success'] = True
                result['action'] = 'REPLACE'
                result['reason'] = 'SUCCESS'
                
                if _bet_logger:
                    _bet_logger.log_order_replaced(
                        market_id=market_id,
                        selection_id=str(selection_id),
                        side=side,
                        old_stake=stake,
                        old_price=current_price,
                        new_stake=stake,
                        new_price=target_price,
                        bet_id=current_bet_id,
                        new_bet_id=new_bet_id
                    )
                
                self._record(result)
                return result
            
            logger.warning(f"[REPLACE PRO] Failed: {response.get('status')}")
            
        except Exception as e:
            logger.error(f"[REPLACE PRO] Exception: {e}")
        
        # 8. Fallback cancel+place
        self.state_manager.set_status(current_bet_id, OrderStatus.PLACED)
        return self._fallback(
            market_id, selection_id, side, 
            target_price, size_remaining, current_bet_id
        )
    
    def _fallback(
        self,
        market_id: str,
        selection_id: int,
        side: str,
        price: float,
        size: float,
        original_bet_id: str
    ) -> Dict:
        """Fallback cancel + place."""
        result = {
            'success': False,
            'action': 'CANCEL_PLACE',
            'betId': original_bet_id,
            'newBetId': None,
            'newPrice': price,
            'reason': None
        }
        
        try:
            # Cancel
            cancel = self.client.cancel_orders(market_id, [original_bet_id])
            if cancel.get('status') != 'SUCCESS':
                result['reason'] = f"Cancel failed: {cancel.get('status')}"
                return result
            
            # Place
            place = self.client.place_bet(market_id, selection_id, side, price, round(size, 2))
            
            if place.get('status') == 'SUCCESS':
                new_bet_id = place.get('betId')
                if new_bet_id:
                    self.bet_id_map[original_bet_id] = new_bet_id
                    self.state_manager.record_replace(original_bet_id, new_bet_id)
                    result['newBetId'] = new_bet_id
                
                self.guard.record(original_bet_id)
                result['success'] = True
                result['reason'] = 'FALLBACK_SUCCESS'
                
                logger.info(f"[FALLBACK] {original_bet_id} -> {new_bet_id}")
            else:
                result['reason'] = f"Place failed: {place.get('status')}"
            
        except Exception as e:
            result['reason'] = f"Exception: {e}"
            logger.error(f"[FALLBACK] Error: {e}")
        
        self._record(result)
        return result
    
    def green_book_cashout(
        self,
        market_id: str,
        positions: List[Dict],
        live_prices: Dict[int, float]
    ) -> Dict:
        """
        Esegue cashout green book (profitto uniforme).
        
        Args:
            market_id: ID mercato
            positions: Lista posizioni aperte
            live_prices: Quote live {selectionId: price}
        
        Returns:
            Dict con risultato e profitto garantito
        """
        result = {
            'success': False,
            'hedges': [],
            'guaranteedProfit': 0,
            'errors': []
        }
        
        hedges, guaranteed = calculate_green_book(positions, live_prices)
        result['guaranteedProfit'] = guaranteed
        
        for sel_id, hedge in hedges.items():
            try:
                place = self.client.place_bet(
                    market_id=market_id,
                    selection_id=sel_id,
                    side=hedge['side'],
                    price=hedge['price'],
                    size=hedge['stake']
                )
                
                if place.get('status') == 'SUCCESS':
                    result['hedges'].append({
                        **hedge,
                        'betId': place.get('betId'),
                        'status': 'SUCCESS'
                    })
                else:
                    result['errors'].append({
                        'selectionId': sel_id,
                        'error': place.get('status')
                    })
                    
            except Exception as e:
                result['errors'].append({
                    'selectionId': sel_id,
                    'error': str(e)
                })
        
        result['success'] = len(result['errors']) == 0
        
        logger.info(f"[GREEN BOOK] Executed {len(result['hedges'])} hedges, profit={guaranteed:.2f}")
        
        return result
    
    def _record(self, result: Dict):
        """Registra nella history."""
        self.history.append({
            'timestamp': time.time(),
            **result
        })
        if len(self.history) > 100:
            self.history = self.history[-100:]
    
    def get_history(self, limit: int = 20) -> List[Dict]:
        """Ultimi N record."""
        return self.history[-limit:]
    
    def reset(self):
        """Reset completo."""
        self.guard.reset()
        self.bet_id_map.clear()
        self.history.clear()


# ==============================================================================
# BATCH OPERATIONS
# ==============================================================================

def batch_smart_replace(
    manager: OrderManagerPro,
    orders: List[Dict],
    live_prices: Dict[int, Dict],
    priority: QueuePriority = QueuePriority.BACK,
    min_ticks: int = 1
) -> List[Dict]:
    """
    Batch replace intelligente per multipli ordini.
    
    Args:
        manager: Istanza OrderManagerPro
        orders: Lista ordini
        live_prices: {selectionId: {'back': X, 'lay': Y}}
        priority: Front/Back queue
        min_ticks: Tick minimi
    
    Returns:
        Lista risultati
    """
    results = []
    
    for order in orders:
        sel_id = order.get('selectionId')
        side = order.get('side', 'BACK')
        
        prices = live_prices.get(sel_id, {})
        best = prices.get('back' if side == 'BACK' else 'lay')
        
        if not best:
            results.append({'success': False, 'reason': 'No live price'})
            continue
        
        result = manager.smart_replace(
            bet_id=order.get('betId'),
            market_id=order.get('marketId'),
            selection_id=sel_id,
            side=side,
            current_price=order.get('price', 0),
            best_price=best,
            size_remaining=order.get('sizeRemaining', 0),
            stake=order.get('stake', 0),
            total_stake=order.get('totalStake', order.get('stake', 0)),
            priority=priority,
            min_ticks=min_ticks
        )
        results.append(result)
    
    return results
