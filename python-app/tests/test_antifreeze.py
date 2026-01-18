"""Tests for the Antifreeze System v3.74"""

import pytest
import time
import threading
from unittest.mock import MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from antifreeze import (
    CircuitBreaker, CircuitState,
    RateLimiter,
    UIQueue, UIUpdatePriority,
    HealthMonitor, HealthStatus,
    GracefulShutdown
)


class TestCircuitBreaker:
    """Test CircuitBreaker pattern."""
    
    def test_initial_state_closed(self):
        breaker = CircuitBreaker(name="test", failure_threshold=3)
        assert breaker.get_state() == CircuitState.CLOSED
        assert breaker.can_execute() == True
    
    def test_opens_after_threshold_failures(self):
        breaker = CircuitBreaker(name="test", failure_threshold=3)
        
        breaker.record_failure()
        assert breaker.get_state() == CircuitState.CLOSED
        
        breaker.record_failure()
        assert breaker.get_state() == CircuitState.CLOSED
        
        breaker.record_failure()
        assert breaker.get_state() == CircuitState.OPEN
        assert breaker.can_execute() == False
    
    def test_success_resets_failure_count(self):
        breaker = CircuitBreaker(name="test", failure_threshold=3)
        
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()
        breaker.record_failure()
        
        assert breaker.get_state() == CircuitState.CLOSED
    
    def test_half_open_after_recovery_timeout(self):
        breaker = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.1)
        
        breaker.record_failure()
        assert breaker.get_state() == CircuitState.OPEN
        assert breaker.can_execute() == False
        
        time.sleep(0.15)
        
        assert breaker.can_execute() == True
        assert breaker.get_state() == CircuitState.HALF_OPEN
    
    def test_half_open_to_closed_on_success(self):
        breaker = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.05)
        
        breaker.record_failure()
        time.sleep(0.1)
        breaker.can_execute()
        breaker.record_success()
        
        assert breaker.get_state() == CircuitState.CLOSED
    
    def test_half_open_to_open_on_failure(self):
        breaker = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.05)
        
        breaker.record_failure()
        time.sleep(0.1)
        breaker.can_execute()
        breaker.record_failure()
        
        assert breaker.get_state() == CircuitState.OPEN
    
    def test_manual_reset(self):
        breaker = CircuitBreaker(name="test", failure_threshold=1)
        
        breaker.record_failure()
        assert breaker.get_state() == CircuitState.OPEN
        
        breaker.reset()
        assert breaker.get_state() == CircuitState.CLOSED
        assert breaker.can_execute() == True


class TestRateLimiter:
    """Test RateLimiter (token bucket)."""
    
    def test_allows_burst(self):
        limiter = RateLimiter(name="test", rate=10.0, burst=5)
        
        for _ in range(5):
            assert limiter.acquire() == True
    
    def test_blocks_after_burst(self):
        limiter = RateLimiter(name="test", rate=10.0, burst=3)
        
        for _ in range(3):
            limiter.acquire()
        
        assert limiter.acquire() == False
    
    def test_refills_over_time(self):
        limiter = RateLimiter(name="test", rate=100.0, burst=1)
        
        assert limiter.acquire() == True
        assert limiter.acquire() == False
        
        time.sleep(0.015)
        
        assert limiter.acquire() == True
    
    def test_wait_and_acquire(self):
        limiter = RateLimiter(name="test", rate=50.0, burst=1)
        
        limiter.acquire()
        
        assert limiter.wait_and_acquire(timeout=0.1) == True


class TestHealthMonitor:
    """Test HealthMonitor."""
    
    def test_initial_state(self):
        monitor = HealthMonitor(check_interval=1.0)
        assert monitor.is_healthy() == True
    
    def test_register_check(self):
        monitor = HealthMonitor(check_interval=1.0)
        monitor.register_check("test", lambda: True)
        
        status = monitor.get_status()
        assert "test" in status
    
    def test_unhealthy_callback(self):
        callback_called = []
        
        def on_unhealthy(name, msg):
            callback_called.append(name)
        
        monitor = HealthMonitor(check_interval=0.05, on_unhealthy=on_unhealthy)
        monitor.register_check("failing", lambda: False)
        monitor.start()
        
        time.sleep(0.15)
        monitor.stop()
        
        assert "failing" in callback_called
    
    def test_start_stop(self):
        monitor = HealthMonitor(check_interval=1.0)
        monitor.start()
        assert monitor._running == True
        
        monitor.stop()
        assert monitor._running == False


class TestGracefulShutdown:
    """Test GracefulShutdown manager."""
    
    def test_execute_in_order(self):
        shutdown = GracefulShutdown(timeout=5.0)
        order = []
        
        shutdown.register("first", lambda: order.append(1), priority=1)
        shutdown.register("second", lambda: order.append(2), priority=2)
        shutdown.register("third", lambda: order.append(3), priority=3)
        
        result = shutdown.execute()
        
        assert result == True
        assert order == [1, 2, 3]
    
    def test_handles_handler_errors(self):
        shutdown = GracefulShutdown(timeout=5.0)
        
        def failing_handler():
            raise Exception("Test error")
        
        shutdown.register("failing", failing_handler, priority=1)
        shutdown.register("success", lambda: None, priority=2)
        
        result = shutdown.execute()
        
        assert result == False
        assert shutdown.is_complete() == True
    
    def test_timeout(self):
        shutdown = GracefulShutdown(timeout=0.1)
        
        def slow_handler():
            time.sleep(0.5)
        
        shutdown.register("slow", slow_handler, priority=1)
        shutdown.register("never_runs", lambda: None, priority=2)
        
        result = shutdown.execute()
        
        assert result == False
    
    def test_is_shutting_down(self):
        shutdown = GracefulShutdown(timeout=5.0)
        
        assert shutdown.is_shutting_down() == False
        
        shutdown.register("test", lambda: None, priority=1)
        shutdown.execute()
        
        assert shutdown.is_complete() == True


class TestUIQueue:
    """Test UIQueue (basic tests without Tk root)."""
    
    def test_priority_ordering(self):
        from antifreeze import UIUpdate
        
        low = UIUpdate(priority=UIUpdatePriority.LOW, callback=lambda: None)
        normal = UIUpdate(priority=UIUpdatePriority.NORMAL, callback=lambda: None)
        high = UIUpdate(priority=UIUpdatePriority.HIGH, callback=lambda: None)
        critical = UIUpdate(priority=UIUpdatePriority.CRITICAL, callback=lambda: None)
        
        assert critical < high < normal < low
    
    def test_same_priority_uses_timestamp(self):
        from antifreeze import UIUpdate
        
        first = UIUpdate(priority=UIUpdatePriority.NORMAL, callback=lambda: None)
        time.sleep(0.001)
        second = UIUpdate(priority=UIUpdatePriority.NORMAL, callback=lambda: None)
        
        assert first < second
