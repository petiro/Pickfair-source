"""
API-Football Integration
========================
Worker thread per fetch dati live da API-Football.

IMPORTANTE:
- Questo modulo NON prende decisioni di trading
- Betfair e' sempre MASTER
- API-Football fornisce solo contesto visivo

API Docs: https://www.api-football.com/documentation-v3
"""

import os
import time
import threading
import logging
import requests
import unicodedata
import re
from difflib import SequenceMatcher
from typing import Optional, Dict, Any, Tuple

log = logging.getLogger("APIFootball")


def normalize_team_name(name: str) -> str:
    """Normalizza nomi squadra per matching cross-API."""
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
    """Similarita' tra due stringhe (0-1)."""
    return SequenceMatcher(None, a, b).ratio()


class APIFootballClient:
    """
    Client per API-Football v3.
    
    Rate limits:
    - Free: 100 requests/day
    - Basic: 7500 requests/day
    """
    
    BASE_URL = "https://v3.football.api-sports.io"
    DEFAULT_KEY = "9d726ff17ef61ad94aa372ebcaf99cd9"
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("API_FOOTBALL_KEY", "") or self.DEFAULT_KEY
        self.session = requests.Session()
        self.session.headers.update({
            "x-apisports-key": self.api_key
        })
        self._cache: Dict[str, Any] = {}
        self._cache_time: Dict[str, float] = {}
        self._cache_ttl = 30
        
    def _request(self, endpoint: str, params: dict = None) -> Optional[dict]:
        """Esegue richiesta API con caching."""
        if not self.api_key:
            log.warning("API_FOOTBALL_KEY non configurata")
            return None
            
        cache_key = f"{endpoint}:{str(params)}"
        now = time.time()
        
        if cache_key in self._cache:
            if now - self._cache_time.get(cache_key, 0) < self._cache_ttl:
                return self._cache[cache_key]
                
        try:
            url = f"{self.BASE_URL}/{endpoint}"
            resp = self.session.get(url, params=params, timeout=10)
            resp.raise_for_status()
            
            data = resp.json()
            
            if data.get("errors"):
                log.error(f"API-Football error: {data['errors']}")
                return None
                
            self._cache[cache_key] = data
            self._cache_time[cache_key] = now
            
            return data
            
        except requests.RequestException as e:
            log.error(f"API-Football request failed: {e}")
            return None
            
    def get_live_fixtures(self) -> list:
        """Ottiene partite live."""
        data = self._request("fixtures", {"live": "all"})
        if not data:
            return []
        return data.get("response", [])
        
    def find_fixture_by_teams(self, home: str, away: str) -> Optional[dict]:
        """
        Trova fixture per nomi squadre (matching fuzzy).
        Ritorna la migliore corrispondenza.
        """
        fixtures = self.get_live_fixtures()
        if not fixtures:
            return None
            
        norm_home = normalize_team_name(home)
        norm_away = normalize_team_name(away)
        
        best_match = None
        best_score = 0.0
        
        for fix in fixtures:
            teams = fix.get("teams", {})
            fix_home = normalize_team_name(teams.get("home", {}).get("name", ""))
            fix_away = normalize_team_name(teams.get("away", {}).get("name", ""))
            
            score_direct = (similarity(norm_home, fix_home) + similarity(norm_away, fix_away)) / 2
            score_cross = (similarity(norm_home, fix_away) + similarity(norm_away, fix_home)) / 2
            
            score = max(score_direct, score_cross)
            
            if score > best_score:
                best_score = score
                best_match = fix
                
        if best_score >= 0.6:
            log.info(f"Match found: {home} vs {away} (confidence={best_score:.2f})")
            return best_match
            
        log.warning(f"No match for: {home} vs {away}")
        return None
        
    def parse_fixture_data(self, fixture: dict) -> dict:
        """
        Estrae dati rilevanti dalla fixture.
        
        Returns:
            dict con minute, injury_time, goals, events, etc.
        """
        if not fixture:
            return {}
            
        fixture_data = fixture.get("fixture", {})
        status = fixture_data.get("status", {})
        teams = fixture.get("teams", {})
        goals = fixture.get("goals", {})
        events = fixture.get("events", [])
        
        minute = status.get("elapsed")
        injury_time = None
        
        extra = status.get("extra")
        if extra:
            injury_time = extra
            
        period_code = status.get("short", "")
        period_map = {
            "1H": "1T",
            "HT": "HT",
            "2H": "2T",
            "ET": "ET",
            "P": "PEN",
            "FT": "FT",
            "AET": "FT",
            "PEN": "PEN",
            "SUSP": "SUSPENDED",
            "INT": "INTERRUPTED",
            "PST": "POSTPONED",
            "CANC": "CANCELLED",
            "ABD": "ABANDONED",
            "NS": "NOT_STARTED",
            "LIVE": "LIVE"
        }
        period = period_map.get(period_code, "UNKNOWN")
        
        goal_minutes = []
        goal_events = {}
        
        for evt in events:
            if evt.get("type") == "Goal":
                evt_min = evt.get("time", {}).get("elapsed", 0)
                player = evt.get("player", {}).get("name", "")
                team = evt.get("team", {}).get("name", "")
                
                goal_minutes.append(evt_min)
                goal_events[evt_min] = f"{player} ({team}) {evt_min}'"
                
        return {
            "minute": minute,
            "injury_time": injury_time,
            "goals_home": goals.get("home", 0) or 0,
            "goals_away": goals.get("away", 0) or 0,
            "goal_minutes": goal_minutes,
            "goal_events": goal_events,
            "period": period,
            "home_team": teams.get("home", {}).get("name", ""),
            "away_team": teams.get("away", {}).get("name", ""),
            "fixture_id": fixture_data.get("id")
        }


