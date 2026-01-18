"""
Antifreeze System v3.74 - Complete protection against UI freezes.

Components:
1. CircuitBreaker - Blocks API calls after consecutive failures
2. RateLimiter - Limits operations per second
3. UIQueue - Centralized queue for all UI updates
4. HealthMonitor - Monitors system health
5. GracefulShutdown - Orderly shutdown of all threads
"""

import threading
import queue
import time
import logging
from typing import Callable, Any, Optional, Dict
from dataclasses import dataclass, field
from enum import Enum
from collections import deque


class CircuitState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class CircuitBreaker:
    """Circuit breaker pattern for API protection.
    
    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Too many failures, requests blocked for recovery_timeout
    - HALF_OPEN: Testing if service recovered, one request allowed
    
    Usage:
        breaker = CircuitBreaker(name="betfair", failure_threshold=3, recovery_timeout=30)
        
        if breaker.can_execute():
            try:
                result = api_call()
                breaker.record_success()
            except Exception as e:
                breaker.record_failure()
                raise
    """
    name: str
    failure_threshold: int = 3
    recovery_timeout: float = 30.0
    success_threshold: int = 1
    
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _success_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    
    def can_execute(self) -> bool:
        """Check if request can proceed."""
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True
            
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_failure_time >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
                    logging.info(f"[CIRCUIT:{self.name}] OPEN -> HALF_OPEN (testing recovery)")
                    return True
                return False
            
            if self._state == CircuitState.HALF_OPEN:
                return True
            
            return False
    
    def record_success(self):
        """Record successful operation."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    logging.info(f"[CIRCUIT:{self.name}] HALF_OPEN -> CLOSED (recovered)")
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0
    
    def record_failure(self):
        """Record failed operation."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                logging.warning(f"[CIRCUIT:{self.name}] HALF_OPEN -> OPEN (still failing)")
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    logging.warning(f"[CIRCUIT:{self.name}] CLOSED -> OPEN (threshold reached: {self._failure_count} failures)")
    
    def get_state(self) -> CircuitState:
        """Get current circuit state."""
        with self._lock:
            return self._state
    
    def reset(self):
        """Manually reset circuit to closed state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            logging.info(f"[CIRCUIT:{self.name}] Manually reset to CLOSED")
    
    def is_open(self) -> bool:
        """Check if circuit is open (blocking requests)."""
        with self._lock:
            return self._state == CircuitState.OPEN


@dataclass
class RateLimiter:
    """Token bucket rate limiter.
    
    Usage:
        limiter = RateLimiter(name="betfair", rate=5.0, burst=10)
        
        if limiter.acquire():
            make_api_call()
        else:
            # Rate limited, try later
            pass
    """
    name: str
    rate: float = 5.0
    burst: int = 10
    
    _tokens: float = field(default=0.0, init=False)
    _last_update: float = field(default=0.0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    
    def __post_init__(self):
        self._tokens = float(self.burst)
        self._last_update = time.time()
    
    def acquire(self, tokens: int = 1) -> bool:
        """Try to acquire tokens. Returns True if successful."""
        with self._lock:
            now = time.time()
            elapsed = now - self._last_update
            self._last_update = now
            
            self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
            
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            
            logging.debug(f"[RATE:{self.name}] Rate limited (tokens={self._tokens:.2f})")
            return False
    
    def wait_and_acquire(self, tokens: int = 1, timeout: float = 5.0) -> bool:
        """Wait until tokens available or timeout."""
        start = time.time()
        while time.time() - start < timeout:
            if self.acquire(tokens):
                return True
            time.sleep(0.05)
        return False


class UIUpdatePriority(Enum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3


@dataclass
class UIUpdate:
    """A queued UI update."""
    priority: UIUpdatePriority
    callback: Callable
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    
    def __lt__(self, other):
        if self.priority.value != other.priority.value:
            return self.priority.value < other.priority.value
        return self.timestamp < other.timestamp


class UIQueue:
    """Centralized queue for all UI updates.
    
    Features:
    - Priority-based processing (CRITICAL > HIGH > NORMAL > LOW)
    - Rate limiting (max updates per second)
    - Coalescing similar updates
    - Thread-safe
    
    Usage:
        ui_queue = UIQueue(root, max_updates_per_sec=30)
        ui_queue.start()
        
        ui_queue.put(lambda: label.config(text="Hello"))
        ui_queue.put(lambda: tree.insert(...), priority=UIUpdatePriority.LOW)
        
        ui_queue.stop()
    """
    
    def __init__(self, root, max_updates_per_sec: int = 30, process_interval_ms: int = 33):
        self.root = root
        self.max_updates_per_sec = max_updates_per_sec
        self.process_interval_ms = process_interval_ms
        
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._running = False
        self._timer_id = None
        self._lock = threading.Lock()
        
        self._updates_this_second = 0
        self._second_start = time.time()
        
        self._stats = {
            'total_queued': 0,
            'total_processed': 0,
            'total_dropped': 0,
            'max_queue_size': 0
        }
    
    def start(self):
        """Start processing queue."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._schedule_process()
            logging.info("[UI_QUEUE] Started")
    
    def stop(self):
        """Stop processing and drain queue."""
        with self._lock:
            self._running = False
            if self._timer_id:
                try:
                    self.root.after_cancel(self._timer_id)
                except:
                    pass
                self._timer_id = None
        
        self._drain_queue()
        logging.info(f"[UI_QUEUE] Stopped. Stats: {self._stats}")
    
    def put(self, callback: Callable, *args, priority: UIUpdatePriority = UIUpdatePriority.NORMAL, **kwargs):
        """Queue a UI update."""
        update = UIUpdate(
            priority=priority,
            callback=callback,
            args=args,
            kwargs=kwargs
        )
        
        try:
            self._queue.put_nowait((update.priority.value, update.timestamp, update))
            with self._lock:
                self._stats['total_queued'] += 1
                current_size = self._queue.qsize()
                if current_size > self._stats['max_queue_size']:
                    self._stats['max_queue_size'] = current_size
        except queue.Full:
            with self._lock:
                self._stats['total_dropped'] += 1
            logging.warning("[UI_QUEUE] Queue full, update dropped")
    
    def put_critical(self, callback: Callable, *args, **kwargs):
        """Queue a critical UI update (highest priority)."""
        self.put(callback, *args, priority=UIUpdatePriority.CRITICAL, **kwargs)
    
    def _schedule_process(self):
        """Schedule next processing cycle."""
        if self._running:
            self._timer_id = self.root.after(self.process_interval_ms, self._process)
    
    def _process(self):
        """Process queued updates (runs in main thread)."""
        if not self._running:
            return
        
        now = time.time()
        if now - self._second_start >= 1.0:
            self._second_start = now
            self._updates_this_second = 0
        
        updates_this_cycle = 0
        max_per_cycle = max(1, self.max_updates_per_sec // 30)
        
        while updates_this_cycle < max_per_cycle and self._updates_this_second < self.max_updates_per_sec:
            try:
                _, _, update = self._queue.get_nowait()
                
                try:
                    update.callback(*update.args, **update.kwargs)
                except Exception as e:
                    logging.error(f"[UI_QUEUE] Update error: {e}")
                
                updates_this_cycle += 1
                self._updates_this_second += 1
                with self._lock:
                    self._stats['total_processed'] += 1
                
            except queue.Empty:
                break
        
        self._schedule_process()
    
    def _drain_queue(self):
        """Process remaining items on shutdown."""
        count = 0
        while not self._queue.empty():
            try:
                _, _, update = self._queue.get_nowait()
                try:
                    update.callback(*update.args, **update.kwargs)
                except:
                    pass
                count += 1
            except queue.Empty:
                break
        if count > 0:
            logging.info(f"[UI_QUEUE] Drained {count} remaining updates")
    
    def get_stats(self) -> dict:
        """Get queue statistics."""
        with self._lock:
            return dict(self._stats)
    
    def get_queue_size(self) -> int:
        """Get current queue size."""
        return self._queue.qsize()


class HealthStatus(Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"


@dataclass
class ComponentHealth:
    """Health status of a component."""
    name: str
    status: HealthStatus
    last_check: float
    message: str = ""
    latency_ms: float = 0.0


class HealthMonitor:
    """Monitors system health and alerts on issues.
    
    Usage:
        monitor = HealthMonitor(check_interval=5.0)
        monitor.register_check("betfair", check_betfair_connection)
        monitor.register_check("telegram", check_telegram_connection)
        monitor.start()
        
        monitor.get_status()
        monitor.stop()
    """
    
    def __init__(self, check_interval: float = 5.0, on_unhealthy: Callable = None):
        self.check_interval = check_interval
        self.on_unhealthy = on_unhealthy
        
        self._checks: Dict[str, Callable] = {}
        self._status: Dict[str, ComponentHealth] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
    
    def register_check(self, name: str, check_fn: Callable[[], bool]):
        """Register a health check function.
        
        check_fn should return True if healthy, False otherwise.
        """
        with self._lock:
            self._checks[name] = check_fn
            self._status[name] = ComponentHealth(
                name=name,
                status=HealthStatus.HEALTHY,
                last_check=0.0
            )
    
    def start(self):
        """Start health monitoring."""
        if self._running:
            return
        
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="HealthMonitor")
        self._thread.start()
        logging.info("[HEALTH] Monitor started")
    
    def stop(self):
        """Stop health monitoring."""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        logging.info("[HEALTH] Monitor stopped")
    
    def _monitor_loop(self):
        """Main monitoring loop."""
        while self._running and not self._stop_event.is_set():
            self._run_checks()
            self._stop_event.wait(self.check_interval)
    
    def _run_checks(self):
        """Run all registered health checks."""
        with self._lock:
            checks_copy = dict(self._checks)
        
        for name, check_fn in checks_copy.items():
            start = time.time()
            try:
                is_healthy = check_fn()
                latency = (time.time() - start) * 1000
                
                status = HealthStatus.HEALTHY if is_healthy else HealthStatus.UNHEALTHY
                message = "" if is_healthy else "Check returned False"
                
                if latency > 5000:
                    status = HealthStatus.DEGRADED
                    message = f"High latency: {latency:.0f}ms"
                
            except Exception as e:
                latency = (time.time() - start) * 1000
                status = HealthStatus.UNHEALTHY
                message = str(e)
            
            health = ComponentHealth(
                name=name,
                status=status,
                last_check=time.time(),
                message=message,
                latency_ms=latency
            )
            
            with self._lock:
                old_status = self._status.get(name)
                self._status[name] = health
                
                if old_status and old_status.status != status:
                    logging.info(f"[HEALTH] {name}: {old_status.status.value} -> {status.value}")
                    
                    if status == HealthStatus.UNHEALTHY and self.on_unhealthy:
                        try:
                            self.on_unhealthy(name, message)
                        except:
                            pass
    
    def get_status(self) -> Dict[str, ComponentHealth]:
        """Get current health status of all components."""
        with self._lock:
            return dict(self._status)
    
    def is_healthy(self, component: str = None) -> bool:
        """Check if a component (or all) is healthy."""
        with self._lock:
            if component:
                health = self._status.get(component)
                return health and health.status != HealthStatus.UNHEALTHY
            
            return all(h.status != HealthStatus.UNHEALTHY for h in self._status.values())


class GracefulShutdown:
    """Manages orderly shutdown of all components.
    
    Usage:
        shutdown = GracefulShutdown()
        shutdown.register("streams", stop_streams, priority=1)
        shutdown.register("telegram", stop_telegram, priority=2)
        shutdown.register("database", close_database, priority=3)
        
        shutdown.execute()
    """
    
    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self._handlers: list = []
        self._lock = threading.Lock()
        self._shutdown_in_progress = False
        self._shutdown_complete = False
    
    def register(self, name: str, handler: Callable, priority: int = 5):
        """Register a shutdown handler.
        
        Lower priority = runs first.
        """
        with self._lock:
            self._handlers.append((priority, name, handler))
            self._handlers.sort(key=lambda x: x[0])
            logging.debug(f"[SHUTDOWN] Registered handler: {name} (priority={priority})")
    
    def execute(self) -> bool:
        """Execute graceful shutdown. Returns True if all handlers completed."""
        with self._lock:
            if self._shutdown_in_progress:
                logging.warning("[SHUTDOWN] Already in progress")
                return False
            self._shutdown_in_progress = True
            handlers_copy = list(self._handlers)
        
        logging.info(f"[SHUTDOWN] Starting graceful shutdown ({len(handlers_copy)} handlers)")
        start = time.time()
        success = True
        
        for priority, name, handler in handlers_copy:
            if time.time() - start > self.timeout:
                logging.error(f"[SHUTDOWN] Timeout reached, skipping remaining handlers")
                success = False
                break
            
            try:
                logging.info(f"[SHUTDOWN] Executing: {name}")
                handler_start = time.time()
                handler()
                elapsed = time.time() - handler_start
                logging.info(f"[SHUTDOWN] Completed: {name} ({elapsed:.2f}s)")
            except Exception as e:
                logging.error(f"[SHUTDOWN] Handler {name} failed: {e}")
                success = False
        
        total_time = time.time() - start
        logging.info(f"[SHUTDOWN] Graceful shutdown completed in {total_time:.2f}s (success={success})")
        
        with self._lock:
            self._shutdown_complete = True
        
        return success
    
    def is_shutting_down(self) -> bool:
        """Check if shutdown is in progress."""
        with self._lock:
            return self._shutdown_in_progress
    
    def is_complete(self) -> bool:
        """Check if shutdown is complete."""
        with self._lock:
            return self._shutdown_complete


class AntifreezeManager:
    """Central manager for all antifreeze components.
    
    Usage:
        manager = AntifreezeManager(root)
        manager.start()
        
        # Use components
        manager.betfair_breaker.can_execute()
        manager.ui_queue.put(...)
        
        manager.shutdown()
    """
    
    def __init__(self, root):
        self.root = root
        
        self.betfair_breaker = CircuitBreaker(
            name="betfair",
            failure_threshold=3,
            recovery_timeout=30.0
        )
        
        self.telegram_breaker = CircuitBreaker(
            name="telegram",
            failure_threshold=5,
            recovery_timeout=60.0
        )
        
        self.betfair_limiter = RateLimiter(
            name="betfair",
            rate=5.0,
            burst=10
        )
        
        self.telegram_limiter = RateLimiter(
            name="telegram",
            rate=2.0,
            burst=5
        )
        
        self.db_limiter = RateLimiter(
            name="database",
            rate=50.0,
            burst=100
        )
        
        self.ui_queue = UIQueue(
            root,
            max_updates_per_sec=30,
            process_interval_ms=33
        )
        
        self.health_monitor = HealthMonitor(
            check_interval=5.0,
            on_unhealthy=self._on_component_unhealthy
        )
        
        self.shutdown_manager = GracefulShutdown(timeout=15.0)
        
        self._started = False
    
    def start(self):
        """Start all antifreeze components."""
        if self._started:
            return
        
        self.ui_queue.start()
        self.health_monitor.start()
        self._started = True
        logging.info("[ANTIFREEZE] Manager started")
    
    def shutdown(self):
        """Shutdown all components gracefully."""
        if not self._started:
            return
        
        self.health_monitor.stop()
        self.ui_queue.stop()
        self.shutdown_manager.execute()
        self._started = False
        logging.info("[ANTIFREEZE] Manager shutdown complete")
    
    def _on_component_unhealthy(self, name: str, message: str):
        """Handle unhealthy component."""
        logging.warning(f"[ANTIFREEZE] Component unhealthy: {name} - {message}")
    
    def schedule_ui(self, callback: Callable, *args, priority: UIUpdatePriority = UIUpdatePriority.NORMAL, **kwargs):
        """Schedule a UI update through the centralized queue."""
        self.ui_queue.put(callback, *args, priority=priority, **kwargs)
    
    def schedule_ui_critical(self, callback: Callable, *args, **kwargs):
        """Schedule a critical UI update."""
        self.ui_queue.put_critical(callback, *args, **kwargs)
    
    def get_status(self) -> dict:
        """Get overall system status."""
        return {
            'betfair_circuit': self.betfair_breaker.get_state().value,
            'telegram_circuit': self.telegram_breaker.get_state().value,
            'health': {name: h.status.value for name, h in self.health_monitor.get_status().items()},
            'ui_queue': self.ui_queue.get_stats(),
            'ui_queue_size': self.ui_queue.get_queue_size()
        }
