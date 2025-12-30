"""
Order Manager - Auto-Follow Best Price con replaceOrders intelligente.

Architettura:
    Live Odds -> Auto-Follow Engine -> Tick Ladder Normalize
    -> Anti-loop Protection -> replaceOrders() -> fallback cancel+place

Features:
    - Tick ladder Betfair ufficiale
    - Anti-loop protection (rate limit + max replaces)
    - Auto-follow best price (BACK/LAY)
    - replaceOrders con fallback cancel+place
    - Tracking betId cambiati
"""

import time
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


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


def normalize_price(price: float) -> float:
    """
    Normalizza prezzo al tick Betfair valido piu vicino.
    
    Args:
        price: Prezzo da normalizzare
    
    Returns:
        Prezzo normalizzato al tick ladder
    """
    if price < 1.01:
        return 1.01
    if price >= 1000.0:
        return 1000.0
    
    for low, high, tick in TICK_LADDER:
        if low <= price < high:
            normalized = round(round(price / tick) * tick, 2)
            return max(low, min(normalized, high - tick))
    
    return round(price, 2)


def get_tick_size(price: float) -> float:
    """Restituisce il tick size per un dato prezzo."""
    for low, high, tick in TICK_LADDER:
        if low <= price < high:
            return tick
    return 0.01


def next_tick_up(price: float) -> float:
    """Prossimo tick superiore."""
    tick = get_tick_size(price)
    return normalize_price(price + tick)


def next_tick_down(price: float) -> float:
    """Prossimo tick inferiore."""
    tick = get_tick_size(price)
    return normalize_price(price - tick)


def ticks_difference(price1: float, price2: float) -> int:
    """Calcola quanti tick di differenza tra due prezzi."""
    if price1 == price2:
        return 0
    
    low, high = min(price1, price2), max(price1, price2)
    ticks = 0
    current = low
    
    while current < high:
        current = next_tick_up(current)
        ticks += 1
        if ticks > 1000:
            break
    
    return ticks if price1 < price2 else -ticks


# ==============================================================================
# ANTI-LOOP PROTECTION
# ==============================================================================

class ReplaceGuard:
    """
    Protezione anti-loop per replaceOrders.
    
    Previene:
    - Spam di replace troppo frequenti
    - Loop infiniti di modifiche
    - Ban da rate limit Betfair
    """
    
    def __init__(self, min_interval: float = 0.4, max_replaces: int = 5, reset_after: float = 10.0):
        """
        Args:
            min_interval: Secondi minimi tra replace (default 0.4s)
            max_replaces: Numero massimo replace per ordine (default 5)
            reset_after: Reset contatore dopo N secondi di inattivita (default 10s)
        """
        self.last_replace: Dict[str, float] = {}
        self.replace_count: Dict[str, int] = {}
        self.min_interval = min_interval
        self.max_replaces = max_replaces
        self.reset_after = reset_after
    
    def can_replace(self, bet_id: str) -> Tuple[bool, str]:
        """
        Verifica se e' possibile fare replace.
        
        Returns:
            (allowed, reason)
        """
        now = time.time()
        
        last = self.last_replace.get(bet_id, 0)
        count = self.replace_count.get(bet_id, 0)
        
        # Reset contatore se inattivo da troppo tempo
        if now - last > self.reset_after:
            self.replace_count[bet_id] = 0
            count = 0
        
        # Check intervallo minimo
        if now - last < self.min_interval:
            return False, f"Rate limit ({self.min_interval}s)"
        
        # Check max replaces
        if count >= self.max_replaces:
            return False, f"Max replaces ({self.max_replaces})"
        
        return True, "OK"
    
    def record_replace(self, bet_id: str):
        """Registra un replace effettuato."""
        now = time.time()
        self.last_replace[bet_id] = now
        self.replace_count[bet_id] = self.replace_count.get(bet_id, 0) + 1
    
    def reset(self, bet_id: str):
        """Reset contatori per un betId."""
        self.last_replace.pop(bet_id, None)
        self.replace_count.pop(bet_id, None)
    
    def reset_all(self):
        """Reset tutti i contatori."""
        self.last_replace.clear()
        self.replace_count.clear()
    
    def get_stats(self, bet_id: str) -> Dict:
        """Statistiche per un betId."""
        now = time.time()
        return {
            'betId': bet_id,
            'replaceCount': self.replace_count.get(bet_id, 0),
            'lastReplace': self.last_replace.get(bet_id, 0),
            'secondsSinceLast': now - self.last_replace.get(bet_id, 0),
            'canReplace': self.can_replace(bet_id)[0]
        }


# ==============================================================================
# AUTO-FOLLOW LOGIC
# ==============================================================================

