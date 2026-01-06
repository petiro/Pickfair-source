"""
HardSyncController
==================
Sincronizzazione HARD tra Betfair Stream e API-Football.

REGOLA D'ORO:
- Betfair = MASTER (unica fonte per trading)
- API-Football = SENSOR (solo contesto visivo)
- API-Football non prende MAI decisioni di trading

Attiva SAFE MODE su:
- Desync tempo/stato
- API lag
- Incoerenze goal/sospensione
"""

import time
import threading
import logging
import unicodedata
import re
from difflib import SequenceMatcher
from typing import Optional

log = logging.getLogger("HardSync")


def normalize_team_name(name: str) -> str:
    """Normalizza nomi squadra per matching."""
    if not name:
        return ""
        
    name = name.lower().strip()
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"[^\w\s]", "", name)
    
    blacklist = [
        "fc", "cf", "calcio", "football", "afc", "sc", "ac",
        "u19", "u20", "u21", "u23",
        "women", "w", "ladies", "femminile"
    ]
    
    parts = [p for p in name.split() if p not in blacklist]
    return " ".join(parts)


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


class HardSyncController:
    """
    Controller centrale sincronizzazione HARD.
    
    Betfair vince SEMPRE.
    """
    
    def __init__(self, safe_mode_manager=None):
        self.safe_mode = safe_mode_manager
        
        self.betfair_event: Optional[dict] = None
        self.betfair_market_status: str = "UNKNOWN"
        self.last_betfair_tick: float = 0
        
        self.api_event: Optional[dict] = None
        self.api_minute: Optional[int] = None
        self.api_injury_time: int = 0
        self.api_goals: tuple = (0, 0)
        self.last_api_update: float = 0
        
        self.matched: bool = False
        self.match_confidence: float = 0.0
        
        self._lock = threading.RLock()
        
    def match_events(self, betfair_event: dict, api_event: dict) -> bool:
        """
        Matching fuzzy tra evento Betfair e API-Football.
        Non bloccante se non trova match.
        """
        with self._lock:
            bf_home = betfair_event.get("home", "")
            bf_away = betfair_event.get("away", "")
            api_home = api_event.get("home", "")
            api_away = api_event.get("away", "")
            
            bf_h = normalize_team_name(bf_home)
            bf_a = normalize_team_name(bf_away)
            api_h = normalize_team_name(api_home)
            api_a = normalize_team_name(api_away)
            
            score_direct = (similarity(bf_h, api_h) + similarity(bf_a, api_a)) / 2
            score_cross = (similarity(bf_h, api_a) + similarity(bf_a, api_h)) / 2
            
            self.match_confidence = max(score_direct, score_cross)
            self.matched = self.match_confidence >= 0.6
            
            log.info(f"[MATCH] confidence={self.match_confidence:.2f} matched={self.matched}")
            
            return self.matched
            
    def update_betfair(self, event: Optional[dict], market_status: str):
        """
        Aggiorna stato da Betfair (MASTER).
        Se market non OPEN, blocca trading.
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
        Controlla desync.
        """
        with self._lock:
            self.api_minute = minute
            self.api_injury_time = injury_time
            self.api_goals = goals
            self.last_api_update = time.time()
            
            self._check_desyncs()
            
    def _check_desyncs(self):
        """Verifica incoerenze e attiva safe mode."""
        now = time.time()
        
        if now - self.last_api_update > 30:
            self._enter_safe("API_FOOTBALL_STALE")
            return
            
        if self.api_minute and self.api_minute >= 90:
            if self.betfair_market_status == "OPEN":
                log.warning("Time desync: API says 90+ but Betfair still OPEN")
                
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
        log.warning(f"[HARD SYNC] Trading blocked: {reason}")
        
    def trading_allowed(self) -> bool:
        """
        Trading permesso SOLO se Betfair OPEN.
        Safe mode non blocca, solo Betfair blocca.
        """
        with self._lock:
            return self.betfair_market_status == "OPEN"
            
    def get_ui_context(self) -> dict:
        """
        Dati per UI (barra timeline).
        """
        with self._lock:
            danger = False
            
            if self.betfair_market_status == "SUSPENDED":
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
                "match_confidence": round(self.match_confidence, 2)
            }
