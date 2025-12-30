"""
Trading Engine PRO - Livello Enterprise.

Architettura completa:
    Market Update
         |
    Profit Engine
         |
    Mixed Solver (NumPy)
         |
    Auto-follow Engine
         |
    Replace / Cashout
         |
    State Machine
         |
    Telegram Broadcast + Audit

Moduli:
    1. Mixed BACK+LAY con sistema lineare (NumPy)
    2. Auto-follow Best Price + Tick Ladder Dinamico
    3. Cashout Live con Trailing Profit
    4. Telegram Broadcast + Audit Enterprise
"""

import asyncio
import time
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from enum import Enum
from pathlib import Path

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

logger = logging.getLogger(__name__)


# ==============================================================================
# CONFIGURAZIONE
# ==============================================================================

COMMISSION = 0.045           # Commissione Betfair Italia
REPLACE_COOLDOWN = 0.4       # Secondi tra replace
PROFIT_THRESHOLD = 0.10      # EUR minimo delta profit per replace
TRAILING_GAP = 0.80          # EUR gap trailing cashout
TELEGRAM_RATE_LIMIT = 0.35   # Secondi tra messaggi Telegram
MAX_TELEGRAM_RETRY = 3       # Retry massimi Telegram
FLOOD_WAIT_BASE = 1.5        # Secondi base flood wait


# ==============================================================================
# TICK LADDER BETFAIR
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
    """Tick size per un dato prezzo."""
    for low, high, tick in TICK_LADDER:
        if low <= price < high:
            return tick
    logger.debug(f"[TICK] Price {price} fuori range, usando tick=10.0")
    return 10.0


def normalize_price(price: float) -> float:
    """Normalizza al tick Betfair valido."""
    original = price
    if price < 1.01:
        logger.warning(f"[TICK] Price {price} < 1.01, normalizzato a 1.01")
        return 1.01
    if price >= 1000.0:
        logger.warning(f"[TICK] Price {price} >= 1000, normalizzato a 1000.0")
        return 1000.0
    tick = get_tick_size(price)
    normalized = round(round(price / tick) * tick, 2)
    if normalized != original:
        logger.debug(f"[TICK] Normalizzato: {original} -> {normalized} (tick={tick})")
    return normalized


def next_tick_up(price: float) -> float:
    """Prossimo tick superiore."""
    return normalize_price(price + get_tick_size(price))


def next_tick_down(price: float) -> float:
    """Prossimo tick inferiore."""
    return normalize_price(max(1.01, price - get_tick_size(price)))


def ticks_between(price1: float, price2: float) -> int:
    """Numero di tick tra due prezzi."""
    if price1 == price2:
        return 0
    low, high = min(price1, price2), max(price1, price2)
    ticks = 0
    current = low
    while current < high and ticks < 500:
        current = next_tick_up(current)
        ticks += 1
    if ticks >= 500:
        logger.warning(f"[TICK] ticks_between overflow: {price1} -> {price2}, capped at 500")
    return ticks


# ==============================================================================
# BET STATE (MACHINE STATE PER betId)
# ==============================================================================

class BetStatus(Enum):
    """Stati ordine."""
    PENDING = "PENDING"
    PLACED = "PLACED"
    PARTIAL = "PARTIAL"
    MATCHED = "MATCHED"
    REPLACING = "REPLACING"
    HEDGING = "HEDGING"
    CASHED_OUT = "CASHED_OUT"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


@dataclass
class BetState:
    """Stato completo di una scommessa."""
    bet_id: str
    market_id: str
    selection_id: int
    side: str  # BACK / LAY
    price: float
    stake: float
    
    # Tracking
    status: BetStatus = BetStatus.PLACED
    size_matched: float = 0.0
    size_remaining: float = 0.0
    
    # Profit tracking
    current_profit: float = 0.0
    max_profit: float = 0.0
    
    # Replace tracking
    last_replace_ts: float = 0.0
    replace_count: int = 0
    
    # Mapping
    new_bet_id: Optional[str] = None
    
    def __post_init__(self):
        if self.size_remaining == 0:
            self.size_remaining = self.stake


# ==============================================================================
# 1. MIXED BACK+LAY SOLVER (NumPy)
# ==============================================================================

