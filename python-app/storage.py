"""
Pickfair - Persistent Storage Module
SQLite database for bet history, Telegram audit, error logging, and state recovery.
"""

import sqlite3
import logging
import os
from pathlib import Path
from datetime import datetime, timedelta
from contextlib import contextmanager
import threading

logger = logging.getLogger(__name__)

class PersistentStorage:
    """SQLite-based persistent storage for Pickfair."""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls, db_path=None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, db_path=None):
        if self._initialized:
            return
        
        if db_path is None:
            appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
            self.db_path = Path(appdata) / 'Pickfair' / 'pickfair_history.db'
        else:
            self.db_path = Path(db_path)
        
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_database()
        self._initialized = True
        logger.info(f"[STORAGE] Database initialized: {self.db_path}")
    
    def _get_connection(self):
        """Get thread-local database connection."""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=30.0
            )
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA busy_timeout=30000")
        return self._local.conn
    
    @contextmanager
    def get_db(self):
        """Context manager for database operations."""
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"[STORAGE] Database error: {e}")
            raise
    
    def _init_database(self):
        """Initialize database tables."""
        with self.get_db() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS bet_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bet_id TEXT,
                market_id TEXT NOT NULL,
                market_name TEXT,
                event_name TEXT,
                selection_id TEXT NOT NULL,
                runner_name TEXT,
                side TEXT NOT NULL,
                stake REAL NOT NULL,
                price REAL NOT NULL,
                matched_size REAL DEFAULT 0,
                avg_price_matched REAL,
                status TEXT NOT NULL,
                profit REAL,
                liability REAL,
                commission REAL,
                error_code TEXT,
                error_message TEXT,
                source TEXT DEFAULT 'MANUAL',
                telegram_chat_id TEXT,
                telegram_message_id TEXT,
                placed_at DATETIME,
                matched_at DATETIME,
                settled_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE INDEX IF NOT EXISTS idx_bet_history_bet_id ON bet_history(bet_id);
            CREATE INDEX IF NOT EXISTS idx_bet_history_market_id ON bet_history(market_id);
            CREATE INDEX IF NOT EXISTS idx_bet_history_status ON bet_history(status);
            CREATE INDEX IF NOT EXISTS idx_bet_history_created_at ON bet_history(created_at);
            CREATE INDEX IF NOT EXISTS idx_bet_history_source ON bet_history(source);
            
            CREATE TABLE IF NOT EXISTS telegram_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bet_id TEXT,
                chat_id TEXT NOT NULL,
                chat_name TEXT,
                message_id TEXT,
                message_text TEXT,
                parsed_data TEXT,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                retry_count INTEGER DEFAULT 0,
                error_code TEXT,
                error_message TEXT,
                flood_wait INTEGER,
                processing_time_ms INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE INDEX IF NOT EXISTS idx_telegram_audit_chat_id ON telegram_audit(chat_id);
            CREATE INDEX IF NOT EXISTS idx_telegram_audit_status ON telegram_audit(status);
            CREATE INDEX IF NOT EXISTS idx_telegram_audit_created_at ON telegram_audit(created_at);
            
            CREATE TABLE IF NOT EXISTS error_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                source TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT,
                stack_trace TEXT,
                market_id TEXT,
                bet_id TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE INDEX IF NOT EXISTS idx_error_log_level ON error_log(level);
            CREATE INDEX IF NOT EXISTS idx_error_log_source ON error_log(source);
            CREATE INDEX IF NOT EXISTS idx_error_log_created_at ON error_log(created_at);
            
            CREATE TABLE IF NOT EXISTS cashout_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bet_id TEXT NOT NULL,
                market_id TEXT NOT NULL,
                selection_id TEXT,
                cashout_type TEXT NOT NULL,
                requested_profit REAL,
                actual_profit REAL,
                lay_stake REAL,
                lay_price REAL,
                status TEXT NOT NULL,
                error_code TEXT,
                error_message TEXT,
                requested_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME
            );
            
            CREATE INDEX IF NOT EXISTS idx_cashout_bet_id ON cashout_history(bet_id);
            CREATE INDEX IF NOT EXISTS idx_cashout_status ON cashout_history(status);
            
            CREATE TABLE IF NOT EXISTS daily_pnl (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE NOT NULL,
                total_bets INTEGER DEFAULT 0,
                won_bets INTEGER DEFAULT 0,
                lost_bets INTEGER DEFAULT 0,
                gross_profit REAL DEFAULT 0,
                gross_loss REAL DEFAULT 0,
                commission_paid REAL DEFAULT 0,
                net_pnl REAL DEFAULT 0,
                telegram_bets INTEGER DEFAULT 0,
                manual_bets INTEGER DEFAULT 0,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS open_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bet_id TEXT UNIQUE NOT NULL,
                market_id TEXT NOT NULL,
                selection_id TEXT NOT NULL,
                side TEXT NOT NULL,
                stake REAL NOT NULL,
                price REAL NOT NULL,
                matched_size REAL DEFAULT 0,
                status TEXT NOT NULL,
                trailing_enabled INTEGER DEFAULT 0,
                trailing_stop_ticks INTEGER,
                max_profit_seen REAL,
                replace_guard_enabled INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE INDEX IF NOT EXISTS idx_open_positions_market ON open_positions(market_id);
            CREATE INDEX IF NOT EXISTS idx_open_positions_status ON open_positions(status);
            """)
        logger.info("[STORAGE] Database tables initialized")
    
    def log_bet_event(
        self,
        market_id,
        selection_id,
        side,
        stake,
        price,
        status,
        bet_id=None,
        market_name=None,
        event_name=None,
        runner_name=None,
        matched_size=0.0,
        avg_price_matched=None,
        profit=None,
        liability=None,
        commission=None,
        error_code=None,
        error_message=None,
        source='MANUAL',
        telegram_chat_id=None,
        telegram_message_id=None
    ):
        """Log a bet event to history."""
        try:
            with self.get_db() as conn:
                now = datetime.utcnow().isoformat()
                
                placed_at = now if status in ('PLACED', 'EXECUTABLE') else None
                matched_at = now if status == 'MATCHED' else None
                settled_at = now if status in ('SETTLED_WIN', 'SETTLED_LOSS', 'CASHED_OUT') else None
                
                conn.execute("""
                INSERT INTO bet_history (
                    bet_id, market_id, market_name, event_name,
                    selection_id, runner_name, side, stake, price,
                    matched_size, avg_price_matched, status,
                    profit, liability, commission,
                    error_code, error_message,
                    source, telegram_chat_id, telegram_message_id,
                    placed_at, matched_at, settled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    bet_id, market_id, market_name, event_name,
                    selection_id, runner_name, side, stake, price,
                    matched_size, avg_price_matched, status,
                    profit, liability, commission,
                    error_code, error_message,
                    source, telegram_chat_id, telegram_message_id,
                    placed_at, matched_at, settled_at
                ))
            logger.debug(f"[STORAGE] Logged bet event: {status} {side} {stake}@{price} on {market_id}")
            return True
        except Exception as e:
            logger.error(f"[STORAGE] Failed to log bet: {e}")
            return False
    
    def update_bet_status(self, bet_id, status, profit=None, matched_size=None, error_code=None, error_message=None):
        """Update existing bet status."""
        try:
            with self.get_db() as conn:
                now = datetime.utcnow().isoformat()
                
                updates = ["status = ?", "updated_at = ?"]
                values = [status, now]
                
                if profit is not None:
                    updates.append("profit = ?")
                    values.append(profit)
                
                if matched_size is not None:
                    updates.append("matched_size = ?")
                    values.append(matched_size)
                
                if error_code:
                    updates.append("error_code = ?")
                    values.append(error_code)
                
                if error_message:
                    updates.append("error_message = ?")
                    values.append(error_message)
                
                if status == 'MATCHED':
                    updates.append("matched_at = ?")
                    values.append(now)
                elif status in ('SETTLED_WIN', 'SETTLED_LOSS', 'CASHED_OUT'):
                    updates.append("settled_at = ?")
                    values.append(now)
                
                values.append(bet_id)
                
                conn.execute(f"""
                UPDATE bet_history SET {', '.join(updates)}
                WHERE bet_id = ? AND id = (
                    SELECT MAX(id) FROM bet_history WHERE bet_id = ?
                )
                """, values + [bet_id])
            
            logger.debug(f"[STORAGE] Updated bet {bet_id} to {status}")
            return True
        except Exception as e:
            logger.error(f"[STORAGE] Failed to update bet: {e}")
            return False
    
    def log_cashout_event(
        self,
        bet_id,
        market_id,
        cashout_type,
        status,
        selection_id=None,
        requested_profit=None,
        actual_profit=None,
        lay_stake=None,
        lay_price=None,
        error_code=None,
        error_message=None
    ):
        """Log a cashout event."""
        try:
            with self.get_db() as conn:
                conn.execute("""
                INSERT INTO cashout_history (
                    bet_id, market_id, selection_id, cashout_type,
                    requested_profit, actual_profit, lay_stake, lay_price,
                    status, error_code, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    bet_id, market_id, selection_id, cashout_type,
                    requested_profit, actual_profit, lay_stake, lay_price,
                    status, error_code, error_message
                ))
            logger.debug(f"[STORAGE] Logged cashout: {bet_id} {status}")
            return True
        except Exception as e:
            logger.error(f"[STORAGE] Failed to log cashout: {e}")
            return False
    
    def update_cashout_status(self, bet_id, status, actual_profit=None, error_code=None, error_message=None):
        """Update cashout status."""
        try:
            with self.get_db() as conn:
                now = datetime.utcnow().isoformat()
                completed_at = now if status in ('COMPLETED', 'CASHED_OUT') else None
                
                conn.execute("""
                UPDATE cashout_history SET 
                    status = ?,
                    actual_profit = COALESCE(?, actual_profit),
                    error_code = COALESCE(?, error_code),
                    error_message = COALESCE(?, error_message),
                    completed_at = COALESCE(?, completed_at)
                WHERE bet_id = ? AND id = (
                    SELECT MAX(id) FROM cashout_history WHERE bet_id = ?
                )
                """, (status, actual_profit, error_code, error_message, completed_at, bet_id, bet_id))
            return True
        except Exception as e:
            logger.error(f"[STORAGE] Failed to update cashout: {e}")
            return False
    
    def log_telegram_event(
        self,
        chat_id,
        action,
        status,
        bet_id=None,
        chat_name=None,
        message_id=None,
        message_text=None,
        parsed_data=None,
        retry_count=0,
        error_code=None,
        error_message=None,
        flood_wait=None,
        processing_time_ms=None
    ):
        """Log a Telegram event."""
        try:
            with self.get_db() as conn:
                conn.execute("""
                INSERT INTO telegram_audit (
                    bet_id, chat_id, chat_name, message_id,
                    message_text, parsed_data, action, status,
                    retry_count, error_code, error_message,
                    flood_wait, processing_time_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    bet_id, chat_id, chat_name, message_id,
                    message_text, parsed_data, action, status,
                    retry_count, error_code, error_message,
                    flood_wait, processing_time_ms
                ))
            logger.debug(f"[STORAGE] Logged Telegram event: {action} {status}")
            return True
        except Exception as e:
            logger.error(f"[STORAGE] Failed to log Telegram event: {e}")
            return False
    
    def log_error(self, level, source, message, details=None, stack_trace=None, market_id=None, bet_id=None):
        """Log an error event."""
        try:
            with self.get_db() as conn:
                conn.execute("""
                INSERT INTO error_log (level, source, message, details, stack_trace, market_id, bet_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (level, source, message, details, stack_trace, market_id, bet_id))
            return True
        except Exception as e:
            logger.error(f"[STORAGE] Failed to log error: {e}")
            return False
    
    def save_open_position(
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
        """Save or update an open position for recovery."""
        try:
            with self.get_db() as conn:
                now = datetime.utcnow().isoformat()
                conn.execute("""
                INSERT OR REPLACE INTO open_positions (
                    bet_id, market_id, selection_id, side,
                    stake, price, matched_size, status,
                    trailing_enabled, trailing_stop_ticks, max_profit_seen,
                    replace_guard_enabled, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    bet_id, market_id, selection_id, side,
                    stake, price, matched_size, status,
                    1 if trailing_enabled else 0, trailing_stop_ticks, max_profit_seen,
                    1 if replace_guard_enabled else 0, now
                ))
            return True
        except Exception as e:
            logger.error(f"[STORAGE] Failed to save position: {e}")
            return False
    
    def remove_open_position(self, bet_id):
        """Remove a closed position."""
        try:
            with self.get_db() as conn:
                conn.execute("DELETE FROM open_positions WHERE bet_id = ?", (bet_id,))
            return True
        except Exception as e:
            logger.error(f"[STORAGE] Failed to remove position: {e}")
            return False
    
    def recover_open_positions(self):
        """Recover all open positions for restart recovery."""
        try:
            with self.get_db() as conn:
                rows = conn.execute("""
                SELECT * FROM open_positions
                WHERE status IN ('PLACED', 'EXECUTABLE', 'PARTIAL', 'MATCHED', 'CASHOUT_REQUESTED')
                ORDER BY created_at
                """).fetchall()
            
            positions = []
            for row in rows:
                positions.append({
                    'bet_id': row['bet_id'],
                    'market_id': row['market_id'],
                    'selection_id': row['selection_id'],
                    'side': row['side'],
                    'stake': row['stake'],
                    'price': row['price'],
                    'matched_size': row['matched_size'],
                    'status': row['status'],
                    'trailing_enabled': bool(row['trailing_enabled']),
                    'trailing_stop_ticks': row['trailing_stop_ticks'],
                    'max_profit_seen': row['max_profit_seen'],
                    'replace_guard_enabled': bool(row['replace_guard_enabled'])
                })
            
            logger.info(f"[STORAGE] Recovered {len(positions)} open positions")
            return positions
        except Exception as e:
            logger.error(f"[STORAGE] Failed to recover positions: {e}")
            return []
    
    def get_equity_curve(self, days=30):
        """Get equity curve data for chart."""
        try:
            with self.get_db() as conn:
                cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
                rows = conn.execute("""
                SELECT created_at, profit
                FROM bet_history
                WHERE status IN ('CASHED_OUT', 'SETTLED_WIN', 'SETTLED_LOSS')
                AND created_at >= ?
                AND profit IS NOT NULL
                ORDER BY created_at
                """, (cutoff,)).fetchall()
            
            equity = 0
            curve = []
            for row in rows:
                equity += row['profit']
                curve.append({
                    'timestamp': row['created_at'],
                    'equity': round(equity, 2)
                })
            return curve
        except Exception as e:
            logger.error(f"[STORAGE] Failed to get equity curve: {e}")
            return []
    
    def get_dashboard_kpis(self, days=None):
        """Get KPI metrics for dashboard."""
        try:
            with self.get_db() as conn:
                where_clause = "WHERE status IN ('CASHED_OUT', 'SETTLED_WIN', 'SETTLED_LOSS')"
                params = []
                
                if days:
                    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
                    where_clause += " AND created_at >= ?"
                    params.append(cutoff)
                
                row = conn.execute(f"""
                SELECT
                    COUNT(*) as total_bets,
                    ROUND(COALESCE(SUM(profit), 0), 2) as total_pnl,
                    ROUND(COALESCE(AVG(profit), 0), 2) as avg_profit,
                    ROUND(100.0 * SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2) as winrate,
                    ROUND(COALESCE(SUM(CASE WHEN profit > 0 THEN profit ELSE 0 END), 0), 2) as gross_win,
                    ROUND(COALESCE(SUM(CASE WHEN profit < 0 THEN profit ELSE 0 END), 0), 2) as gross_loss,
                    ROUND(COALESCE(SUM(commission), 0), 2) as total_commission
                FROM bet_history
                {where_clause}
                """, params).fetchone()
            
            return {
                'total_bets': row['total_bets'] or 0,
                'total_pnl': row['total_pnl'] or 0,
                'avg_profit': row['avg_profit'] or 0,
                'winrate': row['winrate'] or 0,
                'gross_win': row['gross_win'] or 0,
                'gross_loss': row['gross_loss'] or 0,
                'total_commission': row['total_commission'] or 0
            }
        except Exception as e:
            logger.error(f"[STORAGE] Failed to get KPIs: {e}")
            return {}
    
    def get_daily_pnl(self, days=30):
        """Get daily P&L breakdown."""
        try:
            with self.get_db() as conn:
                cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
                rows = conn.execute("""
                SELECT
                    DATE(created_at) as day,
                    COUNT(*) as bets,
                    ROUND(SUM(profit), 2) as total_pnl,
                    SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) as won,
                    SUM(CASE WHEN profit < 0 THEN 1 ELSE 0 END) as lost,
                    ROUND(SUM(CASE WHEN profit > 0 THEN profit ELSE 0 END), 2) as gross_win,
                    ROUND(SUM(CASE WHEN profit < 0 THEN profit ELSE 0 END), 2) as gross_loss
                FROM bet_history
                WHERE status IN ('CASHED_OUT', 'SETTLED_WIN', 'SETTLED_LOSS')
                AND created_at >= ?
                AND profit IS NOT NULL
                GROUP BY DATE(created_at)
                ORDER BY day DESC
                """, (cutoff,)).fetchall()
            
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"[STORAGE] Failed to get daily P&L: {e}")
            return []
    
    def get_bet_history(self, limit=100, offset=0, status_filter=None, source_filter=None):
        """Get bet history with filters."""
        try:
            with self.get_db() as conn:
                where_clauses = []
                params = []
                
                if status_filter:
                    where_clauses.append("status = ?")
                    params.append(status_filter)
                
                if source_filter:
                    where_clauses.append("source = ?")
                    params.append(source_filter)
                
                where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
                
                rows = conn.execute(f"""
                SELECT * FROM bet_history
                {where_sql}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """, params + [limit, offset]).fetchall()
            
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"[STORAGE] Failed to get bet history: {e}")
            return []
    
    def get_telegram_history(self, limit=200, chat_id=None, status_filter=None):
        """Get Telegram audit history."""
        try:
            with self.get_db() as conn:
                where_clauses = []
                params = []
                
                if chat_id:
                    where_clauses.append("chat_id = ?")
                    params.append(chat_id)
                
                if status_filter:
                    where_clauses.append("status = ?")
                    params.append(status_filter)
                
                where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
                
                rows = conn.execute(f"""
                SELECT * FROM telegram_audit
                {where_sql}
                ORDER BY created_at DESC
                LIMIT ?
                """, params + [limit]).fetchall()
            
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"[STORAGE] Failed to get Telegram history: {e}")
            return []
    
    def get_telegram_metrics(self):
        """Get Telegram delivery metrics."""
        try:
            with self.get_db() as conn:
                row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'SENT' THEN 1 ELSE 0 END) as sent,
                    SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed,
                    SUM(CASE WHEN status = 'PROCESSED' THEN 1 ELSE 0 END) as processed,
                    AVG(processing_time_ms) as avg_processing_time
                FROM telegram_audit
                """).fetchone()
            
            total = row['total'] or 0
            sent = row['sent'] or 0
            failed = row['failed'] or 0
            
            return {
                'total': total,
                'sent': sent,
                'failed': failed,
                'processed': row['processed'] or 0,
                'delivery_rate': round(100 * sent / total, 2) if total > 0 else 0,
                'failure_rate': round(100 * failed / total, 2) if total > 0 else 0,
                'avg_processing_time_ms': round(row['avg_processing_time'] or 0, 2)
            }
        except Exception as e:
            logger.error(f"[STORAGE] Failed to get Telegram metrics: {e}")
            return {}
    
    def get_recent_errors(self, limit=100, level=None, source=None):
        """Get recent error logs."""
        try:
            with self.get_db() as conn:
                where_clauses = []
                params = []
                
                if level:
                    where_clauses.append("level = ?")
                    params.append(level)
                
                if source:
                    where_clauses.append("source = ?")
                    params.append(source)
                
                where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
                
                rows = conn.execute(f"""
                SELECT * FROM error_log
                {where_sql}
                ORDER BY created_at DESC
                LIMIT ?
                """, params + [limit]).fetchall()
            
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"[STORAGE] Failed to get errors: {e}")
            return []
    
    def get_cashout_history(self, limit=100, status_filter=None):
        """Get cashout history."""
        try:
            with self.get_db() as conn:
                where_sql = "WHERE status = ?" if status_filter else ""
                params = [status_filter] if status_filter else []
                
                rows = conn.execute(f"""
                SELECT * FROM cashout_history
                {where_sql}
                ORDER BY requested_at DESC
                LIMIT ?
                """, params + [limit]).fetchall()
            
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"[STORAGE] Failed to get cashout history: {e}")
            return []
    
    def cleanup_old_data(self, days_to_keep=90):
        """Clean up old data to manage database size."""
        try:
            cutoff = (datetime.utcnow() - timedelta(days=days_to_keep)).isoformat()
            with self.get_db() as conn:
                conn.execute("DELETE FROM error_log WHERE created_at < ?", (cutoff,))
                conn.execute("DELETE FROM telegram_audit WHERE created_at < ?", (cutoff,))
            logger.info(f"[STORAGE] Cleaned up data older than {days_to_keep} days")
            return True
        except Exception as e:
            logger.error(f"[STORAGE] Failed to cleanup: {e}")
            return False
    
    def rebuild_daily_pnl(self, days=30):
        """Rebuild daily P&L table from bet_history."""
        try:
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            with self.get_db() as conn:
                rows = conn.execute("""
                SELECT
                    DATE(created_at) as day,
                    COUNT(*) as total_bets,
                    SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) as won_bets,
                    SUM(CASE WHEN profit < 0 THEN 1 ELSE 0 END) as lost_bets,
                    COALESCE(SUM(CASE WHEN profit > 0 THEN profit ELSE 0 END), 0) as gross_profit,
                    COALESCE(SUM(CASE WHEN profit < 0 THEN profit ELSE 0 END), 0) as gross_loss,
                    COALESCE(SUM(commission), 0) as commission_paid,
                    COALESCE(SUM(profit), 0) as net_pnl,
                    SUM(CASE WHEN source = 'TELEGRAM' THEN 1 ELSE 0 END) as telegram_bets,
                    SUM(CASE WHEN source = 'MANUAL' OR source = 'DUTCHING' THEN 1 ELSE 0 END) as manual_bets
                FROM bet_history
                WHERE status IN ('CASHED_OUT', 'SETTLED_WIN', 'SETTLED_LOSS')
                AND created_at >= ?
                GROUP BY DATE(created_at)
                """, (cutoff,)).fetchall()
                
                now = datetime.utcnow().isoformat()
                for row in rows:
                    conn.execute("""
                    INSERT OR REPLACE INTO daily_pnl (
                        date, total_bets, won_bets, lost_bets,
                        gross_profit, gross_loss, commission_paid, net_pnl,
                        telegram_bets, manual_bets, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        row['day'], row['total_bets'], row['won_bets'], row['lost_bets'],
                        row['gross_profit'], row['gross_loss'], row['commission_paid'], row['net_pnl'],
                        row['telegram_bets'], row['manual_bets'], now
                    ))
            
            logger.info(f"[STORAGE] Rebuilt daily P&L for {len(rows)} days")
            return True
        except Exception as e:
            logger.error(f"[STORAGE] Failed to rebuild daily P&L: {e}")
            return False
    
    def close_connection(self):
        """Close thread-local connection."""
        if hasattr(self._local, 'conn') and self._local.conn:
            try:
                self._local.conn.close()
                self._local.conn = None
            except:
                pass


_storage_instance = None

def get_persistent_storage(db_path=None):
    """Get singleton instance of persistent storage."""
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = PersistentStorage(db_path)
    return _storage_instance
