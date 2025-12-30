"""
Database layer using SQLite for local storage.
Stores Betfair credentials, certificates, and bet history.
"""

import sqlite3
import os
import json
import threading
from datetime import datetime
from pathlib import Path

# Thread lock for database access
_db_lock = threading.Lock()

def get_db_path():
    """Get database path in user's app data directory."""
    if os.name == 'nt':  # Windows
        app_data = os.environ.get('APPDATA', os.path.expanduser('~'))
        db_dir = os.path.join(app_data, 'Pickfair')
    else:  # Linux/Mac
        db_dir = os.path.join(os.path.expanduser('~'), '.pickfair')
    
    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, 'betfair.db')

class Database:
    def __init__(self):
        self.db_path = get_db_path()
        self._lock = threading.RLock()
        self._conn = None
        self._cleanup_stale_files()
        self._init_connection()
        self._init_db()
    
    def _cleanup_stale_files(self):
        """Remove stale WAL/SHM files if database appears locked."""
        import time
        wal_file = self.db_path + "-wal"
        shm_file = self.db_path + "-shm"
        
        # Try to open database briefly to check if locked
        try:
            test_conn = sqlite3.connect(self.db_path, timeout=2.0)
            test_conn.execute("SELECT 1")
            # Run integrity check
            result = test_conn.execute("PRAGMA integrity_check").fetchone()
            if result and result[0] != 'ok':
                print(f"[WARNING] Database integrity issue: {result[0]}")
            test_conn.close()
        except sqlite3.OperationalError:
            # Database might be locked, try to remove stale files
            print("[WARNING] Database locked, cleaning up stale files...")
            for f in [wal_file, shm_file]:
                if os.path.exists(f):
                    try:
                        os.remove(f)
                        print(f"[INFO] Removed stale file: {f}")
                    except Exception as e:
                        print(f"[WARNING] Could not remove {f}: {e}")
            time.sleep(0.5)
        except sqlite3.DatabaseError as e:
            # Database might be corrupted
            print(f"[ERROR] Database error: {e}")
            self._backup_and_recreate()
    
    def _backup_and_recreate(self):
        """Backup corrupted database and create fresh one."""
        import shutil
        backup_path = self.db_path + ".backup." + datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            if os.path.exists(self.db_path):
                shutil.copy2(self.db_path, backup_path)
                print(f"[INFO] Backed up corrupted database to: {backup_path}")
                os.remove(self.db_path)
        except Exception as e:
            print(f"[WARNING] Could not backup database: {e}")
    
    def _init_connection(self):
        """Initialize persistent database connection."""
        self._conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row  # Set once for all queries
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
    
    def _get_connection(self):
        """Get the persistent database connection."""
        if self._conn is None:
            self._init_connection()
        return self._conn
    
    def _execute(self, func):
        """Execute database operation with lock protection."""
        import time
        import logging
        max_retries = 5
        for attempt in range(max_retries):
            try:
                with self._lock:
                    return func(self._get_connection())
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < max_retries - 1:
                    logging.warning(f"Database locked, retry {attempt+1}/{max_retries}")
                    time.sleep(0.3 * (attempt + 1))
                    continue
                logging.error(f"Database lock error after {max_retries} retries: {e}")
                raise
    
    def _execute_with_retry(self, func, max_retries=5):
        """Legacy: Execute database operation with retry on lock."""
        import time
        import logging
        for attempt in range(max_retries):
            try:
                with self._lock:
                    return func()
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < max_retries - 1:
                    logging.warning(f"Database locked, retry {attempt+1}/{max_retries}")
                    time.sleep(0.3 * (attempt + 1))
                    continue
                logging.error(f"Database lock error after {max_retries} retries: {e}")
                raise
    
    def close(self):
        """Close the database connection."""
        if self._conn:
            try:
                self._conn.close()
            except:
                pass
            self._conn = None
    
    def _init_db(self):
        """Initialize database tables."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY,
                username TEXT,
                app_key TEXT,
                certificate TEXT,
                private_key TEXT,
                password TEXT,
                session_token TEXT,
                session_expiry TEXT
            )
        ''')
        
        # Add password column if it doesn't exist (for existing databases)
        try:
            cursor.execute('ALTER TABLE settings ADD COLUMN password TEXT')
        except sqlite3.OperationalError:
            pass  # Column already exists
        
        # Add update_url column for auto-updates
        try:
            cursor.execute('ALTER TABLE settings ADD COLUMN update_url TEXT')
        except sqlite3.OperationalError:
            pass  # Column already exists
        
        # Add skipped_version column for skipped updates
        try:
            cursor.execute('ALTER TABLE settings ADD COLUMN skipped_version TEXT')
        except sqlite3.OperationalError:
            pass  # Column already exists
        
        # Add auto_update column for auto-update setting
        try:
            cursor.execute('ALTER TABLE settings ADD COLUMN auto_update INTEGER DEFAULT 1')
        except sqlite3.OperationalError:
            pass  # Column already exists
        
        # Add auto_stake column for telegram auto-betting
        try:
            cursor.execute('ALTER TABLE telegram_settings ADD COLUMN auto_stake REAL DEFAULT 1.0')
        except sqlite3.OperationalError:
            pass  # Column already exists or table doesn't exist yet
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bet_id TEXT,
                event_name TEXT,
                market_id TEXT,
                market_name TEXT,
                selection_id INTEGER,
                runner_name TEXT,
                bet_type TEXT,
                side TEXT,
                price REAL,
                stake REAL,
                liability REAL,
                matched_stake REAL DEFAULT 0,
                unmatched_stake REAL DEFAULT 0,
                average_price_matched REAL,
                potential_profit REAL,
                status TEXT,
                placed_at TEXT,
                settled_at TEXT,
                profit_loss REAL,
                outcome TEXT
            )
        ''')
        
        # Add outcome column for bet settlement (WON/LOST/VOID)
        try:
            cursor.execute('ALTER TABLE bets ADD COLUMN outcome TEXT')
        except sqlite3.OperationalError:
            pass  # Column already exists
        
        # Add selections column for dutching bets
        try:
            cursor.execute('ALTER TABLE bets ADD COLUMN selections TEXT')
        except sqlite3.OperationalError:
            pass  # Column already exists
        
        # Add total_stake column for dutching bets
        try:
            cursor.execute('ALTER TABLE bets ADD COLUMN total_stake REAL')
        except sqlite3.OperationalError:
            pass  # Column already exists
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_name TEXT,
                market_id TEXT,
                market_name TEXT,
                selection_id INTEGER,
                runner_name TEXT,
                side TEXT,
                target_price REAL,
                stake REAL,
                current_price REAL,
                status TEXT DEFAULT 'PENDING',
                created_at TEXT,
                triggered_at TEXT,
                bet_id TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS auto_cashout_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT,
                bet_id TEXT,
                profit_target REAL,
                loss_limit REAL,
                status TEXT DEFAULT 'ACTIVE',
                created_at TEXT,
                triggered_at TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cashout_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT,
                selection_id INTEGER,
                original_bet_id TEXT,
                cashout_bet_id TEXT,
                original_side TEXT,
                original_stake REAL,
                original_price REAL,
                cashout_side TEXT,
                cashout_stake REAL,
                cashout_price REAL,
                profit_loss REAL,
                executed_at TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS telegram_settings (
                id INTEGER PRIMARY KEY,
                api_id TEXT,
                api_hash TEXT,
                session_string TEXT,
                phone_number TEXT,
                enabled INTEGER DEFAULT 0,
                auto_bet INTEGER DEFAULT 0,
                require_confirmation INTEGER DEFAULT 1,
                auto_stake REAL DEFAULT 1.0,
                auto_start_listener INTEGER DEFAULT 0,
                auto_stop_listener INTEGER DEFAULT 1,
                copy_mode TEXT DEFAULT 'OFF',
                copy_chat_id TEXT
            )
        ''')
        
        # Add new columns if they don't exist (for existing databases)
        try:
            cursor.execute('ALTER TABLE telegram_settings ADD COLUMN auto_start_listener INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE telegram_settings ADD COLUMN auto_stop_listener INTEGER DEFAULT 1')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE telegram_settings ADD COLUMN copy_mode TEXT DEFAULT 'OFF'")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE telegram_settings ADD COLUMN copy_chat_id TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE telegram_settings ADD COLUMN stake_type TEXT DEFAULT 'fixed'")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE telegram_settings ADD COLUMN stake_percent REAL DEFAULT 1.0')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE telegram_settings ADD COLUMN dutching_enabled INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute('ALTER TABLE telegram_settings ADD COLUMN reply_100_master INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS telegram_chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT UNIQUE,
                chat_name TEXT,
                enabled INTEGER DEFAULT 1,
                added_at TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS telegram_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT,
                sender_id TEXT,
                raw_text TEXT,
                parsed_side TEXT,
                parsed_selection TEXT,
                parsed_odds REAL,
                parsed_stake REAL,
                status TEXT DEFAULT 'PENDING',
                bet_id TEXT,
                received_at TEXT,
                processed_at TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS signal_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                pattern TEXT NOT NULL,
                market_type TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                is_default INTEGER DEFAULT 0,
                created_at TEXT,
                bet_side TEXT DEFAULT 'BACK',
                live_only INTEGER DEFAULT 0
            )
        ''')
        
        # Add bet_side column if it doesn't exist
        try:
            cursor.execute('ALTER TABLE signal_patterns ADD COLUMN bet_side TEXT DEFAULT "BACK"')
        except sqlite3.OperationalError:
            pass
        
        # Add live_only column if it doesn't exist
        try:
            cursor.execute('ALTER TABLE signal_patterns ADD COLUMN live_only INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass
        
        # Simulation mode tables
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS simulation_settings (
                id INTEGER PRIMARY KEY,
                virtual_balance REAL DEFAULT 1000.0,
                starting_balance REAL DEFAULT 1000.0,
                total_bets INTEGER DEFAULT 0,
                total_won INTEGER DEFAULT 0,
                total_lost INTEGER DEFAULT 0,
                total_profit_loss REAL DEFAULT 0.0,
                created_at TEXT,
                last_reset TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS simulation_bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_name TEXT,
                market_id TEXT,
                market_name TEXT,
                side TEXT,
                selections TEXT,
                total_stake REAL,
                potential_profit REAL,
                status TEXT DEFAULT 'MATCHED',
                placed_at TEXT,
                settled_at TEXT,
                profit_loss REAL,
                result TEXT
            )
        ''')
        
        cursor.execute('SELECT COUNT(*) FROM simulation_settings')
        if cursor.fetchone()[0] == 0:
            cursor.execute('''
                INSERT INTO simulation_settings (id, virtual_balance, starting_balance, created_at) 
                VALUES (1, 1000.0, 1000.0, ?)
            ''', (datetime.now().isoformat(),))
        
        cursor.execute('SELECT COUNT(*) FROM settings')
        if cursor.fetchone()[0] == 0:
            cursor.execute('INSERT INTO settings (id) VALUES (1)')
        
        cursor.execute('SELECT COUNT(*) FROM telegram_settings')
        if cursor.fetchone()[0] == 0:
            cursor.execute('INSERT INTO telegram_settings (id) VALUES (1)')
        
        # Telegram audit table for Copy Trading broadcast tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS telegram_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedup_key TEXT,
                chat_id TEXT NOT NULL,
                telegram_msg_id INTEGER,
                payload TEXT,
                status TEXT CHECK(status IN ('QUEUED','SENT','FAILED','ACKED')) DEFAULT 'QUEUED',
                attempts INTEGER DEFAULT 0,
                error_code TEXT,
                error_message TEXT,
                flood_wait_seconds INTEGER,
                queued_at TEXT NOT NULL,
                sent_at TEXT,
                failed_at TEXT,
                acked_at TEXT
            )
        ''')
        
        # Indexes for performance
        try:
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_telegram_audit_status ON telegram_audit(status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_telegram_audit_dedup ON telegram_audit(dedup_key)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_telegram_audit_msgid ON telegram_audit(telegram_msg_id)')
        except sqlite3.OperationalError:
            pass
        
        conn.commit()
        # conn.close() - using persistent connection
    
    def get_settings(self):
        """Get Betfair settings. Strips whitespace from string values."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM settings WHERE id = 1')
        row = cursor.fetchone()
        # conn.close() - using persistent connection
        if row:
            settings = dict(row)
            for key in ['username', 'app_key', 'certificate', 'private_key']:
                if settings.get(key) and isinstance(settings[key], str):
                    settings[key] = settings[key].strip()
            return settings
        return None
    
    def save_credentials(self, username, app_key, certificate, private_key):
        """Save Betfair credentials. Strips whitespace from username and app_key."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE settings SET 
                username = ?, app_key = ?, certificate = ?, private_key = ?
            WHERE id = 1
        ''', (
            username.strip() if username else username,
            app_key.strip() if app_key else app_key,
            certificate.strip() if certificate else certificate,
            private_key.strip() if private_key else private_key
        ))
        conn.commit()
        # conn.close() - using persistent connection
    
    def save_session(self, session_token, session_expiry):
        """Save session token."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE settings SET session_token = ?, session_expiry = ?
            WHERE id = 1
        ''', (session_token, session_expiry))
        conn.commit()
        # conn.close() - using persistent connection
    
    def clear_session(self):
        """Clear session token."""
        self.save_session(None, None)
    
    def save_password(self, password):
        """Save or clear password."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE settings SET password = ? WHERE id = 1', (password,))
        conn.commit()
        # conn.close() - using persistent connection
    
    def save_update_url(self, update_url):
        """Save GitHub releases URL for auto-updates."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE settings SET update_url = ? WHERE id = 1', (update_url,))
        conn.commit()
        # conn.close() - using persistent connection
    
    def save_skipped_version(self, version):
        """Save version that user chose to skip."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE settings SET skipped_version = ? WHERE id = 1', (version,))
        conn.commit()
        # conn.close() - using persistent connection
    
    def save_bet(self, event_name, market_id, market_name, bet_type, 
                 selections, total_stake, potential_profit, status):
        """Save bet to history."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO bets 
            (event_name, market_id, market_name, bet_type, selections, 
             total_stake, potential_profit, status, placed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            event_name, market_id, market_name, bet_type,
            json.dumps(selections), total_stake, potential_profit, 
            status, datetime.now().isoformat()
        ))
        conn.commit()
        # conn.close() - using persistent connection
    
    def get_recent_bets(self, limit=50):
        """Get recent bets."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM bets ORDER BY placed_at DESC LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        # conn.close() - using persistent connection
        return [dict(row) for row in rows]
    
    def save_bet_order(self, bet_id, event_name, market_id, market_name, selection_id,
                       runner_name, side, price, stake, liability, status, matched_stake=0,
                       unmatched_stake=0, average_price=None, potential_profit=None):
        """Save a bet order."""
        import logging
        logging.info(f"save_bet_order called: bet_id={bet_id}, event={event_name}, stake={stake}")
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO bets 
            (bet_id, event_name, market_id, market_name, selection_id, runner_name,
             side, price, stake, liability, matched_stake, unmatched_stake,
             average_price_matched, potential_profit, status, placed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            bet_id, event_name, market_id, market_name, selection_id, runner_name,
            side, price, stake, liability, matched_stake, unmatched_stake,
            average_price, potential_profit, status, datetime.now().isoformat()
        ))
        conn.commit()
        # conn.close() - using persistent connection
    
    def update_bet_status(self, bet_id, status, matched_stake=None, unmatched_stake=None,
                          profit_loss=None, settled_at=None):
        """Update bet status."""
        conn = self._get_connection()
        cursor = conn.cursor()
        updates = ['status = ?']
        params = [status]
        if matched_stake is not None:
            updates.append('matched_stake = ?')
            params.append(matched_stake)
        if unmatched_stake is not None:
            updates.append('unmatched_stake = ?')
            params.append(unmatched_stake)
        if profit_loss is not None:
            updates.append('profit_loss = ?')
            params.append(profit_loss)
        if settled_at is not None:
            updates.append('settled_at = ?')
            params.append(settled_at)
        params.append(bet_id)
        cursor.execute(f'''
            UPDATE bets SET {', '.join(updates)} WHERE bet_id = ?
        ''', params)
        conn.commit()
        # conn.close() - using persistent connection
    
    def get_bets_by_status(self, status_list, limit=50):
        """Get bets by status list."""
        conn = self._get_connection()
        cursor = conn.cursor()
        placeholders = ','.join(['?' for _ in status_list])
        cursor.execute(f'''
            SELECT * FROM bets WHERE status IN ({placeholders})
            ORDER BY placed_at DESC LIMIT ?
        ''', status_list + [limit])
        rows = cursor.fetchall()
        # conn.close() - using persistent connection
        return [dict(row) for row in rows]
    
    def update_bet_outcome(self, bet_id, outcome, profit_loss=None, settled_at=None):
        """Update bet outcome after settlement (WON/LOST/VOID)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        updates = ['outcome = ?', 'status = ?']
        params = [outcome, 'SETTLED']
        if profit_loss is not None:
            updates.append('profit_loss = ?')
            params.append(profit_loss)
        if settled_at is not None:
            updates.append('settled_at = ?')
            params.append(settled_at)
        params.append(bet_id)
        cursor.execute(f'''
            UPDATE bets SET {', '.join(updates)} WHERE bet_id = ?
        ''', params)
        conn.commit()
        # conn.close() - using persistent connection
    
    def get_unsettled_bets(self, limit=100):
        """Get bets without outcome (not yet settled)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM bets 
            WHERE outcome IS NULL AND bet_id IS NOT NULL
            ORDER BY placed_at DESC LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        # conn.close() - using persistent connection
        return [dict(row) for row in rows]
    
    def get_bet_statistics(self):
        """Get betting statistics (total, won, lost, pending, P/L)."""
        import logging
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Total bets (include all bets, not just those with bet_id)
        cursor.execute('SELECT COUNT(*) FROM bets')
        total = cursor.fetchone()[0] or 0
        logging.debug(f"get_bet_statistics: total={total}")
        
        # Won bets
        cursor.execute("SELECT COUNT(*) FROM bets WHERE outcome = 'WON'")
        won = cursor.fetchone()[0] or 0
        
        # Lost bets
        cursor.execute("SELECT COUNT(*) FROM bets WHERE outcome = 'LOST'")
        lost = cursor.fetchone()[0] or 0
        
        # Void bets
        cursor.execute("SELECT COUNT(*) FROM bets WHERE outcome = 'VOID'")
        void = cursor.fetchone()[0] or 0
        
        # Pending bets (no outcome yet)
        cursor.execute("SELECT COUNT(*) FROM bets WHERE outcome IS NULL")
        pending = cursor.fetchone()[0] or 0
        
        # Total P/L from settled bets
        cursor.execute("SELECT COALESCE(SUM(profit_loss), 0) FROM bets WHERE outcome IS NOT NULL")
        total_pl = cursor.fetchone()[0] or 0.0
        
        # Win rate
        settled = won + lost
        win_rate = (won / settled * 100) if settled > 0 else 0.0
        
        # conn.close() - using persistent connection
        return {
            'total': total,
            'won': won,
            'lost': lost,
            'void': void,
            'pending': pending,
            'total_pl': total_pl,
            'win_rate': win_rate
        }
    
    def get_today_profit_loss(self):
        """Get today's total profit/loss including cashouts."""
        conn = self._get_connection()
        cursor = conn.cursor()
        today = datetime.now().strftime('%Y-%m-%d')
        
        # Get settled bets profit/loss
        cursor.execute('''
            SELECT COALESCE(SUM(profit_loss), 0) FROM bets 
            WHERE status = 'SETTLED' AND DATE(settled_at) = ?
        ''', (today,))
        bets_pl = cursor.fetchone()[0] or 0.0
        
        # Get cashout transactions profit/loss
        cursor.execute('''
            SELECT COALESCE(SUM(profit_loss), 0) FROM cashout_transactions 
            WHERE DATE(executed_at) = ?
        ''', (today,))
        cashout_pl = cursor.fetchone()[0] or 0.0
        
        # conn.close() - using persistent connection
        return bets_pl + cashout_pl
    
    def save_cashout_transaction(self, market_id, selection_id, original_bet_id,
                                  cashout_bet_id, original_side, original_stake,
                                  original_price, cashout_side, cashout_stake,
                                  cashout_price, profit_loss):
        """Save a cashout transaction."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO cashout_transactions 
            (market_id, selection_id, original_bet_id, cashout_bet_id,
             original_side, original_stake, original_price, 
             cashout_side, cashout_stake, cashout_price, profit_loss, executed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            market_id, selection_id, original_bet_id, cashout_bet_id,
            original_side, original_stake, original_price,
            cashout_side, cashout_stake, cashout_price, profit_loss,
            datetime.now().isoformat()
        ))
        conn.commit()
        # conn.close() - using persistent connection
    
    def get_active_bets_count(self):
        """Get count of active/pending bets."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) FROM bets WHERE status IN ('PENDING', 'MATCHED', 'PARTIALLY_MATCHED')
        ''')
        result = cursor.fetchone()[0]
        # conn.close() - using persistent connection
        return result
    
    def save_booking(self, event_name, market_id, market_name, selection_id,
                     runner_name, side, target_price, stake, current_price):
        """Save a bet booking."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO bookings 
            (event_name, market_id, market_name, selection_id, runner_name,
             side, target_price, stake, current_price, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
        ''', (
            event_name, market_id, market_name, selection_id, runner_name,
            side, target_price, stake, current_price, datetime.now().isoformat()
        ))
        booking_id = cursor.lastrowid
        conn.commit()
        # conn.close() - using persistent connection
        return booking_id
    
    def get_pending_bookings(self):
        """Get all pending bookings."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM bookings WHERE status = 'PENDING' ORDER BY created_at DESC
        ''')
        rows = cursor.fetchall()
        # conn.close() - using persistent connection
        return [dict(row) for row in rows]
    
    def update_booking_status(self, booking_id, status, bet_id=None):
        """Update booking status."""
        conn = self._get_connection()
        cursor = conn.cursor()
        if bet_id:
            cursor.execute('''
                UPDATE bookings SET status = ?, triggered_at = ?, bet_id = ? WHERE id = ?
            ''', (status, datetime.now().isoformat(), bet_id, booking_id))
        else:
            cursor.execute('''
                UPDATE bookings SET status = ? WHERE id = ?
            ''', (status, booking_id))
        conn.commit()
        # conn.close() - using persistent connection
    
    def cancel_booking(self, booking_id):
        """Cancel a booking."""
        self.update_booking_status(booking_id, 'CANCELLED')
    
    def save_auto_cashout_rule(self, market_id, bet_id, profit_target, loss_limit):
        """Save auto-cashout rule."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO auto_cashout_rules 
            (market_id, bet_id, profit_target, loss_limit, status, created_at)
            VALUES (?, ?, ?, ?, 'ACTIVE', ?)
        ''', (market_id, bet_id, profit_target, loss_limit, datetime.now().isoformat()))
        rule_id = cursor.lastrowid
        conn.commit()
        # conn.close() - using persistent connection
        return rule_id
    
    def get_active_auto_cashout_rules(self):
        """Get active auto-cashout rules."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM auto_cashout_rules WHERE status = 'ACTIVE'
        ''')
        rows = cursor.fetchall()
        # conn.close() - using persistent connection
        return [dict(row) for row in rows]
    
    def deactivate_auto_cashout_rule(self, rule_id):
        """Deactivate an auto-cashout rule."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE auto_cashout_rules SET status = 'TRIGGERED', triggered_at = ? WHERE id = ?
        ''', (datetime.now().isoformat(), rule_id))
        conn.commit()
        # conn.close() - using persistent connection
    
    def get_telegram_settings(self):
        """Get Telegram settings."""
        def do_get():
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM telegram_settings WHERE id = 1')
            row = cursor.fetchone()
            return dict(row) if row else None
        return self._execute_with_retry(do_get)
    
    def save_telegram_settings(self, api_id, api_hash, session_string=None, 
                                phone_number=None, enabled=False, auto_bet=False,
                                require_confirmation=True, auto_stake=1.0,
                                auto_start_listener=False, auto_stop_listener=True,
                                copy_mode='OFF', copy_chat_id=None,
                                stake_type='fixed', stake_percent=1.0, dutching_enabled=False,
                                reply_100_master=False):
        """Save Telegram settings."""
        def do_save():
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE telegram_settings SET 
                    api_id = ?, api_hash = ?, session_string = ?, phone_number = ?,
                    enabled = ?, auto_bet = ?, require_confirmation = ?, auto_stake = ?,
                    auto_start_listener = ?, auto_stop_listener = ?,
                    copy_mode = ?, copy_chat_id = ?,
                    stake_type = ?, stake_percent = ?, dutching_enabled = ?,
                    reply_100_master = ?
                WHERE id = 1
            ''', (api_id, api_hash, session_string, phone_number,
                  1 if enabled else 0, 1 if auto_bet else 0, 1 if require_confirmation else 0, auto_stake,
                  1 if auto_start_listener else 0, 1 if auto_stop_listener else 0,
                  copy_mode, copy_chat_id,
                  stake_type, stake_percent, 1 if dutching_enabled else 0,
                  1 if reply_100_master else 0))
            conn.commit()
        self._execute_with_retry(do_save)
    
    def save_telegram_session(self, session_string):
        """Save Telegram session string."""
        def do_save():
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute('UPDATE telegram_settings SET session_string = ? WHERE id = 1', (session_string,))
            conn.commit()
        self._execute_with_retry(do_save)
    
    def get_telegram_chats(self):
        """Get monitored Telegram chats."""
        def do_get():
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM telegram_chats ORDER BY added_at DESC')
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        return self._execute_with_retry(do_get)
    
    def add_telegram_chat(self, chat_id, chat_name=''):
        """Add a chat to monitor."""
        def do_add():
            conn = self._get_connection()
            cursor = conn.cursor()
            try:
                cursor.execute('''
                    INSERT INTO telegram_chats (chat_id, chat_name, enabled, added_at)
                    VALUES (?, ?, 1, ?)
                ''', (str(chat_id), chat_name, datetime.now().isoformat()))
                conn.commit()
            except sqlite3.IntegrityError:
                pass
        self._execute_with_retry(do_add)
    
    def remove_telegram_chat(self, chat_id):
        """Remove a chat from monitoring."""
        def do_remove():
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM telegram_chats WHERE chat_id = ?', (str(chat_id),))
            conn.commit()
        self._execute_with_retry(do_remove)
    
    def save_telegram_signal(self, chat_id, sender_id, raw_text, parsed_signal):
        """Save a received Telegram signal."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO telegram_signals 
            (chat_id, sender_id, raw_text, parsed_side, parsed_selection, 
             parsed_odds, parsed_stake, status, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
        ''', (
            str(chat_id), str(sender_id), raw_text,
            parsed_signal.get('side'), parsed_signal.get('selection'),
            parsed_signal.get('odds'), parsed_signal.get('stake'),
            datetime.now().isoformat()
        ))
        signal_id = cursor.lastrowid
        conn.commit()
        # conn.close() - using persistent connection
        return signal_id
    
    def get_pending_signals(self):
        """Get pending Telegram signals."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM telegram_signals WHERE status = 'PENDING' ORDER BY received_at DESC
        ''')
        rows = cursor.fetchall()
        # conn.close() - using persistent connection
        return [dict(row) for row in rows]
    
    def update_signal_status(self, signal_id, status, bet_id=None):
        """Update signal status."""
        conn = self._get_connection()
        cursor = conn.cursor()
        if bet_id:
            cursor.execute('''
                UPDATE telegram_signals SET status = ?, bet_id = ?, processed_at = ? WHERE id = ?
            ''', (status, bet_id, datetime.now().isoformat(), signal_id))
        else:
            cursor.execute('''
                UPDATE telegram_signals SET status = ?, processed_at = ? WHERE id = ?
            ''', (status, datetime.now().isoformat(), signal_id))
        conn.commit()
        # conn.close() - using persistent connection
    
    def get_recent_signals(self, limit=50):
        """Get recent Telegram signals."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM telegram_signals ORDER BY received_at DESC LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        # conn.close() - using persistent connection
        return [dict(row) for row in rows]
    
    # Simulation Mode Methods
    def get_simulation_settings(self):
        """Get simulation settings and balance."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM simulation_settings WHERE id = 1')
        row = cursor.fetchone()
        # conn.close() - using persistent connection
        return dict(row) if row else None
    
    def update_simulation_balance(self, new_balance, bet_result=None):
        """Update virtual balance after a bet."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if bet_result == 'won':
            cursor.execute('''
                UPDATE simulation_settings SET 
                    virtual_balance = ?,
                    total_bets = total_bets + 1,
                    total_won = total_won + 1
                WHERE id = 1
            ''', (new_balance,))
        elif bet_result == 'lost':
            cursor.execute('''
                UPDATE simulation_settings SET 
                    virtual_balance = ?,
                    total_bets = total_bets + 1,
                    total_lost = total_lost + 1
                WHERE id = 1
            ''', (new_balance,))
        else:
            cursor.execute('''
                UPDATE simulation_settings SET virtual_balance = ? WHERE id = 1
            ''', (new_balance,))
        
        conn.commit()
        # conn.close() - using persistent connection
    
    def increment_simulation_bet_count(self, new_balance):
        """Increment total_bets counter and update balance when placing a simulation bet."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE simulation_settings SET 
                virtual_balance = ?,
                total_bets = total_bets + 1
            WHERE id = 1
        ''', (new_balance,))
        conn.commit()
        # conn.close() - using persistent connection
    
    def reset_simulation(self, starting_balance=1000.0):
        """Reset simulation to starting balance."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE simulation_settings SET 
                virtual_balance = ?,
                starting_balance = ?,
                total_bets = 0,
                total_won = 0,
                total_lost = 0,
                total_profit_loss = 0.0,
                last_reset = ?
            WHERE id = 1
        ''', (starting_balance, starting_balance, datetime.now().isoformat()))
        cursor.execute('DELETE FROM simulation_bets')
        conn.commit()
        # conn.close() - using persistent connection
    
    def save_simulation_bet(self, event_name, market_id, market_name, side, 
                            selections, total_stake, potential_profit):
        """Save a simulation bet."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO simulation_bets 
            (event_name, market_id, market_name, side, selections, 
             total_stake, potential_profit, status, placed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'MATCHED', ?)
        ''', (
            event_name, market_id, market_name, side,
            json.dumps(selections) if isinstance(selections, (list, dict)) else str(selections),
            total_stake, potential_profit, datetime.now().isoformat()
        ))
        bet_id = cursor.lastrowid
        conn.commit()
        # conn.close() - using persistent connection
        return bet_id
    
    def get_simulation_bets(self, limit=50):
        """Get simulation bet history."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM simulation_bets ORDER BY placed_at DESC LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        # conn.close() - using persistent connection
        return [dict(row) for row in rows]
    
    def get_simulation_stats(self):
        """Get simulation statistics."""
        settings = self.get_simulation_settings()
        if not settings:
            return None
        
        profit_loss = settings['virtual_balance'] - settings['starting_balance']
        return {
            'virtual_balance': settings['virtual_balance'],
            'starting_balance': settings['starting_balance'],
            'profit_loss': profit_loss,
            'total_bets': settings['total_bets'],
            'total_won': settings['total_won'],
            'total_lost': settings['total_lost'],
            'win_rate': (settings['total_won'] / settings['total_bets'] * 100) if settings['total_bets'] > 0 else 0
        }
    
    def get_auto_update_enabled(self):
        """Get auto-update enabled setting."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT auto_update FROM settings LIMIT 1')
        row = cursor.fetchone()
        # conn.close() - using persistent connection
        return bool(row[0]) if row else True
    
    def set_auto_update_enabled(self, enabled):
        """Set auto-update enabled setting."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM settings')
        if cursor.fetchone()[0] > 0:
            cursor.execute('UPDATE settings SET auto_update = ?', (1 if enabled else 0,))
        else:
            cursor.execute('INSERT INTO settings (auto_update) VALUES (?)', (1 if enabled else 0,))
        conn.commit()
        # conn.close() - using persistent connection
    
    def get_signal_patterns(self, enabled_only=False):
        """Get all signal patterns."""
        conn = self._get_connection()
        cursor = conn.cursor()
        if enabled_only:
            cursor.execute('SELECT * FROM signal_patterns WHERE enabled = 1 ORDER BY name')
        else:
            cursor.execute('SELECT * FROM signal_patterns ORDER BY name')
        rows = cursor.fetchall()
        # conn.close() - using persistent connection
        return [dict(row) for row in rows]
    
    def save_signal_pattern(self, name, description, pattern, market_type, enabled=True, is_default=False, bet_side='BACK', live_only=False):
        """Save a new signal pattern."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO signal_patterns (name, description, pattern, market_type, enabled, is_default, created_at, bet_side, live_only)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (name, description, pattern, market_type, 1 if enabled else 0, 1 if is_default else 0, datetime.now().isoformat(), bet_side, 1 if live_only else 0))
        pattern_id = cursor.lastrowid
        conn.commit()
        # conn.close() - using persistent connection
        return pattern_id
    
    def update_signal_pattern(self, pattern_id, name=None, description=None, pattern=None, market_type=None, enabled=None, bet_side=None, live_only=None):
        """Update an existing signal pattern."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        updates = []
        params = []
        if name is not None:
            updates.append('name = ?')
            params.append(name)
        if description is not None:
            updates.append('description = ?')
            params.append(description)
        if pattern is not None:
            updates.append('pattern = ?')
            params.append(pattern)
        if market_type is not None:
            updates.append('market_type = ?')
            params.append(market_type)
        if enabled is not None:
            updates.append('enabled = ?')
            params.append(1 if enabled else 0)
        if bet_side is not None:
            updates.append('bet_side = ?')
            params.append(bet_side)
        if live_only is not None:
            updates.append('live_only = ?')
            params.append(1 if live_only else 0)
        
        if updates:
            params.append(pattern_id)
            cursor.execute(f'UPDATE signal_patterns SET {", ".join(updates)} WHERE id = ?', params)
            conn.commit()
        # conn.close() - using persistent connection
    
    def delete_signal_pattern(self, pattern_id):
        """Delete a signal pattern."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM signal_patterns WHERE id = ?', (pattern_id,))
        conn.commit()
        # conn.close() - using persistent connection
    
    def toggle_signal_pattern(self, pattern_id, enabled):
        """Toggle signal pattern enabled status."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE signal_patterns SET enabled = ? WHERE id = ?', (1 if enabled else 0, pattern_id))
        conn.commit()
        # conn.close() - using persistent connection
    
    # ==================== TELEGRAM AUDIT METHODS ====================
    
    def insert_telegram_audit(self, chat_id: str, payload: str, dedup_key: str = None) -> int:
        """Insert a new audit record when message is queued.
        
        Returns the audit row ID for tracking.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO telegram_audit (chat_id, payload, dedup_key, status, queued_at)
            VALUES (?, ?, ?, 'QUEUED', ?)
        ''', (chat_id, payload, dedup_key, datetime.now().isoformat()))
        audit_id = cursor.lastrowid
        conn.commit()
        return audit_id
    
    def update_telegram_audit_sent(self, audit_id: int, telegram_msg_id: int = None, attempts: int = 1):
        """Mark audit record as successfully sent."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE telegram_audit 
            SET status = 'SENT', telegram_msg_id = ?, attempts = ?, sent_at = ?,
                error_code = NULL, error_message = NULL
            WHERE id = ?
        ''', (telegram_msg_id, attempts, datetime.now().isoformat(), audit_id))
        conn.commit()
    
    def update_telegram_audit_failed(self, audit_id: int, error_code: str = None, error_message: str = None, attempts: int = 0):
        """Mark audit record as failed after all retries."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE telegram_audit 
            SET status = 'FAILED', error_code = ?, error_message = ?, attempts = ?, failed_at = ?
            WHERE id = ?
        ''', (error_code, error_message, attempts, datetime.now().isoformat(), audit_id))
        conn.commit()
    
    def update_telegram_audit_flood_wait(self, audit_id: int, wait_seconds: int):
        """Record flood wait event on audit record."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE telegram_audit 
            SET flood_wait_seconds = ?
            WHERE id = ?
        ''', (wait_seconds, audit_id))
        conn.commit()
    
    def update_telegram_audit_acked(self, telegram_msg_id: int):
        """Mark audit record as acknowledged by follower.
        
        Returns True if a record was updated, False otherwise.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE telegram_audit 
            SET status = 'ACKED', acked_at = ?
            WHERE telegram_msg_id = ? AND status = 'SENT'
        ''', (datetime.now().isoformat(), telegram_msg_id))
        updated = cursor.rowcount > 0
        conn.commit()
        return updated
    
    def get_telegram_audit_metrics(self, hours: int = 24) -> dict:
        """Get delivery metrics for the last N hours.
        
        Returns dict with:
            - queued: total messages queued
            - sent: successfully sent
            - failed: failed after retries
            - acked: acknowledged by followers
            - delivery_rate: sent/queued * 100
            - ack_rate: acked/sent * 100
            - failure_rate: failed/queued * 100
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Calculate cutoff time
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        
        cursor.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status IN ('SENT', 'ACKED') THEN 1 ELSE 0 END) as sent,
                SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed,
                SUM(CASE WHEN status = 'ACKED' THEN 1 ELSE 0 END) as acked
            FROM telegram_audit
            WHERE queued_at >= ?
        ''', (cutoff,))
        
        row = cursor.fetchone()
        total = row['total'] or 0
        sent = row['sent'] or 0
        failed = row['failed'] or 0
        acked = row['acked'] or 0
        
        return {
            'queued': total,
            'sent': sent,
            'failed': failed,
            'acked': acked,
            'delivery_rate': round((sent / total * 100) if total > 0 else 0, 1),
            'ack_rate': round((acked / sent * 100) if sent > 0 else 0, 1),
            'failure_rate': round((failed / total * 100) if total > 0 else 0, 1)
        }
    
    def get_telegram_audit_recent(self, limit: int = 50) -> list:
        """Get recent audit records for display/debugging."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM telegram_audit 
            ORDER BY queued_at DESC 
            LIMIT ?
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]
