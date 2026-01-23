"""
API-Football Integration
========================
Worker thread per fetch dati live da API-Football.

REGOLE CRITICHE:
- Questo modulo NON prende decisioni di trading
- Betfair = MASTER (sempre)
- API-Football = SENSOR (solo contesto visivo)
- ZERO blocchi UI
- ZERO blocchi Betfair

Caratteristiche:
- Thread-safe
- Timeout corto (4s)
- Retry conservativo (max 1)
- Cache locale (30s TTL)
- Safe-mode automatico su errori
- Polling 15-20s

API Docs: https://www.api-football.com/documentation-v3
"""

import os
import time
import threading
import logging
import requests
from typing import Optional, Dict, Any, List, Callable

from thread_guard import GuardedAPIMeta

log = logging.getLogger("APIFootball")


class APIFootballClient(metaclass=GuardedAPIMeta):
    """
    Client robusto per API-Football v3.
    
    Rate limits:
    - Free: 100 requests/day
    - Basic: 7500 requests/day
    
    Features:
    - Timeout corto (4s default)
    - Retry minimo (1)
    - Cache locale (60s TTL - extended for efficiency)
    - Rate limiting giornaliero
    - Thread-safe
    
    All public methods are automatically protected by @assert_not_ui_thread
    via the GuardedAPIMeta metaclass. This prevents UI freezes.
    """
    
    BASE_URL = "https://v3.football.api-sports.io"
    DEFAULT_KEY = "9d726ff17ef61ad94aa372ebcaf99cd9"
    
    # Rate limit config
    DAILY_LIMIT_FREE = 100
    DAILY_LIMIT_BASIC = 7500
    
    def __init__(
        self, 
        api_key: Optional[str] = None,
        timeout: float = 4.0,
        max_retry: int = 1,
        cache_ttl: int = 60,  # Extended from 30s to 60s
        daily_limit: int = 100  # Free tier default
    ):
        self.api_key = api_key or os.environ.get("API_FOOTBALL_KEY", "") or self.DEFAULT_KEY
        self.timeout = timeout
        self.max_retry = max_retry
        self.cache_ttl = cache_ttl
        self.daily_limit = daily_limit
        
        self.session = requests.Session()
        self.session.headers.update({
            "x-apisports-key": self.api_key
        })
        
        self._lock = threading.RLock()
        self._cache: Dict[str, Any] = {}
        self._cache_time: Dict[str, float] = {}
        self._status: str = "INIT"
        self._last_error: Optional[str] = None
        
        # Rate limiting
        self._requests_today: int = 0
        self._requests_date: str = ""
        self._rate_limited: bool = False
        
        self.force_timeout: bool = False
        self.forced_delay: int = 0
        
    @property
    def status(self) -> str:
        """Stato corrente: INIT | OK | STALE | UNAVAILABLE"""
        return self._status
        
    def _check_rate_limit(self) -> bool:
        """Check and update daily rate limit. Returns True if OK, False if limited."""
        from datetime import date
        today = date.today().isoformat()
        
        with self._lock:
            # Reset counter on new day
            if self._requests_date != today:
                self._requests_date = today
                self._requests_today = 0
                self._rate_limited = False
                log.debug(f"[API-Football] Rate limit reset for {today}")
            
            # Check limit
            if self._requests_today >= self.daily_limit:
                if not self._rate_limited:
                    log.warning(f"[API-Football] Daily limit reached: {self._requests_today}/{self.daily_limit}")
                    self._rate_limited = True
                return False
            
            return True
    
    def _increment_request_count(self):
        """Increment daily request counter."""
        with self._lock:
            self._requests_today += 1
            if self._requests_today % 10 == 0:
                log.debug(f"[API-Football] Requests today: {self._requests_today}/{self.daily_limit}")
    
    def get_requests_remaining(self) -> int:
        """Get remaining requests for today."""
        with self._lock:
            return max(0, self.daily_limit - self._requests_today)
    
    def _request(self, endpoint: str, params: dict = None) -> Optional[dict]:
        """
        Esegue richiesta API con caching, rate limiting e retry.
        NON blocca per piu' di timeout * (max_retry + 1) secondi.
        """
        if not self.api_key:
            log.warning("API_FOOTBALL_KEY non configurata")
            self._status = "UNAVAILABLE"
            return None
        
        cache_key = f"{endpoint}:{str(params)}"
        now = time.time()
        
        # Check cache FIRST (no API call needed)
        with self._lock:
            if cache_key in self._cache:
                cache_age = now - self._cache_time.get(cache_key, 0)
                if cache_age < self.cache_ttl:
                    log.debug(f"[API-Football] Cache hit: {endpoint} (age: {cache_age:.0f}s)")
                    return self._cache[cache_key]
        
        # Check rate limit BEFORE making request
        if not self._check_rate_limit():
            self._status = "RATE_LIMITED"
            return self._get_cached(cache_key)  # Return stale cache if available
        
        if self.force_timeout:
            log.warning("[STRESS] Simulated API timeout")
            time.sleep(12)
            self._status = "UNAVAILABLE"
            return self._get_cached(cache_key)
            
        if self.forced_delay > 0:
            log.warning(f"[STRESS] Simulated delay {self.forced_delay}s")
            time.sleep(self.forced_delay)
        
        attempt = 0
        while attempt <= self.max_retry:
            try:
                url = f"{self.BASE_URL}/{endpoint}"
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                
                # Count this request (successful HTTP call)
                self._increment_request_count()
                
                data = resp.json()
                
                if data.get("errors"):
                    log.error(f"API-Football error: {data['errors']}")
                    self._status = "UNAVAILABLE"
                    self._last_error = str(data['errors'])
                    return self._get_cached(cache_key)
                    
                with self._lock:
                    self._cache[cache_key] = data
                    self._cache_time[cache_key] = now
                    
                self._status = "OK"
                self._last_error = None
                return data
                
            except requests.Timeout:
                log.warning(f"[API-Football] Timeout (attempt {attempt + 1})")
                attempt += 1
                if attempt <= self.max_retry:
                    time.sleep(1)
                    
            except requests.RequestException as e:
                log.error(f"API-Football request failed: {e}")
                self._status = "UNAVAILABLE"
                self._last_error = str(e)
                return self._get_cached(cache_key)
                
        self._status = "STALE"
        return self._get_cached(cache_key)
        
    def _get_cached(self, cache_key: str) -> Optional[dict]:
        """Ritorna dato da cache se disponibile."""
        with self._lock:
            return self._cache.get(cache_key)
            
    def get_live_fixtures(self) -> List[dict]:
        """Ottiene tutte le partite live."""
        data = self._request("fixtures", {"live": "all"})
        if not data:
            return []
        return data.get("response", [])
        
    def get_fixture_by_id(self, fixture_id: int) -> Optional[dict]:
        """Ottiene singola partita per ID."""
        data = self._request("fixtures", {"id": fixture_id})
        if not data:
            return None
        response = data.get("response", [])
        return response[0] if response else None
        
    def find_fixture_by_teams(self, home: str, away: str) -> Optional[dict]:
        """
        Trova fixture per nomi squadre (matching fuzzy).
        Usa TeamNameResolver per matching robusto.
        """
        try:
            from team_name_resolver import get_resolver
            resolver = get_resolver()
        except ImportError:
            resolver = None
            
        fixtures = self.get_live_fixtures()
        if not fixtures:
            return None
            
        best_match = None
        best_score = 0.0
        
        for fix in fixtures:
            teams = fix.get("teams", {})
            api_home = teams.get("home", {}).get("name", "")
            api_away = teams.get("away", {}).get("name", "")
            
            betfair_event = f"{home} v {away}"
            
            if resolver:
                matched, reason = resolver.match_event(api_home, api_away, betfair_event)
                if matched:
                    score = 1.0 if reason == "FULL_MATCH" else 0.8
                    if score > best_score:
                        best_score = score
                        best_match = fix
            else:
                from difflib import SequenceMatcher
                
                def norm(s):
                    return s.lower().replace("fc", "").replace("cf", "").strip()
                    
                h_sim = SequenceMatcher(None, norm(home), norm(api_home)).ratio()
                a_sim = SequenceMatcher(None, norm(away), norm(api_away)).ratio()
                score = (h_sim + a_sim) / 2
                
                if score > best_score and score >= 0.6:
                    best_score = score
                    best_match = fix
                    
        if best_match:
            log.info(f"[API-Football] Match found: score={best_score:.2f}")
            
        return best_match
        
    def parse_fixture(self, fixture: dict) -> Dict[str, Any]:
        """
        Estrae dati utili da fixture.
        
        Returns:
            {
                "fixture_id": int,
                "status": str,  # 1H, 2H, HT, FT, etc
                "minute": int,
                "extra_time": int,
                "home_goals": int,
                "away_goals": int,
                "home_team": str,
                "away_team": str,
                "events": [...],
                "updated_at": float
            }
        """
        fixture_data = fixture.get("fixture", {})
        status = fixture_data.get("status", {})
        goals = fixture.get("goals", {})
        teams = fixture.get("teams", {})
        
        return {
            "fixture_id": fixture_data.get("id"),
            "status": status.get("short", ""),
            "minute": status.get("elapsed", 0),
            "extra_time": status.get("extra") or 0,
            "home_goals": goals.get("home", 0) or 0,
            "away_goals": goals.get("away", 0) or 0,
            "home_team": teams.get("home", {}).get("name", ""),
            "away_team": teams.get("away", {}).get("name", ""),
            "events": self._extract_events(fixture.get("events", [])),
            "updated_at": time.time()
        }
        
    def _extract_events(self, events_raw: list) -> List[dict]:
        """Estrae goal, cartellini rossi, VAR."""
        events = []
        for ev in events_raw:
            ev_type = ev.get("type", "")
            ev_detail = ev.get("detail", "")
            
            if ev_type in ("Goal", "Card", "Var"):
                if ev_type == "Card" and ev_detail != "Red Card":
                    continue
                    
                events.append({
                    "minute": ev.get("time", {}).get("elapsed", 0),
                    "extra": ev.get("time", {}).get("extra"),
                    "team": ev.get("team", {}).get("name", ""),
                    "type": ev_type,
                    "detail": ev_detail,
                    "player": ev.get("player", {}).get("name", "")
                })
        return events


