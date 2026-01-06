# ui_helpers.py
# Helper per UI Tkinter - polling Future + defer standard
import logging

logger = logging.getLogger(__name__)

def defer(root, fn, delay=100):
    """
    Esegue fn dopo delay ms.
    MAI I/O diretto nel callback!
    
    Args:
        root: Tk root window
        fn: Function to call (no arguments)
        delay: Milliseconds to wait
    """
    root.after(delay, fn)


def poll_future(root, future, on_ok, on_err, interval=50):
    """
    Poll non bloccante di Future.
    Chiama on_ok(result) o on_err(exception) quando il Future è completo.
    
    REGOLA: MAI usare future.result() direttamente nel main thread!
    
    Args:
        root: Tk root window
        future: concurrent.futures.Future
        on_ok: Callback(result) on success
        on_err: Callback(exception) on error
        interval: Poll interval in ms (default 50ms)
    """
    if future.done():
        try:
            result = future.result()
            logger.debug("[POLL] Future completed successfully")
            on_ok(result)
        except Exception as e:
            logger.error("[POLL] Future failed: %s", e)
            on_err(e)
    else:
        # Schedule next poll
        root.after(interval, lambda: poll_future(root, future, on_ok, on_err, interval))


def poll_future_with_timeout(root, future, on_ok, on_err, on_timeout, 
                             interval=50, timeout_ms=30000):
    """
    Poll con timeout.
    
    Args:
        root: Tk root window
        future: concurrent.futures.Future
        on_ok: Callback(result) on success
        on_err: Callback(exception) on error
        on_timeout: Callback() on timeout
        interval: Poll interval in ms
        timeout_ms: Max wait time in ms
    """
    import time
    start_time_ms = time.time() * 1000
    
    def do_poll():
        elapsed = (time.time() * 1000) - start_time_ms
        
        if future.done():
            try:
                result = future.result()
                on_ok(result)
            except Exception as e:
                on_err(e)
        elif elapsed > timeout_ms:
            logger.warning("[POLL] Future timeout after %dms", timeout_ms)
            on_timeout()
        else:
            root.after(interval, do_poll)
    
    do_poll()