def should_replace(
    current_price: float,
    best_price: float,
    side: str,
    min_ticks: int = 1
) -> Tuple[bool, str]:
    """
    Determina se conviene fare replace per seguire best price.
    
    BACK -> segue miglior quota piu ALTA (vogliamo piu profitto)
    LAY  -> segue miglior quota piu BASSA (vogliamo meno liability)
    
    Args:
        current_price: Quota attuale dell'ordine
        best_price: Miglior quota disponibile
        side: 'BACK' o 'LAY'
        min_ticks: Differenza minima in tick per triggerare replace
    
    Returns:
        (should_replace, reason)
    """
    if current_price == best_price:
        return False, "Stesso prezzo"
    
    normalized_best = normalize_price(best_price)
    
    if current_price == normalized_best:
        return False, "Gia al best price normalizzato"
    
    ticks = abs(ticks_difference(current_price, normalized_best))
    
    if ticks < min_ticks:
        return False, f"Differenza < {min_ticks} tick"
    
    if side == 'BACK':
        if normalized_best > current_price:
            return True, f"BACK: quota migliorata +{ticks} tick"
        else:
            return False, "BACK: nuova quota peggiore"
    else:
        if normalized_best < current_price:
            return True, f"LAY: quota migliorata -{ticks} tick"
        else:
            return False, "LAY: nuova quota peggiore"


# ==============================================================================
# ORDER MANAGER
# ==============================================================================

class OrderManager:
    """
    Gestore ordini intelligente con auto-follow e replace.
    
    Features:
    - Auto-follow best price
    - replaceOrders con tick ladder
    - Fallback cancel+place
    - Anti-loop protection
    - Tracking betId
    """
    
    def __init__(self, betfair_client, min_interval: float = 0.4, max_replaces: int = 5):
        """
        Args:
            betfair_client: Istanza BetfairClient
            min_interval: Intervallo minimo tra replace (default 0.4s)
            max_replaces: Max replace per ordine (default 5)
        """
        self.client = betfair_client
        self.guard = ReplaceGuard(min_interval, max_replaces)
        self.bet_id_map: Dict[str, str] = {}  # old_id -> new_id
        self.order_history: List[Dict] = []
    
    def get_current_bet_id(self, original_bet_id: str) -> str:
        """Restituisce il betId attuale (potrebbe essere cambiato dopo replace)."""
        current = original_bet_id
        while current in self.bet_id_map:
            current = self.bet_id_map[current]
        return current
    
    def smart_update_order(
        self,
        bet_id: str,
        market_id: str,
        selection_id: int,
        side: str,
        current_price: float,
        best_price: float,
        size_remaining: float,
        min_ticks: int = 1
    ) -> Dict:
        """
        Aggiorna ordine intelligentemente per seguire best price.
        
        Flow:
        1. Normalizza prezzo al tick ladder
        2. Verifica se conviene replace
        3. Check anti-loop protection
        4. Try replaceOrders
        5. Fallback cancel+place se fallisce
        
        Args:
            bet_id: ID ordine da modificare
            market_id: ID mercato
            selection_id: ID selezione
            side: 'BACK' o 'LAY'
            current_price: Quota attuale ordine
            best_price: Miglior quota disponibile
            size_remaining: Stake rimanente non matchato
            min_ticks: Tick minimi per triggerare (default 1)
        
        Returns:
            Dict con risultato operazione
        """
        result = {
            'success': False,
            'action': None,
            'originalBetId': bet_id,
            'newBetId': None,
            'newPrice': None,
            'reason': None
        }
        
        # Usa betId corrente (potrebbe essere cambiato)
        current_bet_id = self.get_current_bet_id(bet_id)
        
        # Normalizza prezzo
        normalized_price = normalize_price(best_price)
        result['newPrice'] = normalized_price
        
        # Check se conviene replace
        should, reason = should_replace(current_price, normalized_price, side, min_ticks)
        if not should:
            result['reason'] = reason
            return result
        
        # Check anti-loop
        can, guard_reason = self.guard.can_replace(current_bet_id)
        if not can:
            result['reason'] = f"Guard: {guard_reason}"
            return result
        
        logger.info(f"[ORDER_MGR] {side} {current_bet_id}: {current_price} -> {normalized_price} ({reason})")
        
        # Try replaceOrders
        try:
            response = self.client.replace_orders(
                market_id=market_id,
                bet_id=current_bet_id,
                new_price=normalized_price
            )
            
            if response.get('status') == 'SUCCESS':
                self.guard.record_replace(current_bet_id)
                
                # Check nuovo betId
                reports = response.get('instructionReports', [])
                if reports:
                    new_bet_id = reports[0].get('newBetId')
                    if new_bet_id and new_bet_id != current_bet_id:
                        self.bet_id_map[current_bet_id] = new_bet_id
                        result['newBetId'] = new_bet_id
                        logger.info(f"[ORDER_MGR] BetId changed: {current_bet_id} -> {new_bet_id}")
                    else:
                        result['newBetId'] = current_bet_id
                
                result['success'] = True
                result['action'] = 'REPLACE'
                result['reason'] = 'SUCCESS'
                
                self._record_history('REPLACE', result)
                return result
            
            # Replace fallito, prova fallback
            error_msg = response.get('status', 'UNKNOWN')
            logger.warning(f"[ORDER_MGR] Replace failed: {error_msg}")
            
        except Exception as e:
            logger.error(f"[ORDER_MGR] Replace exception: {e}")
        
        # Fallback: cancel + place
        return self._fallback_cancel_place(
            market_id=market_id,
            selection_id=selection_id,
            side=side,
            price=normalized_price,
            size=size_remaining,
            original_bet_id=current_bet_id
        )
    
    def _fallback_cancel_place(
        self,
        market_id: str,
        selection_id: int,
        side: str,
        price: float,
        size: float,
        original_bet_id: str
    ) -> Dict:
        """
        Fallback: cancella ordine e piazza nuovo.
        
        Usato quando replaceOrders fallisce.
        """
        result = {
            'success': False,
            'action': 'CANCEL_PLACE',
            'originalBetId': original_bet_id,
            'newBetId': None,
            'newPrice': price,
            'reason': None
        }
        
        logger.info(f"[ORDER_MGR] Fallback cancel+place for {original_bet_id}")
        
        try:
            # Cancel
            cancel_result = self.client.cancel_orders(market_id, [original_bet_id])
            
            if cancel_result.get('status') != 'SUCCESS':
                result['reason'] = f"Cancel failed: {cancel_result.get('status')}"
                return result
            
            # Place nuovo ordine
            place_result = self.client.place_bet(
                market_id=market_id,
                selection_id=selection_id,
                side=side,
                price=price,
                size=round(size, 2)
            )
            
            if place_result.get('status') == 'SUCCESS':
                new_bet_id = place_result.get('betId')
                if new_bet_id:
                    self.bet_id_map[original_bet_id] = new_bet_id
                    result['newBetId'] = new_bet_id
                
                result['success'] = True
                result['reason'] = 'FALLBACK_SUCCESS'
                
                self.guard.record_replace(original_bet_id)
                self._record_history('CANCEL_PLACE', result)
                
                logger.info(f"[ORDER_MGR] Fallback success: {original_bet_id} -> {new_bet_id}")
            else:
                result['reason'] = f"Place failed: {place_result.get('status')}"
            
        except Exception as e:
            result['reason'] = f"Fallback exception: {e}"
            logger.error(f"[ORDER_MGR] Fallback error: {e}")
        
        return result
    
    def _record_history(self, action: str, result: Dict):
        """Registra operazione nella history."""
        self.order_history.append({
            'timestamp': time.time(),
            'action': action,
            **result
        })
        
        # Mantieni solo ultime 100 operazioni
        if len(self.order_history) > 100:
            self.order_history = self.order_history[-100:]
    
    def get_history(self, limit: int = 20) -> List[Dict]:
        """Ultimi N record della history."""
        return self.order_history[-limit:]
    
    def reset(self):
        """Reset completo del manager."""
        self.guard.reset_all()
        self.bet_id_map.clear()
        self.order_history.clear()


