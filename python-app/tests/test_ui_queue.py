"""Tests for UIUpdateQueue module."""
import time
import threading
import pytest
from unittest.mock import MagicMock, patch
from ui_queue import UIUpdateQueue, UIPriority, UIJob, run_bg


class MockRoot:
    """Mock Tkinter root for testing."""
    def __init__(self):
        self._after_calls = []
        self._after_id = 0
        
    def after(self, ms, callback):
        self._after_id += 1
        self._after_calls.append((ms, callback))
        return self._after_id


class TestUIPriority:
    def test_priority_ordering(self):
        assert UIPriority.CRITICAL < UIPriority.HIGH
        assert UIPriority.HIGH < UIPriority.NORMAL
        assert UIPriority.NORMAL < UIPriority.LOW
    
    def test_priority_values(self):
        assert int(UIPriority.CRITICAL) == 0
        assert int(UIPriority.HIGH) == 1
        assert int(UIPriority.NORMAL) == 2
        assert int(UIPriority.LOW) == 3


class TestUIJob:
    def test_job_ordering(self):
        job_critical = UIJob(priority=0)
        job_low = UIJob(priority=3)
        assert job_critical < job_low
    
    def test_job_with_key(self):
        job = UIJob(priority=2, key="test_key", fn=lambda: None)
        assert job.key == "test_key"


class TestUIUpdateQueue:
    def test_init(self):
        root = MockRoot()
        uiq = UIUpdateQueue(root, max_updates_per_sec=30)
        assert uiq.max_updates_per_sec == 30
        assert not uiq._running
    
    def test_start_stop(self):
        root = MockRoot()
        uiq = UIUpdateQueue(root)
        uiq.start()
        assert uiq._running
        assert uiq._ui_thread_id == threading.get_ident()
        uiq.stop()
        assert not uiq._running
    
    def test_is_ui_thread(self):
        root = MockRoot()
        uiq = UIUpdateQueue(root)
        uiq.start()
        assert uiq.is_ui_thread()
        uiq.stop()
    
    def test_post_when_not_running_ui_thread(self):
        root = MockRoot()
        uiq = UIUpdateQueue(root)
        uiq._ui_thread_id = threading.get_ident()
        
        result = []
        uiq.post(lambda: result.append(1))
        assert result == [1]
    
    def test_post_when_not_running_other_thread(self):
        root = MockRoot()
        uiq = UIUpdateQueue(root)
        uiq._ui_thread_id = -1
        
        result = []
        uiq.post(lambda: result.append(1))
        assert result == []
        assert uiq._dropped == 1
    
    def test_post_adds_to_queue(self):
        root = MockRoot()
        uiq = UIUpdateQueue(root)
        uiq._running = True
        uiq._ui_thread_id = threading.get_ident()
        
        uiq.post(lambda: None, priority=UIPriority.HIGH)
        assert not uiq._pq.empty()
    
    def test_dedup_keeps_latest(self):
        root = MockRoot()
        uiq = UIUpdateQueue(root)
        uiq._running = True
        uiq._ui_thread_id = threading.get_ident()
        
        uiq.post(lambda: None, key="test", priority=UIPriority.NORMAL)
        uiq.post(lambda: None, key="test", priority=UIPriority.NORMAL)
        
        assert "test" in uiq._dedup_latest
    
    def test_get_stats(self):
        root = MockRoot()
        uiq = UIUpdateQueue(root)
        uiq._executed = 10
        uiq._dropped = 2
        uiq._errors = 1
        
        stats = uiq.get_stats()
        assert stats["executed"] == 10
        assert stats["dropped"] == 2
        assert stats["errors"] == 1
    
    def test_flush_empty_queue(self):
        root = MockRoot()
        uiq = UIUpdateQueue(root)
        
        start = time.time()
        uiq.flush(timeout_sec=0.1)
        elapsed = time.time() - start
        
        assert elapsed < 0.15


class TestRunBg:
    def test_run_bg_executes_in_thread(self):
        result = []
        mock_app = MagicMock()
        
        def task():
            result.append(threading.current_thread().name)
        
        run_bg(mock_app, "TestTask", task)
        time.sleep(0.1)
        
        assert len(result) == 1
        assert result[0] == "TestTask"
    
    def test_run_bg_handles_exception(self):
        mock_app = MagicMock()
        mock_app.uiq = MagicMock()
        mock_app.status_label = MagicMock()
        
        def failing_task():
            raise ValueError("Test error")
        
        run_bg(mock_app, "FailingTask", failing_task)
        time.sleep(0.1)
        
        mock_app.uiq.post.assert_called()
