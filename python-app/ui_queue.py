"""
UIUpdateQueue - Queue UI centralizzata per Pickfair v3.74+

Elimina il 90% dei freeze UI in Tkinter/ttkbootstrap:
- Queue centralizzata (tutti gli update UI passano da qui)
- Rate limit max 30 update/sec (configurabile)
- Dedup (evita spam di .after() uguali)
- Priorità (CRITICAL > HIGH > NORMAL > LOW)
- Log automatico (backlog, UI call droppate)
- Helper: ui_call(), ui_set_text(), run_bg()
"""

import time
import threading
import queue
import traceback
import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Optional, Dict


class UIPriority(IntEnum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3


@dataclass(order=True)
class UIJob:
    priority: int
    created_ts: float = field(compare=False, default_factory=time.time)
    key: Optional[str] = field(compare=False, default=None)
    fn: Optional[Callable] = field(compare=False, default=None)
    args: tuple = field(compare=False, default_factory=tuple)
    kwargs: dict = field(compare=False, default_factory=dict)
    debug_name: str = field(compare=False, default="ui_job")


class UIUpdateQueue:
    """
    Queue centralizzata per TUTTI gli update UI.

    - Tutti i thread chiamano uiq.post(...)
    - Solo il main thread esegue widget update
    - Rate limit max_updates_per_sec (default 30/s)
    - Dedup: se posti lo stesso key, tiene solo l'ultimo
    - Log stats automatico
    """

    def __init__(
        self,
        root,
        logger=None,
        max_updates_per_sec: int = 30,
        tick_ms: int = 15,
        max_jobs_per_tick: int = 10,
        max_queue_size: int = 5000,
    ):
        self.root = root
        self.logger = logger or logging.getLogger(__name__)

        self.max_updates_per_sec = max_updates_per_sec
        self.tick_ms = tick_ms
        self.max_jobs_per_tick = max_jobs_per_tick

        self._pq = queue.PriorityQueue(maxsize=max_queue_size)
        self._dedup_latest: Dict[str, UIJob] = {}
        self._lock = threading.RLock()

        self._running = False
        self._ui_thread_id = None

        # rate limit
        self._window_start = time.time()
        self._sent_in_window = 0

        # stats
        self._dropped = 0
        self._executed = 0
        self._errors = 0
        self._last_stats_log = 0.0

    def start(self):
        if self._running:
            return
        self._running = True
        self._ui_thread_id = threading.get_ident()
        self._schedule_tick()
        self._log("[UIQ] Started")

    def stop(self):
        self._running = False
        self._log("[UIQ] Stopped")

    def is_ui_thread(self) -> bool:
        return threading.get_ident() == self._ui_thread_id

    def post(
        self,
        fn: Callable,
        *args,
        priority: UIPriority = UIPriority.NORMAL,
        key: Optional[str] = None,
        debug_name: str = "ui_job",
        **kwargs,
    ):
        """
        Posta un update UI in coda.
        key: se presente, deduplica (tiene solo l'ultimo)
        """
        if not self._running:
            # fallback: se queue non avviata, prova solo se siamo nel UI thread
            try:
                if self.is_ui_thread():
                    fn(*args, **kwargs)
                else:
                    self._dropped += 1
            except Exception:
                self._errors += 1
            return

        job = UIJob(
            priority=int(priority),
            key=key,
            fn=fn,
            args=args,
            kwargs=kwargs,
            debug_name=debug_name,
        )

        with self._lock:
            if key:
                self._dedup_latest[key] = job
                try:
                    self._pq.put_nowait(job)
                except queue.Full:
                    self._dropped += 1
                    self._dedup_latest.pop(key, None)
            else:
                try:
                    self._pq.put_nowait(job)
                except queue.Full:
                    self._dropped += 1

    def _schedule_tick(self):
        if not self._running:
            return
        self.root.after(self.tick_ms, self._tick)

    def _tick(self):
        if not self._running:
            return

        now = time.time()

        # reset finestra rate-limit
        if now - self._window_start >= 1.0:
            self._window_start = now
            self._sent_in_window = 0

        jobs_done = 0

        while jobs_done < self.max_jobs_per_tick:
            # rate limit globale
            if self._sent_in_window >= self.max_updates_per_sec:
                break

            try:
                job: UIJob = self._pq.get_nowait()
            except queue.Empty:
                break

            # dedup: esegui solo se è l'ultimo per quella key
            if job.key:
                with self._lock:
                    latest = self._dedup_latest.get(job.key)
                    if latest is None:
                        continue
                    if latest is not job:
                        continue
                    self._dedup_latest.pop(job.key, None)

            try:
                job.fn(*job.args, **job.kwargs)
                self._executed += 1
            except Exception as e:
                self._errors += 1
                self._log(
                    f"[UIQ] ERROR in {job.debug_name}: {e}\n{traceback.format_exc()}"
                )

            self._sent_in_window += 1
            jobs_done += 1

        # stats ogni 5s
        if now - self._last_stats_log > 5:
            self._last_stats_log = now
            self._log_stats()

        self._schedule_tick()

    def flush(self, timeout_sec: float = 2.0):
        """
        Prova a svuotare la queue prima di chiudere.
        """
        end = time.time() + timeout_sec
        while time.time() < end:
            try:
                if self._pq.empty():
                    break
            except Exception:
                break
            time.sleep(0.05)

    def _log(self, msg: str):
        if self.logger:
            try:
                self.logger.info(msg)
                return
            except Exception:
                pass

    def _log_stats(self):
        try:
            qsize = self._pq.qsize()
        except Exception:
            qsize = -1

        self._log(
            f"[UIQ] stats executed={self._executed} dropped={self._dropped} errors={self._errors} qsize={qsize}"
        )

    def get_stats(self) -> dict:
        """Return current stats for monitoring."""
        try:
            qsize = self._pq.qsize()
        except Exception:
            qsize = -1
        return {
            "executed": self._executed,
            "dropped": self._dropped,
            "errors": self._errors,
            "qsize": qsize,
            "running": self._running,
        }


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def ui_call(app, fn: Callable, *args, key=None, priority=UIPriority.NORMAL, debug_name="ui_call", **kwargs):
    """Post any callable to the UI queue."""
    app.uiq.post(fn, *args, key=key, priority=priority, debug_name=debug_name, **kwargs)


def ui_set_text(app, widget, text: str, key: str, priority=UIPriority.NORMAL):
    """Set widget text via UI queue (deduped)."""
    app.uiq.post(widget.configure, text=text, key=key, priority=priority, debug_name="ui_set_text")


def ui_set_state(app, widget, state: str, key: str, priority=UIPriority.LOW):
    """Set widget state via UI queue (deduped)."""
    app.uiq.post(widget.configure, state=state, key=key, priority=priority, debug_name="ui_set_state")


def ui_set_progress(app, progressbar, value: float, key: str, priority=UIPriority.LOW):
    """Set progressbar value 0..1 via UI queue (deduped)."""
    app.uiq.post(progressbar.set, value, key=key, priority=priority, debug_name="ui_set_progress")


def run_bg(app, name: str = None, fn: Callable = None, *args, **kwargs):
    """
    Run a function in background thread. Anti-freeze for button clicks.
    
    Usage:
        command=lambda: run_bg(self, "LoadEvents", self._load_events)
        # or with auto-name:
        command=lambda: run_bg(self, fn=self._load_events)
    """
    # Validate fn is provided
    if fn is None:
        raise TypeError("run_bg() requires 'fn' argument - the function to run")
    
    # Auto-generate name if not provided
    if name is None:
        name = getattr(fn, "__qualname__", getattr(fn, "__name__", "bg_task"))
    
    def _job():
        try:
            logging.info(f"[BG] START {name}")
            t0 = time.time()
            fn(*args, **kwargs)
            dt = (time.time() - t0) * 1000
            logging.info(f"[BG] DONE {name} in {dt:.1f}ms")
        except Exception as e:
            logging.exception(f"[BG] CRASH {name}: {e}")
            # Update status label if available (non-blocking)
            if hasattr(app, "uiq") and hasattr(app, "status_label"):
                app.uiq.post(
                    app.status_label.configure,
                    text=f"Errore: {e}",
                    key="status_label",
                    priority=UIPriority.CRITICAL
                )

    threading.Thread(target=_job, daemon=True, name=name).start()


def wrap_command(app, name: str, fn: Callable):
    """
    Wrap a button command with logging. Use with run_bg for full anti-freeze.
    
    Usage:
        ttk.Button(..., command=wrap_command(self, "Dashboard", 
            lambda: run_bg(self, "DashboardOpen", self._open_dashboard)))
    """
    def _wrapped():
        logging.info(f"[UI] CLICK {name}")
        start = time.time()
        try:
            return fn()
        finally:
            dt = (time.time() - start) * 1000
            logging.info(f"[UI] CLICK {name} done in {dt:.1f}ms")
    return _wrapped


def treeview_insert_chunked(app, tree, rows, chunk: int = 50, clear_first: bool = False):
    """
    Insert many rows into Treeview without freezing.
    Chunks insertions across multiple UI ticks.
    
    Args:
        app: Application with uiq attribute
        tree: Treeview widget
        rows: Iterable of row tuples/lists
        chunk: Rows per tick (default 50)
        clear_first: Clear tree before inserting
    """
    rows = list(rows)
    total = len(rows)

    def step(i=0):
        end = min(i + chunk, total)
        for r in rows[i:end]:
            tree.insert("", "end", values=r)

        if end < total:
            # Schedule next chunk
            app.uiq.post(
                lambda idx=end: app.root.after(10, lambda: step(idx)),
                key="tree_chunk_step",
                priority=UIPriority.LOW,
                debug_name="tree_chunk_step"
            )

    def start_insert():
        if clear_first:
            for item in tree.get_children():
                tree.delete(item)
        step(0)

    app.uiq.post(
        start_insert,
        key="tree_chunk_start",
        priority=UIPriority.NORMAL,
        debug_name="tree_chunk_start"
    )
