"""
Simulation Broker - Broker simulato per testing senza ordini reali

Fornisce stessa interfaccia di Betfair API per ordini, ma salva tutto in memoria.
Permette di testare strategie complete senza rischiare soldi reali.
"""

import logging
import threading
import time
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SimulatedOrder:
    """Ordine simulato."""
    bet_id: str
    market_id: str
    selection_id: int
    runner_name: str
    side: str
    price: float
    size: float
    matched: float = 0.0
    status: str = 'PENDING'
    placed_at: float = field(default_factory=time.time)
    
    @property
    def size_matched(self) -> float:
        return self.matched
    
    @property
    def size_remaining(self) -> float:
        return self.size - self.matched


class SimulationBroker:
    """
    Broker simulato con stessa interfaccia di Betfair.
    
    Caratteristiche:
    - place_order() salva in memoria invece di inviare a Betfair
    - cancel_order() rimuove ordini pending
    - list_bets() ritorna tutti gli ordini
    - Simula matching automatico per ordini a prezzo di mercato
    """
    
    def __init__(self, initial_balance: float = 10000.0, commission: float = 4.5):
        """
        Args:
            initial_balance: Bilancio iniziale simulato (default €10.000)
            commission: Commissione su vincite (default 4.5%)
        """
        self.balance = initial_balance
        self.initial_balance = initial_balance
        self.commission = commission
        
        self.orders: Dict[str, SimulatedOrder] = {}
        self.bet_counter = 0
        self.lock = threading.RLock()
        
        logger.info(f"[SIM BROKER] Inizializzato con balance €{initial_balance:.2f}")
    
    def place_order(self, market_id: str, selection_id: int, side: str, 
                    price: float, size: float, runner_name: str = '') -> Dict:
        """
        Piazza ordine simulato.
        
        Args:
            market_id: ID mercato
            selection_id: ID selezione
            side: 'BACK' o 'LAY'
            price: Quota
            size: Importo
            runner_name: Nome runner (opzionale)
            
        Returns:
            Dict con dettagli ordine incluso bet_id
        """
        with self.lock:
            self.bet_counter += 1
            bet_id = f"SIM-{self.bet_counter:06d}"
            
            order = SimulatedOrder(
                bet_id=bet_id,
                market_id=market_id,
                selection_id=selection_id,
                runner_name=runner_name,
                side=side,
                price=price,
                size=size,
                matched=size,
                status='MATCHED'
            )
            
            self.orders[bet_id] = order
            
            if side == 'BACK':
                self.balance -= size
            else:
                liability = size * (price - 1)
                self.balance -= liability
            
            logger.info(f"[SIM] {side} €{size:.2f} @ {price:.2f} su {runner_name} -> {bet_id}")
            
            return {
                'betId': bet_id,
                'selectionId': selection_id,
                'side': side,
                'price': price,
                'size': size,
                'sizeMatched': size,
                'status': 'MATCHED',
                'simulation': True
            }
    
    def cancel_order(self, bet_id: str) -> bool:
        """
        Cancella ordine (solo se pending).
        
        Returns:
            True se cancellato, False se non trovato o già matched
        """
        with self.lock:
            order = self.orders.get(bet_id)
            if not order:
                return False
            
            if order.status == 'MATCHED':
                logger.warning(f"[SIM] Ordine {bet_id} già matched, impossibile cancellare")
                return False
            
            if order.side == 'BACK':
                self.balance += order.size_remaining
            else:
                liability = order.size_remaining * (order.price - 1)
                self.balance += liability
            
            order.status = 'CANCELLED'
            logger.info(f"[SIM] Ordine {bet_id} cancellato")
            return True
    
    def list_bets(self, market_id: Optional[str] = None, 
                  status: Optional[str] = None) -> List[Dict]:
        """
        Lista ordini simulati.
        
        Args:
            market_id: Filtra per mercato
            status: Filtra per status ('MATCHED', 'PENDING', 'CANCELLED')
            
        Returns:
            Lista di dict ordini
        """
        with self.lock:
            result = []
            for order in self.orders.values():
                if market_id and order.market_id != market_id:
                    continue
                if status and order.status != status:
                    continue
                
                result.append({
                    'betId': order.bet_id,
                    'marketId': order.market_id,
                    'selectionId': order.selection_id,
                    'runnerName': order.runner_name,
                    'side': order.side,
                    'price': order.price,
                    'size': order.size,
                    'sizeMatched': order.matched,
                    'status': order.status,
                    'placedAt': order.placed_at,
                    'simulation': True
                })
            
            return result
    
    def get_order(self, bet_id: str) -> Optional[Dict]:
        """Ritorna singolo ordine."""
        with self.lock:
            order = self.orders.get(bet_id)
            if not order:
                return None
            
            return {
                'betId': order.bet_id,
                'marketId': order.market_id,
                'selectionId': order.selection_id,
                'runnerName': order.runner_name,
                'side': order.side,
                'price': order.price,
                'size': order.size,
                'sizeMatched': order.matched,
                'status': order.status,
                'simulation': True
            }
    
    def get_balance(self) -> float:
        """Ritorna bilancio attuale."""
        return self.balance
    
    def get_pnl(self) -> float:
        """Ritorna P&L rispetto a bilancio iniziale."""
        return self.balance - self.initial_balance
    
    def reset(self):
        """Reset completo del broker."""
        with self.lock:
            self.orders.clear()
            self.bet_counter = 0
            self.balance = self.initial_balance
            logger.info("[SIM] Broker resettato")
    
    def settle_market(self, market_id: str, winner_selection_id: int) -> float:
        """
        Regola mercato con vincitore noto.
        
        Args:
            market_id: ID mercato
            winner_selection_id: ID della selezione vincente
            
        Returns:
            P&L totale per il mercato
        """
        with self.lock:
            pnl = 0.0
            
            for order in self.orders.values():
                if order.market_id != market_id or order.status != 'MATCHED':
                    continue
                
                won = (order.selection_id == winner_selection_id)
                
                if order.side == 'BACK':
                    if won:
                        gross = order.matched * (order.price - 1)
                        net = gross * (1 - self.commission / 100)
                        pnl += net
                        self.balance += order.matched + net
                    else:
                        pnl -= order.matched
                else:
                    if won:
                        liability = order.matched * (order.price - 1)
                        pnl -= liability
                    else:
                        gross = order.matched
                        net = gross * (1 - self.commission / 100)
                        pnl += net
                        liability = order.matched * (order.price - 1)
                        self.balance += liability + net
                
                order.status = 'SETTLED'
            
            logger.info(f"[SIM] Mercato {market_id} regolato, P&L: €{pnl:.2f}")
            return pnl


