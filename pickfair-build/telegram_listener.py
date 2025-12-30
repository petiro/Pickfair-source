"""
Telegram Listener for betting signals.
Monitors specified channels/groups/chats and parses betting signals.
"""

import re
import os
import asyncio
import threading
import logging
import time
from datetime import datetime
from typing import Optional, Callable, Dict, List
from telethon import TelegramClient, events
from telethon.sessions import StringSession

try:
    from bet_logger import get_bet_logger
    _bet_logger = get_bet_logger()
except ImportError:
    _bet_logger = None

# Try to import cryptg for faster encryption (optional)
try:
    import cryptg
    logging.info("cryptg available - using accelerated encryption")
except ImportError:
    logging.debug("cryptg not available - using standard encryption")


class TelegramListener:
    """Listens to Telegram messages and triggers bet placement."""
    
    def __init__(self, api_id: int, api_hash: str, session_string: str = None, session_path: str = None):
        """
        Initialize Telegram listener.
        
        Args:
            api_id: Telegram API ID (from my.telegram.org)
            api_hash: Telegram API Hash
            session_string: Optional saved session string for persistent login
            session_path: Optional path to session file (preferred over session_string)
        """
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_string = session_string
        self.session_path = session_path
        self.client: Optional[TelegramClient] = None
        self.running = False
        self.loop = None
        self.thread = None
        
        # Separate client/loop for Master mode sending (doesn't interfere with listener)
        self.send_client: Optional[TelegramClient] = None
        self.send_loop = None
        self.send_thread = None
        self.sending_connected = False
        self._send_lock = threading.Lock()  # Prevent concurrent connect attempts
        self._listener_starting = False  # Blocks send client creation during listener startup
        
        # Broadcast queue for Copy Trading (handles burst, retry, rate limiting)
        self._broadcast_queue = None  # asyncio.Queue, created when send loop starts
        self._broadcast_worker_running = False
        self._last_send_time = 0  # For rate limiting (flood protection)
        self._sent_notifications = set()  # Deduplication: tracks sent order_ids
        self._sent_notifications_max = 1000  # Max size before cleanup
        
        # Dynamic rate limiting
        self._base_delay = 0.4  # Base delay between sends
        self._current_delay = 0.4  # Adaptive delay
        self._last_flood_wait = 0  # Last flood wait duration
        self._consecutive_successes = 0  # Track successes for delay reduction
        
        self.monitored_chats: List[int] = []
        self.signal_callback: Optional[Callable] = None
        self.message_callback: Optional[Callable] = None
        self.status_callback: Optional[Callable] = None
        
        self.signal_patterns = self._default_patterns()
        self.custom_patterns: List[Dict] = []
        self.db = None
    
    def _default_patterns(self) -> Dict:
        """Default regex patterns for parsing betting signals."""
        return {
            'event': r'🆚\s*(.+?)(?:\n|$)',
            'league': r'🏆\s*(.+?)(?:\n|$)',
            'score': r'(\d+)\s*[-–]\s*(\d+)',
            'time': r'(\d+)m',
            'odds': r'@?\s*(\d+[.,]\d+)',
            'stake': r'(?:stake|puntata|€)\s*(\d+(?:[.,]\d+)?)',
            'back': r'\b(back|punta|P\.Exc\.)\b',
            'lay': r'\b(lay|banca|B\.Exc\.)\b',
            'over': r'\b(over|sopra)\s*(\d+[.,]?\d*)',
            'under': r'\b(under|sotto)\s*(\d+[.,]?\d*)',
            'next_goal': r'NEXT\s*GOL|PROSSIMO\s*GOL',
            'gg': r'\b(GG|BTTS|goal\s*goal|entrambe.*segn|both.*score)\b',
            'ng': r'\b(NG|NO\s*GOAL|no\s*gol|nessuna.*segn)\b',
            'first_half_over': r'(?:1[°º]?\s*(?:tempo|half|t)|primo\s*tempo|1T)\s*(?:over|sopra)\s*(\d+[.,]?\d*)',
            'first_half_under': r'(?:1[°º]?\s*(?:tempo|half|t)|primo\s*tempo|1T)\s*(?:under|sotto)\s*(\d+[.,]?\d*)',
            'double_chance': r'\b(1X|X2|12|doppia\s*chance)\b',
            'match_odds': r'\b(?:FT\s*)?([1X2])\b(?!\s*[X12])|(?:esito\s*finale|vincente)\s*([1X2])',
            'correct_score': r'(?:CS|RIS\.?|risultato\s*esatto)\s*(\d+)[-–](\d+)|(\d+)[-–](\d+)\s*(?:finale|FT|CS)',
            'asian_handicap': r'(?:AH|handicap\s*asiatico)\s*(home|away|casa|ospiti|1|2)\s*([+-]?\d+(?:[.,]\d)?)',
            'draw_no_bet': r'\b(DNB|draw\s*no\s*bet|pareggio\s*no\s*scommessa)\s*(1|2|home|away|casa|ospiti)\b',
            'half_time_full_time': r'\b(HT/FT|parziale[/\\]finale)\s*([1X2])[/\\]([1X2])\b|(?<![\d])([1X2])/([1X2])(?![\d])',
            'live_filter': r'\b(LIVE|IN\s*PLAY|IN\s*CORSO|DIRETTA)\b',
            'prematch_filter': r'\b(PRE[-\s]?MATCH|ANTE[-\s]?MATCH|PRIMA\s*PARTITA|NON\s*LIVE)\b',
            'dutching': r'[Dd]ut(?:h)?ching\s+([\d]+-[\d]+(?:\s*,\s*[\d]+-[\d]+)*)',
        }
    
    def set_signal_patterns(self, patterns: Dict):
        """Update signal parsing patterns."""
        self.signal_patterns.update(patterns)
    
    def set_database(self, db):
        """Set database reference for loading custom patterns."""
        self.db = db
        self.reload_custom_patterns()
    
    def reload_custom_patterns(self):
        """Reload custom patterns from database."""
        if self.db:
            try:
                self.custom_patterns = self.db.get_signal_patterns(enabled_only=True)
            except Exception as e:
                print(f"Error loading custom patterns: {e}")
                self.custom_patterns = []
    
    def set_monitored_chats(self, chat_ids: List[int]):
        """Set list of chat IDs to monitor."""
        self.monitored_chats = chat_ids
    
    def set_callbacks(self, 
                      on_signal: Callable = None, 
                      on_message: Callable = None,
                      on_status: Callable = None):
        """Set callback functions for events."""
        self.signal_callback = on_signal
        self.message_callback = on_message
        self.status_callback = on_status
    
    def connect_for_sending(self):
        """Connect to Telegram only for sending messages (Master mode).
        
        Uses separate client/loop from main listener to avoid conflicts.
        Thread-safe with lock to prevent concurrent connection attempts.
        """
        # If full listener is running and ready, use that instead
        if self.running and self.client and self.loop and self.loop.is_running():
            return True
        
        # If listener is starting, return "pending" status - messages will be buffered
        if self._listener_starting:
            return "PENDING"  # Special return value to signal buffering needed
        
        # If listener running but loop not ready yet, wait briefly
        if self.running and (not self.client or not self.loop):
            import time
            for _ in range(30):  # 3 seconds max wait
                time.sleep(0.1)
                if self.client and self.loop and self.loop.is_running():
                    return True
            return False
        
        # Check if already connected
        if self.sending_connected and self.send_client and self.send_loop:
            # Verify loop is still running
            try:
                if self.send_loop.is_running():
                    return True
            except Exception:
                pass
            # Reset if loop is dead
            self._reset_send_connection()
        
        # Use lock to prevent concurrent connection attempts
        with self._send_lock:
            # Double-check after acquiring lock
            if self.sending_connected and self.send_client and self.send_loop:
                try:
                    if self.send_loop.is_running():
                        return True
                except Exception:
                    pass
                self._reset_send_connection()
            
            def _connect_thread():
                try:
                    self.send_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(self.send_loop)
                    
                    # Create separate client for sending (use different session name)
                    session_name = f"{self.session_path}_send" if self.session_path else None
                    if session_name:
                        self.send_client = TelegramClient(session_name, self.api_id, self.api_hash)
                    elif self.session_string:
                        # For session_string, we reuse it (same auth, different client instance)
                        self.send_client = TelegramClient(StringSession(self.session_string), self.api_id, self.api_hash)
                    else:
                        self.send_client = TelegramClient(StringSession(), self.api_id, self.api_hash)
                    
                    self.send_loop.run_until_complete(self.send_client.connect())
                    self.sending_connected = True
                    logging.info("Telegram connected for sending (Master mode)")
                    
                    # Keep loop running for async operations
                    self.send_loop.run_forever()
                except Exception as e:
                    logging.error(f"Error connecting Telegram for sending: {e}")
                finally:
                    # Reset on exit (loop stopped or error)
                    self._reset_send_connection()
            
            self.send_thread = threading.Thread(target=_connect_thread, daemon=True)
            self.send_thread.start()
            
            # Wait for connection
            import time
            for _ in range(50):  # 5 seconds max
                time.sleep(0.1)
                if self.sending_connected:
                    return True
            return False
    
    def _reset_send_connection(self):
        """Reset send connection state for clean reconnect."""
        self.sending_connected = False
        self.send_client = None
        self.send_loop = None
        self._broadcast_worker_running = False
        self._broadcast_queue = None
    
    async def _safe_send(self, client, chat_id: str, message: str, retries: int = 3, audit_id: int = None) -> bool:
        """Send message with retry logic, rate limiting, and audit tracking.
        
        Args:
            client: TelegramClient to use
            chat_id: Target chat ID
            message: Message to send
            retries: Number of retry attempts (default 3)
            audit_id: Database audit row ID for status tracking
            
        Returns:
            True if sent successfully, False otherwise
        """
        import time as time_module
        from telethon.errors import FloodWaitError
        
        last_error = None
        last_error_code = None
        attempt = 0
        
        # Use while loop so FloodWait doesn't consume retry attempts
        while attempt < retries:
            try:
                # Dynamic rate limiting: adaptive delay based on flood history
                now = time_module.time()
                elapsed = now - self._last_send_time
                if elapsed < self._current_delay:
                    await asyncio.sleep(self._current_delay - elapsed)
                
                # Ensure connected
                if not client.is_connected():
                    logging.info(f"_safe_send: reconnecting (attempt {attempt + 1})")
                    await asyncio.wait_for(client.connect(), timeout=5)
                
                # Resolve entity and send
                entity = await asyncio.wait_for(client.get_entity(int(chat_id)), timeout=5)
                result = await asyncio.wait_for(client.send_message(entity, message), timeout=5)
                
                self._last_send_time = time_module.time()
                logging.info(f"_safe_send: SUCCESS to {chat_id} (attempt {attempt + 1})")
                
                # Adaptive rate limiting: reduce delay on consecutive successes
                self._consecutive_successes += 1
                if self._consecutive_successes >= 10 and self._current_delay > self._base_delay:
                    self._current_delay = max(self._base_delay, self._current_delay * 0.9)
                    logging.debug(f"Rate limit reduced to {self._current_delay:.2f}s")
                
                # Update audit: SENT
                if audit_id and self.db:
                    telegram_msg_id = result.id if hasattr(result, 'id') else None
                    self.db.update_telegram_audit_sent(audit_id, telegram_msg_id, attempt + 1)
                
                return True
                
            except FloodWaitError as e:
                # FloodWait: wait EXACTLY the required time, DON'T increment attempt
                wait_seconds = e.seconds + 1
                logging.warning(f"_safe_send: FloodWait {wait_seconds}s (not counting as retry)")
                
                # Adaptive rate limiting: increase delay on flood
                self._consecutive_successes = 0
                self._last_flood_wait = wait_seconds
                self._current_delay = max(self._current_delay, wait_seconds / 2)
                logging.info(f"Rate limit increased to {self._current_delay:.2f}s")
                
                # Record flood wait in audit
                if audit_id and self.db:
                    self.db.update_telegram_audit_flood_wait(audit_id, wait_seconds)
                
                await asyncio.sleep(wait_seconds)
                # Don't increment attempt - continue to next iteration without consuming retry
                continue
                
            except asyncio.TimeoutError:
                last_error = "Timeout"
                last_error_code = "TIMEOUT"
                logging.warning(f"_safe_send: timeout (attempt {attempt + 1}/{retries})")
                attempt += 1  # Consume retry
            except Exception as e:
                last_error = str(e)
                last_error_code = type(e).__name__
                logging.warning(f"_safe_send: error (attempt {attempt + 1}/{retries}): {e}")
                attempt += 1  # Consume retry
            
            # Wait before retry (exponential backoff: 1.5s, 3s, 4.5s)
            if attempt < retries:
                delay = 1.5 * attempt
                logging.info(f"_safe_send: waiting {delay}s before retry")
                await asyncio.sleep(delay)
        
        logging.error(f"_safe_send: FAILED after {retries} attempts to {chat_id}")
        
        # Update audit: FAILED
        if audit_id and self.db:
            self.db.update_telegram_audit_failed(audit_id, last_error_code, last_error, retries)
        
        return False
    
    async def _broadcast_worker(self, client):
        """Async worker that processes broadcast queue with retry and rate limiting.
        
        Runs continuously, consuming messages from queue and sending them.
        Tracks audit status (QUEUED → SENT/FAILED).
        """
        logging.info("Broadcast worker started")
        self._broadcast_worker_running = True
        
        while self._broadcast_worker_running:
            try:
                # Wait for message with timeout (allows periodic checks)
                try:
                    msg_data = await asyncio.wait_for(self._broadcast_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                
                chat_id = msg_data.get('chat_id')
                message = msg_data.get('message')
                audit_id = msg_data.get('audit_id')
                
                if chat_id and message:
                    await self._safe_send(client, chat_id, message, audit_id=audit_id)
                
                self._broadcast_queue.task_done()
                
            except asyncio.CancelledError:
                logging.info("Broadcast worker cancelled")
                break
            except Exception as e:
                logging.error(f"Broadcast worker error: {e}")
        
        logging.info("Broadcast worker stopped")
    
    def _start_broadcast_worker(self, client, loop):
        """Start the broadcast worker task on the given loop."""
        if self._broadcast_queue is None:
            # Create queue in the context of the loop
            async def create_queue():
                self._broadcast_queue = asyncio.Queue()
            asyncio.run_coroutine_threadsafe(create_queue(), loop).result(timeout=2)
        
        # Start worker task
        asyncio.run_coroutine_threadsafe(self._broadcast_worker(client), loop)
        logging.info("Broadcast worker scheduled")
    
    def send_message(self, chat_id: str, message: str, dedup_key: str = None):
        """Send a message to a Telegram chat (for Copy Trading MASTER mode).
        
        Uses async queue for burst handling, retry, and rate limiting.
        First message sends immediately, subsequent messages respect 0.4s rate limit.
        
        Args:
            chat_id: Target chat ID
            message: Message to send
            dedup_key: Optional key for deduplication (e.g., order_id). If provided and
                       already sent, message is skipped.
        """
        import time
        
        # Deduplication check
        if dedup_key:
            if dedup_key in self._sent_notifications:
                logging.debug(f"Skipping duplicate notification: {dedup_key}")
                return True  # Already sent, consider success
            
            # Cleanup if too many entries
            if len(self._sent_notifications) >= self._sent_notifications_max:
                # Remove oldest half
                to_remove = list(self._sent_notifications)[:self._sent_notifications_max // 2]
                for key in to_remove:
                    self._sent_notifications.discard(key)
                logging.debug(f"Cleaned up {len(to_remove)} old notification keys")
        
        # Add timestamp to message to avoid Telegram flood detection (same message filter)
        timestamped_message = f"{message}\n\n[{int(time.time())}]"
        
        logging.info(f"send_message called: chat_id={chat_id}, message_preview={message[:50]}...")
        
        # Determine which client/loop to use
        if self.running and self.client and self.loop and self.loop.is_running():
            active_client = self.client
            active_loop = self.loop
            logging.info("Using main listener for sending")
        else:
            # Try to connect for sending
            connect_result = self.connect_for_sending()
            
            if connect_result == "PENDING":
                logging.warning("Cannot send message: listener starting")
                return False
            
            if not connect_result:
                logging.error("Failed to auto-connect Telegram for sending")
                return False
            
            if self.running and self.client and self.loop and self.loop.is_running():
                active_client = self.client
                active_loop = self.loop
                logging.info("Using main listener for sending (became ready)")
            else:
                active_client = self.send_client
                active_loop = self.send_loop
                logging.info("Using dedicated send client for Master mode")
        
        # Check basic requirements
        if not active_client:
            logging.warning("Telegram client not available, cannot send message")
            return False
        
        if not active_loop or not active_loop.is_running():
            logging.warning("Event loop not available or not running, cannot send message")
            return False
        
        # Start broadcast worker if not running
        if not self._broadcast_worker_running:
            try:
                self._start_broadcast_worker(active_client, active_loop)
                # Brief wait for worker to start
                import time as t
                t.sleep(0.1)
            except Exception as e:
                logging.error(f"Failed to start broadcast worker: {e}")
                return False
        
        # Enqueue message for async processing (with retry and rate limiting)
        if self._broadcast_queue is None:
            logging.error("Broadcast queue not initialized")
            return False
        
        try:
            # Insert audit record: QUEUED
            audit_id = None
            if self.db:
                try:
                    audit_id = self.db.insert_telegram_audit(chat_id, message[:500], dedup_key)
                except Exception as e:
                    logging.warning(f"Failed to insert audit record: {e}")
            
            async def _enqueue():
                await self._broadcast_queue.put({
                    'chat_id': chat_id,
                    'message': timestamped_message,
                    'audit_id': audit_id
                })
            
            future = asyncio.run_coroutine_threadsafe(_enqueue(), active_loop)
            future.result(timeout=2)  # Should be near-instant
            
            # Track dedup key if provided
            if dedup_key:
                self._sent_notifications.add(dedup_key)
            
            logging.info(f"Message queued for broadcast to {chat_id} (audit_id={audit_id})")
            return True
            
        except Exception as e:
            logging.error(f"Error queuing message: {e}")
            return False
    
    def shutdown_broadcast_queue(self, timeout: float = 5.0):
        """Graceful shutdown: wait for queue to empty before stopping worker.
        
        Args:
            timeout: Max seconds to wait for queue to drain (default 5s)
        """
        if not self._broadcast_queue or not self._broadcast_worker_running:
            return
        
        logging.info("Shutting down broadcast queue...")
        
        # Get active loop
        active_loop = self.loop if (self.running and self.loop) else self.send_loop
        if not active_loop or not active_loop.is_running():
            self._broadcast_worker_running = False
            return
        
        try:
            async def _drain():
                try:
                    await asyncio.wait_for(self._broadcast_queue.join(), timeout=timeout)
                    logging.info("Broadcast queue drained successfully")
                except asyncio.TimeoutError:
                    logging.warning(f"Broadcast queue drain timed out after {timeout}s")
            
            future = asyncio.run_coroutine_threadsafe(_drain(), active_loop)
            future.result(timeout=timeout + 1)
        except Exception as e:
            logging.error(f"Error draining broadcast queue: {e}")
        finally:
            self._broadcast_worker_running = False
            logging.info("Broadcast worker stopped")
    
    def parse_copy_bet(self, text: str) -> Optional[Dict]:
        """Parse a COPY BET message from master."""
        if 'COPY BET' not in text.upper():
            return None
        
        logging.debug("[COPY] parse_copy_bet: checking message")
        try:
            evento = re.search(r'Evento:\s*(.+)', text)
            mercato = re.search(r'Mercato:\s*(.+)', text)
            selezione = re.search(r'Selezione:\s*(.+)', text)
            tipo = re.search(r'Tipo:\s*(BACK|LAY)', text, re.IGNORECASE)
            quota = re.search(r'Quota:\s*([\d.,]+)', text)
            stake = re.search(r'Stake:\s*([\d.,]+)%?', text)
            stake_eur = re.search(r'StakeEUR:\s*([\d.,]+)', text)
            
            if evento and mercato and selezione:
                result = {
                    'event': evento.group(1).strip(),
                    'market_type': mercato.group(1).strip(),
                    'selection': selezione.group(1).strip(),
                    'side': tipo.group(1).upper() if tipo else 'BACK',
                    'odds': float(quota.group(1).replace(',', '.')) if quota else None,
                    'stake_percent': float(stake.group(1).replace(',', '.')) if stake else None,
                    'stake_amount': float(stake_eur.group(1).replace(',', '.')) if stake_eur else None,
                    'is_copy_bet': True
                }
                logging.info(f"[COPY] Parsed COPY BET: {result['event']} | {result['selection']} @ {result['odds']} {result['side']}")
                return result
        except Exception as e:
            logging.error(f"[COPY] Error parsing COPY BET: {e}")
        return None
    
    def parse_copy_cashout(self, text: str) -> Optional[Dict]:
        """Parse a COPY CASHOUT message from master."""
        if 'COPY CASHOUT' not in text.upper():
            return None
        
        logging.debug("[COPY] parse_copy_cashout: checking message")
        try:
            evento = re.search(r'Evento:\s*(.+)', text)
            if evento:
                result = {
                    'event': evento.group(1).strip(),
                    'is_copy_cashout': True
                }
                logging.info(f"[COPY] Parsed COPY CASHOUT: {result['event']}")
                return result
        except Exception as e:
            logging.error(f"[COPY] Error parsing COPY CASHOUT: {e}")
        return None
    
    def parse_copy_dutching(self, text: str) -> Optional[Dict]:
        """Parse a COPY DUTCHING message from master.
        
        Format:
        COPY DUTCHING
        Evento: Roma v Lazio
        Mercato: Correct Score
        Selezioni: 2-1 @ 9.50, 2-0 @ 11.00, 1-0 @ 8.00
        Tipo: BACK
        ProfitTargetEUR: 20.00
        StakeTotaleEUR: 7.94
        """
        if 'COPY DUTCHING' not in text.upper():
            return None
        
        logging.debug("[COPY] parse_copy_dutching: checking message")
        try:
            evento = re.search(r'Evento:\s*(.+)', text)
            mercato = re.search(r'Mercato:\s*(.+)', text)
            selezioni = re.search(r'Selezioni:\s*(.+)', text)
            tipo = re.search(r'Tipo:\s*(BACK|LAY|MIXED)', text, re.IGNORECASE)
            profit_target = re.search(r'ProfitTargetEUR:\s*([\d.,]+)', text)
            stake_totale = re.search(r'StakeTotaleEUR:\s*([\d.,]+)', text)
            
            if evento and mercato and selezioni:
                # Parse selections: "2-1 @ 9.50, 2-0 @ 11.00, 1-0 @ 8.00"
                selections_str = selezioni.group(1).strip()
                parsed_selections = []
                for sel_part in selections_str.split(','):
                    sel_part = sel_part.strip()
                    match = re.match(r'(.+?)\s*@\s*([\d.,]+)', sel_part)
                    if match:
                        parsed_selections.append({
                            'selection': match.group(1).strip(),
                            'odds': float(match.group(2).replace(',', '.'))
                        })
                
                result = {
                    'event': evento.group(1).strip(),
                    'market_type': mercato.group(1).strip(),
                    'selections': parsed_selections,
                    'side': tipo.group(1).upper() if tipo else 'BACK',
                    'profit_target': float(profit_target.group(1).replace(',', '.')) if profit_target else None,
                    'total_stake': float(stake_totale.group(1).replace(',', '.')) if stake_totale else None,
                    'is_copy_dutching': True
                }
                logging.info(f"[COPY] Parsed COPY DUTCHING: {result['event']} | {len(parsed_selections)} selections | profit_target={result['profit_target']}")
                return result
        except Exception as e:
            logging.error(f"[COPY] Error parsing COPY DUTCHING: {e}")
        return None
    
    def _parse_ack(self, text: str) -> Optional[int]:
        """Parse ACK message from follower.
        
        Formats supported:
        - "ACK 12345" (message ID)
        - "ack 12345"
        
        Returns telegram_msg_id if valid ACK, None otherwise.
        """
        if not text:
            return None
        
        match = re.match(r'^ACK\s+(\d+)\s*$', text.strip(), re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
        return None
    
    def parse_booking_signal(self, text: str) -> Optional[Dict]:
        """Parse a BOOK message for bet booking.
        
        Formats supported:
        - "Roma - Milan book over 2.5 @ 3"
        - "Roma Milan BOOK BACK 1 @ 2.5"
        - "Roma - Milan book under 1.5 @ 1.80"
        - "Roma Milan book lay X @ 4.0"
        - "Roma Milan prenota over 2.5 @ 3"
        - "Roma Milan reserve 1 @ 2.0"
        - "Roma Milan booking over 1.5 @ 1.8"
        """
        text_lower = text.lower()
        
        # Check for any booking keyword
        booking_keywords = ['book', 'booking', 'prenota', 'prenotazione', 'reserve', 'riserva']
        has_booking = any(kw in text_lower for kw in booking_keywords)
        if not has_booking:
            return None
        
        try:
            # Extract event name (before any booking keyword)
            booking_pattern = r'^(.+?)\s+(?:book|booking|prenota|prenotazione|reserve|riserva)\s+'
            event_match = re.search(booking_pattern, text, re.IGNORECASE)
            if not event_match:
                return None
            event = event_match.group(1).strip()
            
            # Extract target odds (after @)
            odds_match = re.search(r'@\s*([\d.,]+)', text)
            if not odds_match:
                return None
            target_odds = float(odds_match.group(1).replace(',', '.'))
            
            # Determine bet side (BACK default, LAY if specified)
            side = 'BACK'
            if re.search(r'\blay\b', text, re.IGNORECASE):
                side = 'LAY'
            
            # Determine market type and selection
            market_type = None
            selection = None
            over_line = None
            handicap_line = None
            
            text_lower = text.lower()
            text_upper = text.upper()
            
            # Extract selection portion: after booking keyword, before @
            selection_part = ''
            for kw in ['BOOK', 'BOOKING', 'PRENOTA', 'PRENOTAZIONE', 'RESERVE', 'RISERVA']:
                if kw in text_upper:
                    parts = text_upper.split(kw)
                    if len(parts) > 1:
                        selection_part = parts[1].split('@')[0]
                        break
            
            # CORRECT SCORE (Risultato Esatto) - e.g., "book 2-1", "book 0-0", "book 3-2"
            score_match = re.search(r'\b(\d+)\s*[-:]\s*(\d+)\b', selection_part)
            if score_match:
                home_goals = score_match.group(1)
                away_goals = score_match.group(2)
                selection = f"{home_goals} - {away_goals}"
                market_type = 'CORRECT_SCORE'
            
            # Over/Under (all lines)
            elif re.search(r'over\s*([\d.,]+)', text, re.IGNORECASE):
                over_match = re.search(r'over\s*([\d.,]+)', text, re.IGNORECASE)
                over_line = float(over_match.group(1).replace(',', '.'))
                selection = f"Over {over_line}"
                market_type = 'OVER_UNDER'
            elif re.search(r'under\s*([\d.,]+)', text, re.IGNORECASE):
                under_match = re.search(r'under\s*([\d.,]+)', text, re.IGNORECASE)
                over_line = float(under_match.group(1).replace(',', '.'))
                selection = f"Under {over_line}"
                market_type = 'OVER_UNDER'
            
            # First Half Over/Under - e.g., "1t over 0.5", "primo tempo under 1.5"
            elif re.search(r'(1t|primo\s*tempo|1h|first\s*half)\s*over\s*([\d.,]+)', text, re.IGNORECASE):
                fh_over = re.search(r'(1t|primo\s*tempo|1h|first\s*half)\s*over\s*([\d.,]+)', text, re.IGNORECASE)
                over_line = float(fh_over.group(2).replace(',', '.'))
                selection = f"Over {over_line}"
                market_type = 'OVER_UNDER_FH'
            elif re.search(r'(1t|primo\s*tempo|1h|first\s*half)\s*under\s*([\d.,]+)', text, re.IGNORECASE):
                fh_under = re.search(r'(1t|primo\s*tempo|1h|first\s*half)\s*under\s*([\d.,]+)', text, re.IGNORECASE)
                over_line = float(fh_under.group(2).replace(',', '.'))
                selection = f"Under {over_line}"
                market_type = 'OVER_UNDER_FH'
            
            # GG/NG (BTTS - Both Teams To Score)
            elif re.search(r'\bgg\b|\bbtts\b|\bgol\s*gol\b', text, re.IGNORECASE):
                selection = 'Yes'
                market_type = 'BOTH_TEAMS_TO_SCORE'
            elif re.search(r'\bng\b|\bno\s*gol\b|\bnogol\b', text, re.IGNORECASE):
                selection = 'No'
                market_type = 'BOTH_TEAMS_TO_SCORE'
            
            # Double Chance - e.g., "1X", "X2", "12"
            elif re.search(r'\b1X\b|\bX1\b', selection_part, re.IGNORECASE):
                selection = '1X'
                market_type = 'DOUBLE_CHANCE'
            elif re.search(r'\bX2\b|\b2X\b', selection_part, re.IGNORECASE):
                selection = 'X2'
                market_type = 'DOUBLE_CHANCE'
            elif re.search(r'\b12\b', selection_part):
                selection = '12'
                market_type = 'DOUBLE_CHANCE'
            
            # Draw No Bet - e.g., "dnb 1", "dnb casa"
            elif re.search(r'\bdnb\b', text, re.IGNORECASE):
                if re.search(r'\b(1|home|casa)\b', selection_part, re.IGNORECASE):
                    selection = 'Home'
                    market_type = 'DRAW_NO_BET'
                elif re.search(r'\b(2|away|trasferta)\b', selection_part, re.IGNORECASE):
                    selection = 'Away'
                    market_type = 'DRAW_NO_BET'
            
            # Asian Handicap - e.g., "ah +0.5", "handicap -1.5"
            elif re.search(r'(ah|asian|handicap)\s*([+-]?\s*[\d.,]+)', text, re.IGNORECASE):
                ah_match = re.search(r'(ah|asian|handicap)\s*([+-]?\s*[\d.,]+)', text, re.IGNORECASE)
                handicap_line = float(ah_match.group(2).replace(',', '.').replace(' ', ''))
                if re.search(r'\b(1|home|casa)\b', selection_part, re.IGNORECASE):
                    selection = f"Home {handicap_line:+.1f}"
                else:
                    selection = f"Away {-handicap_line:+.1f}"
                market_type = 'ASIAN_HANDICAP'
            
            # Half Time / Full Time - e.g., "1/1", "X/2", "1/X"
            elif re.search(r'\b([1X2])\s*/\s*([1X2])\b', selection_part, re.IGNORECASE):
                htft_match = re.search(r'\b([1X2])\s*/\s*([1X2])\b', selection_part, re.IGNORECASE)
                ht = htft_match.group(1).upper()
                ft = htft_match.group(2).upper()
                selection = f"{ht}/{ft}"
                market_type = 'HALF_TIME_FULL_TIME'
            
            # Half Time Result - e.g., "1t 1", "primo tempo X"
            elif re.search(r'(1t|primo\s*tempo|1h|first\s*half)\s+([1X2])\b', text, re.IGNORECASE):
                ht_match = re.search(r'(1t|primo\s*tempo|1h|first\s*half)\s+([1X2])\b', text, re.IGNORECASE)
                ht_result = ht_match.group(2).upper()
                if ht_result == '1':
                    selection = 'Home'
                elif ht_result == 'X':
                    selection = 'Draw'
                else:
                    selection = 'Away'
                market_type = 'HALF_TIME'
            
            # First Half Correct Score - e.g., "1t 1-0", "primo tempo 0-0"
            elif re.search(r'(1t|primo\s*tempo|1h|first\s*half)\s*(\d+)\s*[-:]\s*(\d+)', text, re.IGNORECASE):
                fh_score = re.search(r'(1t|primo\s*tempo|1h|first\s*half)\s*(\d+)\s*[-:]\s*(\d+)', text, re.IGNORECASE)
                home_goals = fh_score.group(2)
                away_goals = fh_score.group(3)
                selection = f"{home_goals} - {away_goals}"
                market_type = 'HALF_TIME_SCORE'
            
            # 1X2 (Match Odds)
            elif re.search(r'\b1\b', selection_part) and not re.search(r'\b12\b', selection_part):
                selection = 'Home'
                market_type = 'MATCH_ODDS'
            elif re.search(r'\bX\b', selection_part, re.IGNORECASE):
                selection = 'Draw'
                market_type = 'MATCH_ODDS'
            elif re.search(r'\b2\b', selection_part) and not re.search(r'\b12\b', selection_part):
                selection = 'Away'
                market_type = 'MATCH_ODDS'
            
            if event and target_odds and selection and market_type:
                return {
                    'event': event,
                    'market_type': market_type,
                    'selection': selection,
                    'side': side,
                    'target_odds': target_odds,
                    'over_line': over_line,
                    'is_booking': True
                }
        except Exception as e:
            print(f"Error parsing BOOK signal: {e}")
        return None
    
    def parse_signal(self, text: str) -> Optional[Dict]:
        """
        Parse a message text to extract betting signal.
        
        Returns dict with keys: event, side, selection, market_type, odds, stake, raw_text
        or None if no valid signal found.
        """
        signal = {
            'raw_text': text,
            'timestamp': datetime.now().isoformat(),
            'event': None,
            'league': None,
            'side': None,
            'selection': None,
            'market_type': None,
            'odds': None,
            'stake': None,
            'score_home': None,
            'score_away': None,
            'over_line': None,
            'minute': None,
            'bet_side': 'BACK',
            'live_only': False,
            'event_filter': None,  # 'LIVE', 'PRE_MATCH', or None (search both)
            'dutching_selections': None,  # List of scores for dutching, e.g., ['2-1', '3-1', '2-2']
        }
        
        event_match = re.search(self.signal_patterns['event'], text)
        if event_match:
            signal['event'] = event_match.group(1).strip()
        
        league_match = re.search(self.signal_patterns['league'], text)
        if league_match:
            signal['league'] = league_match.group(1).strip()
        
        score_match = re.search(self.signal_patterns['score'], text)
        if score_match:
            signal['score_home'] = int(score_match.group(1))
            signal['score_away'] = int(score_match.group(2))
            total_goals = signal['score_home'] + signal['score_away']
            signal['over_line'] = total_goals + 0.5
        
        time_match = re.search(self.signal_patterns['time'], text)
        if time_match:
            signal['minute'] = int(time_match.group(1))
        
        if re.search(self.signal_patterns['back'], text, re.IGNORECASE):
            signal['side'] = 'BACK'
        elif re.search(self.signal_patterns['lay'], text, re.IGNORECASE):
            signal['side'] = 'LAY'
        
        # Check for event filter (LIVE or PRE-MATCH)
        if re.search(self.signal_patterns['live_filter'], text, re.IGNORECASE):
            signal['event_filter'] = 'LIVE'
            signal['live_only'] = True
        elif re.search(self.signal_patterns['prematch_filter'], text, re.IGNORECASE):
            signal['event_filter'] = 'PRE_MATCH'
        
        for custom in self.custom_patterns:
            try:
                pattern = custom.get('pattern', '')
                market_type = custom.get('market_type', '')
                bet_side = custom.get('bet_side', 'BACK')
                live_only = bool(custom.get('live_only', 0))
                if pattern and re.search(pattern, text, re.IGNORECASE):
                    match = re.search(pattern, text, re.IGNORECASE)
                    signal['market_type'] = market_type
                    signal['bet_side'] = bet_side
                    signal['live_only'] = live_only
                    if match.groups():
                        if 'OVER' in market_type.upper():
                            signal['selection'] = f"Over {match.group(1)}" if match.group(1) else 'Yes'
                        elif 'UNDER' in market_type.upper():
                            signal['selection'] = f"Under {match.group(1)}" if match.group(1) else 'Yes'
                        elif market_type == 'BOTH_TEAMS_TO_SCORE':
                            matched_text = match.group(0).upper()
                            if 'NG' in matched_text or 'NO' in matched_text:
                                signal['selection'] = 'No'
                            else:
                                signal['selection'] = 'Yes'
                        else:
                            signal['selection'] = match.group(1) if match.group(1) else match.group(0)
                    else:
                        if market_type == 'BOTH_TEAMS_TO_SCORE':
                            signal['selection'] = 'Yes'
                        else:
                            signal['selection'] = match.group(0)
                    signal['side'] = bet_side
                    break
            except Exception as e:
                continue
        
        if re.search(self.signal_patterns['next_goal'], text, re.IGNORECASE):
            signal['market_type'] = 'NEXT_GOAL'
            if signal['score_home'] is not None and signal['score_away'] is not None:
                signal['selection'] = f"Over {signal['over_line']}"
                signal['side'] = 'BACK'
        
        if re.search(self.signal_patterns['gg'], text, re.IGNORECASE):
            signal['market_type'] = 'BOTH_TEAMS_TO_SCORE'
            signal['selection'] = 'Yes'
            signal['side'] = signal['side'] or 'BACK'
        
        if re.search(self.signal_patterns['ng'], text, re.IGNORECASE):
            signal['market_type'] = 'BOTH_TEAMS_TO_SCORE'
            signal['selection'] = 'No'
            signal['side'] = signal['side'] or 'BACK'
        
        first_half_over = re.search(self.signal_patterns['first_half_over'], text, re.IGNORECASE)
        if first_half_over:
            line = first_half_over.group(1).replace(',', '.')
            signal['selection'] = f"Over {line}"
            signal['market_type'] = 'FIRST_HALF_GOALS'
            signal['over_line'] = float(line)
            signal['side'] = signal['side'] or 'BACK'
        
        first_half_under = re.search(self.signal_patterns['first_half_under'], text, re.IGNORECASE)
        if first_half_under:
            line = first_half_under.group(1).replace(',', '.')
            signal['selection'] = f"Under {line}"
            signal['market_type'] = 'FIRST_HALF_GOALS'
            signal['over_line'] = float(line)
            signal['side'] = signal['side'] or 'BACK'
        
        dc_match = re.search(self.signal_patterns['double_chance'], text, re.IGNORECASE)
        if dc_match:
            signal['market_type'] = 'DOUBLE_CHANCE'
            signal['selection'] = dc_match.group(1).upper()
            signal['side'] = signal['side'] or 'BACK'
        
        mo_match = re.search(self.signal_patterns['match_odds'], text, re.IGNORECASE)
        if mo_match and not signal['market_type']:
            sel = mo_match.group(1) or mo_match.group(2)
            if sel:
                signal['market_type'] = 'MATCH_ODDS'
                signal['selection'] = sel.upper()
                signal['side'] = signal['side'] or 'BACK'
        
        cs_match = re.search(self.signal_patterns['correct_score'], text, re.IGNORECASE)
        if cs_match and not signal['market_type']:
            if cs_match.group(1) and cs_match.group(2):
                signal['market_type'] = 'CORRECT_SCORE'
                signal['selection'] = f"{cs_match.group(1)}-{cs_match.group(2)}"
            elif cs_match.group(3) and cs_match.group(4):
                signal['market_type'] = 'CORRECT_SCORE'
                signal['selection'] = f"{cs_match.group(3)}-{cs_match.group(4)}"
            signal['side'] = signal['side'] or 'BACK'
        
        ah_match = re.search(self.signal_patterns['asian_handicap'], text, re.IGNORECASE)
        if ah_match and not signal['market_type']:
            team = ah_match.group(1).lower()
            line = ah_match.group(2).replace(',', '.')
            if team in ['home', 'casa', '1']:
                signal['selection'] = f"Home {line}"
            else:
                signal['selection'] = f"Away {line}"
            signal['market_type'] = 'ASIAN_HANDICAP'
            signal['handicap_line'] = float(line)
            signal['side'] = signal['side'] or 'BACK'
        
        dnb_match = re.search(self.signal_patterns['draw_no_bet'], text, re.IGNORECASE)
        if dnb_match and not signal['market_type']:
            team = dnb_match.group(2).lower()
            if team in ['home', 'casa', '1']:
                signal['selection'] = 'Home'
            else:
                signal['selection'] = 'Away'
            signal['market_type'] = 'DRAW_NO_BET'
            signal['side'] = signal['side'] or 'BACK'
        
        htft_match = re.search(self.signal_patterns['half_time_full_time'], text, re.IGNORECASE)
        if htft_match and not signal['market_type']:
            if htft_match.group(2) and htft_match.group(3):
                ht = htft_match.group(2).upper()
                ft = htft_match.group(3).upper()
            elif htft_match.group(4) and htft_match.group(5):
                ht = htft_match.group(4).upper()
                ft = htft_match.group(5).upper()
            else:
                ht, ft = None, None
            if ht and ft:
                signal['market_type'] = 'HALF_TIME_FULL_TIME'
                signal['selection'] = f"{ht}/{ft}"
                signal['side'] = signal['side'] or 'BACK'
        
        # Parse Dutching selections (e.g., "Dutching 2-1, 3-1, 2-2")
        dutching_match = re.search(self.signal_patterns['dutching'], text, re.IGNORECASE)
        if dutching_match:
            scores_str = dutching_match.group(1)
            # Split by comma and normalize scores
            scores = []
            for score in scores_str.split(','):
                score = score.strip()
                # Normalize: "2-1" -> "2 - 1" for Betfair format
                parts = score.split('-')
                if len(parts) == 2:
                    normalized = f"{parts[0].strip()} - {parts[1].strip()}"
                    scores.append(normalized)
            if scores:
                signal['dutching_selections'] = scores
                signal['market_type'] = 'CORRECT_SCORE'
                signal['selection'] = 'Dutching'
                signal['side'] = signal['side'] or 'BACK'
        
        over_match = re.search(self.signal_patterns['over'], text, re.IGNORECASE)
        if over_match and not signal['market_type']:
            signal['selection'] = f"Over {over_match.group(2)}"
            signal['market_type'] = 'OVER_UNDER'
        
        under_match = re.search(self.signal_patterns['under'], text, re.IGNORECASE)
        if under_match and not signal['market_type']:
            signal['selection'] = f"Under {under_match.group(2)}"
            signal['market_type'] = 'OVER_UNDER'
        
        odds_match = re.search(self.signal_patterns['odds'], text)
        if odds_match:
            odds_str = odds_match.group(1).replace(',', '.')
            signal['odds'] = float(odds_str)
        
        stake_match = re.search(self.signal_patterns['stake'], text.lower())
        if stake_match:
            stake_str = stake_match.group(1).replace(',', '.')
            signal['stake'] = float(stake_str)
        
        if signal['market_type'] and signal['selection'] and signal['event']:
            signal['side'] = signal['side'] or 'BACK'
            return signal
        
        if signal['event'] and signal['score_home'] is not None and not signal['market_type']:
            signal['selection'] = f"Over {signal['over_line']}"
            signal['side'] = 'BACK'
            signal['market_type'] = 'OVER_UNDER'
            return signal
        
        if signal['side'] and signal['selection']:
            return signal
        
        return None
    
    async def _connect(self):
        """Connect to Telegram."""
        try:
            if self.session_path:
                self.client = TelegramClient(
                    self.session_path,
                    self.api_id,
                    self.api_hash
                )
            elif self.session_string:
                self.client = TelegramClient(
                    StringSession(self.session_string),
                    self.api_id,
                    self.api_hash
                )
            else:
                session_path = os.path.join(os.environ.get('APPDATA', '.'), 'Pickfair', 'telegram_session')
                self.client = TelegramClient(
                    session_path,
                    self.api_id,
                    self.api_hash
                )
            
            await self.client.connect()
            
            if not await self.client.is_user_authorized():
                if self.status_callback:
                    self.status_callback('AUTH_REQUIRED', 'Autenticazione richiesta')
                return False
            
            if self.status_callback:
                self.status_callback('CONNECTED', 'Connesso a Telegram')
            
            return True
            
        except Exception as e:
            if self.status_callback:
                self.status_callback('ERROR', str(e))
            return False
    
    async def _start_listening(self):
        """Start listening for messages."""
        import logging
        
        if not self.client:
            logging.warning("_start_listening: No client!")
            return
        
        logging.info(f"_start_listening: Setting up handler for {len(self.monitored_chats)} chats: {self.monitored_chats}")
        
        @self.client.on(events.NewMessage(chats=self.monitored_chats if self.monitored_chats else None))
        async def handler(event):
            logging.debug(f"NewMessage event from chat {event.chat_id}")
            message = event.message
            text = message.text or ''
            
            chat_id = event.chat_id
            sender_id = event.sender_id
            
            if self.message_callback:
                self.message_callback({
                    'chat_id': chat_id,
                    'sender_id': sender_id,
                    'text': text,
                    'timestamp': datetime.now().isoformat()
                })
            
            # Check for ACK message from follower (Copy Trading acknowledgement)
            ack_msg_id = self._parse_ack(text)
            if ack_msg_id:
                if self.db:
                    updated = self.db.update_telegram_audit_acked(ack_msg_id)
                    if updated:
                        logging.info(f"ACK received for message {ack_msg_id}")
                    else:
                        logging.debug(f"ACK ignored (no matching audit): {ack_msg_id}")
                return  # Don't process ACK as signal
            
            # Check for COPY BET/CASHOUT first (Copy Trading)
            _start_time = time.time()
            copy_bet = self.parse_copy_bet(text)
            if copy_bet and self.signal_callback:
                copy_bet['chat_id'] = chat_id
                copy_bet['sender_id'] = sender_id
                copy_bet['raw_text'] = text
                if _bet_logger:
                    _bet_logger.log_telegram_signal_received(
                        chat_id=chat_id,
                        message_text=text[:500],
                        signal_type='COPY_BET'
                    )
                logging.info(f"[FOLLOWER] Received COPY BET from chat {chat_id} -> calling signal_callback")
                self.signal_callback(copy_bet)
                if _bet_logger:
                    _bet_logger.log_telegram_signal_processed(
                        chat_id=chat_id,
                        signal_type='COPY_BET',
                        processing_time_ms=int((time.time() - _start_time) * 1000)
                    )
                return
            
            copy_cashout = self.parse_copy_cashout(text)
            if copy_cashout and self.signal_callback:
                copy_cashout['chat_id'] = chat_id
                copy_cashout['sender_id'] = sender_id
                copy_cashout['raw_text'] = text
                if _bet_logger:
                    _bet_logger.log_telegram_signal_received(chat_id=chat_id, message_text=text[:500], signal_type='COPY_CASHOUT')
                logging.info(f"[FOLLOWER] Received COPY CASHOUT from chat {chat_id} -> calling signal_callback")
                self.signal_callback(copy_cashout)
                if _bet_logger:
                    _bet_logger.log_telegram_signal_processed(chat_id=chat_id, signal_type='COPY_CASHOUT', processing_time_ms=int((time.time() - _start_time) * 1000))
                return
            
            # Check for COPY DUTCHING (unified dutching message from Master)
            copy_dutching = self.parse_copy_dutching(text)
            if copy_dutching and self.signal_callback:
                copy_dutching['chat_id'] = chat_id
                copy_dutching['sender_id'] = sender_id
                copy_dutching['raw_text'] = text
                if _bet_logger:
                    _bet_logger.log_telegram_signal_received(chat_id=chat_id, message_text=text[:500], signal_type='COPY_DUTCHING')
                logging.info(f"[FOLLOWER] Received COPY DUTCHING from chat {chat_id}: {len(copy_dutching['selections'])} selections, profit_target={copy_dutching['profit_target']}")
                self.signal_callback(copy_dutching)
                if _bet_logger:
                    _bet_logger.log_telegram_signal_processed(chat_id=chat_id, signal_type='COPY_DUTCHING', processing_time_ms=int((time.time() - _start_time) * 1000))
                return
            
            # Check for booking signals (e.g., "Roma - Milan book over 2.5 @ 3")
            booking = self.parse_booking_signal(text)
            if booking and self.signal_callback:
                booking['chat_id'] = chat_id
                booking['sender_id'] = sender_id
                booking['raw_text'] = text
                if _bet_logger:
                    _bet_logger.log_telegram_signal_received(chat_id=chat_id, message_text=text[:500], signal_type='BOOKING')
                logging.info(f"[LISTENER] Booking signal from chat {chat_id}: {booking.get('event')} -> {booking.get('market_type')}")
                self.signal_callback(booking)
                if _bet_logger:
                    _bet_logger.log_telegram_signal_processed(chat_id=chat_id, signal_type='BOOKING', processing_time_ms=int((time.time() - _start_time) * 1000))
                return
            
            # Parse normal signals
            signal = self.parse_signal(text)
            if signal and self.signal_callback:
                signal['chat_id'] = chat_id
                signal['sender_id'] = sender_id
                if _bet_logger:
                    _bet_logger.log_telegram_signal_received(chat_id=chat_id, message_text=text[:500], signal_type=signal.get('market_type', 'UNKNOWN'))
                logging.info(f"[LISTENER] Signal parsed from chat {chat_id}: {signal.get('event')} -> {signal.get('market_type')} @ {signal.get('odds')}")
                self.signal_callback(signal)
                if _bet_logger:
                    _bet_logger.log_telegram_signal_processed(chat_id=chat_id, signal_type=signal.get('market_type', 'SIGNAL'), processing_time_ms=int((time.time() - _start_time) * 1000))
        
        self.running = True
        logging.info(f"Telegram listener STARTED - monitoring {len(self.monitored_chats)} chats")
        if self.status_callback:
            self.status_callback('LISTENING', f'In ascolto su {len(self.monitored_chats)} chat')
        
        await self.client.run_until_disconnected()
    
    def _run_loop(self):
        """Run the asyncio event loop in a thread with run_forever pattern."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        # Initialize message queue for Copy Trading
        self._send_queue = asyncio.Queue()
        
        async def _main():
            """Main async function that runs connect, listen, and message sender."""
            connected = await self._connect()
            if not connected:
                self._listener_starting = False  # Clear on failure too
                self.running = False  # Mark as not running so send client can work
                return
            
            # Clear startup flag now that connection is established
            self._listener_starting = False
            
            # Start message sender consumer
            sender_task = asyncio.create_task(self._message_sender_loop())
            
            # Run listener (blocks until disconnected)
            await self._start_listening()
            
            # Cleanup
            sender_task.cancel()
        
        try:
            self.loop.run_until_complete(_main())
        except Exception as e:
            import logging
            logging.error(f"Telegram loop error: {e}")
            if self.status_callback:
                self.status_callback('ERROR', str(e))
        finally:
            self.running = False
            # Don't close loop immediately - allow pending sends to complete
            try:
                self.loop.run_until_complete(asyncio.sleep(0.5))
            except:
                pass
            if self.loop and not self.loop.is_closed():
                self.loop.close()
    
    async def _message_sender_loop(self):
        """Async consumer loop for sending queued messages (Copy Trading)."""
        import logging
        while self.running:
            try:
                # Wait for message with timeout to allow periodic checks
                try:
                    msg_data = await asyncio.wait_for(self._send_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                
                chat_id = msg_data.get('chat_id')
                message = msg_data.get('message')
                
                try:
                    # Ensure connected
                    if not self.client.is_connected():
                        logging.info("Sender: Client not connected, reconnecting...")
                        await self.client.connect()
                    
                    # Resolve entity and send
                    entity = await self.client.get_entity(int(chat_id))
                    await self.client.send_message(entity, message)
                    logging.info(f"Sender: Message delivered to {chat_id}")
                    
                    # Small delay between messages to avoid flood
                    await asyncio.sleep(0.3)
                    
                except Exception as e:
                    logging.error(f"Sender: Failed to send to {chat_id}: {e}")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Sender loop error: {e}")
    
    def start(self):
        """Start the listener in a background thread (non-daemon for reliability)."""
        import logging
        
        if self.running:
            logging.debug("start() called but already running")
            return
        
        logging.info(f"Telegram listener start() called - will monitor chats: {self.monitored_chats}")
        
        # Block send client creation during startup
        self._listener_starting = True
        
        # Set running flag early to prevent send_message from recreating send client
        self.running = True
        
        # Shut down send-only client first to avoid session conflicts
        self._shutdown_send_client()
        
        # CRITICAL: daemon=False ensures thread survives main thread issues
        self.thread = threading.Thread(target=self._run_loop, daemon=False)
        self.thread.start()
    
    def _shutdown_send_client(self):
        """Shutdown send-only client to avoid session conflicts with main listener."""
        # Acquire lock to serialize with any connect_for_sending() in progress
        with self._send_lock:
            if not self.sending_connected and not self.send_client:
                return
            
            logging.info("Shutting down send-only client before starting main listener...")
            
            try:
                if self.send_client and self.send_loop and self.send_loop.is_running():
                    # Disconnect client
                    future = asyncio.run_coroutine_threadsafe(
                        self.send_client.disconnect(),
                        self.send_loop
                    )
                    try:
                        future.result(timeout=3)
                    except:
                        pass
                    
                    # Stop the loop
                    self.send_loop.call_soon_threadsafe(self.send_loop.stop)
            except Exception as e:
                logging.error(f"Error shutting down send client: {e}")
            finally:
                self._reset_send_connection()
    
    def stop(self):
        """Stop the listener."""
        self.running = False
        
        # Graceful shutdown: drain broadcast queue first
        self.shutdown_broadcast_queue(timeout=5.0)
        
        if self.client and self.loop:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self.client.disconnect(),
                    self.loop
                )
                future.result(timeout=5)
            except:
                pass
        
        if self.status_callback:
            self.status_callback('STOPPED', 'Listener fermato')
    
    def get_session_string(self) -> Optional[str]:
        """Get current session string for saving."""
        if self.client:
            return self.client.session.save()
        return None
    
    def get_available_dialogs(self, callback):
        """Get available dialogs using the existing connected client.
        
        Args:
            callback: Function to call with list of chat dicts or None on error
        """
        if not self.client or not self.loop or not self.running:
            callback(None)
            return
        
        async def _fetch_dialogs():
            try:
                from telethon.tl.types import Channel, Chat, User
                dialogs = await self.client.get_dialogs()
                chat_list = []
                
                for d in dialogs:
                    entity = d.entity
                    chat_type = 'Altro'
                    
                    if isinstance(entity, Channel):
                        chat_type = 'Canale' if entity.broadcast else 'Gruppo'
                    elif isinstance(entity, Chat):
                        chat_type = 'Gruppo'
                    elif isinstance(entity, User):
                        chat_type = 'Bot' if entity.bot else 'Utente'
                    
                    chat_list.append({
                        'id': d.id,
                        'name': d.name or str(d.id),
                        'type': chat_type
                    })
                
                return chat_list
            except Exception as e:
                logging.error(f"Error fetching dialogs: {e}")
                return None
        
        def run_fetch():
            try:
                future = asyncio.run_coroutine_threadsafe(_fetch_dialogs(), self.loop)
                result = future.result(timeout=30)
                callback(result)
            except Exception as e:
                logging.error(f"Error in get_available_dialogs: {e}")
                callback(None)
        
        threading.Thread(target=run_fetch, daemon=True).start()
    
    async def request_code(self, phone: str):
        """Request authentication code."""
        if not self.client:
            self.client = TelegramClient(
                StringSession(),
                self.api_id,
                self.api_hash
            )
            await self.client.connect()
        
        await self.client.send_code_request(phone)
    
    async def sign_in(self, phone: str, code: str, password: str = None):
        """Complete sign in with code."""
        try:
            await self.client.sign_in(phone, code, password=password)
            return True, self.client.session.save()
        except Exception as e:
            return False, str(e)


class SignalQueue:
    """Thread-safe queue for betting signals."""
    
    def __init__(self, max_size: int = 100):
        self.queue: List[Dict] = []
        self.max_size = max_size
        self.lock = threading.Lock()
    
    def add(self, signal: Dict):
        """Add signal to queue."""
        with self.lock:
            self.queue.append(signal)
            if len(self.queue) > self.max_size:
                self.queue.pop(0)
    
    def get_pending(self) -> List[Dict]:
        """Get all pending signals."""
        with self.lock:
            return list(self.queue)
    
    def remove(self, signal: Dict):
        """Remove a signal from queue."""
        with self.lock:
            if signal in self.queue:
                self.queue.remove(signal)
    
    def clear(self):
        """Clear all signals."""
        with self.lock:
            self.queue.clear()