class APIFootballWorker:
    """
    Worker thread per polling API-Football.
    
    - Esegue in background
    - Aggiorna LiveContextStore
    - Non blocca mai la UI
    """
    
    def __init__(self, context_store, interval: float = 10.0):
        self.client = APIFootballClient()
        self.context_store = context_store
        self.interval = interval
        
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._current_home = ""
        self._current_away = ""
        self._current_fixture_id: Optional[int] = None
        
    def set_match(self, home: str, away: str):
        """Imposta match da monitorare."""
        self._current_home = home
        self._current_away = away
        self._current_fixture_id = None
        log.info(f"Monitoring: {home} vs {away}")
        
    def clear_match(self):
        """Rimuove match corrente."""
        self._current_home = ""
        self._current_away = ""
        self._current_fixture_id = None
        self.context_store.clear()
        
    def start(self):
        """Avvia worker thread."""
        if self._running:
            return
            
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._thread.start()
        log.info("API-Football worker started")
        
    def stop(self):
        """Ferma worker thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        log.info("API-Football worker stopped")
        
    def _worker_loop(self):
        """Loop principale worker."""
        while self._running:
            try:
                self._poll()
            except Exception as e:
                log.error(f"API-Football poll error: {e}")
                
            time.sleep(self.interval)
            
    def _poll(self):
        """Singolo ciclo di polling."""
        if not self._current_home or not self._current_away:
            return
            
        fixture = self.client.find_fixture_by_teams(
            self._current_home,
            self._current_away
        )
        
        if not fixture:
            self.context_store.update(minute=None, period="UNKNOWN")
            return
            
        data = self.client.parse_fixture_data(fixture)
        
        if not data:
            return
            
        danger = False
        minute = data.get("minute")
        if minute and minute >= 80:
            danger = True
        if data.get("injury_time"):
            danger = True
            
        self.context_store.update(
            minute=data.get("minute"),
            injury_time=data.get("injury_time"),
            goals_home=data.get("goals_home", 0),
            goals_away=data.get("goals_away", 0),
            goal_minutes=data.get("goal_minutes", []),
            goal_events=data.get("goal_events", {}),
            period=data.get("period", "UNKNOWN"),
            home_team=data.get("home_team", ""),
            away_team=data.get("away_team", ""),
            danger=danger
        )
