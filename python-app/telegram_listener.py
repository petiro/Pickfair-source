"""
Telegram Listener for betting signals.
Monitors specified channels/groups/chats and parses betting signals.
"""

import re
import os
import asyncio
import threading
from datetime import datetime
from typing import Optional, Callable, Dict, List
from telethon import TelegramClient, events
from telethon.sessions import StringSession


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
        
        self.monitored_chats: List[int] = []
        self.signal_callback: Optional[Callable] = None
        self.message_callback: Optional[Callable] = None
        self.status_callback: Optional[Callable] = None
        self.cashout_callback: Optional[Callable] = None
        
        self.signal_patterns = self._default_patterns()
        self.custom_patterns: List[Dict] = []
        self.custom_patterns_only = False
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
                settings = self.db.get_telegram_settings() or {}
                self.custom_patterns_only = bool(settings.get('custom_patterns_only', 0))
            except Exception as e:
                print(f"Error loading custom patterns: {e}")
                self.custom_patterns = []
    
    def set_monitored_chats(self, chat_ids: List[int]):
        """Set list of chat IDs to monitor."""
        self.monitored_chats = chat_ids
    
    def set_callbacks(self, 
                      on_signal: Callable = None, 
                      on_message: Callable = None,
                      on_status: Callable = None,
                      on_cashout: Callable = None):
        """Set callback functions for events."""
        self.signal_callback = on_signal
        self.message_callback = on_message
        self.status_callback = on_status
        self.cashout_callback = on_cashout
    
    def parse_cashout_command(self, text: str) -> Optional[Dict]:
        """
        Parse cashout command from message.
        
        Supported formats:
        - CASHOUT ALL / CASHOUT TUTTO / CHIUDI TUTTO → close all positions
        - CASHOUT [event name] / CHIUDI [event name] → close specific event
        
        Returns dict with 'type' ('all' or 'event') and 'event_name' if specific.
        """
        text_upper = text.upper().strip()
        
        all_pattern = r'\b(CASHOUT|CHIUDI|ESCI|CLOSE)\s+(ALL|TUTTO|TUTTI|TUTTE)\b'
        if re.search(all_pattern, text_upper):
            return {'type': 'all', 'raw_text': text}
        
        event_pattern = r'\b(CASHOUT|CHIUDI|ESCI|CLOSE)\s+(.+?)(?:\n|$)'
        match = re.search(event_pattern, text, re.IGNORECASE)
        if match:
            event_name = match.group(2).strip()
            if event_name and len(event_name) > 2:
                return {'type': 'event', 'event_name': event_name, 'raw_text': text}
        
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
        
        if self.custom_patterns_only:
            if not signal['market_type']:
                return None
            if signal['event']:
                return signal
            return None
        
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
        if not self.client:
            return
        
        @self.client.on(events.NewMessage(chats=self.monitored_chats if self.monitored_chats else None))
        async def handler(event):
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
            
            copy_cashout = self.parse_copy_cashout(text)
            if copy_cashout and self.cashout_callback:
                copy_cashout['chat_id'] = chat_id
                copy_cashout['sender_id'] = sender_id
                self.cashout_callback(copy_cashout)
                return
            
            cashout_cmd = self.parse_cashout_command(text)
            if cashout_cmd and self.cashout_callback:
                cashout_cmd['chat_id'] = chat_id
                cashout_cmd['sender_id'] = sender_id
                self.cashout_callback(cashout_cmd)
                return
            
            copy_bet = self.parse_copy_bet(text)
            if copy_bet and self.signal_callback:
                copy_bet['chat_id'] = chat_id
                copy_bet['sender_id'] = sender_id
                self.signal_callback(copy_bet)
                return
            
            signal = self.parse_signal(text)
            if signal and self.signal_callback:
                signal['chat_id'] = chat_id
                signal['sender_id'] = sender_id
                self.signal_callback(signal)
        
        self.running = True
        if self.status_callback:
            self.status_callback('LISTENING', f'In ascolto su {len(self.monitored_chats)} chat')
        
        await self.client.run_until_disconnected()
    
    def _run_loop(self):
        """Run the asyncio event loop in a thread."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        try:
            connected = self.loop.run_until_complete(self._connect())
            if connected:
                self.loop.run_until_complete(self._start_listening())
        except Exception as e:
            if self.status_callback:
                self.status_callback('ERROR', str(e))
        finally:
            self.running = False
            if self.loop:
                self.loop.close()
    
    def start(self):
        """Start the listener in a background thread."""
        if self.running:
            return
        
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
    
    def stop(self):
        """Stop the listener."""
        self.running = False
        
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
    
    async def _send_message_async(self, chat_id: int, message: str) -> bool:
        """Send a message to a chat asynchronously."""
        try:
            if self.client and await self.client.is_user_authorized():
                await self.client.send_message(chat_id, message)
                return True
            return False
        except Exception as e:
            print(f"[COPY] Error sending message: {e}")
            return False
    
    def send_message(self, chat_id: int, message: str) -> bool:
        """
        Send a message to a chat (thread-safe).
        Used for Copy Trading to broadcast bets/cashouts.
        
        Args:
            chat_id: Telegram chat ID to send to
            message: Message text to send
            
        Returns:
            True if message was sent successfully
        """
        if not self.running or not self.client or not self.loop:
            print("[COPY] Cannot send message: listener not running")
            return False
        
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._send_message_async(chat_id, message),
                self.loop
            )
            result = future.result(timeout=10)
            return result
        except Exception as e:
            print(f"[COPY] Error in send_message: {e}")
            return False
    
    def format_copy_bet_message(self, event_name: str, market_type: str, selection: str, 
                                 side: str, odds: float, stake_percent: float) -> str:
        """
        Format a COPY BET message for broadcasting.
        
        Args:
            event_name: Name of the event (e.g., "Juventus vs Inter")
            market_type: Market type (e.g., "OVER_UNDER", "MATCH_ODDS")
            selection: Selection name (e.g., "Over 2.5", "1")
            side: BACK or LAY
            odds: Bet odds
            stake_percent: Stake as percentage of bankroll
            
        Returns:
            Formatted message string
        """
        return (
            f"COPY BET\n"
            f"Evento: {event_name}\n"
            f"Mercato: {market_type}\n"
            f"Selezione: {selection}\n"
            f"Tipo: {side}\n"
            f"Quota: {odds:.2f}\n"
            f"Stake: {stake_percent:.1f}%"
        )
    
    def format_copy_cashout_message(self, event_name: str = None, cashout_all: bool = False) -> str:
        """
        Format a COPY CASHOUT message for broadcasting.
        
        Args:
            event_name: Name of the event to cashout (None if cashout all)
            cashout_all: True to cashout all positions
            
        Returns:
            Formatted message string
        """
        if cashout_all:
            return "COPY CASHOUT ALL"
        else:
            return f"COPY CASHOUT\nEvento: {event_name}"
    
    def parse_copy_bet(self, text: str) -> Optional[Dict]:
        """
        Parse a COPY BET message from another Pickfair instance.
        
        Returns dict with: event, market_type, selection, side, odds, stake_percent
        or None if not a valid COPY BET message.
        """
        if "COPY BET" not in text.upper():
            return None
        
        try:
            result = {
                'is_copy_bet': True,
                'raw_text': text,
                'timestamp': datetime.now().isoformat()
            }
            
            event_match = re.search(r'Evento:\s*(.+?)(?:\n|$)', text)
            if event_match:
                result['event'] = event_match.group(1).strip()
            
            market_match = re.search(r'Mercato:\s*(.+?)(?:\n|$)', text)
            if market_match:
                result['market_type'] = market_match.group(1).strip()
            
            selection_match = re.search(r'Selezione:\s*(.+?)(?:\n|$)', text)
            if selection_match:
                result['selection'] = selection_match.group(1).strip()
            
            side_match = re.search(r'Tipo:\s*(BACK|LAY)', text, re.IGNORECASE)
            if side_match:
                result['side'] = side_match.group(1).upper()
            
            odds_match = re.search(r'Quota:\s*([\d.,]+)', text)
            if odds_match:
                result['odds'] = float(odds_match.group(1).replace(',', '.'))
            
            stake_match = re.search(r'Stake:\s*([\d.,]+)%?', text)
            if stake_match:
                result['stake_percent'] = float(stake_match.group(1).replace(',', '.'))
            
            if result.get('event') and result.get('selection'):
                return result
            
            return None
            
        except Exception as e:
            print(f"[COPY] Error parsing COPY BET: {e}")
            return None
    
    def parse_copy_cashout(self, text: str) -> Optional[Dict]:
        """
        Parse a COPY CASHOUT message from another Pickfair instance.
        
        Returns dict with: type ('all' or 'event'), event_name (if specific)
        or None if not a valid COPY CASHOUT message.
        """
        text_upper = text.upper().strip()
        
        if "COPY CASHOUT" not in text_upper:
            return None
        
        try:
            if "COPY CASHOUT ALL" in text_upper:
                return {'type': 'all', 'is_copy_cashout': True, 'raw_text': text}
            
            event_match = re.search(r'Evento:\s*(.+?)(?:\n|$)', text)
            if event_match:
                return {
                    'type': 'event',
                    'event_name': event_match.group(1).strip(),
                    'is_copy_cashout': True,
                    'raw_text': text
                }
            
            return None
            
        except Exception as e:
            print(f"[COPY] Error parsing COPY CASHOUT: {e}")
            return None


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
