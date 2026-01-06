"""
TeamNameResolver
----------------
Risoluzione robusta e sicura dei nomi squadra tra:
- Betfair Exchange
- API-Football

Caratteristiche:
- Normalizzazione aggressiva
- Alias automatici
- Supporto Women / U21 / Friendly
- DB-backed (auto apprendimento)
- NON blocca eventi (fail-safe)
"""

import re
import sqlite3
import unicodedata
import logging
import os
from typing import Tuple, Optional, List

log = logging.getLogger(__name__)


def normalize_team_name(name: str) -> str:
    """Normalizzazione aggressiva nome squadra."""
    if not name:
        return ""

    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")

    name = name.lower()

    name = name.replace("-", " ")
    name = name.replace(".", "")
    name = name.replace(",", "")
    name = name.replace("'", "")

    STOPWORDS = [
        "fc", "cf", "calcio", "football", "club", "afc", "sc", "ac",
        "women", "w", "ladies", "femminile",
        "u21", "u20", "u19", "u18", "u17", "u16",
        "reserves", "reserve", "b", "ii",
        "team", "squad", "youth"
    ]

    tokens = name.split()
    tokens = [t for t in tokens if t not in STOPWORDS]

    return " ".join(tokens).strip()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS team_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT NOT NULL,
    alias_name TEXT NOT NULL,
    source TEXT DEFAULT 'auto',
    confidence REAL DEFAULT 1.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(canonical_name, alias_name)
);

CREATE INDEX IF NOT EXISTS idx_canonical ON team_aliases(canonical_name);
CREATE INDEX IF NOT EXISTS idx_alias ON team_aliases(alias_name);
"""

BUILTIN_ALIASES = {
    "inter": ["inter milan", "internazionale", "inter milano"],
    "milan": ["ac milan", "ac milano"],
    "man united": ["manchester united", "manchester utd", "man utd"],
    "man city": ["manchester city"],
    "psg": ["paris saint germain", "paris sg", "paris saint-germain"],
    "bayern": ["bayern munich", "bayern munchen", "bayern münchen"],
    "roma": ["as roma", "roma"],
    "napoli": ["ssc napoli"],
    "juventus": ["juve"],
    "real madrid": ["real"],
    "atletico madrid": ["atletico", "atl madrid"],
    "barcelona": ["barca", "fc barcelona"],
    "tottenham": ["spurs", "tottenham hotspur"],
    "arsenal": ["arsenal fc"],
    "chelsea": ["chelsea fc"],
    "liverpool": ["liverpool fc"],
}


class TeamAliasDB:
    """Database per alias squadre con auto-apprendimento."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()
        self._load_builtin_aliases()

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path) as con:
                con.executescript(SCHEMA_SQL)
        except Exception as e:
            log.error(f"[TEAM-RESOLVER] DB init error: {e}")

    def _load_builtin_aliases(self):
        """Carica alias built-in nel DB."""
        try:
            with sqlite3.connect(self.db_path) as con:
                for canonical, aliases in BUILTIN_ALIASES.items():
                    for alias in aliases:
                        con.execute(
                            """
                            INSERT OR IGNORE INTO team_aliases
                            (canonical_name, alias_name, source, confidence)
                            VALUES (?, ?, 'builtin', 1.0)
                            """,
                            (canonical, alias)
                        )
        except Exception as e:
            log.debug(f"[TEAM-RESOLVER] Builtin aliases load: {e}")

    def add_alias(self, canonical: str, alias: str, source: str = "auto", confidence: float = 0.8):
        """Aggiunge alias al DB."""
        try:
            with sqlite3.connect(self.db_path) as con:
                con.execute(
                    """
                    INSERT OR IGNORE INTO team_aliases
                    (canonical_name, alias_name, source, confidence)
                    VALUES (?, ?, ?, ?)
                    """,
                    (canonical, alias, source, confidence)
                )
        except Exception as e:
            log.debug(f"[TEAM-RESOLVER] DB insert error: {e}")

    def get_aliases(self, name: str) -> List[Tuple[str, str]]:
        """Ottiene tutti gli alias per un nome."""
        try:
            with sqlite3.connect(self.db_path) as con:
                cur = con.execute(
                    """
                    SELECT canonical_name, alias_name
                    FROM team_aliases
                    WHERE canonical_name = ? OR alias_name = ?
                    """,
                    (name, name)
                )
                return cur.fetchall()
        except Exception as e:
            log.debug(f"[TEAM-RESOLVER] DB query error: {e}")
            return []


