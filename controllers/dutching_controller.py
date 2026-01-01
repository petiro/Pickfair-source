"""
DutchingController - Orchestratore unificato per dutching

Coordina UI → validazioni → AI → dutching → broker
Entry point unico per tutto il flusso di dutching.
"""

from typing import List, Dict, Optional, Tuple
import time
import logging
from dataclasses import dataclass, field

from market_validator import MarketValidator
from dutching import (
    calculate_dutching_stakes,
    calculate_mixed_dutching
)
from ai.ai_pattern_engine import AIPatternEngine
from automation_engine import AutomationEngine
from safety_logger import get_safety_logger
from safe_mode import get_safe_mode_manager
from trading_config import (
    MIN_STAKE, MAX_STAKE_PCT, MIN_LIQUIDITY, MAX_SPREAD_TICKS,
    MIN_PRICE, BOOK_WARNING, BOOK_BLOCK
)


@dataclass
class PreflightResult:
    """Risultato del preflight check."""
    is_valid: bool = True
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    liquidity_ok: bool = True
    spread_ok: bool = True
    stake_ok: bool = True
    price_ok: bool = True
    book_ok: bool = True
    details: Dict = field(default_factory=dict)

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
        trailing: Optional[float] = None,
        dry_run: bool = False
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
            dry_run: Se True, calcola tutto ma NON piazza ordini (preview)
            
        Returns:
            Dict con status, orders, simulation flag, preflight
            
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
        
        # Preflight check (sempre eseguito per avere warning)
        preflight = self.preflight_check(selections, total_stake, mode)
        
        # Valida stake per-runner (post calcolo)
        for r in results:
            stake = r.get("stake", 0)
            side = r.get("side", r.get("effectiveType", mode))
            
            # Per BACK: stake minimo
            if side == "BACK" and stake < MIN_STAKE:
                preflight.is_valid = False
                preflight.stake_ok = False
                preflight.errors.append(
                    f"{r.get('runnerName', 'Runner')}: stake €{stake:.2f} < min €{MIN_STAKE:.2f}"
                )
            
            # Per LAY: liability minima (stake * (price - 1))
            if side == "LAY":
                price = r.get("price", 1)
                liability = stake * (price - 1)
                if liability < MIN_STAKE:
                    preflight.is_valid = False
                    preflight.stake_ok = False
                    preflight.errors.append(
                        f"{r.get('runnerName', 'Runner')}: liability €{liability:.2f} < min €{MIN_STAKE:.2f}"
                    )
        
        # BLOCCO: se preflight fallisce, non piazzare ordini
        if not preflight.is_valid and not dry_run:
            logger.warning(f"[CONTROLLER] Preflight fallito: {preflight.errors}")
            return {
                "status": "PREFLIGHT_FAILED",
                "orders": [],
                "simulation": self.simulation,
                "mode": mode,
                "total_stake": total_stake,
                "profit": profit,
                "implied_prob": implied_prob,
                "auto_green": auto_green,
                "dry_run": dry_run,
                "preflight": {
                    "is_valid": preflight.is_valid,
                    "warnings": preflight.warnings,
                    "errors": preflight.errors,
                    "liquidity_ok": preflight.liquidity_ok,
                    "spread_ok": preflight.spread_ok,
                    "stake_ok": preflight.stake_ok,
                    "price_ok": preflight.price_ok,
                    "book_ok": preflight.book_ok,
                    "book_pct": preflight.details.get("book_pct", 0),
                    "details": preflight.details
                }
            }
        
        # Piazzamento ordini (o preview se dry_run)
        placed = []
        placed_at = time.time()
        
        for r in results:
            if dry_run:
                # DRY RUN: crea ordine preview senza piazzare
                order = {
                    "betId": f"DRY_{r['selectionId']}_{int(placed_at)}",
                    "selectionId": r["selectionId"],
                    "side": r.get("side", r.get("effectiveType", mode)),
                    "price": r["price"],
                    "size": r["stake"],
                    "runnerName": r.get("runnerName", ""),
                    "status": "DRY_RUN",
                    "dry_run": True
                }
                if auto_green:
                    order["auto_green"] = True
                    order["placed_at"] = placed_at
                    order["simulation"] = self.simulation
                placed.append(order)
            else:
                # LIVE/SIMULATION: piazza ordine reale
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
        
        status = "DRY_RUN" if dry_run else "OK"
        logger.info(f"[CONTROLLER] Dutching {status}: {len(placed)} ordini, sim={self.simulation}")
        
        return {
            "status": status,
            "orders": placed,
            "simulation": self.simulation,
            "mode": mode,
            "total_stake": total_stake,
            "profit": profit,
            "implied_prob": implied_prob,
            "auto_green": auto_green,
            "dry_run": dry_run,
            "preflight": {
                "is_valid": preflight.is_valid,
                "warnings": preflight.warnings,
                "errors": preflight.errors,
                "liquidity_ok": preflight.liquidity_ok,
                "spread_ok": preflight.spread_ok,
                "stake_ok": preflight.stake_ok,
                "price_ok": preflight.price_ok,
                "book_ok": preflight.book_ok,
                "book_pct": preflight.details.get("book_pct", 0),
                "details": preflight.details
            }
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
    
    def preflight_check(
        self,
        selections: List[Dict],
        total_stake: float,
        mode: str = "BACK"
    ) -> PreflightResult:
        """
        Controllo pre-ordine per validare condizioni di mercato.
        
        Verifica:
        - Liquidità minima per ogni runner
        - Spread BACK/LAY entro limiti
        - Stake non supera % della liquidità disponibile
        - Stake totale >= MIN_STAKE * num_selections
        
        Args:
            selections: Lista selezioni con back_ladder, lay_ladder
            total_stake: Stake totale da distribuire
            mode: 'BACK', 'LAY', o 'MIXED'
            
        Returns:
            PreflightResult con is_valid, warnings, errors
        """
        result = PreflightResult()
        num_selections = len(selections)
        
        if num_selections == 0:
            result.is_valid = False
            result.errors.append("Nessuna selezione")
            return result
        
        # Check stake minimo totale
        min_total = MIN_STAKE * num_selections
        if total_stake < min_total:
            result.is_valid = False
            result.stake_ok = False
            result.errors.append(
                f"Stake totale €{total_stake:.2f} insufficiente (min €{min_total:.2f})"
            )
        
        total_liquidity = 0.0
        total_implied_prob = 0.0
        
        for sel in selections:
            runner_name = sel.get("runnerName", f"ID {sel.get('selectionId', '?')}")
            price = sel.get("price", 0)
            back_ladder = sel.get("back_ladder", [])
            lay_ladder = sel.get("lay_ladder", [])
            
            # Check quota minima (troppo bassa = rischio)
            if price > 0 and price < MIN_PRICE:
                result.price_ok = False
                result.warnings.append(
                    f"{runner_name}: quota {price:.2f} troppo bassa (min {MIN_PRICE:.2f})"
                )
            
            # Accumula implied probability per Book %
            if price > 1:
                total_implied_prob += 1.0 / price
            
            # Calcola liquidità disponibile
            back_liq = sum(p.get("size", 0) for p in back_ladder)
            lay_liq = sum(p.get("size", 0) for p in lay_ladder)
            
            side = sel.get("side", sel.get("effectiveType", mode))
            relevant_liq = back_liq if side == "BACK" else lay_liq
            total_liquidity += relevant_liq
            
            # Check liquidità minima
            if relevant_liq < MIN_LIQUIDITY:
                result.liquidity_ok = False
                result.warnings.append(
                    f"{runner_name}: liquidità {side} bassa (€{relevant_liq:.0f})"
                )
            
            # Check spread (differenza tick tra best BACK e best LAY)
            if back_ladder and lay_ladder:
                best_back = back_ladder[0].get("price", 0)
                best_lay = lay_ladder[0].get("price", 0)
                
                if best_back > 0 and best_lay > 0:
                    spread = best_lay - best_back
                    # Stima approssimativa tick (1 tick ~ 0.01-0.02 a quote basse)
                    tick_size = 0.02 if best_back < 2 else 0.05 if best_back < 4 else 0.1
                    spread_ticks = spread / tick_size if tick_size > 0 else 0
                    
                    if spread_ticks > MAX_SPREAD_TICKS:
                        result.spread_ok = False
                        result.warnings.append(
                            f"{runner_name}: spread largo ({spread_ticks:.0f} tick)"
                        )
                    
                    result.details[sel.get("selectionId")] = {
                        "back_liq": back_liq,
                        "lay_liq": lay_liq,
                        "best_back": best_back,
                        "best_lay": best_lay,
                        "spread_ticks": spread_ticks
                    }
        
        # Check stake vs liquidità (non superare MAX_STAKE_PCT della liquidità)
        if total_liquidity > 0:
            stake_pct = total_stake / total_liquidity
            if stake_pct > MAX_STAKE_PCT:
                result.warnings.append(
                    f"Stake alto rispetto a liquidità ({stake_pct*100:.0f}% > {MAX_STAKE_PCT*100:.0f}%)"
                )
        
        # Check Book % (somma implied prob * 100)
        book_pct = total_implied_prob * 100
        if book_pct > BOOK_BLOCK:
            result.book_ok = False
            result.is_valid = False
            result.errors.append(
                f"Book {book_pct:.1f}% troppo alto (blocco a {BOOK_BLOCK:.0f}%)"
            )
        elif book_pct > BOOK_WARNING:
            result.book_ok = False
            result.warnings.append(
                f"Book {book_pct:.1f}% elevato (warning a {BOOK_WARNING:.0f}%)"
            )
        
        result.details["book_pct"] = book_pct
        
        # Se ci sono errori, non è valido
        if result.errors:
            result.is_valid = False
        
        # Log preflight
        logger.info(f"[PREFLIGHT] valid={result.is_valid}, warnings={len(result.warnings)}, errors={len(result.errors)}")
        
        return result