class BookOptimizer:
    """
    Ottimizzatore Book % per dutching.
    
    Quando book > 105%, redistribuisce stake proporzionalmente
    per mantenere book entro limiti sicuri.
    """
    
    def __init__(self, warning_threshold: float = 105.0, 
                 max_threshold: float = 110.0):
        """
        Args:
            warning_threshold: Soglia warning book %
            max_threshold: Soglia blocco submit
        """
        self.warning_threshold = warning_threshold
        self.max_threshold = max_threshold
    
    def calculate_book(self, selections: List[Dict]) -> float:
        """
        Calcola book % dalle selezioni.
        
        Args:
            selections: Lista con 'price' per ogni runner
            
        Returns:
            Book % (100 = fair, >100 = overround)
        """
        if not selections:
            return 0.0
        
        total = sum(1 / s['price'] for s in selections if s.get('price', 0) > 1)
        return total * 100
    
    def optimize(self, selections: List[Dict], target_book: float = 100.0) -> List[Dict]:
        """
        Ottimizza stake per raggiungere target book %.
        
        Args:
            selections: Lista selezioni con 'stake' e 'price'
            target_book: Book % target
            
        Returns:
            Selezioni con stake ottimizzati
        """
        current_book = self.calculate_book(selections)
        
        if current_book <= target_book:
            return selections
        
        ratio = target_book / current_book
        
        for s in selections:
            if 'stake' in s:
                s['stake'] = s['stake'] * ratio
        
        logger.info(f"[BOOK OPT] Ridotto book da {current_book:.1f}% a {target_book:.1f}%")
        return selections
    
    def get_status(self, book_value: float) -> str:
        """
        Ritorna status per UI.
        
        Returns:
            'OK', 'WARNING', o 'BLOCKED'
        """
        if book_value > self.max_threshold:
            return 'BLOCKED'
        elif book_value > self.warning_threshold:
            return 'WARNING'
        return 'OK'


class TickReplayEngine:
    """
    Engine per replay storico tick.
    
    Permette di riprodurre tick passati per testare strategie
    senza rischio, con velocità configurabile.
    """
    
    def __init__(self, on_tick: Optional[Callable] = None):
        """
        Args:
            on_tick: Callback chiamato per ogni tick (selection_id, price)
        """
        self.ticks: List[Dict] = []
        self.index = 0
        self.on_tick = on_tick
        self.playing = False
        self.speed = 1.0
        self.lock = threading.Lock()
    
    def load_ticks(self, ticks: List[Dict]):
        """
        Carica tick storici.
        
        Args:
            ticks: Lista di {'selectionId': int, 'price': float, 'timestamp': float}
        """
        with self.lock:
            self.ticks = sorted(ticks, key=lambda t: t.get('timestamp', 0))
            self.index = 0
            logger.info(f"[REPLAY] Caricati {len(ticks)} tick")
    
    def next_tick(self) -> Optional[Dict]:
        """Ritorna prossimo tick o None se finito."""
        with self.lock:
            if self.index >= len(self.ticks):
                return None
            
            tick = self.ticks[self.index]
            self.index += 1
            
            if self.on_tick:
                self.on_tick(tick['selectionId'], tick['price'])
            
            return tick
    
    def play(self, speed: float = 1.0):
        """
        Avvia replay automatico in background.
        
        Args:
            speed: Velocità (1.0 = tempo reale, 2.0 = doppia velocità)
        """
        self.speed = speed
        self.playing = True
        
        def _play_loop():
            while self.playing:
                tick = self.next_tick()
                if not tick:
                    self.playing = False
                    break
                time.sleep(1.0 / self.speed)
        
        thread = threading.Thread(target=_play_loop, daemon=True)
        thread.start()
        logger.info(f"[REPLAY] Avviato a velocità {speed}x")
    
    def pause(self):
        """Mette in pausa replay."""
        self.playing = False
    
    def reset(self):
        """Reset replay all'inizio."""
        with self.lock:
            self.index = 0
            self.playing = False
    
    @property
    def progress(self) -> float:
        """Ritorna progresso 0.0 - 1.0."""
        with self.lock:
            if not self.ticks:
                return 0.0
            return self.index / len(self.ticks)
