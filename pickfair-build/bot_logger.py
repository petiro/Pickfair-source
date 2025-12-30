"""
Bot Logger - Sistema logging completo per Pickfair.

Features:
    - Console + File rotante (5 file x 10MB)
    - Livelli configurabili (DEBUG/INFO/WARNING/ERROR)
    - Alert Telegram per errori critici
    - Prefissi chiari per ogni modulo
    - Traceback completi su eccezioni
"""

import logging
from logging.handlers import RotatingFileHandler
import sys
import os
from pathlib import Path
from typing import Optional, Callable
from datetime import datetime


# ==============================================================================
# CONFIGURAZIONE
# ==============================================================================

LOG_DIR = Path.home() / "AppData" / "Roaming" / "Pickfair" / "logs"
LOG_FILE = "pickfair.log"
MAX_LOG_SIZE = 10 * 1024 * 1024  # 10MB
BACKUP_COUNT = 5

# Crea directory se non esiste
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ==============================================================================
# FORMATTER PERSONALIZZATO
# ==============================================================================

class BotFormatter(logging.Formatter):
    """Formatter con colori per console e prefissi chiari."""
    
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
        'RESET': '\033[0m'
    }
    
    def __init__(self, use_colors: bool = True):
        super().__init__(
            fmt="[%(asctime)s] [%(levelname)s] [%(module)s:%(lineno)d] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        self.use_colors = use_colors
    
    def format(self, record):
        if self.use_colors and record.levelname in self.COLORS:
            color = self.COLORS[record.levelname]
            reset = self.COLORS['RESET']
            record.levelname = f"{color}{record.levelname}{reset}"
        return super().format(record)


# ==============================================================================
# SETUP LOGGER PRINCIPALE
# ==============================================================================

def setup_bot_logger(
    name: str = "Pickfair",
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    telegram_callback: Optional[Callable] = None
) -> logging.Logger:
    """
    Configura logger principale del bot.
    
    Args:
        name: Nome del logger
        console_level: Livello log console (default INFO)
        file_level: Livello log file (default DEBUG)
        telegram_callback: Funzione per inviare alert Telegram
    
    Returns:
        Logger configurato
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    
    # Evita duplicati
    if logger.handlers:
        return logger
    
    # --- Console Handler ---
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(BotFormatter(use_colors=True))
    logger.addHandler(console_handler)
    
    # --- File Handler Rotante ---
    log_path = LOG_DIR / LOG_FILE
    file_handler = RotatingFileHandler(
        str(log_path),
        maxBytes=MAX_LOG_SIZE,
        backupCount=BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(BotFormatter(use_colors=False))
    logger.addHandler(file_handler)
    
    # --- Telegram Handler (opzionale) ---
    if telegram_callback:
        telegram_handler = TelegramHandler(telegram_callback)
        telegram_handler.setLevel(logging.ERROR)
        telegram_handler.setFormatter(BotFormatter(use_colors=False))
        logger.addHandler(telegram_handler)
    
    logger.info(f"[LOGGER] Inizializzato: console={logging.getLevelName(console_level)}, file={log_path}")
    
    return logger


# ==============================================================================
# TELEGRAM HANDLER
# ==============================================================================

class TelegramHandler(logging.Handler):
    """Handler per inviare log critici su Telegram."""
    
    def __init__(self, callback: Callable):
        super().__init__()
        self.callback = callback
        self.last_error_time = 0
        self.min_interval = 60  # Minimo 60s tra alert
    
    def emit(self, record):
        try:
            import time
            now = time.time()
            
            # Rate limit per non spammare
            if now - self.last_error_time < self.min_interval:
                return
            
            self.last_error_time = now
            msg = self.format(record)
            
            # Tronca messaggi lunghi
            if len(msg) > 4000:
                msg = msg[:3900] + "\n... [TRONCATO]"
            
            self.callback(msg)
            
        except Exception:
            pass  # Non fallire mai sul logging


# ==============================================================================
# LOGGER SINGLETON
# ==============================================================================

_bot_logger: Optional[logging.Logger] = None


def get_logger() -> logging.Logger:
    """Ottieni logger singleton."""
    global _bot_logger
    if _bot_logger is None:
        _bot_logger = setup_bot_logger()
    return _bot_logger


# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def log_debug(msg: str, prefix: str = ""):
    """Log DEBUG con prefisso opzionale."""
    logger = get_logger()
    if prefix:
        msg = f"[{prefix}] {msg}"
    logger.debug(msg)


def log_info(msg: str, prefix: str = ""):
    """Log INFO con prefisso opzionale."""
    logger = get_logger()
    if prefix:
        msg = f"[{prefix}] {msg}"
    logger.info(msg)


def log_warning(msg: str, prefix: str = ""):
    """Log WARNING con prefisso opzionale."""
    logger = get_logger()
    if prefix:
        msg = f"[{prefix}] {msg}"
    logger.warning(msg)


def log_error(msg: str, prefix: str = "", exc_info: bool = False):
    """Log ERROR con prefisso e traceback opzionale."""
    logger = get_logger()
    if prefix:
        msg = f"[{prefix}] {msg}"
    logger.error(msg, exc_info=exc_info)


def log_exception(msg: str, prefix: str = ""):
    """Log ERROR con traceback completo."""
    logger = get_logger()
    if prefix:
        msg = f"[{prefix}] {msg}"
    logger.exception(msg)


# ==============================================================================
# MODULO-SPECIFIC LOGGERS
# ==============================================================================

class ModuleLogger:
    """Logger con prefisso fisso per un modulo specifico."""
    
    def __init__(self, prefix: str):
        self.prefix = prefix
        self.logger = get_logger()
    
    def debug(self, msg: str):
        self.logger.debug(f"[{self.prefix}] {msg}")
    
    def info(self, msg: str):
        self.logger.info(f"[{self.prefix}] {msg}")
    
    def warning(self, msg: str):
        self.logger.warning(f"[{self.prefix}] {msg}")
    
    def error(self, msg: str, exc_info: bool = False):
        self.logger.error(f"[{self.prefix}] {msg}", exc_info=exc_info)
    
    def exception(self, msg: str):
        self.logger.exception(f"[{self.prefix}] {msg}")


# Loggers predefiniti per moduli
dutching_log = ModuleLogger("DUTCHING")
replace_log = ModuleLogger("REPLACE")
cashout_log = ModuleLogger("CASHOUT")
telegram_log = ModuleLogger("TELEGRAM")
betfair_log = ModuleLogger("BETFAIR")
stream_log = ModuleLogger("STREAM")
engine_log = ModuleLogger("ENGINE")
audit_log = ModuleLogger("AUDIT")


# ==============================================================================
# AUDIT LOGGER (per tracciamento operazioni)
# ==============================================================================

class AuditLogger:
    """Logger specializzato per audit operazioni trading."""
    
    def __init__(self, log_file: str = "audit.log"):
        self.log_path = LOG_DIR / log_file
        self.logger = logging.getLogger("Audit")
        self.logger.setLevel(logging.INFO)
        
        if not self.logger.handlers:
            handler = RotatingFileHandler(
                str(self.log_path),
                maxBytes=MAX_LOG_SIZE,
                backupCount=10,
                encoding='utf-8'
            )
            handler.setFormatter(logging.Formatter(
                "%(asctime)s|%(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            ))
            self.logger.addHandler(handler)
    
    def log_bet(self, bet_id: str, action: str, **kwargs):
        """Log operazione bet."""
        parts = [f"BET|{bet_id}|{action}"]
        for k, v in kwargs.items():
            parts.append(f"{k}={v}")
        self.logger.info("|".join(parts))
    
    def log_replace(self, bet_id: str, old_price: float, new_price: float, 
                    success: bool, reason: str = ""):
        """Log replace order."""
        status = "OK" if success else "FAIL"
        self.logger.info(f"REPLACE|{bet_id}|{old_price}->{new_price}|{status}|{reason}")
    
    def log_cashout(self, bet_id: str, profit: float, success: bool, 
                    lay_stake: float = 0, lay_price: float = 0):
        """Log cashout."""
        status = "OK" if success else "FAIL"
        self.logger.info(f"CASHOUT|{bet_id}|profit={profit:.2f}|{status}|lay={lay_stake:.2f}@{lay_price}")
    
    def log_telegram(self, chat_id: int, status: str, error: str = ""):
        """Log invio Telegram."""
        self.logger.info(f"TELEGRAM|{chat_id}|{status}|{error}")
    
    def log_dutching(self, market_id: str, selections: int, total_stake: float, 
                     target_profit: float):
        """Log dutching."""
        self.logger.info(f"DUTCHING|{market_id}|sel={selections}|stake={total_stake:.2f}|target={target_profit:.2f}")


# Singleton audit logger
_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    """Ottieni audit logger singleton."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


# ==============================================================================
# PERFORMANCE LOGGER
# ==============================================================================

class PerfLogger:
    """Logger per misurare performance operazioni."""
    
    def __init__(self, name: str):
        self.name = name
        self.start_time = None
        self.logger = get_logger()
    
    def __enter__(self):
        import time
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        import time
        elapsed = (time.time() - self.start_time) * 1000
        if exc_type:
            self.logger.error(f"[PERF] {self.name}: FAILED in {elapsed:.1f}ms - {exc_val}")
        else:
            level = "WARNING" if elapsed > 1000 else "DEBUG"
            getattr(self.logger, level.lower())(f"[PERF] {self.name}: {elapsed:.1f}ms")
        return False


def perf_log(name: str) -> PerfLogger:
    """Context manager per logging performance."""
    return PerfLogger(name)


# ==============================================================================
# INIZIALIZZAZIONE
# ==============================================================================

if __name__ == "__main__":
    # Test logging
    logger = setup_bot_logger()
    
    logger.debug("Test DEBUG")
    logger.info("Test INFO")
    logger.warning("Test WARNING")
    logger.error("Test ERROR")
    
    # Test module loggers
    dutching_log.info("Dutching test")
    replace_log.info("Replace test")
    
    # Test audit
    audit = get_audit_logger()
    audit.log_bet("12345", "PLACE", side="BACK", price=2.5, stake=10.0)
    audit.log_replace("12345", 2.5, 2.6, True)
    audit.log_cashout("12345", 5.50, True, 10.2, 2.55)
    
    # Test perf
    with perf_log("test_operation"):
        import time
        time.sleep(0.1)
    
    print(f"\nLog salvati in: {LOG_DIR}")
