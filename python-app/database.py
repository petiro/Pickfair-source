"""
Database layer using SQLite for local storage.
Stores Betfair credentials, certificates, and bet history.
"""

import sqlite3
import os
import json
from datetime import datetime
from pathlib import Path

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
        self._init_db()
    
    def _init_db(self):
        """Initialize database tables."""
        conn = sqlite3.connect(self.db_path)
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
                profit_loss REAL
            )
        ''')
        
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
                require_confirmation INTEGER DEFAULT 1
            )
        ''')
        
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
        
        conn.commit()
        conn.close()
    
    def get_settings(self):
        """Get Betfair settings. Strips whitespace from string values."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM settings WHERE id = 1')
        row = cursor.fetchone()
        conn.close()
        if row:
            settings = dict(row)
            for key in ['username', 'app_key', 'certificate', 'private_key']:
                if settings.get(key) and isinstance(settings[key], str):
                    settings[key] = settings[key].strip()
            return settings
        return None
    
    def save_credentials(self, username, app_key, certificate, private_key):
        """Save Betfair credentials. Strips whitespace from username and app_key."""
        conn = sqlite3.connect(self.db_path)
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
        conn.close()
    
    def save_session(self, session_token, session_expiry):
        """Save session token."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE settings SET session_token = ?, session_expiry = ?
            WHERE id = 1
        ''', (session_token, session_expiry))
        conn.commit()
        conn.close()
    
    def clear_session(self):
        """Clear session token."""
        self.save_session(None, None)
    
    def save_password(self, password):
        """Save or clear password."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('UPDATE settings SET password = ? WHERE id = 1', (password,))
        conn.commit()
        conn.close()
    
    def save_update_url(self, update_url):
        """Save GitHub releases URL for auto-updates."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('UPDATE settings SET update_url = ? WHERE id = 1', (update_url,))
        conn.commit()
        conn.close()
    
    def save_skipped_version(self, version):
        """Save version that user chose to skip."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('UPDATE settings SET skipped_version = ? WHERE id = 1', (version,))
        conn.commit()
        conn.close()
    
    def save_bet(self, event_name, market_id, market_name, bet_type, 
                 selections, total_stake, potential_profit, status):
        """Save bet to history."""
        conn = sqlite3.connect(self.db_path)
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
        conn.close()
    
    def get_recent_bets(self, limit=50):
        """Get recent bets."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM bets ORDER BY placed_at DESC LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def save_bet_order(self, bet_id, event_name, market_id, market_name, selection_id,
                       runner_name, side, price, stake, liability, status, matched_stake=0,
                       unmatched_stake=0, average_price=None, potential_profit=None):
        """Save a bet order."""
        conn = sqlite3.connect(self.db_path)
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
        conn.close()
    
    def update_bet_status(self, bet_id, status, matched_stake=None, unmatched_stake=None,
                          profit_loss=None, settled_at=None):
        """Update bet status."""
        conn = sqlite3.connect(self.db_path)
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
        conn.close()
    
    def get_bets_by_status(self, status_list, limit=50):
        """Get bets by status list."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        placeholders = ','.join(['?' for _ in status_list])
        cursor.execute(f'''
            SELECT * FROM bets WHERE status IN ({placeholders})
            ORDER BY placed_at DESC LIMIT ?
        ''', status_list + [limit])
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def get_today_profit_loss(self):
        """Get today's total profit/loss including cashouts."""
        conn = sqlite3.connect(self.db_path)
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
        
        conn.close()
        return bets_pl + cashout_pl
    
    def save_cashout_transaction(self, market_id, selection_id, original_bet_id,
                                  cashout_bet_id, original_side, original_stake,
                                  original_price, cashout_side, cashout_stake,
                                  cashout_price, profit_loss):
        """Save a cashout transaction."""
        conn = sqlite3.connect(self.db_path)
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
        conn.close()
    
    def get_active_bets_count(self):
        """Get count of active/pending bets."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) FROM bets WHERE status IN ('PENDING', 'MATCHED', 'PARTIALLY_MATCHED')
        ''')
        result = cursor.fetchone()[0]
        conn.close()
        return result
    
    def save_booking(self, event_name, market_id, market_name, selection_id,
                     runner_name, side, target_price, stake, current_price):
        """Save a bet booking."""
        conn = sqlite3.connect(self.db_path)
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
        conn.close()
        return booking_id
    
    def get_pending_bookings(self):
        """Get all pending bookings."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM bookings WHERE status = 'PENDING' ORDER BY created_at DESC
        ''')
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def update_booking_status(self, booking_id, status, bet_id=None):
        """Update booking status."""
        conn = sqlite3.connect(self.db_path)
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
        conn.close()
    
    def cancel_booking(self, booking_id):
        """Cancel a booking."""
        self.update_booking_status(booking_id, 'CANCELLED')
    
    def save_auto_cashout_rule(self, market_id, bet_id, profit_target, loss_limit):
        """Save auto-cashout rule."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO auto_cashout_rules 
            (market_id, bet_id, profit_target, loss_limit, status, created_at)
            VALUES (?, ?, ?, ?, 'ACTIVE', ?)
        ''', (market_id, bet_id, profit_target, loss_limit, datetime.now().isoformat()))
        rule_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return rule_id
    
    def get_active_auto_cashout_rules(self):
        """Get active auto-cashout rules."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM auto_cashout_rules WHERE status = 'ACTIVE'
        ''')
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def deactivate_auto_cashout_rule(self, rule_id):
        """Deactivate an auto-cashout rule."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE auto_cashout_rules SET status = 'TRIGGERED', triggered_at = ? WHERE id = ?
        ''', (datetime.now().isoformat(), rule_id))
        conn.commit()
        conn.close()
    
    def get_telegram_settings(self):
        """Get Telegram settings."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM telegram_settings WHERE id = 1')
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def save_telegram_settings(self, api_id, api_hash, session_string=None, 
                                phone_number=None, enabled=False, auto_bet=False,
                                require_confirmation=True):
        """Save Telegram settings."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE telegram_settings SET 
                api_id = ?, api_hash = ?, session_string = ?, phone_number = ?,
                enabled = ?, auto_bet = ?, require_confirmation = ?
            WHERE id = 1
        ''', (api_id, api_hash, session_string, phone_number,
              1 if enabled else 0, 1 if auto_bet else 0, 1 if require_confirmation else 0))
        conn.commit()
        conn.close()
    
    def save_telegram_session(self, session_string):
        """Save Telegram session string."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('UPDATE telegram_settings SET session_string = ? WHERE id = 1', (session_string,))
        conn.commit()
        conn.close()
    
    def get_telegram_chats(self):
        """Get monitored Telegram chats."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM telegram_chats ORDER BY added_at DESC')
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def add_telegram_chat(self, chat_id, chat_name=''):
        """Add a chat to monitor."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO telegram_chats (chat_id, chat_name, enabled, added_at)
                VALUES (?, ?, 1, ?)
            ''', (str(chat_id), chat_name, datetime.now().isoformat()))
            conn.commit()
        except sqlite3.IntegrityError:
            pass
        conn.close()
    
    def remove_telegram_chat(self, chat_id):
        """Remove a chat from monitoring."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM telegram_chats WHERE chat_id = ?', (str(chat_id),))
        conn.commit()
        conn.close()
    
    def save_telegram_signal(self, chat_id, sender_id, raw_text, parsed_signal):
        """Save a received Telegram signal."""
        conn = sqlite3.connect(self.db_path)
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
        conn.close()
        return signal_id
    
    def get_pending_signals(self):
        """Get pending Telegram signals."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM telegram_signals WHERE status = 'PENDING' ORDER BY received_at DESC
        ''')
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def update_signal_status(self, signal_id, status, bet_id=None):
        """Update signal status."""
        conn = sqlite3.connect(self.db_path)
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
        conn.close()
    
    def get_recent_signals(self, limit=50):
        """Get recent Telegram signals."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM telegram_signals ORDER BY received_at DESC LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    # Simulation Mode Methods
    def get_simulation_settings(self):
        """Get simulation settings and balance."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM simulation_settings WHERE id = 1')
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def update_simulation_balance(self, new_balance, bet_result=None):
        """Update virtual balance after a bet."""
        conn = sqlite3.connect(self.db_path)
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
        conn.close()
    
    def increment_simulation_bet_count(self, new_balance):
        """Increment total_bets counter and update balance when placing a simulation bet."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE simulation_settings SET 
                virtual_balance = ?,
                total_bets = total_bets + 1
            WHERE id = 1
        ''', (new_balance,))
        conn.commit()
        conn.close()
    
    def reset_simulation(self, starting_balance=1000.0):
        """Reset simulation to starting balance."""
        conn = sqlite3.connect(self.db_path)
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
        conn.close()
    
    def save_simulation_bet(self, event_name, market_id, market_name, side, 
                            selections, total_stake, potential_profit):
        """Save a simulation bet."""
        conn = sqlite3.connect(self.db_path)
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
        conn.close()
        return bet_id
    
    def get_simulation_bets(self, limit=50):
        """Get simulation bet history."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM simulation_bets ORDER BY placed_at DESC LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
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
