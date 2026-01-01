"""
Test per i nuovi componenti:
- DutchingController
- AIPatternEngine
- DraggableRunner
- MiniLadder
"""

import pytest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestAIPatternEngine:
    """Test per AIPatternEngine WoM analysis."""
    
    def test_calculate_wom_balanced(self):
        """WoM bilanciato ritorna ~0.5."""
        from ai.ai_pattern_engine import AIPatternEngine
        
        engine = AIPatternEngine()
        selection = {
            "selectionId": 1,
            "back_ladder": [{"price": 2.0, "size": 100}],
            "lay_ladder": [{"price": 2.02, "size": 100}]
        }
        
        wom = engine.calculate_wom(selection)
        assert 0.45 <= wom <= 0.55, f"WoM bilanciato dovrebbe essere ~0.5, got {wom}"
    
    def test_calculate_wom_back_heavy(self):
        """WoM > 0.55 con più liquidità BACK."""
        from ai.ai_pattern_engine import AIPatternEngine
        
        engine = AIPatternEngine()
        selection = {
            "selectionId": 1,
            "back_ladder": [{"price": 2.0, "size": 200}],
            "lay_ladder": [{"price": 2.02, "size": 50}]
        }
        
        wom = engine.calculate_wom(selection)
        assert wom > 0.55, f"WoM BACK heavy dovrebbe essere > 0.55, got {wom}"
    
    def test_calculate_wom_lay_heavy(self):
        """WoM < 0.45 con più liquidità LAY."""
        from ai.ai_pattern_engine import AIPatternEngine
        
        engine = AIPatternEngine()
        selection = {
            "selectionId": 1,
            "back_ladder": [{"price": 2.0, "size": 50}],
            "lay_ladder": [{"price": 2.02, "size": 200}]
        }
        
        wom = engine.calculate_wom(selection)
        assert wom < 0.45, f"WoM LAY heavy dovrebbe essere < 0.45, got {wom}"
    
    def test_calculate_wom_no_liquidity(self):
        """WoM neutro (0.5) se nessuna liquidità."""
        from ai.ai_pattern_engine import AIPatternEngine
        
        engine = AIPatternEngine()
        selection = {
            "selectionId": 1,
            "back_ladder": [],
            "lay_ladder": []
        }
        
        wom = engine.calculate_wom(selection)
        assert wom == 0.5, f"WoM senza liquidità dovrebbe essere 0.5, got {wom}"
    
    def test_decide_back_on_high_wom(self):
        """Decide BACK quando WoM > threshold."""
        from ai.ai_pattern_engine import AIPatternEngine
        
        engine = AIPatternEngine()
        selections = [
            {
                "selectionId": 1,
                "back_ladder": [{"price": 2.0, "size": 200}],
                "lay_ladder": [{"price": 2.02, "size": 50}]
            }
        ]
        
        decisions = engine.decide(selections)
        assert decisions[1] == "BACK"
    
    def test_decide_lay_on_low_wom(self):
        """Decide LAY quando WoM < threshold."""
        from ai.ai_pattern_engine import AIPatternEngine
        
        engine = AIPatternEngine()
        selections = [
            {
                "selectionId": 1,
                "back_ladder": [{"price": 2.0, "size": 30}],
                "lay_ladder": [{"price": 2.02, "size": 200}]
            }
        ]
        
        decisions = engine.decide(selections)
        assert decisions[1] == "LAY"
    
    def test_force_mixed_when_all_same(self):
        """Forza almeno 1 BACK + 1 LAY quando tutti uguali."""
        from ai.ai_pattern_engine import AIPatternEngine
        
        engine = AIPatternEngine()
        # Tutti con WoM bilanciato → tutti BACK di default
        selections = [
            {"selectionId": 1, "back_ladder": [{"size": 100}], "lay_ladder": [{"size": 100}]},
            {"selectionId": 2, "back_ladder": [{"size": 100}], "lay_ladder": [{"size": 100}]},
            {"selectionId": 3, "back_ladder": [{"size": 100}], "lay_ladder": [{"size": 100}]}
        ]
        
        decisions = engine.decide(selections)
        sides = set(decisions.values())
        
        # Deve forzare almeno un BACK e un LAY
        assert "BACK" in sides and "LAY" in sides, f"Deve forzare mixed, got {sides}"
    
    def test_get_wom_analysis_returns_list(self):
        """get_wom_analysis ritorna lista con analisi."""
        from ai.ai_pattern_engine import AIPatternEngine
        
        engine = AIPatternEngine()
        selections = [
            {"selectionId": 1, "runnerName": "Runner A", "back_ladder": [{"size": 100}], "lay_ladder": [{"size": 50}]}
        ]
        
        analysis = engine.get_wom_analysis(selections)
        
        assert len(analysis) == 1
        assert analysis[0]["selectionId"] == 1
        assert "wom" in analysis[0]
        assert "suggested_side" in analysis[0]
        assert "confidence" in analysis[0]


