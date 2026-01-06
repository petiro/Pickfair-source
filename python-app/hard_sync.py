"""
HardSyncController
==================
Sincronizzazione HARD tra Betfair Stream e API-Football.

REGOLA D'ORO:
- Betfair = MASTER (unica fonte per trading)
- API-Football = SENSOR (solo contesto visivo)
- API-Football non prende MAI decisioni di trading

Features:
- Goal detection con conferma Betfair
- Mismatch detection
- Safe mode automatico
- Zero blocchi UI

Attiva SAFE MODE su:
- Desync tempo/stato (30s threshold)
- API lag persistente
- Incoerenze goal/sospensione
"""

import time
import threading
import logging
from typing import Optional, Dict, Any

log = logging.getLogger("HardSync")


class HardSyncController:
    """
    Controller centrale sincronizzazione HARD.
    
    Betfair vince SEMPRE.
    API-Football e' solo informativo.
    """
    
    GOAL_CONFIRM_WINDOW = 30
    MISMATCH_TIMEOUT = 20
    STALE_THRESHOLD = 30
    
    def __init__(self, safe_mode_manager=None):
        self.safe_mode = safe_mode_manager
        
        self.betfair_event: Optional[dict] = None
        self.betfair_market_status: str = "UNKNOWN"
        self.betfair_in_play: bool = False
        self.last_betfair_tick: float = 0
        
        self.api_event: Optional[dict] = None
        self.api_minute: Optional[int] = None
        self.api_injury_time: int = 0
        self.api_goals: tuple = (0, 0)
        self.api_status: str = "INIT"
        self.last_api_update: float = 0
        
        self.matched: bool = False
        self.match_confidence: float = 0.0
        
        self._last_goals: Optional[tuple] = None
        self._goal_pending: bool = False
        self._goal_pending_ts: float = 0
        
        self._lock = threading.RLock()
        
    def on_betfair_update(self, market_id: str, market_status: str, in_play: bool):
        """
        Callback da Betfair Stream (MASTER).
        
        Args:
            market_id: ID mercato Betfair
            market_status: OPEN, SUSPENDED, CLOSED
            in_play: True se in-play
        """
        with self._lock:
            self.betfair_market_status = market_status
            self.betfair_in_play = in_play
            self.last_betfair_tick = time.time()
            
            if market_status == "SUSPENDED" and self._goal_pending:
                log.info("[SYNC] Goal CONFIRMED by Betfair SUSPENDED")
                self._goal_pending = False
                
            if market_status != "OPEN":
                self._force_block(f"BETFAIR_{market_status}")
                
    def on_api_update(self, data: dict):
        """
        Callback da API-Football Worker (SENSOR).
        
        Args:
            data: {
                "minute": int,
                "extra_time": int,
                "home_goals": int,
                "away_goals": int,
                "status": str,
                "goal_detected": bool,
                "client_status": str
            }
        """
        with self._lock:
            self.api_minute = data.get("minute")
            self.api_injury_time = data.get("extra_time", 0)
            self.api_goals = (
                data.get("home_goals", 0),
                data.get("away_goals", 0)
            )
            self.api_status = data.get("client_status", "UNKNOWN")
            self.last_api_update = time.time()
            
            if data.get("goal_detected"):
                log.info("[SYNC] Goal detected from API-Football, waiting Betfair confirm...")
                self._goal_pending = True
                self._goal_pending_ts = time.time()
                
            self._check_mismatch()
            
    def _check_mismatch(self):
        """Verifica incoerenze e attiva safe mode se necessario."""
        now = time.time()
        
        if now - self.last_api_update > self.STALE_THRESHOLD:
            self._enter_safe("API_FOOTBALL_STALE")
            return
            
        if self._goal_pending:
            elapsed = now - self._goal_pending_ts
            if elapsed > self.MISMATCH_TIMEOUT:
                log.warning(f"[SYNC] Goal mismatch: API detected goal but Betfair not SUSPENDED after {elapsed:.0f}s")
                self._goal_pending = False
            
        if self.api_minute and self.api_minute >= 90:
            if self.betfair_market_status == "OPEN":
                log.debug("Time note: API says 90+ but Betfair still OPEN (normal for injury time)")
                
    def update_betfair(self, event: Optional[dict], market_status: str):
        """
        Aggiorna stato da Betfair (MASTER).
        Metodo legacy per compatibilita'.
        """
        with self._lock:
            self.betfair_event = event
            self.betfair_market_status = market_status
            self.last_betfair_tick = time.time()
            
            if market_status != "OPEN":
                self._force_block(f"BETFAIR_{market_status}")
                
    def update_api_football(self, minute: Optional[int], injury_time: int, 
                           goals: tuple, period: str):
        """
        Aggiorna dati da API-Football (SENSOR).
        Metodo legacy per compatibilita'.
        """
        self.on_api_update({
            "minute": minute,
            "extra_time": injury_time,
            "home_goals": goals[0] if len(goals) > 0 else 0,
            "away_goals": goals[1] if len(goals) > 1 else 0,
            "status": period,
            "goal_detected": False,
            "client_status": "OK"
        })
            
    def match_events(self, betfair_event: dict, api_event: dict) -> bool:
        """
        Matching fuzzy tra evento Betfair e API-Football.
        Usa TeamNameResolver se disponibile.
        """
        try:
            from team_name_resolver import get_resolver
            resolver = get_resolver()
            
            bf_name = betfair_event.get("name", "")
            api_home = api_event.get("home", "")
            api_away = api_event.get("away", "")
            
            matched, reason = resolver.match_event(api_home, api_away, bf_name)
            
            with self._lock:
                self.matched = matched
                self.match_confidence = 1.0 if matched else 0.0
                self.betfair_event = betfair_event
                self.api_event = api_event
                
            log.info(f"[MATCH] resolver: matched={matched} reason={reason}")
            return matched
            
        except ImportError:
            return self._legacy_match(betfair_event, api_event)
            
    def _legacy_match(self, betfair_event: dict, api_event: dict) -> bool:
        """Matching legacy senza TeamNameResolver."""
        from difflib import SequenceMatcher
        import unicodedata
        import re
        
        def normalize(name: str) -> str:
            if not name:
                return ""
            name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
            name = name.lower()
            name = re.sub(r"[^\w\s]", "", name)
            return " ".join(name.split())
        
        with self._lock:
            bf_home = normalize(betfair_event.get("home", ""))
            bf_away = normalize(betfair_event.get("away", ""))
            api_home = normalize(api_event.get("home", ""))
            api_away = normalize(api_event.get("away", ""))
            
            score_direct = (
                SequenceMatcher(None, bf_home, api_home).ratio() +
                SequenceMatcher(None, bf_away, api_away).ratio()
            ) / 2
            
            score_cross = (
                SequenceMatcher(None, bf_home, api_away).ratio() +
                SequenceMatcher(None, bf_away, api_home).ratio()
            ) / 2
            
            self.match_confidence = max(score_direct, score_cross)
            self.matched = self.match_confidence >= 0.6
            
            log.info(f"[MATCH] legacy: confidence={self.match_confidence:.2f} matched={self.matched}")
            
            return self.matched
            
    def _enter_safe(self, reason: str):
        """Entra in safe mode (non blocca trading, solo warning)."""
        log.warning(f"[HARD SYNC] Safe mode trigger: {reason}")
        if self.safe_mode:
            try:
                if hasattr(self.safe_mode, 'increment_error'):
                    self.safe_mode.increment_error(reason)
            except Exception as e:
                log.debug(f"Safe mode trigger failed: {e}")
            
    def _force_block(self, reason: str):
        """Blocca trading (Betfair non OPEN)."""
        log.debug(f"[HARD SYNC] Trading blocked: {reason}")
        
    def trading_allowed(self) -> bool:
        """
        Trading permesso SOLO se Betfair OPEN.
        Safe mode non blocca, solo Betfair blocca.
        """
        with self._lock:
            return self.betfair_market_status == "OPEN"
            
    def is_goal_pending(self) -> bool:
        """True se c'e' un goal in attesa di conferma Betfair."""
        with self._lock:
            return self._goal_pending
            
    def get_ui_context(self) -> dict:
        """
        Dati per UI (barra timeline).
        """
        with self._lock:
            danger = False
            
            if self.betfair_market_status == "SUSPENDED":
                danger = True
            elif self._goal_pending:
                danger = True
            elif self.api_minute and self.api_minute >= 80:
                danger = True
            elif self.api_injury_time:
                danger = True
                
            return {
                "minute": self.api_minute,
                "injury_time": self.api_injury_time,
                "goals": self.api_goals,
                "market_status": self.betfair_market_status,
                "danger": danger,
                "goal_pending": self._goal_pending,
                "match_confidence": round(self.match_confidence, 2),
                "api_status": self.api_status,
                "matched": self.matched
            }
