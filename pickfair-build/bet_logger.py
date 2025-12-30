"""
Pickfair - Bet Logger Module
Universal logging functions for all betting events.
"""

import logging
import traceback
import json
from datetime import datetime
from storage import get_persistent_storage

logger = logging.getLogger(__name__)


class BetLogger:
    """Centralized bet event logger."""
    
    def __init__(self):
        self.storage = get_persistent_storage()
    
    def log_order_placed(
        self,
        market_id,
        selection_id,
        side,
        stake,
        price,
        bet_id=None,
        market_name=None,
        event_name=None,
        runner_name=None,
        source='MANUAL',
        telegram_chat_id=None,
        telegram_message_id=None
    ):
        """Log when an order is placed."""
        return self.storage.log_bet_event(
            market_id=market_id,
            selection_id=selection_id,
            side=side,
            stake=stake,
            price=price,
            status='PLACED',
            bet_id=bet_id,
            market_name=market_name,
            event_name=event_name,
            runner_name=runner_name,
            source=source,
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id
        )
    
    def log_order_matched(
        self,
        market_id,
        selection_id,
        side,
        stake,
        price,
        bet_id,
        matched_size,
        avg_price_matched=None,
        profit=None,
        runner_name=None,
        source='MANUAL'
    ):
        """Log when an order is matched."""
        return self.storage.log_bet_event(
            market_id=market_id,
            selection_id=selection_id,
            side=side,
            stake=stake,
            price=price,
            status='MATCHED',
            bet_id=bet_id,
            matched_size=matched_size,
            avg_price_matched=avg_price_matched,
            profit=profit,
            runner_name=runner_name,
            source=source
        )
    
    def log_order_partial(
        self,
        market_id,
        selection_id,
        side,
        stake,
        price,
        bet_id,
        matched_size,
        runner_name=None,
        source='MANUAL'
    ):
        """Log partial match."""
        return self.storage.log_bet_event(
            market_id=market_id,
            selection_id=selection_id,
            side=side,
            stake=stake,
            price=price,
            status='PARTIAL',
            bet_id=bet_id,
            matched_size=matched_size,
            runner_name=runner_name,
            source=source
        )
    
    def log_order_failed(
        self,
        market_id,
        selection_id,
        side,
        stake,
        price,
        error_code,
        error_message=None,
        runner_name=None,
        source='MANUAL',
        telegram_chat_id=None
    ):
        """Log when an order fails."""
        self.storage.log_error(
            level='ERROR',
            source='ORDER_PLACEMENT',
            message=f"Order failed: {error_code}",
            details=json.dumps({
                'market_id': market_id,
                'selection_id': selection_id,
                'side': side,
                'stake': stake,
                'price': price,
                'error_code': error_code,
                'error_message': error_message
            }),
            market_id=market_id
        )
        
        return self.storage.log_bet_event(
            market_id=market_id,
            selection_id=selection_id,
            side=side,
            stake=stake,
            price=price,
            status='FAILED',
            error_code=error_code,
            error_message=error_message,
            runner_name=runner_name,
            source=source,
            telegram_chat_id=telegram_chat_id
        )
    
    def log_order_cancelled(
        self,
        market_id,
        selection_id,
        side,
        stake,
        price,
        bet_id,
        reason=None
    ):
        """Log when an order is cancelled."""
        return self.storage.log_bet_event(
            market_id=market_id,
            selection_id=selection_id,
            side=side,
            stake=stake,
            price=price,
            status='CANCELLED',
            bet_id=bet_id,
            error_message=reason
        )
    
    def log_order_replaced(
        self,
        market_id,
        selection_id,
        side,
        old_stake,
        old_price,
        new_stake,
        new_price,
        bet_id,
        new_bet_id=None
    ):
        """Log when an order is replaced."""
        return self.storage.log_bet_event(
            market_id=market_id,
            selection_id=selection_id,
            side=side,
            stake=new_stake,
            price=new_price,
            status='REPLACED',
            bet_id=new_bet_id or bet_id,
            error_message=f"Replaced from {old_stake}@{old_price}"
        )
    
    def log_settled(
        self,
        market_id,
        selection_id,
        side,
        stake,
        price,
        bet_id,
        profit,
        commission=None,
        runner_name=None
    ):
        """Log bet settlement."""
        status = 'SETTLED_WIN' if profit > 0 else 'SETTLED_LOSS'
        return self.storage.log_bet_event(
            market_id=market_id,
            selection_id=selection_id,
            side=side,
            stake=stake,
            price=price,
            status=status,
            bet_id=bet_id,
            profit=profit,
            commission=commission,
            runner_name=runner_name
        )
    
    def log_cashout_requested(
        self,
        bet_id,
        market_id,
        cashout_type,
        requested_profit,
        lay_stake=None,
        lay_price=None,
        selection_id=None
    ):
        """Log cashout request."""
        return self.storage.log_cashout_event(
            bet_id=bet_id,
            market_id=market_id,
            cashout_type=cashout_type,
            status='REQUESTED',
            selection_id=selection_id,
            requested_profit=requested_profit,
            lay_stake=lay_stake,
            lay_price=lay_price
        )
    
    def log_cashout_completed(
        self,
        bet_id,
        market_id,
        actual_profit,
        cashout_type='MANUAL'
    ):
        """Log successful cashout."""
        self.storage.update_cashout_status(
            bet_id=bet_id,
            status='COMPLETED',
            actual_profit=actual_profit
        )
        
        return self.storage.log_bet_event(
            market_id=market_id,
            selection_id='',
            side='CASHOUT',
            stake=0,
            price=0,
            status='CASHED_OUT',
            bet_id=bet_id,
            profit=actual_profit
        )
    
    def log_cashout_failed(
        self,
        bet_id,
        market_id,
        error_code,
        error_message=None
    ):
        """Log failed cashout."""
        self.storage.log_error(
            level='WARNING',
            source='CASHOUT',
            message=f"Cashout failed: {error_code}",
            details=error_message,
            market_id=market_id,
            bet_id=bet_id
        )
        
        return self.storage.update_cashout_status(
            bet_id=bet_id,
            status='FAILED',
            error_code=error_code,
            error_message=error_message
        )
    
    def log_cashout_expired(self, bet_id, market_id, reason=None):
        """Log expired cashout request."""
        return self.storage.update_cashout_status(
            bet_id=bet_id,
            status='EXPIRED',
            error_message=reason or 'Cashout request expired'
        )
    
    def log_telegram_signal_received(
        self,
        chat_id,
        message_id,
        message_text,
        chat_name=None,
        parsed_data=None
    ):
        """Log incoming Telegram signal."""
        return self.storage.log_telegram_event(
            chat_id=chat_id,
            action='SIGNAL_RECEIVED',
            status='RECEIVED',
            message_id=message_id,
            message_text=message_text[:500] if message_text else None,
            chat_name=chat_name,
            parsed_data=json.dumps(parsed_data) if parsed_data else None
        )
    
    def log_telegram_signal_processed(
        self,
        chat_id,
        message_id,
        bet_id=None,
        processing_time_ms=None
    ):
        """Log processed Telegram signal."""
        return self.storage.log_telegram_event(
            chat_id=chat_id,
            action='SIGNAL_PROCESSED',
            status='PROCESSED',
            message_id=message_id,
            bet_id=bet_id,
            processing_time_ms=processing_time_ms
        )
    
    def log_telegram_signal_failed(
        self,
        chat_id,
        message_id,
        error_code,
        error_message=None,
        retry_count=0
    ):
        """Log failed Telegram signal processing."""
        return self.storage.log_telegram_event(
            chat_id=chat_id,
            action='SIGNAL_FAILED',
            status='FAILED',
            message_id=message_id,
            error_code=error_code,
            error_message=error_message,
            retry_count=retry_count
        )
    
    def log_telegram_flood_wait(
        self,
        chat_id,
        flood_wait_seconds,
        message_id=None
    ):
        """Log Telegram flood wait."""
        return self.storage.log_telegram_event(
            chat_id=chat_id,
            action='FLOOD_WAIT',
            status='WAITING',
            message_id=message_id,
            flood_wait=flood_wait_seconds
        )
    
    def log_telegram_message_sent(
        self,
        chat_id,
        message_text,
        bet_id=None,
        chat_name=None
    ):
        """Log outgoing Telegram message."""
        return self.storage.log_telegram_event(
            chat_id=chat_id,
            action='MESSAGE_SENT',
            status='SENT',
            message_text=message_text[:500] if message_text else None,
            bet_id=bet_id,
            chat_name=chat_name
        )
    
    def log_error(
        self,
        source,
        message,
        level='ERROR',
        details=None,
        market_id=None,
        bet_id=None,
        exception=None
    ):
        """Log a general error."""
        stack_trace = None
        if exception:
            stack_trace = ''.join(traceback.format_exception(type(exception), exception, exception.__traceback__))
        
        return self.storage.log_error(
            level=level,
            source=source,
            message=message,
            details=details,
            stack_trace=stack_trace,
            market_id=market_id,
            bet_id=bet_id
        )
    
    def log_warning(self, source, message, details=None, market_id=None, bet_id=None):
        """Log a warning."""
        return self.log_error(
            source=source,
            message=message,
            level='WARNING',
            details=details,
            market_id=market_id,
            bet_id=bet_id
        )
    
    def log_info(self, source, message, details=None, market_id=None, bet_id=None):
        """Log an info event."""
        return self.log_error(
            source=source,
            message=message,
            level='INFO',
            details=details,
            market_id=market_id,
            bet_id=bet_id
        )
    
    def save_position(
        self,
        bet_id,
        market_id,
        selection_id,
        side,
        stake,
        price,
        status,
        matched_size=0,
        trailing_enabled=False,
        trailing_stop_ticks=None,
        max_profit_seen=None,
        replace_guard_enabled=False
    ):
        """Save open position for recovery."""
        return self.storage.save_open_position(
            bet_id=bet_id,
            market_id=market_id,
            selection_id=selection_id,
            side=side,
            stake=stake,
            price=price,
            status=status,
            matched_size=matched_size,
            trailing_enabled=trailing_enabled,
            trailing_stop_ticks=trailing_stop_ticks,
            max_profit_seen=max_profit_seen,
            replace_guard_enabled=replace_guard_enabled
        )
    
    def close_position(self, bet_id):
        """Remove closed position."""
        return self.storage.remove_open_position(bet_id)
    
    def get_open_positions(self):
        """Get all open positions for recovery."""
        return self.storage.recover_open_positions()


_bet_logger = None

def get_bet_logger():
    """Get singleton BetLogger instance."""
    global _bet_logger
    if _bet_logger is None:
        _bet_logger = BetLogger()
    return _bet_logger
