"""
Safe Mode Controller for Pickfair
Automatically disables auto-betting when too many errors occur.
Protects account and money from cascading failures.
"""

import time
import logging
import threading
from collections import deque
from typing import Optional, Callable

class SafeModeController:
    """
    Monitors error rate and activates safe mode when threshold exceeded.
    
    Safe mode:
    - Blocks auto-bet placement
    - Continues logging and parsing
    - UI remains responsive
    - Auto-recovers when errors clear
    """
    
    def __init__(self, max_errors: int = 5, window_sec: int = 60, 
                 on_safe_mode_change: Optional[Callable[[bool], None]] = None):
        """
        Initialize safe mode controller.
        
        Args:
            max_errors: Number of errors to trigger safe mode (default 5)
            window_sec: Time window in seconds (default 60)
            on_safe_mode_change: Callback when safe mode changes (receives bool)
        """
        self.max_errors = max_errors
        self.window_sec = window_sec
        self.on_safe_mode_change = on_safe_mode_change
        
        self._errors: deque = deque()
        self._lock = threading.RLock()
        self._safe_mode = False
        self._safe_mode_logged = False
        self._last_error_time = 0
        self._total_errors = 0
        self._total_successes = 0
        
        # Manual override
        self._manual_override = False  # True = forced OFF (user disabled)
        
    @property
    def safe_mode(self) -> bool:
        """Current safe mode state."""
        with self._lock:
            return self._safe_mode or self._manual_override
    
    @property
    def is_auto_recovery(self) -> bool:
        """True if safe mode was triggered automatically (not manual)."""
        with self._lock:
            return self._safe_mode and not self._manual_override
    
    @property
    def error_count(self) -> int:
        """Current error count in window."""
        with self._lock:
            self._cleanup(time.time())
            return len(self._errors)
    
    @property
    def stats(self) -> dict:
        """Get statistics for UI display."""
        with self._lock:
            return {
                'safe_mode': self._safe_mode,
                'manual_override': self._manual_override,
                'errors_in_window': len(self._errors),
                'max_errors': self.max_errors,
                'window_sec': self.window_sec,
                'total_errors': self._total_errors,
                'total_successes': self._total_successes,
                'last_error_time': self._last_error_time
            }
    
    def record_error(self, source: str = "unknown", error_msg: str = "") -> None:
        """
        Record an error occurrence.
        
        Args:
            source: Error source (e.g., "telegram", "betfair")
            error_msg: Error message for logging
        """
        now = time.time()
        
        with self._lock:
            self._errors.append(now)
            self._last_error_time = now
            self._total_errors += 1
            self._cleanup(now)
            self._evaluate()
        
        logging.warning(f"[SAFE-MODE] Error recorded from {source}: {error_msg[:100]}")
    
    def record_success(self) -> None:
        """Record a successful operation (helps with recovery)."""
        now = time.time()
        
        with self._lock:
            self._total_successes += 1
            self._cleanup(now)
            self._evaluate()
    
    def _cleanup(self, now: float) -> None:
        """Remove expired errors from window (must hold lock)."""
        while self._errors and now - self._errors[0] > self.window_sec:
            self._errors.popleft()
    
    def _evaluate(self) -> None:
        """Evaluate if safe mode should change (must hold lock)."""
        was_safe = self._safe_mode
        
        if len(self._errors) >= self.max_errors:
            self._safe_mode = True
            if not self._safe_mode_logged:
                logging.critical(
                    f"[SAFE-MODE] ACTIVATED - {len(self._errors)} errors in {self.window_sec}s window. "
                    f"Auto-bet BLOCKED until errors clear."
                )
                self._safe_mode_logged = True
        elif self._safe_mode and len(self._errors) == 0:
            self._safe_mode = False
            self._safe_mode_logged = False
            logging.info("[SAFE-MODE] DEACTIVATED - No errors in window. Auto-bet RESUMED.")
        
        # Notify callback if state changed
        if was_safe != self._safe_mode and self.on_safe_mode_change:
            try:
                self.on_safe_mode_change(self._safe_mode)
            except Exception as e:
                logging.error(f"[SAFE-MODE] Callback error: {e}")
    
    def set_manual_override(self, enabled: bool) -> None:
        """
        Manually enable/disable safe mode.
        
        Args:
            enabled: True to force safe mode ON (block auto-bet)
        """
        with self._lock:
            self._manual_override = enabled
        
        if enabled:
            logging.info("[SAFE-MODE] Manual override ENABLED - Auto-bet blocked by user")
        else:
            logging.info("[SAFE-MODE] Manual override DISABLED - Auto mode resumed")
    
    def reset(self) -> None:
        """Reset all counters and exit safe mode."""
        with self._lock:
            self._errors.clear()
            self._safe_mode = False
            self._safe_mode_logged = False
            self._manual_override = False
        
        logging.info("[SAFE-MODE] Reset - All counters cleared")
    
    def can_auto_bet(self) -> bool:
        """
        Check if auto-betting is allowed.
        
        Returns:
            True if auto-bet is allowed, False if blocked by safe mode
        """
        return not self.safe_mode


# Global instance for easy access
_safe_mode_controller: Optional[SafeModeController] = None

def get_safe_mode_controller() -> SafeModeController:
    """Get or create the global safe mode controller."""
    global _safe_mode_controller
    if _safe_mode_controller is None:
        _safe_mode_controller = SafeModeController()
    return _safe_mode_controller

def set_safe_mode_controller(controller: SafeModeController) -> None:
    """Set the global safe mode controller (for dependency injection)."""
    global _safe_mode_controller
    _safe_mode_controller = controller