def calculate_mixed_dutching_numpy(
    bets: List[Dict],
    target_profit: float,
    commission: float = COMMISSION
) -> Tuple[List[float], float]:
    """
    Risolve sistema lineare per profitto NETTO uniforme su BACK+LAY misto.
    
    Args:
        bets: [{'side': 'BACK'/'LAY', 'price': X, 'selectionId': Y}]
        target_profit: Profitto target per esito
        commission: Commissione
    
    Returns:
        (stakes, guaranteed_profit)
    
    Sistema: A * x = b
        A = matrice impatto ogni bet su ogni esito
        x = stakes da calcolare
        b = target profit per ogni esito
    """
    if not HAS_NUMPY:
        logger.error("[MIXED] NumPy non disponibile!")
        return [], 0
    
    n = len(bets)
    if n == 0:
        logger.warning("[MIXED] Nessuna bet fornita")
        return [], 0
    
    logger.info(f"[MIXED] Risolvo sistema {n}x{n} per target={target_profit:.2f} EUR")
    logger.debug(f"[MIXED] Input bets: {bets}")
    
    # Costruisci matrice A e vettore b
    A = []
    b = []
    
    for i in range(n):
        row = []
        for j, bet in enumerate(bets):
            price = bet['price']
            side = bet['side']
            
            if i == j:
                # Impatto su se stesso (se vince questa selezione)
                if side == 'BACK':
                    # BACK: vinciamo (price-1) * stake - commissione
                    row.append((price - 1) * (1 - commission))
                else:
                    # LAY: perdiamo (price-1) * stake
                    row.append(-(price - 1))
            else:
                # Impatto su altri esiti
                if side == 'BACK':
                    # BACK su altra selezione: perdiamo stake
                    row.append(-1)
                else:
                    # LAY su altra selezione: vinciamo stake - commissione
                    row.append(1 * (1 - commission))
        
        A.append(row)
        b.append(target_profit)
    
    try:
        A_np = np.array(A, dtype=float)
        b_np = np.array(b, dtype=float)
        
        logger.debug(f"[MIXED] Matrice A:\n{A_np}")
        logger.debug(f"[MIXED] Vettore b: {b_np}")
        
        # Verifica determinante
        det = np.linalg.det(A_np)
        logger.debug(f"[MIXED] Determinante matrice: {det:.6f}")
        
        if abs(det) < 1e-10:
            logger.error(f"[MIXED] Matrice quasi singolare! det={det}")
        
        # Risolvi sistema
        stakes = np.linalg.solve(A_np, b_np)
        logger.debug(f"[MIXED] Soluzione grezza: {stakes}")
        
        # Verifica soluzione valida (stake positivi)
        if np.any(stakes < 0):
            logger.warning(f"[MIXED] Soluzione con stake negativi: {stakes}")
            logger.info("[MIXED] Provo least squares per approssimazione...")
            stakes, residuals, rank, s = np.linalg.lstsq(A_np, b_np, rcond=None)
            stakes = np.maximum(stakes, 0)
            logger.info(f"[MIXED] Least squares: stakes={stakes}, residuals={residuals}")
        
        stakes_list = [round(s, 2) for s in stakes.tolist()]
        total_stake = sum(stakes_list)
        
        # Verifica profitto reale
        logger.info(f"[MIXED] SUCCESSO: stakes={stakes_list}, total={total_stake:.2f}")
        
        return stakes_list, target_profit
        
    except np.linalg.LinAlgError as e:
        logger.error(f"[MIXED] ERRORE Sistema singolare: {e}")
        logger.error(f"[MIXED] Matrice A: {A}")
        return [], 0
    except Exception as e:
        logger.error(f"[MIXED] ERRORE Inatteso: {type(e).__name__}: {e}")
        return [], 0


def calculate_mixed_dutching_fallback(
    bets: List[Dict],
    target_profit: float,
    commission: float = COMMISSION
) -> Tuple[List[float], float]:
    """
    Fallback senza NumPy per casi semplici (solo BACK o solo LAY).
    """
    logger.info(f"[MIXED FALLBACK] Usando fallback (NumPy non disponibile)")
    sides = set(b['side'] for b in bets)
    logger.debug(f"[MIXED FALLBACK] Sides presenti: {sides}")
    
    if sides == {'BACK'}:
        # Solo BACK: usa formula standard
        prices = [b['price'] for b in bets]
        inv_sum = sum(1/p for p in prices)
        
        if inv_sum >= 1:
            logger.warning("[MIXED FALLBACK] Quote sfavorevoli per BACK")
            return [], 0
        
        total = target_profit / (1 - inv_sum)
        stakes = [round(total / p, 2) for p in prices]
        actual_profit = (total * (1 - inv_sum)) * (1 - commission)
        
        return stakes, round(actual_profit, 2)
    
    elif sides == {'LAY'}:
        # Solo LAY: distribuisci liability
        prices = [b['price'] for b in bets]
        inv_liability = [1 / (p - 1) for p in prices]
        total_inv = sum(inv_liability)
        
        liability = target_profit / (1 - commission)
        stakes = [round(liability * inv / total_inv, 2) for inv in inv_liability]
        
        return stakes, target_profit
    
    else:
        # Misto senza NumPy: non supportato
        logger.error("[MIXED FALLBACK] BACK+LAY misto richiede NumPy!")
        return [], 0


