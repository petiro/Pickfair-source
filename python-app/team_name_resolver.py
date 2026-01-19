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

    # Comprehensive stopwords for worldwide team name matching
    STOPWORDS = [
        # Generic football terms
        "fc", "cf", "calcio", "football", "club", "futbol", "futebol", "fussball",
        # European prefixes
        "ac", "afc", "sc", "as", "us", "ss", "ssc", "asd", "usd",  # Italy
        "fk", "nk", "sk", "hk", "bk",  # Eastern Europe
        "if", "bk", "ik", "ff",  # Scandinavia
        "sv", "tsv", "vfb", "vfl", "fsv", "spvgg", "bsc", "bsv",  # Germany
        "rcd", "sd", "ud", "cd", "ad", "ue", "ce",  # Spain
        "sl", "gd", "cf", "csd", "cdf",  # Portugal
        "aek", "paok", "pas", "gf", "ae", "ao", "gs",  # Greece
        # South American prefixes
        "se", "ec", "ca", "cr", "aa", "ad", "cd", "ce", "rc", "gr",  # Brazil
        "sp", "rj", "mg", "pr", "rs", "ba", "pe", "go",  # Brazil state codes
        "csd", "cd", "deportivo", "atletico", "atl",  # Argentina/others
        # British prefixes
        "afc", "fc", "utd", "united", "city", "town", "rovers", "wanderers",
        # Asian prefixes
        "jef", "yokohama", "cerezo", "urawa", "kawasaki",  # Japan
        "beijing", "shanghai", "guangzhou", "shandong",  # China
        "fc seoul", "ulsan", "jeonbuk", "pohang",  # Korea
        # Other regions
        "al", "el",  # Arabic (careful - also part of names)
        # Youth/Women/Reserve
        "women", "w", "ladies", "femminile", "damen", "frauen",
        "u23", "u21", "u20", "u19", "u18", "u17", "u16", "u15",
        "reserves", "reserve", "b", "ii", "2", "primavera", "juvenil",
        "team", "squad", "youth", "jong", "ii", "iii",
        # Common suffixes
        "1893", "1896", "1898", "1899", "1900", "1904", "1905", "1907", "1908", "1909",
        "1910", "1911", "1912", "1913", "1919", "1920", "1925", "1927", "1932", "1945",
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
    # ITALY - Serie A
    "inter": ["inter milan", "internazionale", "inter milano", "fc internazionale"],
    "milan": ["ac milan", "ac milano", "rossoneri"],
    "juventus": ["juve", "juventus turin", "juventus torino"],
    "napoli": ["ssc napoli", "napoli calcio"],
    "roma": ["as roma", "roma calcio"],
    "lazio": ["ss lazio", "lazio roma"],
    "atalanta": ["atalanta bergamo", "atalanta bc"],
    "fiorentina": ["acf fiorentina", "fiorentina firenze", "viola"],
    "torino": ["torino fc", "toro"],
    "bologna": ["bologna fc"],
    "udinese": ["udinese calcio"],
    "sassuolo": ["us sassuolo"],
    "empoli": ["empoli fc"],
    "cagliari": ["cagliari calcio"],
    "verona": ["hellas verona", "verona fc"],
    "genoa": ["genoa cfc", "genoa cricket"],
    "sampdoria": ["uc sampdoria", "samp"],
    "monza": ["ac monza"],
    "lecce": ["us lecce"],
    "salernitana": ["us salernitana"],
    "frosinone": ["frosinone calcio"],
    "spezia": ["spezia calcio"],
    "cremonese": ["us cremonese"],
    "venezia": ["venezia fc"],
    
    # ENGLAND - Premier League
    "man united": ["manchester united", "manchester utd", "man utd", "mufc"],
    "man city": ["manchester city", "mcfc"],
    "liverpool": ["liverpool fc", "lfc"],
    "arsenal": ["arsenal fc", "gunners"],
    "chelsea": ["chelsea fc", "cfc"],
    "tottenham": ["spurs", "tottenham hotspur", "thfc"],
    "newcastle": ["newcastle united", "newcastle utd", "nufc"],
    "aston villa": ["villa", "avfc"],
    "west ham": ["west ham united", "west ham utd", "hammers"],
    "brighton": ["brighton hove albion", "brighton and hove albion"],
    "wolves": ["wolverhampton", "wolverhampton wanderers"],
    "crystal palace": ["palace", "cpfc"],
    "everton": ["everton fc", "toffees"],
    "brentford": ["brentford fc"],
    "nottm forest": ["nottingham forest", "forest", "nffc"],
    "fulham": ["fulham fc"],
    "bournemouth": ["afc bournemouth"],
    "burnley": ["burnley fc"],
    "sheffield utd": ["sheffield united", "sheffield u", "blades"],
    "luton": ["luton town"],
    
    # SPAIN - La Liga
    "real madrid": ["real", "madrid", "rmcf"],
    "barcelona": ["barca", "fc barcelona", "fcb"],
    "atletico madrid": ["atletico", "atl madrid", "atleti"],
    "sevilla": ["sevilla fc"],
    "real sociedad": ["sociedad", "la real"],
    "villarreal": ["villarreal cf", "yellow submarine"],
    "betis": ["real betis", "betis sevilla"],
    "athletic bilbao": ["athletic", "athletic club"],
    "valencia": ["valencia cf"],
    "osasuna": ["ca osasuna"],
    "celta vigo": ["celta", "rc celta"],
    "rayo vallecano": ["rayo"],
    "mallorca": ["rcd mallorca"],
    "getafe": ["getafe cf"],
    "cadiz": ["cadiz cf"],
    "alaves": ["deportivo alaves"],
    "girona": ["girona fc"],
    "almeria": ["ud almeria"],
    "granada": ["granada cf"],
    "las palmas": ["ud las palmas"],
    
    # GERMANY - Bundesliga
    "bayern": ["bayern munich", "bayern munchen", "bayern münchen", "fcb"],
    "dortmund": ["borussia dortmund", "bvb"],
    "leipzig": ["rb leipzig", "rasenballsport leipzig"],
    "leverkusen": ["bayer leverkusen", "bayer 04"],
    "frankfurt": ["eintracht frankfurt", "sge"],
    "wolfsburg": ["vfl wolfsburg"],
    "gladbach": ["borussia monchengladbach", "borussia mgladbach", "bmg"],
    "freiburg": ["sc freiburg"],
    "hoffenheim": ["tsg hoffenheim", "1899 hoffenheim"],
    "union berlin": ["1 fc union berlin", "union"],
    "koln": ["1 fc koln", "fc koln", "cologne"],
    "mainz": ["mainz 05", "1 fsv mainz"],
    "augsburg": ["fc augsburg"],
    "stuttgart": ["vfb stuttgart"],
    "werder bremen": ["werder", "sv werder"],
    "bochum": ["vfl bochum"],
    "heidenheim": ["1 fc heidenheim"],
    "darmstadt": ["sv darmstadt 98"],
    
    # FRANCE - Ligue 1
    "psg": ["paris saint germain", "paris sg", "paris saint-germain", "paris"],
    "marseille": ["olympique marseille", "om"],
    "lyon": ["olympique lyon", "olympique lyonnais", "ol"],
    "monaco": ["as monaco", "asm"],
    "lille": ["losc lille", "losc"],
    "nice": ["ogc nice"],
    "lens": ["rc lens"],
    "rennes": ["stade rennais", "stade rennes"],
    "nantes": ["fc nantes"],
    "montpellier": ["montpellier hsc"],
    "strasbourg": ["rc strasbourg", "racing strasbourg"],
    "reims": ["stade reims", "stade de reims"],
    "toulouse": ["toulouse fc"],
    "brest": ["stade brestois", "stade brest"],
    "lorient": ["fc lorient"],
    "clermont": ["clermont foot"],
    "metz": ["fc metz"],
    "le havre": ["le havre ac"],
    
    # PORTUGAL - Primeira Liga
    "benfica": ["sl benfica", "sport lisboa benfica"],
    "porto": ["fc porto", "fcp"],
    "sporting": ["sporting cp", "sporting lisbon", "sporting lisboa"],
    "braga": ["sc braga", "sporting braga"],
    "guimaraes": ["vitoria guimaraes", "vitoria sc"],
    "famalicao": ["fc famalicao"],
    "estoril": ["estoril praia"],
    "rio ave": ["rio ave fc"],
    "boavista": ["boavista fc"],
    "casa pia": ["casa pia ac"],
    "arouca": ["fc arouca"],
    "vizela": ["fc vizela"],
    "chaves": ["gd chaves"],
    "gil vicente": ["gil vicente fc"],
    "farense": ["sc farense"],
    "moreirense": ["moreirense fc"],
    "portimonense": ["portimonense sc"],
    
    # NETHERLANDS - Eredivisie
    "ajax": ["ajax amsterdam", "afc ajax"],
    "psv": ["psv eindhoven"],
    "feyenoord": ["feyenoord rotterdam"],
    "az": ["az alkmaar"],
    "twente": ["fc twente"],
    "utrecht": ["fc utrecht"],
    "vitesse": ["vitesse arnhem"],
    "heerenveen": ["sc heerenveen"],
    "groningen": ["fc groningen"],
    
    # BRAZIL - Serie A/B
    "palmeiras": ["se palmeiras", "sociedade esportiva palmeiras"],
    "flamengo": ["cr flamengo", "clube de regatas flamengo"],
    "corinthians": ["sc corinthians", "sport club corinthians"],
    "sao paulo": ["sao paulo fc", "spfc"],
    "santos": ["santos fc"],
    "gremio": ["gremio porto alegre", "gremio fbpa"],
    "internacional": ["sc internacional", "inter porto alegre"],
    "atletico mg": ["atletico mineiro", "galo"],
    "fluminense": ["fluminense fc"],
    "botafogo": ["botafogo fr", "botafogo rj"],
    "vasco": ["vasco da gama", "cr vasco da gama"],
    "cruzeiro": ["cruzeiro ec"],
    "athletico pr": ["athletico paranaense", "cap", "atletico paranaense"],
    "fortaleza": ["fortaleza ec"],
    "bahia": ["ec bahia", "esporte clube bahia"],
    "cuiaba": ["cuiaba ec"],
    "goias": ["goias ec"],
    "bragantino": ["red bull bragantino", "rb bragantino"],
    "america mg": ["america mineiro"],
    "coritiba": ["coritiba fc"],
    
    # ARGENTINA - Primera Division
    "boca juniors": ["boca", "cabj"],
    "river plate": ["river", "carp"],
    "racing": ["racing club", "racing avellaneda"],
    "independiente": ["ca independiente"],
    "san lorenzo": ["san lorenzo de almagro"],
    "huracan": ["ca huracan"],
    "velez": ["velez sarsfield"],
    "estudiantes": ["estudiantes la plata"],
    "lanus": ["ca lanus"],
    "defensa y justicia": ["defensa justicia"],
    "talleres": ["talleres cordoba"],
    "argentinos jrs": ["argentinos juniors"],
    "newells": ["newells old boys"],
    "rosario central": ["rosario"],
    "godoy cruz": ["godoy cruz antonio tomba"],
    "union santa fe": ["club atletico union"],
    "colon": ["colon santa fe"],
    "gimnasia lp": ["gimnasia la plata"],
    "platense": ["ca platense"],
    "tigre": ["ca tigre"],
    "banfield": ["ca banfield"],
    "sarmiento": ["sarmiento junin"],
    "central cordoba": ["central cordoba sde"],
    
    # TURKEY - Super Lig
    "galatasaray": ["galatasaray sk", "gala", "gs"],
    "fenerbahce": ["fenerbahce sk", "fener", "fb"],
    "besiktas": ["besiktas jk", "bjk"],
    "trabzonspor": ["trabzon", "ts"],
    "basaksehir": ["istanbul basaksehir"],
    "kasimpasa": ["kasimpasa sk"],
    "konyaspor": ["konya"],
    "antalyaspor": ["antalya"],
    "sivasspor": ["sivas"],
    "alanyaspor": ["alanya"],
    "kayserispor": ["kayseri"],
    "gaziantep": ["gaziantep fk"],
    "hatayspor": ["hatay"],
    "adana demirspor": ["adana demir"],
    "rizespor": ["caykur rizespor"],
    
    # GREECE - Super League
    "olympiacos": ["olympiacos piraeus", "olympiakos"],
    "panathinaikos": ["panathinaikos athens", "pao"],
    "aek athens": ["aek", "aek fc"],
    "paok": ["paok thessaloniki", "paok salonika"],
    "aris": ["aris thessaloniki", "aris salonika"],
    
    # OTHER MAJOR CLUBS
    "celtic": ["celtic glasgow", "celtic fc"],
    "rangers": ["rangers glasgow", "rangers fc"],
    "anderlecht": ["rsc anderlecht"],
    "club brugge": ["club bruges"],
    "ajax": ["ajax amsterdam"],
    "psv": ["psv eindhoven"],
    "shakhtar": ["shakhtar donetsk"],
    "dynamo kyiv": ["dynamo kiev"],
    "zenit": ["zenit st petersburg", "zenit saint petersburg"],
    "spartak moscow": ["spartak moskva"],
    "cska moscow": ["cska moskva"],
    "red star": ["red star belgrade", "crvena zvezda"],
    "partizan": ["partizan belgrade"],
    "dinamo zagreb": ["gnk dinamo zagreb"],
    "hajduk split": ["hnk hajduk split"],
    "salzburg": ["rb salzburg", "red bull salzburg"],
    "rapid vienna": ["rapid wien"],
    "young boys": ["bsc young boys"],
    "basel": ["fc basel"],
    "copenhagen": ["fc copenhagen", "fc kobenhavn"],
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
