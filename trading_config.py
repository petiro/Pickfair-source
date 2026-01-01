"""
Trading Configuration - Costanti configurabili per il trading

Questo modulo centralizza tutte le costanti di configurazione
per evitare dipendenze circolari tra moduli.
"""

# Book % thresholds
BOOK_WARNING = 105.0   # Warning (arancione) - tipico per calcio
BOOK_BLOCK = 110.0     # Blocco submit (rosso)

# Stake limits (Betfair Italia)
MIN_STAKE = 2.0        # Minimo €2 per ordine
MAX_WIN = 10000.0      # Vincita massima €10.000

# Commission
DEFAULT_COMMISSION = 4.5  # 4.5% Betfair Italia

# Session
SESSION_TIMEOUT_MIN = 20  # Timeout sessione 20 minuti

# Simulation defaults
SIM_INITIAL_BALANCE = 10000.0  # Bilancio iniziale simulazione

# AI Mixed Dutching
PROFIT_EPSILON = 0.50  # Tolleranza max €0.50 varianza profitto tra scenari

# Auto-Green
AUTO_GREEN_DELAY_SEC = 2.5  # Grace period prima di attivare auto-green
