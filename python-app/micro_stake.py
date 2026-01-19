"""
Micro Stake Module - Enables betting below Betfair's €2 minimum (€0.50, €1.00, €1.50)

Uses the "50 cent trick":
1. Place order at impossible odds (BACK high/LAY low) with €2
2. Modify order adding desired micro amount (€2 -> €2.50)
3. Betfair creates 2 orders: €2 + €0.50
4. Cancel the €2 order
5. Modify remaining micro order to real odds

This is a legitimate workaround used by trading software like Fairbot, Geeks Toy, etc.
"""

import logging
import time
import threading
from typing import Optional, Callable, Dict, Any, Tuple

MICRO_STAKE_IMPOSSIBLE_BACK_ODDS = 1000.0
MICRO_STAKE_IMPOSSIBLE_LAY_ODDS = 1.01
MICRO_STAKE_BASE = 2.0
MICRO_STAKE_VALID_AMOUNTS = [0.50, 1.00, 1.50]


class MicroStakeManager:
    """
    Manages micro stake orders (below €2 minimum).
    
    Thread-safe implementation for use with Pickfair's antifreeze architecture.
    """
    
    def __init__(self, betfair_client, on_progress: Optional[Callable[[str], None]] = None):
        """
        Initialize MicroStakeManager.
        
        Args:
            betfair_client: BetfairClient instance for API calls
            on_progress: Optional callback for progress updates
        """
        self.client = betfair_client
        self.on_progress = on_progress or (lambda msg: None)
        self._lock = threading.Lock()
        self._enabled = False
        self._micro_amount = 0.50
    
    @property
    def enabled(self) -> bool:
        return self._enabled
    
    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value
        logging.info(f"[MICRO_STAKE] {'Enabled' if value else 'Disabled'}")
    
    @property
    def micro_amount(self) -> float:
        return self._micro_amount
    
    @micro_amount.setter
    def micro_amount(self, value: float):
        if value not in MICRO_STAKE_VALID_AMOUNTS:
            raise ValueError(f"Invalid micro amount. Must be one of: {MICRO_STAKE_VALID_AMOUNTS}")
        self._micro_amount = value
        logging.info(f"[MICRO_STAKE] Amount set to €{value:.2f}")
    
    def is_micro_stake(self, stake: float) -> bool:
        """Check if stake requires micro stake handling."""
        return stake < MICRO_STAKE_BASE and stake in MICRO_STAKE_VALID_AMOUNTS
    
    def should_use_micro_stake(self, stake: float) -> bool:
        """Check if micro stake should be used for this order."""
        return self._enabled and self.is_micro_stake(stake)
    
    def place_micro_order(
        self,
        market_id: str,
        selection_id: int,
        side: str,
        target_odds: float,
        micro_stake: float,
        persistence_type: str = 'LAPSE'
    ) -> Dict[str, Any]:
        """
        Place a micro stake order using the 50 cent trick.
        
        Args:
            market_id: Betfair market ID
            selection_id: Runner selection ID
            side: 'BACK' or 'LAY'
            target_odds: Desired final odds
            micro_stake: Micro stake amount (0.50, 1.00, or 1.50)
            persistence_type: Order persistence type
            
        Returns:
            Dict with result: {'success': bool, 'bet_id': str, 'message': str}
        """
        if micro_stake not in MICRO_STAKE_VALID_AMOUNTS:
            return {
                'success': False,
                'bet_id': None,
                'message': f"Invalid micro stake: €{micro_stake}. Use 0.50, 1.00, or 1.50"
            }
        
        with self._lock:
            try:
                return self._execute_micro_order(
                    market_id, selection_id, side, target_odds, micro_stake, persistence_type
                )
            except Exception as e:
                logging.error(f"[MICRO_STAKE] Error: {e}")
                return {
                    'success': False,
                    'bet_id': None,
                    'message': f"Micro stake error: {str(e)}"
                }
    
    def _execute_micro_order(
        self,
        market_id: str,
        selection_id: int,
        side: str,
        target_odds: float,
        micro_stake: float,
        persistence_type: str
    ) -> Dict[str, Any]:
        """Execute the 50 cent trick sequence.
        
        The trick works as follows:
        1. Place order at impossible odds with total stake (€2 + micro)
        2. Partial cancel €2 using sizeReduction, leaving only micro amount
        3. Modify odds to real target using replace_orders
        """
        
        impossible_odds = (
            MICRO_STAKE_IMPOSSIBLE_BACK_ODDS if side == 'BACK' 
            else MICRO_STAKE_IMPOSSIBLE_LAY_ODDS
        )
        
        total_stake = MICRO_STAKE_BASE + micro_stake
        
        self.on_progress(f"Step 1/3: Piazzamento ordine €{total_stake:.2f}...")
        
        # Step 1: Place order at impossible odds with total stake
        # Use _bypass_micro=True to prevent recursion
        base_result = self.client.place_bet(
            market_id=market_id,
            selection_id=selection_id,
            side=side,
            price=impossible_odds,
            size=total_stake,
            persistence_type=persistence_type,
            _bypass_micro=True
        )
        
        if not base_result or base_result.get('status') != 'SUCCESS':
            return {
                'success': False,
                'bet_id': None,
                'message': f"Failed to place base order: {base_result}"
            }
        
        bet_id = base_result.get('instructionReports', [{}])[0].get('betId')
        if not bet_id:
            return {
                'success': False,
                'bet_id': None,
                'message': "No bet ID returned for base order"
            }
        
        logging.info(f"[MICRO_STAKE] Base order placed: {bet_id} (€{total_stake})")
        
        time.sleep(0.1)
        
        # Step 2: Partial cancel €2 using sizeReduction, leaving only micro amount
        self.on_progress(f"Step 2/3: Cancellazione parziale €{MICRO_STAKE_BASE:.2f}...")
        
        cancel_result = self.client.cancel_orders(
            market_id=market_id,
            bet_ids=bet_id,
            size_reduction=MICRO_STAKE_BASE
        )
        
        # Validate cancel result - check both overall status and instruction status
        if not cancel_result or cancel_result.get('status') not in ['SUCCESS', 'PROCESSED_WITH_ERRORS']:
            # Try to clean up
            self.client.cancel_orders(market_id=market_id, bet_ids=bet_id)
            return {
                'success': False,
                'bet_id': None,
                'message': f"Failed to partial cancel: {cancel_result}"
            }
        
        # Verify sizeCancelled matches expected amount
        instruction_reports = cancel_result.get('instructionReports', [])
        if instruction_reports:
            instr = instruction_reports[0]
            size_cancelled = instr.get('sizeCancelled', 0)
            instr_status = instr.get('status', 'UNKNOWN')
            
            if instr_status != 'SUCCESS' or abs(size_cancelled - MICRO_STAKE_BASE) > 0.01:
                logging.error(f"[MICRO_STAKE] Partial cancel mismatch: status={instr_status}, sizeCancelled={size_cancelled}, expected={MICRO_STAKE_BASE}")
                # Rollback: cancel the entire order
                self.client.cancel_orders(market_id=market_id, bet_ids=bet_id)
                return {
                    'success': False,
                    'bet_id': None,
                    'message': f"Partial cancel mismatch: cancelled €{size_cancelled:.2f} instead of €{MICRO_STAKE_BASE:.2f}"
                }
        
        logging.info(f"[MICRO_STAKE] Partial cancel verified, remaining: €{micro_stake}")
        
        time.sleep(0.1)
        
        # Step 3: Modify odds to real target
        self.on_progress(f"Step 3/3: Impostazione quota reale {target_odds}...")
        
        final_result = self.client.replace_orders(
            market_id=market_id,
            bet_id=bet_id,
            new_price=target_odds
        )
        
        if not final_result or final_result.get('status') != 'SUCCESS':
            return {
                'success': False,
                'bet_id': bet_id,
                'message': f"Micro order created but failed to set target odds: {final_result}"
            }
        
        # Get new bet ID if changed during replace
        final_bet_id = final_result.get('instructionReports', [{}])[0].get('betId', bet_id)
        
        logging.info(f"[MICRO_STAKE] Success! Micro order {final_bet_id} at €{micro_stake} @ {target_odds}")
        self.on_progress(f"Ordine micro €{micro_stake:.2f} piazzato!")
        
        return {
            'success': True,
            'bet_id': final_bet_id,
            'message': f"Micro stake €{micro_stake:.2f} placed at {target_odds}"
        }


def create_micro_stake_wrapper(manager: MicroStakeManager):
    """
    Create a wrapper function that intercepts place_bet calls.
    
    Returns a function that checks if micro stake should be used,
    and if so, routes through the micro stake flow.
    """
    def wrapper(
        original_place_fn: Callable,
        market_id: str,
        selection_id: int,
        side: str,
        price: float,
        size: float,
        **kwargs
    ) -> Dict[str, Any]:
        if manager.should_use_micro_stake(size):
            logging.info(f"[MICRO_STAKE] Intercepting order: €{size} -> micro stake flow")
            return manager.place_micro_order(
                market_id=market_id,
                selection_id=selection_id,
                side=side,
                target_odds=price,
                micro_stake=size,
                persistence_type=kwargs.get('persistence_type', 'LAPSE')
            )
        else:
            return original_place_fn(
                market_id=market_id,
                selection_id=selection_id,
                side=side,
                price=price,
                size=size,
                **kwargs
            )
    
    return wrapper