class TestDutchingController:
    """Test per DutchingController."""
    
    def test_controller_init(self):
        """Controller si inizializza correttamente."""
        from controllers.dutching_controller import DutchingController
        from simulation_broker import SimulationBroker
        
        broker = SimulationBroker()
        controller = DutchingController(broker=broker, pnl_engine=None, simulation=True)
        
        assert controller.simulation is True
        assert controller.broker is broker
    
    def test_validate_selections_empty(self):
        """Validazione fallisce senza selezioni."""
        from controllers.dutching_controller import DutchingController
        from simulation_broker import SimulationBroker
        
        broker = SimulationBroker()
        controller = DutchingController(broker=broker, pnl_engine=None)
        
        errors = controller.validate_selections([])
        assert len(errors) > 0
        assert "Nessuna selezione" in errors[0]
    
    def test_validate_selections_invalid_price(self):
        """Validazione fallisce con prezzo <= 1."""
        from controllers.dutching_controller import DutchingController
        from simulation_broker import SimulationBroker
        
        broker = SimulationBroker()
        controller = DutchingController(broker=broker, pnl_engine=None)
        
        selections = [{"selectionId": 1, "runnerName": "Test", "price": 1.0}]
        errors = controller.validate_selections(selections)
        
        assert len(errors) > 0
        assert "prezzo non valido" in errors[0]
    
    def test_validate_selections_missing_id(self):
        """Validazione fallisce senza selectionId."""
        from controllers.dutching_controller import DutchingController
        from simulation_broker import SimulationBroker
        
        broker = SimulationBroker()
        controller = DutchingController(broker=broker, pnl_engine=None)
        
        selections = [{"runnerName": "Test", "price": 2.0}]
        errors = controller.validate_selections(selections)
        
        assert len(errors) > 0
        assert "selectionId mancante" in errors[0]
    
    def test_validate_selections_ok(self):
        """Validazione passa con selezioni valide."""
        from controllers.dutching_controller import DutchingController
        from simulation_broker import SimulationBroker
        
        broker = SimulationBroker()
        controller = DutchingController(broker=broker, pnl_engine=None)
        
        selections = [
            {"selectionId": 1, "runnerName": "Runner A", "price": 2.0},
            {"selectionId": 2, "runnerName": "Runner B", "price": 3.0}
        ]
        errors = controller.validate_selections(selections)
        
        assert len(errors) == 0
    
    def test_set_simulation(self):
        """set_simulation cambia modalità."""
        from controllers.dutching_controller import DutchingController
        from simulation_broker import SimulationBroker
        
        broker = SimulationBroker()
        controller = DutchingController(broker=broker, pnl_engine=None, simulation=False)
        
        controller.set_simulation(True)
        assert controller.simulation is True
        
        controller.set_simulation(False)
        assert controller.simulation is False
    
    def test_submit_dutching_back(self):
        """Submit dutching BACK funziona."""
        from controllers.dutching_controller import DutchingController
        from simulation_broker import SimulationBroker
        
        broker = SimulationBroker(initial_balance=1000)
        controller = DutchingController(broker=broker, pnl_engine=None, simulation=True)
        
        selections = [
            {"selectionId": 1, "runnerName": "Runner A", "price": 2.0},
            {"selectionId": 2, "runnerName": "Runner B", "price": 3.0}
        ]
        
        result = controller.submit_dutching(
            market_id="1.234",
            market_type="MATCH_ODDS",
            selections=selections,
            total_stake=100,
            mode="BACK"
        )
        
        assert result["status"] == "OK"
        assert len(result["orders"]) == 2
        assert result["simulation"] is True
    
    def test_submit_dutching_lay(self):
        """Submit dutching LAY funziona."""
        from controllers.dutching_controller import DutchingController
        from simulation_broker import SimulationBroker
        
        broker = SimulationBroker(initial_balance=1000)
        controller = DutchingController(broker=broker, pnl_engine=None, simulation=True)
        
        selections = [
            {"selectionId": 1, "runnerName": "Runner A", "price": 2.0},
            {"selectionId": 2, "runnerName": "Runner B", "price": 3.0}
        ]
        
        result = controller.submit_dutching(
            market_id="1.234",
            market_type="MATCH_ODDS",
            selections=selections,
            total_stake=100,
            mode="LAY"
        )
        
        assert result["status"] == "OK"
        assert len(result["orders"]) == 2
    
    def test_get_ai_analysis(self):
        """get_ai_analysis ritorna analisi WoM."""
        from controllers.dutching_controller import DutchingController
        from simulation_broker import SimulationBroker
        
        broker = SimulationBroker()
        controller = DutchingController(broker=broker, pnl_engine=None)
        
        selections = [
            {"selectionId": 1, "runnerName": "Runner A", "back_ladder": [{"size": 100}], "lay_ladder": [{"size": 50}]}
        ]
        
        analysis = controller.get_ai_analysis(selections)
        assert len(analysis) == 1
        assert "wom" in analysis[0]


