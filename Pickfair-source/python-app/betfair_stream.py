"""
Betfair Exchange Stream API client for real-time order and market updates.
Based on: https://betfair-developer-docs.atlassian.net/wiki/spaces/1smk3cen4v3lu3yomq5qye0ni/pages/2687396/Exchange+Stream+API
"""

import ssl
import socket
import json
import threading
import logging
import time
from typing import Callable, Optional, Dict, Any


class BetfairStream:
    """Client for Betfair Exchange Stream API (Order and Market Stream)."""
    
    STREAM_HOST = "stream-api.betfair.com"
    STREAM_HOST_IT = "stream-api.betfair.it"
    STREAM_PORT = 443
    
    def __init__(self, app_key: str, session_token: str, use_italian_exchange: bool = True):
        """
        Initialize Betfair Stream client.
        
        Args:
            app_key: Betfair application key
            session_token: Valid session token from login
            use_italian_exchange: Use Italian exchange endpoint
        """
        self.app_key = app_key
        self.session_token = session_token
        self.host = self.STREAM_HOST_IT if use_italian_exchange else self.STREAM_HOST
        
        self.socket = None
        self.ssl_socket = None
        self.running = False
        self.connected = False
        self.authenticated = False
        
        self.message_id = 0
        self.connection_id = None
        
        # Order stream callbacks
        self.on_order_change: Optional[Callable[[Dict], None]] = None
        self.on_status_change: Optional[Callable[[str], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        
        # Market stream callbacks
        self.on_market_change: Optional[Callable[[Dict], None]] = None
        
        # Market data cache for delta processing
        self._market_cache: Dict[str, Dict] = {}
        self._subscribed_markets: list = []
        
        self._read_thread = None
        self._heartbeat_thread = None
        
    def set_callbacks(self, 
                      on_order_change: Callable[[Dict], None] = None,
                      on_status_change: Callable[[str], None] = None,
                      on_error: Callable[[str], None] = None,
                      on_market_change: Callable[[Dict], None] = None):
        """Set callback functions for stream events."""
        self.on_order_change = on_order_change
        self.on_status_change = on_status_change
        self.on_error = on_error
        self.on_market_change = on_market_change
    
    def _get_next_id(self) -> int:
        """Get next message ID."""
        self.message_id += 1
        return self.message_id
    
    def connect(self) -> bool:
        """Establish SSL connection to Betfair Stream API."""
        try:
            logging.info(f"Connecting to Betfair Stream: {self.host}:{self.STREAM_PORT}")
            
            context = ssl.create_default_context()
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(30)
            self.ssl_socket = context.wrap_socket(self.socket, server_hostname=self.host)
            self.ssl_socket.connect((self.host, self.STREAM_PORT))
            
            self.connected = True
            self.running = True
            
            self._read_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._read_thread.start()
            
            time.sleep(0.5)
            
            if self._authenticate():
                logging.info("Betfair Stream: Authentication successful")
                
                self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
                self._heartbeat_thread.start()
                
                return True
            else:
                logging.error("Betfair Stream: Authentication failed")
                self.disconnect()
                return False
                
        except Exception as e:
            logging.error(f"Betfair Stream connection error: {e}")
            self.disconnect()
            return False
    
    def disconnect(self):
        """Disconnect from stream."""
        self.running = False
        self.connected = False
        self.authenticated = False
        
        if self.ssl_socket:
            try:
                self.ssl_socket.close()
            except:
                pass
            self.ssl_socket = None
        
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None
        
        logging.info("Betfair Stream: Disconnected")
        
        if self.on_status_change:
            self.on_status_change("DISCONNECTED")
    
    def _send_message(self, message: Dict) -> bool:
        """Send JSON message to stream."""
        if not self.ssl_socket or not self.connected:
            return False
        
        try:
            json_str = json.dumps(message) + "\r\n"
            self.ssl_socket.sendall(json_str.encode('utf-8'))
            logging.debug(f"Stream sent: {message.get('op')}")
            return True
        except Exception as e:
            logging.error(f"Stream send error: {e}")
            return False
    
    def _authenticate(self) -> bool:
        """Send authentication message."""
        auth_msg = {
            "op": "authentication",
            "id": self._get_next_id(),
            "appKey": self.app_key,
            "session": self.session_token
        }
        
        if self._send_message(auth_msg):
            time.sleep(1)
            return self.authenticated
        return False
    
    def subscribe_orders(self) -> bool:
        """Subscribe to order changes."""
        if not self.authenticated:
            logging.warning("Stream: Cannot subscribe - not authenticated")
            return False
        
        sub_msg = {
            "op": "orderSubscription",
            "id": self._get_next_id(),
            "orderFilter": {},
            "initialClk": None,
            "clk": None
        }
        
        logging.info("Betfair Stream: Subscribing to orders")
        return self._send_message(sub_msg)
    
    def subscribe_markets(self, market_ids: list, fields: list = None) -> bool:
        """
        Subscribe to market data changes for real-time quotes.
        
        Args:
            market_ids: List of market IDs to subscribe to
            fields: Optional list of fields to include (default: best prices)
        """
        if not self.authenticated:
            logging.warning("Stream: Cannot subscribe to markets - not authenticated")
            return False
        
        if not market_ids:
            logging.warning("Stream: No market IDs provided")
            return False
        
        # Default fields for price streaming
        if fields is None:
            fields = ["EX_BEST_OFFERS", "EX_TRADED"]
        
        self._subscribed_markets = market_ids
        
        sub_msg = {
            "op": "marketSubscription",
            "id": self._get_next_id(),
            "marketFilter": {
                "marketIds": market_ids
            },
            "marketDataFilter": {
                "fields": fields,
                "ladderLevels": 3  # Get top 3 price levels
            },
            "conflateMs": 0  # No delay - instant updates for realtime trading
        }
        
        logging.info(f"Betfair Stream: Subscribing to markets: {market_ids}")
        return self._send_message(sub_msg)
    
    def unsubscribe_markets(self) -> bool:
        """Unsubscribe from all markets."""
        if not self.authenticated:
            return False
        
        # Send empty subscription to clear
        sub_msg = {
            "op": "marketSubscription",
            "id": self._get_next_id(),
            "marketFilter": {
                "marketIds": []
            }
        }
        
        self._subscribed_markets = []
        self._market_cache = {}
        
        logging.info("Betfair Stream: Unsubscribed from markets")
        return self._send_message(sub_msg)
    
    def _send_heartbeat(self):
        """Send heartbeat to keep connection alive."""
        hb_msg = {
            "op": "heartbeat",
            "id": self._get_next_id()
        }
        self._send_message(hb_msg)
    
    def _heartbeat_loop(self):
        """Send periodic heartbeats."""
        while self.running and self.connected:
            time.sleep(30)
            if self.running and self.connected:
                self._send_heartbeat()
    
    def _read_loop(self):
        """Read messages from stream."""
        buffer = ""
        
        while self.running and self.connected:
            try:
                self.ssl_socket.settimeout(60)
                data = self.ssl_socket.recv(4096)
                
                if not data:
                    logging.warning("Stream: Connection closed by server")
                    break
                
                buffer += data.decode('utf-8')
                
                while "\r\n" in buffer:
                    line, buffer = buffer.split("\r\n", 1)
                    if line.strip():
                        self._process_message(line)
                        
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    logging.error(f"Stream read error: {e}")
                break
        
        if self.running:
            self.disconnect()
    
    def _process_message(self, line: str):
        """Process received JSON message."""
        try:
            msg = json.loads(line)
            op = msg.get("op")
            
            if op == "connection":
                self.connection_id = msg.get("connectionId")
                logging.info(f"Stream socket connected: {self.connection_id}")
                # Don't notify CONNECTED yet - wait for authentication success
                    
            elif op == "status":
                status_code = msg.get("statusCode")
                if status_code == "SUCCESS":
                    self.authenticated = True
                    logging.info("Stream: Authenticated successfully")
                    # Notify CONNECTED only after authentication is complete
                    if self.on_status_change:
                        self.on_status_change("CONNECTED")
                else:
                    error_msg = msg.get("errorMessage", "Unknown error")
                    logging.error(f"Stream status error: {status_code} - {error_msg}")
                    if self.on_error:
                        self.on_error(f"{status_code}: {error_msg}")
                        
            elif op == "ocm":
                self._handle_order_change(msg)
                
            elif op == "mcm":
                self._handle_market_change(msg)
                
            else:
                logging.debug(f"Stream message: {op}")
                
        except json.JSONDecodeError as e:
            logging.error(f"Stream JSON error: {e}")
    
    def _handle_order_change(self, msg: Dict):
        """Handle OrderChangeMessage (ocm)."""
        try:
            oc = msg.get("oc", [])
            
            for market_orders in oc:
                market_id = market_orders.get("id")
                orders = market_orders.get("orc", [])
                
                for runner_orders in orders:
                    selection_id = runner_orders.get("id")
                    unmatched_orders = runner_orders.get("uo", [])
                    matched_orders = runner_orders.get("mo", [])
                    
                    for order in unmatched_orders:
                        order_data = {
                            "type": "UNMATCHED",
                            "market_id": market_id,
                            "selection_id": selection_id,
                            "bet_id": order.get("id"),
                            "price": order.get("p"),
                            "size": order.get("s"),
                            "side": order.get("side"),
                            "status": order.get("status"),
                            "size_matched": order.get("sm", 0),
                            "size_remaining": order.get("sr", 0),
                            "size_lapsed": order.get("sl", 0),
                            "size_cancelled": order.get("sc", 0),
                            "size_voided": order.get("sv", 0),
                            "placed_date": order.get("pd"),
                            "matched_date": order.get("md")
                        }
                        
                        logging.info(f"Order update: {order_data['bet_id']} - matched={order_data['size_matched']}")
                        
                        if self.on_order_change:
                            self.on_order_change(order_data)
                    
                    for order in matched_orders:
                        order_data = {
                            "type": "MATCHED",
                            "market_id": market_id,
                            "selection_id": selection_id,
                            "bet_id": order.get("id"),
                            "price": order.get("p"),
                            "size": order.get("s"),
                            "side": order.get("side")
                        }
                        
                        logging.info(f"Matched order: {order_data['bet_id']}")
                        
                        if self.on_order_change:
                            self.on_order_change(order_data)
                            
        except Exception as e:
            logging.error(f"Error handling order change: {e}")
    
    def _handle_market_change(self, msg: Dict):
        """
        Handle MarketChangeMessage (mcm) for real-time price updates.
        
        MCM format:
        {
            "op": "mcm",
            "ct": "SUB_IMAGE" | "RESUB_DELTA" | "HEARTBEAT",
            "mc": [
                {
                    "id": "1.234567",
                    "rc": [
                        {
                            "id": 12345,  # selection_id
                            "atb": [[1.5, 100], [1.48, 200]],  # available to back
                            "atl": [[1.52, 50], [1.55, 150]],  # available to lay
                            "trd": [[1.5, 500]],  # traded volume
                            "ltp": 1.5,  # last traded price
                            "tv": 1500  # total volume
                        }
                    ]
                }
            ]
        }
        """
        try:
            change_type = msg.get("ct", "")
            market_changes = msg.get("mc", [])
            
            for market in market_changes:
                market_id = market.get("id")
                if not market_id:
                    continue
                
                # Initialize cache for this market if needed
                if market_id not in self._market_cache:
                    self._market_cache[market_id] = {
                        "runners": {}
                    }
                
                market_cache = self._market_cache[market_id]
                
                # Market status
                market_status = market.get("marketStatus")
                if market_status:
                    market_cache["status"] = market_status
                
                # Runner changes
                runner_changes = market.get("rc", [])
                
                for rc in runner_changes:
                    selection_id = rc.get("id")
                    if not selection_id:
                        continue
                    
                    # Initialize runner cache
                    if selection_id not in market_cache["runners"]:
                        market_cache["runners"][selection_id] = {
                            "back": [],
                            "lay": [],
                            "ltp": None,
                            "tv": 0
                        }
                    
                    runner_cache = market_cache["runners"][selection_id]
                    
                    # Available to back (atb) - best prices to back
                    atb = rc.get("atb")
                    if atb is not None:
                        # Format: [[price, size], ...]
                        runner_cache["back"] = sorted(atb, key=lambda x: -x[0]) if atb else []
                    
                    # Available to lay (atl) - best prices to lay
                    atl = rc.get("atl")
                    if atl is not None:
                        runner_cache["lay"] = sorted(atl, key=lambda x: x[0]) if atl else []
                    
                    # Last traded price
                    ltp = rc.get("ltp")
                    if ltp is not None:
                        runner_cache["ltp"] = ltp
                    
                    # Total volume
                    tv = rc.get("tv")
                    if tv is not None:
                        runner_cache["tv"] = tv
                    
                    # Build update data for callback
                    best_back_price = runner_cache["back"][0][0] if runner_cache["back"] else None
                    best_back_size = runner_cache["back"][0][1] if runner_cache["back"] else 0
                    best_lay_price = runner_cache["lay"][0][0] if runner_cache["lay"] else None
                    best_lay_size = runner_cache["lay"][0][1] if runner_cache["lay"] else 0
                    
                    update_data = {
                        "market_id": market_id,
                        "selection_id": selection_id,
                        "back_price": best_back_price,
                        "back_size": best_back_size,
                        "lay_price": best_lay_price,
                        "lay_size": best_lay_size,
                        "ltp": runner_cache["ltp"],
                        "tv": runner_cache["tv"],
                        "back_prices": runner_cache["back"][:3],  # Top 3 levels
                        "lay_prices": runner_cache["lay"][:3]
                    }
                    
                    # Notify callback
                    if self.on_market_change:
                        self.on_market_change(update_data)
                        
        except Exception as e:
            logging.error(f"Error handling market change: {e}")
    
    def get_market_cache(self, market_id: str) -> Optional[Dict]:
        """Get cached market data."""
        return self._market_cache.get(market_id)
    
    def is_connected(self) -> bool:
        """Check if stream is connected and authenticated."""
        return self.connected and self.authenticated
