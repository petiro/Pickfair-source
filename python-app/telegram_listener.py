"""
Telegram Listener for betting signals.
Monitors specified channels/groups/chats and parses betting signals.
"""

import re
import asyncio
import threading
from datetime import datetime
from typing import Optional, Callable, Dict, List
from telethon import TelegramClient, events
from telethon.sessions import StringSession


class TelegramListener:
    """Listens to Telegram messages and triggers bet placement."""
    
    def __init__(self, api_id: int, api_hash: str, session_string: str = None):
        """
        Initialize Telegram listener.
        
        Args:
            api_id: Telegram API ID (from my.telegram.org)
            api_hash: Telegram API Hash
            session_string: Optional saved session string for persistent login
        """
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_string = session_string
        self.client: Optional[TelegramClient] = None
        self.running = False
        self.loop = None
        self.thread = None
        
        self.monitored_chats: List[int] = []
        self.signal_callback: Optional[Callable] = None
        self.message_callback: Optional[Callable] = None
        self.status_callback: Optional[Callable] = None
        
        self.signal_patterns = self._default_patterns()
    
    def _default_patterns(self) -> Dict:
        """Default regex patterns for parsing betting signals."""
        return {
            'score': r'(\d+)\s*[-:]\s*(\d+)',
            'odds': r'@?\s*(\d+[.,]\d+)',
            'stake': r'(?:stake|puntata|€)\s*(\d+(?:[.,]\d+)?)',
            'back': r'\b(back|punta)\b',
            'lay': r'\b(lay|banca)\b',
            'over': r'\b(over|sopra)\s*(\d+[.,]?\d*)',
            'under': r'\b(under|sotto)\s*(\d+[.,]?\d*)',
        }
    
    def set_signal_patterns(self, patterns: Dict):
        """Update signal parsing patterns."""
        self.signal_patterns.update(patterns)
    
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
    
    def parse_signal(self, text: str) -> Optional[Dict]:
        """
        Parse a message text to extract betting signal.
        
        Returns dict with keys: side, selection, odds, stake, raw_text
        or None if no valid signal found.
        """
        text_lower = text.lower()
        signal = {
            'raw_text': text,
            'timestamp': datetime.now().isoformat(),
            'side': None,
            'selection': None,
            'odds': None,
            'stake': None,
        }
        
        if re.search(self.signal_patterns['back'], text_lower):
            signal['side'] = 'BACK'
        elif re.search(self.signal_patterns['lay'], text_lower):
            signal['side'] = 'LAY'
        
        score_match = re.search(self.signal_patterns['score'], text)
        if score_match:
            signal['selection'] = f"{score_match.group(1)}-{score_match.group(2)}"
        
        odds_match = re.search(self.signal_patterns['odds'], text)
        if odds_match:
            odds_str = odds_match.group(1).replace(',', '.')
            signal['odds'] = float(odds_str)
        
        stake_match = re.search(self.signal_patterns['stake'], text_lower)
        if stake_match:
            stake_str = stake_match.group(1).replace(',', '.')
            signal['stake'] = float(stake_str)
        
        over_match = re.search(self.signal_patterns['over'], text_lower)
        if over_match:
            signal['selection'] = f"Over {over_match.group(2)}"
        
        under_match = re.search(self.signal_patterns['under'], text_lower)
        if under_match:
            signal['selection'] = f"Under {under_match.group(2)}"
        
        if signal['side'] and (signal['selection'] or signal['odds']):
            return signal
        
        return None
    
    async def _connect(self):
        """Connect to Telegram."""
        try:
            if self.session_string:
                self.client = TelegramClient(
                    StringSession(self.session_string),
                    self.api_id,
                    self.api_hash
                )
            else:
                self.client = TelegramClient(
                    StringSession(),
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