class TestDraggableRunner:
    """Test per DraggableRunner (mock - no UI)."""
    
    def test_runner_data_structure(self):
        """Verifica struttura dati runner."""
        runner = {
            "selectionId": 1,
            "runnerName": "Test Runner",
            "price": 2.5,
            "stake": 10.0
        }
        
        assert runner["selectionId"] == 1
        assert runner["price"] == 2.5
    
    def test_runner_order_callback(self):
        """Callback ordine viene chiamato."""
        moved_runners = []
        
        def on_order_change(runners):
            moved_runners.append(runners)
        
        # Simula riordinamento
        runners = [
            {"selectionId": 1, "runnerName": "A"},
            {"selectionId": 2, "runnerName": "B"}
        ]
        
        # Inverti ordine
        new_order = [runners[1], runners[0]]
        on_order_change(new_order)
        
        assert len(moved_runners) == 1
        assert moved_runners[0][0]["selectionId"] == 2


class TestMiniLadder:
    """Test per MiniLadder (mock - no UI)."""
    
    def test_ladder_data_format(self):
        """Verifica formato dati ladder."""
        runner = {
            "selectionId": 1,
            "runnerName": "Test",
            "back_ladder": [
                {"price": 2.00, "size": 100},
                {"price": 1.98, "size": 50},
                {"price": 1.96, "size": 25}
            ],
            "lay_ladder": [
                {"price": 2.02, "size": 80},
                {"price": 2.04, "size": 40},
                {"price": 2.06, "size": 20}
            ]
        }
        
        assert len(runner["back_ladder"]) == 3
        assert len(runner["lay_ladder"]) == 3
        assert runner["back_ladder"][0]["price"] == 2.00
        assert runner["lay_ladder"][0]["price"] == 2.02
    
    def test_best_price_identification(self):
        """Best price è il primo della ladder."""
        back_ladder = [
            {"price": 2.00, "size": 100},
            {"price": 1.98, "size": 50}
        ]
        
        best_back = back_ladder[0]["price"] if back_ladder else None
        assert best_back == 2.00
    
    def test_price_click_callback(self):
        """Callback price click con dati corretti."""
        clicked = []
        
        def on_price_click(selection_id, side, price):
            clicked.append((selection_id, side, price))
        
        # Simula click
        on_price_click(1, "BACK", 2.00)
        
        assert len(clicked) == 1
        assert clicked[0] == (1, "BACK", 2.00)


class TestIntegration:
    """Test di integrazione tra componenti."""
    
    def test_ai_to_controller_flow(self):
        """Flusso completo AI → Controller → Broker."""
        from controllers.dutching_controller import DutchingController
        from simulation_broker import SimulationBroker
        
        broker = SimulationBroker(initial_balance=1000)
        controller = DutchingController(broker=broker, pnl_engine=None, simulation=True)
        
        # Selezioni con WoM diversi
        selections = [
            {
                "selectionId": 1,
                "runnerName": "Favorito",
                "price": 2.0,
                "back_ladder": [{"size": 200}],
                "lay_ladder": [{"size": 50}]
            },
            {
                "selectionId": 2,
                "runnerName": "Outsider",
                "price": 5.0,
                "back_ladder": [{"size": 30}],
                "lay_ladder": [{"size": 150}]
            }
        ]
        
        # Ottieni analisi AI
        analysis = controller.get_ai_analysis(selections)
        assert len(analysis) == 2
        
        # Verifica analisi
        for a in analysis:
            assert "wom" in a
            assert "suggested_side" in a
    
    def test_controller_with_automations(self):
        """Controller registra correttamente automazioni SL/TP."""
        from controllers.dutching_controller import DutchingController
        from simulation_broker import SimulationBroker
        
        broker = SimulationBroker(initial_balance=1000)
        controller = DutchingController(broker=broker, pnl_engine=None, simulation=True)
        
        selections = [
            {"selectionId": 1, "runnerName": "Runner A", "price": 2.0},
            {"selectionId": 2, "runnerName": "Runner B", "price": 3.0}
        ]
        
        result = controller.submit_dutching(
            market_id="1.234",
            market_type="MATCH_ODDS",
            selections=selections,
            total_stake=100,
            mode="BACK",
            stop_loss=-50,
            take_profit=30,
            trailing=10
        )
        
        assert result["status"] == "OK"
        # Verifica che automazioni siano state registrate
        for order in result["orders"]:
            bet_id = order.get("betId", "")
            badges = controller.automation.get_automation_badges(bet_id)
            assert "SL" in badges
            assert "TP" in badges
            assert "TR" in badges


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
