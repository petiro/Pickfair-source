"""
DutchingController - Orchestratore unificato per dutching

Coordina UI → validazioni → AI → dutching → broker
Entry point unico per tutto il flusso di dutching.
"""

from typing import List, Dict, Optional
import time
import logging

from market_validator import MarketValidator
from dutching import (
    calculate_dutching_stakes,
    calculate_mixed_dutching
)
from ai.ai_pattern_engine import AIPatternEngine
from automation_engine import AutomationEngine
from safety_logger import get_safety_logger
from safe_mode import get_safe_mode_manager

logger = logging.getLogger(__name__)


class DutchingController:
    """
    Controller unificato per operazioni di dutching.
    
    Gestisce:
    - Validazione mercato
    - AI pattern per BACK/LAY auto-selection
    - Calcolo stake dutching
    - Piazzamento ordini (live o simulato)
    - Setup automazioni (SL/TP/Trailing)
    """
    
    def __init__(
        self,
        broker,
        pnl_engine,
        simulation: bool = False
    ):
        """
        Args:
            broker: SimulationBroker o BetfairClient
            pnl_engine: P&L Engine per calcoli live
            simulation: True per modalità simulazione
        """
        self.broker = broker
        self.pnl_engine = pnl_engine
        self.simulation = simulation
        
        self.ai_engine = AIPatternEngine()
        self.market_validator = MarketValidator()
        self.automation = AutomationEngine()
        self.safety_logger = get_safety_logger()
        self.safe_mode = get_safe_mode_manager()
    
    def submit_dutching(
        self,
        market_id: str,
        market_type: str,
        selections: List[Dict],
        total_stake: float,
        mode: str = "BACK",
        ai_enabled: bool = False,
        auto_green: bool = False,
        commission: float = 4.5,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        trailing: Optional[float] = None
    ) -> Dict:
        """
        Entry point UNICO per tutto il dutching.
        
        Args:
            market_id: ID mercato Betfair
            market_type: Tipo mercato (MATCH_ODDS, WINNER, etc.)
            selections: Lista selezioni con price, selectionId, runnerName
            total_stake: Stake totale da distribuire
            mode: 'BACK', 'LAY', o 'MIXED'
            ai_enabled: Se True, usa AI per decidere BACK/LAY per runner
            auto_green: Se True, imposta metadata per auto-green
            commission: Commissione % (default 4.5 Italia)
            stop_loss: Valore SL (opzionale)
            take_profit: Valore TP (opzionale)
            trailing: Valore trailing stop (opzionale)
            
        Returns:
            Dict con status, orders, simulation flag
            
        Raises:
            RuntimeError: Se SAFE MODE attivo
            ValueError: Se mercato non compatibile con AI
        """
        
        # SAFE MODE check
        if self.safe_mode.is_safe_mode_active:
            self.safety_logger.log_safe_mode_triggered(0, "submit_dutching blocked")
            raise RuntimeError("SAFE MODE attivo: dutching bloccato")
        
        # Market validation per AI
        if ai_enabled:
            if not self.market_validator.is_dutching_ready(market_type):
                self.safety_logger.log_ai_blocked(
                    market_type=market_type,
                    reason="MARKET_NOT_COMPATIBLE"
                )
                raise ValueError(f"Mercato {market_type} non compatibile con AI dutching")
        
        # AI pattern decision (BACK / LAY per runner)
        if ai_enabled:
            try:
                ai_sides = self.ai_engine.decide(selections)
                for sel in selections:
                    sel["side"] = ai_sides.get(sel["selectionId"], "BACK")
                    # Per calculate_mixed_dutching usa 'effectiveType'
                    sel["effectiveType"] = ai_sides.get(sel["selectionId"], "BACK")
                mode = "MIXED"
                logger.info(f"[CONTROLLER] AI sides: {ai_sides}")
            except Exception as e:
                self.safe_mode.report_error("AIPatternError", str(e), market_id)
                self.safety_logger.log_mixed_dutching_error(str(e))
                raise
        
        # Calcolo dutching (MATEMATICA INVARIATA)
        try:
            if mode == "MIXED":
                results, profit, implied_prob = calculate_mixed_dutching(
                    selections,
                    total_stake,
                    commission=commission
                )
            else:
                results, profit, implied_prob = calculate_dutching_stakes(
                    selections,
                    total_stake,
                    bet_type=mode,
                    commission=commission
                )
        except Exception as e:
            self.safe_mode.report_error("DutchingCalcError", str(e), market_id)
            logger.error(f"[CONTROLLER] Errore calcolo dutching: {e}")
            raise
        
        # Report success per safe mode
        self.safe_mode.report_success()
        
        # Piazzamento ordini
        placed = []
        placed_at = time.time()
        
        for r in results:
            try:
                order = self.broker.place_order(
                    market_id=market_id,
                    selection_id=r["selectionId"],
                    side=r.get("side", r.get("effectiveType", mode)),
                    price=r["price"],
                    size=r["stake"],
                    runner_name=r.get("runnerName", "")
                )
                
                # Aggiungi metadata per auto-green
                if auto_green:
                    order["auto_green"] = True
                    order["placed_at"] = placed_at
                    order["simulation"] = self.simulation
                
                placed.append(order)
                
                # Registra automazioni se configurate
                if stop_loss is not None or take_profit is not None or trailing is not None:
                    self.automation.add_position(
                        bet_id=order.get("betId", ""),
                        selection_id=r["selectionId"],
                        market_id=market_id,
                        entry_price=r["price"],
                        stake=r["stake"],
                        side=r.get("side", mode),
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        trailing=trailing
                    )
                    
            except Exception as e:
                logger.error(f"[CONTROLLER] Errore place_order: {e}")
                # Continua con gli altri ordini
        
        logger.info(f"[CONTROLLER] Dutching completato: {len(placed)} ordini, sim={self.simulation}")
        
        return {
            "status": "OK",
            "orders": placed,
            "simulation": self.simulation,
            "mode": mode,
            "total_stake": total_stake,
            "profit": profit,
            "implied_prob": implied_prob,
            "auto_green": auto_green
        }
    
    def validate_selections(self, selections: List[Dict]) -> List[str]:
        """
        Valida selezioni prima del submit.
        
        Returns:
            Lista di errori (vuota se tutto ok)
        """
        errors = []
        
        if not selections:
            errors.append("Nessuna selezione")
            return errors
        
        for sel in selections:
            if not sel.get("price") or sel["price"] <= 1:
                errors.append(f"{sel.get('runnerName', 'Runner')}: prezzo non valido")
            if not sel.get("selectionId"):
                errors.append(f"{sel.get('runnerName', 'Runner')}: selectionId mancante")
        
        return errors
    
    def set_simulation(self, enabled: bool):
        """Abilita/disabilita modalità simulazione."""
        self.simulation = enabled
        logger.info(f"[CONTROLLER] Simulation mode: {enabled}")
    
    def get_ai_analysis(self, selections: List[Dict]) -> List[Dict]:
        """
        Ottiene analisi WoM senza piazzare ordini.
        
        Returns:
            Lista analisi per UI preview
        """
        return self.ai_engine.get_wom_analysis(selections)
