"""
Thread safety guard for Pickfair.

RULE:
Main thread MUST NEVER call:
- Betfair API
- API-Football
- HTTP
- socket
- blocking I/O

UI thread = render only
"""

import threading
import functools
import traceback
import logging
import time
import os


def is_ui_thread():
    """Check if current thread is the main UI thread."""
    return threading.current_thread() is threading.main_thread()


def ui_guard(name: str, warn_ms: int = 200):
    """
    Decoratore per callback UI.
    Logga durata e avvisa se supera warn_ms.
    
    Uso:
        btn.config(command=ui_guard("dashboard")(self._open_dashboard))
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            logging.info(f"[UI-CLICK] {name}")
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                dt = (time.perf_counter() - t0) * 1000
                if dt > warn_ms:
                    logging.warning(f"[UI-GUARD] SLOW callback {name}: {dt:.0f}ms (>{warn_ms}ms)")
                else:
                    logging.info(f"[UI-CLICK] {name} done in {dt:.0f}ms")
        return wrapper
    return deco


def _is_testing():
    """Check if running in test mode (evaluated at call time, not import time)."""
    return (
        os.environ.get("PYTEST_CURRENT_TEST") is not None or 
        os.environ.get("THREAD_GUARD_DISABLED") == "1"
    )


def assert_not_ui_thread(fn):
    """
    Decorator that prevents a function from being called on the UI (main) thread.
    
    If called from main thread, raises RuntimeError with full stacktrace.
    This transforms silent UI freezes into immediate, debuggable crashes.
    
    Automatically disabled during pytest execution.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        # Skip check during testing (check at call time)
        if _is_testing():
            return fn(*args, **kwargs)
        
        if threading.current_thread() is threading.main_thread():
            stack = "".join(traceback.format_stack(limit=10))
            error_msg = (
                f"[THREAD VIOLATION] {fn.__qualname__} called from UI thread!\n"
                f"This would freeze the UI. Stack trace:\n{stack}"
            )
            logging.error(error_msg)
            raise RuntimeError(error_msg)
        return fn(*args, **kwargs)
    return wrapper


class GuardedAPIMeta(type):
    """
    Metaclass that automatically applies @assert_not_ui_thread
    to all public methods of a class.
    
    Usage:
        class BetfairClient(metaclass=GuardedAPIMeta):
            def get_current_orders(self):
                ...  # Automatically protected
    
    This ensures that ALL API methods are protected from being called
    on the main thread, preventing UI freezes.
    """
    
    EXCLUDED_METHODS = {
        '__init__', '__del__', '__repr__', '__str__',
        '__enter__', '__exit__', '__hash__', '__eq__',
        '__ne__', '__lt__', '__le__', '__gt__', '__ge__',
    }
    
    def __new__(cls, name, bases, namespace):
        for attr, value in namespace.items():
            if (
                callable(value)
                and not attr.startswith("_")
                and attr not in cls.EXCLUDED_METHODS
            ):
                namespace[attr] = assert_not_ui_thread(value)
        return super().__new__(cls, name, bases, namespace)


def warn_if_ui_thread(fn):
    """
    Soft version: logs warning but doesn't crash.
    Use this for methods that SHOULD be called from background but aren't critical.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if threading.current_thread() is threading.main_thread():
            logging.warning(
                f"[THREAD WARNING] {fn.__qualname__} called from UI thread. "
                f"Consider moving to background thread."
            )
        return fn(*args, **kwargs)
    return wrapper