class APIFootballWorker:
    """
    Worker thread per polling API-Football.
    
    Features:
    - Thread daemon (auto-termina)
    - Polling configurabile (30s default - optimized to save API calls)
    - Callbacks thread-safe
    - Stop graceful
    - Zero blocchi UI
    """
    
    GOAL_CONFIRM_WINDOW = 30
    
    def __init__(
        self, 
        client: Optional[APIFootballClient] = None,
        poll_interval: int = 30  # Extended from 15s to 30s to save API calls
    ):
        self.client = client or APIFootballClient()
        self.poll_interval = poll_interval
        
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._current_fixture_id: Optional[int] = None
        self._current_teams: Optional[tuple] = None
        
        self._callbacks: List[Callable[[dict], None]] = []
        self._lock = threading.RLock()
        
        self._last_goals: Optional[tuple] = None
        self._goal_pending = False
        self._goal_pending_ts: float = 0
        
    def register_callback(self, cb: Callable[[dict], None]):
        """Registra callback per dati live."""
        with self._lock:
            self._callbacks.append(cb)
            
    def set_match(self, home: str, away: str):
        """Imposta match da monitorare per nomi squadre."""
        with self._lock:
            self._current_teams = (home, away)
            self._current_fixture_id = None
            self._last_goals = None
            
    def set_fixture_id(self, fixture_id: int):
        """Imposta fixture ID diretto."""
        with self._lock:
            self._current_fixture_id = fixture_id
            self._current_teams = None
            self._last_goals = None
            
    def start(self):
        """Avvia worker thread."""
        if self._running:
            return
            
        self._running = True
        self._thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="APIFootball-Worker"
        )
        self._thread.start()
        log.info("[API-Football] Worker started")
        
    def stop(self):
        """Ferma worker thread."""
        self._running = False
        log.info("[API-Football] Worker stopped")
        
    def _worker_loop(self):
        """Loop principale polling."""
        while self._running:
            try:
                self._poll()
            except Exception as e:
                log.error(f"[API-Football] Worker error: {e}")
                
            time.sleep(self.poll_interval)
            
    def _poll(self):
        """Singola iterazione polling."""
        with self._lock:
            teams = self._current_teams
            fixture_id = self._current_fixture_id
            
        if not teams and not fixture_id:
            return
            
        fixture = None
        
        if fixture_id:
            fixture = self.client.get_fixture_by_id(fixture_id)
        elif teams:
            fixture = self.client.find_fixture_by_teams(teams[0], teams[1])
            if fixture:
                with self._lock:
                    self._current_fixture_id = fixture.get("fixture", {}).get("id")
                    
        if not fixture:
            self._notify_callbacks({
                "status": "UNAVAILABLE",
                "minute": None,
                "extra_time": 0,
                "home_goals": 0,
                "away_goals": 0,
                "events": [],
                "goal_detected": False,
                "updated_at": time.time()
            })
            return
            
        data = self.client.parse_fixture(fixture)
        
        goal_detected = self._check_goal(data)
        data["goal_detected"] = goal_detected
        data["client_status"] = self.client.status
        
        self._notify_callbacks(data)
        
    def _check_goal(self, data: dict) -> bool:
        """Rileva nuovo goal (anti-spam)."""
        current = (data.get("home_goals", 0), data.get("away_goals", 0))
        
        with self._lock:
            last = self._last_goals
            self._last_goals = current
            
            if last is None:
                return False
                
            if current != last:
                log.info(f"[API-Football] GOAL detected: {last} -> {current}")
                self._goal_pending = True
                self._goal_pending_ts = time.time()
                return True
                
            if self._goal_pending:
                if time.time() - self._goal_pending_ts > self.GOAL_CONFIRM_WINDOW:
                    self._goal_pending = False
                    
        return False
        
    def _notify_callbacks(self, data: dict):
        """Notifica tutti i callback."""
        with self._lock:
            callbacks = list(self._callbacks)
            
        for cb in callbacks:
            try:
                cb(data)
            except Exception as e:
                log.error(f"[API-Football] Callback error: {e}")
