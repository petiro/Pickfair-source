"""
Safety Logger - Logging automatico per errori di sicurezza

Registra automaticamente su file .txt tutti gli eventi critici:
- MixedDutchingError (errori calcolo dutching)
- AI bloccata per mercato non compatibile
- Auto-green negato (con motivo specifico)

I log sono salvati in %APPDATA%/Pickfair/logs/safety_YYYYMMDD.log
"""

import os
import logging
from datetime import datetime
from pathlib import Path
from enum import Enum
from typing import Optional, Dict, Any
import threading


class SafetyEventType(Enum):
    """Tipi di eventi di sicurezza loggati."""
    MIXED_DUTCHING_ERROR = "MIXED_DUTCH_ERR"
    AI_BLOCKED_MARKET = "AI_BLOCKED"
    AUTO_GREEN_DENIED = "AUTO_GREEN_DENIED"
    SAFE_MODE_TRIGGERED = "SAFE_MODE"
    PROFIT_VALIDATION_FAILED = "PROFIT_VAL_FAIL"
    MARKET_VALIDATION_FAILED = "MARKET_VAL_FAIL"


class SafetyLogger:
    """
    Logger dedicato per eventi di sicurezza del trading.
    
    Thread-safe, con rotazione giornaliera automatica.
    """
    
    _instance: Optional['SafetyLogger'] = None
    _lock = threading.Lock()
    
    def __new__(cls) -> 'SafetyLogger':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        self._log_lock = threading.Lock()
        self._log_dir = self._get_log_directory()
        self._current_date: Optional[str] = None
        self._file_handler: Optional[logging.FileHandler] = None
        self._logger = logging.getLogger("pickfair.safety")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        
        self._setup_logger()
    
    def _get_log_directory(self) -> Path:
        """Ottiene la directory per i log di sicurezza."""
        if os.name == 'nt':
            base = Path(os.environ.get('APPDATA', '.'))
        else:
            base = Path.home() / '.config'
        
        log_dir = base / 'Pickfair' / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir
    
    def _get_log_filename(self) -> str:
        """Genera nome file con data corrente."""
        return f"safety_{datetime.now().strftime('%Y%m%d')}.log"
    
    def _setup_logger(self):
        """Configura il logger con handler file."""
        today = datetime.now().strftime('%Y%m%d')
        
        if self._current_date == today and self._file_handler:
            return
        
        if self._file_handler:
            self._logger.removeHandler(self._file_handler)
            self._file_handler.close()
        
        log_path = self._log_dir / self._get_log_filename()
        self._file_handler = logging.FileHandler(log_path, encoding='utf-8')
        self._file_handler.setLevel(logging.INFO)
        
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        self._file_handler.setFormatter(formatter)
        self._logger.addHandler(self._file_handler)
        self._current_date = today
    
    def _rotate_if_needed(self):
        """Ruota il file di log se è cambiato il giorno."""
        today = datetime.now().strftime('%Y%m%d')
        if self._current_date != today:
            self._setup_logger()
    
    def log_event(
        self,
        event_type: SafetyEventType,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ):
        """
        Registra un evento di sicurezza.
        
        Args:
            event_type: Tipo di evento (da SafetyEventType)
            message: Messaggio descrittivo
            details: Dettagli aggiuntivi (dict opzionale)
        """
        with self._log_lock:
            self._rotate_if_needed()
            
            detail_str = ""
            if details:
                detail_str = " | " + " | ".join(
                    f"{k}={v}" for k, v in details.items()
                )
            
            log_line = f"[{event_type.value}] {message}{detail_str}"
            self._logger.info(log_line)
    
    def log_mixed_dutching_error(
        self,
        error_message: str,
        market_id: Optional[str] = None,
        stake: Optional[float] = None,
        selections_count: Optional[int] = None
    ):
        """Logga errore MixedDutchingError."""
        self.log_event(
            SafetyEventType.MIXED_DUTCHING_ERROR,
            error_message,
            {
                "market_id": market_id or "N/A",
                "stake": f"€{stake:.2f}" if stake else "N/A",
                "selections": selections_count or 0
            }
        )
    
    def log_ai_blocked(
        self,
        market_type: str,
        market_id: Optional[str] = None,
        reason: str = "Mercato non compatibile con AI Mixed"
    ):
        """Logga AI bloccata per mercato non compatibile."""
        self.log_event(
            SafetyEventType.AI_BLOCKED_MARKET,
            reason,
            {
                "market_type": market_type,
                "market_id": market_id or "N/A"
            }
        )
    
    def log_auto_green_denied(
        self,
        reason: str,
        order_id: Optional[str] = None,
        market_status: Optional[str] = None,
        elapsed_seconds: Optional[float] = None
    ):
        """Logga auto-green negato con motivo."""
        details = {"order_id": order_id or "N/A"}
        
        if market_status:
            details["market_status"] = market_status
        if elapsed_seconds is not None:
            details["elapsed_sec"] = f"{elapsed_seconds:.2f}"
        
        self.log_event(
            SafetyEventType.AUTO_GREEN_DENIED,
            reason,
            details
        )
    
    def log_safe_mode_triggered(
        self,
        consecutive_errors: int,
        last_error: str
    ):
        """Logga attivazione Safe Mode."""
        self.log_event(
            SafetyEventType.SAFE_MODE_TRIGGERED,
            "Safe Mode attivato - AI disabilitata",
            {
                "consecutive_errors": consecutive_errors,
                "last_error": last_error
            }
        )
    
    def log_profit_validation_failed(
        self,
        variance: float,
        threshold: float,
        market_id: Optional[str] = None
    ):
        """Logga fallimento validazione profitto uniforme."""
        self.log_event(
            SafetyEventType.PROFIT_VALIDATION_FAILED,
            f"Varianza profitto {variance:.2f} supera soglia {threshold:.2f}",
            {"market_id": market_id or "N/A"}
        )
    
    def log_market_validation_failed(
        self,
        market_type: str,
        market_id: Optional[str] = None
    ):
        """Logga fallimento validazione mercato."""
        self.log_event(
            SafetyEventType.MARKET_VALIDATION_FAILED,
            f"Mercato {market_type} non valido per dutching",
            {"market_id": market_id or "N/A"}
        )
    
    def get_log_path(self) -> Path:
        """Restituisce il path del file log corrente."""
        return self._log_dir / self._get_log_filename()


_safety_logger: Optional[SafetyLogger] = None


def get_safety_logger() -> SafetyLogger:
    """Ottiene l'istanza singleton del SafetyLogger."""
    global _safety_logger
    if _safety_logger is None:
        _safety_logger = SafetyLogger()
    return _safety_logger