# ==============================================================================
# BATCH UPDATE
# ==============================================================================

def batch_follow_orders(
    order_manager: OrderManager,
    orders: List[Dict],
    live_prices: Dict[int, Dict],
    min_ticks: int = 1
) -> List[Dict]:
    """
    Aggiorna batch di ordini per seguire best price.
    
    Args:
        order_manager: Istanza OrderManager
        orders: Lista ordini [{'betId', 'marketId', 'selectionId', 'side', 'price', 'sizeRemaining'}]
        live_prices: Dict {selectionId: {'back': best_back, 'lay': best_lay}}
        min_ticks: Tick minimi per update
    
    Returns:
        Lista risultati per ogni ordine
    """
    results = []
    
    for order in orders:
        sel_id = order.get('selectionId')
        side = order.get('side', 'BACK')
        
        prices = live_prices.get(sel_id, {})
        best_price = prices.get('back' if side == 'BACK' else 'lay', order.get('price', 0))
        
        if not best_price:
            results.append({'success': False, 'reason': 'No live price'})
            continue
        
        result = order_manager.smart_update_order(
            bet_id=order.get('betId'),
            market_id=order.get('marketId'),
            selection_id=sel_id,
            side=side,
            current_price=order.get('price', 0),
            best_price=best_price,
            size_remaining=order.get('sizeRemaining', 0),
            min_ticks=min_ticks
        )
        results.append(result)
    
    return results