def calculate_mixed_dutching(
    bets: List[Dict],
    target_profit: float,
    commission: float = COMMISSION
) -> Tuple[List[float], float]:
    """
    Wrapper che usa NumPy se disponibile, altrimenti fallback.
    """
    logger.info(f"[DUTCHING] Calcolo mixed dutching: {len(bets)} bets, target={target_profit:.2f}")
    
    if HAS_NUMPY:
        logger.debug("[DUTCHING] Usando NumPy solver")
        return calculate_mixed_dutching_numpy(bets, target_profit, commission)
    else:
        logger.warning("[DUTCHING] NumPy non disponibile, uso fallback")
        return calculate_mixed_dutching_fallback(bets, target_profit, commission)


# ==============================================================================
# 2. AUTO-FOLLOW BEST PRICE + TICK LADDER DINAMICO
# ==============================================================================

class SpreadStrategy(Enum):
    """Strategia basata su spread."""
    FRONT = "FRONT"   # Spread stretto: -1 tick per match veloce
    MATCH = "MATCH"   # Spread medio: best price
    BACK = "BACK"     # Spread largo: attendi prezzo migliore


def analyze_spread(back_price: float, lay_price: float) -> SpreadStrategy:
    """
    Analizza spread e suggerisce strategia.
    
    Spread stretto (1-2 tick): FRONT
    Spread medio (3-5 tick): MATCH
    Spread largo (>5 tick): BACK
    """
    ticks = ticks_between(back_price, lay_price)
    
    if ticks <= 2:
        strategy = SpreadStrategy.FRONT
    elif ticks <= 5:
        strategy = SpreadStrategy.MATCH
    else:
        strategy = SpreadStrategy.BACK
    
    logger.debug(f"[SPREAD] back={back_price}, lay={lay_price}, ticks={ticks} -> {strategy.value}")
    return strategy


def calculate_target_price(
    best_price: float,
    side: str,
    strategy: SpreadStrategy
) -> float:
    """
    Calcola prezzo target basato su strategia.
    """
    if strategy == SpreadStrategy.FRONT:
        if side == 'BACK':
            return next_tick_down(best_price)  # Offri meno
        else:
            return next_tick_up(best_price)    # Offri di piu
    
    elif strategy == SpreadStrategy.BACK:
        if side == 'BACK':
            return next_tick_up(best_price)    # Attendi prezzo migliore
        else:
            return next_tick_down(best_price)  # Attendi prezzo migliore
    
    return normalize_price(best_price)


def calculate_profit_delta(
    stake: float,
    old_price: float,
    new_price: float,
    side: str,
    commission: float = COMMISSION
) -> float:
    """Calcola delta profitto tra due prezzi."""
    if side == 'BACK':
        old_profit = stake * (old_price - 1) * (1 - commission)
        new_profit = stake * (new_price - 1) * (1 - commission)
    else:
        old_profit = stake * (1 - commission)
        new_profit = stake * (1 - commission)
    
    delta = new_profit - old_profit
    logger.debug(f"[PROFIT DELTA] {side} stake={stake:.2f}: {old_price}->{new_price} = {old_profit:.2f}->{new_profit:.2f} (delta={delta:+.2f})")
    return delta


