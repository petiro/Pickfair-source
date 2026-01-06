"""
Test API-Football Client
========================
Verifica comportamento con timeout, cache, retry.
"""

import pytest
import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_football import APIFootballClient


class TestAPIFootballClient:
    """Test API client resilienza."""
    
    def test_initial_status(self):
        """Status iniziale INIT."""
        client = APIFootballClient()
        assert client.status == "INIT"
        
    def test_force_timeout_flag(self):
        """force_timeout blocca richieste."""
        client = APIFootballClient()
        client.force_timeout = True
        
        start = time.time()
        result = client.get_live_fixtures()
        elapsed = time.time() - start
        
        assert elapsed >= 10
        assert client.status == "UNAVAILABLE"
        
    def test_forced_delay(self):
        """forced_delay rallenta richieste."""
        client = APIFootballClient()
        client.forced_delay = 2
        
        start = time.time()
        client._request("fixtures", {"live": "all"})
        elapsed = time.time() - start
        
        assert elapsed >= 2
        
    def test_cache_ttl(self):
        """Cache rispetta TTL."""
        client = APIFootballClient(cache_ttl=5)
        
        client._cache["test:None"] = {"data": "cached"}
        client._cache_time["test:None"] = time.time()
        
        result = client._get_cached("test:None")
        assert result == {"data": "cached"}
        
    def test_parse_fixture(self):
        """parse_fixture estrae dati corretti."""
        client = APIFootballClient()
        
        fixture = {
            "fixture": {
                "id": 12345,
                "status": {"short": "1H", "elapsed": 35, "extra": None}
            },
            "goals": {"home": 1, "away": 0},
            "teams": {
                "home": {"name": "Inter"},
                "away": {"name": "Milan"}
            },
            "events": []
        }
        
        parsed = client.parse_fixture(fixture)
        
        assert parsed["fixture_id"] == 12345
        assert parsed["status"] == "1H"
        assert parsed["minute"] == 35
        assert parsed["home_goals"] == 1
        assert parsed["away_goals"] == 0
        assert parsed["home_team"] == "Inter"
        assert parsed["away_team"] == "Milan"
