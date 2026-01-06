"""
LiveContextStore
================
Cache thread-safe per dati live match.
Usata per sincronizzare API-Football thread con UI.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict


@dataclass
class LiveContext:
    """Dati live del match - definizione condivisa."""
    minute: Optional[int] = None
    injury_time: Optional[int] = None
    goals_home: int = 0
    goals_away: int = 0
    goal_minutes: List[int] = field(default_factory=list)
    goal_events: Dict[int, str] = field(default_factory=dict)
    period: str = "LIVE"
    market_status: str = "UNKNOWN"
    danger: bool = False
    home_team: str = ""
    away_team: str = ""


class LiveContextStore:
    """
    Store thread-safe per LiveContext.
    
    - API thread scrive via update()
    - UI thread legge via get()
    - Nessun blocco, nessuna race condition
    """
    
    def __init__(self):
        self._lock = threading.RLock()
        self._ctx = LiveContext()
        self._last_update = 0
        self._stale_threshold = 30
        
    def update(self, **kwargs):
        """
        Aggiorna contesto (chiamato da API thread).
        
        Esempio:
            live_context.update(
                minute=45,
                goals_home=1,
                goals_away=0,
                goal_minutes=[23],
                market_status="OPEN"
            )
        """
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self._ctx, k):
                    setattr(self._ctx, k, v)
            self._last_update = time.time()
            
    def get(self) -> LiveContext:
        """
        Ritorna copia snapshot del contesto (chiamato da UI thread).
        Thread-safe, non blocca.
        """
        with self._lock:
            return LiveContext(
                minute=self._ctx.minute,
                injury_time=self._ctx.injury_time,
                goals_home=self._ctx.goals_home,
                goals_away=self._ctx.goals_away,
                goal_minutes=list(self._ctx.goal_minutes),
                goal_events=dict(self._ctx.goal_events),
                period=self._ctx.period,
                market_status=self._ctx.market_status,
                danger=self._ctx.danger,
                home_team=self._ctx.home_team,
                away_team=self._ctx.away_team
            )
            
    def is_stale(self) -> bool:
        """Ritorna True se dati non aggiornati da troppo tempo."""
        with self._lock:
            return (time.time() - self._last_update) > self._stale_threshold
            
    def last_update_ago(self) -> float:
        """Secondi dall'ultimo aggiornamento."""
        with self._lock:
            return time.time() - self._last_update
            
    def clear(self):
        """Reset contesto."""
        with self._lock:
            self._ctx = LiveContext()
            self._last_update = 0


live_context_store = LiveContextStore()