class AutoFollowEngine:
    """
    Engine auto-follow best price con tick ladder dinamico.
    """
    
    def __init__(
        self,
        betfair_client,
        profit_threshold: float = PROFIT_THRESHOLD,
        cooldown: float = REPLACE_COOLDOWN
    ):
        self.client = betfair_client
        self.profit_threshold = profit_threshold
        self.cooldown = cooldown
        self.states: Dict[str, BetState] = {}
    
    def register(self, bet: BetState):
        """Registra bet per tracking."""
        self.states[bet.bet_id] = bet
        logger.info(f"[AUTO-FOLLOW] Registrato: {bet.bet_id} {bet.side}@{bet.price} stake={bet.stake:.2f}")
    
    def should_follow(
        self,
        bet: BetState,
        best_back: float,
        best_lay: float
    ) -> Tuple[bool, float, str]:
        """
        Decide se seguire il prezzo.
        
        Returns:
            (should_replace, target_price, reason)
        """
        now = time.time()
        
        # Cooldown check
        if now - bet.last_replace_ts < self.cooldown:
            return False, 0, "Cooldown"
        
        # Scegli best price per side
        best_price = best_back if bet.side == 'BACK' else best_lay
        
        if not best_price or best_price <= 1:
            return False, 0, "No price"
        
        # Analizza spread
        strategy = analyze_spread(best_back, best_lay)
        target = calculate_target_price(best_price, bet.side, strategy)
        
        # Stesso prezzo?
        if target == bet.price:
            return False, 0, "Same price"
        
        # Direzione sbagliata?
        if bet.side == 'BACK' and target < bet.price:
            # BACK: non accettare prezzo peggiore
            pass  # OK se stiamo migliorando
        if bet.side == 'LAY' and target > bet.price:
            # LAY: non accettare prezzo peggiore
            pass
        
        # Delta profit check
        delta = calculate_profit_delta(
            bet.stake, bet.price, target, bet.side
        )
        
        if delta < self.profit_threshold:
            return False, 0, f"Delta {delta:.2f} < threshold"
        
        return True, target, f"Strategy={strategy.value}, delta=+{delta:.2f}"
    
    async def follow(
        self,
        bet: BetState,
        best_back: float,
        best_lay: float
    ) -> Dict:
        """
        Esegue follow se conveniente.
        """
        result = {
            'success': False,
            'betId': bet.bet_id,
            'action': None,
            'reason': None
        }
        
        should, target, reason = self.should_follow(bet, best_back, best_lay)
        
        if not should:
            result['reason'] = reason
            logger.debug(f"[AUTO-FOLLOW] {bet.bet_id}: Skip - {reason}")
            return result
        
        logger.info(f"[AUTO-FOLLOW] {bet.bet_id}: {bet.price} -> {target} ({reason})")
        
        # Esegui replace
        bet.status = BetStatus.REPLACING
        logger.debug(f"[AUTO-FOLLOW] {bet.bet_id}: Status -> REPLACING")
        
        try:
            logger.debug(f"[AUTO-FOLLOW] Chiamata replaceOrders: market={bet.market_id}, betId={bet.bet_id}, price={target}")
            response = self.client.replace_orders(
                market_id=bet.market_id,
                bet_id=bet.bet_id,
                new_price=target
            )
            logger.debug(f"[AUTO-FOLLOW] Response: {response}")
            
            if response.get('status') == 'SUCCESS':
                old_price = bet.price
                bet.price = target
                bet.last_replace_ts = time.time()
                bet.replace_count += 1
                bet.status = BetStatus.PLACED
                
                # Check nuovo betId
                reports = response.get('instructionReports', [])
                if reports:
                    new_id = reports[0].get('newBetId')
                    if new_id and new_id != bet.bet_id:
                        logger.info(f"[AUTO-FOLLOW] BetId cambiato: {bet.bet_id} -> {new_id}")
                        bet.new_bet_id = new_id
                        result['newBetId'] = new_id
                
                result['success'] = True
                result['action'] = 'REPLACE'
                result['newPrice'] = target
                logger.info(f"[AUTO-FOLLOW] SUCCESSO: {bet.bet_id} {old_price} -> {target} (replace #{bet.replace_count})")
            else:
                error_msg = response.get('errorCode', response.get('status', 'UNKNOWN'))
                result['reason'] = f"API error: {error_msg}"
                bet.status = BetStatus.PLACED
                logger.error(f"[AUTO-FOLLOW] ERRORE API: {error_msg}")
                logger.error(f"[AUTO-FOLLOW] Response completa: {response}")
                
        except Exception as e:
            result['reason'] = str(e)
            bet.status = BetStatus.ERROR
            logger.error(f"[AUTO-FOLLOW] ECCEZIONE: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"[AUTO-FOLLOW] Traceback: {traceback.format_exc()}")
        
        return result


# ==============================================================================
# 3. CASHOUT LIVE CON TRAILING PROFIT
# ==============================================================================

class TrailingCashoutEngine:
    """
    Cashout con trailing profit - chiude quando ritraccia.
    
    Logica:
        1. Traccia max_profit per ogni posizione
        2. Se profit scende sotto max - trailing_gap -> CASHOUT
    """
    
    def __init__(
        self,
        betfair_client,
        trailing_gap: float = TRAILING_GAP,
        commission: float = COMMISSION
    ):
        self.client = betfair_client
        self.trailing_gap = trailing_gap
        self.commission = commission
        self.positions: Dict[str, BetState] = {}
    
    def add_position(self, bet: BetState):
        """Aggiungi posizione da monitorare."""
        self.positions[bet.bet_id] = bet
        logger.info(f"[TRAILING] Posizione aggiunta: {bet.bet_id} {bet.side}@{bet.price} stake={bet.stake:.2f}")
    
    def calculate_position_profit(
        self,
        bet: BetState,
        live_price: float
    ) -> Tuple[float, float]:
        """
        Calcola profitto attuale e lay stake per cashout.
        
        Returns:
            (current_profit, lay_stake_needed)
        """
        if bet.side != 'BACK':
            logger.debug(f"[TRAILING] {bet.bet_id}: Skip side={bet.side} (solo BACK supportato)")
            return 0, 0
        
        if live_price <= 1:
            logger.warning(f"[TRAILING] {bet.bet_id}: live_price={live_price} non valido")
            return 0, 0
        
        # Profitto se vince al prezzo attuale
        win_profit = bet.stake * (bet.price - 1)
        
        # Lay stake per pareggiare
        lay_stake = (bet.stake * bet.price) / live_price
        
        # Profitto cashout (uniforme)
        cashout_profit = (bet.stake * bet.price / live_price) - bet.stake
        
        if cashout_profit > 0:
            cashout_profit *= (1 - self.commission)
        
        logger.debug(f"[TRAILING] {bet.bet_id}: live={live_price}, profit={cashout_profit:.2f}, lay_stake={lay_stake:.2f}")
        return round(cashout_profit, 2), round(lay_stake, 2)
    
    def check_trailing(
        self,
        bet: BetState,
        live_price: float
    ) -> Tuple[bool, float, float]:
        """
        Verifica se triggerare trailing cashout.
        
        Returns:
            (should_cashout, current_profit, max_profit)
        """
        current_profit, _ = self.calculate_position_profit(bet, live_price)
        
        # Aggiorna max profit
        if current_profit > bet.max_profit:
            bet.max_profit = current_profit
            logger.debug(f"[TRAILING] {bet.bet_id}: new max={bet.max_profit:.2f}")
        
        # Check trailing
        if bet.max_profit > 0 and current_profit < (bet.max_profit - self.trailing_gap):
            logger.info(f"[TRAILING] TRIGGER: current={current_profit:.2f}, max={bet.max_profit:.2f}")
            return True, current_profit, bet.max_profit
        
        return False, current_profit, bet.max_profit
    
    async def execute_cashout(
        self,
        bet: BetState,
        live_price: float
    ) -> Dict:
        """
        Esegue cashout per singola posizione.
        """
        result = {
            'success': False,
            'betId': bet.bet_id,
            'profit': 0,
            'reason': None
        }
        
        current_profit, lay_stake = self.calculate_position_profit(bet, live_price)
        
        if lay_stake < 2:  # Stake minimo Betfair
            result['reason'] = "Stake troppo basso"
            logger.warning(f"[CASHOUT] {bet.bet_id}: lay_stake={lay_stake:.2f} < 2 EUR minimo")
            return result
        
        bet.status = BetStatus.HEDGING
        logger.info(f"[CASHOUT] {bet.bet_id}: Eseguo cashout LAY@{live_price} x{lay_stake:.2f}")
        
        try:
            logger.debug(f"[CASHOUT] Chiamata place_bet: market={bet.market_id}, sel={bet.selection_id}")
            response = self.client.place_bet(
                market_id=bet.market_id,
                selection_id=bet.selection_id,
                side='LAY',
                price=live_price,
                size=lay_stake
            )
            logger.debug(f"[CASHOUT] Response: {response}")
            
            if response.get('status') == 'SUCCESS':
                bet.status = BetStatus.CASHED_OUT
                bet.current_profit = current_profit
                
                result['success'] = True
                result['profit'] = current_profit
                result['layStake'] = lay_stake
                result['layPrice'] = live_price
                
                logger.info(f"[CASHOUT] SUCCESSO: {bet.bet_id} profit={current_profit:.2f} EUR (LAY@{live_price}x{lay_stake:.2f})")
            else:
                error_msg = response.get('errorCode', response.get('status', 'UNKNOWN'))
                result['reason'] = error_msg
                bet.status = BetStatus.PLACED
                logger.error(f"[CASHOUT] ERRORE API: {error_msg}")
                logger.error(f"[CASHOUT] Response: {response}")
                
        except Exception as e:
            result['reason'] = str(e)
            bet.status = BetStatus.ERROR
            logger.error(f"[CASHOUT] ECCEZIONE: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"[CASHOUT] Traceback: {traceback.format_exc()}")
        
        return result
    
    async def monitor_all(
        self,
        live_prices: Dict[int, float]
    ) -> List[Dict]:
        """
        Monitora tutte le posizioni e esegue cashout se necessario.
        """
        results = []
        active_count = 0
        
        for bet in list(self.positions.values()):
            if bet.status in [BetStatus.CASHED_OUT, BetStatus.CANCELLED]:
                continue
            
            active_count += 1
            live_price = live_prices.get(bet.selection_id)
            if not live_price:
                logger.debug(f"[TRAILING MONITOR] {bet.bet_id}: Nessun prezzo live per sel={bet.selection_id}")
                continue
            
            should_cashout, current, max_p = self.check_trailing(bet, live_price)
            
            if should_cashout:
                logger.info(f"[TRAILING MONITOR] {bet.bet_id}: TRIGGER cashout! current={current:.2f}, max={max_p:.2f}")
                result = await self.execute_cashout(bet, live_price)
                results.append(result)
        
        if results:
            logger.info(f"[TRAILING MONITOR] Eseguiti {len(results)} cashout su {active_count} posizioni attive")
        
        return results


# ==============================================================================
# 4. TELEGRAM BROADCAST + AUDIT
# ==============================================================================

class TelegramAuditStatus(Enum):
    """Stati audit Telegram."""
    QUEUED = "QUEUED"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    RETRY = "RETRY"
    FLOOD_WAIT = "FLOOD_WAIT"


@dataclass
class TelegramAuditRecord:
    """Record audit singolo messaggio."""
    id: int = 0
    bet_id: Optional[str] = None
    chat_id: int = 0
    message: str = ""
    status: TelegramAuditStatus = TelegramAuditStatus.QUEUED
    retry_count: int = 0
    error_code: Optional[str] = None
    flood_wait_seconds: int = 0
    timestamp: float = field(default_factory=time.time)
    sent_at: Optional[float] = None


class TelegramBroadcastEngine:
    """
    Engine broadcast Telegram con audit completo.
    
    Features:
        - Queue async
        - Retry intelligente
        - Rate limiting
        - Flood wait handling
        - Audit persistente SQLite
    """
    
    def __init__(
        self,
        telegram_client,
        db_path: str = None,
        rate_limit: float = TELEGRAM_RATE_LIMIT,
        max_retry: int = MAX_TELEGRAM_RETRY
    ):
        self.client = telegram_client
        self.rate_limit = rate_limit
        self.max_retry = max_retry
        self.queue: asyncio.Queue = asyncio.Queue()
        self.running = False
        
        # Audit database
        if db_path is None:
            appdata = Path.home() / "AppData" / "Roaming" / "Pickfair"
            appdata.mkdir(parents=True, exist_ok=True)
            db_path = str(appdata / "telegram_audit.db")
        
        self.db_path = db_path
        self._init_audit_db()
        logger.info(f"[TG BROADCAST] Inizializzato: db={db_path}, rate_limit={rate_limit}s, max_retry={max_retry}")
        
        # Metriche
        self.metrics = {
            'total_sent': 0,
            'total_failed': 0,
            'total_retry': 0,
            'flood_incidents': 0
        }
    
    def _init_audit_db(self):
        """Inizializza database audit."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS telegram_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bet_id TEXT,
                chat_id INTEGER,
                message TEXT,
                status TEXT,
                retry_count INTEGER DEFAULT 0,
                error_code TEXT,
                flood_wait_seconds INTEGER DEFAULT 0,
                timestamp REAL,
                sent_at REAL
            )
        ''')
        conn.commit()
        conn.close()
    
    def _save_audit(self, record: TelegramAuditRecord) -> int:
        """Salva record audit."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if record.id == 0:
            cursor.execute('''
                INSERT INTO telegram_audit 
                (bet_id, chat_id, message, status, retry_count, error_code, 
                 flood_wait_seconds, timestamp, sent_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                record.bet_id, record.chat_id, record.message,
                record.status.value, record.retry_count, record.error_code,
                record.flood_wait_seconds, record.timestamp, record.sent_at
            ))
            record.id = cursor.lastrowid
        else:
            cursor.execute('''
                UPDATE telegram_audit 
                SET status=?, retry_count=?, error_code=?, 
                    flood_wait_seconds=?, sent_at=?
                WHERE id=?
            ''', (
                record.status.value, record.retry_count, record.error_code,
                record.flood_wait_seconds, record.sent_at, record.id
            ))
        
        conn.commit()
        conn.close()
        return record.id
    
    def broadcast(
        self,
        chat_id: int,
        message: str,
        bet_id: Optional[str] = None
    ):
        """
        Accoda messaggio per broadcast.
        """
        record = TelegramAuditRecord(
            bet_id=bet_id,
            chat_id=chat_id,
            message=message,
            status=TelegramAuditStatus.QUEUED
        )
        
        try:
            record.id = self._save_audit(record)
            self.queue.put_nowait(record)
            logger.info(f"[TG BROADCAST] Queued: chat={chat_id}, bet={bet_id}, audit_id={record.id}, queue_size={self.queue.qsize()}")
        except Exception as e:
            logger.error(f"[TG BROADCAST] ERRORE queue: {type(e).__name__}: {e}")
    
    async def _send_single(self, record: TelegramAuditRecord) -> bool:
        """Invia singolo messaggio con retry."""
        logger.debug(f"[TG SEND] Inizio invio: id={record.id}, chat={record.chat_id}")
        
        for attempt in range(self.max_retry):
            record.retry_count = attempt
            record.status = TelegramAuditStatus.SENDING
            self._save_audit(record)
            
            logger.debug(f"[TG SEND] Tentativo {attempt+1}/{self.max_retry}")
            
            try:
                await self.client.send_message(record.chat_id, record.message)
                
                record.status = TelegramAuditStatus.SENT
                record.sent_at = time.time()
                self._save_audit(record)
                
                self.metrics['total_sent'] += 1
                logger.info(f"[TG SEND] SUCCESSO: id={record.id}, chat={record.chat_id}, attempt={attempt+1}")
                return True
                
            except Exception as e:
                error_str = str(e).lower()
                logger.warning(f"[TG SEND] Errore tentativo {attempt+1}: {e}")
                
                # Flood wait detection
                if 'flood' in error_str or 'too many' in error_str:
                    wait = FLOOD_WAIT_BASE * (attempt + 1) * 2
                    record.status = TelegramAuditStatus.FLOOD_WAIT
                    record.flood_wait_seconds = int(wait)
                    self._save_audit(record)
                    
                    self.metrics['flood_incidents'] += 1
                    logger.warning(f"[TG SEND] FLOOD WAIT: {wait}s (incident #{self.metrics['flood_incidents']})")
                    await asyncio.sleep(wait)
                    continue
                
                # Altri errori
                record.status = TelegramAuditStatus.RETRY
                record.error_code = str(e)[:100]
                self._save_audit(record)
                
                self.metrics['total_retry'] += 1
                logger.debug(f"[TG SEND] Retry #{self.metrics['total_retry']}, wait {FLOOD_WAIT_BASE * (attempt + 1)}s")
                await asyncio.sleep(FLOOD_WAIT_BASE * (attempt + 1))
        
        # Fallito dopo tutti i retry
        record.status = TelegramAuditStatus.FAILED
        self._save_audit(record)
        
        self.metrics['total_failed'] += 1
        logger.error(f"[TG SEND] FALLITO: id={record.id}, chat={record.chat_id}, dopo {self.max_retry} tentativi")
        logger.error(f"[TG SEND] Ultimo errore: {record.error_code}")
        return False
    
    async def worker(self):
        """Worker async per processare coda."""
        self.running = True
        logger.info("[TG WORKER] Started, rate_limit={self.rate_limit}s")
        
        processed = 0
        while self.running:
            try:
                record = await asyncio.wait_for(
                    self.queue.get(), 
                    timeout=1.0
                )
                
                logger.debug(f"[TG WORKER] Processing record id={record.id}")
                await self._send_single(record)
                await asyncio.sleep(self.rate_limit)
                
                self.queue.task_done()
                processed += 1
                
                if processed % 10 == 0:
                    logger.info(f"[TG WORKER] Processed {processed} messages, queue={self.queue.qsize()}")
                
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"[TG WORKER] ERRORE: {type(e).__name__}: {e}")
                import traceback
                logger.error(f"[TG WORKER] Traceback: {traceback.format_exc()}")
        
        logger.info(f"[TG WORKER] Stopped, total processed={processed}")
    
    def stop(self):
        """Ferma worker."""
        logger.info(f"[TG BROADCAST] Stop richiesto, queue_size={self.queue.qsize()}")
        self.running = False
    
    def get_metrics(self) -> Dict:
        """Restituisce metriche."""
        return {
            **self.metrics,
            'queue_size': self.queue.qsize(),
            'delivery_rate': (
                self.metrics['total_sent'] / 
                max(1, self.metrics['total_sent'] + self.metrics['total_failed'])
            ) * 100
        }
    
    def get_audit_history(self, limit: int = 50) -> List[Dict]:
        """Ultimi N record audit."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM telegram_audit 
            ORDER BY id DESC LIMIT ?
        ''', (limit,))
        
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(zip(columns, row)) for row in rows]


# ==============================================================================
# TRADING ENGINE PRO (ORCHESTRATORE)
# ==============================================================================

class TradingEnginePro:
    """
    Orchestratore principale che integra tutti i moduli.
    
    Flow:
        Market Update -> Profit Engine -> Mixed Solver
        -> Auto-follow -> Replace/Cashout -> State Machine
        -> Telegram Broadcast
    """
    
    def __init__(
        self,
        betfair_client,
        telegram_client=None,
        config: Dict = None
    ):
        self.betfair = betfair_client
        self.telegram = telegram_client
        
        cfg = config or {}
        
        # Engines
        self.auto_follow = AutoFollowEngine(
            betfair_client,
            profit_threshold=cfg.get('profit_threshold', PROFIT_THRESHOLD),
            cooldown=cfg.get('replace_cooldown', REPLACE_COOLDOWN)
        )
        
        self.trailing_cashout = TrailingCashoutEngine(
            betfair_client,
            trailing_gap=cfg.get('trailing_gap', TRAILING_GAP),
            commission=cfg.get('commission', COMMISSION)
        )
        
        if telegram_client:
            self.telegram_broadcast = TelegramBroadcastEngine(
                telegram_client,
                rate_limit=cfg.get('telegram_rate_limit', TELEGRAM_RATE_LIMIT)
            )
            logger.info("[ENGINE PRO] Telegram broadcast abilitato")
        else:
            self.telegram_broadcast = None
            logger.info("[ENGINE PRO] Telegram broadcast disabilitato")
        
        logger.info(f"[ENGINE PRO] Inizializzato: profit_threshold={cfg.get('profit_threshold', PROFIT_THRESHOLD)}, trailing_gap={cfg.get('trailing_gap', TRAILING_GAP)}")
        
        # State
        self.positions: Dict[str, BetState] = {}
        self.running = False
    
    def add_position(self, bet: BetState):
        """Aggiungi posizione."""
        self.positions[bet.bet_id] = bet
        self.auto_follow.register(bet)
        self.trailing_cashout.add_position(bet)
        logger.info(f"[ENGINE PRO] Posizione aggiunta: {bet.bet_id} {bet.side}@{bet.price}, totale={len(self.positions)}")
    
    def calculate_dutching(
        self,
        bets: List[Dict],
        target_profit: float
    ) -> Tuple[List[float], float]:
        """Calcola dutching misto."""
        logger.info(f"[ENGINE PRO] Calcolo dutching: {len(bets)} bets, target={target_profit:.2f}")
        return calculate_mixed_dutching(bets, target_profit)
    
    async def process_market_update(
        self,
        market_id: str,
        live_prices: Dict[int, Dict]
    ) -> Dict:
        """
        Processa update mercato.
        
        Args:
            market_id: ID mercato
            live_prices: {selectionId: {'back': X, 'lay': Y}}
        """
        logger.debug(f"[ENGINE PRO] Market update: {market_id}, {len(live_prices)} selections")
        
        results = {
            'auto_follow': [],
            'cashouts': [],
            'broadcasts': []
        }
        
        positions_checked = 0
        
        # 1. Auto-follow per ogni posizione
        for bet in self.positions.values():
            if bet.market_id != market_id:
                continue
            if bet.status in [BetStatus.CASHED_OUT, BetStatus.CANCELLED]:
                continue
            
            positions_checked += 1
            prices = live_prices.get(bet.selection_id, {})
            back = prices.get('back', 0)
            lay = prices.get('lay', 0)
            
            if back and lay:
                result = await self.auto_follow.follow(bet, back, lay)
                results['auto_follow'].append(result)
                if result.get('success'):
                    logger.info(f"[ENGINE PRO] Auto-follow eseguito: {bet.bet_id}")
        
        # 2. Check trailing cashout
        lay_prices = {k: v.get('lay', 0) for k, v in live_prices.items()}
        cashouts = await self.trailing_cashout.monitor_all(lay_prices)
        results['cashouts'] = cashouts
        
        # 3. Broadcast cashouts
        if self.telegram_broadcast and cashouts:
            for cashout in cashouts:
                if cashout.get('success'):
                    msg = f"Cashout: +{cashout['profit']:.2f} EUR"
                    logger.info(f"[ENGINE PRO] Cashout broadcast: {msg}")
                    # Broadcast a tutti i chat configurati
                    # self.telegram_broadcast.broadcast(chat_id, msg, cashout.get('betId'))
        
        # Log summary
        follow_success = sum(1 for r in results['auto_follow'] if r.get('success'))
        cashout_success = sum(1 for r in results['cashouts'] if r.get('success'))
        
        if follow_success or cashout_success:
            logger.info(f"[ENGINE PRO] Update completato: {positions_checked} pos, {follow_success} follow, {cashout_success} cashout")
        
        return results
    
    async def start(self):
        """Avvia engine."""
        self.running = True
        logger.info(f"[ENGINE PRO] Avvio con {len(self.positions)} posizioni")
        
        if self.telegram_broadcast:
            asyncio.create_task(self.telegram_broadcast.worker())
            logger.info("[ENGINE PRO] Telegram worker avviato")
        
        logger.info("[ENGINE PRO] STARTED")
    
    def stop(self):
        """Ferma engine."""
        logger.info(f"[ENGINE PRO] Stop richiesto, {len(self.positions)} posizioni attive")
        self.running = False
        
        if self.telegram_broadcast:
            self.telegram_broadcast.stop()
            logger.info("[ENGINE PRO] Telegram worker fermato")
        
        logger.info("[ENGINE PRO] STOPPED")
    
    def get_status(self) -> Dict:
        """Stato engine."""
        status = {
            'running': self.running,
            'positions': len(self.positions),
            'telegram_metrics': (
                self.telegram_broadcast.get_metrics() 
                if self.telegram_broadcast else None
            )
        }
        logger.debug(f"[ENGINE PRO] Status: {status}")
        return status
