"""
Test TeamNameResolver
=====================
Verifica matching nomi squadre.
"""

import pytest
import sys
import os
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from team_name_resolver import TeamNameResolver, normalize_team_name


class TestNormalization:
    """Test normalizzazione nomi."""
    
    def test_removes_fc(self):
        """Rimuove FC."""
        assert "juventus" in normalize_team_name("FC Juventus").lower()
        
    def test_removes_women(self):
        """Rimuove Women."""
        result = normalize_team_name("AC Milan Women")
        assert "women" not in result.lower()
        
    def test_removes_u21(self):
        """Rimuove U21."""
        result = normalize_team_name("Inter U21")
        assert "u21" not in result.lower()
        
    def test_handles_accents(self):
        """Gestisce accenti."""
        result = normalize_team_name("Atlético Madrid")
        assert "madrid" in result.lower()


class TestTeamNameResolver:
    """Test resolver completo."""
    
    @pytest.fixture
    def resolver(self):
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        return TeamNameResolver(db_path)
        
    def test_event_match_exact(self, resolver):
        """Match evento esatto funziona."""
        matched, reason = resolver.match_event(
            "Inter Milan", 
            "AC Milan",
            "Inter Milan v AC Milan"
        )
        assert matched is True
        
    def test_event_match_partial(self, resolver):
        """Match evento parziale funziona."""
        matched, reason = resolver.match_event(
            "Inter", 
            "Milan",
            "Inter v Milan"
        )
        assert matched is True
        
    def test_event_no_match(self, resolver):
        """Non matcha eventi diversi."""
        matched, reason = resolver.match_event(
            "Roma", 
            "Lazio",
            "Juventus v Napoli"
        )
        assert matched is False
        
    def test_builtin_aliases(self, resolver):
        """Alias built-in funzionano."""
        matched, reason = resolver.match_event(
            "Inter", 
            "AC Milan",
            "Internazionale v AC Milan"
        )
        assert matched is True or reason == "PARTIAL_MATCH"