class TeamNameResolver:
    """
    Resolver principale per matching nomi squadre.
    
    Usa:
    1. Normalizzazione
    2. Match esatto
    3. Alias DB
    4. Partial match (fallback)
    """
    
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
            pickfair_dir = os.path.join(appdata, 'Pickfair')
            os.makedirs(pickfair_dir, exist_ok=True)
            db_path = os.path.join(pickfair_dir, 'team_aliases.db')
        
        self.db = TeamAliasDB(db_path)

    def teams_match(self, a: str, b: str) -> bool:
        """Verifica se due nomi squadra corrispondono."""
        na = normalize_team_name(a)
        nb = normalize_team_name(b)

        if not na or not nb:
            return False

        if na == nb:
            return True

        aliases_a = self.db.get_aliases(na)
        aliases_b = self.db.get_aliases(nb)

        for canonical, alias in aliases_a:
            if alias == nb or canonical == nb:
                return True

        for canonical, alias in aliases_b:
            if alias == na or canonical == na:
                return True

        if na in nb or nb in na:
            return True

        return False

    def match_event(
        self,
        api_home: str,
        api_away: str,
        betfair_event_name: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Verifica se evento Betfair corrisponde a fixture API-Football.
        
        Returns:
            (matched: bool, reason: str | None)
        """
        bf_parts = betfair_event_name.lower().replace(" v ", " vs ").split(" vs ")
        
        if len(bf_parts) == 2:
            bf_home = normalize_team_name(bf_parts[0])
            bf_away = normalize_team_name(bf_parts[1])
            
            api_h = normalize_team_name(api_home)
            api_a = normalize_team_name(api_away)
            
            home_ok = self.teams_match(api_h, bf_home) or self.teams_match(api_h, bf_away)
            away_ok = self.teams_match(api_a, bf_home) or self.teams_match(api_a, bf_away)
            
            if home_ok and away_ok:
                return True, "FULL_MATCH"
            
            if home_ok or away_ok:
                self.db.add_alias(api_h, api_a, source="auto_partial", confidence=0.5)
                return True, "PARTIAL_MATCH"

        bf_norm = normalize_team_name(betfair_event_name)
        api_h = normalize_team_name(api_home)
        api_a = normalize_team_name(api_away)
        
        if api_h in bf_norm and api_a in bf_norm:
            return True, "CONTAINS_BOTH"
        
        if api_h in bf_norm or api_a in bf_norm:
            return True, "CONTAINS_ONE"

        log.debug(
            f"[TEAM-RESOLVER] No match | "
            f"API: {api_home} vs {api_away} | "
            f"BF: {betfair_event_name}"
        )

        return False, None

    def learn_match(self, api_name: str, betfair_name: str):
        """Impara un nuovo alias da un match confermato."""
        na = normalize_team_name(api_name)
        nb = normalize_team_name(betfair_name)
        
        if na and nb and na != nb:
            self.db.add_alias(na, nb, source="learned", confidence=0.9)
            log.info(f"[TEAM-RESOLVER] Learned: {na} = {nb}")


resolver = None

def get_resolver() -> TeamNameResolver:
    """Singleton resolver."""
    global resolver
    if resolver is None:
        resolver = TeamNameResolver()
    return resolver
