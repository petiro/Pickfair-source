"""
Betfair Dutching - Tutti i Mercati
Main application with CustomTkinter GUI for Windows desktop.
Supports all market types and Streaming API for real-time prices.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import customtkinter as ctk
import threading
import json
import logging
import os
import sys
from datetime import datetime

APP_NAME = "Pickfair"
APP_VERSION = "3.57.0"

# Setup file logging
def setup_logging():
    """Setup logging to file in APPDATA folder."""
    if os.name == 'nt':
        log_dir = os.path.join(os.environ.get('APPDATA', '.'), 'Pickfair', 'logs')
    else:
        log_dir = os.path.join(os.path.expanduser('~'), '.pickfair', 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'pickfair.log')
    
    # Rotate log if too large (>5MB)
    try:
        if os.path.exists(log_file) and os.path.getsize(log_file) > 5 * 1024 * 1024:
            backup = log_file + '.old'
            if os.path.exists(backup):
                os.remove(backup)
            os.rename(log_file, backup)
    except:
        pass
    
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logging.info(f"=== Pickfair v{APP_VERSION} started ===")
    logging.info(f"Log file: {log_file}")
    return log_file

LOG_FILE = setup_logging()

from database import Database
from betfair_client import BetfairClient, MARKET_TYPES
from storage import get_persistent_storage
from bet_logger import get_bet_logger
from betfair_stream import BetfairStream
from dutching import calculate_dutching_stakes, validate_selections, format_currency
from telegram_listener import TelegramListener, SignalQueue
from auto_updater import check_for_updates, show_update_dialog, DEFAULT_UPDATE_URL
from theme import COLORS, FONTS, configure_customtkinter, configure_ttk_dark_theme
from plugin_manager import PluginManager, PluginAPI, PluginInfo
from license_manager import get_hardware_id, is_licensed, activate_license, load_license

WINDOW_WIDTH = 1400
WINDOW_HEIGHT = 900

def check_single_instance():
    """Ensure only one instance of Pickfair is running using Windows mutex."""
    import os
    
    if os.name == 'nt':
        # Use Windows mutex for reliable single instance check
        try:
            import ctypes
            from ctypes import wintypes
            
            kernel32 = ctypes.windll.kernel32
            
            # CreateMutexW returns handle if created, or existing handle
            mutex_name = "Global\\PickfairSingleInstanceMutex"
            handle = kernel32.CreateMutexW(None, True, mutex_name)
            
            if handle == 0:
                logging.error("Failed to create mutex")
                return None
            
            # ERROR_ALREADY_EXISTS = 183
            last_error = kernel32.GetLastError()
            if last_error == 183:
                logging.warning("Another instance of Pickfair is already running (mutex exists)")
                kernel32.CloseHandle(handle)
                return None
            
            logging.info("Single instance check passed - mutex acquired")
            return handle  # Keep handle open
            
        except Exception as e:
            logging.error(f"Mutex check failed: {e}, trying fallback lock file")
            # Fall through to lock file method
    
    # Fallback: Lock file method for non-Windows or if mutex fails
    if os.name == 'nt':
        lock_dir = os.path.join(os.environ.get('APPDATA', '.'), 'Pickfair')
    else:
        lock_dir = os.path.join(os.path.expanduser('~'), '.pickfair')
    os.makedirs(lock_dir, exist_ok=True)
    lock_file = os.path.join(lock_dir, 'pickfair.lock')
    
    try:
        if os.name == 'nt':
            import msvcrt
            fd = os.open(lock_file, os.O_CREAT | os.O_RDWR)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                logging.info("Single instance check passed - lock file acquired")
                return fd
            except IOError:
                logging.warning("Another instance is running (lock file busy)")
                os.close(fd)
                return None
        else:
            import fcntl
            fd = open(lock_file, 'w')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                logging.info("Single instance check passed - flock acquired")
                return fd
            except IOError:
                logging.warning("Another instance is running (flock busy)")
                fd.close()
                return None
    except Exception as e:
        logging.error(f"Single instance check failed: {e}")
        return None  # Block execution on error instead of allowing

LIVE_REFRESH_INTERVAL = 2000  # 2 seconds for real-time odds


class PickfairApp:
    def __init__(self):
        configure_customtkinter()
        
        self.root = ctk.CTk()
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.configure(fg_color=COLORS['bg_dark'])
        
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        
        taskbar_offset = 50
        available_height = screen_height - taskbar_offset
        
        window_width = min(WINDOW_WIDTH, int(screen_width * 0.9))
        window_height = min(WINDOW_HEIGHT, int(available_height * 0.9))
        
        x = (screen_width - window_width) // 2
        y = max(0, (available_height - window_height) // 2)
        
        self.root.geometry(f"{window_width}x{window_height}+{x}+{y}")
        
        min_width = min(900, screen_width - 100)
        min_height = min(500, available_height - 50)
        self.root.minsize(min_width, min_height)
        
        self.root.resizable(True, True)
        
        if screen_width <= 1366 or screen_height <= 768:
            self.root.after(100, lambda: self._try_maximize())
        
        try:
            self.root.iconbitmap("icon.ico")
        except:
            pass
        
        self.db = Database()
        print(f"[DEBUG] Database path: {self.db.db_path}")
        
        self.persistent_storage = get_persistent_storage()
        self.bet_logger = get_bet_logger()
        logging.info(f"[STORAGE] Persistent storage initialized")
        
        self.client = None
        self.current_event = None
        self.current_market = None
        self.available_markets = []
        self.selected_runners = {}
        self.streaming_active = False
        self.live_mode = False
        self.live_refresh_id = None
        self.booking_monitor_id = None
        self.auto_cashout_monitor_id = None
        self.pending_bookings = []
        self.account_data = {'available': 0, 'exposure': 0, 'total': 0}
        self.telegram_listener = None
        self.telegram_signal_queue = SignalQueue()
        self.telegram_status = 'STOPPED'
        self.market_status = 'OPEN'
        self.simulation_mode = False  # Simulation mode flag
        self.recovered_positions = []  # Positions recovered from DB
        
        # License check
        self.is_app_licensed = is_licensed()
        
        if not self.is_app_licensed:
            self._create_activation_screen()
        else:
            self._initialize_full_app()
    
    def _try_maximize(self):
        """Try to maximize window on Windows."""
        try:
            self.root.state('zoomed')
        except:
            pass
    
    def _initialize_full_app(self):
        """Initialize the full application after license validation."""
        # Plugin system
        self.plugin_manager = PluginManager(self)
        self.plugin_tabs = {}
        
        self._create_menu()
        self._create_main_layout()
        self._load_settings()
        self._configure_styles()
        self._start_booking_monitor()
        self._start_auto_cashout_monitor()
        self._start_settlement_monitor()
        self._check_for_updates_on_startup()
        
        # Recovery: restore open positions from database
        self._recover_open_positions()
        
        # Telegram listener: manual start only (user preference)
        # self.root.after(2000, self._auto_start_telegram_listener)
    
    def _recover_open_positions(self):
        """Recover open positions from database after restart."""
        try:
            positions = self.bet_logger.get_open_positions()
            if positions:
                logging.info(f"[RECOVERY] Found {len(positions)} open positions to recover")
                self.recovered_positions = positions
                for pos in positions:
                    logging.info(f"[RECOVERY] - {pos['side']} {pos['stake']}@{pos['price']} on {pos['market_id']}")
            else:
                logging.info("[RECOVERY] No open positions to recover")
        except Exception as e:
            logging.error(f"[RECOVERY] Failed to recover positions: {e}")
    
    def _create_activation_screen(self):
        """Create the license activation screen."""
        self.activation_frame = ctk.CTkFrame(self.root, fg_color=COLORS['bg_dark'])
        self.activation_frame.pack(fill='both', expand=True)
        
        center_frame = ctk.CTkFrame(self.activation_frame, fg_color=COLORS['bg_card'], corner_radius=15)
        center_frame.place(relx=0.5, rely=0.5, anchor='center')
        
        title_label = ctk.CTkLabel(
            center_frame,
            text="Attivazione Licenza",
            font=ctk.CTkFont(size=28, weight="bold"),
            text_color=COLORS['text']
        )
        title_label.pack(pady=(40, 10))
        
        subtitle_label = ctk.CTkLabel(
            center_frame,
            text="Inserisci la chiave di licenza per attivare Pickfair",
            font=ctk.CTkFont(size=14),
            text_color=COLORS['text_secondary']
        )
        subtitle_label.pack(pady=(0, 30))
        
        hwid_label = ctk.CTkLabel(
            center_frame,
            text="Il tuo Hardware ID:",
            font=ctk.CTkFont(size=12),
            text_color=COLORS['text_secondary']
        )
        hwid_label.pack(anchor='w', padx=40)
        
        hardware_id = get_hardware_id()
        
        hwid_frame = ctk.CTkFrame(center_frame, fg_color=COLORS['bg_hover'], corner_radius=8)
        hwid_frame.pack(fill='x', padx=40, pady=(5, 5))
        
        self.hwid_display = ctk.CTkLabel(
            hwid_frame,
            text=hardware_id,
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLORS['primary']
        )
        self.hwid_display.pack(pady=10, padx=15)
        
        copy_hwid_btn = ctk.CTkButton(
            center_frame,
            text="Copia Hardware ID",
            command=lambda: self._copy_to_clipboard(hardware_id),
            width=200,
            height=32,
            font=ctk.CTkFont(size=12),
            fg_color=COLORS['bg_hover'],
            hover_color=COLORS['border']
        )
        copy_hwid_btn.pack(pady=(0, 25))
        
        license_label = ctk.CTkLabel(
            center_frame,
            text="Chiave di Licenza:",
            font=ctk.CTkFont(size=12),
            text_color=COLORS['text_secondary']
        )
        license_label.pack(anchor='w', padx=40)
        
        self.license_entry = ctk.CTkEntry(
            center_frame,
            placeholder_text="PICK-XXXX-XXXX-XXXX-XXXX-XXXX",
            width=400,
            height=45,
            font=ctk.CTkFont(size=14)
        )
        self.license_entry.pack(pady=(5, 20), padx=40)
        
        self.activation_status = ctk.CTkLabel(
            center_frame,
            text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS['loss']
        )
        self.activation_status.pack(pady=(0, 10))
        
        activate_btn = ctk.CTkButton(
            center_frame,
            text="Attiva Licenza",
            command=self._attempt_activation,
            width=250,
            height=50,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color=COLORS['success'],
            hover_color='#059669'
        )
        activate_btn.pack(pady=(10, 40))
        
        info_label = ctk.CTkLabel(
            center_frame,
            text="Invia il tuo Hardware ID al venditore per ricevere la chiave di licenza",
            font=ctk.CTkFont(size=11),
            text_color=COLORS['text_secondary']
        )
        info_label.pack(pady=(0, 20))
    
    def _copy_to_clipboard(self, text):
        """Copy text to clipboard."""
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        messagebox.showinfo("Copiato", "Hardware ID copiato negli appunti!")
    
    def _attempt_activation(self):
        """Attempt to activate the license."""
        license_key = self.license_entry.get().strip().upper()
        
        if not license_key:
            self.activation_status.configure(text="Inserisci una chiave di licenza", text_color=COLORS['loss'])
            return
        
        success, message = activate_license(license_key)
        
        if success:
            self.activation_status.configure(text=message, text_color=COLORS['success'])
            self.is_app_licensed = True
            self.root.after(1500, self._switch_to_full_app)
        else:
            self.activation_status.configure(text=message, text_color=COLORS['loss'])
    
    def _switch_to_full_app(self):
        """Switch from activation screen to full app."""
        self.activation_frame.destroy()
        self._initialize_full_app()
    
    def _configure_styles(self):
        """Configure ttk styles for dark theme."""
        style = ttk.Style()
        configure_ttk_dark_theme(style)
    
    def _create_menu(self):
        """Create application menu."""
        menubar = tk.Menu(self.root)
        self.root.configure(menu=menubar)
        
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Configura Credenziali", command=self._show_credentials_dialog)
        file_menu.add_command(label="Configura Aggiornamenti", command=self._show_update_settings_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="Verifica Aggiornamenti", command=self._check_for_updates_manual)
        file_menu.add_separator()
        file_menu.add_command(label="Esci", command=self._on_close)
        
        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Strumenti", menu=tools_menu)
        tools_menu.add_command(label="Multi-Market Monitor", command=self._show_multi_market_monitor)
        tools_menu.add_command(label="Filtri Avanzati", command=self._show_advanced_filters)
        tools_menu.add_separator()
        tools_menu.add_command(label="Dashboard Simulazione", command=self._show_simulation_dashboard)
        tools_menu.add_command(label="Reset Simulazione", command=self._reset_simulation)
        
        telegram_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Telegram", menu=telegram_menu)
        telegram_menu.add_command(label="Configura Telegram", command=self._show_telegram_settings)
        telegram_menu.add_command(label="Segnali Ricevuti", command=self._show_telegram_signals)
        telegram_menu.add_separator()
        telegram_menu.add_command(label="Avvia Listener", command=self._start_telegram_listener)
        telegram_menu.add_command(label="Ferma Listener", command=self._stop_telegram_listener)
        
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Aiuto", menu=help_menu)
        help_menu.add_command(label="Informazioni", command=self._show_about)
        
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
    
    def _on_close(self):
        """Handle window close."""
        self._stop_auto_refresh()
        
        # Auto-stop Telegram listener if enabled
        settings = self.db.get_telegram_settings()
        if settings and settings.get('auto_stop_listener', 1) and self.telegram_listener:
            try:
                self.telegram_listener.stop()
                self.telegram_listener = None
            except:
                pass
        
        if self.client:
            self.client.logout()
        self.root.destroy()
    
    def _create_main_layout(self):
        """Create main application layout with tabs."""
        self.main_frame = ctk.CTkFrame(self.root, fg_color=COLORS['bg_dark'])
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        self._create_status_bar()
        
        self.main_notebook = ctk.CTkTabview(self.main_frame, 
                                            fg_color=COLORS['bg_surface'],
                                            segmented_button_fg_color=COLORS['bg_panel'],
                                            segmented_button_selected_color=COLORS['back'],
                                            segmented_button_unselected_color=COLORS['bg_panel'],
                                            text_color=COLORS['text_primary'])
        self.main_notebook.pack(fill=tk.BOTH, expand=True, pady=10)
        
        self.main_notebook.add("Trading")
        self.main_notebook.add("Dashboard")
        self.main_notebook.add("Telegram")
        self.main_notebook.add("Strumenti")
        self.main_notebook.add("Plugin")
        self.main_notebook.add("Impostazioni")
        self.main_notebook.add("Simulazione")
        
        self.trading_tab = self.main_notebook.tab("Trading")
        self.dashboard_tab = self.main_notebook.tab("Dashboard")
        self.telegram_tab = self.main_notebook.tab("Telegram")
        self.strumenti_tab = self.main_notebook.tab("Strumenti")
        self.plugin_tab = self.main_notebook.tab("Plugin")
        self.impostazioni_tab = self.main_notebook.tab("Impostazioni")
        self.simulazione_tab = self.main_notebook.tab("Simulazione")
        
        self._create_events_panel(self.trading_tab)
        self._create_market_panel(self.trading_tab)
        self._create_dutching_panel(self.trading_tab)
        
        self._create_dashboard_tab()
        self._create_telegram_tab()
        self._create_strumenti_tab()
        self._create_plugin_tab()
        self._create_impostazioni_tab()
        self._create_simulazione_tab()
    
    def _create_status_bar(self):
        """Create status bar with connection info and mode buttons."""
        status_frame = ctk.CTkFrame(self.main_frame, fg_color=COLORS['bg_panel'], corner_radius=8, height=50)
        status_frame.pack(fill=tk.X, pady=(0, 10))
        status_frame.pack_propagate(False)
        
        self.status_label = ctk.CTkLabel(status_frame, text="Non connesso", 
                                         text_color=COLORS['error'], font=FONTS['default'])
        self.status_label.pack(side=tk.LEFT, padx=15)
        
        self.balance_label = ctk.CTkLabel(status_frame, text="", 
                                          text_color=COLORS['back'], font=('Segoe UI', 12, 'bold'))
        self.balance_label.pack(side=tk.LEFT, padx=20)
        
        self.stream_label = ctk.CTkLabel(status_frame, text="", 
                                         text_color=COLORS['warning'], font=FONTS['default'])
        self.stream_label.pack(side=tk.LEFT, padx=10)
        
        self.connect_btn = ctk.CTkButton(status_frame, text="Connetti", 
                                         command=self._toggle_connection,
                                         fg_color=COLORS['button_primary'],
                                         hover_color=COLORS['back_hover'],
                                         corner_radius=6, width=100)
        self.connect_btn.pack(side=tk.RIGHT, padx=10)
        
        self.refresh_btn = ctk.CTkButton(status_frame, text="Aggiorna", 
                                         command=self._refresh_data, state=tk.DISABLED,
                                         fg_color=COLORS['button_secondary'],
                                         corner_radius=6, width=100)
        self.refresh_btn.pack(side=tk.RIGHT, padx=5)
        
        self.live_btn = ctk.CTkButton(status_frame, text="LIVE",
                                      fg_color=COLORS['loss'], hover_color='#c62828',
                                      command=self._toggle_live_mode,
                                      corner_radius=6, width=80)
        self.live_btn.pack(side=tk.RIGHT, padx=5)
        
        self.sim_btn = ctk.CTkButton(status_frame, text="SIMULAZIONE",
                                     fg_color=COLORS['button_secondary'], hover_color=COLORS['bg_hover'],
                                     command=self._toggle_simulation_mode,
                                     corner_radius=6, width=120)
        self.sim_btn.pack(side=tk.RIGHT, padx=5)
        
        self.sim_balance_label = ctk.CTkLabel(status_frame, text="", 
                                              text_color='#9c27b0', font=('Segoe UI', 11, 'bold'))
        self.sim_balance_label.pack(side=tk.LEFT, padx=10)
    
    def _create_events_panel(self, parent):
        """Create events list panel with country grouping."""
        events_frame = ctk.CTkFrame(parent, fg_color=COLORS['bg_panel'], corner_radius=8)
        events_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        
        # Title label (replaces LabelFrame text)
        ctk.CTkLabel(events_frame, text="Partite", font=FONTS['heading'], 
                     text_color=COLORS['text_primary']).pack(anchor=tk.W, padx=10, pady=(10, 5))
        
        search_frame = ctk.CTkFrame(events_frame, fg_color='transparent')
        search_frame.pack(fill=tk.X, padx=10, pady=(0, 5))
        
        self.search_var = tk.StringVar()
        self.search_var.trace_add('write', self._filter_events)
        search_entry = ctk.CTkEntry(search_frame, textvariable=self.search_var, 
                                    placeholder_text="Cerca partita...",
                                    fg_color=COLORS['bg_card'], border_color=COLORS['border'])
        search_entry.pack(fill=tk.X)
        
        # Auto-refresh controls (in seconds for faster updates)
        auto_refresh_frame = ctk.CTkFrame(events_frame, fg_color='transparent')
        auto_refresh_frame.pack(fill=tk.X, padx=10, pady=(5, 5))
        
        self.auto_refresh_var = tk.BooleanVar(value=True)  # Enabled by default
        self.auto_refresh_check = ctk.CTkCheckBox(
            auto_refresh_frame,
            text="Auto-refresh ogni",
            variable=self.auto_refresh_var,
            command=self._toggle_auto_refresh,
            fg_color=COLORS['back'], hover_color=COLORS['back_hover'],
            text_color=COLORS['text_primary']
        )
        self.auto_refresh_check.pack(side=tk.LEFT)
        
        self.auto_refresh_interval_var = tk.StringVar(value="30")  # 30 seconds default
        self.auto_refresh_interval = ctk.CTkOptionMenu(
            auto_refresh_frame,
            variable=self.auto_refresh_interval_var,
            values=["15", "30", "60", "120", "300"],
            width=60,
            fg_color=COLORS['bg_card'], button_color=COLORS['back'],
            button_hover_color=COLORS['back_hover'],
            command=lambda v: self._on_auto_refresh_interval_change(None)
        )
        self.auto_refresh_interval.pack(side=tk.LEFT, padx=5)
        
        ctk.CTkLabel(auto_refresh_frame, text="sec", text_color=COLORS['text_secondary']).pack(side=tk.LEFT)
        
        self.auto_refresh_status = ctk.CTkLabel(auto_refresh_frame, text="", text_color=COLORS['success'])
        self.auto_refresh_status.pack(side=tk.LEFT, padx=10)
        
        # Hierarchical tree: Country -> Matches (Treeview remains ttk)
        tree_container = ctk.CTkFrame(events_frame, fg_color='transparent')
        tree_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        
        columns = ('name', 'date')
        self.events_tree = ttk.Treeview(tree_container, columns=columns, show='tree headings', height=20)
        self.events_tree.heading('#0', text='Nazione')
        self.events_tree.heading('name', text='Partita')
        self.events_tree.heading('date', text='Data')
        self.events_tree.column('#0', width=80, minwidth=60)
        self.events_tree.column('name', width=150, minwidth=100)
        self.events_tree.column('date', width=70, minwidth=60)
        
        scrollbar = ttk.Scrollbar(tree_container, orient=tk.VERTICAL, command=self.events_tree.yview)
        self.events_tree.configure(yscrollcommand=scrollbar.set)
        
        self.events_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.events_tree.bind('<<TreeviewSelect>>', self._on_event_selected)
        
        self.all_events = []
        self.auto_refresh_id = None
    
    def _create_market_panel(self, parent):
        """Create market/runners panel with market type selector."""
        market_frame = ctk.CTkFrame(parent, fg_color=COLORS['bg_panel'], corner_radius=8)
        market_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        
        # Title
        ctk.CTkLabel(market_frame, text="Mercato", font=FONTS['heading'],
                     text_color=COLORS['text_primary']).pack(anchor=tk.W, padx=10, pady=(10, 5))
        
        header_frame = ctk.CTkFrame(market_frame, fg_color='transparent')
        header_frame.pack(fill=tk.X, padx=10, pady=(0, 5))
        
        self.event_name_label = ctk.CTkLabel(header_frame, text="Seleziona una partita", 
                                             font=('Segoe UI', 12, 'bold'),
                                             text_color=COLORS['text_primary'])
        self.event_name_label.pack(anchor=tk.W)
        
        selector_frame = ctk.CTkFrame(market_frame, fg_color='transparent')
        selector_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ctk.CTkLabel(selector_frame, text="Tipo Mercato:", text_color=COLORS['text_secondary']).pack(side=tk.LEFT)
        self.market_type_var = tk.StringVar()
        self.market_combo = ctk.CTkOptionMenu(
            selector_frame, 
            variable=self.market_type_var,
            values=[""],
            width=200,
            fg_color=COLORS['bg_card'], button_color=COLORS['back'],
            button_hover_color=COLORS['back_hover'],
            command=lambda v: self._on_market_type_selected(None)
        )
        self.market_combo.pack(side=tk.LEFT, padx=5)
        
        stream_frame = ctk.CTkFrame(market_frame, fg_color='transparent')
        stream_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.stream_var = tk.BooleanVar(value=False)
        self.stream_check = ctk.CTkCheckBox(
            stream_frame, 
            text="Streaming Quote Live", 
            variable=self.stream_var,
            command=self._toggle_streaming,
            fg_color=COLORS['back'], hover_color=COLORS['back_hover'],
            text_color=COLORS['text_primary']
        )
        self.stream_check.pack(side=tk.LEFT)
        
        # Dutching modal button
        self.dutch_modal_btn = ctk.CTkButton(
            stream_frame, 
            text="Dutching Avanzato", 
            fg_color=COLORS['info'], hover_color=COLORS['info_hover'],
            command=self._show_dutching_modal,
            state=tk.DISABLED,
            corner_radius=6, width=130
        )
        self.dutch_modal_btn.pack(side=tk.LEFT, padx=5)
        
        # Market status indicator
        self.market_status_label = ctk.CTkLabel(
            stream_frame,
            text="",
            font=('Segoe UI', 9, 'bold')
        )
        self.market_status_label.pack(side=tk.RIGHT, padx=10)
        
        # Runners tree container
        runners_container = ctk.CTkFrame(market_frame, fg_color='transparent')
        runners_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        
        columns = ('select', 'name', 'back', 'back_size', 'lay', 'lay_size')
        self.runners_tree = ttk.Treeview(runners_container, columns=columns, show='headings', height=18)
        self.runners_tree.heading('select', text='')
        self.runners_tree.heading('name', text='Selezione')
        self.runners_tree.heading('back', text='Back')
        self.runners_tree.heading('back_size', text='Disp.')
        self.runners_tree.heading('lay', text='Lay')
        self.runners_tree.heading('lay_size', text='Disp.')
        self.runners_tree.column('select', width=30)
        self.runners_tree.column('name', width=120)
        self.runners_tree.column('back', width=60)
        self.runners_tree.column('back_size', width=60)
        self.runners_tree.column('lay', width=60)
        self.runners_tree.column('lay_size', width=60)
        
        # Configure row tags for FairBot-style coloring
        self.runners_tree.tag_configure('runner_row', background=COLORS['bg_card'])
        
        scrollbar = ttk.Scrollbar(runners_container, orient=tk.VERTICAL, command=self.runners_tree.yview)
        self.runners_tree.configure(yscrollcommand=scrollbar.set)
        
        self.runners_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.runners_tree.bind('<ButtonRelease-1>', self._on_runner_clicked)
        self.runners_tree.bind('<Button-3>', self._show_runner_context_menu)  # Right-click
        
        # Style for odds cells to show they're clickable
        self.runners_tree.tag_configure('clickable_back', foreground=COLORS['clickable_back'])
        self.runners_tree.tag_configure('clickable_lay', foreground=COLORS['clickable_lay'])
        
        # Price movement highlight tags (real-time visual feedback)
        self.runners_tree.tag_configure('price_up', background='#1a472a')  # Dark green highlight
        self.runners_tree.tag_configure('price_down', background='#4a1a1a')  # Dark red highlight
        
        # Context menu for runners
        self.runner_context_menu = tk.Menu(self.root, tearoff=0)
        self.runner_context_menu.add_command(label="Prenota Scommessa", command=self._book_selected_runner)
        self.runner_context_menu.add_separator()
        self.runner_context_menu.add_command(label="Seleziona per Dutching", command=lambda: None)
    
    def _create_dutching_panel(self, parent):
        """Create dutching calculator panel with scrollable content."""
        dutch_outer = ctk.CTkFrame(parent, fg_color=COLORS['bg_panel'], corner_radius=8)
        dutch_outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0))
        
        # Title
        ctk.CTkLabel(dutch_outer, text="Calcolo Dutching", font=FONTS['heading'],
                     text_color=COLORS['text_primary']).pack(anchor=tk.W, padx=10, pady=(10, 5))
        
        # ========== QUICK BET PANEL (fixed position, always visible) ==========
        self.quick_bet_frame = ctk.CTkFrame(dutch_outer, fg_color=COLORS['bg_card'], corner_radius=8)
        # Hidden by default - don't pack yet
        
        # Quick bet title
        qb_title_frame = ctk.CTkFrame(self.quick_bet_frame, fg_color='transparent')
        qb_title_frame.pack(fill=tk.X, padx=10, pady=(10, 5))
        
        ctk.CTkLabel(qb_title_frame, text="Scommessa Rapida", font=('Segoe UI', 11, 'bold'),
                     text_color=COLORS['text_primary']).pack(side=tk.LEFT)
        
        ctk.CTkButton(qb_title_frame, text="X", width=30, height=24,
                      fg_color=COLORS['button_secondary'], hover_color=COLORS['bg_hover'],
                      command=self._hide_quick_bet_panel).pack(side=tk.RIGHT)
        
        self.qb_selection_label = ctk.CTkLabel(self.quick_bet_frame, text="Selezione: -",
                                                font=('Segoe UI', 10, 'bold'),
                                                text_color=COLORS['text_primary'])
        self.qb_selection_label.pack(anchor=tk.W, padx=10, pady=2)
        
        qb_type_frame = ctk.CTkFrame(self.quick_bet_frame, fg_color='transparent')
        qb_type_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ctk.CTkLabel(qb_type_frame, text="Tipo:", text_color=COLORS['text_secondary']).pack(side=tk.LEFT)
        
        self.qb_bet_type_var = tk.StringVar(value='BACK')
        self.qb_back_btn = ctk.CTkButton(qb_type_frame, text="Back", 
                                          fg_color=COLORS['back'], hover_color=COLORS['back_hover'],
                                          corner_radius=6, width=80,
                                          command=lambda: self._set_qb_type('BACK'))
        self.qb_back_btn.pack(side=tk.LEFT, padx=5)
        
        self.qb_lay_btn = ctk.CTkButton(qb_type_frame, text="Lay", 
                                         fg_color=COLORS['lay'], hover_color=COLORS['lay_hover'],
                                         corner_radius=6, width=80,
                                         command=lambda: self._set_qb_type('LAY'))
        self.qb_lay_btn.pack(side=tk.LEFT)
        
        qb_odds_frame = ctk.CTkFrame(self.quick_bet_frame, fg_color='transparent')
        qb_odds_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ctk.CTkLabel(qb_odds_frame, text="Quota:", text_color=COLORS['text_secondary']).pack(side=tk.LEFT)
        self.qb_odds_var = tk.StringVar(value='1.00')
        self.qb_odds_entry = ctk.CTkEntry(qb_odds_frame, textvariable=self.qb_odds_var, width=80,
                                           fg_color=COLORS['bg_panel'], border_color=COLORS['border'])
        self.qb_odds_entry.pack(side=tk.LEFT, padx=5)
        
        self.qb_live_odds_label = ctk.CTkLabel(qb_odds_frame, text="(Live: -)", 
                                                font=('Segoe UI', 9),
                                                text_color=COLORS['text_tertiary'])
        self.qb_live_odds_label.pack(side=tk.LEFT, padx=5)
        
        ctk.CTkButton(qb_odds_frame, text="Usa Live", width=70,
                      fg_color=COLORS['button_secondary'], hover_color=COLORS['bg_hover'],
                      corner_radius=6, command=self._use_live_odds).pack(side=tk.LEFT, padx=2)
        
        qb_stake_frame = ctk.CTkFrame(self.quick_bet_frame, fg_color='transparent')
        qb_stake_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ctk.CTkLabel(qb_stake_frame, text="Stake (EUR):", text_color=COLORS['text_secondary']).pack(side=tk.LEFT)
        self.qb_stake_var = tk.StringVar(value='1.00')
        self.qb_stake_entry = ctk.CTkEntry(qb_stake_frame, textvariable=self.qb_stake_var, width=80,
                                            fg_color=COLORS['bg_panel'], border_color=COLORS['border'])
        self.qb_stake_entry.pack(side=tk.LEFT, padx=5)
        
        ctk.CTkLabel(qb_stake_frame, text="(min. 1 EUR)", 
                     font=('Segoe UI', 8), text_color=COLORS['text_tertiary']).pack(side=tk.LEFT, padx=5)
        
        self.qb_pl_label = ctk.CTkLabel(self.quick_bet_frame, text="P/L Potenziale: -",
                                         font=('Segoe UI', 10),
                                         text_color=COLORS['success'])
        self.qb_pl_label.pack(anchor=tk.W, padx=10, pady=5)
        
        self.qb_mode_label = ctk.CTkLabel(self.quick_bet_frame, text="",
                                           font=('Segoe UI', 9, 'bold'),
                                           text_color=COLORS['warning'])
        self.qb_mode_label.pack(anchor=tk.W, padx=10)
        
        qb_btn_frame = ctk.CTkFrame(self.quick_bet_frame, fg_color='transparent')
        qb_btn_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ctk.CTkButton(qb_btn_frame, text="Annulla", 
                      fg_color=COLORS['button_secondary'], hover_color=COLORS['bg_hover'],
                      corner_radius=6, command=self._hide_quick_bet_panel).pack(side=tk.LEFT, padx=2)
        
        self.qb_confirm_btn = ctk.CTkButton(qb_btn_frame, text="PIAZZA SCOMMESSA", 
                                            fg_color=COLORS['button_success'], hover_color='#4caf50',
                                            font=('Segoe UI', 10, 'bold'),
                                            corner_radius=6, command=self._confirm_quick_bet)
        self.qb_confirm_btn.pack(side=tk.RIGHT, padx=2)
        
        self.qb_current_runner = None
        self.qb_current_selection_id = None
        self.qb_live_update_id = None
        
        # Create scrollable canvas
        canvas = tk.Canvas(dutch_outer, highlightthickness=0, bg=COLORS['bg_panel'])
        scrollbar = ttk.Scrollbar(dutch_outer, orient=tk.VERTICAL, command=canvas.yview)
        dutch_frame = ctk.CTkFrame(canvas, fg_color='transparent')
        
        # Configure canvas scrolling
        def configure_scroll(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        
        dutch_frame.bind('<Configure>', configure_scroll)
        canvas_window = canvas.create_window((0, 0), window=dutch_frame, anchor='nw')
        
        # Make canvas resize with window
        def configure_canvas(event):
            canvas.itemconfig(canvas_window, width=event.width)
        canvas.bind('<Configure>', configure_canvas)
        
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Enable mousewheel scrolling only when mouse is over this panel
        def on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        
        def bind_mousewheel(event):
            canvas.bind_all('<MouseWheel>', on_mousewheel)
        
        def unbind_mousewheel(event):
            canvas.unbind_all('<MouseWheel>')
        
        canvas.bind('<Enter>', bind_mousewheel)
        canvas.bind('<Leave>', unbind_mousewheel)
        dutch_frame.bind('<Enter>', bind_mousewheel)
        dutch_frame.bind('<Leave>', unbind_mousewheel)
        
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        type_frame = ctk.CTkFrame(dutch_frame, fg_color='transparent')
        type_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ctk.CTkLabel(type_frame, text="Tipo:", text_color=COLORS['text_secondary']).pack(side=tk.LEFT)
        self.bet_type_var = tk.StringVar(value='BACK')
        
        # Blue button for BACK
        self.back_btn = ctk.CTkButton(type_frame, text="Back", 
                                      fg_color=COLORS['back'], hover_color=COLORS['back_hover'],
                                      corner_radius=6, width=80,
                                      command=lambda: self._set_bet_type('BACK'))
        self.back_btn.pack(side=tk.LEFT, padx=5)
        
        # Pink button for LAY (banca)
        self.lay_btn = ctk.CTkButton(type_frame, text="Lay", 
                                     fg_color=COLORS['lay'], hover_color=COLORS['lay_hover'],
                                     corner_radius=6, width=80,
                                     command=lambda: self._set_bet_type('LAY'))
        self.lay_btn.pack(side=tk.LEFT)
        
        stake_frame = ctk.CTkFrame(dutch_frame, fg_color='transparent')
        stake_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ctk.CTkLabel(stake_frame, text="Stake Totale (EUR):", text_color=COLORS['text_secondary']).pack(side=tk.LEFT)
        self.stake_var = tk.StringVar(value='1.00')
        self.stake_var.trace_add('write', lambda *args: self._recalculate())
        stake_entry = ctk.CTkEntry(stake_frame, textvariable=self.stake_var, width=80,
                                   fg_color=COLORS['bg_card'], border_color=COLORS['border'])
        stake_entry.pack(side=tk.LEFT, padx=5)
        
        # Note about minimum stake
        ctk.CTkLabel(stake_frame, text="(min. 1 EUR per selezione)", 
                     font=('Segoe UI', 8), text_color=COLORS['text_tertiary']).pack(side=tk.LEFT, padx=5)
        
        # Best price option
        options_frame = ctk.CTkFrame(dutch_frame, fg_color='transparent')
        options_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.best_price_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(options_frame, text="Accetta Miglior Prezzo", 
                        variable=self.best_price_var,
                        fg_color=COLORS['back'], hover_color=COLORS['back_hover'],
                        text_color=COLORS['text_primary']).pack(side=tk.LEFT)
        ctk.CTkLabel(options_frame, text="(piazza al prezzo corrente)", 
                     font=('Segoe UI', 8), text_color=COLORS['text_tertiary']).pack(side=tk.LEFT, padx=5)
        
        ctk.CTkLabel(dutch_frame, text="Selezioni:", font=('Segoe UI', 11, 'bold'),
                     text_color=COLORS['text_primary']).pack(anchor=tk.W, padx=10, pady=(10, 5))
        
        self.selections_text = ctk.CTkTextbox(dutch_frame, height=100, 
                                               fg_color=COLORS['bg_card'], 
                                               text_color=COLORS['text_primary'],
                                               border_color=COLORS['border'])
        self.selections_text.pack(fill=tk.BOTH, expand=True, padx=10)
        self.selections_text.configure(state=tk.DISABLED)
        
        # Placed bets for current market
        ctk.CTkLabel(dutch_frame, text="Scommesse Piazzate:", font=('Segoe UI', 11, 'bold'),
                     text_color=COLORS['text_primary']).pack(anchor=tk.W, padx=10, pady=(10, 2))
        
        placed_cols = ('sel', 'tipo', 'quota', 'stake')
        self.placed_bets_tree = ttk.Treeview(dutch_frame, columns=placed_cols, show='headings', height=4)
        self.placed_bets_tree.heading('sel', text='Selezione')
        self.placed_bets_tree.heading('tipo', text='Tipo')
        self.placed_bets_tree.heading('quota', text='Quota')
        self.placed_bets_tree.heading('stake', text='Stake')
        self.placed_bets_tree.column('sel', width=100)
        self.placed_bets_tree.column('tipo', width=40)
        self.placed_bets_tree.column('quota', width=50)
        self.placed_bets_tree.column('stake', width=50)
        
        self.placed_bets_tree.tag_configure('back', foreground=COLORS['back'])
        self.placed_bets_tree.tag_configure('lay', foreground=COLORS['lay'])
        
        self.placed_bets_tree.pack(fill=tk.X, padx=10, pady=2)
        
        summary_frame = ctk.CTkFrame(dutch_frame, fg_color='transparent')
        summary_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.profit_label = ctk.CTkLabel(summary_frame, text="Profitto: -", 
                                         font=('Segoe UI', 11, 'bold'),
                                         text_color=COLORS['text_primary'])
        self.profit_label.pack(anchor=tk.W)
        
        self.prob_label = ctk.CTkLabel(summary_frame, text="Probabilita Implicita: -",
                                       text_color=COLORS['text_secondary'])
        self.prob_label.pack(anchor=tk.W)
        
        btn_frame = ctk.CTkFrame(dutch_frame, fg_color='transparent')
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ctk.CTkButton(btn_frame, text="Cancella Selezioni", command=self._clear_selections,
                      fg_color=COLORS['button_secondary'], hover_color=COLORS['bg_hover'],
                      corner_radius=6).pack(side=tk.LEFT)
        self.place_btn = ctk.CTkButton(btn_frame, text="Piazza Scommesse", command=self._place_bets, 
                                       state=tk.DISABLED,
                                       fg_color=COLORS['button_success'], hover_color='#4caf50',
                                       corner_radius=6)
        self.place_btn.pack(side=tk.RIGHT)
        
        # Separator before cashout section
        separator = ctk.CTkFrame(dutch_frame, fg_color=COLORS['border'], height=2)
        separator.pack(fill=tk.X, padx=10, pady=10)
        
        # Cashout section in main panel
        ctk.CTkLabel(dutch_frame, text="Cashout", font=('Segoe UI', 11, 'bold'),
                     text_color=COLORS['text_primary']).pack(anchor=tk.W, padx=10, pady=(5, 2))
        
        # Cashout positions list
        cashout_cols = ('sel', 'tipo', 'p/l')
        self.market_cashout_tree = ttk.Treeview(dutch_frame, columns=cashout_cols, show='headings', height=4)
        self.market_cashout_tree.heading('sel', text='Selezione')
        self.market_cashout_tree.heading('tipo', text='Tipo')
        self.market_cashout_tree.heading('p/l', text='P/L')
        self.market_cashout_tree.column('sel', width=80)
        self.market_cashout_tree.column('tipo', width=40)
        self.market_cashout_tree.column('p/l', width=60)
        
        self.market_cashout_tree.tag_configure('profit', foreground=COLORS['success'])
        self.market_cashout_tree.tag_configure('loss', foreground=COLORS['loss'])
        
        self.market_cashout_tree.pack(fill=tk.X, padx=10, pady=2)
        
        # Cashout buttons
        cashout_btn_frame = ctk.CTkFrame(dutch_frame, fg_color='transparent')
        cashout_btn_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.market_cashout_btn = ctk.CTkButton(cashout_btn_frame, text="CASHOUT", 
                                               fg_color=COLORS['success'], hover_color='#0d9668',
                                               font=('Segoe UI', 9, 'bold'), state=tk.DISABLED,
                                               corner_radius=6, width=100,
                                               command=self._do_market_cashout)
        self.market_cashout_btn.pack(side=tk.LEFT, padx=2)
        
        # Auto-confirm checkbox (skip confirmation dialog)
        self.auto_cashout_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(cashout_btn_frame, text="Auto", variable=self.auto_cashout_var,
                        fg_color=COLORS['back'], hover_color=COLORS['back_hover'],
                        text_color=COLORS['text_primary'], width=60).pack(side=tk.LEFT, padx=5)
        
        self.market_live_tracking_var = tk.BooleanVar(value=True)  # Auto-enabled by default
        ctk.CTkCheckBox(cashout_btn_frame, text="Live", variable=self.market_live_tracking_var,
                        command=self._toggle_market_live_tracking,
                        fg_color=COLORS['success'], hover_color='#4caf50',
                        text_color=COLORS['text_primary'], width=60).pack(side=tk.LEFT, padx=5)
        
        self.market_live_status = ctk.CTkLabel(cashout_btn_frame, text="", 
                                               font=('Segoe UI', 8, 'bold'),
                                               text_color=COLORS['text_secondary'])
        self.market_live_status.pack(side=tk.LEFT)
        
        ctk.CTkButton(cashout_btn_frame, text="Aggiorna", command=self._update_market_cashout_positions,
                      fg_color=COLORS['button_secondary'], hover_color=COLORS['bg_hover'],
                      corner_radius=6, width=80).pack(side=tk.RIGHT, padx=2)
        
        # Bind double-click on cashout tree to cashout single position
        self.market_cashout_tree.bind('<Double-1>', self._do_single_cashout)
        
        # Store live tracking timer ID and fetch state
        self.market_live_tracking_id = None
        self.market_cashout_fetch_in_progress = False
        self.polling_fallback_id = None
        self.market_cashout_fetch_cancelled = False  # Cancellation flag
        self.market_cashout_positions = {}
        
    
    def _update_placed_bets(self):
        """Update placed bets list for current market."""
        # Clear existing
        for item in self.placed_bets_tree.get_children():
            self.placed_bets_tree.delete(item)
        
        if not self.client or not self.current_market:
            return
        
        market_id = self.current_market.get('marketId')
        if not market_id:
            return
        
        # Build runner name lookup
        runner_names = {}
        for runner in self.current_market.get('runners', []):
            runner_names[runner['selectionId']] = runner['runnerName']
        
        def fetch_bets():
            try:
                orders = self.client.get_current_orders()
                matched = orders.get('matched', [])
                
                # Filter orders for current market
                market_orders = [o for o in matched if o.get('marketId') == market_id]
                
                # Update UI in main thread
                self.root.after(0, lambda: self._display_placed_bets(market_orders, runner_names))
            except Exception as e:
                print(f"Error fetching placed bets: {e}")
        
        threading.Thread(target=fetch_bets, daemon=True).start()
    
    def _display_placed_bets(self, orders, runner_names):
        """Display placed bets in treeview."""
        for item in self.placed_bets_tree.get_children():
            self.placed_bets_tree.delete(item)
        
        for order in orders:
            selection_id = order.get('selectionId')
            side = order.get('side', 'BACK')
            price = order.get('price', 0)
            stake = order.get('sizeMatched', 0)
            
            runner_name = runner_names.get(selection_id, f"ID:{selection_id}")
            if len(runner_name) > 15:
                runner_name = runner_name[:15] + "..."
            
            tag = 'back' if side == 'BACK' else 'lay'
            
            self.placed_bets_tree.insert('', tk.END, values=(
                runner_name,
                side[:1],  # B or L
                f"{price:.2f}",
                f"{stake:.2f}"
            ), tags=(tag,))
    
    def _update_market_cashout_positions(self):
        """Update cashout positions for ALL markets with matched orders."""
        # Prevent spawning multiple fetch threads
        if self.market_cashout_fetch_in_progress:
            return
        
        if not self.client:
            self.market_cashout_btn.configure(state=tk.DISABLED)
            return
        
        self.market_cashout_fetch_in_progress = True
        self.market_cashout_fetch_cancelled = False
        
        def fetch_positions():
            try:
                if self.market_cashout_fetch_cancelled:
                    self.market_cashout_fetch_in_progress = False
                    return
                
                orders = self.client.get_current_orders()
                matched = orders.get('matched', [])
                unmatched = orders.get('unmatched', [])
                
                logging.debug(f"Cashout: Total matched={len(matched)}, unmatched={len(unmatched)}")
                
                if self.market_cashout_fetch_cancelled:
                    self.market_cashout_fetch_in_progress = False
                    return
                
                # Get ALL orders (matched + unmatched) - no market filter
                all_orders = matched + unmatched
                positions = []
                market_cache = {}
                
                for order in all_orders:
                    if self.market_cashout_fetch_cancelled:
                        self.market_cashout_fetch_in_progress = False
                        return
                    
                    market_id = order.get('marketId')
                    selection_id = order.get('selectionId')
                    side = order.get('side')
                    price = order.get('price', 0)
                    stake = order.get('sizeMatched', 0)
                    
                    if stake > 0:
                        # Get event/market/runner names
                        event_name = ''
                        market_name = ''
                        runner_name = str(selection_id)
                        
                        if market_id and market_id not in market_cache:
                            try:
                                catalogue = self.client.get_market_catalogue([market_id])
                                if catalogue:
                                    market_cache[market_id] = catalogue[0]
                            except:
                                pass
                        
                        if market_id in market_cache:
                            cat = market_cache[market_id]
                            event_name = cat.get('event', {}).get('name', '')[:20]
                            market_name = cat.get('marketName', '')[:15]
                            for r in cat.get('runners', []):
                                if r.get('selectionId') == selection_id:
                                    runner_name = r.get('runnerName', str(selection_id))[:15]
                                    break
                        
                        # Calculate cashout
                        try:
                            cashout_info = self.client.calculate_cashout(
                                market_id, selection_id, side, stake, price
                            )
                            green_up = cashout_info.get('green_up', 0)
                        except:
                            cashout_info = None
                            green_up = 0
                        
                        positions.append({
                            'bet_id': order.get('betId'),
                            'market_id': market_id,
                            'selection_id': selection_id,
                            'event_name': event_name,
                            'market_name': market_name,
                            'runner_name': runner_name,
                            'side': side,
                            'price': price,
                            'stake': stake,
                            'green_up': green_up,
                            'cashout_info': cashout_info
                        })
                
                def update_ui():
                    self.market_cashout_fetch_in_progress = False
                    if not self.market_cashout_fetch_cancelled:
                        self._display_market_cashout_positions(positions)
                
                self.root.after(0, update_ui)
            except Exception as e:
                self.market_cashout_fetch_in_progress = False
                logging.error(f"Error fetching cashout positions: {e}")
        
        threading.Thread(target=fetch_positions, daemon=True).start()
    
    def _display_market_cashout_positions(self, positions):
        """Display cashout positions in market view (all markets)."""
        for item in self.market_cashout_tree.get_children():
            self.market_cashout_tree.delete(item)
        
        self.market_cashout_positions = {}
        
        for pos in positions:
            bet_id = pos['bet_id']
            green_up = pos['green_up']
            pl_tag = 'profit' if green_up > 0 else 'loss'
            
            # Show event/runner combined in sel column for compact display
            display_name = pos.get('event_name', '')
            if display_name:
                display_name = f"{display_name[:10]}-{pos['runner_name'][:10]}"
            else:
                display_name = pos['runner_name']
            
            self.market_cashout_tree.insert('', tk.END, iid=str(bet_id), values=(
                display_name[:20],
                pos['side'][:1],
                f"{green_up:+.2f}"
            ), tags=(pl_tag,))
            
            self.market_cashout_positions[str(bet_id)] = pos
        
        if positions:
            self.market_cashout_btn.configure(state=tk.NORMAL)
        else:
            self.market_cashout_btn.configure(state=tk.DISABLED)
    
    def _toggle_market_live_tracking(self):
        """Toggle live tracking for market cashout."""
        if self.market_live_tracking_var.get():
            self._start_market_live_tracking()
        else:
            self._stop_market_live_tracking()
    
    def _start_market_live_tracking(self):
        """Start live tracking for market cashout."""
        def update():
            if not self.market_live_tracking_var.get():
                return
            self._update_market_cashout_positions()
            self.market_live_tracking_id = self.root.after(LIVE_REFRESH_INTERVAL, update)
        
        self._update_market_cashout_positions()
        self.market_live_tracking_id = self.root.after(LIVE_REFRESH_INTERVAL, update)
        self.market_live_status.configure(text="LIVE", text_color=COLORS['success'])
    
    def _stop_market_live_tracking(self):
        """Stop live tracking for market cashout."""
        if self.market_live_tracking_id:
            self.root.after_cancel(self.market_live_tracking_id)
            self.market_live_tracking_id = None
        # Signal cancellation to any in-flight fetch thread
        self.market_cashout_fetch_cancelled = True
        self.market_live_status.configure(text="", text_color=COLORS['text_secondary'])
    
    def _do_single_cashout(self, event):
        """Execute cashout for double-clicked position."""
        item = self.market_cashout_tree.identify_row(event.y)
        if item:
            self.market_cashout_tree.selection_set(item)
            self._do_market_cashout()
    
    def _do_market_cashout(self):
        """Execute cashout for selected position in market view."""
        selected = self.market_cashout_tree.selection()
        if not selected:
            messagebox.showwarning("Attenzione", "Seleziona una posizione")
            return
        
        for bet_id in selected:
            pos = self.market_cashout_positions.get(bet_id)
            if not pos or not pos.get('cashout_info'):
                continue
            
            info = pos['cashout_info']
            
            # Check if auto-confirm is enabled
            if self.auto_cashout_var.get():
                confirm = True
            else:
                confirm = messagebox.askyesno(
                    "Conferma Cashout",
                    f"Eseguire cashout?\n\n"
                    f"Selezione: {pos['runner_name']}\n"
                    f"Tipo: {info['cashout_side']} @ {info['current_price']:.2f}\n"
                    f"Stake: {info['cashout_stake']:.2f}\n"
                    f"Profitto garantito: {info['green_up']:+.2f}"
                )
            
            if confirm:
                try:
                    market_id = pos.get('market_id')
                    if not market_id:
                        messagebox.showerror("Errore", "Market ID non trovato")
                        continue
                    
                    result = self.client.execute_cashout(
                        market_id,
                        pos['selection_id'],
                        info['cashout_side'],
                        info['cashout_stake'],
                        info['current_price']
                    )
                    
                    if result.get('status') == 'SUCCESS':
                        self.db.save_cashout_transaction(
                            market_id=market_id,
                            selection_id=pos['selection_id'],
                            original_bet_id=bet_id,
                            cashout_bet_id=result.get('betId'),
                            original_side=pos['side'],
                            original_stake=pos['stake'],
                            original_price=pos['price'],
                            cashout_side=info['cashout_side'],
                            cashout_stake=info['cashout_stake'],
                            cashout_price=result.get('averagePriceMatched') or info['current_price'],
                            profit_loss=info['green_up']
                        )
                        self.bet_logger.log_cashout_completed(
                            bet_id=bet_id,
                            market_id=market_id,
                            selection_id=str(pos['selection_id']),
                            profit=info['green_up'],
                            original_stake=pos['stake'],
                            cashout_stake=info['cashout_stake']
                        )
                        # Broadcast cashout to Copy Trading followers
                        event_name = self.current_event.get('name', '') if self.current_event else self.current_market.get('eventName', '')
                        self._broadcast_copy_cashout(event_name)
                        messagebox.showinfo("Successo", f"Cashout eseguito!\nProfitto: {info['green_up']:+.2f}")
                        self._update_market_cashout_positions()
                        self._update_balance()
                    else:
                        self.bet_logger.log_cashout_failed(
                            bet_id=bet_id,
                            market_id=market_id,
                            reason=result.get('error', 'Errore sconosciuto')
                        )
                        messagebox.showerror("Errore", f"Cashout fallito: {result.get('error', 'Errore')}")
                except Exception as e:
                    messagebox.showerror("Errore", f"Errore cashout: {e}")
    
    def _load_settings(self):
        """Load saved settings."""
        settings = self.db.get_settings()
        if settings and settings.get('session_token'):
            self._try_restore_session(settings)
    
    def _try_restore_session(self, settings):
        """Try to restore previous session."""
        if not all([settings.get('username'), settings.get('app_key'), 
                   settings.get('certificate'), settings.get('private_key')]):
            return
        
        expiry = settings.get('session_expiry')
        if expiry:
            try:
                expiry_dt = datetime.fromisoformat(expiry)
                if datetime.now() < expiry_dt:
                    self.status_label.configure(text="Sessione salvata (clicca Connetti)", text_color=COLORS['text_secondary'])
            except:
                pass
    
    def _show_credentials_dialog(self):
        """Show credentials configuration dialog."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Configura Credenziali Betfair")
        dialog.geometry("500x600")
        dialog.transient(self.root)
        dialog.grab_set()
        
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        
        settings = self.db.get_settings() or {}
        
        ttk.Label(frame, text="Username Betfair:").pack(anchor=tk.W)
        username_var = tk.StringVar(value=settings.get('username', ''))
        ttk.Entry(frame, textvariable=username_var, width=50).pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(frame, text="App Key:").pack(anchor=tk.W)
        appkey_var = tk.StringVar(value=settings.get('app_key', ''))
        ttk.Entry(frame, textvariable=appkey_var, width=50).pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(frame, text="Certificato SSL (.pem):").pack(anchor=tk.W)
        cert_text = scrolledtext.ScrolledText(frame, height=6, width=50)
        cert_text.pack(fill=tk.X, pady=(0, 5))
        if settings.get('certificate'):
            cert_text.insert('1.0', settings['certificate'])
        
        def load_cert():
            path = filedialog.askopenfilename(filetypes=[
                ("Certificati", "*.pem *.crt *.cer"),
                ("PEM files", "*.pem"),
                ("CRT files", "*.crt"),
                ("All files", "*.*")
            ])
            if path:
                with open(path, 'r') as f:
                    cert_text.delete('1.0', tk.END)
                    cert_text.insert('1.0', f.read())
        
        ttk.Button(frame, text="Carica da file...", command=load_cert).pack(anchor=tk.W, pady=(0, 10))
        
        ttk.Label(frame, text="Chiave Privata (.key o .pem):").pack(anchor=tk.W)
        key_text = scrolledtext.ScrolledText(frame, height=6, width=50)
        key_text.pack(fill=tk.X, pady=(0, 5))
        if settings.get('private_key'):
            key_text.insert('1.0', settings['private_key'])
        
        def load_key():
            path = filedialog.askopenfilename(filetypes=[
                ("Chiavi private", "*.pem *.key"),
                ("PEM files", "*.pem"),
                ("KEY files", "*.key"),
                ("All files", "*.*")
            ])
            if path:
                with open(path, 'r') as f:
                    key_text.delete('1.0', tk.END)
                    key_text.insert('1.0', f.read())
        
        ttk.Button(frame, text="Carica da file...", command=load_key).pack(anchor=tk.W, pady=(0, 20))
        
        def save():
            self.db.save_credentials(
                username_var.get(),
                appkey_var.get(),
                cert_text.get('1.0', tk.END).strip(),
                key_text.get('1.0', tk.END).strip()
            )
            messagebox.showinfo("Salvato", "Credenziali salvate con successo!")
            dialog.destroy()
        
        ttk.Button(frame, text="Salva", command=save).pack(pady=10)
    
    def _toggle_connection(self):
        """Connect or disconnect from Betfair."""
        if self.client:
            self._disconnect()
        else:
            self._connect()
    
    def _connect(self):
        """Connect to Betfair."""
        settings = self.db.get_settings()
        
        if not all([settings.get('username'), settings.get('app_key'),
                   settings.get('certificate'), settings.get('private_key')]):
            messagebox.showerror("Errore", "Configura prima le credenziali dal menu File")
            return
        
        pwd_dialog = tk.Toplevel(self.root)
        pwd_dialog.title("Password Betfair")
        pwd_dialog.geometry("350x180")
        pwd_dialog.transient(self.root)
        pwd_dialog.grab_set()
        
        # Center dialog on screen
        pwd_dialog.update_idletasks()
        x = (pwd_dialog.winfo_screenwidth() // 2) - (175)
        y = (pwd_dialog.winfo_screenheight() // 2) - (90)
        pwd_dialog.geometry(f"350x180+{x}+{y}")
        
        frame = ttk.Frame(pwd_dialog, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text="Password Betfair:").pack(anchor=tk.W)
        
        # Pre-populate password if saved
        saved_password = settings.get('password', '')
        pwd_var = tk.StringVar(value=saved_password or '')
        pwd_entry = ttk.Entry(frame, textvariable=pwd_var, show='*')
        pwd_entry.pack(fill=tk.X, pady=5)
        pwd_entry.focus()
        
        # Save password checkbox
        save_pwd_var = tk.BooleanVar(value=bool(saved_password))
        ttk.Checkbutton(frame, text="Salva Password", variable=save_pwd_var).pack(anchor=tk.W, pady=5)
        
        def do_login():
            password = pwd_var.get()
            
            # Save or clear password based on checkbox
            if save_pwd_var.get():
                self.db.save_password(password)
            else:
                self.db.save_password(None)
            
            pwd_dialog.destroy()
            
            self.status_label.configure(text="Connessione in corso...", text_color=COLORS['text_secondary'])
            self.connect_btn.configure(state=tk.DISABLED)
            
            def login_thread():
                try:
                    self.client = BetfairClient(
                        settings['username'],
                        settings['app_key'],
                        settings['certificate'],
                        settings['private_key']
                    )
                    result = self.client.login(password)
                    
                    self.db.save_session(result['session_token'], result['expiry'])
                    
                    self.root.after(0, self._on_connected)
                except Exception as e:
                    error_msg = str(e)
                    self.root.after(0, lambda msg=error_msg: self._on_connection_error(msg))
            
            threading.Thread(target=login_thread, daemon=True).start()
        
        pwd_entry.bind('<Return>', lambda e: do_login())
        ttk.Button(frame, text="Connetti", command=do_login).pack(pady=10)
    
    def _on_connected(self):
        """Handle successful connection."""
        self.status_label.configure(text="Connesso a Betfair Italia", text_color=COLORS['success'])
        self.connect_btn.configure(text="Disconnetti", state=tk.NORMAL)
        self.refresh_btn.configure(state=tk.NORMAL)
        
        self._update_balance()
        self._load_events()
        
        # Auto-enable refresh on connect
        self.auto_refresh_var.set(True)
        self._start_auto_refresh()
        
        # Start session keep-alive to prevent disconnection
        self._start_session_keepalive()
        
        # Start Order Stream for real-time order updates
        self._start_order_stream()
        
        # Refresh dashboard tab with connected data
        self._refresh_dashboard_tab()
    
    def _start_session_keepalive(self):
        """Start periodic session keep-alive to prevent timeout."""
        self.keepalive_id = None
        
        def keepalive():
            if self.client:
                try:
                    # Simple API call to keep session alive
                    self.client.get_account_balance()
                except Exception as e:
                    print(f"Keepalive failed: {e}")
                    # Try to re-login silently if session expired
                    self._try_silent_relogin()
            
            # Schedule next keepalive (every 10 minutes)
            if self.client:
                self.keepalive_id = self.root.after(600000, keepalive)
        
        # First keepalive after 10 minutes
        self.keepalive_id = self.root.after(600000, keepalive)
    
    def _stop_session_keepalive(self):
        """Stop session keep-alive."""
        if hasattr(self, 'keepalive_id') and self.keepalive_id:
            self.root.after_cancel(self.keepalive_id)
            self.keepalive_id = None
    
    def _start_order_stream(self):
        """Start Order Stream for real-time order updates."""
        logging.info("_start_order_stream: Starting...")
        
        if not self.client:
            logging.warning("_start_order_stream: No client, aborting")
            return
        
        # Stop existing stream first to avoid races
        self._stop_order_stream()
        
        try:
            settings = self.db.get_settings()
            app_key = settings.get('app_key', '')
            # Session token is stored in settings, not separate table
            session_token = settings.get('session_token', '')
            logging.info(f"_start_order_stream: app_key={app_key[:8] if app_key else 'NONE'}..., session={bool(session_token)}")
            
            if not app_key or not session_token:
                logging.warning("Order Stream: Missing app_key or session_token")
                return
            
            self.order_stream = BetfairStream(app_key, session_token, use_italian_exchange=True)
            self.order_stream.set_callbacks(
                on_order_change=self._on_order_stream_update,
                on_status_change=self._on_order_stream_status,
                on_error=self._on_order_stream_error,
                on_market_change=self._on_market_stream_update
            )
            
            # Cache reference for thread safety
            stream_ref = self.order_stream
            
            def connect_stream():
                if stream_ref is None:
                    return
                try:
                    if stream_ref.connect():
                        stream_ref.subscribe_orders()
                        logging.info("Order Stream: Connected and subscribed")
                    else:
                        logging.error("Order Stream: Failed to connect")
                except Exception as e:
                    logging.error(f"Order Stream connect error: {e}")
            
            threading.Thread(target=connect_stream, daemon=True).start()
            
        except Exception as e:
            logging.error(f"Order Stream init error: {e}")
    
    def _stop_order_stream(self):
        """Stop Order Stream."""
        if hasattr(self, 'order_stream') and self.order_stream:
            self.order_stream.disconnect()
            self.order_stream = None
    
    def _on_order_stream_update(self, order_data: dict):
        """Handle order update from stream."""
        logging.info(f"Order Stream update: {order_data}")
        
        bet_id = order_data.get('bet_id')
        size_matched = order_data.get('size_matched', 0)
        status = order_data.get('status', '')
        order_type = order_data.get('type', 'UNMATCHED')
        
        # Refresh UI on any order state change (matched, cancelled, lapsed, etc.)
        should_refresh = False
        
        if order_type == 'MATCHED' or size_matched > 0:
            logging.info(f"Order {bet_id} matched: {size_matched}")
            should_refresh = True
        
        if status in ('CANCELLED', 'LAPSED', 'VOIDED', 'EXPIRED'):
            logging.info(f"Order {bet_id} status changed: {status}")
            should_refresh = True
        
        if order_type == 'UNMATCHED':
            # Unmatched order update (price change, partial match, etc.)
            should_refresh = True
        
        if should_refresh:
            def update_ui():
                self._update_balance()
                if hasattr(self, 'bets_tree'):
                    self._refresh_current_bets()
            
            self.root.after(0, update_ui)
    
    def _on_order_stream_status(self, status: str):
        """Handle order stream status change."""
        logging.info(f"Order Stream status: {status}")
        
        def update_status():
            if status == "CONNECTED":
                # Try to subscribe to pending market when stream connects
                if hasattr(self, '_pending_market_subscription') and self._pending_market_subscription:
                    market_id = self._pending_market_subscription
                    if self._subscribe_to_market_stream(market_id):
                        self.streaming_active = True
                        self._stop_polling_fallback()
                        self.stream_label.configure(text="STREAM LIVE", text_color=COLORS['success'])
                        logging.info(f"Market Stream: Subscribed on connect to {market_id}")
                    else:
                        self.stream_label.configure(text="Stream: ON", text_color=COLORS['success'])
                else:
                    self.stream_label.configure(text="Stream: ON", text_color=COLORS['success'])
            else:
                self.stream_label.configure(text="Stream: OFF", text_color=COLORS['text_secondary'])
        
        self.root.after(0, update_status)
    
    def _on_order_stream_error(self, error: str):
        """Handle order stream error."""
        logging.error(f"Order Stream error: {error}")
    
    def _on_market_stream_update(self, market_data: dict):
        """Handle real-time market price update from stream - accumulate in buffer."""
        if not self.current_market:
            return
        
        # Only process updates for current market
        if market_data.get('market_id') != self.current_market.get('marketId'):
            return
        
        # Accumulate updates in thread-safe buffer (instead of updating UI directly)
        selection_id = str(market_data.get('selection_id'))
        
        # Initialize buffers if needed
        if not hasattr(self, '_market_update_buffer'):
            self._market_update_buffer = {}
            self._market_update_dirty = False
        
        if not hasattr(self, '_cashout_dirty'):
            self._cashout_dirty = False
        
        # Store latest data for each selection (overwrites previous - we only need latest)
        self._market_update_buffer[selection_id] = {
            'back_price': market_data.get('back_price'),
            'back_size': market_data.get('back_size', 0),
            'lay_price': market_data.get('lay_price'),
            'lay_size': market_data.get('lay_size', 0),
            'ltp': market_data.get('ltp'),
            'tv': market_data.get('tv', 0)
        }
        self._market_update_dirty = True
        
        # Mark cashout dirty if we have positions (for realtime cashout update)
        if hasattr(self, 'market_cashout_positions') and self.market_cashout_positions:
            self._cashout_dirty = True
        
        # Start throttled UI refresh loop if not running
        if not hasattr(self, '_stream_ui_loop_running') or not self._stream_ui_loop_running:
            self._stream_ui_loop_running = True
            self.root.after(30, self._process_stream_buffer)
    
    def _process_stream_buffer(self):
        """Process accumulated stream updates at throttled interval (30ms)."""
        if not hasattr(self, '_market_update_buffer'):
            self._stream_ui_loop_running = False
            return
        
        # Only update UI if there are changes
        if self._market_update_dirty and self._market_update_buffer:
            self._market_update_dirty = False
            
            # Take snapshot of current buffer
            buffer_snapshot = dict(self._market_update_buffer)
            
            # Update all changed runners in one batch
            for selection_id, data in buffer_snapshot.items():
                try:
                    if not self.runners_tree.exists(selection_id):
                        continue
                    
                    current_values = list(self.runners_tree.item(selection_id)['values'])
                    
                    # Get old prices for flash effect
                    old_back = current_values[2] if current_values[2] != '-' else None
                    
                    # Format new values
                    back_price = data['back_price']
                    lay_price = data['lay_price']
                    new_back = f"{back_price:.2f}" if back_price else "-"
                    new_lay = f"{lay_price:.2f}" if lay_price else "-"
                    new_back_size = f"{data['back_size']:.0f}" if data['back_size'] else "-"
                    new_lay_size = f"{data['lay_size']:.0f}" if data['lay_size'] else "-"
                    
                    current_values[2] = new_back
                    current_values[3] = new_back_size
                    current_values[4] = new_lay
                    current_values[5] = new_lay_size
                    
                    # Update tree
                    self.runners_tree.item(selection_id, values=current_values)
                    
                    # Flash effect for price changes
                    if old_back and new_back != old_back:
                        try:
                            flash_tag = 'price_up' if float(new_back) > float(old_back) else 'price_down'
                            self.runners_tree.item(selection_id, tags=(flash_tag,))
                            self.root.after(500, lambda sid=selection_id: self._reset_runner_tag(sid))
                        except ValueError:
                            pass
                            
                except Exception as e:
                    logging.debug(f"Stream buffer UI error: {e}")
        
        # Update cashout values in realtime using buffered prices
        if hasattr(self, '_cashout_dirty') and self._cashout_dirty:
            self._cashout_dirty = False
            self._update_cashout_from_buffer()
        
        # Continue loop if streaming is active
        if self.streaming_active and self.stream_var.get():
            self.root.after(30, self._process_stream_buffer)
        else:
            self._stream_ui_loop_running = False
    
    def _reset_runner_tag(self, selection_id):
        """Reset runner row tag after flash effect."""
        try:
            if self.runners_tree.exists(selection_id):
                self.runners_tree.item(selection_id, tags=('runner_row',))
        except:
            pass
    
    def _update_cashout_from_buffer(self):
        """Update cashout values using realtime prices from stream buffer.
        
        Uses formula (same as betfair_client.py calculate_cashout):
        - BACK position: cashout by LAY at current_lay_price
          cashout_stake = (stake * back_price) / lay_price
        - LAY position: cashout by BACK at current_back_price
          cashout_stake = (stake * lay_price) / back_price
        
        Green-up = guaranteed profit regardless of outcome
        With proper hedge: profit_if_win == profit_if_lose
        """
        if not hasattr(self, 'market_cashout_positions') or not self.market_cashout_positions:
            return
        
        if not hasattr(self, '_market_update_buffer') or not self._market_update_buffer:
            return
        
        buffer = self._market_update_buffer
        
        for bet_id, pos in self.market_cashout_positions.items():
            try:
                selection_id = str(pos.get('selection_id'))
                if selection_id not in buffer:
                    continue
                
                price_data = buffer[selection_id]
                side = pos.get('side')
                matched_stake = pos.get('stake', 0)
                matched_price = pos.get('price', 0)
                
                if matched_stake <= 0 or matched_price <= 0:
                    continue
                
                # Get current price for cashout direction
                if side == 'BACK':
                    # BACK position: cashout by LAYing
                    current_price = price_data.get('lay_price')
                    available_liquidity = price_data.get('lay_size', 0)
                else:
                    # LAY position: cashout by BACKing
                    current_price = price_data.get('back_price')
                    available_liquidity = price_data.get('back_size', 0)
                
                if not current_price or current_price <= 1:
                    continue
                
                # Calculate ideal cashout stake using hedge formula
                # Formula: cashout_stake = (stake * original_price) / current_price
                ideal_cashout_stake = (matched_stake * matched_price) / current_price
                
                # Check liquidity - clamp to available if needed (partial cashout)
                is_partial = False
                if available_liquidity and ideal_cashout_stake > available_liquidity:
                    cashout_stake = available_liquidity
                    is_partial = True
                else:
                    cashout_stake = ideal_cashout_stake
                
                # Calculate green-up (guaranteed profit) with actual cashout stake
                if side == 'BACK':
                    # profit_if_win = back_profit - lay_liability
                    # profit_if_lose = -back_stake + lay_stake
                    profit_if_win = matched_stake * (matched_price - 1) - cashout_stake * (current_price - 1)
                    profit_if_lose = -matched_stake + cashout_stake
                else:
                    # LAY: hedge by BACKing
                    original_liability = matched_stake * (matched_price - 1)
                    profit_if_win = -original_liability + cashout_stake * (current_price - 1)
                    profit_if_lose = matched_stake - cashout_stake
                
                # For perfect hedge: profit_if_win == profit_if_lose
                # Average handles rounding differences
                green_up = (profit_if_win + profit_if_lose) / 2
                
                # Apply commission ONLY on positive profit (Betfair Italia 4.5%)
                commission = 0.045
                if green_up > 0:
                    green_up = green_up * (1 - commission)
                
                # Update position data
                pos['green_up'] = round(green_up, 2)
                pos['cashout_stake'] = round(cashout_stake, 2)
                pos['ideal_cashout_stake'] = round(ideal_cashout_stake, 2)
                pos['current_price'] = current_price
                pos['available_liquidity'] = available_liquidity
                pos['is_partial'] = is_partial
                
                # Update tree display
                if self.market_cashout_tree.exists(bet_id):
                    old_values = list(self.market_cashout_tree.item(bet_id)['values'])
                    
                    # Format P/L with partial indicator
                    if is_partial:
                        new_pl = f"{green_up:+.2f}*"  # * indicates partial cashout
                    else:
                        new_pl = f"{green_up:+.2f}"
                    
                    # Update value with profit/loss tag
                    pl_tag = 'profit' if green_up > 0 else 'loss'
                    self.market_cashout_tree.item(bet_id, values=(
                        old_values[0],  # selection name
                        old_values[1],  # side
                        new_pl
                    ), tags=(pl_tag,))
                    
            except Exception as e:
                logging.debug(f"Cashout buffer update error: {e}")
    
    def _subscribe_to_market_stream(self, market_id: str):
        """Subscribe to real-time market data for a specific market."""
        if not hasattr(self, 'order_stream') or not self.order_stream:
            logging.debug("Market Stream: No stream connection available")
            return False
        
        if not self.order_stream.is_connected():
            logging.debug("Market Stream: Stream not connected")
            return False
        
        logging.info(f"Subscribing to market stream: {market_id}")
        return self.order_stream.subscribe_markets([market_id])
    
    def _unsubscribe_from_market_stream(self):
        """Unsubscribe from market data."""
        if hasattr(self, 'order_stream') and self.order_stream and self.order_stream.is_connected():
            self.order_stream.unsubscribe_markets()
    
    def _try_silent_relogin(self):
        """Try to re-login silently if session expired."""
        settings = self.db.get_settings()
        password = settings.get('password')
        
        if password and self.client:
            try:
                result = self.client.login(password)
                self.db.save_session(result['session_token'], result['expiry'])
                print("Session renewed successfully")
            except Exception as e:
                print(f"Silent relogin failed: {e}")
                # Show notification to user
                self.root.after(0, lambda: messagebox.showwarning(
                    "Sessione Scaduta", 
                    "La sessione è scaduta. Riconnettiti manualmente."
                ))
    
    def _on_connection_error(self, error):
        """Handle connection error."""
        self.status_label.configure(text=f"Errore: {error}", text_color=COLORS['error'])
        self.connect_btn.configure(text="Connetti", state=tk.NORMAL)
        self.client = None
        messagebox.showerror("Errore Connessione", error)
    
    def _disconnect(self):
        """Disconnect from Betfair."""
        self._stop_auto_refresh()
        self._stop_session_keepalive()
        self._stop_order_stream()
        self.auto_refresh_var.set(False)
        
        if self.client:
            self.client.logout()
            self.client = None
        
        self.db.clear_session()
        self.status_label.configure(text="Non connesso", text_color=COLORS['error'])
        self.stream_label.configure(text="")
        self.connect_btn.configure(text="Connetti")
        self.refresh_btn.configure(state=tk.DISABLED)
        self.balance_label.configure(text="")
        self.streaming_active = False
        self.stream_var.set(False)
        
        self.events_tree.delete(*self.events_tree.get_children())
        self.runners_tree.delete(*self.runners_tree.get_children())
        self.market_combo.configure(values=[])
        self._clear_selections()
    
    def _update_balance(self):
        """Update account balance display."""
        def fetch():
            try:
                funds = self.client.get_account_funds()
                self.root.after(0, lambda: self.balance_label.configure(
                    text=f"Saldo: {format_currency(funds['available'])}"
                ))
            except Exception as e:
                print(f"Error fetching balance: {e}")
        
        threading.Thread(target=fetch, daemon=True).start()
    
    def _load_events(self):
        """Load football events."""
        def fetch():
            try:
                events = self.client.get_football_events()
                self.root.after(0, lambda: self._display_events(events))
            except Exception as e:
                err_msg = str(e)
                self.root.after(0, lambda msg=err_msg: messagebox.showerror("Errore", f"Errore caricamento partite: {msg}"))
        
        threading.Thread(target=fetch, daemon=True).start()
    
    def _display_events(self, events):
        """Display events in treeview grouped by country."""
        self.all_events = events
        self._populate_events_tree()
    
    def _populate_events_tree(self):
        """Populate events tree based on current search filter."""
        self.events_tree.delete(*self.events_tree.get_children())
        search = self.search_var.get().lower()
        
        if search:
            # Search mode - show flat list of matching events
            for event in self.all_events:
                if search in event['name'].lower():
                    date_str = self._format_event_date(event)
                    self.events_tree.insert('', tk.END, iid=event['id'], text=event.get('countryCode', ''), values=(
                        event['name'],
                        date_str
                    ))
        else:
            # No search - show grouped by country
            countries = {}
            for event in self.all_events:
                country = event.get('countryCode', 'XX') or 'XX'
                if country not in countries:
                    countries[country] = []
                countries[country].append(event)
            
            for country in sorted(countries.keys()):
                country_id = f"country_{country}"
                self.events_tree.insert('', tk.END, iid=country_id, text=country, open=False)
                
                for event in countries[country]:
                    date_str = self._format_event_date(event)
                    self.events_tree.insert(country_id, tk.END, iid=event['id'], values=(
                        event['name'],
                        date_str
                    ))
    
    def _format_event_date(self, event):
        """Format event date for display, with LIVE indicator for in-play events."""
        if event.get('inPlay'):
            return "LIVE"
        if event.get('openDate'):
            try:
                dt = datetime.fromisoformat(event['openDate'].replace('Z', '+00:00'))
                return dt.strftime('%d/%m %H:%M')
            except:
                return event['openDate'][:16]
        return ""
    
    def _filter_events(self, *args):
        """Filter events by search text."""
        self._populate_events_tree()
    
    def _toggle_auto_refresh(self):
        """Toggle auto-refresh of events list."""
        if self.auto_refresh_var.get():
            self._start_auto_refresh()
        else:
            self._stop_auto_refresh()
    
    def _start_auto_refresh(self):
        """Start auto-refresh timer (in seconds)."""
        if not self.client:
            self.auto_refresh_var.set(False)
            return  # Silently disable if not connected
        
        self._stop_auto_refresh()  # Stop any existing timer
        
        interval_sec = int(self.auto_refresh_interval_var.get())
        interval_ms = interval_sec * 1000  # Convert seconds to milliseconds
        
        def do_refresh():
            if self.client and self.auto_refresh_var.get():
                self._load_events()
                self._update_balance()
                now = datetime.now().strftime('%H:%M:%S')
                self.auto_refresh_status.configure(text=f"Ultimo: {now}")
                self.auto_refresh_id = self.root.after(interval_ms, do_refresh)
        
        self.auto_refresh_id = self.root.after(interval_ms, do_refresh)
        self.auto_refresh_status.configure(text="Attivo")
    
    def _stop_auto_refresh(self):
        """Stop auto-refresh timer."""
        if self.auto_refresh_id:
            self.root.after_cancel(self.auto_refresh_id)
            self.auto_refresh_id = None
        self.auto_refresh_status.configure(text="")
    
    def _on_auto_refresh_interval_change(self, event=None):
        """Handle auto-refresh interval change."""
        if self.auto_refresh_var.get():
            self._start_auto_refresh()
    
    def _refresh_data(self):
        """Refresh all data."""
        self._update_balance()
        self._load_events()
        if self.current_event:
            self._load_available_markets(self.current_event['id'])
    
    def _on_event_selected(self, event):
        """Handle event selection."""
        selection = self.events_tree.selection()
        if not selection:
            return
        
        event_id = selection[0]
        
        # Ignore country parent nodes (they start with "country_")
        if event_id.startswith('country_'):
            return
        
        for evt in self.all_events:
            if evt['id'] == event_id:
                self.current_event = evt
                self.event_name_label.configure(text=evt['name'])
                break
        else:
            return  # Event not found
        
        self._stop_streaming()
        self._clear_selections()
        self._load_available_markets(event_id)
    
    def _load_available_markets(self, event_id):
        """Load all available markets for an event."""
        self.runners_tree.delete(*self.runners_tree.get_children())
        self.market_combo.configure(values=[])
        
        if not self.client:
            messagebox.showwarning("Attenzione", "Non sei connesso a Betfair")
            return
        
        def fetch():
            try:
                logging.info(f"Loading markets for event: {event_id}")
                markets = self.client.get_available_markets(event_id)
                logging.info(f"Got {len(markets) if markets else 0} markets")
                self.root.after(0, lambda: self._display_available_markets(markets))
            except Exception as e:
                err_msg = str(e)
                logging.error(f"Error loading markets: {err_msg}")
                self.root.after(0, lambda msg=err_msg: messagebox.showerror("Errore", f"Errore caricamento mercati: {msg}"))
        
        threading.Thread(target=fetch, daemon=True).start()
    
    def _display_available_markets(self, markets):
        """Display available markets in dropdown."""
        self.available_markets = markets
        logging.debug(f"_display_available_markets called with {len(markets) if markets else 0} markets")
        
        if not markets:
            self.market_combo.configure(values=["Nessun mercato disponibile"])
            self.market_combo.set("Nessun mercato disponibile")
            return
        
        display_names = []
        for m in markets:
            name = m.get('displayName') or m.get('marketName', 'Sconosciuto')
            if m.get('inPlay'):
                name = f"[LIVE] {name}"
            display_names.append(name)
        
        logging.debug(f"Setting combobox values: {display_names[:3]}...")
        self.market_combo.configure(values=display_names)
        logging.debug(f"Combobox values set, count: {len(display_names)}")
        
        if display_names:
            self.market_combo.set(display_names[0])
            logging.debug(f"Selected first market: {display_names[0]}")
            self._on_market_type_selected(None)
    
    def _on_market_type_selected(self, event):
        """Handle market type selection from dropdown."""
        current_value = self.market_combo.get()
        if not current_value or current_value == "Nessun mercato disponibile":
            return
        
        # Find the index of the selected market
        selection = -1
        for i, m in enumerate(self.available_markets):
            name = m.get('displayName') or m.get('marketName', 'Sconosciuto')
            if m.get('inPlay'):
                name = f"[LIVE] {name}"
            if name == current_value:
                selection = i
                break
        
        if selection < 0 or selection >= len(self.available_markets):
            return
        
        market = self.available_markets[selection]
        self._stop_streaming()
        self._clear_selections()
        self._load_market(market['marketId'])
    
    def _load_market(self, market_id):
        """Load a specific market with prices."""
        self.runners_tree.delete(*self.runners_tree.get_children())
        
        def fetch():
            try:
                market = self.client.get_market_with_prices(market_id)
                self.root.after(0, lambda: self._display_market(market))
            except Exception as e:
                err_msg = str(e)
                self.root.after(0, lambda msg=err_msg: messagebox.showerror("Errore", f"Mercato non disponibile: {msg}"))
        
        threading.Thread(target=fetch, daemon=True).start()
    
    def _display_market(self, market):
        """Display market runners."""
        self.current_market = market
        self.runners_tree.delete(*self.runners_tree.get_children())
        
        # Update market status
        self.market_status = market.get('status', 'OPEN')
        is_inplay = market.get('inPlay', False)
        
        # Update status indicator
        if self.market_status == 'SUSPENDED':
            self.market_status_label.configure(text="SOSPESO", text_color=COLORS['loss'])
            self.dutch_modal_btn.configure(state=tk.DISABLED)
            self.place_btn.configure(state=tk.DISABLED)
        elif self.market_status == 'CLOSED':
            self.market_status_label.configure(text="CHIUSO", text_color=COLORS['text_secondary'])
            self.dutch_modal_btn.configure(state=tk.DISABLED)
            self.place_btn.configure(state=tk.DISABLED)
        else:
            if is_inplay:
                self.market_status_label.configure(text="LIVE - APERTO", text_color=COLORS['success'])
            else:
                self.market_status_label.configure(text="APERTO", text_color=COLORS['success'])
            self.dutch_modal_btn.configure(state=tk.NORMAL)
            # place_btn state is managed by calculate function
        
        for runner in market['runners']:
            back_price = f"{runner['backPrice']:.2f}" if runner.get('backPrice') else "-"
            lay_price = f"{runner['layPrice']:.2f}" if runner.get('layPrice') else "-"
            back_size = f"{runner['backSize']:.0f}" if runner.get('backSize') else "-"
            lay_size = f"{runner['laySize']:.0f}" if runner.get('laySize') else "-"
            
            self.runners_tree.insert('', tk.END, iid=str(runner['selectionId']), values=(
                '',
                runner['runnerName'],
                back_price,
                back_size,
                lay_price,
                lay_size
            ), tags=('runner_row',))
        
        # Auto-start streaming for live price updates
        if self.market_status not in ('SUSPENDED', 'CLOSED'):
            self.stream_var.set(True)
            self._start_streaming()
        
        # Update placed bets and cashout positions
        self._update_placed_bets()
        self._update_market_cashout_positions()
        
        # Auto-start live tracking for cashout if enabled
        if self.market_live_tracking_var.get() and not self.market_live_tracking_id:
            self._start_market_live_tracking()
    
    def _refresh_prices(self):
        """Manually refresh prices for current market."""
        if not self.current_market:
            return
        
        self._load_market(self.current_market['marketId'])
    
    def _toggle_streaming(self):
        """Toggle streaming on/off."""
        if self.stream_var.get():
            self._start_streaming()
        else:
            self._stop_streaming()
    
    def _start_streaming(self):
        """Start streaming prices for current market (with polling fallback)."""
        if not self.client or not self.current_market:
            self.stream_var.set(False)
            return
        
        market_id = self.current_market['marketId']
        
        # Store pending market for subscription when stream connects
        self._pending_market_subscription = market_id
        
        # Try to use real-time Market Stream if already connected
        if self._subscribe_to_market_stream(market_id):
            self.streaming_active = True
            self._stop_polling_fallback()
            self.stream_label.configure(text="STREAM LIVE", text_color=COLORS['success'])
            logging.info(f"Market Stream: Subscribed to {market_id}")
        else:
            # Start polling as fallback, will switch to stream when connected
            self.streaming_active = False
            self._start_polling_fallback()
    
    def _start_polling_fallback(self):
        """Start polling fallback when streaming is not available."""
        self._stop_polling_fallback()
        self.stream_label.configure(text="POLLING (2s)")
        self._polling_loop()
    
    def _polling_loop(self):
        """Polling loop to refresh prices every 5 seconds."""
        if not self.stream_var.get() or not self.current_market:
            self.polling_fallback_id = None
            return
        
        # Refresh prices silently
        self._refresh_prices_silent()
        
        # Schedule next poll (2 seconds for real-time feel)
        self.polling_fallback_id = self.root.after(2000, self._polling_loop)
    
    def _refresh_prices_silent(self):
        """Refresh prices without reloading entire market."""
        if not self.client or not self.current_market:
            return
        
        def fetch_and_update():
            try:
                market_id = self.current_market['marketId']
                book = self.client.get_market_book(market_id)
                if book and book.get('runners'):
                    runners_data = []
                    for runner in book['runners']:
                        back_prices = [[runner.get('backPrice', 0), runner.get('backSize', 0)]] if runner.get('backPrice') else []
                        lay_prices = [[runner.get('layPrice', 0), runner.get('laySize', 0)]] if runner.get('layPrice') else []
                        runners_data.append({
                            'selectionId': runner['selectionId'],
                            'backPrices': back_prices,
                            'layPrices': lay_prices
                        })
                    self.root.after(0, lambda: self._on_price_update(market_id, runners_data))
            except Exception as e:
                logging.debug(f"Polling refresh error: {e}")
        
        import threading
        threading.Thread(target=fetch_and_update, daemon=True).start()
    
    def _stop_polling_fallback(self):
        """Stop polling fallback."""
        if hasattr(self, 'polling_fallback_id') and self.polling_fallback_id:
            try:
                self.root.after_cancel(self.polling_fallback_id)
            except:
                pass
            self.polling_fallback_id = None
    
    def _stop_streaming(self):
        """Stop streaming and polling."""
        if self.client:
            self.client.stop_streaming()
        self._stop_polling_fallback()
        self._unsubscribe_from_market_stream()
        self._pending_market_subscription = None
        self.streaming_active = False
        self.stream_var.set(False)
        self.stream_label.configure(text="")
    
    def _on_price_update(self, market_id, runners_data):
        """Handle streaming price update with visual highlights."""
        if not self.current_market or market_id != self.current_market['marketId']:
            return
        
        # Initialize previous prices cache if not exists
        if not hasattr(self, '_prev_prices'):
            self._prev_prices = {}
        
        def update_ui():
            for runner_update in runners_data:
                selection_id = str(runner_update['selectionId'])
                
                try:
                    item = self.runners_tree.item(selection_id)
                    if not item:
                        continue
                    
                    current_values = list(item['values'])
                    
                    back_prices = runner_update.get('backPrices', [])
                    lay_prices = runner_update.get('layPrices', [])
                    
                    # Get previous prices for comparison
                    prev = self._prev_prices.get(selection_id, {'back': 0, 'lay': 0})
                    new_back = back_prices[0][0] if back_prices else 0
                    new_lay = lay_prices[0][0] if lay_prices else 0
                    
                    # Determine highlight color based on price movement
                    highlight_tag = None
                    if prev['back'] > 0 and new_back > 0:
                        if new_back > prev['back']:
                            highlight_tag = 'price_up'  # Green - quote sale
                        elif new_back < prev['back']:
                            highlight_tag = 'price_down'  # Red - quote scende
                    elif prev['lay'] > 0 and new_lay > 0:
                        if new_lay > prev['lay']:
                            highlight_tag = 'price_up'
                        elif new_lay < prev['lay']:
                            highlight_tag = 'price_down'
                    
                    # Store new prices
                    self._prev_prices[selection_id] = {'back': new_back, 'lay': new_lay}
                    
                    if back_prices:
                        best_back = back_prices[0]
                        current_values[2] = f"{best_back[0]:.2f}"
                        current_values[3] = f"{best_back[1]:.0f}" if len(best_back) > 1 else "-"
                    
                    if lay_prices:
                        best_lay = lay_prices[0]
                        current_values[4] = f"{best_lay[0]:.2f}"
                        current_values[5] = f"{best_lay[1]:.0f}" if len(best_lay) > 1 else "-"
                    
                    self.runners_tree.item(selection_id, values=current_values)
                    
                    # Apply highlight if price changed (preserve existing tags)
                    if highlight_tag:
                        existing_tags = list(self.runners_tree.item(selection_id, 'tags') or ())
                        # Remove old highlight tags
                        existing_tags = [t for t in existing_tags if t not in ('price_up', 'price_down')]
                        existing_tags.append(highlight_tag)
                        self.runners_tree.item(selection_id, tags=tuple(existing_tags))
                        # Remove highlight after 500ms
                        self.root.after(500, lambda sid=selection_id: self._clear_price_highlight(sid))
                    
                    if selection_id in self.selected_runners:
                        if back_prices:
                            self.selected_runners[selection_id]['backPrice'] = back_prices[0][0]
                        if lay_prices:
                            self.selected_runners[selection_id]['layPrice'] = lay_prices[0][0]
                        # Update price based on current bet type
                        bet_type = self.bet_type_var.get()
                        if bet_type == 'BACK' and back_prices:
                            self.selected_runners[selection_id]['price'] = back_prices[0][0]
                        elif bet_type == 'LAY' and lay_prices:
                            self.selected_runners[selection_id]['price'] = lay_prices[0][0]
                        self._recalculate()
                        
                except Exception:
                    pass
        
        self.root.after(0, update_ui)
    
    def _clear_price_highlight(self, selection_id):
        """Clear price highlight from a runner row (preserve other tags)."""
        try:
            existing_tags = list(self.runners_tree.item(selection_id, 'tags') or ())
            # Remove only highlight tags, keep others
            clean_tags = [t for t in existing_tags if t not in ('price_up', 'price_down')]
            self.runners_tree.item(selection_id, tags=tuple(clean_tags))
        except:
            pass
    
    def _show_runner_context_menu(self, event):
        """Show context menu on right-click."""
        item = self.runners_tree.identify_row(event.y)
        if item:
            self.runners_tree.selection_set(item)
            self._context_menu_selection = item
            self.runner_context_menu.post(event.x_root, event.y_root)
    
    def _book_selected_runner(self):
        """Book the selected runner from context menu."""
        if not hasattr(self, '_context_menu_selection') or not self._context_menu_selection:
            return
        
        selection_id = self._context_menu_selection
        if not self.current_market:
            return
        
        for runner in self.current_market['runners']:
            if str(runner['selectionId']) == selection_id:
                current_price = runner.get('backPrice') or runner.get('layPrice') or 0
                if current_price > 0:
                    self._show_booking_dialog(
                        selection_id,
                        runner['runnerName'],
                        current_price,
                        self.current_market['marketId']
                    )
                break
    
    def _on_runner_clicked(self, event):
        """Handle runner row click - check which column was clicked for quick betting."""
        item = self.runners_tree.identify_row(event.y)
        if not item:
            return
        
        # Identify which column was clicked
        column = self.runners_tree.identify_column(event.x)
        # column is like '#1', '#2', etc. - #3 is back, #5 is lay
        
        selection_id = item
        
        # Quick bet on Back price column (#3)
        if column == '#3':
            self._quick_bet(selection_id, 'BACK')
            return
        
        # Quick bet on Lay price column (#5)
        if column == '#5':
            self._quick_bet(selection_id, 'LAY')
            return
        
        # Default: toggle selection for dutching
        if selection_id in self.selected_runners:
            del self.selected_runners[selection_id]
            values = list(self.runners_tree.item(item)['values'])
            values[0] = ''
            self.runners_tree.item(item, values=values)
        else:
            if self.current_market:
                for runner in self.current_market['runners']:
                    if str(runner['selectionId']) == selection_id:
                        runner_data = runner.copy()
                        
                        # Get current prices from treeview
                        values = list(self.runners_tree.item(item)['values'])
                        # values: [selection, runnerName, backPrice, backSize, layPrice, laySize]
                        try:
                            back_price = float(str(values[2]).replace(',', '.')) if values[2] and values[2] != '-' else 0
                            lay_price = float(str(values[4]).replace(',', '.')) if values[4] and values[4] != '-' else 0
                        except (ValueError, IndexError):
                            back_price = 0
                            lay_price = 0
                        
                        runner_data['backPrice'] = back_price
                        runner_data['layPrice'] = lay_price
                        # Set 'price' based on current bet type for dutching calculation
                        bet_type = self.bet_type_var.get()
                        runner_data['price'] = back_price if bet_type == 'BACK' else lay_price
                        
                        self.selected_runners[selection_id] = runner_data
                        values[0] = 'X'
                        self.runners_tree.item(item, values=values)
                        break
        
        self._recalculate()
    
    def _quick_bet(self, selection_id, bet_type):
        """Place a quick single bet on a runner at current price."""
        if not self.client and not self.simulation_mode:
            messagebox.showwarning("Attenzione", "Devi prima connetterti")
            return
        
        if not self.current_market:
            return
        
        # Get runner info
        runner = None
        for r in self.current_market['runners']:
            if str(r['selectionId']) == selection_id:
                runner = r
                break
        
        if not runner:
            return
        
        # Get price from treeview
        values = list(self.runners_tree.item(selection_id)['values'])
        try:
            if bet_type == 'BACK':
                price = float(str(values[2]).replace(',', '.')) if values[2] and values[2] != '-' else 0
            else:
                price = float(str(values[4]).replace(',', '.')) if values[4] and values[4] != '-' else 0
        except (ValueError, IndexError):
            price = 0
        
        if price <= 0:
            messagebox.showwarning("Attenzione", "Quota non disponibile")
            return
        
        # Get stake from entry
        try:
            stake = float(self.stake_var.get().replace(',', '.'))
        except ValueError:
            stake = 1.0
        
        # Minimum stake check (€1 for Italian regulations)
        if stake < 1.0:
            stake = 1.0
        
        # Show editable quick bet dialog
        self._show_quick_bet_dialog(runner, bet_type, price, stake)
    
    def _show_quick_bet_dialog(self, runner, bet_type, initial_price, initial_stake):
        """Show editable quick bet confirmation dialog."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Scommessa Rapida")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        
        # Center dialog
        dialog.geometry("340x330")
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - 340) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - 330) // 2
        dialog.geometry(f"+{x}+{y}")
        
        # Configure dark theme
        dialog.configure(bg=COLORS['bg_dark'])
        
        # Mode indicator
        if self.simulation_mode:
            mode_label = ctk.CTkLabel(dialog, text="[MODALITA SIMULAZIONE]", 
                                       text_color=COLORS['warning'], font=('Segoe UI', 10, 'bold'))
            mode_label.pack(pady=(10, 5))
        
        # Selection name
        tipo_text = "BACK (Punta)" if bet_type == 'BACK' else "LAY (Banca)"
        tipo_color = COLORS['back'] if bet_type == 'BACK' else COLORS['lay']
        
        ctk.CTkLabel(dialog, text=runner['runnerName'], font=('Segoe UI', 12, 'bold'),
                     text_color=COLORS['text_primary']).pack(pady=(10, 5))
        
        ctk.CTkLabel(dialog, text=tipo_text, font=('Segoe UI', 11, 'bold'),
                     text_color=tipo_color).pack(pady=2)
        
        # Input frame
        input_frame = ctk.CTkFrame(dialog, fg_color=COLORS['bg_panel'])
        input_frame.pack(fill=tk.X, padx=20, pady=10)
        
        # Quota (editable)
        quota_frame = ctk.CTkFrame(input_frame, fg_color='transparent')
        quota_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ctk.CTkLabel(quota_frame, text="Quota:", width=80, anchor='w',
                     text_color=COLORS['text_secondary']).pack(side=tk.LEFT)
        
        price_var = tk.StringVar(value=f"{initial_price:.2f}")
        price_entry = ctk.CTkEntry(quota_frame, textvariable=price_var, width=100,
                                    fg_color=COLORS['bg_card'], text_color=COLORS['text_primary'])
        price_entry.pack(side=tk.LEFT, padx=5)
        
        # Market price checkbox (for immediate match) - Default OFF to use exact clicked price
        market_price_var = tk.BooleanVar(value=False)
        market_check = ctk.CTkCheckBox(input_frame, text="Accetta quota mercato (match immediato)",
                                        variable=market_price_var, 
                                        text_color=COLORS['text_secondary'],
                                        fg_color=COLORS['success'],
                                        hover_color=COLORS['success'])
        market_check.pack(padx=10, pady=5, anchor='w')
        
        def on_market_check_change():
            if market_price_var.get():
                price_entry.configure(state='disabled')
                price_var.set(f"{initial_price:.2f}")
            else:
                price_entry.configure(state='normal')
        
        market_check.configure(command=on_market_check_change)
        on_market_check_change()
        
        # Stake (editable)
        stake_frame = ctk.CTkFrame(input_frame, fg_color='transparent')
        stake_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ctk.CTkLabel(stake_frame, text="Stake (EUR):", width=80, anchor='w',
                     text_color=COLORS['text_secondary']).pack(side=tk.LEFT)
        
        stake_var = tk.StringVar(value=f"{initial_stake:.2f}")
        stake_entry = ctk.CTkEntry(stake_frame, textvariable=stake_var, width=100,
                                    fg_color=COLORS['bg_card'], text_color=COLORS['text_primary'])
        stake_entry.pack(side=tk.LEFT, padx=5)
        
        # Potential profit/liability label
        pl_label = ctk.CTkLabel(dialog, text="", font=('Segoe UI', 10),
                                 text_color=COLORS['text_secondary'])
        pl_label.pack(pady=5)
        
        def update_pl(*args):
            try:
                p = float(price_var.get().replace(',', '.'))
                s = float(stake_var.get().replace(',', '.'))
                if bet_type == 'BACK':
                    profit = s * (p - 1) * 0.955
                    pl_label.configure(text=f"Profitto potenziale: {profit:.2f} EUR")
                else:
                    liability = s * (p - 1)
                    pl_label.configure(text=f"Liability: {liability:.2f} EUR")
            except:
                pl_label.configure(text="")
        
        price_var.trace('w', update_pl)
        stake_var.trace('w', update_pl)
        update_pl()
        
        # Buttons
        btn_frame = ctk.CTkFrame(dialog, fg_color='transparent')
        btn_frame.pack(fill=tk.X, padx=20, pady=15)
        
        def confirm():
            try:
                final_stake = float(stake_var.get().replace(',', '.'))
            except ValueError:
                messagebox.showwarning("Errore", "Stake non valido")
                return
            
            if final_stake < 1.0:
                messagebox.showwarning("Errore", "Stake minimo: 1.00 EUR")
                return
            
            # Get price based on market checkbox
            if market_price_var.get():
                # Use current market price for immediate match
                final_price = initial_price
            else:
                try:
                    final_price = float(price_var.get().replace(',', '.'))
                except ValueError:
                    messagebox.showwarning("Errore", "Quota non valida")
                    return
                
                if final_price <= 1.0:
                    messagebox.showwarning("Errore", "Quota deve essere > 1.00")
                    return
            
            dialog.destroy()
            
            # Place the bet (use_market_price affects how bet is placed)
            if self.simulation_mode:
                self._place_quick_simulation_bet(runner, bet_type, final_price, final_stake)
            else:
                self._place_quick_real_bet(runner, bet_type, final_price, final_stake, use_market_price=market_price_var.get())
        
        def cancel():
            dialog.destroy()
        
        ctk.CTkButton(btn_frame, text="PIAZZA", width=100, height=35,
                      fg_color=COLORS['success'], hover_color='#1a5f2a',
                      command=confirm).pack(side=tk.LEFT, padx=10, expand=True)
        
        ctk.CTkButton(btn_frame, text="Annulla", width=100, height=35,
                      fg_color=COLORS['button_secondary'], hover_color=COLORS['bg_hover'],
                      command=cancel).pack(side=tk.LEFT, padx=10, expand=True)
        
        # Focus on stake entry
        stake_entry.focus_set()
        stake_entry.select_range(0, tk.END)
        
        # Handle Enter key
        dialog.bind('<Return>', lambda e: confirm())
        dialog.bind('<Escape>', lambda e: cancel())
    
    def _show_quick_bet_panel(self, runner, selection_id, bet_type, price, stake):
        """Show the quick bet inline panel with runner data."""
        self.qb_current_runner = runner
        self.qb_current_selection_id = selection_id
        
        # Set selection name
        self.qb_selection_label.configure(text=f"Selezione: {runner['runnerName']}")
        
        # Set bet type
        self.qb_bet_type_var.set(bet_type)
        self._update_qb_type_buttons()
        
        # Set odds and stake
        self.qb_odds_var.set(f"{price:.2f}")
        self.qb_stake_var.set(f"{stake:.2f}")
        
        # Show simulation mode indicator
        if self.simulation_mode:
            self.qb_mode_label.configure(text="[MODALITA SIMULAZIONE]")
        else:
            self.qb_mode_label.configure(text="")
        
        # Update P/L
        self._update_qb_pl()
        
        # Show the panel
        self.quick_bet_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Start live odds updates
        self._start_qb_live_updates()
    
    def _hide_quick_bet_panel(self):
        """Hide the quick bet panel."""
        self.quick_bet_frame.pack_forget()
        self._stop_qb_live_updates()
        self.qb_current_runner = None
        self.qb_current_selection_id = None
    
    def _set_qb_type(self, bet_type):
        """Set quick bet type (BACK/LAY)."""
        self.qb_bet_type_var.set(bet_type)
        self._update_qb_type_buttons()
        self._update_qb_live_odds()
        self._update_qb_pl()
    
    def _update_qb_type_buttons(self):
        """Update visual state of BACK/LAY buttons."""
        bet_type = self.qb_bet_type_var.get()
        if bet_type == 'BACK':
            self.qb_back_btn.configure(fg_color=COLORS['back'])
            self.qb_lay_btn.configure(fg_color=COLORS['button_secondary'])
        else:
            self.qb_back_btn.configure(fg_color=COLORS['button_secondary'])
            self.qb_lay_btn.configure(fg_color=COLORS['lay'])
    
    def _update_qb_pl(self):
        """Update potential P/L display."""
        try:
            odds = float(self.qb_odds_var.get().replace(',', '.'))
            stake = float(self.qb_stake_var.get().replace(',', '.'))
            bet_type = self.qb_bet_type_var.get()
            
            commission = 0.045  # 4.5% Betfair Italia
            if bet_type == 'BACK':
                gross_profit = stake * (odds - 1)
                net_profit = gross_profit * (1 - commission)
                liability = stake
            else:
                gross_profit = stake
                net_profit = gross_profit * (1 - commission)
                liability = stake * (odds - 1)
            
            self.qb_pl_label.configure(
                text=f"P/L: +{net_profit:.2f} EUR | Liab: {liability:.2f} EUR",
                text_color=COLORS['success']
            )
        except (ValueError, ZeroDivisionError):
            self.qb_pl_label.configure(text="P/L: -", text_color=COLORS['text_secondary'])
    
    def _start_qb_live_updates(self):
        """Start updating live odds in the quick bet panel."""
        # Cancel any existing scheduled update
        if self.qb_live_update_id:
            try:
                self.root.after_cancel(self.qb_live_update_id)
            except:
                pass
            self.qb_live_update_id = None
        
        # Schedule the update loop
        self._qb_live_update_loop()
    
    def _qb_live_update_loop(self):
        """Live update loop for quick bet panel."""
        # Check if panel is visible and we have a selection
        if not self.qb_current_selection_id:
            self.qb_live_update_id = None
            return
        
        # Update odds
        self._update_qb_live_odds()
        
        # Schedule next update
        self.qb_live_update_id = self.root.after(2000, self._qb_live_update_loop)
    
    def _stop_qb_live_updates(self):
        """Stop live odds updates."""
        if self.qb_live_update_id:
            try:
                self.root.after_cancel(self.qb_live_update_id)
            except:
                pass
            self.qb_live_update_id = None
    
    def _update_qb_live_odds(self):
        """Update live odds display from current market data."""
        if not self.qb_current_selection_id:
            return
        
        try:
            values = list(self.runners_tree.item(self.qb_current_selection_id)['values'])
            bet_type = self.qb_bet_type_var.get()
            
            if bet_type == 'BACK':
                live_price = float(str(values[2]).replace(',', '.')) if values[2] and values[2] != '-' else 0
            else:
                live_price = float(str(values[4]).replace(',', '.')) if values[4] and values[4] != '-' else 0
            
            if live_price > 0:
                self.qb_live_odds_label.configure(text=f"(Live: {live_price:.2f})")
            else:
                self.qb_live_odds_label.configure(text="(Live: -)")
        except:
            self.qb_live_odds_label.configure(text="(Live: -)")
    
    def _use_live_odds(self):
        """Set the odds entry to current live odds."""
        if not self.qb_current_selection_id:
            return
        
        try:
            values = list(self.runners_tree.item(self.qb_current_selection_id)['values'])
            bet_type = self.qb_bet_type_var.get()
            
            if bet_type == 'BACK':
                live_price = float(str(values[2]).replace(',', '.')) if values[2] and values[2] != '-' else 0
            else:
                live_price = float(str(values[4]).replace(',', '.')) if values[4] and values[4] != '-' else 0
            
            if live_price > 0:
                self.qb_odds_var.set(f"{live_price:.2f}")
        except:
            pass
    
    def _confirm_quick_bet(self):
        """Confirm and place the quick bet."""
        if not self.qb_current_runner:
            return
        
        try:
            price = float(self.qb_odds_var.get().replace(',', '.'))
            stake = float(self.qb_stake_var.get().replace(',', '.'))
        except ValueError:
            messagebox.showwarning("Attenzione", "Quota o stake non validi")
            return
        
        if price <= 1.0:
            messagebox.showwarning("Attenzione", "Quota deve essere maggiore di 1.00")
            return
        
        if stake < 1.0:
            stake = 1.0
        
        bet_type = self.qb_bet_type_var.get()
        runner = self.qb_current_runner
        
        # Hide panel
        self._hide_quick_bet_panel()
        
        # Place the bet
        if self.simulation_mode:
            self._place_quick_simulation_bet(runner, bet_type, price, stake)
        else:
            self._place_quick_real_bet(runner, bet_type, price, stake)
    
    def _place_quick_simulation_bet(self, runner, bet_type, price, stake):
        """Place a quick simulated bet."""
        try:
            # Calculate P/L
            commission = 0.045  # 4.5% Betfair Italia commission
            if bet_type == 'BACK':
                gross_profit = stake * (price - 1)
                profit = gross_profit * (1 - commission)  # Net profit after commission
                liability = stake
            else:
                gross_profit = stake
                profit = gross_profit * (1 - commission)  # Net profit after commission
                liability = stake * (price - 1)
            
            # Check balance
            settings = self.db.get_simulation_settings()
            current_balance = settings.get('virtual_balance', 10000.0)
            
            if liability > current_balance:
                messagebox.showerror("Errore Simulazione", 
                    f"Saldo virtuale insufficiente.\n"
                    f"Saldo: {format_currency(current_balance)}\n"
                    f"Richiesto: {format_currency(liability)}")
                return
            
            # Deduct from virtual balance and increment bet count
            new_balance = current_balance - liability
            self.db.increment_simulation_bet_count(new_balance)
            
            # Save bet
            self.db.save_simulation_bet(
                event_name=self.current_market.get('eventName', 'Quick Bet'),
                market_id=self.current_market['marketId'],
                market_name=self.current_market.get('marketName', ''),
                side=bet_type,
                selection_id=str(runner['selectionId']),
                selection_name=runner['runnerName'],
                price=price,
                stake=stake,
                status='MATCHED'
            )
            
            messagebox.showinfo("Simulazione", 
                f"Scommessa simulata piazzata!\n\n"
                f"{runner['runnerName']} @ {price:.2f}\n"
                f"Stake: {format_currency(stake)}\n"
                f"Nuovo Saldo: {format_currency(new_balance)}")
            
        except Exception as e:
            messagebox.showerror("Errore", str(e))
    
    def _place_quick_real_bet(self, runner, bet_type, price, stake, use_market_price=False):
        """Place a quick real bet via Betfair API.
        
        Args:
            use_market_price: If True, uses betTargetType=PAYOUT for immediate match at best price
        """
        logging.info(f"[BET] _place_quick_real_bet CALLED: runner={runner['runnerName']}, type={bet_type}, price={price}, stake={stake}")
        
        def place_thread():
            logging.info(f"[BET] place_thread STARTED")
            try:
                if use_market_price:
                    # Use market order for immediate match
                    # For BACK: we want to get matched at best available back price
                    # For LAY: we want to get matched at best available lay price
                    # We use a very generous price to ensure match
                    if bet_type == 'BACK':
                        # For BACK, use a high price (1000) to accept any available price
                        match_price = 1000.0
                    else:
                        # For LAY, use a low price (1.01) to accept any available price
                        match_price = 1.01
                    
                    logging.info(f"Quick bet using market price: requested={price}, match_price={match_price}")
                else:
                    match_price = price
                
                result = self.client.place_bet(
                    market_id=self.current_market['marketId'],
                    selection_id=runner['selectionId'],
                    side=bet_type,
                    price=match_price,
                    size=stake,
                    persistence_type='LAPSE'
                )
                
                bet_result = result
                self.root.after(0, lambda r=bet_result, rn=runner, bt=bet_type, p=price, s=stake: self._on_quick_bet_result(r, rn, bt, p, s))
            except Exception as e:
                err_msg = str(e)
                logging.error(f"Quick bet error: {err_msg}")
                self.root.after(0, lambda msg=err_msg: messagebox.showerror("Errore", msg))
        
        threading.Thread(target=place_thread, daemon=True).start()
    
    def _on_quick_bet_result(self, result, runner, bet_type, price, stake):
        """Handle quick bet result."""
        logging.info(f"Quick bet result: status={result.get('status')}, runner={runner['runnerName']}")
        logging.info(f"Full bet result: {result}")
        
        if result.get('status') == 'SUCCESS':
            logging.info(f"[DEBUG] Entering SUCCESS block...")
            instruction_reports = result.get('instructionReports', [])
            matched = sum(r.get('sizeMatched', 0) for r in instruction_reports)
            bet_id = instruction_reports[0].get('betId', '') if instruction_reports else ''
            logging.info(f"Quick bet SUCCESS: betId={bet_id}, matched={matched}")
            
            # Save to database
            self.db.save_bet(
                event_name=self.current_market.get('eventName', ''),
                market_id=self.current_market['marketId'],
                market_name=self.current_market.get('marketName', ''),
                bet_type=bet_type,
                selections=runner['runnerName'],
                total_stake=stake,
                potential_profit=(stake * (price - 1)) * 0.955 if bet_type == 'BACK' else stake * 0.955,
                status='PLACED'
            )
            
            # Broadcast to Copy Trading followers (always on SUCCESS - matched status comes later)
            logging.info(f"[COPY] Quick bet SUCCESS - about to broadcast")
            try:
                available = self.account_data.get('available', 100) if self.account_data else 100
                stake_percent = (stake / available * 100) if available > 0 else 1.0
                event_name = self.current_event.get('name', '') if self.current_event else self.current_market.get('eventName', '')
                self._broadcast_copy_bet(
                    event_name=event_name,
                    market_name=self.current_market.get('marketName', ''),
                    selection=runner['runnerName'],
                    side=bet_type,
                    price=price,
                    stake_percent=stake_percent,
                    stake_amount=stake
                )
                logging.info(f"[COPY] Quick bet broadcast call completed")
            except Exception as e:
                logging.error(f"[COPY] Quick bet broadcast FAILED: {e}")
            
            messagebox.showinfo("Successo", 
                f"Scommessa piazzata!\n\n"
                f"{runner['runnerName']} @ {price:.2f}\n"
                f"Stake: {format_currency(stake)}")
            
            self._update_balance()
        else:
            # Log detailed error info
            error_code = result.get('errorCode', 'N/A')
            instruction_reports = result.get('instructionReports', [])
            error_details = []
            for ir in instruction_reports:
                if ir.get('errorCode'):
                    error_details.append(ir.get('errorCode'))
            
            logging.warning(f"Quick bet failed: status={result.get('status')}, errorCode={error_code}, details={error_details}")
            
            # Show user-friendly error message
            error_msg = f"Stato: {result.get('status')}"
            if error_code != 'N/A':
                error_msg += f"\nErrore: {error_code}"
            if error_details:
                error_msg += f"\nDettagli: {', '.join(error_details)}"
            
            messagebox.showwarning("Attenzione", error_msg)
    
    def _set_bet_type(self, bet_type):
        """Set the bet type and update button colors."""
        self.bet_type_var.set(bet_type)
        
        if bet_type == 'BACK':
            # BACK selected - blue active, pink faded for LAY
            self.back_btn.configure(fg_color=COLORS['back'])
            self.lay_btn.configure(fg_color=COLORS['button_secondary'])
        else:
            # LAY selected - pink active, blue faded for BACK
            self.back_btn.configure(fg_color=COLORS['button_secondary'])
            self.lay_btn.configure(fg_color=COLORS['lay'])
        
        self._recalculate()
    
    def _clear_selections(self):
        """Clear all selections."""
        self.selected_runners = {}
        
        for item in self.runners_tree.get_children():
            values = list(self.runners_tree.item(item)['values'])
            values[0] = ''
            self.runners_tree.item(item, values=values)
        
        self.selections_text.configure(state=tk.NORMAL)
        self.selections_text.delete('1.0', tk.END)
        self.selections_text.configure(state=tk.DISABLED)
        
        self.profit_label.configure(text="Profitto: -")
        self.prob_label.configure(text="Probabilita Implicita: -")
        self.place_btn.configure(state=tk.DISABLED)
        self.calculated_results = None
    
    def _recalculate(self):
        """Recalculate dutching stakes."""
        if not self.selected_runners:
            self.selections_text.configure(state=tk.NORMAL)
            self.selections_text.delete('1.0', tk.END)
            self.selections_text.configure(state=tk.DISABLED)
            self.profit_label.configure(text="Profitto: -")
            self.prob_label.configure(text="Probabilita Implicita: -")
            self.place_btn.configure(state=tk.DISABLED)
            return
        
        self.selections_text.configure(state=tk.NORMAL)
        self.selections_text.delete('1.0', tk.END)
        
        try:
            total_stake = float(self.stake_var.get().replace(',', '.'))
        except ValueError:
            total_stake = 10.0
        
        bet_type = self.bet_type_var.get()
        
        # Update price for each selection based on current bet type
        for sel_id, sel in self.selected_runners.items():
            if bet_type == 'BACK':
                sel['price'] = sel.get('backPrice', 0)
            else:
                sel['price'] = sel.get('layPrice', 0)
        
        selections = list(self.selected_runners.values())
        
        try:
            results, profit, implied_prob = calculate_dutching_stakes(
                selections, total_stake, bet_type
            )
            
            text_lines = []
            for r in results:
                text_lines.append(f"{r['runnerName']}")
                text_lines.append(f"  Quota: {r['price']:.2f}")
                text_lines.append(f"  Stake: {format_currency(r['stake'])}")
                if bet_type == 'LAY':
                    text_lines.append(f"  Liability: {format_currency(r.get('liability', 0))}")
                    text_lines.append(f"  Se vince: {format_currency(r['profitIfWins'])}")
                else:
                    text_lines.append(f"  Profitto se vince: {format_currency(r['profitIfWins'])}")
                text_lines.append("")
            
            self.selections_text.insert('1.0', '\n'.join(text_lines))
            
            if bet_type == 'LAY' and results:
                # Show both best and worst case for LAY
                best = results[0].get('bestCase', profit)
                worst = results[0].get('worstCase', 0)
                self.profit_label.configure(text=f"Profitto Max: {format_currency(best)} | Rischio: {format_currency(worst)}")
            else:
                self.profit_label.configure(text=f"Profitto Atteso: {format_currency(profit)}")
            self.prob_label.configure(text=f"Probabilita Implicita: {implied_prob:.1f}%")
            
            errors = validate_selections(results, bet_type)
            if not errors:
                self.place_btn.configure(state=tk.NORMAL)
            else:
                self.place_btn.configure(state=tk.DISABLED)
                self.selections_text.insert(tk.END, "\nErrori:\n" + "\n".join(errors))
            
            self.calculated_results = results
            
        except Exception as e:
            self.selections_text.insert('1.0', f"Errore calcolo: {e}")
            self.profit_label.configure(text="Profitto: -")
            self.place_btn.configure(state=tk.DISABLED)
        
        self.selections_text.configure(state=tk.DISABLED)
    
    def _place_bets(self):
        """Place the calculated bets (real or simulated)."""
        logging.info("[DUTCHING] _place_bets called")
        
        # Reentrancy guard - prevent double placement
        if hasattr(self, '_placing_in_progress') and self._placing_in_progress:
            logging.warning("[DUTCHING] Placement already in progress, skipping")
            return
        
        if not hasattr(self, 'calculated_results') or not self.calculated_results:
            logging.warning("[DUTCHING] No calculated_results available")
            return
        
        if not self.current_market:
            logging.warning("[DUTCHING] No current_market selected")
            return
        
        logging.info(f"[DUTCHING] Placing {len(self.calculated_results)} bets:")
        for r in self.calculated_results:
            logging.info(f"[DUTCHING]   {r.get('runnerName', 'N/A')} @ {r.get('price', 0):.2f} -> stake={r.get('stake', 0):.2f}")
        
        # Check if market is suspended
        if self.market_status == 'SUSPENDED':
            messagebox.showwarning("Mercato Sospeso", 
                "Il mercato e' attualmente sospeso.\nAttendi che riapra per piazzare scommesse.")
            return
        
        if self.market_status == 'CLOSED':
            messagebox.showwarning("Mercato Chiuso", 
                "Il mercato e' chiuso. Non e' possibile piazzare scommesse.")
            return
        
        total_stake = sum(r['stake'] for r in self.calculated_results)
        potential_profit = self.calculated_results[0].get('profitIfWins', 0)
        bet_type = self.bet_type_var.get()
        
        # Different confirmation message for simulation mode
        if self.simulation_mode:
            sim_settings = self.db.get_simulation_settings()
            virtual_balance = sim_settings.get('virtual_balance', 0) if sim_settings else 0
            
            if total_stake > virtual_balance:
                messagebox.showwarning("Saldo Insufficiente", 
                    f"Saldo virtuale insufficiente.\n\n"
                    f"Stake richiesto: {format_currency(total_stake)}\n"
                    f"Saldo disponibile: {format_currency(virtual_balance)}")
                return
            
            msg = f"[SIMULAZIONE] Confermi il piazzamento virtuale?\n\n"
            msg += f"Scommesse: {len(self.calculated_results)}\n"
            msg += f"Stake Totale: {format_currency(total_stake)}\n"
            msg += f"Profitto Potenziale: {format_currency(potential_profit)}\n\n"
            msg += f"Saldo Attuale: {format_currency(virtual_balance)}\n"
            msg += f"Saldo Dopo: {format_currency(virtual_balance - total_stake)}"
        else:
            msg = f"Confermi il piazzamento di {len(self.calculated_results)} scommesse?\n\n"
            msg += f"Stake Totale: {format_currency(total_stake)}"
        
        if not messagebox.askyesno("Conferma Scommesse", msg):
            return
        
        use_best_price = self.best_price_var.get()
        market_id = self.current_market['marketId']
        
        self.place_btn.configure(state=tk.DISABLED)
        self._placing_in_progress = True
        
        # Handle simulation mode separately
        if self.simulation_mode:
            self._place_simulation_bets(total_stake, potential_profit, bet_type)
            self._placing_in_progress = False
            return
        
        def place():
            try:
                # Build instructions with current or best prices
                instructions = []
                
                if use_best_price:
                    # Fetch fresh prices before placing
                    book = self.client.get_market_book(market_id)
                    current_prices = {}
                    if book and book.get('runners'):
                        for runner in book['runners']:
                            sel_id = runner.get('selectionId')
                            ex = runner.get('ex', {})
                            if bet_type == 'BACK':
                                backs = ex.get('availableToBack', [])
                                if backs:
                                    current_prices[sel_id] = backs[0].get('price', 1.01)
                            else:  # LAY
                                lays = ex.get('availableToLay', [])
                                if lays:
                                    current_prices[sel_id] = lays[0].get('price', 1000)
                    
                    for r in self.calculated_results:
                        sel_id = r['selectionId']
                        # Use current best price if available, otherwise use calculated price
                        price = current_prices.get(sel_id, r['price'])
                        instructions.append({
                            'selectionId': sel_id,
                            'side': bet_type,
                            'price': price,
                            'size': r['stake']
                        })
                else:
                    # Use original calculated prices
                    for r in self.calculated_results:
                        instructions.append({
                            'selectionId': r['selectionId'],
                            'side': bet_type,
                            'price': r['price'],
                            'size': r['stake']
                        })
                
                logging.info(f"[DUTCHING] Calling place_bets with {len(instructions)} instructions")
                for inst in instructions:
                    logging.info(f"[DUTCHING]   Instruction: selId={inst['selectionId']}, side={inst['side']}, price={inst['price']}, size={inst['size']}")
                
                result = self.client.place_bets(market_id, instructions)
                logging.info(f"[DUTCHING] place_bets response: status={result.get('status')}")
                
                # Process each instruction report individually
                reports = result.get('instructionReports', [])
                logging.info(f"[DUTCHING] instructionReports count: {len(reports)}")
                for i, rep in enumerate(reports):
                    logging.info(f"[DUTCHING]   Report[{i}]: status={rep.get('status')}, sizeMatched={rep.get('sizeMatched', 0)}, betId={rep.get('betId')}")
                
                # Determine overall bet status from instruction statuses
                all_matched = all(r.get('status') == 'SUCCESS' and r.get('sizeMatched', 0) > 0 for r in reports)
                any_matched = any(r.get('sizeMatched', 0) > 0 for r in reports)
                
                if result['status'] == 'SUCCESS':
                    if all_matched:
                        bet_status = 'MATCHED'
                    elif any_matched:
                        bet_status = 'PARTIALLY_MATCHED'
                    else:
                        bet_status = 'PENDING'
                elif result['status'] == 'FAILURE':
                    bet_status = 'FAILED'
                else:
                    bet_status = result['status']
                
                # Add runner names and matched amounts to selections for storage
                selections_with_names = []
                for i, r in enumerate(self.calculated_results):
                    report = reports[i] if i < len(reports) else {}
                    selections_with_names.append({
                        'runnerName': r.get('runnerName', 'Unknown'),
                        'selectionId': r['selectionId'],
                        'price': r['price'],
                        'stake': r['stake'],
                        'sizeMatched': report.get('sizeMatched', 0),
                        'betId': report.get('betId'),
                        'instructionStatus': report.get('status', 'UNKNOWN')
                    })
                
                # Calculate actual matched amount
                total_matched = sum(r.get('sizeMatched', 0) for r in reports)
                
                self.db.save_bet(
                    self.current_event['name'],
                    self.current_market['marketId'],
                    self.current_market['marketName'],
                    bet_type,
                    selections_with_names,
                    total_stake,
                    self.calculated_results[0]['profitIfWins'],
                    bet_status
                )
                
                bet_result = result
                self.root.after(0, lambda r=bet_result: self._on_bets_placed(r))
            except Exception as e:
                err_msg = str(e)
                self.root.after(0, lambda msg=err_msg: self._on_bets_error(msg))
        
        threading.Thread(target=place, daemon=True).start()
    
    def _place_simulation_bets(self, total_stake, potential_profit, bet_type):
        """Place simulated bets without calling Betfair API."""
        try:
            # Get current simulation balance
            sim_settings = self.db.get_simulation_settings()
            virtual_balance = sim_settings.get('virtual_balance', 0)
            
            # Deduct stake from virtual balance and increment bet count
            new_balance = virtual_balance - total_stake
            self.db.increment_simulation_bet_count(new_balance)
            
            # Save simulation bet
            selections_info = [
                {'name': r.get('runnerName', 'Unknown'), 
                 'price': r['price'], 
                 'stake': r['stake']}
                for r in self.calculated_results
            ]
            
            self.db.save_simulation_bet(
                event_name=self.current_event['name'],
                market_id=self.current_market['marketId'],
                market_name=self.current_market['marketName'],
                side=bet_type,
                selections=selections_info,
                total_stake=total_stake,
                potential_profit=potential_profit
            )
            
            # Update display
            self._update_simulation_balance_display()
            self.place_btn.configure(state=tk.NORMAL)
            
            messagebox.showinfo("Simulazione", 
                f"Scommessa virtuale piazzata!\n\n"
                f"Stake: {format_currency(total_stake)}\n"
                f"Profitto Potenziale: {format_currency(potential_profit)}\n"
                f"Nuovo Saldo Virtuale: {format_currency(new_balance)}")
            
            self._clear_selections()
            
        except Exception as e:
            self.place_btn.configure(state=tk.NORMAL)
            messagebox.showerror("Errore Simulazione", f"Errore: {e}")
    
    def _on_bets_placed(self, result):
        """Handle successful bet placement."""
        self._placing_in_progress = False
        self.place_btn.configure(state=tk.NORMAL)
        
        if result['status'] == 'SUCCESS':
            matched = sum(r.get('sizeMatched', 0) for r in result.get('instructionReports', []))
            
            # Broadcast to Copy Trading followers (always on SUCCESS - matching happens asynchronously)
            if self.calculated_results:
                available = self.account_data.get('available', 100) if self.account_data else 100
                bet_type = self.bet_type_var.get()
                event_name = self.current_event.get('name', '') if self.current_event else self.current_market.get('eventName', '')
                
                if len(self.calculated_results) > 1:
                    # DUTCHING: Send single unified message with all selections
                    total_stake = sum(r['stake'] for r in self.calculated_results)
                    profit_target = self.calculated_results[0].get('profitIfWins', 0)
                    self._broadcast_copy_dutching(
                        event_name=event_name,
                        market_name=self.current_market.get('marketName', ''),
                        selections=self.calculated_results,
                        side=bet_type,
                        profit_target=profit_target,
                        total_stake=total_stake
                    )
                else:
                    # Single bet: use standard broadcast
                    r = self.calculated_results[0]
                    stake_percent = (r['stake'] / available * 100) if available > 0 else 1.0
                    self._broadcast_copy_bet(
                        event_name=event_name,
                        market_name=self.current_market.get('marketName', ''),
                        selection=r['runnerName'],
                        side=bet_type,
                        price=r['price'],
                        stake_percent=stake_percent,
                        stake_amount=r['stake']
                    )
            
            messagebox.showinfo("Successo", f"Scommesse piazzate!\nImporto matchato: {format_currency(matched)}")
            self._update_balance()
            self._clear_selections()
        else:
            # Log detailed error info
            logging.warning(f"[DUTCHING] Bet placement failed: {result}")
            error_code = result.get('errorCode', 'N/A')
            instruction_reports = result.get('instructionReports', [])
            error_details = []
            for ir in instruction_reports:
                if ir.get('errorCode'):
                    error_details.append(f"{ir.get('errorCode')}")
            
            error_msg = f"Stato: {result['status']}"
            if error_code != 'N/A':
                error_msg += f"\nErrore: {error_code}"
            if error_details:
                error_msg += f"\nDettagli: {', '.join(error_details)}"
            
            messagebox.showwarning("Attenzione", error_msg)
    
    def _on_bets_error(self, error):
        """Handle bet placement error."""
        self._placing_in_progress = False
        self.place_btn.configure(state=tk.NORMAL)
        messagebox.showerror("Errore", f"Errore piazzamento: {error}")
    
    def _show_about(self):
        """Show about dialog."""
        from database import get_db_path
        db_path = get_db_path()
        market_list = "\n".join([f"- {v}" for k, v in list(MARKET_TYPES.items())[:8]])
        messagebox.showinfo(
            "Informazioni",
            f"{APP_NAME}\n"
            f"Versione {APP_VERSION}\n\n"
            "Applicazione per dutching su Betfair Exchange Italia.\n\n"
            "Mercati supportati:\n"
            f"{market_list}\n"
            "...e altri\n\n"
            "Funzionalita:\n"
            "- Streaming quote in tempo reale\n"
            "- Calcolo automatico stake dutching\n"
            "- Dashboard con saldo e scommesse\n"
            "- Prenotazione quote\n"
            "- Cashout automatico\n\n"
            f"Database:\n{db_path}\n\n"
            "Requisiti:\n"
            "- Account Betfair Italia\n"
            "- Certificato SSL per API\n"
            "- App Key Betfair"
        )
    
    def _check_for_updates_on_startup(self):
        """Check for updates when app starts."""
        settings = self.db.get_settings() or {}
        
        # Use saved URL or default
        update_url = settings.get('update_url') or DEFAULT_UPDATE_URL
        if not update_url:
            return
        
        skipped_version = settings.get('skipped_version')
        
        def on_update_result(result):
            if result.get('update_available'):
                latest = result.get('latest_version', '')
                # Skip if user previously skipped this version
                if skipped_version and latest == skipped_version:
                    return
                
                # Show update dialog on main thread
                self.root.after(100, lambda: self._show_update_notification(result))
        
        check_for_updates(APP_VERSION, callback=on_update_result, update_url=update_url)
    
    def _show_update_notification(self, update_info):
        """Show update notification dialog."""
        choice = show_update_dialog(self.root, update_info)
        
        if choice == 'skip':
            # Save skipped version so we don't prompt again
            self.db.save_skipped_version(update_info.get('latest_version'))
    
    def _check_for_updates_manual(self):
        """Manually check for updates."""
        settings = self.db.get_settings() or {}
        update_url = settings.get('update_url') or DEFAULT_UPDATE_URL
        
        if not update_url:
            messagebox.showinfo("Aggiornamenti", 
                "Nessun URL di aggiornamento configurato.\n\n"
                "Vai su File > Configura Aggiornamenti per impostarlo.")
            return
        
        def on_result(result):
            if result.get('update_available'):
                self.root.after(0, lambda: self._show_update_notification(result))
            elif result.get('error'):
                self.root.after(0, lambda: messagebox.showerror("Errore", 
                    f"Impossibile verificare aggiornamenti:\n{result.get('error')}"))
            else:
                self.root.after(0, lambda: messagebox.showinfo("Aggiornamenti", 
                    f"Hai gia' l'ultima versione ({APP_VERSION})!"))
        
        check_for_updates(APP_VERSION, callback=on_result, update_url=update_url)
    
    def _show_update_settings_dialog(self):
        """Show dialog to configure auto-updates."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Configura Aggiornamenti")
        dialog.geometry("500x250")
        dialog.transient(self.root)
        dialog.grab_set()
        
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text="Configura Aggiornamenti Automatici", 
                  style='Title.TLabel').pack(pady=(0, 15))
        
        settings = self.db.get_settings() or {}
        
        ttk.Label(frame, text="URL GitHub Releases API:").pack(anchor=tk.W)
        ttk.Label(frame, text=f"(Default: {DEFAULT_UPDATE_URL})", 
                  foreground='gray', font=('Segoe UI', 8)).pack(anchor=tk.W)
        
        url_var = tk.StringVar(value=settings.get('update_url', '') or DEFAULT_UPDATE_URL)
        url_entry = ttk.Entry(frame, textvariable=url_var, width=60)
        url_entry.pack(fill=tk.X, pady=(5, 15))
        
        ttk.Label(frame, text="L'app controllera' automaticamente gli aggiornamenti all'avvio.", 
                  foreground='gray').pack(anchor=tk.W)
        
        def save():
            self.db.save_update_url(url_var.get().strip())
            self.db.save_skipped_version(None)  # Reset skipped version
            dialog.destroy()
            messagebox.showinfo("Salvato", "Impostazioni aggiornamento salvate!")
        
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=15)
        ttk.Button(btn_frame, text="Salva", command=save).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Annulla", command=dialog.destroy).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Verifica Ora", 
                   command=lambda: [dialog.destroy(), self._check_for_updates_manual()]).pack(side=tk.RIGHT, padx=5)
    
    def _toggle_live_mode(self):
        """Toggle live-only mode."""
        if not self.client:
            messagebox.showwarning("Attenzione", "Devi prima connetterti")
            return
        
        self.live_mode = not self.live_mode
        
        if self.live_mode:
            self.live_btn.configure(fg_color=COLORS['success'], text="LIVE ON")
            self._load_live_events()
            self._start_live_refresh()
        else:
            self.live_btn.configure(fg_color=COLORS['loss'], text="LIVE")
            self._stop_live_refresh()
            self._load_events()  # Load all events
    
    def _load_live_events(self):
        """Load only live/in-play events."""
        if not self.client:
            return
        
        def fetch():
            try:
                events = self.client.get_live_events_only()
                live_events = events
                self.root.after(0, lambda evts=live_events: self._populate_events(evts, live_only=True))
            except Exception as e:
                err_msg = str(e)
                self.root.after(0, lambda msg=err_msg: messagebox.showerror("Errore", msg))
        
        threading.Thread(target=fetch, daemon=True).start()
    
    def _start_live_refresh(self):
        """Start auto-refresh for live odds."""
        self._stop_live_refresh()  # Cancel any existing timer
        self._do_live_refresh()
    
    def _do_live_refresh(self):
        """Single live refresh cycle."""
        if not self.live_mode:
            return
        if self.current_market:
            self._refresh_prices()
        # Schedule next refresh
        self.live_refresh_id = self.root.after(LIVE_REFRESH_INTERVAL, self._do_live_refresh)
    
    def _stop_live_refresh(self):
        """Stop auto-refresh for live odds."""
        if self.live_refresh_id:
            self.root.after_cancel(self.live_refresh_id)
            self.live_refresh_id = None
    
    def _create_dashboard_tab(self):
        """Create dashboard tab content."""
        main_frame = ctk.CTkFrame(self.dashboard_tab, fg_color='transparent')
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        ctk.CTkLabel(main_frame, text="Dashboard - Account Betfair Italy", 
                     font=FONTS['title'], text_color=COLORS['text_primary']).pack(anchor=tk.W, pady=(0, 20))
        
        self.dashboard_stats_frame = ctk.CTkFrame(main_frame, fg_color='transparent')
        self.dashboard_stats_frame.pack(fill=tk.X, pady=10)
        
        self.dashboard_not_connected = ctk.CTkLabel(main_frame, text="Connettiti a Betfair per vedere i dati", 
                                                     font=('Segoe UI', 11), text_color=COLORS['text_secondary'])
        self.dashboard_not_connected.pack(pady=20)
        
        ctk.CTkButton(main_frame, text="Aggiorna Dashboard", command=self._refresh_dashboard_tab,
                      fg_color=COLORS['button_primary'], hover_color=COLORS['back_hover'],
                      corner_radius=6).pack(anchor=tk.E, pady=10)
        
        # Dashboard sub-tabs (using CTkTabview)
        self.dashboard_notebook = ctk.CTkTabview(main_frame, fg_color=COLORS['bg_panel'],
                                                  segmented_button_fg_color=COLORS['bg_card'],
                                                  segmented_button_selected_color=COLORS['back'],
                                                  segmented_button_unselected_color=COLORS['bg_card'])
        self.dashboard_notebook.pack(fill=tk.BOTH, expand=True, pady=10)
        
        self.dashboard_notebook.add("Scommesse Recenti")
        self.dashboard_notebook.add("Ordini Correnti")
        self.dashboard_notebook.add("Prenotazioni")
        self.dashboard_notebook.add("Cashout")
        self.dashboard_notebook.add("Statistiche")
        self.dashboard_notebook.add("Storico P&L")
        self.dashboard_notebook.add("Audit Telegram")
        self.dashboard_notebook.add("Log Errori")
        self.dashboard_notebook.add("Performance")
        
        self.dashboard_recent_frame = self.dashboard_notebook.tab("Scommesse Recenti")
        self.dashboard_orders_frame = self.dashboard_notebook.tab("Ordini Correnti")
        self.dashboard_bookings_frame = self.dashboard_notebook.tab("Prenotazioni")
        self.dashboard_cashout_frame = self.dashboard_notebook.tab("Cashout")
        self.dashboard_stats_tab_frame = self.dashboard_notebook.tab("Statistiche")
        self.dashboard_pnl_frame = self.dashboard_notebook.tab("Storico P&L")
        self.dashboard_telegram_audit_frame = self.dashboard_notebook.tab("Audit Telegram")
        self.dashboard_error_log_frame = self.dashboard_notebook.tab("Log Errori")
        self.dashboard_performance_frame = self.dashboard_notebook.tab("Performance")
    
    def _refresh_dashboard_tab(self):
        """Refresh dashboard tab data."""
        if not self.client:
            self.dashboard_not_connected.configure(text="Connettiti a Betfair per vedere i dati")
            return
        
        self.dashboard_not_connected.configure(text="")
        
        def create_stat_card(parent, title, value, subtitle, col):
            card = ctk.CTkFrame(parent, fg_color=COLORS['bg_card'], corner_radius=8)
            card.grid(row=0, column=col, padx=5, sticky='nsew')
            ctk.CTkLabel(card, text=title, font=('Segoe UI', 9), 
                        text_color=COLORS['text_secondary']).pack(pady=(10, 2))
            ctk.CTkLabel(card, text=value, font=FONTS['title'], 
                        text_color=COLORS['text_primary']).pack()
            ctk.CTkLabel(card, text=subtitle, font=('Segoe UI', 8), 
                        text_color=COLORS['text_tertiary']).pack(pady=(2, 10))
            return card
        
        def fetch_data():
            try:
                funds = self.client.get_account_funds()
                self.account_data = funds
                daily_pl = self.db.get_today_profit_loss()
                try:
                    orders = self.client.get_current_orders()
                    active_count = len([o for o in orders.get('matched', []) if o.get('sizeMatched', 0) > 0])
                except:
                    active_count = self.db.get_active_bets_count()
                
                try:
                    settled_bets = self.client.get_settled_bets(days=7)
                except:
                    settled_bets = []
                
                self.root.after(0, lambda: update_ui(funds, daily_pl, active_count, orders, settled_bets))
            except Exception as e:
                err_msg = str(e)
                self.root.after(0, lambda msg=err_msg: messagebox.showerror("Errore", msg))
        
        def update_ui(funds, daily_pl, active_count, orders, settled_bets=None):
            for widget in self.dashboard_stats_frame.winfo_children():
                widget.destroy()
            
            create_stat_card(self.dashboard_stats_frame, "Saldo Disponibile", 
                            f"{funds.get('available', 0):.2f} EUR", 
                            "Fondi disponibili", 0)
            create_stat_card(self.dashboard_stats_frame, "Esposizione", 
                            f"{abs(funds.get('exposure', 0)):.2f} EUR", 
                            "Responsabilita corrente", 1)
            pl_text = f"+{daily_pl:.2f}" if daily_pl >= 0 else f"{daily_pl:.2f}"
            create_stat_card(self.dashboard_stats_frame, "P/L Oggi", 
                            f"{pl_text} EUR", 
                            "Profitto/Perdita giornaliero", 2)
            create_stat_card(self.dashboard_stats_frame, "Scommesse Attive", 
                            str(active_count), 
                            "In attesa di risultato", 3)
            
            for i in range(4):
                self.dashboard_stats_frame.columnconfigure(i, weight=1)
            
            for widget in self.dashboard_recent_frame.winfo_children():
                widget.destroy()
            self._create_settled_bets_list(self.dashboard_recent_frame, settled_bets or [])
            
            for widget in self.dashboard_orders_frame.winfo_children():
                widget.destroy()
            self._create_current_orders_view(self.dashboard_orders_frame)
            
            for widget in self.dashboard_bookings_frame.winfo_children():
                widget.destroy()
            self._create_bookings_view(self.dashboard_bookings_frame)
            
            for widget in self.dashboard_cashout_frame.winfo_children():
                widget.destroy()
            self._create_cashout_view(self.dashboard_cashout_frame, None)
            
            for widget in self.dashboard_stats_tab_frame.winfo_children():
                widget.destroy()
            self._create_statistics_view(self.dashboard_stats_tab_frame)
            
            for widget in self.dashboard_pnl_frame.winfo_children():
                widget.destroy()
            self.persistent_storage.rebuild_daily_pnl(days=30)
            self._create_pnl_history_view(self.dashboard_pnl_frame)
            
            for widget in self.dashboard_telegram_audit_frame.winfo_children():
                widget.destroy()
            self._create_telegram_audit_view(self.dashboard_telegram_audit_frame)
            
            for widget in self.dashboard_error_log_frame.winfo_children():
                widget.destroy()
            self._create_error_log_view(self.dashboard_error_log_frame)
            
            for widget in self.dashboard_performance_frame.winfo_children():
                widget.destroy()
            self._create_performance_view(self.dashboard_performance_frame)
        
        threading.Thread(target=fetch_data, daemon=True).start()
    
    def _create_statistics_view(self, parent):
        """Create statistics view with bet outcomes."""
        stats = self.db.get_bet_statistics()
        
        # Title
        ctk.CTkLabel(parent, text="Statistiche Scommesse", 
                     font=FONTS['title'], text_color=COLORS['text_primary']).pack(anchor=tk.W, pady=(10, 20))
        
        # Stats cards grid
        cards_frame = ctk.CTkFrame(parent, fg_color='transparent')
        cards_frame.pack(fill=tk.X, pady=10)
        
        def create_stat_card(parent, title, value, color, col):
            card = ctk.CTkFrame(parent, fg_color=COLORS['bg_card'], corner_radius=8)
            card.grid(row=0, column=col, padx=5, sticky='nsew')
            ctk.CTkLabel(card, text=title, font=('Segoe UI', 10), 
                        text_color=COLORS['text_secondary']).pack(pady=(15, 5))
            ctk.CTkLabel(card, text=str(value), font=('Segoe UI Bold', 24), 
                        text_color=color).pack()
            card.grid_columnconfigure(0, weight=1)
            return card
        
        # Stats cards
        create_stat_card(cards_frame, "Totali", stats['total'], COLORS['text_primary'], 0)
        create_stat_card(cards_frame, "Vinte", stats['won'], COLORS['success'], 1)
        create_stat_card(cards_frame, "Perse", stats['lost'], COLORS['loss'], 2)
        create_stat_card(cards_frame, "Void", stats['void'], COLORS['text_secondary'], 3)
        create_stat_card(cards_frame, "In Attesa", stats['pending'], COLORS['warning'], 4)
        
        for i in range(5):
            cards_frame.columnconfigure(i, weight=1)
        
        # P/L and Win Rate section
        summary_frame = ctk.CTkFrame(parent, fg_color=COLORS['bg_card'], corner_radius=8)
        summary_frame.pack(fill=tk.X, pady=20, padx=5)
        
        pl_color = COLORS['success'] if stats['total_pl'] >= 0 else COLORS['loss']
        pl_text = f"+{stats['total_pl']:.2f}" if stats['total_pl'] >= 0 else f"{stats['total_pl']:.2f}"
        
        pl_frame = ctk.CTkFrame(summary_frame, fg_color='transparent')
        pl_frame.pack(side=tk.LEFT, padx=30, pady=15)
        ctk.CTkLabel(pl_frame, text="Profitto/Perdita Totale", 
                     font=('Segoe UI', 11), text_color=COLORS['text_secondary']).pack()
        ctk.CTkLabel(pl_frame, text=f"{pl_text} EUR", 
                     font=('Segoe UI Bold', 20), text_color=pl_color).pack()
        
        wr_frame = ctk.CTkFrame(summary_frame, fg_color='transparent')
        wr_frame.pack(side=tk.LEFT, padx=30, pady=15)
        ctk.CTkLabel(wr_frame, text="Win Rate", 
                     font=('Segoe UI', 11), text_color=COLORS['text_secondary']).pack()
        ctk.CTkLabel(wr_frame, text=f"{stats['win_rate']:.1f}%", 
                     font=('Segoe UI Bold', 20), text_color=COLORS['text_primary']).pack()
        
        # Refresh button
        ctk.CTkButton(parent, text="Aggiorna Statistiche", 
                      command=lambda: self._refresh_statistics_view(parent),
                      fg_color=COLORS['button_primary'], hover_color=COLORS['back_hover'],
                      corner_radius=6).pack(anchor=tk.E, pady=10)
    
    def _refresh_statistics_view(self, parent):
        """Refresh the statistics view."""
        for widget in parent.winfo_children():
            widget.destroy()
        self._create_statistics_view(parent)
    
    def _create_pnl_history_view(self, parent):
        """Create P&L history view from persistent storage."""
        ctk.CTkLabel(parent, text="Storico Profitti/Perdite (da Database)", 
                     font=FONTS['title'], text_color=COLORS['text_primary']).pack(anchor=tk.W, pady=(10, 5))
        
        kpis = self.persistent_storage.get_dashboard_kpis()
        
        kpi_frame = ctk.CTkFrame(parent, fg_color='transparent')
        kpi_frame.pack(fill=tk.X, pady=10)
        
        def create_kpi_card(parent, title, value, color, col):
            card = ctk.CTkFrame(parent, fg_color=COLORS['bg_card'], corner_radius=8)
            card.grid(row=0, column=col, padx=5, sticky='nsew')
            ctk.CTkLabel(card, text=title, font=('Segoe UI', 9), 
                        text_color=COLORS['text_secondary']).pack(pady=(10, 2))
            ctk.CTkLabel(card, text=str(value), font=('Segoe UI Bold', 18), 
                        text_color=color).pack()
            ctk.CTkLabel(card, text="", font=('Segoe UI', 8)).pack(pady=(0, 10))
            return card
        
        pnl = kpis.get('total_pnl', 0)
        pnl_color = COLORS['success'] if pnl >= 0 else COLORS['loss']
        pnl_text = f"+{pnl:.2f}" if pnl >= 0 else f"{pnl:.2f}"
        
        create_kpi_card(kpi_frame, "Scommesse Totali", kpis.get('total_bets', 0), COLORS['text_primary'], 0)
        create_kpi_card(kpi_frame, "P&L Totale", f"{pnl_text} EUR", pnl_color, 1)
        create_kpi_card(kpi_frame, "Win Rate", f"{kpis.get('winrate', 0):.1f}%", COLORS['text_primary'], 2)
        create_kpi_card(kpi_frame, "Media Profitto", f"{kpis.get('avg_profit', 0):.2f} EUR", COLORS['text_secondary'], 3)
        create_kpi_card(kpi_frame, "Commissioni", f"{kpis.get('total_commission', 0):.2f} EUR", COLORS['warning'], 4)
        
        for i in range(5):
            kpi_frame.columnconfigure(i, weight=1)
        
        try:
            import matplotlib.pyplot as plt
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
            
            equity_data = self.persistent_storage.get_equity_curve(days=30)
            if equity_data and len(equity_data) > 1:
                ctk.CTkLabel(parent, text="Equity Curve", 
                            font=('Segoe UI', 12, 'bold'), text_color=COLORS['text_primary']).pack(anchor=tk.W, pady=(15, 5))
                
                chart_frame = ctk.CTkFrame(parent, fg_color=COLORS['bg_card'], corner_radius=8, height=200)
                chart_frame.pack(fill=tk.X, pady=5, padx=5)
                chart_frame.pack_propagate(False)
                
                fig = Figure(figsize=(8, 2.5), dpi=100, facecolor=COLORS['bg_card'])
                ax = fig.add_subplot(111)
                
                x_vals = list(range(len(equity_data)))
                y_vals = [d['equity'] for d in equity_data]
                
                line_color = COLORS['success'] if y_vals[-1] >= 0 else COLORS['loss']
                ax.plot(x_vals, y_vals, color=line_color, linewidth=2)
                ax.fill_between(x_vals, y_vals, alpha=0.3, color=line_color)
                ax.axhline(y=0, color='gray', linestyle='--', linewidth=0.5)
                
                ax.set_facecolor(COLORS['bg_main'])
                ax.tick_params(colors=COLORS['text_secondary'], labelsize=8)
                ax.spines['top'].set_visible(False)
                ax.spines['right'].set_visible(False)
                ax.spines['bottom'].set_color(COLORS['text_secondary'])
                ax.spines['left'].set_color(COLORS['text_secondary'])
                ax.set_ylabel('EUR', fontsize=9, color=COLORS['text_secondary'])
                ax.grid(True, alpha=0.2)
                
                fig.tight_layout()
                
                canvas = FigureCanvasTkAgg(fig, master=chart_frame)
                canvas.draw()
                canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        except ImportError:
            pass
        except Exception as e:
            logging.warning(f"Equity chart error: {e}")
        
        ctk.CTkLabel(parent, text="P&L Giornaliero (ultimi 30 giorni)", 
                     font=('Segoe UI', 12, 'bold'), text_color=COLORS['text_primary']).pack(anchor=tk.W, pady=(20, 5))
        
        daily_pnl = self.persistent_storage.get_daily_pnl(days=30)
        
        columns = ('giorno', 'scommesse', 'vinte', 'perse', 'pnl')
        tree = ttk.Treeview(parent, columns=columns, show='headings', height=10)
        tree.heading('giorno', text='Giorno')
        tree.heading('scommesse', text='Scommesse')
        tree.heading('vinte', text='Vinte')
        tree.heading('perse', text='Perse')
        tree.heading('pnl', text='P&L')
        tree.column('giorno', width=100)
        tree.column('scommesse', width=80)
        tree.column('vinte', width=60)
        tree.column('perse', width=60)
        tree.column('pnl', width=100)
        
        for day_data in daily_pnl:
            pnl_val = day_data.get('total_pnl', 0) or 0
            pnl_str = f"+{pnl_val:.2f}" if pnl_val >= 0 else f"{pnl_val:.2f}"
            tree.insert('', 'end', values=(
                day_data.get('day', ''),
                day_data.get('bets', 0),
                day_data.get('won', 0),
                day_data.get('lost', 0),
                pnl_str
            ))
        
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=5)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=5)
    
    def _create_telegram_audit_view(self, parent):
        """Create Telegram audit view from persistent storage."""
        ctk.CTkLabel(parent, text="Audit Messaggi Telegram", 
                     font=FONTS['title'], text_color=COLORS['text_primary']).pack(anchor=tk.W, pady=(10, 5))
        
        metrics = self.persistent_storage.get_telegram_metrics()
        
        metrics_frame = ctk.CTkFrame(parent, fg_color=COLORS['bg_card'], corner_radius=8)
        metrics_frame.pack(fill=tk.X, pady=10, padx=5)
        
        info_text = (f"Totale messaggi: {metrics.get('total', 0)} | "
                    f"Elaborati: {metrics.get('processed', 0)} | "
                    f"Falliti: {metrics.get('failed', 0)} | "
                    f"Tempo medio: {metrics.get('avg_processing_time_ms', 0):.0f}ms")
        ctk.CTkLabel(metrics_frame, text=info_text, font=('Segoe UI', 10), 
                    text_color=COLORS['text_secondary']).pack(pady=10, padx=10)
        
        history = self.persistent_storage.get_telegram_history(limit=100)
        
        columns = ('data', 'chat', 'azione', 'stato', 'messaggio')
        tree = ttk.Treeview(parent, columns=columns, show='headings', height=12)
        tree.heading('data', text='Data')
        tree.heading('chat', text='Chat')
        tree.heading('azione', text='Azione')
        tree.heading('stato', text='Stato')
        tree.heading('messaggio', text='Messaggio')
        tree.column('data', width=130)
        tree.column('chat', width=120)
        tree.column('azione', width=120)
        tree.column('stato', width=80)
        tree.column('messaggio', width=300)
        
        for record in history:
            msg_preview = (record.get('message_text') or '')[:50]
            if len(record.get('message_text') or '') > 50:
                msg_preview += '...'
            tree.insert('', 'end', values=(
                record.get('created_at', '')[:19] if record.get('created_at') else '',
                record.get('chat_name') or record.get('chat_id', ''),
                record.get('action', ''),
                record.get('status', ''),
                msg_preview
            ))
        
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=5)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=5)
    
    def _create_error_log_view(self, parent):
        """Create error log view from persistent storage."""
        ctk.CTkLabel(parent, text="Log Errori e Problemi", 
                     font=FONTS['title'], text_color=COLORS['text_primary']).pack(anchor=tk.W, pady=(10, 5))
        
        ctk.CTkLabel(parent, text="Ultimi 100 errori registrati nel database", 
                     font=('Segoe UI', 10), text_color=COLORS['text_secondary']).pack(anchor=tk.W, pady=(0, 10))
        
        errors = self.persistent_storage.get_recent_errors(limit=100)
        
        columns = ('data', 'livello', 'sorgente', 'messaggio')
        tree = ttk.Treeview(parent, columns=columns, show='headings', height=15)
        tree.heading('data', text='Data/Ora')
        tree.heading('livello', text='Livello')
        tree.heading('sorgente', text='Sorgente')
        tree.heading('messaggio', text='Messaggio')
        tree.column('data', width=140)
        tree.column('livello', width=70)
        tree.column('sorgente', width=120)
        tree.column('messaggio', width=400)
        
        tree.tag_configure('error', foreground='#dc3545')
        tree.tag_configure('warning', foreground='#ffc107')
        tree.tag_configure('info', foreground='#17a2b8')
        
        for err in errors:
            level = err.get('level', 'INFO')
            tag = level.lower() if level.lower() in ('error', 'warning', 'info') else ''
            msg_preview = (err.get('message') or '')[:80]
            if len(err.get('message') or '') > 80:
                msg_preview += '...'
            tree.insert('', 'end', values=(
                err.get('created_at', '')[:19] if err.get('created_at') else '',
                level,
                err.get('source', ''),
                msg_preview
            ), tags=(tag,))
        
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=5)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=5)
        
        btn_frame = ctk.CTkFrame(parent, fg_color='transparent')
        btn_frame.pack(fill=tk.X, pady=10)
        
        def export_errors():
            try:
                import csv
                from tkinter import filedialog
                filename = filedialog.asksaveasfilename(
                    defaultextension='.csv',
                    filetypes=[('CSV files', '*.csv')],
                    title='Esporta Log Errori'
                )
                if filename:
                    with open(filename, 'w', newline='', encoding='utf-8') as f:
                        writer = csv.writer(f)
                        writer.writerow(['Data', 'Livello', 'Sorgente', 'Messaggio', 'Dettagli'])
                        for err in errors:
                            writer.writerow([
                                err.get('created_at', ''),
                                err.get('level', ''),
                                err.get('source', ''),
                                err.get('message', ''),
                                err.get('details', '')
                            ])
                    messagebox.showinfo("Esportazione", f"Log esportati in {filename}")
            except Exception as e:
                messagebox.showerror("Errore", f"Esportazione fallita: {e}")
        
        ctk.CTkButton(btn_frame, text="Esporta CSV", command=export_errors,
                      fg_color=COLORS['button_primary'], hover_color=COLORS['back_hover'],
                      corner_radius=6).pack(side=tk.RIGHT, padx=5)
    
    def _create_performance_view(self, parent):
        """Create performance metrics view."""
        from betfair_client import get_performance_metrics, get_market_cache
        
        ctk.CTkLabel(parent, text="Metriche Performance", 
                     font=FONTS['title'], text_color=COLORS['text_primary']).pack(anchor=tk.W, pady=(10, 5))
        
        ctk.CTkLabel(parent, text="Statistiche API Betfair e sistema", 
                     font=('Segoe UI', 10), text_color=COLORS['text_secondary']).pack(anchor=tk.W, pady=(0, 15))
        
        try:
            perf = get_performance_metrics()
            metrics = perf.get_metrics()
            cache_stats = metrics.get('cache_stats', {})
        except:
            metrics = {}
            cache_stats = {}
        
        metrics_frame = ctk.CTkFrame(parent, fg_color='transparent')
        metrics_frame.pack(fill=tk.X, pady=10)
        
        def create_metric_card(parent, title, value, subtitle, col, row=0):
            card = ctk.CTkFrame(parent, fg_color=COLORS['bg_card'], corner_radius=8)
            card.grid(row=row, column=col, padx=5, pady=5, sticky='nsew')
            ctk.CTkLabel(card, text=title, font=('Segoe UI', 9), 
                        text_color=COLORS['text_secondary']).pack(pady=(10, 2))
            ctk.CTkLabel(card, text=str(value), font=('Segoe UI Bold', 18), 
                        text_color=COLORS['text_primary']).pack()
            ctk.CTkLabel(card, text=subtitle, font=('Segoe UI', 8), 
                        text_color=COLORS['text_tertiary']).pack(pady=(2, 10))
        
        create_metric_card(metrics_frame, "API Calls/min", metrics.get('api_calls_per_min', 0), "Ultimo minuto", 0)
        create_metric_card(metrics_frame, "Latenza Media", f"{metrics.get('avg_latency_ms', 0)}ms", "Ultimi 100 call", 1)
        create_metric_card(metrics_frame, "Replace Eseguiti", metrics.get('replace_executed', 0), "Ordini modificati", 2)
        create_metric_card(metrics_frame, "Replace Saltati", metrics.get('replace_skipped', 0), "Ottimizzazione", 3)
        create_metric_card(metrics_frame, "Uptime", f"{metrics.get('uptime_min', 0)} min", "Sessione corrente", 4)
        
        for i in range(5):
            metrics_frame.columnconfigure(i, weight=1)
        
        ctk.CTkLabel(parent, text="Cache Market Book", 
                     font=('Segoe UI', 12, 'bold'), text_color=COLORS['text_primary']).pack(anchor=tk.W, pady=(20, 10))
        
        cache_frame = ctk.CTkFrame(parent, fg_color=COLORS['bg_card'], corner_radius=8)
        cache_frame.pack(fill=tk.X, pady=5, padx=5)
        
        cache_info = ctk.CTkFrame(cache_frame, fg_color='transparent')
        cache_info.pack(fill=tk.X, padx=15, pady=15)
        
        hit_rate = cache_stats.get('hit_rate', 0)
        hit_color = COLORS['success'] if hit_rate >= 50 else COLORS['warning'] if hit_rate >= 20 else COLORS['loss']
        
        ctk.CTkLabel(cache_info, text=f"Hit Rate: {hit_rate}%", 
                    font=('Segoe UI Bold', 14), text_color=hit_color).pack(side=tk.LEFT, padx=10)
        ctk.CTkLabel(cache_info, text=f"Hits: {cache_stats.get('hits', 0)}", 
                    font=('Segoe UI', 11), text_color=COLORS['text_secondary']).pack(side=tk.LEFT, padx=10)
        ctk.CTkLabel(cache_info, text=f"Misses: {cache_stats.get('misses', 0)}", 
                    font=('Segoe UI', 11), text_color=COLORS['text_secondary']).pack(side=tk.LEFT, padx=10)
        ctk.CTkLabel(cache_info, text=f"API Calls Risparmiate: {cache_stats.get('api_calls_saved', 0)}", 
                    font=('Segoe UI', 11), text_color=COLORS['success']).pack(side=tk.LEFT, padx=10)
        
        telegram_frame = ctk.CTkFrame(parent, fg_color=COLORS['bg_card'], corner_radius=8)
        telegram_frame.pack(fill=tk.X, pady=15, padx=5)
        
        tg_info = ctk.CTkFrame(telegram_frame, fg_color='transparent')
        tg_info.pack(fill=tk.X, padx=15, pady=15)
        
        tg_metrics = self.persistent_storage.get_telegram_metrics()
        queue_depth = 0
        if hasattr(self, 'telegram_listener') and self.telegram_listener:
            if hasattr(self.telegram_listener, '_broadcast_queue') and self.telegram_listener._broadcast_queue:
                queue_depth = self.telegram_listener._broadcast_queue.qsize() if hasattr(self.telegram_listener._broadcast_queue, 'qsize') else 0
        
        ctk.CTkLabel(tg_info, text="Telegram:", 
                    font=('Segoe UI Bold', 12), text_color=COLORS['text_primary']).pack(side=tk.LEFT, padx=5)
        ctk.CTkLabel(tg_info, text=f"Coda: {queue_depth}", 
                    font=('Segoe UI', 11), text_color=COLORS['text_secondary']).pack(side=tk.LEFT, padx=10)
        ctk.CTkLabel(tg_info, text=f"Media elaborazione: {tg_metrics.get('avg_processing_time_ms', 0):.0f}ms", 
                    font=('Segoe UI', 11), text_color=COLORS['text_secondary']).pack(side=tk.LEFT, padx=10)
        ctk.CTkLabel(tg_info, text=f"Totale: {tg_metrics.get('total', 0)} messaggi", 
                    font=('Segoe UI', 11), text_color=COLORS['text_secondary']).pack(side=tk.LEFT, padx=10)
        
        ctk.CTkButton(parent, text="Reset Metriche", 
                      command=lambda: (get_performance_metrics().reset(), self._create_performance_view(parent)),
                      fg_color=COLORS['button_secondary'], hover_color=COLORS['back_hover'],
                      corner_radius=6).pack(anchor=tk.E, pady=15)
    
    def _create_simulation_bets_list(self, parent):
        """Create list of simulation bets."""
        sim_bets = self.db.get_simulation_bets(limit=50)
        sim_settings = self.db.get_simulation_settings()
        
        if sim_settings:
            balance = sim_settings.get('virtual_balance', 1000)
            starting = sim_settings.get('starting_balance', 1000)
            pl = balance - starting
            pl_text = f"+{pl:.2f}" if pl >= 0 else f"{pl:.2f}"
            info_frame = ttk.Frame(parent)
            info_frame.pack(fill=tk.X, pady=(0, 10))
            ttk.Label(info_frame, text=f"Saldo Simulato: {balance:.2f} EUR", 
                     font=('Segoe UI', 10, 'bold')).pack(side=tk.LEFT)
            ttk.Label(info_frame, text=f"  |  P/L: {pl_text} EUR", 
                     foreground='#28a745' if pl >= 0 else '#dc3545').pack(side=tk.LEFT)
        
        columns = ('data', 'evento', 'mercato', 'tipo', 'stake', 'profitto')
        tree = ttk.Treeview(parent, columns=columns, show='headings', height=12)
        tree.heading('data', text='Data')
        tree.heading('evento', text='Evento')
        tree.heading('mercato', text='Mercato')
        tree.heading('tipo', text='Tipo')
        tree.heading('stake', text='Stake')
        tree.heading('profitto', text='Profitto')
        tree.column('data', width=100)
        tree.column('evento', width=150)
        tree.column('mercato', width=120)
        tree.column('tipo', width=50)
        tree.column('stake', width=70)
        tree.column('profitto', width=80)
        
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        if not sim_bets:
            ttk.Label(parent, text="Nessuna scommessa simulata", font=('Segoe UI', 10)).pack(pady=20)
        
        for bet in sim_bets:
            placed_at = bet.get('placed_at', '')[:16] if bet.get('placed_at') else ''
            profit = bet.get('potential_profit', 0)
            profit_display = f"+{profit:.2f}" if profit and profit > 0 else f"{profit:.2f}" if profit else "-"
            
            tree.insert('', tk.END, values=(
                placed_at,
                bet.get('event_name', '')[:25],
                bet.get('market_name', '')[:20],
                bet.get('side', ''),
                f"{bet.get('total_stake', 0):.2f}",
                profit_display
            ))
    
    def _create_telegram_tab(self):
        """Create Telegram tab content with sub-tabs."""
        main_frame = ctk.CTkFrame(self.telegram_tab, fg_color='transparent')
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        left_container = ctk.CTkFrame(main_frame, fg_color='transparent', width=450)
        left_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(0, 10))
        left_container.pack_propagate(False)
        
        subtab_btn_frame = ctk.CTkFrame(left_container, fg_color='transparent')
        subtab_btn_frame.pack(fill=tk.X, pady=(0, 5))
        
        self.tg_subtab_frames = {}
        self.tg_subtab_buttons = {}
        
        subtab_names = [
            ("config", "Copy Trading"),
            ("chats", "Chat Monitorate"),
            ("available", "Chat Disponibili"),
            ("rules", "Regole Parsing")
        ]
        
        for key, label in subtab_names:
            btn = ctk.CTkButton(subtab_btn_frame, text=label, width=105, height=28,
                                fg_color=COLORS['button_secondary'], hover_color=COLORS['bg_hover'],
                                corner_radius=4, font=('Segoe UI', 9),
                                command=lambda k=key: self._switch_telegram_subtab(k))
            btn.pack(side=tk.LEFT, padx=2)
            self.tg_subtab_buttons[key] = btn
        
        subtab_container = ctk.CTkFrame(left_container, fg_color='transparent')
        subtab_container.pack(fill=tk.BOTH, expand=True)
        
        right_frame = ctk.CTkFrame(main_frame, fg_color='transparent')
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        config_frame = ctk.CTkFrame(subtab_container, fg_color=COLORS['bg_panel'], corner_radius=8)
        self.tg_subtab_frames['config'] = config_frame
        
        settings = self.db.get_telegram_settings() or {}
        
        # Copy Trading Section (main content of this tab now)
        ctk.CTkLabel(config_frame, text="Copy Trading", font=FONTS['heading'],
                     text_color=COLORS['text_primary']).pack(anchor=tk.W, padx=10, pady=(10, 2))
        ctk.CTkLabel(config_frame, text="Master invia operazioni ai follower, Follower le riceve e copia", 
                     font=('Segoe UI', 8), text_color=COLORS['text_tertiary']).pack(anchor=tk.W, padx=10)
        
        self.tg_copy_mode_var = tk.StringVar(value=settings.get('copy_mode', 'OFF'))
        
        copy_mode_frame = ctk.CTkFrame(config_frame, fg_color='transparent')
        copy_mode_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ctk.CTkRadioButton(copy_mode_frame, text="Off", variable=self.tg_copy_mode_var, value='OFF',
                           fg_color=COLORS['back'], hover_color=COLORS['back_hover'],
                           text_color=COLORS['text_primary']).pack(side=tk.LEFT, padx=5)
        ctk.CTkRadioButton(copy_mode_frame, text="Master (invia)", variable=self.tg_copy_mode_var, value='MASTER',
                           fg_color=COLORS['back'], hover_color=COLORS['back_hover'],
                           text_color=COLORS['text_primary']).pack(side=tk.LEFT, padx=5)
        ctk.CTkRadioButton(copy_mode_frame, text="Follower (riceve)", variable=self.tg_copy_mode_var, value='FOLLOWER',
                           fg_color=COLORS['back'], hover_color=COLORS['back_hover'],
                           text_color=COLORS['text_primary']).pack(side=tk.LEFT, padx=5)
        
        # Follower option: Reply 100% Master
        follower_options_frame = ctk.CTkFrame(config_frame, fg_color='transparent')
        follower_options_frame.pack(fill=tk.X, padx=10, pady=(0, 5))
        self.tg_reply_master_var = tk.BooleanVar(value=settings.get('reply_100_master', False))
        self.reply_master_checkbox = ctk.CTkCheckBox(follower_options_frame, text="Reply 100% Master (usa stake % del Master)", 
                                                      variable=self.tg_reply_master_var,
                                                      fg_color=COLORS['back'], hover_color=COLORS['back_hover'],
                                                      text_color=COLORS['text_primary'])
        self.reply_master_checkbox.pack(side=tk.LEFT, padx=5)
        
        copy_chat_frame = ctk.CTkFrame(config_frame, fg_color='transparent')
        copy_chat_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        ctk.CTkLabel(copy_chat_frame, text="Chat ID Copy:", text_color=COLORS['text_secondary']).pack(side=tk.LEFT)
        self.tg_copy_chat_id_var = tk.StringVar(value=settings.get('copy_chat_id', ''))
        ctk.CTkEntry(copy_chat_frame, textvariable=self.tg_copy_chat_id_var, width=150,
                     fg_color=COLORS['bg_card'], border_color=COLORS['border']).pack(side=tk.LEFT, padx=5)
        ctk.CTkLabel(copy_chat_frame, text="(ID del canale/gruppo)", 
                     font=('Segoe UI', 8), text_color=COLORS['text_tertiary']).pack(side=tk.LEFT)
        
        ctk.CTkButton(config_frame, text="Salva Copy Trading", command=self._save_copy_trading_settings,
                      fg_color=COLORS['button_primary'], hover_color=COLORS['back_hover'],
                      corner_radius=6, width=150).pack(anchor=tk.W, padx=10, pady=10)
        
        ctk.CTkLabel(config_frame, text="Le impostazioni Telegram (API, telefono, stake) sono nella tab Impostazioni", 
                     font=('Segoe UI', 9), text_color=COLORS['text_tertiary']).pack(anchor=tk.W, padx=10, pady=(10, 5))
        
        auth_frame = ctk.CTkFrame(config_frame, fg_color='transparent')
        auth_frame.pack(fill=tk.X, padx=10, pady=(5, 0))
        ctk.CTkLabel(auth_frame, text="Codice:", text_color=COLORS['text_secondary']).pack(side=tk.LEFT)
        self.tg_code_var = tk.StringVar()
        ctk.CTkEntry(auth_frame, textvariable=self.tg_code_var, width=60,
                     fg_color=COLORS['bg_card'], border_color=COLORS['border']).pack(side=tk.LEFT, padx=2)
        ctk.CTkLabel(auth_frame, text="2FA:", text_color=COLORS['text_secondary']).pack(side=tk.LEFT, padx=(10, 0))
        self.tg_2fa_var = tk.StringVar()
        ctk.CTkEntry(auth_frame, textvariable=self.tg_2fa_var, width=60, show='*',
                     fg_color=COLORS['bg_card'], border_color=COLORS['border']).pack(side=tk.LEFT, padx=2)
        
        auth_btn_frame = ctk.CTkFrame(config_frame, fg_color='transparent')
        auth_btn_frame.pack(fill=tk.X, padx=10, pady=(5, 0))
        ctk.CTkButton(auth_btn_frame, text="Invia Codice", command=self._send_telegram_code,
                      fg_color=COLORS['button_secondary'], hover_color=COLORS['bg_hover'],
                      corner_radius=6, width=100).pack(side=tk.LEFT, padx=2)
        ctk.CTkButton(auth_btn_frame, text="Verifica", command=self._verify_telegram_code,
                      fg_color=COLORS['button_primary'], hover_color=COLORS['back_hover'],
                      corner_radius=6, width=80).pack(side=tk.LEFT, padx=2)
        ctk.CTkButton(auth_btn_frame, text="Reset", command=self._reset_telegram_session,
                      fg_color=COLORS['button_danger'], hover_color='#c62828',
                      corner_radius=6, width=70).pack(side=tk.LEFT, padx=2)
        
        self.tg_status_label = ctk.CTkLabel(config_frame, text=f"Stato: {self.telegram_status}",
                                            text_color=COLORS['text_secondary'])
        self.tg_status_label.pack(anchor=tk.W, padx=10, pady=5)
        
        btn_frame = ctk.CTkFrame(config_frame, fg_color='transparent')
        btn_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        ctk.CTkButton(btn_frame, text="Avvia Listener", command=self._start_telegram_listener,
                      fg_color=COLORS['button_success'], hover_color='#4caf50',
                      corner_radius=6, width=100).pack(side=tk.LEFT, padx=2)
        ctk.CTkButton(btn_frame, text="Ferma", command=self._stop_telegram_listener,
                      fg_color=COLORS['button_danger'], hover_color='#c62828',
                      corner_radius=6, width=60).pack(side=tk.LEFT, padx=2)
        
        chats_frame = ctk.CTkFrame(subtab_container, fg_color=COLORS['bg_panel'], corner_radius=8)
        self.tg_subtab_frames['chats'] = chats_frame
        
        ctk.CTkLabel(chats_frame, text="Chat Monitorate", font=FONTS['heading'],
                     text_color=COLORS['text_primary']).pack(anchor=tk.W, padx=10, pady=(10, 5))
        
        chat_btn_frame = ctk.CTkFrame(chats_frame, fg_color='transparent')
        chat_btn_frame.pack(fill=tk.X, padx=10, pady=(0, 5))
        ctk.CTkButton(chat_btn_frame, text="Rimuovi", command=self._remove_telegram_chat,
                      fg_color=COLORS['button_danger'], hover_color='#c62828',
                      corner_radius=6, width=80).pack(side=tk.LEFT, padx=2)
        
        columns = ('name', 'enabled')
        self.tg_chats_tree = ttk.Treeview(chats_frame, columns=columns, show='headings', height=4)
        self.tg_chats_tree.heading('name', text='Nome Chat')
        self.tg_chats_tree.heading('enabled', text='Attivo')
        self.tg_chats_tree.column('name', width=200)
        self.tg_chats_tree.column('enabled', width=50)
        self.tg_chats_tree.pack(fill=tk.X, padx=10, pady=(0, 10))
        
        self._refresh_telegram_chats_tree()
        
        available_frame = ctk.CTkFrame(subtab_container, fg_color=COLORS['bg_panel'], corner_radius=8)
        self.tg_subtab_frames['available'] = available_frame
        
        ctk.CTkLabel(available_frame, text="Chat Disponibili da Telegram", font=FONTS['heading'],
                     text_color=COLORS['text_primary']).pack(anchor=tk.W, padx=10, pady=(10, 5))
        
        avail_btn_frame = ctk.CTkFrame(available_frame, fg_color='transparent')
        avail_btn_frame.pack(fill=tk.X, padx=10, pady=(0, 5))
        ctk.CTkButton(avail_btn_frame, text="Carica/Aggiorna Chat", command=self._load_available_chats,
                      fg_color=COLORS['button_primary'], hover_color=COLORS['back_hover'],
                      corner_radius=6, width=140).pack(side=tk.LEFT, padx=2)
        ctk.CTkButton(avail_btn_frame, text="Aggiungi Selezionate", command=self._add_selected_available_chats,
                      fg_color=COLORS['button_success'], hover_color='#4caf50',
                      corner_radius=6, width=140).pack(side=tk.LEFT, padx=2)
        
        self.tg_available_status = ctk.CTkLabel(avail_btn_frame, text="", text_color=COLORS['text_secondary'])
        self.tg_available_status.pack(side=tk.RIGHT, padx=5)
        
        avail_columns = ('select', 'type', 'name', 'chat_id')
        avail_tree_container = ctk.CTkFrame(available_frame, fg_color='transparent')
        avail_tree_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        
        self.tg_available_tree = ttk.Treeview(avail_tree_container, columns=avail_columns, show='headings', height=8, selectmode='extended')
        self.tg_available_tree.heading('select', text='')
        self.tg_available_tree.heading('type', text='Tipo')
        self.tg_available_tree.heading('name', text='Nome')
        self.tg_available_tree.heading('chat_id', text='ID')
        self.tg_available_tree.column('select', width=30)
        self.tg_available_tree.column('type', width=60)
        self.tg_available_tree.column('name', width=180)
        self.tg_available_tree.column('chat_id', width=120)
        
        # Bind click on ID column to copy to clipboard
        self.tg_available_tree.bind('<ButtonRelease-1>', self._on_available_tree_click)
        
        avail_scroll = ttk.Scrollbar(avail_tree_container, orient=tk.VERTICAL, command=self.tg_available_tree.yview)
        self.tg_available_tree.configure(yscrollcommand=avail_scroll.set)
        self.tg_available_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        avail_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.available_chats_data = []
        
        rules_frame = ctk.CTkFrame(subtab_container, fg_color=COLORS['bg_panel'], corner_radius=8)
        self.tg_subtab_frames['rules'] = rules_frame
        
        ctk.CTkLabel(rules_frame, text="Regole di Parsing", font=FONTS['heading'],
                     text_color=COLORS['text_primary']).pack(anchor=tk.W, padx=10, pady=(10, 5))
        ctk.CTkLabel(rules_frame, text="Definisci pattern regex per riconoscere i segnali", 
                     font=('Segoe UI', 8), text_color=COLORS['text_tertiary']).pack(anchor=tk.W, padx=10)
        
        rules_btn_frame = ctk.CTkFrame(rules_frame, fg_color='transparent')
        rules_btn_frame.pack(fill=tk.X, padx=10, pady=(5, 5))
        ctk.CTkButton(rules_btn_frame, text="Aggiungi", command=self._add_signal_pattern,
                      fg_color=COLORS['button_success'], hover_color='#4caf50',
                      corner_radius=6, width=80).pack(side=tk.LEFT, padx=2)
        ctk.CTkButton(rules_btn_frame, text="Modifica", command=self._edit_signal_pattern,
                      fg_color=COLORS['button_primary'], hover_color=COLORS['back_hover'],
                      corner_radius=6, width=80).pack(side=tk.LEFT, padx=2)
        ctk.CTkButton(rules_btn_frame, text="Elimina", command=self._delete_signal_pattern,
                      fg_color=COLORS['button_danger'], hover_color='#c62828',
                      corner_radius=6, width=80).pack(side=tk.LEFT, padx=2)
        ctk.CTkButton(rules_btn_frame, text="Attiva/Disattiva", command=self._toggle_signal_pattern,
                      fg_color=COLORS['button_secondary'], hover_color=COLORS['bg_hover'],
                      corner_radius=6, width=110).pack(side=tk.LEFT, padx=2)
        
        rules_columns = ('enabled', 'name', 'market', 'pattern')
        rules_tree_container = ctk.CTkFrame(rules_frame, fg_color='transparent')
        rules_tree_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        
        self.rules_tree = ttk.Treeview(rules_tree_container, columns=rules_columns, show='headings', height=6)
        self.rules_tree.heading('enabled', text='ON')
        self.rules_tree.heading('name', text='Nome')
        self.rules_tree.heading('market', text='Mercato')
        self.rules_tree.heading('pattern', text='Pattern')
        self.rules_tree.column('enabled', width=30)
        self.rules_tree.column('name', width=120)
        self.rules_tree.column('market', width=100)
        self.rules_tree.column('pattern', width=150)
        
        rules_scroll = ttk.Scrollbar(rules_tree_container, orient=tk.VERTICAL, command=self.rules_tree.yview)
        self.rules_tree.configure(yscrollcommand=rules_scroll.set)
        self.rules_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        rules_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self._refresh_rules_tree()
        
        signals_frame = ctk.CTkFrame(right_frame, fg_color=COLORS['bg_panel'], corner_radius=8)
        signals_frame.pack(fill=tk.BOTH, expand=True)
        
        ctk.CTkLabel(signals_frame, text="Segnali Ricevuti", font=FONTS['heading'],
                     text_color=COLORS['text_primary']).pack(anchor=tk.W, padx=10, pady=(10, 5))
        
        signals_tree_container = ctk.CTkFrame(signals_frame, fg_color='transparent')
        signals_tree_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        
        columns = ('data', 'evento', 'over', 'status')
        self.tg_signals_tree = ttk.Treeview(signals_tree_container, columns=columns, show='headings', height=15)
        self.tg_signals_tree.heading('data', text='Data')
        self.tg_signals_tree.heading('evento', text='Selezione')
        self.tg_signals_tree.heading('over', text='Tipo')
        self.tg_signals_tree.heading('status', text='Stato')
        self.tg_signals_tree.column('data', width=100)
        self.tg_signals_tree.column('evento', width=150)
        self.tg_signals_tree.column('over', width=50)
        self.tg_signals_tree.column('status', width=80)
        
        self.tg_signals_tree.tag_configure('success', foreground=COLORS['success'])
        self.tg_signals_tree.tag_configure('failed', foreground=COLORS['loss'])
        
        scrollbar = ttk.Scrollbar(signals_tree_container, orient=tk.VERTICAL, command=self.tg_signals_tree.yview)
        self.tg_signals_tree.configure(yscrollcommand=scrollbar.set)
        self.tg_signals_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        ctk.CTkButton(signals_frame, text="Aggiorna Segnali", command=self._refresh_telegram_signals_tree,
                      fg_color=COLORS['button_primary'], hover_color=COLORS['back_hover'],
                      corner_radius=6).pack(pady=10)
        
        self._refresh_telegram_signals_tree()
        
        self._switch_telegram_subtab('config')
    
    def _switch_telegram_subtab(self, active_key):
        """Switch between Telegram sub-tabs."""
        for key, frame in self.tg_subtab_frames.items():
            if key == active_key:
                frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
            else:
                frame.pack_forget()
        
        for key, btn in self.tg_subtab_buttons.items():
            if key == active_key:
                btn.configure(fg_color=COLORS['button_primary'])
            else:
                btn.configure(fg_color=COLORS['button_secondary'])
    
    def _save_copy_trading_settings(self):
        """Save Copy Trading settings from Telegram tab."""
        settings = self.db.get_telegram_settings() or {}
        self.db.save_telegram_settings(
            api_id=settings.get('api_id', ''),
            api_hash=settings.get('api_hash', ''),
            session_string=settings.get('session_string'),
            phone_number=settings.get('phone_number', ''),
            enabled=settings.get('enabled', True),
            auto_bet=settings.get('auto_bet', False),
            require_confirmation=settings.get('require_confirmation', True),
            auto_stake=settings.get('auto_stake', 1.0),
            auto_start_listener=settings.get('auto_start_listener', False),
            auto_stop_listener=settings.get('auto_stop_listener', True),
            copy_mode=self.tg_copy_mode_var.get(),
            copy_chat_id=self.tg_copy_chat_id_var.get(),
            stake_type=settings.get('stake_type', 'fixed'),
            stake_percent=settings.get('stake_percent', 1.0),
            dutching_enabled=settings.get('dutching_enabled', False),
            reply_100_master=self.tg_reply_master_var.get()
        )
        messagebox.showinfo("Salvato", "Impostazioni Copy Trading salvate")
    
    def _load_available_chats(self):
        """Load available chats from Telegram account into the tree."""
        settings = self.db.get_telegram_settings()
        if not settings or not settings.get('api_id') or not settings.get('api_hash'):
            messagebox.showwarning("Attenzione", "Configura prima le credenziali Telegram")
            return
        
        self.tg_available_status.configure(text="Caricamento...")
        self.tg_available_tree.delete(*self.tg_available_tree.get_children())
        self.available_chats_data = []
        
        # If listener is running, use its existing client to avoid session conflicts
        if self.telegram_listener and self.telegram_listener.running:
            def on_dialogs_result(result):
                if result is None:
                    self.root.after(0, lambda: self.tg_available_status.configure(text="Errore caricamento"))
                else:
                    self.root.after(0, lambda: self._populate_available_chats(result))
            
            self.telegram_listener.get_available_dialogs(on_dialogs_result)
            return
        
        # Listener not running, create temporary client
        def fetch_dialogs():
            try:
                import asyncio
                import os
                from telethon import TelegramClient
                from telethon.tl.types import Channel, Chat, User
                
                async def do_fetch():
                    api_id = int(settings['api_id'])
                    api_hash = settings['api_hash'].strip()
                    session_path = os.path.join(os.environ.get('APPDATA', '.'), 'Pickfair', 'telegram_session')
                    
                    client = TelegramClient(session_path, api_id, api_hash)
                    await client.connect()
                    
                    if not await client.is_user_authorized():
                        await client.disconnect()
                        return None
                    
                    dialogs = await client.get_dialogs()
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
                    
                    await client.disconnect()
                    return chat_list
                
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(do_fetch())
                loop.close()
                
                if result is None:
                    self.root.after(0, lambda: self.tg_available_status.configure(text="Non autenticato"))
                    self.root.after(0, lambda: messagebox.showwarning("Attenzione", 
                        "Non autenticato. Clicca 'Invia Codice' e poi 'Verifica'."))
                else:
                    self.root.after(0, lambda: self._populate_available_chats(result))
                
            except Exception as e:
                err = str(e)
                logging.error(f"Error loading available chats: {e}")
                self.root.after(0, lambda: self.tg_available_status.configure(text=f"Errore: {err[:30]}"))
        
        threading.Thread(target=fetch_dialogs, daemon=True).start()
    
    def _populate_available_chats(self, chat_list):
        """Populate the available chats tree."""
        self.tg_available_tree.delete(*self.tg_available_tree.get_children())
        self.available_chats_data = chat_list
        
        monitored_ids = set()
        for chat in self.db.get_telegram_chats():
            monitored_ids.add(int(chat['chat_id']))
        
        for chat in chat_list:
            if chat['id'] in monitored_ids:
                continue
            self.tg_available_tree.insert('', tk.END, iid=str(chat['id']), values=(
                '',
                chat['type'],
                chat['name'],
                str(chat['id'])
            ))
        
        count = len(self.tg_available_tree.get_children())
        self.tg_available_status.configure(text=f"{count} chat disponibili")
    
    def _on_available_tree_click(self, event):
        """Handle click on available chats tree - copy ID to clipboard if clicked on ID column."""
        region = self.tg_available_tree.identify_region(event.x, event.y)
        if region != 'cell':
            return
        
        column = self.tg_available_tree.identify_column(event.x)
        # Column #4 is chat_id (columns are #1, #2, #3, #4)
        if column == '#4':
            item_id = self.tg_available_tree.identify_row(event.y)
            if item_id:
                item = self.tg_available_tree.item(item_id)
                values = item['values']
                if len(values) >= 4:
                    chat_id = str(values[3])
                    self.root.clipboard_clear()
                    self.root.clipboard_append(chat_id)
                    self.tg_available_status.configure(text=f"ID {chat_id} copiato!")
    
    def _add_selected_available_chats(self):
        """Add selected chats from available list to monitored."""
        selected = self.tg_available_tree.selection()
        if not selected:
            messagebox.showwarning("Attenzione", "Seleziona almeno una chat dalla lista")
            return
        
        count = 0
        errors = 0
        for item_id in selected:
            try:
                item = self.tg_available_tree.item(item_id)
                values = item['values']
                chat_id = int(item_id)
                chat_name = values[2] if len(values) > 2 else str(chat_id)
                
                self.db.add_telegram_chat(chat_id, chat_name)
                self.tg_available_tree.delete(item_id)
                count += 1
            except Exception as e:
                errors += 1
                logging.error(f"Error adding chat {item_id}: {e}")
        
        self._refresh_telegram_chats_tree()
        remaining = len(self.tg_available_tree.get_children())
        self.tg_available_status.configure(text=f"{remaining} chat disponibili")
        if errors > 0:
            messagebox.showwarning("Attenzione", f"Aggiunte {count} chat, {errors} errori")
        else:
            messagebox.showinfo("Aggiunto", f"Aggiunte {count} chat alla lista monitorata")
    
    def _add_telegram_chat(self):
        """Add a new telegram chat to monitor."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Aggiungi Chat")
        dialog.geometry("400x150")
        dialog.transient(self.root)
        dialog.grab_set()
        
        ttk.Label(dialog, text="Chat ID:").pack(anchor=tk.W, padx=20, pady=(20, 5))
        chat_id_var = tk.StringVar()
        ttk.Entry(dialog, textvariable=chat_id_var, width=40).pack(padx=20)
        
        ttk.Label(dialog, text="Nome Chat (opzionale):").pack(anchor=tk.W, padx=20, pady=(10, 5))
        chat_name_var = tk.StringVar()
        ttk.Entry(dialog, textvariable=chat_name_var, width=40).pack(padx=20)
        
        def save():
            chat_id = chat_id_var.get().strip()
            if not chat_id:
                messagebox.showwarning("Errore", "Inserisci un Chat ID")
                return
            try:
                chat_id_int = int(chat_id)
                chat_name = chat_name_var.get().strip() or f"Chat {chat_id}"
                self.db.add_telegram_chat(chat_id_int, chat_name)
                self._refresh_telegram_chats_tree()
                dialog.destroy()
                messagebox.showinfo("Successo", f"Chat '{chat_name}' aggiunta")
            except ValueError:
                messagebox.showwarning("Errore", "Chat ID deve essere un numero")
        
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=15)
        ttk.Button(btn_frame, text="Salva", command=save).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Annulla", command=dialog.destroy).pack(side=tk.LEFT, padx=5)
    
    def _remove_telegram_chat(self):
        """Remove selected telegram chat."""
        selected = self.tg_chats_tree.selection()
        if not selected:
            messagebox.showwarning("Attenzione", "Seleziona una chat da rimuovere")
            return
        
        item = self.tg_chats_tree.item(selected[0])
        chat_name = item['values'][0]
        
        if messagebox.askyesno("Conferma", f"Rimuovere la chat '{chat_name}'?"):
            chats = self.db.get_telegram_chats()
            for chat in chats:
                if chat.get('chat_name') == chat_name or str(chat['chat_id']) == chat_name:
                    self.db.remove_telegram_chat(chat['chat_id'])
                    break
            self._refresh_telegram_chats_tree()
    
    def _refresh_telegram_chats_tree(self):
        """Refresh chats tree in Telegram tab."""
        self.tg_chats_tree.delete(*self.tg_chats_tree.get_children())
        chats = self.db.get_telegram_chats()
        for chat in chats:
            self.tg_chats_tree.insert('', tk.END, values=(
                chat.get('chat_name', str(chat['chat_id'])),
                'Si' if chat.get('enabled') else 'No'
            ))
    
    def _refresh_telegram_signals_tree(self):
        """Refresh signals tree in Telegram tab."""
        self.tg_signals_tree.delete(*self.tg_signals_tree.get_children())
        signals = self.db.get_recent_signals(limit=50)
        for sig in signals:
            timestamp = sig.get('received_at', '')[:16]
            selection = sig.get('parsed_selection', '')[:25] if sig.get('parsed_selection') else 'N/A'
            side = sig.get('parsed_side', '')
            status = sig.get('status', 'PENDING')
            tag = 'success' if status in ('MATCHED', 'PLACED') else 'failed' if status == 'FAILED' else ''
            
            self.tg_signals_tree.insert('', tk.END, values=(
                timestamp,
                selection,
                side,
                status
            ), tags=(tag,) if tag else ())
    
    def _refresh_rules_tree(self):
        """Refresh signal patterns tree."""
        self.rules_tree.delete(*self.rules_tree.get_children())
        patterns = self.db.get_signal_patterns()
        for p in patterns:
            enabled_str = 'Si' if p.get('enabled') else 'No'
            pattern_display = p.get('pattern', '')[:40]
            self.rules_tree.insert('', tk.END, iid=str(p['id']), values=(
                enabled_str,
                p.get('name', ''),
                p.get('market_type', ''),
                pattern_display
            ))
    
    def _add_signal_pattern(self):
        """Add a new signal pattern via inline form in the tab."""
        self._show_pattern_editor(mode='add')
    
    def _edit_signal_pattern(self):
        """Edit the selected signal pattern."""
        selected = self.rules_tree.selection()
        if not selected:
            messagebox.showwarning("Attenzione", "Seleziona una regola da modificare")
            return
        pattern_id = int(selected[0])
        self._show_pattern_editor(mode='edit', pattern_id=pattern_id)
    
    def _delete_signal_pattern(self):
        """Delete the selected signal pattern."""
        selected = self.rules_tree.selection()
        if not selected:
            messagebox.showwarning("Attenzione", "Seleziona una regola da eliminare")
            return
        
        pattern_id = int(selected[0])
        item = self.rules_tree.item(selected[0])
        pattern_name = item['values'][1]
        
        if messagebox.askyesno("Conferma", f"Eliminare la regola '{pattern_name}'?"):
            self.db.delete_signal_pattern(pattern_id)
            self._refresh_rules_tree()
            self._reload_listener_patterns()
    
    def _toggle_signal_pattern(self):
        """Toggle enable/disable for selected pattern."""
        selected = self.rules_tree.selection()
        if not selected:
            messagebox.showwarning("Attenzione", "Seleziona una regola da attivare/disattivare")
            return
        
        pattern_id = int(selected[0])
        item = self.rules_tree.item(selected[0])
        current_enabled = item['values'][0] == 'Si'
        
        self.db.toggle_signal_pattern(pattern_id, not current_enabled)
        self._refresh_rules_tree()
        self._reload_listener_patterns()
    
    def _reload_listener_patterns(self):
        """Reload custom patterns in the Telegram listener if running."""
        if self.telegram_listener:
            try:
                self.telegram_listener.reload_custom_patterns()
            except Exception as e:
                print(f"[DEBUG] Error reloading listener patterns: {e}")
    
    def _show_pattern_editor(self, mode='add', pattern_id=None):
        """Show pattern editor in a sub-tab instead of popup."""
        if hasattr(self, 'pattern_editor_frame') and self.pattern_editor_frame.winfo_exists():
            self.pattern_editor_frame.destroy()
        
        existing_pattern = None
        if mode == 'edit' and pattern_id:
            patterns = self.db.get_signal_patterns()
            for p in patterns:
                if p['id'] == pattern_id:
                    existing_pattern = p
                    break
        
        self.pattern_editor_frame = ctk.CTkFrame(self.telegram_tab, fg_color=COLORS['bg_panel'], corner_radius=8)
        self.pattern_editor_frame.place(relx=0.5, rely=0.5, anchor=tk.CENTER, relwidth=0.7, relheight=0.7)
        
        header_frame = ctk.CTkFrame(self.pattern_editor_frame, fg_color='transparent')
        header_frame.pack(fill=tk.X, padx=15, pady=(15, 10))
        
        title_text = "Modifica Regola" if mode == 'edit' else "Nuova Regola di Parsing"
        ctk.CTkLabel(header_frame, text=title_text, font=FONTS['heading'],
                     text_color=COLORS['text_primary']).pack(side=tk.LEFT)
        
        ctk.CTkButton(header_frame, text="X", width=30, height=30,
                      fg_color=COLORS['button_secondary'], hover_color=COLORS['loss'],
                      corner_radius=6, command=lambda: self.pattern_editor_frame.destroy()).pack(side=tk.RIGHT)
        
        form_frame = ctk.CTkFrame(self.pattern_editor_frame, fg_color='transparent')
        form_frame.pack(fill=tk.X, padx=15, pady=5)
        
        ctk.CTkLabel(form_frame, text="Nome:", text_color=COLORS['text_secondary']).grid(row=0, column=0, sticky=tk.W, pady=3)
        name_var = tk.StringVar(value=existing_pattern.get('name', '') if existing_pattern else '')
        ctk.CTkEntry(form_frame, textvariable=name_var, width=300,
                     fg_color=COLORS['bg_card'], border_color=COLORS['border']).grid(row=0, column=1, pady=3, padx=5)
        
        ctk.CTkLabel(form_frame, text="Descrizione:", text_color=COLORS['text_secondary']).grid(row=1, column=0, sticky=tk.W, pady=3)
        desc_var = tk.StringVar(value=existing_pattern.get('description', '') if existing_pattern else '')
        ctk.CTkEntry(form_frame, textvariable=desc_var, width=300,
                     fg_color=COLORS['bg_card'], border_color=COLORS['border']).grid(row=1, column=1, pady=3, padx=5)
        
        ctk.CTkLabel(form_frame, text="Pattern Predefinito:", text_color=COLORS['text_secondary']).grid(row=2, column=0, sticky=tk.W, pady=3)
        
        preset_patterns = {
            "-- Seleziona --": ("", "OVER_UNDER_X5"),
            "Over 0.5": ("over.*(0.5)", "OVER_UNDER_X5"),
            "Over 1.5": ("over.*(1.5)", "OVER_UNDER_X5"),
            "Over 2.5": ("over.*(2.5)", "OVER_UNDER_X5"),
            "Over 3.5": ("over.*(3.5)", "OVER_UNDER_X5"),
            "Under 0.5": ("under.*(0.5)", "OVER_UNDER_X5"),
            "Under 1.5": ("under.*(1.5)", "OVER_UNDER_X5"),
            "Under 2.5": ("under.*(2.5)", "OVER_UNDER_X5"),
            "Under 3.5": ("under.*(3.5)", "OVER_UNDER_X5"),
            "GG / BTTS": ("(gg|btts|gol)", "BOTH_TEAMS_TO_SCORE"),
            "NG / No Goal": ("(ng|no.?goal|nogol)", "BOTH_TEAMS_TO_SCORE"),
            "1T Over 0.5": ("1t.*(over|0.5)", "OVER_UNDER_15_FH"),
            "1T Under 0.5": ("1t.*(under|0.5)", "OVER_UNDER_15_FH"),
            "1X (Casa o Pari)": ("(1x)", "DOUBLE_CHANCE"),
            "X2 (Pari o Trasferta)": ("(x2)", "DOUBLE_CHANCE"),
            "12 (Casa o Trasferta)": ("(12)", "DOUBLE_CHANCE"),
            "Casa Vince": ("(home|casa|1)", "MATCH_ODDS"),
            "Pareggio": ("(draw|pari|x)", "MATCH_ODDS"),
            "Trasferta Vince": ("(away|trasferta|2)", "MATCH_ODDS"),
            "Personalizzato...": ("", "OVER_UNDER_X5"),
        }
        
        preset_var = tk.StringVar(value="-- Seleziona --")
        pattern_var = tk.StringVar(value=existing_pattern.get('pattern', '') if existing_pattern else '')
        
        def on_preset_change(choice):
            if choice in preset_patterns and choice not in ["-- Seleziona --", "Personalizzato..."]:
                pattern, market = preset_patterns[choice]
                pattern_var.set(pattern)
                market_var.set(market)
                if not name_var.get():
                    name_var.set(choice)
        
        preset_menu = ctk.CTkOptionMenu(form_frame, variable=preset_var, values=list(preset_patterns.keys()),
                                        fg_color=COLORS['bg_card'], button_color=COLORS['success'],
                                        button_hover_color='#4caf50', width=200, command=on_preset_change)
        preset_menu.grid(row=2, column=1, pady=3, padx=5, sticky=tk.W)
        
        options_frame = ctk.CTkFrame(form_frame, fg_color='transparent')
        options_frame.grid(row=2, column=2, pady=3, padx=5, sticky=tk.W)
        
        lay_var = tk.BooleanVar(value=existing_pattern.get('bet_side', 'BACK') == 'LAY' if existing_pattern else False)
        ctk.CTkCheckBox(options_frame, text="LAY", variable=lay_var,
                        fg_color=COLORS['lay'], hover_color=COLORS['lay_hover'],
                        text_color=COLORS['text_primary'], width=60).pack(side=tk.LEFT, padx=(0, 10))
        
        live_var = tk.BooleanVar(value=bool(existing_pattern.get('live_only', 0)) if existing_pattern else False)
        ctk.CTkCheckBox(options_frame, text="LIVE", variable=live_var,
                        fg_color=COLORS['success'], hover_color='#4caf50',
                        text_color=COLORS['text_primary'], width=60).pack(side=tk.LEFT)
        
        ctk.CTkLabel(form_frame, text="Pattern Regex:", text_color=COLORS['text_secondary']).grid(row=3, column=0, sticky=tk.W, pady=3)
        ctk.CTkEntry(form_frame, textvariable=pattern_var, width=300,
                     fg_color=COLORS['bg_card'], border_color=COLORS['border']).grid(row=3, column=1, pady=3, padx=5)
        
        ctk.CTkLabel(form_frame, text="Tipo Mercato:", text_color=COLORS['text_secondary']).grid(row=4, column=0, sticky=tk.W, pady=3)
        market_types = ['OVER_UNDER_X5', 'BOTH_TEAMS_TO_SCORE', 'OVER_UNDER_15_FH', 'DOUBLE_CHANCE',
                        'MATCH_ODDS', 'CORRECT_SCORE', 'ASIAN_HANDICAP', 'DRAW_NO_BET', 'HALF_TIME_FULL_TIME']
        market_var = tk.StringVar(value=existing_pattern.get('market_type', market_types[0]) if existing_pattern else market_types[0])
        market_menu = ctk.CTkOptionMenu(form_frame, variable=market_var, values=market_types,
                                        fg_color=COLORS['bg_card'], button_color=COLORS['button_primary'],
                                        button_hover_color=COLORS['back_hover'], width=200)
        market_menu.grid(row=4, column=1, pady=3, padx=5, sticky=tk.W)
        
        enabled_var = tk.BooleanVar(value=existing_pattern.get('enabled', True) if existing_pattern else True)
        ctk.CTkCheckBox(form_frame, text="Regola Attiva", variable=enabled_var,
                        fg_color=COLORS['back'], hover_color=COLORS['back_hover'],
                        text_color=COLORS['text_primary']).grid(row=5, column=1, pady=10, sticky=tk.W)
        
        help_frame = ctk.CTkFrame(self.pattern_editor_frame, fg_color=COLORS['bg_card'], corner_radius=6)
        help_frame.pack(fill=tk.X, padx=15, pady=10)
        
        help_text = """Seleziona un pattern predefinito dal menu sopra, oppure scegli "Personalizzato" 
e scrivi il tuo pattern nel campo Pattern Regex."""
        ctk.CTkLabel(help_frame, text=help_text, font=('Segoe UI', 10),
                     text_color=COLORS['text_secondary'], justify=tk.LEFT).pack(anchor=tk.W, padx=10, pady=10)
        
        btn_frame = ctk.CTkFrame(self.pattern_editor_frame, fg_color='transparent')
        btn_frame.pack(fill=tk.X, padx=15, pady=15)
        
        def save_pattern():
            name = name_var.get().strip()
            pattern = pattern_var.get().strip()
            market = market_var.get()
            desc = desc_var.get().strip()
            enabled = enabled_var.get()
            bet_side = 'LAY' if lay_var.get() else 'BACK'
            live_only = live_var.get()
            
            if not name:
                messagebox.showwarning("Errore", "Inserisci un nome per la regola")
                return
            if not pattern:
                messagebox.showwarning("Errore", "Inserisci il pattern regex")
                return
            
            import re
            try:
                re.compile(pattern)
            except re.error as e:
                messagebox.showerror("Errore Regex", f"Pattern non valido: {e}")
                return
            
            if mode == 'edit' and pattern_id:
                self.db.update_signal_pattern(pattern_id, name=name, description=desc,
                                               pattern=pattern, market_type=market, enabled=enabled,
                                               bet_side=bet_side, live_only=live_only)
            else:
                self.db.save_signal_pattern(name, desc, pattern, market, enabled, bet_side=bet_side, live_only=live_only)
            
            self.pattern_editor_frame.destroy()
            self._refresh_rules_tree()
            self._reload_listener_patterns()
            messagebox.showinfo("Salvato", f"Regola '{name}' salvata con successo!")
        
        def cancel_edit():
            self.pattern_editor_frame.destroy()
        
        ctk.CTkButton(btn_frame, text="Salva", command=save_pattern,
                      fg_color=COLORS['button_success'], hover_color='#4caf50',
                      corner_radius=6, width=100).pack(side=tk.LEFT, padx=5)
        ctk.CTkButton(btn_frame, text="Annulla", command=cancel_edit,
                      fg_color=COLORS['button_secondary'], hover_color=COLORS['bg_hover'],
                      corner_radius=6, width=100).pack(side=tk.LEFT, padx=5)
    
    def _create_strumenti_tab(self):
        """Create Strumenti tab content."""
        main_frame = ctk.CTkFrame(self.strumenti_tab, fg_color='transparent')
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        ctk.CTkLabel(main_frame, text="Strumenti", font=FONTS['title'],
                     text_color=COLORS['text_primary']).pack(anchor=tk.W, pady=(0, 20))
        
        tools_frame = ctk.CTkFrame(main_frame, fg_color=COLORS['bg_panel'], corner_radius=8)
        tools_frame.pack(fill=tk.X, pady=10)
        
        ctk.CTkLabel(tools_frame, text="Strumenti di Trading", font=FONTS['heading'],
                     text_color=COLORS['text_primary']).pack(anchor=tk.W, padx=15, pady=(15, 10))
        
        btn_frame1 = ctk.CTkFrame(tools_frame, fg_color='transparent')
        btn_frame1.pack(fill=tk.X, padx=15, pady=5)
        ctk.CTkButton(btn_frame1, text="Multi-Market Monitor", command=self._show_multi_market_monitor,
                      fg_color=COLORS['button_primary'], hover_color=COLORS['back_hover'],
                      corner_radius=6, width=180).pack(side=tk.LEFT, padx=5)
        ctk.CTkLabel(btn_frame1, text="Monitora piu mercati contemporaneamente", 
                     font=('Segoe UI', 9), text_color=COLORS['text_secondary']).pack(side=tk.LEFT, padx=10)
        
        btn_frame2 = ctk.CTkFrame(tools_frame, fg_color='transparent')
        btn_frame2.pack(fill=tk.X, padx=15, pady=(5, 15))
        ctk.CTkButton(btn_frame2, text="Filtri Avanzati", command=self._show_advanced_filters,
                      fg_color=COLORS['button_primary'], hover_color=COLORS['back_hover'],
                      corner_radius=6, width=180).pack(side=tk.LEFT, padx=5)
        ctk.CTkLabel(btn_frame2, text="Configura filtri per eventi e mercati", 
                     font=('Segoe UI', 9), text_color=COLORS['text_secondary']).pack(side=tk.LEFT, padx=10)
    
    def _create_plugin_tab(self):
        """Create Plugin tab content with install/uninstall/enable/disable functionality."""
        main_frame = ctk.CTkFrame(self.plugin_tab, fg_color='transparent')
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        # Header
        header_frame = ctk.CTkFrame(main_frame, fg_color='transparent')
        header_frame.pack(fill=tk.X, pady=(0, 20))
        
        ctk.CTkLabel(header_frame, text="Gestione Plugin", font=FONTS['title'],
                     text_color=COLORS['text_primary']).pack(side=tk.LEFT)
        
        # Install button
        ctk.CTkButton(header_frame, text="Installa Plugin", command=self._install_plugin,
                      fg_color=COLORS['success'], hover_color='#0ea271',
                      corner_radius=6, width=140).pack(side=tk.RIGHT, padx=5)
        
        # Reload all button
        ctk.CTkButton(header_frame, text="Ricarica Tutti", command=self._reload_all_plugins,
                      fg_color=COLORS['button_primary'], hover_color=COLORS['back_hover'],
                      corner_radius=6, width=120).pack(side=tk.RIGHT, padx=5)
        
        # Plugin list frame
        list_frame = ctk.CTkFrame(main_frame, fg_color=COLORS['bg_panel'], corner_radius=8)
        list_frame.pack(fill=tk.BOTH, expand=True)
        
        ctk.CTkLabel(list_frame, text="Plugin Installati", font=FONTS['heading'],
                     text_color=COLORS['text_primary']).pack(anchor=tk.W, padx=15, pady=(15, 10))
        
        # Treeview for plugins
        tree_frame = ctk.CTkFrame(list_frame, fg_color='transparent')
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        
        columns = ('name', 'version', 'author', 'status', 'description')
        self.plugins_tree = ttk.Treeview(tree_frame, columns=columns, show='headings', height=10)
        
        self.plugins_tree.heading('name', text='Nome')
        self.plugins_tree.heading('version', text='Versione')
        self.plugins_tree.heading('author', text='Autore')
        self.plugins_tree.heading('status', text='Stato')
        self.plugins_tree.heading('description', text='Descrizione')
        
        self.plugins_tree.column('name', width=150)
        self.plugins_tree.column('version', width=70)
        self.plugins_tree.column('author', width=120)
        self.plugins_tree.column('status', width=100)
        self.plugins_tree.column('description', width=300)
        
        # Tags for status colors
        self.plugins_tree.tag_configure('enabled', foreground=COLORS['success'])
        self.plugins_tree.tag_configure('disabled', foreground=COLORS['text_secondary'])
        self.plugins_tree.tag_configure('error', foreground=COLORS['error'])
        
        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.plugins_tree.yview)
        self.plugins_tree.configure(yscrollcommand=scrollbar.set)
        
        self.plugins_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Action buttons frame
        action_frame = ctk.CTkFrame(list_frame, fg_color='transparent')
        action_frame.pack(fill=tk.X, padx=15, pady=(5, 15))
        
        ctk.CTkButton(action_frame, text="Abilita", command=self._enable_selected_plugin,
                      fg_color=COLORS['success'], hover_color='#0ea271',
                      corner_radius=6, width=100).pack(side=tk.LEFT, padx=5)
        
        ctk.CTkButton(action_frame, text="Disabilita", command=self._disable_selected_plugin,
                      fg_color=COLORS['warning'], hover_color='#d97706',
                      corner_radius=6, width=100).pack(side=tk.LEFT, padx=5)
        
        ctk.CTkButton(action_frame, text="Disinstalla", command=self._uninstall_selected_plugin,
                      fg_color=COLORS['loss'], hover_color='#dc2626',
                      corner_radius=6, width=100).pack(side=tk.LEFT, padx=5)
        
        ctk.CTkButton(action_frame, text="Dettagli", command=self._show_plugin_details,
                      fg_color=COLORS['button_secondary'], hover_color=COLORS['bg_hover'],
                      corner_radius=6, width=100).pack(side=tk.LEFT, padx=5)
        
        # Security info frame
        security_frame = ctk.CTkFrame(main_frame, fg_color=COLORS['bg_panel'], corner_radius=8)
        security_frame.pack(fill=tk.X, pady=(10, 0))
        
        ctk.CTkLabel(security_frame, text="Sicurezza Plugin", font=FONTS['heading'],
                     text_color=COLORS['text_primary']).pack(anchor=tk.W, padx=15, pady=(15, 5))
        
        security_info = """I plugin vengono eseguiti con le seguenti protezioni:
- Timeout: Max 10 secondi per operazione
- Thread separato: L'app rimane fluida anche se il plugin si blocca
- Sandbox: Accesso file limitato alle cartelle plugins e data
- Validazione: Funzioni pericolose bloccate (eval, exec, os.system, ecc.)
- Librerie: Solo librerie pre-approvate o installate via requirements.txt"""
        
        ctk.CTkLabel(security_frame, text=security_info, font=('Segoe UI', 10),
                     text_color=COLORS['text_secondary'], justify=tk.LEFT).pack(anchor=tk.W, padx=15, pady=(0, 15))
        
        # Plugin folder path
        folder_frame = ctk.CTkFrame(security_frame, fg_color='transparent')
        folder_frame.pack(fill=tk.X, padx=15, pady=(0, 15))
        
        ctk.CTkLabel(folder_frame, text=f"Cartella plugin: {self.plugin_manager.plugins_dir}", 
                     font=('Segoe UI', 9), text_color=COLORS['text_secondary']).pack(side=tk.LEFT)
        
        ctk.CTkButton(folder_frame, text="Apri Cartella", command=self._open_plugins_folder,
                      fg_color=COLORS['button_secondary'], hover_color=COLORS['bg_hover'],
                      corner_radius=6, width=100).pack(side=tk.RIGHT)
        
        # Load existing plugins
        self._load_plugins_on_startup()
        self._refresh_plugins_tree()
    
    def _load_plugins_on_startup(self):
        """Load all plugins from plugins folder on startup."""
        try:
            self.plugin_manager.load_all_plugins()
        except Exception as e:
            print(f"[Plugin] Errore caricamento plugin: {e}")
    
    def _refresh_plugins_tree(self):
        """Refresh the plugins treeview."""
        self.plugins_tree.delete(*self.plugins_tree.get_children())
        
        for plugin in self.plugin_manager.get_plugin_list():
            if plugin.error:
                status = 'Errore'
                tag = 'error'
            elif plugin.enabled:
                status = 'Abilitato'
                tag = 'enabled'
            else:
                status = 'Disabilitato'
                tag = 'disabled'
            
            self.plugins_tree.insert('', tk.END, values=(
                plugin.name,
                plugin.version,
                plugin.author,
                status,
                plugin.description[:50] + '...' if len(plugin.description) > 50 else plugin.description
            ), tags=(tag,))
    
    def _install_plugin(self):
        """Install a plugin from file."""
        filepath = filedialog.askopenfilename(
            title="Seleziona Plugin",
            filetypes=[("Python files", "*.py")],
            initialdir=str(self.plugin_manager.plugins_dir)
        )
        
        if not filepath:
            return
        
        success, msg = self.plugin_manager.install_plugin_from_file(filepath)
        if success:
            messagebox.showinfo("Plugin Installato", msg)
            self._refresh_plugins_tree()
        else:
            messagebox.showerror("Errore Installazione", msg)
    
    def _reload_all_plugins(self):
        """Reload all plugins."""
        # Unload all first
        for name in list(self.plugin_manager.plugins.keys()):
            self.plugin_manager.unload_plugin(name)
        
        # Reload
        self.plugin_manager.load_all_plugins()
        self._refresh_plugins_tree()
        messagebox.showinfo("Plugin", "Plugin ricaricati")
    
    def _get_selected_plugin_name(self):
        """Get the name of the selected plugin."""
        selection = self.plugins_tree.selection()
        if not selection:
            messagebox.showwarning("Seleziona Plugin", "Seleziona un plugin dalla lista")
            return None
        
        item = self.plugins_tree.item(selection[0])
        return item['values'][0]
    
    def _enable_selected_plugin(self):
        """Enable the selected plugin."""
        name = self._get_selected_plugin_name()
        if not name:
            return
        
        success, msg = self.plugin_manager.enable_plugin(name)
        if success:
            self._refresh_plugins_tree()
        else:
            messagebox.showerror("Errore", msg)
    
    def _disable_selected_plugin(self):
        """Disable the selected plugin."""
        name = self._get_selected_plugin_name()
        if not name:
            return
        
        success, msg = self.plugin_manager.disable_plugin(name)
        if success:
            self._refresh_plugins_tree()
        else:
            messagebox.showerror("Errore", msg)
    
    def _uninstall_selected_plugin(self):
        """Uninstall the selected plugin."""
        name = self._get_selected_plugin_name()
        if not name:
            return
        
        if not messagebox.askyesno("Conferma", f"Disinstallare il plugin '{name}'?"):
            return
        
        success, msg = self.plugin_manager.uninstall_plugin(name)
        if success:
            self._refresh_plugins_tree()
            messagebox.showinfo("Plugin Rimosso", msg)
        else:
            messagebox.showerror("Errore", msg)
    
    def _show_plugin_details(self):
        """Show details of the selected plugin."""
        name = self._get_selected_plugin_name()
        if not name:
            return
        
        if name not in self.plugin_manager.plugins:
            return
        
        plugin = self.plugin_manager.plugins[name]
        
        details = f"""Nome: {plugin.name}
Versione: {plugin.version}
Autore: {plugin.author}
Abilitato: {'Si' if plugin.enabled else 'No'}
Verificato: {'Si' if plugin.verified else 'No'}

Descrizione:
{plugin.description}

File: {plugin.path}
Tempo caricamento: {plugin.load_time:.2f}s
Esecuzioni: {plugin.execution_count}

Ultimo errore: {plugin.last_error or 'Nessuno'}"""
        
        messagebox.showinfo(f"Dettagli Plugin: {name}", details)
    
    def _open_plugins_folder(self):
        """Open the plugins folder in file explorer."""
        import subprocess
        try:
            subprocess.Popen(['explorer', str(self.plugin_manager.plugins_dir)])
        except:
            messagebox.showinfo("Cartella Plugin", str(self.plugin_manager.plugins_dir))
    
    def add_plugin_tab(self, title: str, create_func, plugin_name: str):
        """Add a tab created by a plugin."""
        # Not implemented - plugins can't add main tabs for now
        pass
    
    def remove_plugin_tab(self, title: str, plugin_name: str):
        """Remove a tab created by a plugin."""
        pass
    
    def add_event_filter(self, name: str, filter_func, plugin_name: str):
        """Add a custom event filter from a plugin."""
        # Can be implemented to allow plugins to filter events
        pass
    
    def _create_impostazioni_tab(self):
        """Create Impostazioni tab content with scrollbar."""
        canvas = tk.Canvas(self.impostazioni_tab, bg=COLORS['bg_dark'], highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.impostazioni_tab, orient="vertical", command=canvas.yview)
        scrollable_frame = ctk.CTkFrame(canvas, fg_color='transparent')
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        
        main_frame = scrollable_frame
        
        ctk.CTkLabel(main_frame, text="Impostazioni", font=FONTS['title'],
                     text_color=COLORS['text_primary']).pack(anchor=tk.W, padx=20, pady=(20, 20))
        
        cred_frame = ctk.CTkFrame(main_frame, fg_color=COLORS['bg_panel'], corner_radius=8)
        cred_frame.pack(fill=tk.X, padx=20, pady=10)
        
        ctk.CTkLabel(cred_frame, text="Credenziali Betfair", font=FONTS['heading'],
                     text_color=COLORS['text_primary']).grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=15, pady=(15, 10))
        
        settings = self.db.get_settings() or {}
        
        ctk.CTkLabel(cred_frame, text="Username:", text_color=COLORS['text_secondary']).grid(row=1, column=0, sticky=tk.W, padx=15, pady=2)
        self.settings_username_var = tk.StringVar(value=settings.get('username', ''))
        ctk.CTkEntry(cred_frame, textvariable=self.settings_username_var, width=250,
                     fg_color=COLORS['bg_card'], border_color=COLORS['border']).grid(row=1, column=1, pady=2, padx=5)
        
        ctk.CTkLabel(cred_frame, text="App Key:", text_color=COLORS['text_secondary']).grid(row=2, column=0, sticky=tk.W, padx=15, pady=2)
        self.settings_appkey_var = tk.StringVar(value=settings.get('app_key', ''))
        ctk.CTkEntry(cred_frame, textvariable=self.settings_appkey_var, width=320,
                     fg_color=COLORS['bg_card'], border_color=COLORS['border']).grid(row=2, column=1, pady=2, padx=5)
        
        ctk.CTkLabel(cred_frame, text="Certificato (.crt):", text_color=COLORS['text_secondary']).grid(row=3, column=0, sticky=tk.W, padx=15, pady=2)
        self.settings_cert_var = tk.StringVar(value=settings.get('certificate', ''))
        cert_frame = ctk.CTkFrame(cred_frame, fg_color='transparent')
        cert_frame.grid(row=3, column=1, pady=2, padx=5, sticky=tk.W)
        ctk.CTkEntry(cert_frame, textvariable=self.settings_cert_var, width=280,
                     fg_color=COLORS['bg_card'], border_color=COLORS['border']).pack(side=tk.LEFT)
        ctk.CTkButton(cert_frame, text="...", width=30, command=lambda: self._browse_file(self.settings_cert_var, [("Certificati", "*.crt *.pem")]),
                      fg_color=COLORS['button_secondary'], hover_color=COLORS['bg_hover'], corner_radius=6).pack(side=tk.LEFT, padx=2)
        
        ctk.CTkLabel(cred_frame, text="Chiave Privata (.key):", text_color=COLORS['text_secondary']).grid(row=4, column=0, sticky=tk.W, padx=15, pady=2)
        self.settings_key_var = tk.StringVar(value=settings.get('private_key', ''))
        key_frame = ctk.CTkFrame(cred_frame, fg_color='transparent')
        key_frame.grid(row=4, column=1, pady=2, padx=5, sticky=tk.W)
        ctk.CTkEntry(key_frame, textvariable=self.settings_key_var, width=280,
                     fg_color=COLORS['bg_card'], border_color=COLORS['border']).pack(side=tk.LEFT)
        ctk.CTkButton(key_frame, text="...", width=30, command=lambda: self._browse_file(self.settings_key_var, [("Chiavi", "*.key *.pem")]),
                      fg_color=COLORS['button_secondary'], hover_color=COLORS['bg_hover'], corner_radius=6).pack(side=tk.LEFT, padx=2)
        
        ctk.CTkButton(cred_frame, text="Salva Credenziali", command=self._save_settings_from_tab,
                      fg_color=COLORS['button_primary'], hover_color=COLORS['back_hover'],
                      corner_radius=6, width=150).grid(row=5, column=1, pady=(10, 15), sticky=tk.W, padx=5)
        
        update_frame = ctk.CTkFrame(main_frame, fg_color=COLORS['bg_panel'], corner_radius=8)
        update_frame.pack(fill=tk.X, padx=20, pady=10)
        
        ctk.CTkLabel(update_frame, text="Aggiornamenti", font=FONTS['heading'],
                     text_color=COLORS['text_primary']).pack(anchor=tk.W, padx=15, pady=(15, 10))
        
        self.auto_update_var = tk.BooleanVar(value=self.db.get_auto_update_enabled())
        ctk.CTkCheckBox(update_frame, text="Controlla automaticamente aggiornamenti all'avvio", 
                        variable=self.auto_update_var, command=self._save_auto_update_setting,
                        fg_color=COLORS['back'], hover_color=COLORS['back_hover'],
                        text_color=COLORS['text_primary']).pack(anchor=tk.W, padx=15)
        
        btn_update_frame = ctk.CTkFrame(update_frame, fg_color='transparent')
        btn_update_frame.pack(fill=tk.X, padx=15, pady=(10, 15))
        ctk.CTkButton(btn_update_frame, text="Verifica Aggiornamenti", command=self._check_for_updates_manual,
                      fg_color=COLORS['button_primary'], hover_color=COLORS['back_hover'],
                      corner_radius=6, width=180).pack(side=tk.LEFT, padx=5)
        ctk.CTkLabel(btn_update_frame, text=f"Versione attuale: {APP_VERSION}", 
                     font=('Segoe UI', 9), text_color=COLORS['text_secondary']).pack(side=tk.LEFT, padx=10)
        
        app_frame = ctk.CTkFrame(main_frame, fg_color=COLORS['bg_panel'], corner_radius=8)
        app_frame.pack(fill=tk.X, padx=20, pady=10)
        
        ctk.CTkLabel(app_frame, text="Applicazione", font=FONTS['heading'],
                     text_color=COLORS['text_primary']).pack(anchor=tk.W, padx=15, pady=(15, 10))
        
        ctk.CTkButton(app_frame, text="Esci dall'Applicazione", command=self._on_close,
                      fg_color=COLORS['button_danger'], hover_color='#c62828',
                      corner_radius=6, width=180).pack(anchor=tk.W, padx=15, pady=(0, 15))
        
        # Telegram Settings Section
        tg_frame = ctk.CTkFrame(main_frame, fg_color=COLORS['bg_panel'], corner_radius=8)
        tg_frame.pack(fill=tk.X, padx=20, pady=10)
        
        ctk.CTkLabel(tg_frame, text="Telegram", font=FONTS['heading'],
                     text_color=COLORS['text_primary']).pack(anchor=tk.W, padx=15, pady=(15, 5))
        ctk.CTkLabel(tg_frame, text="Ottieni API ID e Hash su my.telegram.org", 
                     font=('Segoe UI', 8), text_color=COLORS['text_tertiary']).pack(anchor=tk.W, padx=15)
        
        tg_settings = self.db.get_telegram_settings() or {}
        
        tg_grid = ctk.CTkFrame(tg_frame, fg_color='transparent')
        tg_grid.pack(fill=tk.X, padx=15, pady=5)
        
        ctk.CTkLabel(tg_grid, text="API ID:", text_color=COLORS['text_secondary']).grid(row=0, column=0, sticky=tk.W, pady=2)
        self.settings_tg_api_id_var = tk.StringVar(value=tg_settings.get('api_id', ''))
        ctk.CTkEntry(tg_grid, textvariable=self.settings_tg_api_id_var, width=200,
                     fg_color=COLORS['bg_card'], border_color=COLORS['border']).grid(row=0, column=1, pady=2, padx=5)
        
        ctk.CTkLabel(tg_grid, text="API Hash:", text_color=COLORS['text_secondary']).grid(row=1, column=0, sticky=tk.W, pady=2)
        self.settings_tg_api_hash_var = tk.StringVar(value=tg_settings.get('api_hash', ''))
        ctk.CTkEntry(tg_grid, textvariable=self.settings_tg_api_hash_var, width=280,
                     fg_color=COLORS['bg_card'], border_color=COLORS['border']).grid(row=1, column=1, pady=2, padx=5)
        
        ctk.CTkLabel(tg_grid, text="Telefono (+39...):", text_color=COLORS['text_secondary']).grid(row=2, column=0, sticky=tk.W, pady=2)
        self.settings_tg_phone_var = tk.StringVar(value=tg_settings.get('phone_number', ''))
        ctk.CTkEntry(tg_grid, textvariable=self.settings_tg_phone_var, width=150,
                     fg_color=COLORS['bg_card'], border_color=COLORS['border']).grid(row=2, column=1, pady=2, padx=5, sticky=tk.W)
        
        # Stake Type Section
        stake_frame = ctk.CTkFrame(tg_frame, fg_color='transparent')
        stake_frame.pack(fill=tk.X, padx=15, pady=5)
        
        ctk.CTkLabel(stake_frame, text="Tipo Stake:", font=('Segoe UI', 10, 'bold'),
                     text_color=COLORS['text_primary']).pack(anchor=tk.W)
        
        self.settings_tg_stake_type_var = tk.StringVar(value=tg_settings.get('stake_type', 'fixed'))
        
        stake_options = ctk.CTkFrame(stake_frame, fg_color='transparent')
        stake_options.pack(fill=tk.X, pady=2)
        
        # Stake Fisso
        fixed_frame = ctk.CTkFrame(stake_options, fg_color='transparent')
        fixed_frame.pack(anchor=tk.W, pady=2)
        ctk.CTkRadioButton(fixed_frame, text="Stake Fisso (EUR):", variable=self.settings_tg_stake_type_var, value='fixed',
                          fg_color=COLORS['back'], hover_color=COLORS['back_hover'],
                          text_color=COLORS['text_primary']).pack(side=tk.LEFT)
        self.settings_tg_stake_var = tk.StringVar(value=str(tg_settings.get('auto_stake', '1.0')))
        ctk.CTkEntry(fixed_frame, textvariable=self.settings_tg_stake_var, width=60,
                     fg_color=COLORS['bg_card'], border_color=COLORS['border']).pack(side=tk.LEFT, padx=5)
        
        # Percentuale Bankroll
        percent_br_frame = ctk.CTkFrame(stake_options, fg_color='transparent')
        percent_br_frame.pack(anchor=tk.W, pady=2)
        ctk.CTkRadioButton(percent_br_frame, text="% Bankroll:", variable=self.settings_tg_stake_type_var, value='percent_bankroll',
                          fg_color=COLORS['back'], hover_color=COLORS['back_hover'],
                          text_color=COLORS['text_primary']).pack(side=tk.LEFT)
        self.settings_tg_percent_var = tk.StringVar(value=str(tg_settings.get('stake_percent', '1.0')))
        ctk.CTkEntry(percent_br_frame, textvariable=self.settings_tg_percent_var, width=50,
                     fg_color=COLORS['bg_card'], border_color=COLORS['border']).pack(side=tk.LEFT, padx=5)
        ctk.CTkLabel(percent_br_frame, text="% (es. 3 = 3% del saldo)", 
                     text_color=COLORS['text_tertiary']).pack(side=tk.LEFT, padx=5)
        
        tg_checks = ctk.CTkFrame(tg_frame, fg_color='transparent')
        tg_checks.pack(fill=tk.X, padx=15, pady=5)
        
        self.settings_tg_auto_bet_var = tk.BooleanVar(value=bool(tg_settings.get('auto_bet', 0)))
        ctk.CTkCheckBox(tg_checks, text="Piazza automaticamente", variable=self.settings_tg_auto_bet_var,
                        fg_color=COLORS['back'], hover_color=COLORS['back_hover'],
                        text_color=COLORS['text_primary']).pack(anchor=tk.W)
        
        self.settings_tg_confirm_var = tk.BooleanVar(value=bool(tg_settings.get('require_confirmation', 1)))
        ctk.CTkCheckBox(tg_checks, text="Richiedi conferma (solo se auto OFF)", variable=self.settings_tg_confirm_var,
                        fg_color=COLORS['back'], hover_color=COLORS['back_hover'],
                        text_color=COLORS['text_primary']).pack(anchor=tk.W)
        
        self.settings_tg_auto_start_var = tk.BooleanVar(value=bool(tg_settings.get('auto_start_listener', 0)))
        ctk.CTkCheckBox(tg_checks, text="Avvia listener all'avvio", variable=self.settings_tg_auto_start_var,
                        fg_color=COLORS['back'], hover_color=COLORS['back_hover'],
                        text_color=COLORS['text_primary']).pack(anchor=tk.W)
        
        self.settings_tg_auto_stop_var = tk.BooleanVar(value=bool(tg_settings.get('auto_stop_listener', 1)))
        ctk.CTkCheckBox(tg_checks, text="Ferma listener alla chiusura", variable=self.settings_tg_auto_stop_var,
                        fg_color=COLORS['back'], hover_color=COLORS['back_hover'],
                        text_color=COLORS['text_primary']).pack(anchor=tk.W)
        
        ctk.CTkButton(tg_frame, text="Salva Impostazioni Telegram", command=self._save_telegram_settings_from_impostazioni,
                      fg_color=COLORS['button_primary'], hover_color=COLORS['back_hover'],
                      corner_radius=6, width=200).pack(anchor=tk.W, padx=15, pady=(10, 15))
    
    def _save_telegram_settings_from_impostazioni(self):
        """Save Telegram settings from Impostazioni tab."""
        try:
            stake = float(self.settings_tg_stake_var.get().replace(',', '.'))
        except:
            stake = 1.0
        
        try:
            stake_percent = float(self.settings_tg_percent_var.get().replace(',', '.'))
        except:
            stake_percent = 1.0
        
        settings = self.db.get_telegram_settings() or {}
        self.db.save_telegram_settings(
            api_id=self.settings_tg_api_id_var.get(),
            api_hash=self.settings_tg_api_hash_var.get(),
            session_string=settings.get('session_string'),
            phone_number=self.settings_tg_phone_var.get(),
            enabled=True,
            auto_bet=self.settings_tg_auto_bet_var.get(),
            require_confirmation=self.settings_tg_confirm_var.get(),
            auto_stake=stake,
            auto_start_listener=self.settings_tg_auto_start_var.get(),
            auto_stop_listener=self.settings_tg_auto_stop_var.get(),
            copy_mode=settings.get('copy_mode', 'OFF'),
            copy_chat_id=settings.get('copy_chat_id', ''),
            stake_type=self.settings_tg_stake_type_var.get(),
            stake_percent=stake_percent,
            dutching_enabled=False
        )
        messagebox.showinfo("Salvato", "Impostazioni Telegram salvate")
    
    def _create_simulazione_tab(self):
        """Create Simulazione tab content."""
        main_frame = ctk.CTkFrame(self.simulazione_tab, fg_color='transparent')
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        ctk.CTkLabel(main_frame, text="Simulazione", font=FONTS['title'],
                     text_color=COLORS['text_primary']).pack(anchor=tk.W, pady=(0, 10))
        
        sim_settings = self.db.get_simulation_settings()
        balance = sim_settings.get('virtual_balance', 1000) if sim_settings else 1000
        starting = sim_settings.get('starting_balance', 1000) if sim_settings else 1000
        pl = balance - starting
        pl_text = f"+{pl:.2f}" if pl >= 0 else f"{pl:.2f}"
        pl_color = COLORS['success'] if pl >= 0 else COLORS['loss']
        
        stats_frame = ctk.CTkFrame(main_frame, fg_color='transparent')
        stats_frame.pack(fill=tk.X, pady=10)
        
        balance_card = ctk.CTkFrame(stats_frame, fg_color=COLORS['bg_panel'], corner_radius=8)
        balance_card.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ctk.CTkLabel(balance_card, text="Saldo Simulato", font=('Segoe UI', 9),
                     text_color=COLORS['text_secondary']).pack(pady=(10, 2))
        ctk.CTkLabel(balance_card, text=f"{balance:.2f} EUR", font=FONTS['title'],
                     text_color=COLORS['text_primary']).pack(pady=(0, 10))
        
        pl_card = ctk.CTkFrame(stats_frame, fg_color=COLORS['bg_panel'], corner_radius=8)
        pl_card.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ctk.CTkLabel(pl_card, text="Profitto/Perdita", font=('Segoe UI', 9),
                     text_color=COLORS['text_secondary']).pack(pady=(10, 2))
        ctk.CTkLabel(pl_card, text=f"{pl_text} EUR", font=('Segoe UI', 14, 'bold'),
                     text_color=pl_color).pack(pady=(0, 10))
        
        starting_card = ctk.CTkFrame(stats_frame, fg_color=COLORS['bg_panel'], corner_radius=8)
        starting_card.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ctk.CTkLabel(starting_card, text="Saldo Iniziale", font=('Segoe UI', 9),
                     text_color=COLORS['text_secondary']).pack(pady=(10, 2))
        ctk.CTkLabel(starting_card, text=f"{starting:.2f} EUR", font=FONTS['title'],
                     text_color=COLORS['text_primary']).pack(pady=(0, 10))
        
        btn_frame = ctk.CTkFrame(main_frame, fg_color='transparent')
        btn_frame.pack(fill=tk.X, pady=10)
        ctk.CTkButton(btn_frame, text="Reset Simulazione", command=self._reset_simulation,
                      fg_color=COLORS['button_danger'], hover_color='#c62828',
                      corner_radius=6, width=150).pack(side=tk.LEFT, padx=5)
        ctk.CTkButton(btn_frame, text="Aggiorna", command=self._refresh_simulazione_tab,
                      fg_color=COLORS['button_primary'], hover_color=COLORS['back_hover'],
                      corner_radius=6, width=120).pack(side=tk.LEFT, padx=5)
        
        bets_frame = ctk.CTkFrame(main_frame, fg_color=COLORS['bg_panel'], corner_radius=8)
        bets_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        ctk.CTkLabel(bets_frame, text="Storico Scommesse Simulate", font=FONTS['heading'],
                     text_color=COLORS['text_primary']).pack(anchor=tk.W, padx=10, pady=(10, 5))
        
        self.sim_bets_frame = bets_frame
        self._refresh_simulation_bets_list()
    
    def _refresh_simulazione_tab(self):
        """Refresh the Simulazione tab."""
        for widget in self.simulazione_tab.winfo_children():
            widget.destroy()
        self._create_simulazione_tab()
    
    def _refresh_simulation_bets_list(self):
        """Refresh the simulation bets list."""
        for widget in self.sim_bets_frame.winfo_children():
            widget.destroy()
        
        sim_bets = self.db.get_simulation_bets(limit=50)
        
        columns = ('data', 'evento', 'mercato', 'tipo', 'stake', 'profitto')
        tree = ttk.Treeview(self.sim_bets_frame, columns=columns, show='headings', height=15)
        tree.heading('data', text='Data')
        tree.heading('evento', text='Evento')
        tree.heading('mercato', text='Mercato')
        tree.heading('tipo', text='Tipo')
        tree.heading('stake', text='Stake')
        tree.heading('profitto', text='Profitto')
        tree.column('data', width=130)
        tree.column('evento', width=180)
        tree.column('mercato', width=120)
        tree.column('tipo', width=50)
        tree.column('stake', width=70)
        tree.column('profitto', width=80)
        
        tree.tag_configure('win', foreground=COLORS['success'])
        tree.tag_configure('loss', foreground=COLORS['loss'])
        
        scrollbar = ttk.Scrollbar(self.sim_bets_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        if not sim_bets:
            ttk.Label(self.sim_bets_frame, text="Nessuna scommessa simulata", 
                     font=('Segoe UI', 10)).place(relx=0.5, rely=0.5, anchor=tk.CENTER)
            return
        
        for bet in sim_bets:
            placed_at = bet.get('placed_at', '')[:16] if bet.get('placed_at') else ''
            event_name = bet.get('event_name', '')[:25]
            market_name = bet.get('market_name', '')[:20]
            bet_type = bet.get('bet_type', '')
            stake = bet.get('stake', 0)
            profit = bet.get('profit', 0) or 0
            
            tag = 'win' if profit > 0 else 'loss' if profit < 0 else ''
            profit_text = f"+{profit:.2f}" if profit > 0 else f"{profit:.2f}"
            
            tree.insert('', tk.END, values=(
                placed_at,
                event_name,
                market_name,
                bet_type,
                f"{stake:.2f}",
                profit_text
            ), tags=(tag,) if tag else ())
    
    def _save_settings_from_tab(self):
        """Save settings from Impostazioni tab."""
        self.db.save_credentials(
            username=self.settings_username_var.get(),
            app_key=self.settings_appkey_var.get(),
            certificate=self.settings_cert_var.get(),
            private_key=self.settings_key_var.get()
        )
        messagebox.showinfo("Salvato", "Credenziali salvate con successo")
    
    def _save_auto_update_setting(self):
        """Save auto-update setting."""
        self.db.set_auto_update_enabled(self.auto_update_var.get())
    
    def _browse_file(self, var, filetypes):
        """Open file browser and set variable."""
        from tkinter import filedialog
        filename = filedialog.askopenfilename(filetypes=filetypes)
        if filename:
            var.set(filename)
    
    def _show_dashboard(self):
        """Show dashboard with account info and bets."""
        if not self.client:
            messagebox.showwarning("Attenzione", "Devi prima connetterti")
            return
        
        dialog = tk.Toplevel(self.root)
        dialog.title("Dashboard - Account Betfair Italy")
        dialog.geometry("800x700")
        dialog.transient(self.root)
        
        main_frame = ttk.Frame(dialog, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Header
        ttk.Label(main_frame, text="Panoramica del tuo account Betfair Italy", 
                 style='Title.TLabel').pack(anchor=tk.W, pady=(0, 20))
        
        # Stats cards frame
        stats_frame = ttk.Frame(main_frame)
        stats_frame.pack(fill=tk.X, pady=10)
        
        # Create stat cards
        def create_stat_card(parent, title, value, subtitle, col):
            card = ttk.LabelFrame(parent, text=title, padding=10)
            card.grid(row=0, column=col, padx=5, sticky='nsew')
            ttk.Label(card, text=value, style='Title.TLabel').pack()
            ttk.Label(card, text=subtitle, font=('Segoe UI', 8)).pack()
            return card
        
        # Fetch account data
        try:
            funds = self.client.get_account_funds()
            self.account_data = funds
        except:
            funds = self.account_data
        
        daily_pl = self.db.get_today_profit_loss()
        # Get active bets count from Betfair (matched orders)
        try:
            orders = self.client.get_current_orders()
            active_count = len([o for o in orders.get('matched', []) if o.get('sizeMatched', 0) > 0])
        except:
            active_count = self.db.get_active_bets_count()
        
        create_stat_card(stats_frame, "Saldo Disponibile", 
                        f"{funds.get('available', 0):.2f} EUR", 
                        "Fondi disponibili per scommettere", 0)
        create_stat_card(stats_frame, "Esposizione", 
                        f"{abs(funds.get('exposure', 0)):.2f} EUR", 
                        "Responsabilita corrente", 1)
        pl_text = f"+{daily_pl:.2f}" if daily_pl >= 0 else f"{daily_pl:.2f}"
        create_stat_card(stats_frame, "P/L Oggi", 
                        f"{pl_text} EUR", 
                        "Profitto/Perdita giornaliero", 2)
        create_stat_card(stats_frame, "Scommesse Attive", 
                        str(active_count), 
                        "In attesa di risultato", 3)
        
        for i in range(4):
            stats_frame.columnconfigure(i, weight=1)
        
        # Refresh button - fetch data in background, update UI on main thread
        def refresh_dashboard():
            def fetch_data():
                try:
                    funds = self.client.get_account_funds()
                    self.account_data = funds
                    daily_pl = self.db.get_today_profit_loss()
                    # Get active bets count from Betfair (matched orders)
                    try:
                        orders = self.client.get_current_orders()
                        active_count = len([o for o in orders.get('matched', []) if o.get('sizeMatched', 0) > 0])
                    except:
                        active_count = self.db.get_active_bets_count()
                    
                    # Schedule UI update on main thread
                    f, pl, ac = funds, daily_pl, active_count
                    self.root.after(0, lambda f=f, pl=pl, ac=ac: update_ui(f, pl, ac))
                except Exception as e:
                    err_msg = str(e)
                    self.root.after(0, lambda msg=err_msg: messagebox.showerror("Errore", msg))
            
            def update_ui(funds, daily_pl, active_count):
                if not dialog.winfo_exists():
                    return
                
                for widget in stats_frame.winfo_children():
                    widget.destroy()
                
                create_stat_card(stats_frame, "Saldo Disponibile", 
                                f"{funds.get('available', 0):.2f} EUR", 
                                "Fondi disponibili per scommettere", 0)
                create_stat_card(stats_frame, "Esposizione", 
                                f"{abs(funds.get('exposure', 0)):.2f} EUR", 
                                "Responsabilita corrente", 1)
                pl_text = f"+{daily_pl:.2f}" if daily_pl >= 0 else f"{daily_pl:.2f}"
                create_stat_card(stats_frame, "P/L Oggi", 
                                f"{pl_text} EUR", 
                                "Profitto/Perdita giornaliero", 2)
                create_stat_card(stats_frame, "Scommesse Attive", 
                                str(active_count), 
                                "In attesa di risultato", 3)
            
            threading.Thread(target=fetch_data, daemon=True).start()
        
        ttk.Button(main_frame, text="Aggiorna", command=refresh_dashboard).pack(anchor=tk.E, pady=10)
        
        # Notebook for different bet views
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True, pady=10)
        
        # Recent bets tab
        recent_frame = ttk.Frame(notebook, padding=10)
        notebook.add(recent_frame, text="Scommesse Recenti")
        self._create_bets_list(recent_frame, self.db.get_recent_bets(20))
        
        # Current orders tab (matched/unmatched)
        orders_frame = ttk.Frame(notebook, padding=10)
        notebook.add(orders_frame, text="Ordini Correnti")
        self._create_current_orders_view(orders_frame)
        
        # Bookings tab
        bookings_frame = ttk.Frame(notebook, padding=10)
        notebook.add(bookings_frame, text="Prenotazioni")
        self._create_bookings_view(bookings_frame)
        
        # Cashout tab
        cashout_frame = ttk.Frame(notebook, padding=10)
        notebook.add(cashout_frame, text="Cashout")
        self._create_cashout_view(cashout_frame, dialog)
    
    def _create_settled_bets_list(self, parent, settled_bets):
        """Create a list view of settled bets from Betfair."""
        columns = ('data', 'mercato', 'selezione', 'tipo', 'stake', 'profitto')
        tree = ttk.Treeview(parent, columns=columns, show='headings', height=12)
        tree.heading('data', text='Data')
        tree.heading('mercato', text='Market ID')
        tree.heading('selezione', text='Selezione')
        tree.heading('tipo', text='Tipo')
        tree.heading('stake', text='Stake')
        tree.heading('profitto', text='Profitto')
        tree.column('data', width=130)
        tree.column('mercato', width=140)
        tree.column('selezione', width=100)
        tree.column('tipo', width=50)
        tree.column('stake', width=70)
        tree.column('profitto', width=80)
        
        tree.tag_configure('win', foreground=COLORS['success'])
        tree.tag_configure('loss', foreground=COLORS['loss'])
        
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        if not settled_bets:
            ttk.Label(parent, text="Nessuna scommessa negli ultimi 7 giorni", 
                     font=('Segoe UI', 10)).place(relx=0.5, rely=0.5, anchor=tk.CENTER)
            return
        
        for bet in settled_bets:
            settled_date = bet.get('settledDate', '')[:16].replace('T', ' ') if bet.get('settledDate') else ''
            market_id = bet.get('marketId', '')
            selection_id = str(bet.get('selectionId', ''))
            side = bet.get('side', '')
            stake = bet.get('size', 0)
            profit = bet.get('profit', 0)
            
            tag = 'win' if profit > 0 else 'loss' if profit < 0 else ''
            profit_text = f"+{profit:.2f}" if profit > 0 else f"{profit:.2f}"
            
            tree.insert('', tk.END, values=(
                settled_date,
                market_id,
                selection_id,
                side,
                f"{stake:.2f}",
                profit_text
            ), tags=(tag,))
    
    def _create_bets_list(self, parent, bets):
        """Create a list view of bets with status colors."""
        columns = ('data', 'evento', 'mercato', 'tipo', 'stake', 'profitto', 'stato')
        tree = ttk.Treeview(parent, columns=columns, show='headings', height=12)
        tree.heading('data', text='Data')
        tree.heading('evento', text='Evento')
        tree.heading('mercato', text='Mercato')
        tree.heading('tipo', text='Tipo')
        tree.heading('stake', text='Stake')
        tree.heading('profitto', text='Prof. Atteso')
        tree.heading('stato', text='Stato')
        tree.column('data', width=100)
        tree.column('evento', width=140)
        tree.column('mercato', width=110)
        tree.column('tipo', width=50)
        tree.column('stake', width=70)
        tree.column('profitto', width=70)
        tree.column('stato', width=80)
        
        # Configure status color tags
        tree.tag_configure('matched', foreground=COLORS['matched'])
        tree.tag_configure('pending', foreground=COLORS['pending'])
        tree.tag_configure('partially_matched', foreground=COLORS['partially_matched'])
        tree.tag_configure('settled', foreground=COLORS['settled'])
        
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        if not bets:
            ttk.Label(parent, text="Nessuna scommessa recente", font=('Segoe UI', 10)).pack(pady=20)
        
        for bet in bets:
            placed_at = bet.get('placed_at', '')[:16] if bet.get('placed_at') else ''
            status = bet.get('status', '')
            profit = bet.get('potential_profit', 0)
            profit_display = f"+{profit:.2f}" if profit else "-"
            
            # Determine tag based on status
            status_lower = status.lower().replace(' ', '_')
            tag = status_lower if status_lower in ('matched', 'pending', 'partially_matched', 'settled') else ''
            
            tree.insert('', tk.END, values=(
                placed_at,
                bet.get('event_name', '')[:25],
                bet.get('market_name', '')[:18],
                bet.get('side', bet.get('bet_type', '')),
                f"{bet.get('stake', bet.get('total_stake', 0)):.2f}",
                profit_display,
                status
            ), tags=(tag,) if tag else ())
    
    def _create_current_orders_view(self, parent):
        """Create view for current orders from Betfair."""
        if not self.client:
            ttk.Label(parent, text="Non connesso").pack()
            return
        
        # Tabs for matched/unmatched
        sub_notebook = ttk.Notebook(parent)
        sub_notebook.pack(fill=tk.BOTH, expand=True)
        
        try:
            orders = self.client.get_current_orders()
        except:
            orders = {'matched': [], 'unmatched': [], 'partiallyMatched': []}
        
        # Matched
        matched_frame = ttk.Frame(sub_notebook, padding=5)
        sub_notebook.add(matched_frame, text=f"Abbinate ({len(orders['matched'])})")
        self._create_orders_list(matched_frame, orders['matched'])
        
        # Unmatched
        unmatched_frame = ttk.Frame(sub_notebook, padding=5)
        sub_notebook.add(unmatched_frame, text=f"Non Abbinate ({len(orders['unmatched'])})")
        self._create_orders_list(unmatched_frame, orders['unmatched'], show_cancel=True)
        
        # Partially matched
        partial_frame = ttk.Frame(sub_notebook, padding=5)
        sub_notebook.add(partial_frame, text=f"Parziali ({len(orders['partiallyMatched'])})")
        self._create_orders_list(partial_frame, orders['partiallyMatched'])
    
    def _create_orders_list(self, parent, orders, show_cancel=False):
        """Create list of orders with event and market names."""
        # Create container with scrollbar
        scroll_frame = ttk.Frame(parent)
        scroll_frame.pack(fill=tk.BOTH, expand=True)
        
        # Columns with evento and mercato names
        columns = ('evento', 'mercato', 'selezione', 'tipo', 'quota', 'stake', 'abbinato')
        tree = ttk.Treeview(scroll_frame, columns=columns, show='headings', height=12)
        tree.heading('evento', text='Evento')
        tree.heading('mercato', text='Mercato')
        tree.heading('selezione', text='Selezione')
        tree.heading('tipo', text='Tipo')
        tree.heading('quota', text='Quota')
        tree.heading('stake', text='Stake')
        tree.heading('abbinato', text='Abbinato')
        tree.column('evento', width=180)
        tree.column('mercato', width=120)
        tree.column('selezione', width=100)
        tree.column('tipo', width=50)
        tree.column('quota', width=60)
        tree.column('stake', width=60)
        tree.column('abbinato', width=60)
        
        scrollbar = ttk.Scrollbar(scroll_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Get market details for better display
        market_cache = {}
        bet_to_market = {}  # Map bet_id -> market_id for cancel
        
        for order in orders:
            market_id = order.get('marketId', '')
            selection_id = order.get('selectionId', '')
            bet_id = order.get('betId', '')
            
            # Store mapping for cancel
            if bet_id and market_id:
                bet_to_market[bet_id] = market_id
            
            # Try to get event/market names from cache or API
            event_name = ''
            market_name = ''
            runner_name = str(selection_id)
            
            if market_id and market_id not in market_cache:
                try:
                    if self.client:
                        catalogue = self.client.get_market_catalogue([market_id])
                        if catalogue:
                            market_cache[market_id] = catalogue[0]
                except:
                    pass
            
            if market_id in market_cache:
                cat = market_cache[market_id]
                event_name = cat.get('event', {}).get('name', '')[:25]
                market_name = cat.get('marketName', '')[:20]
                # Find runner name
                for runner in cat.get('runners', []):
                    if runner.get('selectionId') == selection_id:
                        runner_name = runner.get('runnerName', str(selection_id))[:20]
                        break
            
            tree.insert('', tk.END, iid=bet_id, values=(
                event_name,
                market_name,
                runner_name,
                order.get('side', ''),
                f"{order.get('price', 0):.2f}",
                f"{order.get('size', 0):.2f}",
                f"{order.get('sizeMatched', 0):.2f}"
            ))
        
        if show_cancel and orders:
            def cancel_selected():
                selected = tree.selection()
                if selected and self.client:
                    for bet_id in selected:
                        market_id = bet_to_market.get(bet_id)
                        if market_id:
                            try:
                                self.client.cancel_orders(market_id, [bet_id])
                            except:
                                pass
                    messagebox.showinfo("Info", "Ordini cancellati")
            
            ttk.Button(parent, text="Cancella Selezionati", command=cancel_selected).pack(pady=5)
    
    def _create_bookings_view(self, parent):
        """Create view for bet bookings."""
        bookings = self.db.get_pending_bookings()
        
        columns = ('runner', 'quota_target', 'stake', 'tipo', 'stato')
        tree = ttk.Treeview(parent, columns=columns, show='headings', height=8)
        tree.heading('runner', text='Selezione')
        tree.heading('quota_target', text='Quota Target')
        tree.heading('stake', text='Stake')
        tree.heading('tipo', text='Tipo')
        tree.heading('stato', text='Stato')
        
        tree.pack(fill=tk.BOTH, expand=True)
        
        for booking in bookings:
            tree.insert('', tk.END, iid=str(booking['id']), values=(
                booking.get('runner_name', '')[:20],
                f"{booking.get('target_price', 0):.2f}",
                f"{booking.get('stake', 0):.2f}",
                booking.get('side', ''),
                booking.get('status', '')
            ))
        
        def cancel_booking():
            selected = tree.selection()
            for bid in selected:
                self.db.cancel_booking(int(bid))
            messagebox.showinfo("Info", "Prenotazioni cancellate")
            # Refresh
            for item in tree.get_children():
                tree.delete(item)
            for booking in self.db.get_pending_bookings():
                tree.insert('', tk.END, iid=str(booking['id']), values=(
                    booking.get('runner_name', '')[:20],
                    f"{booking.get('target_price', 0):.2f}",
                    f"{booking.get('stake', 0):.2f}",
                    booking.get('side', ''),
                    booking.get('status', '')
                ))
        
        ttk.Button(parent, text="Cancella Prenotazione", command=cancel_booking).pack(pady=5)
        ttk.Label(parent, text="Le prenotazioni verranno attivate quando la quota raggiunge il target").pack()
    
    def _create_cashout_view(self, parent, dialog):
        """Create cashout view with positions and cashout buttons."""
        if not self.client:
            ttk.Label(parent, text="Non connesso a Betfair").pack()
            return
        
        # Header
        ttk.Label(parent, text="Posizioni Aperte con Cashout", style='Title.TLabel').pack(anchor=tk.W, pady=(0, 10))
        
        # Create container with scrollbar
        scroll_frame = ttk.Frame(parent)
        scroll_frame.pack(fill=tk.BOTH, expand=True)
        
        # Positions list with event and market names
        columns = ('evento', 'mercato', 'selezione', 'tipo', 'quota', 'stake', 'p/l_attuale')
        tree = ttk.Treeview(scroll_frame, columns=columns, show='headings', height=15)
        tree.heading('evento', text='Evento')
        tree.heading('mercato', text='Mercato')
        tree.heading('selezione', text='Selezione')
        tree.heading('tipo', text='Tipo')
        tree.heading('quota', text='Quota')
        tree.heading('stake', text='Stake')
        tree.heading('p/l_attuale', text='P/L Attuale')
        tree.column('evento', width=180)
        tree.column('mercato', width=120)
        tree.column('selezione', width=100)
        tree.column('tipo', width=50)
        tree.column('quota', width=60)
        tree.column('stake', width=60)
        tree.column('p/l_attuale', width=80)
        
        # Configure tags for P/L colors
        tree.tag_configure('profit', foreground=COLORS['success'])
        tree.tag_configure('loss', foreground=COLORS['loss'])
        
        scrollbar = ttk.Scrollbar(scroll_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Store position data for cashout
        positions_data = {}
        no_positions_label = [None]  # Use list to allow modification in nested function
        
        def load_positions():
            """Load matched orders and calculate P/L with event/market names."""
            # Clear previous no-positions label if exists
            if no_positions_label[0]:
                no_positions_label[0].destroy()
                no_positions_label[0] = None
            
            try:
                orders = self.client.get_current_orders()
                matched = orders.get('matched', [])
                
                for item in tree.get_children():
                    tree.delete(item)
                positions_data.clear()
                
                # Cache market catalogues for event/market names
                market_cache = {}
                
                for order in matched:
                    market_id = order.get('marketId')
                    selection_id = order.get('selectionId')
                    side = order.get('side')
                    price = order.get('price', 0)
                    stake = order.get('sizeMatched', 0)
                    
                    if stake > 0:
                        # Get event/market names
                        event_name = ''
                        market_name = ''
                        runner_name = str(selection_id)
                        
                        if market_id and market_id not in market_cache:
                            try:
                                catalogue = self.client.get_market_catalogue([market_id])
                                if catalogue:
                                    market_cache[market_id] = catalogue[0]
                            except:
                                pass
                        
                        if market_id in market_cache:
                            cat = market_cache[market_id]
                            event_name = cat.get('event', {}).get('name', '')[:25]
                            market_name = cat.get('marketName', '')[:20]
                            for runner in cat.get('runners', []):
                                if runner.get('selectionId') == selection_id:
                                    runner_name = runner.get('runnerName', str(selection_id))[:20]
                                    break
                        
                        try:
                            cashout_info = self.client.calculate_cashout(
                                market_id, selection_id, side, stake, price
                            )
                            green_up = cashout_info.get('green_up', 0)
                            pl_display = f"{green_up:+.2f}"
                            pl_tag = 'profit' if green_up > 0 else 'loss'
                        except:
                            cashout_info = None
                            pl_display = "N/D"
                            pl_tag = None
                        
                        item_id = f"{order.get('betId')}"
                        tags = (pl_tag,) if pl_tag else ()
                        tree.insert('', tk.END, iid=item_id, values=(
                            event_name,
                            market_name,
                            runner_name,
                            side,
                            f"{price:.2f}",
                            f"{stake:.2f}",
                            pl_display
                        ), tags=tags)
                        
                        positions_data[item_id] = {
                            'market_id': market_id,
                            'selection_id': selection_id,
                            'side': side,
                            'price': price,
                            'stake': stake,
                            'cashout_info': cashout_info,
                            'event_name': event_name
                        }
                
                if not positions_data:
                    no_positions_label[0] = ttk.Label(parent, text="Nessuna posizione aperta al momento", 
                             font=('Segoe UI', 10))
                    no_positions_label[0].pack(anchor=tk.W, pady=5)
                    cashout_btn.config(state='disabled')
                else:
                    cashout_btn.config(state='normal')
            except Exception as e:
                err_msg = str(e)
                self.root.after(0, lambda msg=err_msg: messagebox.showerror("Errore", f"Impossibile caricare posizioni: {msg}"))
        
        def do_cashout():
            """Execute cashout for selected position."""
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("Attenzione", "Seleziona una posizione")
                return
            
            for item_id in selected:
                pos = positions_data.get(item_id)
                if not pos or not pos.get('cashout_info'):
                    continue
                
                info = pos['cashout_info']
                confirm = messagebox.askyesno(
                    "Conferma Cashout",
                    f"Eseguire cashout?\n\n"
                    f"Tipo: {info['cashout_side']} @ {info['current_price']:.2f}\n"
                    f"Stake: {info['cashout_stake']:.2f}\n"
                    f"Profitto garantito: {info['green_up']:+.2f}"
                )
                
                if confirm:
                    try:
                        result = self.client.execute_cashout(
                            pos['market_id'],
                            pos['selection_id'],
                            info['cashout_side'],
                            info['cashout_stake'],
                            info['current_price']
                        )
                        
                        if result.get('status') == 'SUCCESS':
                            # Record cashout transaction with profit/loss
                            self.db.save_cashout_transaction(
                                market_id=pos['market_id'],
                                selection_id=pos['selection_id'],
                                original_bet_id=item_id,
                                cashout_bet_id=result.get('betId'),
                                original_side=pos['side'],
                                original_stake=pos['stake'],
                                original_price=pos['price'],
                                cashout_side=info['cashout_side'],
                                cashout_stake=info['cashout_stake'],
                                cashout_price=result.get('averagePriceMatched') or info['current_price'],
                                profit_loss=info['green_up']
                            )
                            messagebox.showinfo("Successo", f"Cashout eseguito!\nProfitto bloccato: {info['green_up']:+.2f}")
                            load_positions()
                            # Update dashboard stats
                            self._update_balance()
                        elif result.get('status') == 'ERROR':
                            messagebox.showerror("Errore", f"Cashout fallito: {result.get('error', 'Errore sconosciuto')}")
                        else:
                            messagebox.showerror("Errore", f"Cashout fallito: {result.get('status')}")
                    except Exception as e:
                        err_msg = str(e)
                        messagebox.showerror("Errore", f"Errore cashout: {err_msg}")
        
        # Buttons frame
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(btn_frame, text="Aggiorna Posizioni", command=load_positions).pack(side=tk.LEFT, padx=5)
        
        cashout_btn = tk.Button(btn_frame, text="CASHOUT", bg='#28a745', fg='white', 
                               font=('Segoe UI', 10, 'bold'), command=do_cashout)
        cashout_btn.pack(side=tk.LEFT, padx=5)
        
        # Live tracking toggle
        live_tracking_var = tk.BooleanVar(value=True)  # Auto-enabled by default
        live_tracking_id = [None]
        
        def toggle_live_tracking():
            if live_tracking_var.get():
                start_live_tracking()
            else:
                stop_live_tracking()
        
        def start_live_tracking():
            """Start live P/L tracking."""
            def update_pl():
                if not live_tracking_var.get():
                    return
                try:
                    load_positions()
                except:
                    pass
                live_tracking_id[0] = parent.after(LIVE_REFRESH_INTERVAL, update_pl)
            
            live_tracking_id[0] = parent.after(LIVE_REFRESH_INTERVAL, update_pl)
            live_status_label.config(text="LIVE", foreground='#28a745')
        
        def stop_live_tracking():
            """Stop live tracking."""
            if live_tracking_id[0]:
                parent.after_cancel(live_tracking_id[0])
                live_tracking_id[0] = None
            live_status_label.config(text="", foreground='gray')
        
        ttk.Checkbutton(btn_frame, text="Live Tracking", variable=live_tracking_var,
                       command=toggle_live_tracking).pack(side=tk.LEFT, padx=15)
        
        live_status_label = ttk.Label(btn_frame, text="", font=('Segoe UI', 9, 'bold'))
        live_status_label.pack(side=tk.LEFT)
        
        # Stop live tracking when dialog closes
        def on_close():
            stop_live_tracking()
            dialog.destroy()
        
        dialog.protocol("WM_DELETE_WINDOW", on_close)
        
        # Auto-cashout section
        auto_frame = ttk.LabelFrame(parent, text="Auto-Cashout", padding=10)
        auto_frame.pack(fill=tk.X, pady=10)
        
        ttk.Label(auto_frame, text="Target Profitto:").grid(row=0, column=0, padx=5)
        profit_target = ttk.Entry(auto_frame, width=10)
        profit_target.insert(0, "10.00")
        profit_target.grid(row=0, column=1, padx=5)
        
        ttk.Label(auto_frame, text="Limite Perdita:").grid(row=0, column=2, padx=5)
        loss_limit = ttk.Entry(auto_frame, width=10)
        loss_limit.insert(0, "-5.00")
        loss_limit.grid(row=0, column=3, padx=5)
        
        def set_auto_cashout():
            """Set auto-cashout rule for selected position."""
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("Attenzione", "Seleziona una posizione")
                return
            
            try:
                target = float(profit_target.get())
                limit = float(loss_limit.get())
            except:
                messagebox.showerror("Errore", "Valori non validi")
                return
            
            for item_id in selected:
                pos = positions_data.get(item_id)
                if pos:
                    self.db.save_auto_cashout_rule(
                        pos['market_id'],
                        item_id,
                        target,
                        limit
                    )
            
            messagebox.showinfo("Info", "Auto-cashout impostato")
        
        ttk.Button(auto_frame, text="Imposta Auto-Cashout", command=set_auto_cashout).grid(row=0, column=4, padx=10)
        
        ttk.Label(parent, text="Auto-cashout esegue automaticamente quando P/L raggiunge target o limite",
                 font=('Segoe UI', 8)).pack(anchor=tk.W)
        
        # Load positions on view creation and auto-start live tracking
        load_positions()
        start_live_tracking()  # Auto-start live tracking by default
    
    def _start_booking_monitor(self):
        """Start monitoring bookings for price triggers."""
        self._do_booking_monitor()
    
    def _do_booking_monitor(self):
        """Single booking monitor cycle."""
        if self.client:
            bookings = self.db.get_pending_bookings()
            self.pending_bookings = bookings
            if bookings:
                # Run in background thread to avoid UI blocking
                threading.Thread(target=self._check_booking_triggers, args=(bookings,), daemon=True).start()
        # Schedule next check
        self.booking_monitor_id = self.root.after(10000, self._do_booking_monitor)
    
    def _check_booking_triggers(self, bookings):
        """Check if any booking should be triggered (runs in background thread)."""
        if not self.client:
            return
        
        # Group bookings by market to reduce API calls
        markets_to_check = {}
        for booking in bookings:
            mid = booking['market_id']
            if mid not in markets_to_check:
                markets_to_check[mid] = []
            markets_to_check[mid].append(booking)
        
        for market_id, market_bookings in markets_to_check.items():
            try:
                market = self.client.get_market_with_prices(market_id)
                
                for booking in market_bookings:
                    for runner in market['runners']:
                        if runner['selectionId'] == booking['selection_id']:
                            current_price = runner.get('backPrice') if booking['side'] == 'BACK' else runner.get('layPrice')
                            
                            should_trigger = False
                            if booking['side'] == 'BACK' and current_price and current_price >= booking['target_price']:
                                should_trigger = True
                            elif booking['side'] == 'LAY' and current_price and current_price <= booking['target_price']:
                                should_trigger = True
                            
                            if should_trigger:
                                result = self.client.place_bets(market_id, [{
                                    'selectionId': booking['selection_id'],
                                    'side': booking['side'],
                                    'price': current_price,
                                    'size': booking['stake']
                                }])
                                
                                if result.get('status') == 'SUCCESS':
                                    bet_id = result['instructionReports'][0].get('betId') if result.get('instructionReports') else None
                                    self.db.update_booking_status(booking['id'], 'TRIGGERED', bet_id)
                                else:
                                    self.db.update_booking_status(booking['id'], 'FAILED')
                            break
            except Exception:
                pass
    
    def _broadcast_copy_bet(self, event_name, market_name, selection, side, price, stake_percent, stake_amount=None):
        """Broadcast a COPY BET message to followers (Master mode only)."""
        logging.info(f"=== COPY TRADING BROADCAST ===")
        logging.info(f"_broadcast_copy_bet called: {event_name}, {selection}, {side}")
        
        settings = self.db.get_telegram_settings()
        if not settings:
            logging.warning("Copy Trading: No telegram settings found")
            return
        
        copy_mode = settings.get('copy_mode', 'OFF')
        copy_chat_id = settings.get('copy_chat_id', '')
        
        logging.info(f"Copy Trading settings: mode={copy_mode}, chat_id={copy_chat_id}")
        
        if copy_mode != 'MASTER' or not copy_chat_id:
            logging.info(f"Copy Trading: Not in MASTER mode or no chat_id (mode={copy_mode})")
            return
        
        # Check telegram listener exists
        if not self.telegram_listener:
            logging.warning("Copy Trading: Telegram listener object is None")
            return
        
        logging.info(f"Copy Trading: telegram_listener.running = {self.telegram_listener.running}")
        logging.info(f"Copy Trading: sending_connected = {self.telegram_listener.sending_connected}")
        # NOTE: Don't check self.running - send_message() auto-connects dedicated send client if needed
        
        message = f"""COPY BET
Evento: {event_name}
Mercato: {market_name}
Selezione: {selection}
Tipo: {side}
Quota: {price:.2f}
Stake: {stake_percent:.1f}%
StakeEUR: {stake_amount:.2f}"""
        
        try:
            self.telegram_listener.send_message(copy_chat_id, message)
            logging.info(f"Copy Trading: Broadcast BET sent to {copy_chat_id}")
        except Exception as e:
            logging.error(f"Copy Trading broadcast error: {e}")
    
    def _broadcast_copy_dutching(self, event_name, market_name, selections, side, profit_target, total_stake):
        """Broadcast a COPY DUTCHING message to followers (Master mode only).
        
        Sends a single message with all dutching selections for followers to replicate.
        
        Args:
            event_name: Event name
            market_name: Market name (e.g., "Correct Score")
            selections: List of {'runnerName', 'price', 'stake'}
            side: BACK or LAY (or MIXED for mixed dutching)
            profit_target: Target profit in EUR
            total_stake: Total stake in EUR
        """
        logging.info(f"=== COPY TRADING DUTCHING BROADCAST ===")
        logging.info(f"_broadcast_copy_dutching: {event_name}, {len(selections)} selections, profit_target={profit_target}")
        
        settings = self.db.get_telegram_settings()
        if not settings:
            logging.warning("Copy Trading Dutching: No telegram settings found")
            return
        
        copy_mode = settings.get('copy_mode', 'OFF')
        copy_chat_id = settings.get('copy_chat_id', '')
        
        if copy_mode != 'MASTER' or not copy_chat_id:
            logging.info(f"Copy Trading Dutching: Not in MASTER mode or no chat_id")
            return
        
        if not self.telegram_listener:
            logging.warning("Copy Trading Dutching: Telegram listener object is None")
            return
        
        # Build selections string: "2-1 @ 9.50, 2-0 @ 11.00, 1-0 @ 8.00"
        selections_str = ", ".join([f"{s['runnerName']} @ {s['price']:.2f}" for s in selections])
        
        # Check if mixed dutching (some BACK, some LAY)
        has_back = any(s.get('effectiveType', side) == 'BACK' for s in selections)
        has_lay = any(s.get('effectiveType', side) == 'LAY' for s in selections)
        if has_back and has_lay:
            side = 'MIXED'
        
        message = f"""COPY DUTCHING
Evento: {event_name}
Mercato: {market_name}
Selezioni: {selections_str}
Tipo: {side}
ProfitTargetEUR: {profit_target:.2f}
StakeTotaleEUR: {total_stake:.2f}"""
        
        try:
            self.telegram_listener.send_message(copy_chat_id, message)
            logging.info(f"Copy Trading Dutching: Broadcast sent to {copy_chat_id}")
        except Exception as e:
            logging.error(f"Copy Trading Dutching broadcast error: {e}")
    
    def _broadcast_copy_cashout(self, event_name):
        """Broadcast a COPY CASHOUT message to followers (Master mode only)."""
        settings = self.db.get_telegram_settings()
        if not settings:
            return
        
        copy_mode = settings.get('copy_mode', 'OFF')
        copy_chat_id = settings.get('copy_chat_id', '')
        
        if copy_mode != 'MASTER' or not copy_chat_id:
            return
        
        if not self.telegram_listener:
            logging.warning("Copy Trading Cashout: Telegram listener object is None")
            return
        
        # NOTE: Don't check self.running - send_message() auto-connects dedicated send client if needed
        logging.info(f"Copy Trading Cashout: broadcasting to {copy_chat_id}")
        
        message = f"""COPY CASHOUT
Evento: {event_name}"""
        
        try:
            self.telegram_listener.send_message(copy_chat_id, message)
            print(f"Copy Trading: Cashout broadcast sent to {copy_chat_id}")
        except Exception as e:
            print(f"Copy Trading cashout broadcast error: {e}")
    
    def _start_settlement_monitor(self):
        """Start monitoring bets for settlement/outcome updates."""
        self._do_settlement_monitor()
    
    def _do_settlement_monitor(self):
        """Single settlement monitor cycle - check for settled bets."""
        if self.client and not self.simulation_mode:
            threading.Thread(target=self._check_bet_settlements, daemon=True).start()
        # Schedule next check every 60 seconds
        self.settlement_monitor_id = self.root.after(60000, self._do_settlement_monitor)
    
    def _check_bet_settlements(self):
        """Check unsettled bets for outcomes (runs in background thread)."""
        if not self.client:
            return
        
        try:
            # Get unsettled bets from database
            unsettled = self.db.get_unsettled_bets(limit=50)
            if not unsettled:
                return
            
            # Get bet IDs to check
            bet_ids = [bet['bet_id'] for bet in unsettled if bet.get('bet_id')]
            if not bet_ids:
                return
            
            # Get cleared orders from Betfair
            cleared = self.client.get_cleared_orders(bet_ids=bet_ids)
            
            # Update database with outcomes
            for settled_bet in cleared:
                bet_id = settled_bet.get('bet_id')
                outcome = settled_bet.get('outcome')
                profit = settled_bet.get('profit')
                settled_date = settled_bet.get('settled_date')
                
                if bet_id and outcome:
                    self.db.update_bet_outcome(bet_id, outcome, profit, settled_date)
            
            # Refresh statistics view if visible
            if hasattr(self, 'dashboard_stats_tab_frame'):
                self.root.after(0, lambda: self._refresh_statistics_view(self.dashboard_stats_tab_frame))
        except Exception as e:
            print(f"Settlement monitor error: {e}")
    
    def _start_auto_cashout_monitor(self):
        """Start monitoring positions for auto-cashout triggers."""
        self._do_auto_cashout_monitor()
    
    def _do_auto_cashout_monitor(self):
        """Single auto-cashout monitor cycle."""
        if self.client:
            rules = self.db.get_active_auto_cashout_rules()
            if rules:
                threading.Thread(target=self._check_auto_cashout_triggers, args=(rules,), daemon=True).start()
        # Schedule next check every 15 seconds
        self.auto_cashout_monitor_id = self.root.after(15000, self._do_auto_cashout_monitor)
    
    def _check_auto_cashout_triggers(self, rules):
        """Check if any auto-cashout rule should be triggered."""
        if not self.client:
            return
        
        for rule in rules:
            try:
                market_id = rule['market_id']
                bet_id = rule['bet_id']
                profit_target = rule['profit_target']
                loss_limit = rule['loss_limit']
                
                # Get current orders to find the position
                orders = self.client.get_current_orders()
                matched = orders.get('matched', [])
                
                for order in matched:
                    if str(order.get('betId')) == str(bet_id):
                        selection_id = order.get('selectionId')
                        side = order.get('side')
                        price = order.get('price', 0)
                        stake = order.get('sizeMatched', 0)
                        
                        if stake > 0:
                            try:
                                cashout_info = self.client.calculate_cashout(
                                    market_id, selection_id, side, stake, price
                                )
                                current_pl = cashout_info['green_up']
                                
                                # Check if should trigger
                                should_trigger = False
                                trigger_reason = ""
                                
                                if current_pl >= profit_target:
                                    should_trigger = True
                                    trigger_reason = f"Target profitto raggiunto: {current_pl:+.2f}"
                                elif current_pl <= loss_limit:
                                    should_trigger = True
                                    trigger_reason = f"Limite perdita raggiunto: {current_pl:+.2f}"
                                
                                if should_trigger:
                                    # Execute cashout
                                    result = self.client.execute_cashout(
                                        market_id,
                                        selection_id,
                                        cashout_info['cashout_side'],
                                        cashout_info['cashout_stake'],
                                        cashout_info['current_price']
                                    )
                                    
                                    if result.get('status') == 'SUCCESS':
                                        self.db.deactivate_auto_cashout_rule(rule['id'])
                                        # Use root.after to safely show message from main thread
                                        def show_cashout_message(reason):
                                            messagebox.showinfo("Auto-Cashout", f"Cashout automatico eseguito!\n{reason}")
                                        self.root.after(0, lambda: show_cashout_message(trigger_reason))
                            except Exception:
                                pass
                        break
            except Exception:
                pass
    
    def _show_booking_dialog(self, selection_id, runner_name, current_price, market_id):
        """Show dialog to create a bet booking."""
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Prenota Scommessa - {runner_name}")
        dialog.geometry("400x300")
        dialog.transient(self.root)
        dialog.grab_set()
        
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text=f"Selezione: {runner_name}", style='Header.TLabel').pack(anchor=tk.W)
        ttk.Label(frame, text=f"Quota Attuale: {current_price:.2f}").pack(anchor=tk.W, pady=5)
        
        ttk.Label(frame, text="Tipo:").pack(anchor=tk.W, pady=(10, 0))
        side_var = tk.StringVar(value='BACK')
        side_frame = ttk.Frame(frame)
        side_frame.pack(fill=tk.X)
        ttk.Radiobutton(side_frame, text="Back", variable=side_var, value='BACK').pack(side=tk.LEFT)
        ttk.Radiobutton(side_frame, text="Lay", variable=side_var, value='LAY').pack(side=tk.LEFT, padx=10)
        
        ttk.Label(frame, text="Quota Target:").pack(anchor=tk.W, pady=(10, 0))
        target_var = tk.StringVar(value=str(current_price + 0.25))
        ttk.Entry(frame, textvariable=target_var, width=10).pack(anchor=tk.W)
        
        ttk.Label(frame, text="Stake (EUR):").pack(anchor=tk.W, pady=(10, 0))
        stake_var = tk.StringVar(value='10.00')
        ttk.Entry(frame, textvariable=stake_var, width=10).pack(anchor=tk.W)
        
        def save_booking():
            try:
                target = float(target_var.get().replace(',', '.'))
                stake = float(stake_var.get().replace(',', '.'))
                
                if stake < 1.0 and side_var.get() == 'BACK':
                    messagebox.showerror("Errore", "Stake minimo BACK: 1.00 EUR")
                    return
                
                self.db.save_booking(
                    self.current_event['name'] if self.current_event else '',
                    market_id,
                    self.current_market['marketName'] if self.current_market else '',
                    int(selection_id),
                    runner_name,
                    side_var.get(),
                    target,
                    stake,
                    current_price
                )
                
                messagebox.showinfo("Successo", f"Prenotazione salvata!\nQuando la quota raggiunge {target:.2f}, la scommessa verra piazzata automaticamente.")
                dialog.destroy()
            except ValueError:
                messagebox.showerror("Errore", "Valori non validi")
        
        ttk.Button(frame, text="Prenota", command=save_booking).pack(pady=20)
    
    def _show_dutching_modal(self):
        """Show Fairbot-style dutching modal with advanced options."""
        if not self.current_market:
            return
        
        dialog = tk.Toplevel(self.root)
        event_name = self.current_event.get('name', '') if self.current_event else ''
        market_name = self.current_market.get('marketName', '')
        status_text = "IN-PLAY" if self.current_event and self.current_event.get('inPlayOdds') else "PRE-MATCH"
        dialog.title(f"Dutching - {event_name} | {market_name} | {status_text}")
        dialog.geometry("900x650")
        dialog.transient(self.root)
        dialog.grab_set()
        
        # Store runner data for calculations
        dialog.runner_data = {}
        dialog.calculated_results = None
        dialog.bet_type = 'BACK'
        
        main_frame = ttk.Frame(dialog, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # ============ TOP SECTION: Dutching Type ============
        type_frame = ttk.LabelFrame(main_frame, text="Tipo Dutching", padding=10)
        type_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Left column: Stake Available / Required Profit / Variable Profit
        left_col = ttk.Frame(type_frame)
        left_col.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        dutching_mode = tk.StringVar(value='STAKE')
        stake_var = tk.StringVar(value='100')
        profit_var = tk.StringVar(value='10')
        
        # Row 1: Stake Available
        row1 = ttk.Frame(left_col)
        row1.pack(fill=tk.X, pady=2)
        ttk.Radiobutton(row1, text="Stake Totale", variable=dutching_mode, value='STAKE').pack(side=tk.LEFT)
        stake_entry = ttk.Entry(row1, textvariable=stake_var, width=10)
        stake_entry.pack(side=tk.LEFT, padx=5)
        ttk.Label(row1, text="EUR").pack(side=tk.LEFT)
        
        # Row 2: Required Profit
        row2 = ttk.Frame(left_col)
        row2.pack(fill=tk.X, pady=2)
        ttk.Radiobutton(row2, text="Profitto Target", variable=dutching_mode, value='PROFIT').pack(side=tk.LEFT)
        profit_entry = ttk.Entry(row2, textvariable=profit_var, width=10)
        profit_entry.pack(side=tk.LEFT, padx=5)
        ttk.Label(row2, text="EUR").pack(side=tk.LEFT)
        
        
        # Right column: Bet type (BACK/LAY)
        right_col = ttk.Frame(type_frame)
        right_col.pack(side=tk.RIGHT, padx=20)
        
        bet_type_var = tk.StringVar(value='BACK')
        ttk.Label(right_col, text="Tipo Scommessa:").pack(anchor=tk.W)
        bet_frame = ttk.Frame(right_col)
        bet_frame.pack(fill=tk.X)
        
        back_btn = ctk.CTkButton(bet_frame, text="BACK", fg_color=COLORS['back'], 
                                 hover_color=COLORS['back_hover'], width=70, corner_radius=6,
                                 command=lambda: [bet_type_var.set('BACK'), update_bet_type_buttons()])
        back_btn.pack(side=tk.LEFT, padx=2)
        
        lay_btn = ctk.CTkButton(bet_frame, text="LAY", fg_color=COLORS['button_secondary'], 
                                hover_color=COLORS['bg_hover'], width=70, corner_radius=6,
                                command=lambda: [bet_type_var.set('LAY'), update_bet_type_buttons()])
        lay_btn.pack(side=tk.LEFT, padx=2)
        
        mixed_btn = ctk.CTkButton(bet_frame, text="MIXED", fg_color=COLORS['button_secondary'], 
                                  hover_color=COLORS['bg_hover'], width=70, corner_radius=6,
                                  command=lambda: [bet_type_var.set('MIXED'), update_bet_type_buttons()])
        mixed_btn.pack(side=tk.LEFT, padx=2)
        
        def update_bet_type_buttons():
            bet_type = bet_type_var.get()
            if bet_type == 'BACK':
                back_btn.configure(fg_color=COLORS['back'])
                lay_btn.configure(fg_color=COLORS['button_secondary'])
                mixed_btn.configure(fg_color=COLORS['button_secondary'])
            elif bet_type == 'LAY':
                back_btn.configure(fg_color=COLORS['button_secondary'])
                lay_btn.configure(fg_color=COLORS['lay'])
                mixed_btn.configure(fg_color=COLORS['button_secondary'])
            else:  # MIXED
                back_btn.configure(fg_color=COLORS['button_secondary'])
                lay_btn.configure(fg_color=COLORS['button_secondary'])
                mixed_btn.configure(fg_color=COLORS['warning'])  # Orange for MIXED
            repopulate_tree()
            recalculate()
        
        # Book Value display
        book_frame = ttk.Frame(type_frame)
        book_frame.pack(side=tk.RIGHT, padx=20)
        book_value_var = tk.StringVar(value="Book Value: -")
        ttk.Label(book_frame, textvariable=book_value_var, font=('Arial', 10, 'bold')).pack()
        
        # Mixed Mode indicator
        mode_indicator_var = tk.StringVar(value="")
        mode_indicator_label = ctk.CTkLabel(book_frame, textvariable=mode_indicator_var, 
                                            font=('Arial', 10, 'bold'), text_color=COLORS['warning'])
        mode_indicator_label.pack(pady=(5, 0))
        
        # ============ MIDDLE SECTION: Runners Table ============
        table_frame = ttk.Frame(main_frame)
        table_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Create treeview with columns
        columns = ('selected', 'runner', 'offset', 'odds', 'stake', 'profit_loss')
        tree = ttk.Treeview(table_frame, columns=columns, show='headings', height=15)
        
        tree.heading('selected', text='')
        tree.heading('runner', text='Selezione')
        tree.heading('offset', text='Offset')
        tree.heading('odds', text='Quota')
        tree.heading('stake', text='Stake')
        tree.heading('profit_loss', text='Profitto/Perdita')
        
        tree.column('selected', width=40, anchor='center')
        tree.column('runner', width=220, anchor='w')
        tree.column('offset', width=80, anchor='center')
        tree.column('odds', width=100, anchor='center')
        tree.column('stake', width=100, anchor='e')
        tree.column('profit_loss', width=140, anchor='e')
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Configure tag colors for profit/loss
        tree.tag_configure('profit', foreground=COLORS['success'])
        tree.tag_configure('loss', foreground=COLORS['loss'])
        tree.tag_configure('selected', background='#e8f4fc')
        tree.tag_configure('swapped', background='#fff3e0')  # Orange tint for swapped rows
        tree.tag_configure('back_type', foreground=COLORS['back'])
        tree.tag_configure('lay_type', foreground=COLORS['lay'])
        
        # Store runner selection and offset data
        # Format: {item_id: {'selected': bool, 'offset': int, 'selectionId': int, 'side': 'BACK'|'LAY', 'runner': runner}}
        runner_selections = {}
        
        # Check if LIVE and CORRECT_SCORE to filter impossible results
        is_live = self.current_event and self.current_event.get('inPlayOdds', False)
        is_correct_score = 'CORRECT_SCORE' in market_name.upper() or 'RISULTATO ESATTO' in market_name.upper()
        
        # Store base runners for repopulation
        base_runners = []
        for runner in self.current_market.get('runners', []):
            runner_status = runner.get('status', 'ACTIVE')
            if is_live and is_correct_score and runner_status != 'ACTIVE':
                continue
            base_runners.append(runner)
        
        def repopulate_tree():
            """Repopulate tree based on current bet type mode."""
            nonlocal runner_selections
            
            # Clear tree
            for item in tree.get_children():
                tree.delete(item)
            
            # Preserve selections if possible
            old_selections = {k: v['selected'] for k, v in runner_selections.items()}
            runner_selections = {}
            
            bet_type = bet_type_var.get()
            
            if bet_type == 'MIXED':
                # MIXED mode: show 2 rows per runner (BACK and LAY)
                for runner in base_runners:
                    sel_id = runner['selectionId']
                    back_price = runner.get('backPrice', 0) or 0
                    lay_price = runner.get('layPrice', 0) or 0
                    
                    # BACK row
                    item_id_back = f"{sel_id}_B"
                    runner_selections[item_id_back] = {
                        'selected': old_selections.get(item_id_back, False),
                        'offset': 0,
                        'selectionId': sel_id,
                        'side': 'BACK',
                        'runner': runner
                    }
                    back_str = f"{back_price:.2f}" if back_price > 0 else '-'
                    tree.insert('', tk.END, iid=item_id_back, values=(
                        '[B]' if runner_selections[item_id_back]['selected'] else '[ ]',
                        f"[B] {runner['runnerName']}",
                        '0',
                        back_str,
                        '-',
                        '-'
                    ), tags=('back_row',))
                    
                    # LAY row
                    item_id_lay = f"{sel_id}_L"
                    runner_selections[item_id_lay] = {
                        'selected': old_selections.get(item_id_lay, False),
                        'offset': 0,
                        'selectionId': sel_id,
                        'side': 'LAY',
                        'runner': runner
                    }
                    lay_str = f"{lay_price:.2f}" if lay_price > 0 else '-'
                    tree.insert('', tk.END, iid=item_id_lay, values=(
                        '[L]' if runner_selections[item_id_lay]['selected'] else '[ ]',
                        f"[L] {runner['runnerName']}",
                        '0',
                        lay_str,
                        '-',
                        '-'
                    ), tags=('lay_row',))
            else:
                # BACK or LAY mode: 1 row per runner
                for runner in base_runners:
                    sel_id = runner['selectionId']
                    back_price = runner.get('backPrice', 0) or 0
                    lay_price = runner.get('layPrice', 0) or 0
                    
                    item_id = str(sel_id)
                    runner_selections[item_id] = {
                        'selected': old_selections.get(item_id, False),
                        'offset': 0,
                        'selectionId': sel_id,
                        'side': bet_type,
                        'runner': runner
                    }
                    
                    price = back_price if bet_type == 'BACK' else lay_price
                    price_str = f"{price:.2f}" if price > 0 else '-'
                    
                    tree.insert('', tk.END, iid=item_id, values=(
                        '[ ]',
                        runner['runnerName'],
                        '0',
                        price_str,
                        '-',
                        '-'
                    ))
        
        # Configure additional tag colors
        tree.tag_configure('back_row', background='#e3f2fd')  # Light blue tint
        tree.tag_configure('lay_row', background='#fce4ec')   # Light pink tint
        
        # Initial population
        repopulate_tree()
        
        # Handle row selection (toggle)
        def on_row_click(event):
            region = tree.identify_region(event.x, event.y)
            if region == 'cell':
                col = tree.identify_column(event.x)
                item_id = tree.identify_row(event.y)
                if item_id and item_id in runner_selections:
                    col_num = int(col.replace('#', ''))
                    
                    if col_num == 1 or col_num == 2:  # Selected or runner column - toggle selection
                        runner_selections[item_id]['selected'] = not runner_selections[item_id]['selected']
                        update_row(item_id)
                        recalculate()
        
        def on_row_double_click(event):
            """Double-click on offset to edit."""
            region = tree.identify_region(event.x, event.y)
            if region == 'cell':
                col = tree.identify_column(event.x)
                item_id = tree.identify_row(event.y)
                if item_id and item_id in runner_selections and col == '#3':  # Offset column
                    edit_offset(item_id)
        
        def edit_offset(item_id):
            """Show popup to edit offset value."""
            current_offset = runner_selections[item_id]['offset']
            
            popup = tk.Toplevel(dialog)
            popup.title("Modifica Offset")
            popup.geometry("200x80")
            popup.transient(dialog)
            popup.grab_set()
            
            ttk.Label(popup, text="Offset (ticks):").pack(pady=5)
            offset_var = tk.StringVar(value=str(current_offset))
            entry = ttk.Entry(popup, textvariable=offset_var, width=10)
            entry.pack()
            entry.focus()
            entry.select_range(0, tk.END)
            
            def save_offset():
                try:
                    new_offset = int(offset_var.get())
                    runner_selections[item_id]['offset'] = new_offset
                    update_row(item_id)
                    recalculate()
                    popup.destroy()
                except ValueError:
                    pass
            
            entry.bind('<Return>', lambda e: save_offset())
            ttk.Button(popup, text="OK", command=save_offset).pack(pady=5)
        
        def update_row(item_id):
            """Update a single row display."""
            if item_id not in runner_selections:
                return
            data = runner_selections[item_id]
            runner = data['runner']
            bet_type = bet_type_var.get()
            effective_type = data.get('side', bet_type)
            
            # Get price based on effective type
            if effective_type == 'BACK':
                base_price = runner.get('backPrice', 0) or 0
            else:
                base_price = runner.get('layPrice', 0) or 0
            
            # Apply offset (price ticks)
            price = apply_price_offset(base_price, data['offset'])
            price_str = f"{price:.2f}" if price > 0 else '-'
            
            # Selection indicator with type
            if data['selected']:
                type_tag = 'B' if effective_type == 'BACK' else 'L'
                sel_indicator = f'[{type_tag}]'
            else:
                sel_indicator = '[ ]'
            
            # Get calculated stake/profit if available
            stake_str = '-'
            profit_str = '-'
            tags = []
            
            # Apply row color based on side in MIXED mode
            if bet_type == 'MIXED':
                tags.append('back_row' if effective_type == 'BACK' else 'lay_row')
            
            if dialog.calculated_results:
                for r in dialog.calculated_results:
                    if r.get('item_id') == item_id:
                        stake_str = f"{r['stake']:.2f} EUR"
                        profit = r.get('profitIfWins', 0)
                        if profit >= 0:
                            profit_str = f"+{profit:.2f} EUR"
                            tags.append('profit')
                        else:
                            profit_str = f"{profit:.2f} EUR"
                            tags.append('loss')
                        break
            
            # Runner name with type indicator in MIXED mode
            if bet_type == 'MIXED':
                runner_display = f"[{effective_type[0]}] {runner['runnerName']}"
            else:
                runner_display = runner['runnerName']
            
            tree.item(item_id, values=(
                sel_indicator,
                runner_display,
                str(data['offset']),
                price_str,
                stake_str,
                profit_str
            ), tags=tuple(tags))
        
        def apply_price_offset(price, offset):
            """Apply tick offset to price."""
            if price <= 0 or offset == 0:
                return price
            
            # Betfair tick increments
            if price < 2:
                increment = 0.01
            elif price < 3:
                increment = 0.02
            elif price < 4:
                increment = 0.05
            elif price < 6:
                increment = 0.1
            elif price < 10:
                increment = 0.2
            elif price < 20:
                increment = 0.5
            elif price < 30:
                increment = 1.0
            elif price < 50:
                increment = 2.0
            elif price < 100:
                increment = 5.0
            else:
                increment = 10.0
            
            new_price = price + (increment * offset)
            return max(1.01, min(1000, round(new_price, 2)))
        
        def recalculate():
            """Recalculate dutching stakes."""
            bet_type = bet_type_var.get()
            mode = dutching_mode.get()
            
            # Gather selected runners with their effective type and prices
            selections = []
            has_mixed = False
            is_mixed_mode = (bet_type == 'MIXED')
            
            for item_id, data in runner_selections.items():
                if data['selected']:
                    runner = data['runner']
                    effective_type = data.get('side', bet_type)
                    
                    # Check if we have mixed types
                    if is_mixed_mode:
                        has_mixed = True
                    
                    # Get base price based on effective type
                    if effective_type == 'BACK':
                        base_price = runner.get('backPrice', 0) or 0
                    else:
                        base_price = runner.get('layPrice', 0) or 0
                    
                    # Apply offset
                    price = apply_price_offset(base_price, data['offset'])
                    
                    if price > 1:
                        selections.append({
                            'item_id': item_id,
                            'selectionId': data['selectionId'],
                            'runnerName': runner['runnerName'],
                            'price': price,
                            'effectiveType': effective_type
                        })
            
            if not selections:
                dialog.calculated_results = None
                book_value_var.set("Book Value: -")
                total_var.set("Totale: -")
                mode_indicator_var.set("")
                for item_id in runner_selections:
                    update_row(item_id)
                return
            
            # Calculate book value (implied probability)
            implied = sum(1.0 / s['price'] for s in selections) * 100
            book_value_var.set(f"Book Value: {implied:.1f}%")
            
            # Check if we have mixed BACK+LAY
            back_count = sum(1 for s in selections if s['effectiveType'] == 'BACK')
            lay_count = sum(1 for s in selections if s['effectiveType'] == 'LAY')
            has_mixed = back_count > 0 and lay_count > 0
            
            # Update mixed mode indicator
            if is_mixed_mode:
                mode_indicator_var.set(f"MIXED: {back_count}B + {lay_count}L")
            elif has_mixed:
                mode_indicator_var.set(f"MIXED: {back_count}B + {lay_count}L")
            else:
                mode_indicator_var.set("")
            
            try:
                from dutching import calculate_mixed_dutching, calculate_back_target_profit
                
                commission = 4.5
                
                if mode == 'STAKE':
                    amount = float(stake_var.get().replace(',', '.'))
                    if has_mixed:
                        results, profit, _ = calculate_mixed_dutching(selections, amount, commission)
                    else:
                        results, profit, _ = calculate_dutching_stakes(selections, amount, bet_type if not is_mixed_mode else selections[0]['effectiveType'])
                else:  # PROFIT mode - Target profit fisso
                    target_profit = float(profit_var.get().replace(',', '.'))
                    
                    if has_mixed:
                        # Mixed BACK+LAY con target profit
                        results, profit, _ = calculate_mixed_dutching(selections, target_profit, commission)
                    elif bet_type == 'BACK' or (is_mixed_mode and back_count > 0):
                        # BACK dutching con target profit
                        results, profit, _ = calculate_back_target_profit(selections, target_profit, commission)
                    else:
                        # LAY dutching: target is best case (all lose)
                        commission_mult = 1 - (commission / 100)
                        required_stake = target_profit / commission_mult
                        results, profit, _ = calculate_dutching_stakes(selections, required_stake, 'LAY')
                
                dialog.calculated_results = results
                dialog.bet_type = bet_type
                dialog.has_mixed = has_mixed
                
                total_stake = sum(r['stake'] for r in results)
                total_var.set(f"Totale: {total_stake:.2f} EUR")
                
            except Exception as e:
                dialog.calculated_results = None
                total_var.set(f"Errore: {str(e)[:30]}")
            
            # Update all rows
            for item_id in runner_selections:
                update_row(item_id)
        
        tree.bind('<Button-1>', on_row_click)
        tree.bind('<Double-Button-1>', on_row_double_click)
        
        # ============ BOTTOM SECTION: Controls ============
        controls_frame = ttk.Frame(main_frame)
        controls_frame.pack(fill=tk.X, pady=10)
        
        # Left controls
        left_controls = ttk.Frame(controls_frame)
        left_controls.pack(side=tk.LEFT)
        
        live_odds_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(left_controls, text="Quote Live", variable=live_odds_var).pack(side=tk.LEFT, padx=5)
        
        ttk.Label(left_controls, text="Offset Globale:").pack(side=tk.LEFT, padx=(20, 5))
        global_offset_var = tk.StringVar(value='0')
        global_offset_spin = ttk.Spinbox(left_controls, from_=-10, to=10, width=5, textvariable=global_offset_var)
        global_offset_spin.pack(side=tk.LEFT)
        
        def apply_global_offset():
            try:
                offset = int(global_offset_var.get())
                for sel_id in runner_selections:
                    runner_selections[sel_id]['offset'] = offset
                    update_row(sel_id)
                recalculate()
            except ValueError:
                pass
        
        global_offset_spin.bind('<Return>', lambda e: apply_global_offset())
        ttk.Button(left_controls, text="Applica", command=apply_global_offset).pack(side=tk.LEFT, padx=2)
        
        # Total display
        total_var = tk.StringVar(value="Totale: -")
        ttk.Label(left_controls, textvariable=total_var, font=('Arial', 10, 'bold')).pack(side=tk.LEFT, padx=20)
        
        # Right controls (buttons)
        right_controls = ttk.Frame(controls_frame)
        right_controls.pack(side=tk.RIGHT)
        
        def select_all():
            for sel_id in runner_selections:
                runner_selections[sel_id]['selected'] = True
                update_row(sel_id)
            recalculate()
        
        def select_none():
            for sel_id in runner_selections:
                runner_selections[sel_id]['selected'] = False
                update_row(sel_id)
            recalculate()
        
        def refresh_odds():
            """Refresh market odds."""
            if not self.client or not self.current_market:
                return
            try:
                market_id = self.current_market['marketId']
                prices = self.client.get_market_with_prices(market_id)
                if prices and 'runners' in prices:
                    for runner_price in prices['runners']:
                        sel_id = runner_price['selectionId']
                        if sel_id in runner_selections:
                            runner_selections[sel_id]['runner']['backPrice'] = runner_price.get('backPrice', 0)
                            runner_selections[sel_id]['runner']['layPrice'] = runner_price.get('layPrice', 0)
                            update_row(sel_id)
                    recalculate()
                    messagebox.showinfo("Aggiornato", "Quote aggiornate!")
            except Exception as e:
                messagebox.showerror("Errore", f"Impossibile aggiornare quote: {e}")
        
        ttk.Button(right_controls, text="Sel. Tutti", command=select_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(right_controls, text="Sel. Nessuno", command=select_none).pack(side=tk.LEFT, padx=2)
        ttk.Button(right_controls, text="Aggiorna Quote", command=refresh_odds).pack(side=tk.LEFT, padx=2)
        
        # ============ BOTTOM BUTTONS ============
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=10)
        
        def place_bets():
            if not dialog.calculated_results:
                messagebox.showwarning("Attenzione", "Calcola prima le scommesse")
                return
            
            if self.market_status == 'SUSPENDED':
                messagebox.showwarning("Mercato Sospeso", "Attendi che il mercato riapra.")
                return
            
            if self.market_status == 'CLOSED':
                messagebox.showwarning("Mercato Chiuso", "Il mercato e' chiuso.")
                return
            
            # Show confirmation
            total = sum(r['stake'] for r in dialog.calculated_results)
            msg = f"Piazzare {len(dialog.calculated_results)} scommesse per un totale di {total:.2f} EUR?"
            if not messagebox.askyesno("Conferma", msg):
                return
            
            # Validate all bets before placing
            # Betfair: min 1€ per selezione (dal febbraio 2022)
            MIN_STAKE = 1.0
            MAX_PAYOUT = 10000.0
            
            validation_errors = []
            for r in dialog.calculated_results:
                stake = r['stake']
                price = r['price']
                runner_name = r.get('runnerName', 'Selezione')
                
                # Check minimum stake
                if stake < MIN_STAKE:
                    validation_errors.append(f"{runner_name}: stake {stake:.2f}€ < min {MIN_STAKE:.0f}€")
                
                # Check max payout
                potential_payout = stake * (price - 1)
                if potential_payout > MAX_PAYOUT:
                    validation_errors.append(f"{runner_name}: payout {potential_payout:.0f}€ > max {MAX_PAYOUT}€")
            
            if validation_errors:
                error_msg = "Validazione fallita:\n" + "\n".join(validation_errors)
                error_msg += f"\n\nAumenta il Profit Target per avere stake >= {MIN_STAKE:.0f}€"
                messagebox.showwarning("Validazione", error_msg)
                return
            
            instructions = []
            for r in dialog.calculated_results:
                # Use effectiveType if available (mixed dutching), otherwise global bet_type
                side = r.get('effectiveType', dialog.bet_type)
                # Normalize price to valid Betfair tick
                from order_manager import normalize_price
                normalized_price = normalize_price(r['price'])
                instructions.append({
                    'selectionId': r['selectionId'],
                    'side': side,
                    'price': normalized_price,
                    'size': round(r['stake'], 2)
                })
            
            try:
                market_id = self.current_market['marketId']
                market_name = self.current_market.get('marketName', '')
                event_name = self.current_event.get('name', '') if self.current_event else ''
                
                if self.simulation_mode:
                    for instr in instructions:
                        self.bet_logger.log_order_placed(
                            market_id=market_id,
                            selection_id=str(instr['selectionId']),
                            side=instr['side'],
                            stake=instr['size'],
                            price=instr['price'],
                            market_name=market_name,
                            event_name=event_name,
                            source='SIMULATION'
                        )
                    messagebox.showinfo("Simulazione", f"[SIMULAZIONE] {len(instructions)} scommesse piazzate!")
                    dialog.destroy()
                    return
                
                result = self.client.place_bets(market_id, instructions)
                logging.info(f"[DUTCHING] Place bets result: {result}")
                
                if result.get('status') == 'SUCCESS':
                    instr_reports = result.get('instructionReports', [])
                    for i, instr in enumerate(instructions):
                        rep = instr_reports[i] if i < len(instr_reports) else {}
                        bet_id = rep.get('betId')
                        runner_name = dialog.calculated_results[i].get('runnerName', '') if i < len(dialog.calculated_results) else ''
                        
                        self.bet_logger.log_order_placed(
                            market_id=market_id,
                            selection_id=str(instr['selectionId']),
                            side=instr['side'],
                            stake=instr['size'],
                            price=instr['price'],
                            bet_id=bet_id,
                            market_name=market_name,
                            event_name=event_name,
                            runner_name=runner_name,
                            source='DUTCHING'
                        )
                        
                        if bet_id:
                            self.bet_logger.save_position(
                                bet_id=bet_id,
                                market_id=market_id,
                                selection_id=str(instr['selectionId']),
                                side=instr['side'],
                                stake=instr['size'],
                                price=instr['price'],
                                status='PLACED'
                            )
                    
                    messagebox.showinfo("Successo", f"{len(instructions)} scommesse piazzate!")
                    dialog.destroy()
                else:
                    error_code = result.get('errorCode', '')
                    error_msg = f"Stato: {result.get('status', 'UNKNOWN')}"
                    if error_code:
                        error_msg += f"\nErrore: {error_code}"
                    
                    instr_reports = result.get('instructionReports', [])
                    for i, rep in enumerate(instr_reports):
                        instr = instructions[i] if i < len(instructions) else {}
                        runner_name = dialog.calculated_results[i].get('runnerName', '') if i < len(dialog.calculated_results) else ''
                        
                        if rep.get('status') != 'SUCCESS':
                            rep_error = rep.get('errorCode', '')
                            if rep_error:
                                error_msg += f"\n- Selezione {i+1}: {rep_error}"
                            
                            self.bet_logger.log_order_failed(
                                market_id=market_id,
                                selection_id=str(instr.get('selectionId', '')),
                                side=instr.get('side', ''),
                                stake=instr.get('size', 0),
                                price=instr.get('price', 0),
                                error_code=rep_error or error_code,
                                error_message=rep.get('errorMessage', ''),
                                runner_name=runner_name,
                                source='DUTCHING'
                            )
                    
                    logging.warning(f"[DUTCHING] Bet placement failed: {error_msg}")
                    messagebox.showwarning("Attenzione", error_msg)
            except Exception as e:
                self.bet_logger.log_error(
                    source='DUTCHING',
                    message=f"Exception placing bets: {str(e)}",
                    exception=e,
                    market_id=self.current_market.get('marketId', '') if self.current_market else ''
                )
                messagebox.showerror("Errore", str(e))
        
        submit_btn = tk.Button(btn_frame, text="Piazza Scommesse", bg='#27ae60', fg='white', 
                              font=('Arial', 10, 'bold'), command=place_bets)
        submit_btn.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(btn_frame, text="Ricalcola", command=recalculate).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Chiudi", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)
        
        # Bind variable changes to recalculate
        stake_var.trace_add('write', lambda *args: recalculate())
        profit_var.trace_add('write', lambda *args: recalculate())
        dutching_mode.trace_add('write', lambda *args: recalculate())
        
        # Initial calculation
        dialog.after(100, recalculate)
    
    def _show_telegram_settings(self):
        """Show Telegram configuration dialog."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Configura Telegram")
        dialog.geometry("500x400")
        dialog.transient(self.root)
        dialog.grab_set()
        
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text="Configurazione Telegram", style='Title.TLabel').pack(anchor=tk.W, pady=(0, 20))
        
        ttk.Label(frame, text="Per ottenere API ID e Hash vai su my.telegram.org").pack(anchor=tk.W)
        
        settings = self.db.get_telegram_settings() or {}
        
        ttk.Label(frame, text="API ID:").pack(anchor=tk.W, pady=(10, 0))
        api_id_var = tk.StringVar(value=settings.get('api_id', ''))
        ttk.Entry(frame, textvariable=api_id_var, width=40).pack(anchor=tk.W)
        
        ttk.Label(frame, text="API Hash:").pack(anchor=tk.W, pady=(10, 0))
        api_hash_var = tk.StringVar(value=settings.get('api_hash', ''))
        ttk.Entry(frame, textvariable=api_hash_var, width=40).pack(anchor=tk.W)
        
        ttk.Label(frame, text="Numero di Telefono (con prefisso +39):").pack(anchor=tk.W, pady=(10, 0))
        phone_var = tk.StringVar(value=settings.get('phone_number', ''))
        ttk.Entry(frame, textvariable=phone_var, width=40).pack(anchor=tk.W)
        
        ttk.Label(frame, text="Stake Automatico (EUR):").pack(anchor=tk.W, pady=(10, 0))
        auto_stake_var = tk.StringVar(value=str(settings.get('auto_stake', '1.0')))
        ttk.Entry(frame, textvariable=auto_stake_var, width=15).pack(anchor=tk.W)
        
        auto_bet_var = tk.BooleanVar(value=bool(settings.get('auto_bet', 0)))
        ttk.Checkbutton(frame, text="Piazza scommesse automaticamente", variable=auto_bet_var).pack(anchor=tk.W, pady=(10, 0))
        
        confirm_var = tk.BooleanVar(value=bool(settings.get('require_confirmation', 1)))
        ttk.Checkbutton(frame, text="Richiedi conferma prima di scommettere", variable=confirm_var).pack(anchor=tk.W)
        
        status_label = ttk.Label(frame, text=f"Stato: {self.telegram_status}")
        status_label.pack(anchor=tk.W, pady=10)
        
        def save_settings():
            try:
                stake = float(auto_stake_var.get().replace(',', '.'))
            except:
                stake = 1.0
            self.db.save_telegram_settings(
                api_id=api_id_var.get(),
                api_hash=api_hash_var.get(),
                session_string=settings.get('session_string'),
                phone_number=phone_var.get(),
                enabled=True,
                auto_bet=auto_bet_var.get(),
                require_confirmation=confirm_var.get(),
                auto_stake=stake
            )
            messagebox.showinfo("Salvato", "Impostazioni Telegram salvate")
            dialog.destroy()
        
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=20)
        ttk.Button(btn_frame, text="Salva", command=save_settings).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Chiudi", command=dialog.destroy).pack(side=tk.LEFT)
    
    def _show_telegram_signals(self):
        """Show received Telegram signals."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Segnali Telegram Ricevuti")
        dialog.geometry("700x500")
        dialog.transient(self.root)
        
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text="Segnali Ricevuti", style='Title.TLabel').pack(anchor=tk.W)
        
        columns = ('data', 'selezione', 'tipo', 'quota', 'stake', 'stato')
        tree = ttk.Treeview(frame, columns=columns, show='headings', height=15)
        tree.heading('data', text='Data')
        tree.heading('selezione', text='Selezione')
        tree.heading('tipo', text='Tipo')
        tree.heading('quota', text='Quota')
        tree.heading('stake', text='Stake')
        tree.heading('stato', text='Stato')
        tree.column('data', width=120)
        tree.column('selezione', width=100)
        tree.column('tipo', width=60)
        tree.column('quota', width=60)
        tree.column('stake', width=60)
        tree.column('stato', width=80)
        
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=10)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=10)
        
        signals = self.db.get_recent_signals(50)
        for sig in signals:
            tree.insert('', tk.END, iid=str(sig['id']), values=(
                sig.get('received_at', '')[:16] if sig.get('received_at') else '',
                sig.get('parsed_selection', ''),
                sig.get('parsed_side', ''),
                f"{sig.get('parsed_odds', 0):.2f}" if sig.get('parsed_odds') else '',
                f"{sig.get('parsed_stake', 0):.2f}" if sig.get('parsed_stake') else '',
                sig.get('status', '')
            ))
        
        def process_selected():
            selected = tree.selection()
            if not selected:
                return
            if not self.client:
                messagebox.showwarning("Attenzione", "Connettiti prima a Betfair")
                return
            messagebox.showinfo("Info", "Funzionalita in sviluppo: cerca mercato e piazza scommessa")
        
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=10)
        ttk.Button(btn_frame, text="Processa Selezionato", command=process_selected).pack(side=tk.LEFT)
    
    def _start_telegram_listener(self):
        """Start the Telegram listener."""
        settings = self.db.get_telegram_settings()
        if not settings or not settings.get('api_id') or not settings.get('api_hash'):
            messagebox.showwarning("Attenzione", "Configura prima le credenziali Telegram")
            return
        
        try:
            import os
            session_path = os.path.join(os.environ.get('APPDATA', '.'), 'Pickfair', 'telegram_session')
            self.telegram_listener = TelegramListener(
                api_id=int(settings['api_id']),
                api_hash=settings['api_hash'],
                session_path=session_path
            )
            self.telegram_listener.set_database(self.db)
            
            chats = self.db.get_telegram_chats()
            chat_ids = [int(c['chat_id']) for c in chats if c.get('enabled')]
            
            # In FOLLOWER mode, ensure copy_chat_id is monitored for receiving COPY BET signals
            copy_mode = settings.get('copy_mode', 'OFF')
            copy_chat_id = settings.get('copy_chat_id', '')
            if copy_mode == 'FOLLOWER' and copy_chat_id:
                try:
                    follower_chat = int(copy_chat_id)
                    if follower_chat not in chat_ids:
                        chat_ids.append(follower_chat)
                        logging.info(f"[COPY] FOLLOWER mode: added copy_chat_id {follower_chat} to monitored chats")
                except ValueError:
                    logging.warning(f"[COPY] Invalid copy_chat_id: {copy_chat_id}")
            
            self.telegram_listener.set_monitored_chats(chat_ids)
            
            def on_signal(signal):
                self.telegram_signal_queue.add(signal)
                self.db.save_telegram_signal(
                    signal.get('chat_id', ''),
                    signal.get('sender_id', ''),
                    signal.get('raw_text', ''),
                    signal
                )
                self.root.after(0, lambda: self._notify_new_signal(signal))
                
                # Handle COPY DUTCHING signals (unified dutching from Master)
                if signal.get('is_copy_dutching') and signal.get('event') and signal.get('selections'):
                    self.root.after(100, lambda: self._process_telegram_copy_dutching(signal, settings))
                # Handle booking signals
                elif signal.get('is_booking') and signal.get('event'):
                    self.root.after(100, lambda: self._process_telegram_booking(signal, settings))
                elif settings.get('auto_bet') and signal.get('event') and signal.get('market_type'):
                    self.root.after(100, lambda: self._process_telegram_auto_bet(signal, settings))
            
            def on_status(status, message):
                self.telegram_status = status
                if status == 'AUTH_REQUIRED':
                    self.root.after(0, lambda: self.tg_status_label.configure(text="Stato: AUTH_REQUIRED"))
                elif status == 'CONNECTED':
                    self.root.after(0, lambda: self.tg_status_label.configure(text="Stato: CONNECTED"))
            
            self.telegram_listener.set_callbacks(on_signal=on_signal, on_status=on_status)
            self.telegram_listener.start()
            
            self.telegram_status = 'STARTING'
            messagebox.showinfo("Telegram", "Listener Telegram avviato")
            
        except Exception as e:
            messagebox.showerror("Errore", f"Errore avvio Telegram: {e}")
    
    def _stop_telegram_listener(self):
        """Stop the Telegram listener."""
        if self.telegram_listener:
            self.telegram_listener.stop()
            self.telegram_listener = None
        self.telegram_status = 'STOPPED'
        messagebox.showinfo("Telegram", "Listener Telegram fermato")
    
    def _auto_start_telegram_listener(self):
        """Auto-start Telegram listener when credentials are configured."""
        try:
            settings = self.db.get_telegram_settings()
            if not settings:
                return
            
            # Check if credentials are configured
            if not settings.get('api_id') or not settings.get('api_hash'):
                return
            
            # Check if there's a valid session (means user already authenticated once)
            import os
            session_path = os.path.join(os.environ.get('APPDATA', '.'), 'Pickfair', 'telegram_session.session')
            if not os.path.exists(session_path):
                return
            
            # Auto-start if:
            # 1. Copy Trading is in MASTER or FOLLOWER mode, OR
            # 2. There are chats to monitor
            copy_mode = settings.get('copy_mode', 'OFF')
            chats = self.db.get_telegram_chats()
            
            should_start = copy_mode in ('MASTER', 'FOLLOWER') or (chats and len([c for c in chats if c.get('enabled')]) > 0)
            
            if not should_start:
                return
            
            # Start the listener silently
            logging.info(f"Auto-starting Telegram listener (copy_mode={copy_mode}, chats={len(chats) if chats else 0})")
            self._start_telegram_listener_silent()
            
        except Exception as e:
            logging.error(f"Auto-start Telegram listener failed: {e}")
    
    def _start_telegram_listener_silent(self):
        """Start Telegram listener without showing message boxes."""
        import os
        
        settings = self.db.get_telegram_settings()
        if not settings or not settings.get('api_id') or not settings.get('api_hash'):
            return
        
        if self.telegram_listener:
            return  # Already running
        
        try:
            session_path = os.path.join(os.environ.get('APPDATA', '.'), 'Pickfair', 'telegram_session')
            self.telegram_listener = TelegramListener(
                api_id=int(settings['api_id']),
                api_hash=settings['api_hash'],
                session_path=session_path
            )
            self.telegram_listener.set_database(self.db)
            
            chats = self.db.get_telegram_chats()
            chat_ids = [int(c['chat_id']) for c in chats if c.get('enabled')]
            
            # In FOLLOWER mode, ensure copy_chat_id is monitored for receiving COPY BET signals
            copy_mode = settings.get('copy_mode', 'OFF')
            copy_chat_id = settings.get('copy_chat_id', '')
            if copy_mode == 'FOLLOWER' and copy_chat_id:
                try:
                    follower_chat = int(copy_chat_id)
                    if follower_chat not in chat_ids:
                        chat_ids.append(follower_chat)
                        logging.info(f"[COPY] FOLLOWER mode: added copy_chat_id {follower_chat} to monitored chats")
                except ValueError:
                    logging.warning(f"[COPY] Invalid copy_chat_id: {copy_chat_id}")
            
            self.telegram_listener.set_monitored_chats(chat_ids)
            
            def on_signal(signal):
                self.telegram_signal_queue.add(signal)
                signal_id = self.db.save_telegram_signal(
                    signal.get('chat_id', ''),
                    signal.get('sender_id', ''),
                    signal.get('raw_text', ''),
                    signal
                )
                signal['signal_id'] = signal_id
                self.root.after(0, lambda: self._notify_new_signal(signal))
                self.root.after(0, lambda: self._refresh_telegram_signals_tree())
                
                logging.debug(f"[AUTO-BET DEBUG] Signal received: event={signal.get('event')}, market_type={signal.get('market_type')}, selection={signal.get('selection')}, auto_bet={settings.get('auto_bet')}")
                
                # Handle COPY DUTCHING signals (unified dutching from Master)
                if signal.get('is_copy_dutching') and signal.get('event') and signal.get('selections'):
                    logging.info(f"[COPY DUTCHING] Processing dutching for: {signal.get('event')} - {len(signal.get('selections', []))} selections")
                    self.root.after(100, lambda: self._process_telegram_copy_dutching(signal, settings))
                # Handle booking signals
                elif signal.get('is_booking') and signal.get('event'):
                    logging.debug(f"[BOOKING DEBUG] Processing booking for: {signal.get('event')} @ {signal.get('target_odds')}")
                    self.root.after(100, lambda: self._process_telegram_booking(signal, settings))
                elif settings.get('auto_bet') and signal.get('event') and signal.get('market_type'):
                    logging.debug(f"[AUTO-BET DEBUG] Processing auto-bet for: {signal.get('event')} - {signal.get('market_type')}")
                    self.root.after(100, lambda: self._process_telegram_auto_bet(signal, settings))
                else:
                    logging.debug(f"[AUTO-BET DEBUG] Skipped - auto_bet={settings.get('auto_bet')}, event={signal.get('event')}, market_type={signal.get('market_type')}")
            
            def on_status(status, message):
                self.telegram_status = status
                try:
                    if status == 'AUTH_REQUIRED':
                        self.root.after(0, lambda: self.tg_status_label.configure(text="Stato: AUTH_REQUIRED"))
                    elif status == 'CONNECTED':
                        self.root.after(0, lambda: self.tg_status_label.configure(text="Stato: CONNECTED (Auto)"))
                except:
                    pass
            
            self.telegram_listener.set_callbacks(on_signal=on_signal, on_status=on_status)
            self.telegram_listener.start()
            
            self.telegram_status = 'STARTING'
            print("[DEBUG] Telegram listener auto-started successfully")
            
        except Exception as e:
            print(f"[DEBUG] Error auto-starting Telegram: {e}")
    
    def _reset_telegram_session(self):
        """Reset Telegram session to start fresh authentication."""
        import os
        session_path = os.path.join(os.environ.get('APPDATA', '.'), 'Pickfair', 'telegram_session')
        
        files_to_delete = [
            session_path + '.session',
            session_path + '.session-journal',
        ]
        
        deleted = False
        for f in files_to_delete:
            if os.path.exists(f):
                try:
                    os.remove(f)
                    deleted = True
                except:
                    pass
        
        self.tg_phone_code_hash = None
        self.telegram_status = 'AUTH_REQUIRED'
        self.tg_status_label.configure(text="Stato: AUTH_REQUIRED")
        
        if deleted:
            messagebox.showinfo("Reset", "Sessione Telegram eliminata. Ora clicca 'Invia Codice'.")
        else:
            messagebox.showinfo("Reset", "Nessuna sessione da eliminare. Clicca 'Invia Codice'.")
    
    def _send_telegram_code(self):
        """Send authentication code to Telegram."""
        settings = self.db.get_telegram_settings()
        if not settings or not settings.get('api_id') or not settings.get('api_hash'):
            messagebox.showwarning("Attenzione", "Configura prima API ID e Hash")
            return
        
        phone = self.settings_tg_phone_var.get().strip()
        if not phone:
            messagebox.showwarning("Attenzione", "Inserisci il numero di telefono nelle Impostazioni")
            return
        
        if not phone.startswith('+'):
            phone = '+' + phone
            self.settings_tg_phone_var.set(phone)
        
        self.tg_status_label.configure(text="Stato: Invio codice...")
        
        def send_thread():
            try:
                import asyncio
                import os
                from telethon import TelegramClient
                
                async def do_send():
                    api_id = int(settings['api_id'])
                    api_hash = settings['api_hash'].strip()
                    session_path = os.path.join(os.environ.get('APPDATA', '.'), 'Pickfair', 'telegram_session')
                    
                    client = TelegramClient(session_path, api_id, api_hash)
                    await client.connect()
                    
                    if await client.is_user_authorized():
                        await client.disconnect()
                        return "ALREADY_AUTH"
                    
                    result = await client.send_code_request(phone)
                    self.tg_phone_code_hash = result.phone_code_hash
                    await client.disconnect()
                    
                    return "SENT"
                
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(do_send())
                loop.close()
                
                if result == "ALREADY_AUTH":
                    self.root.after(0, lambda: self.tg_status_label.configure(text="Stato: Gia autenticato!"))
                    self.root.after(0, lambda: messagebox.showinfo("Telegram", "Sei gia autenticato! Clicca 'Carica/Aggiorna Chat'."))
                else:
                    self.root.after(0, lambda: self.tg_status_label.configure(text="Stato: Codice inviato! Inserisci e clicca Verifica"))
                    self.root.after(0, lambda: messagebox.showinfo("Telegram", "Codice inviato! Controlla Telegram e inseriscilo nel campo 'Codice'."))
            except Exception as e:
                err = str(e)
                self.root.after(0, lambda: self.tg_status_label.configure(text=f"Stato: Errore: {err[:50]}"))
                self.root.after(0, lambda: messagebox.showerror("Errore Telegram", f"Errore: {err}"))
        
        threading.Thread(target=send_thread, daemon=True).start()
    
    def _verify_telegram_code(self):
        """Verify the Telegram authentication code."""
        settings = self.db.get_telegram_settings()
        if not settings:
            return
        
        code = self.tg_code_var.get().strip()
        if not code:
            messagebox.showwarning("Attenzione", "Inserisci il codice ricevuto")
            return
        
        phone = self.settings_tg_phone_var.get().strip()
        password = self.tg_2fa_var.get().strip()
        phone_hash = getattr(self, 'tg_phone_code_hash', None)
        
        if not phone_hash:
            messagebox.showwarning("Attenzione", "Prima clicca 'Invia Codice'")
            return
        
        self.tg_status_label.configure(text="Stato: Verifica in corso...")
        
        def verify_thread():
            try:
                import asyncio
                import os
                from telethon import TelegramClient
                from telethon.errors import SessionPasswordNeededError
                
                async def do_verify():
                    api_id = int(settings['api_id'])
                    api_hash = settings['api_hash'].strip()
                    session_path = os.path.join(os.environ.get('APPDATA', '.'), 'Pickfair', 'telegram_session')
                    
                    client = TelegramClient(session_path, api_id, api_hash)
                    await client.connect()
                    
                    try:
                        await client.sign_in(phone, code, phone_code_hash=phone_hash)
                    except SessionPasswordNeededError:
                        if password:
                            await client.sign_in(password=password)
                        else:
                            await client.disconnect()
                            return "2FA"
                    
                    if await client.is_user_authorized():
                        await client.disconnect()
                        return "OK"
                    else:
                        await client.disconnect()
                        return "FAIL"
                
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(do_verify())
                loop.close()
                
                if result == "OK":
                    self.root.after(0, lambda: self.tg_status_label.configure(text="Stato: AUTHENTICATED"))
                    self.root.after(0, lambda: messagebox.showinfo("Telegram", "Autenticazione completata!"))
                elif result == "2FA":
                    self.root.after(0, lambda: self.tg_status_label.configure(text="Stato: Richiesta password 2FA"))
                else:
                    self.root.after(0, lambda: self.tg_status_label.configure(text="Stato: Autenticazione fallita"))
            except Exception as e:
                err = str(e)
                self.root.after(0, lambda: self.tg_status_label.configure(text=f"Stato: Errore: {err[:50]}"))
        
        threading.Thread(target=verify_thread, daemon=True).start()
    
    def _notify_new_signal(self, signal):
        """Notify user of new betting signal."""
        settings = self.db.get_telegram_settings() or {}
        
        event = signal.get('event', 'N/A')
        over_line = signal.get('over_line')
        score = f"{signal.get('score_home', '?')}-{signal.get('score_away', '?')}"
        
        msg = f"Nuovo segnale ricevuto:\n"
        msg += f"Evento: {event}\n"
        msg += f"Risultato: {score}\n"
        if over_line is not None:
            msg += f"Scommessa: BACK Over {over_line}\n"
        
        if settings.get('require_confirmation') or not settings.get('auto_bet'):
            messagebox.showinfo("Segnale Telegram", msg)
    
    def _process_telegram_booking(self, signal, settings):
        """Process a booking signal from Telegram to create a bet reservation."""
        if not self.client:
            messagebox.showwarning("Prenotazione", "Non connesso a Betfair")
            return
        
        event_name = signal.get('event', '')
        market_type = signal.get('market_type', 'OVER_UNDER')
        selection = signal.get('selection', '')
        side = signal.get('side', 'BACK')
        target_odds = signal.get('target_odds')
        over_line = signal.get('over_line')
        
        if not event_name or not target_odds:
            return
        
        # Get stake from settings
        stake_type = settings.get('stake_type', 'fixed')
        if stake_type == 'percent_bankroll':
            sim_settings = self.db.get_simulation_settings()
            if self.simulation_mode and sim_settings:
                bankroll = sim_settings.get('virtual_balance', 1000)
            elif self.client:
                try:
                    balance_info = self.client.get_account_balance()
                    bankroll = balance_info.get('available', 0)
                except:
                    bankroll = 0
            else:
                bankroll = 0
            percent = float(settings.get('stake_percent', 1.0))
            stake = round(bankroll * percent / 100, 2)
            stake = max(1.0, stake)
        else:
            stake = float(settings.get('auto_stake', 1.0))
        
        try:
            # Find the event on Betfair
            all_events = self.client.get_football_events(include_inplay=True)
            event_lower = event_name.lower().replace(' v ', ' ').replace(' vs ', ' ').replace(' - ', ' ')
            
            matched_event = None
            for event in all_events:
                event_search = event['name'].lower().replace(' v ', ' ').replace(' vs ', ' ').replace(' - ', ' ')
                words_signal = set(event_lower.split())
                words_event = set(event_search.split())
                common = words_signal & words_event
                if len(common) >= 2:
                    matched_event = event
                    break
            
            if not matched_event:
                messagebox.showwarning("Prenotazione", f"Evento non trovato: {event_name}")
                return
            
            # Get market for this event
            # Map market types to correct Betfair market codes
            bf_market_type = market_type  # Default to same
            
            if market_type == 'OVER_UNDER' and over_line:
                # Map Over/Under lines to correct Betfair market codes
                line_map = {
                    0.5: 'OVER_UNDER_05',
                    1.5: 'OVER_UNDER_15',
                    2.5: 'OVER_UNDER_25',
                    3.5: 'OVER_UNDER_35',
                    4.5: 'OVER_UNDER_45',
                    5.5: 'OVER_UNDER_55',
                    6.5: 'OVER_UNDER_65',
                    7.5: 'OVER_UNDER_75',
                }
                bf_market_type = line_map.get(over_line, 'OVER_UNDER_25')
            elif market_type == 'OVER_UNDER_FH' and over_line:
                # First Half Over/Under
                fh_line_map = {
                    0.5: 'OVER_UNDER_05_FH',
                    1.5: 'OVER_UNDER_15_FH',
                    2.5: 'OVER_UNDER_25_FH',
                }
                bf_market_type = fh_line_map.get(over_line, 'OVER_UNDER_05_FH')
            else:
                # All other markets
                market_type_map = {
                    'OVER_UNDER': 'OVER_UNDER_25',
                    'MATCH_ODDS': 'MATCH_ODDS',
                    'BOTH_TEAMS_TO_SCORE': 'BOTH_TEAMS_TO_SCORE',
                    'CORRECT_SCORE': 'CORRECT_SCORE',
                    'DOUBLE_CHANCE': 'DOUBLE_CHANCE',
                    'DRAW_NO_BET': 'DRAW_NO_BET',
                    'ASIAN_HANDICAP': 'ASIAN_HANDICAP',
                    'HALF_TIME': 'HALF_TIME',
                    'HALF_TIME_SCORE': 'HALF_TIME_SCORE',
                    'HALF_TIME_FULL_TIME': 'HALF_TIME_FULL_TIME',
                }
                bf_market_type = market_type_map.get(market_type, market_type)
            
            markets = self.client.list_market_catalogue(
                event_ids=[matched_event['id']],
                market_type_codes=[bf_market_type]
            )
            
            if not markets:
                messagebox.showwarning("Prenotazione", f"Mercato {market_type} non trovato per {matched_event['name']}")
                return
            
            target_market = markets[0]
            market_id = target_market['marketId']
            market_name = target_market.get('marketName', market_type)
            
            # Get runners
            market_with_prices = self.client.get_market_with_prices(market_id)
            runners = market_with_prices.get('runners', [])
            
            # Find matching runner
            target_runner = None
            selection_lower = selection.lower()
            selection_upper = selection.upper()
            
            for runner in runners:
                runner_name = runner.get('runnerName', '').lower()
                runner_name_upper = runner.get('runnerName', '').upper()
                
                # Direct match
                if selection_lower in runner_name or runner_name in selection_lower:
                    target_runner = runner
                    break
                
                # Correct Score - match exact score format
                if market_type == 'CORRECT_SCORE':
                    # Normalize score format: "2 - 1" matches "2-1" or "2 - 1"
                    sel_normalized = selection.replace(' ', '').replace(':', '-')
                    runner_normalized = runner.get('runnerName', '').replace(' ', '').replace(':', '-')
                    if sel_normalized == runner_normalized:
                        target_runner = runner
                        break
                
                # Over/Under
                if 'over' in selection_lower and 'over' in runner_name:
                    target_runner = runner
                    break
                if 'under' in selection_lower and 'under' in runner_name:
                    target_runner = runner
                    break
                
                # 1X2 / Match Odds
                if selection_lower in ['home', '1'] and runner.get('sortPriority', 0) == 1:
                    target_runner = runner
                    break
                if selection_lower in ['draw', 'x'] and runner.get('sortPriority', 0) == 2:
                    target_runner = runner
                    break
                if selection_lower in ['away', '2'] and runner.get('sortPriority', 0) == 3:
                    target_runner = runner
                    break
                
                # BTTS (Both Teams To Score)
                if market_type == 'BOTH_TEAMS_TO_SCORE':
                    if selection_lower == 'yes' and ('yes' in runner_name or 'si' in runner_name):
                        target_runner = runner
                        break
                    if selection_lower == 'no' and 'no' in runner_name:
                        target_runner = runner
                        break
                
                # Double Chance
                if market_type == 'DOUBLE_CHANCE':
                    if selection_upper == '1X' and ('1x' in runner_name or 'home or draw' in runner_name):
                        target_runner = runner
                        break
                    if selection_upper == 'X2' and ('x2' in runner_name or 'draw or away' in runner_name):
                        target_runner = runner
                        break
                    if selection_upper == '12' and ('12' in runner_name or 'home or away' in runner_name):
                        target_runner = runner
                        break
                
                # Half Time / Full Time
                if market_type == 'HALF_TIME_FULL_TIME':
                    if selection_upper in runner_name_upper:
                        target_runner = runner
                        break
            
            if not target_runner:
                messagebox.showwarning("Prenotazione", f"Selezione '{selection}' non trovata nel mercato")
                return
            
            # Get current price
            current_price = target_runner.get('backPrice') if side == 'BACK' else target_runner.get('layPrice')
            current_price = current_price or 0
            
            # Save booking
            booking_id = self.db.save_booking(
                event_name=matched_event['name'],
                market_id=market_id,
                market_name=market_name,
                selection_id=target_runner['selectionId'],
                runner_name=target_runner['runnerName'],
                side=side,
                target_price=target_odds,
                stake=stake,
                current_price=current_price
            )
            
            messagebox.showinfo("Prenotazione", 
                f"Prenotazione creata!\n\n"
                f"Evento: {matched_event['name']}\n"
                f"Selezione: {target_runner['runnerName']}\n"
                f"Tipo: {side}\n"
                f"Quota target: {target_odds}\n"
                f"Quota attuale: {current_price:.2f}\n"
                f"Stake: {stake:.2f}€")
        
        except Exception as e:
            messagebox.showerror("Errore Prenotazione", f"Errore: {str(e)}")
    
    def _process_telegram_copy_dutching(self, signal, settings):
        """Process COPY DUTCHING signal from Master - place dutching bets with profit target.
        
        Format received:
        {
            'event': 'Roma v Lazio',
            'market_type': 'Correct Score',
            'selections': [{'selection': '2-1', 'odds': 9.50}, {'selection': '2-0', 'odds': 11.00}, ...],
            'side': 'BACK' or 'LAY' or 'MIXED',
            'profit_target': 20.00,
            'total_stake': 7.94,
            'is_copy_dutching': True
        }
        """
        from dutching import calculate_dutching_stakes, calculate_back_target_profit
        
        logging.info(f"[COPY DUTCHING] Processing: {signal.get('event')} | {len(signal.get('selections', []))} selections")
        
        if not self.client:
            logging.warning("[COPY DUTCHING] Not connected to Betfair")
            return
        
        event_name = signal.get('event', '')
        market_type_str = signal.get('market_type', '')
        selections = signal.get('selections', [])
        side = signal.get('side', 'BACK')
        profit_target = signal.get('profit_target')
        total_stake_master = signal.get('total_stake')
        
        if not event_name or not selections:
            logging.warning("[COPY DUTCHING] Missing event or selections")
            return
        
        try:
            # Find event on Betfair
            live_events = self.client.get_live_events('1')
            all_events = self.client.get_football_events(include_inplay=True)
            
            event_lower = event_name.lower().replace(' v ', ' ').replace(' vs ', ' ')
            
            def find_best_match(events_list):
                best_match = None
                best_score = 0
                for event in events_list:
                    event_search = event['name'].lower().replace(' v ', ' ').replace(' vs ', ' ')
                    words_signal = set(event_lower.split())
                    words_event = set(event_search.split())
                    common = words_signal & words_event
                    match_score = len(common)
                    if match_score > best_score and match_score >= 2:
                        best_score = match_score
                        best_match = event
                return best_match
            
            matched_event = find_best_match(live_events) or find_best_match(all_events)
            
            if not matched_event:
                logging.warning(f"[COPY DUTCHING] Event not found: {event_name}")
                return
            
            logging.info(f"[COPY DUTCHING] Matched event: {matched_event['name']}")
            
            # Determine market type from string
            market_type_mapping = {
                'Correct Score': 'CORRECT_SCORE',
                'Risultato Esatto': 'CORRECT_SCORE',
                'Match Odds': 'MATCH_ODDS',
                'Over/Under': 'OVER_UNDER',
            }
            market_type_key = market_type_mapping.get(market_type_str, 'CORRECT_SCORE')
            
            # Get market
            markets = self.client.get_markets_for_event(matched_event['id'], market_type_key)
            if not markets:
                logging.warning(f"[COPY DUTCHING] Market not found for {market_type_str}")
                return
            
            target_market = markets[0]
            market_id = target_market['marketId']
            
            # Get runners with prices
            runners = self.client.get_runners_for_market(market_id)
            if not runners:
                logging.warning(f"[COPY DUTCHING] No runners found for market {market_id}")
                return
            
            # Match selections to runners
            matched_selections = []
            for sel in selections:
                sel_name = sel['selection']
                sel_odds = sel['odds']
                
                # Find runner by name
                target_runner = None
                for runner in runners:
                    runner_name = runner.get('runnerName', '')
                    if runner_name.lower() == sel_name.lower() or sel_name.lower() in runner_name.lower():
                        target_runner = runner
                        break
                
                if target_runner:
                    current_price = target_runner.get('backPrice') if side in ('BACK', 'MIXED') else target_runner.get('layPrice')
                    matched_selections.append({
                        'selectionId': target_runner['selectionId'],
                        'runnerName': target_runner['runnerName'],
                        'price': current_price or sel_odds,  # Use current price or fallback to signal odds
                        'effectiveType': sel.get('effectiveType', side)
                    })
                else:
                    logging.warning(f"[COPY DUTCHING] Selection not found: {sel_name}")
            
            if len(matched_selections) < len(selections):
                logging.warning(f"[COPY DUTCHING] Only matched {len(matched_selections)}/{len(selections)} selections")
            
            if not matched_selections:
                logging.error("[COPY DUTCHING] No selections matched")
                return
            
            # Calculate stakes using dutching with profit target
            commission = 4.5  # Betfair Italia
            
            if profit_target:
                # Use profit target to calculate stakes
                if side == 'BACK':
                    results, profit, _ = calculate_back_target_profit(matched_selections, profit_target, commission)
                else:
                    # For LAY/MIXED, use total_stake from Master as approximation
                    results, profit, _ = calculate_dutching_stakes(matched_selections, total_stake_master or 10, side)
            else:
                # Fallback: use total stake
                results, profit, _ = calculate_dutching_stakes(matched_selections, total_stake_master or 10, side)
            
            total_stake = sum(r['stake'] for r in results)
            logging.info(f"[COPY DUTCHING] Calculated: total_stake={total_stake:.2f}€, profit={profit:.2f}€")
            
            for r in results:
                logging.info(f"[COPY DUTCHING]   {r['runnerName']} @ {r['price']:.2f} -> stake={r['stake']:.2f}€")
            
            # Place bets
            if self.simulation_mode:
                # Simulation mode
                sim_settings = self.db.get_simulation_settings()
                virtual_balance = sim_settings.get('virtual_balance', 0) if sim_settings else 0
                
                if total_stake > virtual_balance:
                    logging.warning(f"[COPY DUTCHING] Insufficient virtual balance: {virtual_balance:.2f}€ < {total_stake:.2f}€")
                    return
                
                new_balance = virtual_balance - total_stake
                self.db.update_virtual_balance(new_balance)
                
                # Save simulated bet
                selections_info = [{'runnerName': r['runnerName'], 'stake': r['stake'], 'price': r['price']} for r in results]
                self.db.save_simulated_bet(
                    event_name=matched_event['name'],
                    market_id=market_id,
                    market_name=target_market.get('marketName', market_type_str),
                    side=side,
                    selections=selections_info,
                    total_stake=total_stake,
                    potential_profit=profit
                )
                
                logging.info(f"[COPY DUTCHING] Simulation bet placed: {len(results)} selections, stake={total_stake:.2f}€")
            else:
                # Real bets
                instructions = []
                for r in results:
                    bet_side = r.get('effectiveType', side)
                    instruction = {
                        'selectionId': r['selectionId'],
                        'handicap': 0,
                        'side': bet_side,
                        'orderType': 'LIMIT',
                        'limitOrder': {
                            'size': round(r['stake'], 2),
                            'price': r['price'],
                            'persistenceType': 'LAPSE'
                        }
                    }
                    instructions.append(instruction)
                
                result = self.client.place_orders(market_id, instructions)
                
                if result.get('status') == 'SUCCESS':
                    logging.info(f"[COPY DUTCHING] Bets placed successfully: {len(instructions)} orders")
                else:
                    logging.error(f"[COPY DUTCHING] Bet placement failed: {result}")
        
        except Exception as e:
            logging.error(f"[COPY DUTCHING] Error: {e}")
            import traceback
            logging.error(traceback.format_exc())
    
    def _process_telegram_auto_bet(self, signal, settings):
        """Process automatic bet from Telegram signal."""
        if not self.client:
            messagebox.showwarning("Auto-Bet", "Non connesso a Betfair")
            return
        
        event_name = signal.get('event', '')
        league = signal.get('league', '')
        market_type = signal.get('market_type', 'OVER_UNDER')
        selection = signal.get('selection', '')
        over_line = signal.get('over_line')
        
        # Calculate stake based on stake_type
        # Check if this is a COPY BET and reply_100_master is enabled
        is_copy_bet = signal.get('is_copy_bet', False)
        reply_100_master = settings.get('reply_100_master', False)
        copy_mode = settings.get('copy_mode', 'OFF')
        
        if is_copy_bet and reply_100_master and copy_mode == 'FOLLOWER':
            # Use Master's exact stake amount in EUR
            master_stake_amount = signal.get('stake_amount')
            if master_stake_amount:
                stake = max(1.0, master_stake_amount)  # Minimum 1€
                logging.info(f"[FOLLOWER] Processing COPY BET: using Master stake {master_stake_amount}€")
            else:
                # Fallback to follower's settings if stake_amount not available
                stake = float(settings.get('auto_stake', 1.0))
                logging.info(f"[FOLLOWER] Processing COPY BET: fallback stake {stake}€ (master stake not available)")
        else:
            stake_type = settings.get('stake_type', 'fixed')
            if stake_type == 'percent_bankroll':
                # Get bankroll from simulation or real balance
                sim_settings = self.db.get_simulation_settings()
                if self.simulation_mode and sim_settings:
                    bankroll = sim_settings.get('virtual_balance', 1000)
                elif self.client:
                    try:
                        balance_info = self.client.get_account_balance()
                        bankroll = balance_info.get('available', 0)
                    except:
                        bankroll = 0
                else:
                    bankroll = 0
                
                percent = float(settings.get('stake_percent', 1.0))
                stake = round(bankroll * percent / 100, 2)
                stake = max(1.0, stake)  # Minimum 1€
            else:
                # Fixed stake
                stake = float(settings.get('auto_stake', 1.0))
        
        signal_id = signal.get('signal_id')
        bet_side = signal.get('bet_side', signal.get('side', 'BACK'))
        live_only = signal.get('live_only', False)
        event_filter = signal.get('event_filter')  # 'LIVE', 'PRE_MATCH', or None
        
        if not event_name:
            return
        
        if market_type == 'OVER_UNDER' and over_line is None:
            return
        
        def log_failed_bet(reason):
            self.db.save_telegram_signal(
                signal.get('chat_id', ''),
                signal.get('sender_id', ''),
                signal.get('raw_text', ''),
                {**signal, 'auto_bet_status': 'FAILED', 'auto_bet_reason': reason}
            )
        
        def update_status(status):
            if signal_id:
                self.db.update_signal_status(signal_id, status)
                self._refresh_telegram_signals_tree()
        
        def get_runner_price(runner, side):
            """Get back or lay price based on bet side."""
            if side == 'LAY':
                return runner.get('layPrice')
            return runner.get('backPrice')
        
        try:
            live_events = self.client.get_live_events('1')
            all_events = self.client.get_football_events(include_inplay=True)
            
            event_lower = event_name.lower().replace(' v ', ' ').replace(' vs ', ' ')
            league_lower = league.lower() if league else ''
            
            league_country = ''
            if league_lower:
                for country in ['greek', 'italian', 'spanish', 'english', 'german', 'french', 
                               'portuguese', 'dutch', 'belgian', 'turkish', 'russian', 'polish',
                               'grecia', 'italia', 'spagna', 'inghilterra', 'germania', 'francia']:
                    if country in league_lower:
                        league_country = country
                        break
            
            def find_best_match(events_list):
                best_match = None
                best_score = 0
                for event in events_list:
                    event_search = event['name'].lower().replace(' v ', ' ').replace(' vs ', ' ')
                    competition = event.get('competition', {}).get('name', '').lower()
                    
                    words_signal = set(event_lower.split())
                    words_event = set(event_search.split())
                    common = words_signal & words_event
                    match_score = len(common)
                    
                    if league_country and league_country in competition:
                        match_score += 1
                    
                    if match_score > best_score and match_score >= 2:
                        best_score = match_score
                        best_match = event
                return best_match
            
            # Search based on event_filter for targeted lookup
            if event_filter == 'PRE_MATCH':
                # Only search pre-match events (exclude live)
                prematch_events = [e for e in all_events if e.get('id') not in [le.get('id') for le in live_events]]
                matched_event = find_best_match(prematch_events)
            elif event_filter == 'LIVE' or live_only:
                # Only search live events
                matched_event = find_best_match(live_events)
            else:
                # Default: search live first, then all events
                matched_event = find_best_match(live_events)
                if not matched_event:
                    matched_event = find_best_match(all_events)
            
            if not matched_event:
                reason = f"Evento non trovato: {event_name}"
                if league:
                    reason += f" ({league})"
                log_failed_bet(reason)
                update_status('FAILED')
                messagebox.showwarning("Auto-Bet", reason)
                return
            
            markets = self.client.get_markets(matched_event['id'])
            
            target_market = None
            target_runner = None
            
            if market_type == 'OVER_UNDER':
                target_line = over_line
                for market in markets:
                    market_name = market.get('marketName', '').lower()
                    if 'over' in market_name and 'under' in market_name:
                        line_str = str(target_line).replace('.', '')
                        if line_str in market_name.replace('.', '').replace(',', ''):
                            target_market = market
                            break
                
                if target_market:
                    market_book = self.client.get_market_with_prices(target_market['marketId'])
                    for runner in market_book.get('runners', []):
                        runner_name = runner.get('runnerName', '').lower()
                        if 'over' in runner_name:
                            price = get_runner_price(runner, bet_side)
                            if price:
                                target_runner = {
                                    'selectionId': runner['selectionId'],
                                    'runnerName': runner.get('runnerName'),
                                    'price': price
                                }
                                break
            
            elif market_type == 'BOTH_TEAMS_TO_SCORE':
                for market in markets:
                    market_name = market.get('marketName', '').lower()
                    market_type_api = market.get('marketType', '')
                    if market_type_api == 'BOTH_TEAMS_TO_SCORE' or 'entrambe' in market_name or 'both' in market_name:
                        target_market = market
                        break
                
                if target_market:
                    market_book = self.client.get_market_with_prices(target_market['marketId'])
                    for runner in market_book.get('runners', []):
                        runner_name = runner.get('runnerName', '').lower()
                        if selection.lower() == 'yes' and ('yes' in runner_name or 'si' in runner_name or runner_name == 'yes'):
                            price = get_runner_price(runner, bet_side)
                            if price:
                                target_runner = {
                                    'selectionId': runner['selectionId'],
                                    'runnerName': runner.get('runnerName'),
                                    'price': price
                                }
                                break
                        elif selection.lower() == 'no' and ('no' in runner_name and 'goal' not in runner_name):
                            price = get_runner_price(runner, bet_side)
                            if price:
                                target_runner = {
                                    'selectionId': runner['selectionId'],
                                    'runnerName': runner.get('runnerName'),
                                    'price': price
                                }
                                break
            
            elif market_type == 'FIRST_HALF_GOALS':
                target_line = over_line
                for market in markets:
                    market_name = market.get('marketName', '').lower()
                    market_type_api = market.get('marketType', '')
                    if ('1' in market_name or 'primo' in market_name or 'first' in market_name or 'half' in market_name):
                        if 'goal' in market_name or 'over' in market_name:
                            line_str = str(target_line).replace('.', '')
                            if line_str in market_name.replace('.', '').replace(',', ''):
                                target_market = market
                                break
                
                if target_market:
                    market_book = self.client.get_market_with_prices(target_market['marketId'])
                    for runner in market_book.get('runners', []):
                        runner_name = runner.get('runnerName', '').lower()
                        if 'over' in selection.lower() and 'over' in runner_name:
                            price = get_runner_price(runner, bet_side)
                            if price:
                                target_runner = {
                                    'selectionId': runner['selectionId'],
                                    'runnerName': runner.get('runnerName'),
                                    'price': price
                                }
                                break
                        elif 'under' in selection.lower() and 'under' in runner_name:
                            price = get_runner_price(runner, bet_side)
                            if price:
                                target_runner = {
                                    'selectionId': runner['selectionId'],
                                    'runnerName': runner.get('runnerName'),
                                    'price': price
                                }
                                break
            
            elif market_type == 'DOUBLE_CHANCE':
                for market in markets:
                    market_name = market.get('marketName', '').lower()
                    market_type_api = market.get('marketType', '')
                    if market_type_api == 'DOUBLE_CHANCE' or 'doppia' in market_name or 'double' in market_name:
                        target_market = market
                        break
                
                if target_market:
                    market_book = self.client.get_market_with_prices(target_market['marketId'])
                    for runner in market_book.get('runners', []):
                        runner_name = runner.get('runnerName', '').upper()
                        if selection.upper() in runner_name or selection.upper() == runner_name:
                            price = get_runner_price(runner, bet_side)
                            if price:
                                target_runner = {
                                    'selectionId': runner['selectionId'],
                                    'runnerName': runner.get('runnerName'),
                                    'price': price
                                }
                                break
            
            elif market_type == 'MATCH_ODDS':
                for market in markets:
                    market_type_api = market.get('marketType', '')
                    market_name = market.get('marketName', '').lower()
                    if market_type_api == 'MATCH_ODDS' or 'esito' in market_name or 'match odds' in market_name:
                        target_market = market
                        break
                
                if target_market:
                    market_book = self.client.get_market_with_prices(target_market['marketId'])
                    runners = market_book.get('runners', [])
                    for i, runner in enumerate(runners):
                        price = get_runner_price(runner, bet_side)
                        if not price:
                            continue
                        if selection == '1' and i == 0:
                            target_runner = {'selectionId': runner['selectionId'], 'runnerName': runner.get('runnerName'), 'price': price}
                            break
                        elif selection == 'X' and 'draw' in runner.get('runnerName', '').lower():
                            target_runner = {'selectionId': runner['selectionId'], 'runnerName': runner.get('runnerName'), 'price': price}
                            break
                        elif selection == '2' and i == 1 and 'draw' not in runner.get('runnerName', '').lower():
                            target_runner = {'selectionId': runner['selectionId'], 'runnerName': runner.get('runnerName'), 'price': price}
                            break
            
            elif market_type == 'CORRECT_SCORE':
                dutching_selections = signal.get('dutching_selections')
                
                for market in markets:
                    market_type_api = market.get('marketType', '')
                    market_name = market.get('marketName', '').lower()
                    if market_type_api == 'CORRECT_SCORE' or 'correct score' in market_name or 'risultato esatto' in market_name:
                        target_market = market
                        break
                
                if target_market and dutching_selections:
                    # Dutching mode: place bets on multiple correct scores
                    market_book = self.client.get_market_with_prices(target_market['marketId'])
                    matched_runners = []
                    missing_selections = []
                    
                    for score in dutching_selections:
                        score_normalized = score.replace('-', ' - ').strip()
                        found = False
                        for runner in market_book.get('runners', []):
                            runner_name = runner.get('runnerName', '')
                            runner_normalized = runner_name.replace('-', ' - ').replace('  ', ' ').strip()
                            if score_normalized in runner_normalized or score.replace(' ', '') in runner_name.replace(' ', ''):
                                price = get_runner_price(runner, bet_side)
                                if price:
                                    matched_runners.append({
                                        'selectionId': runner['selectionId'],
                                        'runnerName': runner_name,
                                        'price': price
                                    })
                                    found = True
                                    break
                        if not found:
                            missing_selections.append(score)
                    
                    # Verify ALL selections were found - abort if any missing
                    if missing_selections:
                        reason = f"Dutching incompleto - risultati non trovati: {', '.join(missing_selections)}"
                        log_failed_bet(reason)
                        update_status('FAILED')
                        messagebox.showwarning("Auto-Bet Dutching", reason)
                        return
                    
                    if matched_runners and len(matched_runners) == len(dutching_selections):
                        # Use dutching calculation
                        from dutching import calculate_dutching_stakes
                        dutching_result, total_profit, _ = calculate_dutching_stakes(
                            matched_runners, stake, bet_side, commission=4.5
                        )
                        
                        bet_details = []
                        for dr in dutching_result:
                            bet_details.append(f"  {dr['runnerName']}: €{dr['stake']:.2f} @ {dr['price']:.2f}")
                        bet_info = (
                            f"Evento: {matched_event['name']}\n"
                            f"Mercato: {target_market.get('marketName', 'N/A')}\n"
                            f"Tipo: DUTCHING {bet_side}\n"
                            f"Stake Totale: €{stake:.2f}\n"
                            f"Selezioni:\n" + "\n".join(bet_details) + f"\n"
                            f"Profitto Potenziale: €{total_profit:.2f}"
                        )
                        
                        if self.simulation_mode:
                            self.db.save_simulation_bet(
                                event_name=matched_event['name'],
                                market_id=target_market['marketId'],
                                market_name=target_market.get('marketName', ''),
                                bet_type=f'DUTCHING_{bet_side}',
                                selections=', '.join([r['runnerName'] for r in matched_runners]),
                                total_stake=stake,
                                potential_profit=total_profit,
                                status='MATCHED'
                            )
                            update_status('PLACED')
                            messagebox.showinfo("Auto-Bet Dutching (Simulazione)", f"Dutching simulato piazzato!\n\n{bet_info}")
                        else:
                            # Place real dutching bets with retry (3 attempts, 10s delay)
                            import time
                            max_retries = 3
                            retry_delay = 10
                            
                            for attempt in range(max_retries):
                                success_count = 0
                                failed_bets = []
                                
                                for dr in dutching_result:
                                    result = self.client.place_bet(
                                        market_id=target_market['marketId'],
                                        selection_id=dr['selectionId'],
                                        side=bet_side,
                                        price=dr['price'],
                                        size=dr['stake']
                                    )
                                    if result.get('status') == 'SUCCESS':
                                        success_count += 1
                                    else:
                                        failed_bets.append(dr)
                                
                                if success_count == len(dutching_result):
                                    update_status('PLACED')
                                    messagebox.showinfo("Auto-Bet Dutching", f"Dutching piazzato con successo!\n\n{bet_info}")
                                    break
                                elif attempt < max_retries - 1:
                                    # Retry failed bets after delay
                                    time.sleep(retry_delay)
                                    dutching_result = failed_bets
                                else:
                                    # Last attempt failed
                                    if success_count > 0:
                                        update_status('PARTIAL')
                                        messagebox.showwarning("Auto-Bet Dutching", f"Dutching parziale dopo {max_retries} tentativi: {success_count}/{len(matched_runners)} scommesse piazzate")
                                    else:
                                        update_status('FAILED')
                                        log_failed_bet(f"Tutte le scommesse dutching fallite dopo {max_retries} tentativi")
                                        messagebox.showerror("Auto-Bet Dutching Errore", f"Errore piazzamento dopo {max_retries} tentativi")
                        return
                    else:
                        reason = f"Nessun risultato trovato per dutching: {', '.join(dutching_selections)}"
                        log_failed_bet(reason)
                        update_status('FAILED')
                        messagebox.showwarning("Auto-Bet", reason)
                        return
                
                elif target_market:
                    # Single correct score bet (non-dutching)
                    market_book = self.client.get_market_with_prices(target_market['marketId'])
                    score_normalized = selection.replace('-', ' - ')
                    for runner in market_book.get('runners', []):
                        runner_name = runner.get('runnerName', '')
                        runner_normalized = runner_name.replace('-', ' - ').replace('  ', ' ')
                        if selection in runner_name or score_normalized in runner_normalized:
                            price = get_runner_price(runner, bet_side)
                            if price:
                                target_runner = {'selectionId': runner['selectionId'], 'runnerName': runner_name, 'price': price}
                                break
            
            elif market_type == 'ASIAN_HANDICAP':
                handicap_line = signal.get('handicap_line', 0)
                for market in markets:
                    market_type_api = market.get('marketType', '')
                    market_name = market.get('marketName', '').lower()
                    if market_type_api in ['ASIAN_HANDICAP', 'ASIAN_HANDICAP_DOUBLE_LINE'] or 'handicap' in market_name:
                        line_str = str(abs(handicap_line)).replace('.0', '')
                        if line_str in market_name.replace('.0', '').replace(',', '.'):
                            target_market = market
                            break
                
                if target_market:
                    market_book = self.client.get_market_with_prices(target_market['marketId'])
                    is_home = 'home' in selection.lower()
                    for runner in market_book.get('runners', []):
                        price = get_runner_price(runner, bet_side)
                        if not price:
                            continue
                        runner_handicap = runner.get('handicap', 0)
                        if is_home and runner_handicap == handicap_line:
                            target_runner = {'selectionId': runner['selectionId'], 'runnerName': runner.get('runnerName'), 'price': price}
                            break
                        elif not is_home and runner_handicap == -handicap_line:
                            target_runner = {'selectionId': runner['selectionId'], 'runnerName': runner.get('runnerName'), 'price': price}
                            break
            
            elif market_type == 'DRAW_NO_BET':
                for market in markets:
                    market_type_api = market.get('marketType', '')
                    market_name = market.get('marketName', '').lower()
                    if 'draw no bet' in market_name or 'pareggio no' in market_name:
                        target_market = market
                        break
                    if market_type_api == 'ASIAN_HANDICAP' and '0' in market_name:
                        target_market = market
                        break
                
                if target_market:
                    market_book = self.client.get_market_with_prices(target_market['marketId'])
                    runners = market_book.get('runners', [])
                    is_home = 'home' in selection.lower()
                    for i, runner in enumerate(runners):
                        price = get_runner_price(runner, bet_side)
                        if not price:
                            continue
                        if is_home and i == 0:
                            target_runner = {'selectionId': runner['selectionId'], 'runnerName': runner.get('runnerName'), 'price': price}
                            break
                        elif not is_home and i == 1:
                            target_runner = {'selectionId': runner['selectionId'], 'runnerName': runner.get('runnerName'), 'price': price}
                            break
            
            elif market_type == 'HALF_TIME_FULL_TIME':
                for market in markets:
                    market_type_api = market.get('marketType', '')
                    market_name = market.get('marketName', '').lower()
                    if market_type_api == 'HALF_TIME_FULL_TIME' or 'half time' in market_name or 'parziale' in market_name:
                        target_market = market
                        break
                
                if target_market:
                    market_book = self.client.get_market_with_prices(target_market['marketId'])
                    ht_ft_map = {'1': 'home', 'X': 'draw', '2': 'away'}
                    parts = selection.split('/')
                    if len(parts) == 2:
                        ht_sel = ht_ft_map.get(parts[0], parts[0].lower())
                        ft_sel = ht_ft_map.get(parts[1], parts[1].lower())
                        for runner in market_book.get('runners', []):
                            runner_name = runner.get('runnerName', '').lower()
                            price = get_runner_price(runner, bet_side)
                            if not price:
                                continue
                            if ht_sel in runner_name and ft_sel in runner_name:
                                target_runner = {'selectionId': runner['selectionId'], 'runnerName': runner.get('runnerName'), 'price': price}
                                break
                            if selection.upper().replace('/', ' / ') in runner_name.upper() or selection.upper() in runner_name.upper():
                                target_runner = {'selectionId': runner['selectionId'], 'runnerName': runner.get('runnerName'), 'price': price}
                                break
            
            if not target_market:
                reason = f"Mercato {market_type} non trovato per {matched_event['name']}"
                log_failed_bet(reason)
                update_status('FAILED')
                messagebox.showwarning("Auto-Bet", reason)
                return
            
            if not target_runner:
                reason = f"Selezione '{selection}' non disponibile per {matched_event['name']}"
                log_failed_bet(reason)
                update_status('FAILED')
                messagebox.showwarning("Auto-Bet", reason)
                return
            
            if bet_side == 'LAY':
                liability = stake * (target_runner['price'] - 1)
                potential_profit = stake
                bet_info = (
                    f"Evento: {matched_event['name']}\n"
                    f"Mercato: {target_market.get('marketName', 'N/A')}\n"
                    f"Selezione: {target_runner['runnerName']}\n"
                    f"Quota: {target_runner['price']}\n"
                    f"Liability: {liability:.2f} EUR\n"
                    f"Profitto Potenziale: {potential_profit:.2f} EUR\n"
                    f"Tipo: LAY"
                )
            else:
                potential_profit = stake * (target_runner['price'] - 1)
                bet_info = (
                    f"Evento: {matched_event['name']}\n"
                    f"Mercato: {target_market.get('marketName', 'N/A')}\n"
                    f"Selezione: {target_runner['runnerName']}\n"
                    f"Quota: {target_runner['price']}\n"
                    f"Stake: {stake:.2f} EUR\n"
                    f"Profitto Potenziale: {potential_profit:.2f} EUR\n"
                    f"Tipo: BACK"
                )
            
            if self.simulation_mode:
                commission = 0.045
                if bet_side == 'LAY':
                    gross_profit = stake
                else:
                    gross_profit = stake * (target_runner['price'] - 1)
                net_profit = gross_profit * (1 - commission)
                
                self.db.save_simulation_bet(
                    event_name=matched_event['name'],
                    market_id=target_market['marketId'],
                    market_name=target_market.get('marketName', ''),
                    bet_type=bet_side,
                    selections=target_runner['runnerName'],
                    total_stake=stake,
                    potential_profit=net_profit,
                    status='MATCHED'
                )
                update_status('PLACED')
                messagebox.showinfo("Auto-Bet (Simulazione)", f"Scommessa simulata piazzata!\n\n{bet_info}")
            else:
                # Place bet with retry (3 attempts, 10s delay)
                import time
                max_retries = 3
                retry_delay = 10
                
                for attempt in range(max_retries):
                    result = self.client.place_bet(
                        market_id=target_market['marketId'],
                        selection_id=target_runner['selectionId'],
                        side=bet_side,
                        price=target_runner['price'],
                        size=stake
                    )
                    
                    if result.get('status') == 'SUCCESS':
                        update_status('PLACED')
                        # Broadcast to Copy Trading followers (ALWAYS on SUCCESS, don't wait for match)
                        logging.info(f"[COPY] Auto-bet SUCCESS - about to broadcast")
                        try:
                            available = self.account_data.get('available', 100) if self.account_data else 100
                            stake_percent = (stake / available * 100) if available > 0 else 1.0
                            self._broadcast_copy_bet(
                                event_name=matched_event['name'],
                                market_name=target_market.get('marketName', ''),
                                selection=target_runner['runnerName'],
                                side=bet_side,
                                price=target_runner['price'],
                                stake_percent=stake_percent,
                                stake_amount=stake
                            )
                            logging.info(f"[COPY] Auto-bet broadcast call completed")
                        except Exception as e:
                            logging.error(f"[COPY] Auto-bet broadcast FAILED: {e}")
                        messagebox.showinfo("Auto-Bet", f"Scommessa piazzata con successo!\n\n{bet_info}")
                        break
                    elif attempt < max_retries - 1:
                        # Retry after delay
                        time.sleep(retry_delay)
                    else:
                        # Last attempt failed
                        error = result.get('errorCode', 'UNKNOWN')
                        reason = f"Errore Betfair dopo {max_retries} tentativi: {error}"
                        update_status('FAILED')
                        log_failed_bet(reason)
                        messagebox.showerror("Auto-Bet Errore", f"Errore piazzamento dopo {max_retries} tentativi: {error}")
        
        except Exception as e:
            log_failed_bet(f"Eccezione: {str(e)}")
            update_status('FAILED')
            messagebox.showerror("Auto-Bet Errore", f"Errore: {str(e)}")
    
    def _show_multi_market_monitor(self):
        """Show multi-market monitor window."""
        if not self.client:
            messagebox.showwarning("Attenzione", "Connettiti prima a Betfair")
            return
        
        monitor = tk.Toplevel(self.root)
        monitor.title("Multi-Market Monitor")
        monitor.geometry("1000x600")
        monitor.transient(self.root)
        
        # Initialize watchlist
        if not hasattr(self, 'watchlist'):
            self.watchlist = []
        
        main_frame = ttk.Frame(monitor, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Top controls
        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(control_frame, text="Aggiungi mercato corrente alla watchlist:").pack(side=tk.LEFT)
        
        def add_current_market():
            if self.current_market and self.current_event:
                market_info = {
                    'event_id': self.current_event['id'],
                    'event_name': self.current_event['name'],
                    'market_id': self.current_market['marketId'],
                    'market_name': self.current_market.get('marketName', 'N/A'),
                    'runners': self.current_market.get('runners', [])
                }
                # Check if already in watchlist
                for m in self.watchlist:
                    if m['market_id'] == market_info['market_id']:
                        messagebox.showinfo("Info", "Mercato già nella watchlist")
                        return
                self.watchlist.append(market_info)
                refresh_watchlist()
                messagebox.showinfo("Aggiunto", f"Aggiunto: {market_info['event_name']}")
            else:
                messagebox.showwarning("Attenzione", "Seleziona prima un mercato")
        
        ttk.Button(control_frame, text="+ Aggiungi Corrente", command=add_current_market).pack(side=tk.LEFT, padx=10)
        
        # Auto-refresh toggle
        monitor_refresh_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(control_frame, text="Auto-refresh (30s)", variable=monitor_refresh_var).pack(side=tk.LEFT, padx=10)
        
        def remove_selected():
            selection = watchlist_tree.selection()
            if selection:
                idx = watchlist_tree.index(selection[0])
                if 0 <= idx < len(self.watchlist):
                    del self.watchlist[idx]
                    refresh_watchlist()
        
        ttk.Button(control_frame, text="Rimuovi Selezionato", command=remove_selected).pack(side=tk.RIGHT)
        
        # Watchlist treeview
        columns = ('event', 'market', 'runner1', 'back1', 'lay1', 'runner2', 'back2', 'lay2', 'runner3', 'back3', 'lay3')
        watchlist_tree = ttk.Treeview(main_frame, columns=columns, show='headings', height=20)
        
        watchlist_tree.heading('event', text='Evento')
        watchlist_tree.heading('market', text='Mercato')
        watchlist_tree.heading('runner1', text='Sel 1')
        watchlist_tree.heading('back1', text='Back')
        watchlist_tree.heading('lay1', text='Lay')
        watchlist_tree.heading('runner2', text='Sel 2')
        watchlist_tree.heading('back2', text='Back')
        watchlist_tree.heading('lay2', text='Lay')
        watchlist_tree.heading('runner3', text='Sel 3')
        watchlist_tree.heading('back3', text='Back')
        watchlist_tree.heading('lay3', text='Lay')
        
        watchlist_tree.column('event', width=150)
        watchlist_tree.column('market', width=100)
        for col in ['runner1', 'runner2', 'runner3']:
            watchlist_tree.column(col, width=80)
        for col in ['back1', 'lay1', 'back2', 'lay2', 'back3', 'lay3']:
            watchlist_tree.column(col, width=50)
        
        scrollbar = ttk.Scrollbar(main_frame, orient=tk.VERTICAL, command=watchlist_tree.yview)
        watchlist_tree.configure(yscrollcommand=scrollbar.set)
        
        watchlist_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        def refresh_watchlist():
            watchlist_tree.delete(*watchlist_tree.get_children())
            for item in self.watchlist:
                runners = item.get('runners', [])[:3]
                values = [item['event_name'][:20], item['market_name'][:15]]
                for r in runners:
                    values.append(r.get('runnerName', 'N/A')[:10])
                    values.append(r.get('backPrice', '-'))
                    values.append(r.get('layPrice', '-'))
                # Pad if less than 3 runners
                while len(values) < 11:
                    values.append('-')
                watchlist_tree.insert('', tk.END, values=values)
        
        def update_prices():
            if not monitor.winfo_exists():
                return
            
            def fetch_all():
                updated = []
                for item in self.watchlist:
                    try:
                        book = self.client.get_market_book(item['market_id'])
                        if book and book.get('runners'):
                            runners = []
                            for r in book['runners'][:3]:
                                runner_info = {
                                    'runnerName': next((x.get('runnerName') for x in item['runners'] 
                                                       if x.get('selectionId') == r.get('selectionId')), 'N/A'),
                                    'backPrice': r.get('ex', {}).get('availableToBack', [{}])[0].get('price', '-') if r.get('ex', {}).get('availableToBack') else '-',
                                    'layPrice': r.get('ex', {}).get('availableToLay', [{}])[0].get('price', '-') if r.get('ex', {}).get('availableToLay') else '-'
                                }
                                runners.append(runner_info)
                            item['runners'] = runners
                        updated.append(item)
                    except Exception as e:
                        print(f"Error updating {item['market_id']}: {e}")
                        updated.append(item)
                return updated
            
            def on_complete(updated):
                self.watchlist = updated
                refresh_watchlist()
                if monitor.winfo_exists() and monitor_refresh_var.get():
                    monitor.after(30000, update_prices)
            
            def thread_func():
                result = fetch_all()
                if monitor.winfo_exists():
                    monitor.after(0, lambda: on_complete(result))
            
            threading.Thread(target=thread_func, daemon=True).start()
        
        refresh_watchlist()
        update_prices()
        
        # Status bar
        status = ttk.Label(main_frame, text=f"Mercati monitorati: {len(self.watchlist)}")
        status.pack(side=tk.BOTTOM, pady=5)
    
    def _show_advanced_filters(self):
        """Show advanced filters dialog."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Filtri Avanzati")
        dialog.geometry("400x450")
        dialog.transient(self.root)
        dialog.grab_set()
        
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text="Filtri Avanzati Eventi", style='Title.TLabel').pack(pady=(0, 15))
        
        # Initialize filter vars if not exists
        if not hasattr(self, 'filter_settings'):
            self.filter_settings = {
                'competitions': [],
                'time_filter': 'all',
                'min_odds': 1.01,
                'max_odds': 1000,
                'only_live': False
            }
        
        # Competition filter
        ttk.Label(frame, text="Campionati (separati da virgola):").pack(anchor=tk.W)
        comp_var = tk.StringVar(value=','.join(self.filter_settings.get('competitions', [])))
        ttk.Entry(frame, textvariable=comp_var, width=40).pack(fill=tk.X, pady=(0, 10))
        ttk.Label(frame, text="Es: Serie A, Premier League, La Liga", 
                  font=('Segoe UI', 8), foreground='gray').pack(anchor=tk.W)
        
        # Time filter
        ttk.Label(frame, text="Periodo:").pack(anchor=tk.W, pady=(10, 0))
        time_var = tk.StringVar(value=self.filter_settings.get('time_filter', 'all'))
        time_frame = ttk.Frame(frame)
        time_frame.pack(fill=tk.X, pady=5)
        
        ttk.Radiobutton(time_frame, text="Tutti", variable=time_var, value='all').pack(side=tk.LEFT)
        ttk.Radiobutton(time_frame, text="Oggi", variable=time_var, value='today').pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(time_frame, text="24 ore", variable=time_var, value='24h').pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(time_frame, text="48 ore", variable=time_var, value='48h').pack(side=tk.LEFT, padx=10)
        
        # Odds range
        ttk.Label(frame, text="Range Quote:").pack(anchor=tk.W, pady=(15, 0))
        odds_frame = ttk.Frame(frame)
        odds_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(odds_frame, text="Min:").pack(side=tk.LEFT)
        min_odds_var = tk.StringVar(value=str(self.filter_settings.get('min_odds', 1.01)))
        ttk.Entry(odds_frame, textvariable=min_odds_var, width=8).pack(side=tk.LEFT, padx=5)
        
        ttk.Label(odds_frame, text="Max:").pack(side=tk.LEFT, padx=(20, 0))
        max_odds_var = tk.StringVar(value=str(self.filter_settings.get('max_odds', 1000)))
        ttk.Entry(odds_frame, textvariable=max_odds_var, width=8).pack(side=tk.LEFT, padx=5)
        
        # Live only
        live_var = tk.BooleanVar(value=self.filter_settings.get('only_live', False))
        ttk.Checkbutton(frame, text="Solo partite LIVE", variable=live_var).pack(anchor=tk.W, pady=15)
        
        # Apply button
        def apply_filters():
            try:
                min_odds = float(min_odds_var.get())
                max_odds = float(max_odds_var.get())
            except ValueError:
                messagebox.showerror("Errore", "Quote min/max devono essere numeri")
                return
            
            comps = [c.strip() for c in comp_var.get().split(',') if c.strip()]
            
            self.filter_settings = {
                'competitions': comps,
                'time_filter': time_var.get(),
                'min_odds': min_odds,
                'max_odds': max_odds,
                'only_live': live_var.get()
            }
            
            self._apply_filters_to_events()
            dialog.destroy()
            messagebox.showinfo("Filtri Applicati", "I filtri sono stati applicati alla lista eventi")
        
        def reset_filters():
            self.filter_settings = {
                'competitions': [],
                'time_filter': 'all',
                'min_odds': 1.01,
                'max_odds': 1000,
                'only_live': False
            }
            self._apply_filters_to_events()
            dialog.destroy()
            messagebox.showinfo("Filtri Reset", "I filtri sono stati rimossi")
        
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=20)
        
        ttk.Button(btn_frame, text="Applica Filtri", command=apply_filters).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Reset", command=reset_filters).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Annulla", command=dialog.destroy).pack(side=tk.LEFT, padx=5)
    
    def _apply_filters_to_events(self):
        """Apply advanced filters to events list."""
        if not hasattr(self, 'all_events') or not self.all_events:
            return
        
        filters = getattr(self, 'filter_settings', {})
        
        filtered = []
        now = datetime.now()
        
        for event in self.all_events:
            # Competition filter
            if filters.get('competitions'):
                event_name = event.get('name', '').lower()
                comp_match = any(comp.lower() in event_name for comp in filters['competitions'])
                country = event.get('countryCode', '')
                comp_match = comp_match or any(comp.lower() in country.lower() for comp in filters['competitions'])
                if not comp_match:
                    continue
            
            # Time filter
            time_filter = filters.get('time_filter', 'all')
            if time_filter != 'all' and event.get('openDate'):
                try:
                    event_time = datetime.fromisoformat(event['openDate'].replace('Z', '+00:00')).replace(tzinfo=None)
                    if time_filter == 'today':
                        if event_time.date() != now.date():
                            continue
                    elif time_filter == '24h':
                        if (event_time - now).total_seconds() > 86400:
                            continue
                    elif time_filter == '48h':
                        if (event_time - now).total_seconds() > 172800:
                            continue
                except:
                    pass
            
            # Live only filter
            if filters.get('only_live') and not event.get('inPlay'):
                continue
            
            filtered.append(event)
        
        # Update display with filtered events
        self.filtered_events = filtered
        self._populate_events_tree_filtered()
    
    def _populate_events_tree_filtered(self):
        """Populate events tree with filtered events."""
        events = getattr(self, 'filtered_events', self.all_events)
        if not events:
            events = self.all_events
        
        self.events_tree.delete(*self.events_tree.get_children())
        search = self.search_var.get().lower()
        
        if search:
            for event in events:
                if search in event['name'].lower():
                    date_str = self._format_event_date(event)
                    self.events_tree.insert('', tk.END, iid=event['id'], text=event.get('countryCode', ''), values=(
                        event['name'],
                        date_str
                    ))
        else:
            countries = {}
            for event in events:
                country = event.get('countryCode', 'XX') or 'XX'
                if country not in countries:
                    countries[country] = []
                countries[country].append(event)
            
            for country in sorted(countries.keys()):
                country_id = f"country_{country}"
                self.events_tree.insert('', tk.END, iid=country_id, text=country, open=False)
                
                for event in countries[country]:
                    date_str = self._format_event_date(event)
                    self.events_tree.insert(country_id, tk.END, iid=event['id'], values=(
                        event['name'],
                        date_str
                    ))
    
    def _toggle_simulation_mode(self):
        """Toggle simulation mode on/off."""
        self.simulation_mode = not self.simulation_mode
        
        if self.simulation_mode:
            self.sim_btn.configure(fg_color='#9c27b0', text="SIMULAZIONE ON")
            self.root.title(f"{APP_NAME} v{APP_VERSION} - MODALITA SIMULAZIONE")
            sim_settings = self.db.get_simulation_settings()
            if sim_settings:
                balance = sim_settings.get('virtual_balance', 1000)
                self.sim_balance_label.configure(text=f"Saldo Virtuale: {format_currency(balance)}")
            messagebox.showinfo("Simulazione Attiva", 
                "Modalita simulazione attivata!\n\n"
                "Le scommesse NON saranno piazzate su Betfair.\n"
                "Userai soldi virtuali per testare strategie.\n\n"
                "Le quote sono REALI da Betfair Exchange.")
        else:
            self.sim_btn.configure(fg_color=COLORS['button_secondary'], text="SIMULAZIONE")
            self.root.title(f"{APP_NAME} v{APP_VERSION}")
            self.sim_balance_label.configure(text="")
    
    def _update_simulation_balance_display(self):
        """Update simulation balance display."""
        if self.simulation_mode:
            sim_settings = self.db.get_simulation_settings()
            if sim_settings:
                balance = sim_settings.get('virtual_balance', 1000)
                self.sim_balance_label.configure(text=f"Saldo Virtuale: {format_currency(balance)}")
    
    def _show_simulation_dashboard(self):
        """Show simulation statistics dashboard."""
        stats = self.db.get_simulation_stats()
        if not stats:
            messagebox.showinfo("Simulazione", "Nessun dato di simulazione disponibile")
            return
        
        dialog = tk.Toplevel(self.root)
        dialog.title("Dashboard Simulazione")
        dialog.geometry("500x450")
        dialog.transient(self.root)
        
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text="Statistiche Simulazione", style='Title.TLabel').pack(pady=(0, 20))
        
        # Balance section
        balance_frame = ttk.LabelFrame(frame, text="Bilancio", padding=10)
        balance_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(balance_frame, text=f"Saldo Iniziale: {format_currency(stats['starting_balance'])}").pack(anchor=tk.W)
        ttk.Label(balance_frame, text=f"Saldo Attuale: {format_currency(stats['virtual_balance'])}", 
                  style='Money.TLabel').pack(anchor=tk.W)
        
        pl_color = 'green' if stats['profit_loss'] >= 0 else 'red'
        pl_label = ttk.Label(balance_frame, text=f"Profitto/Perdita: {format_currency(stats['profit_loss'])}")
        pl_label.pack(anchor=tk.W)
        pl_label.config(foreground=pl_color)
        
        # Stats section
        stats_frame = ttk.LabelFrame(frame, text="Statistiche Scommesse", padding=10)
        stats_frame.pack(fill=tk.X, pady=10)
        
        ttk.Label(stats_frame, text=f"Totale Scommesse: {stats['total_bets']}").pack(anchor=tk.W)
        ttk.Label(stats_frame, text=f"Vinte: {stats['total_won']}").pack(anchor=tk.W)
        ttk.Label(stats_frame, text=f"Perse: {stats['total_lost']}").pack(anchor=tk.W)
        ttk.Label(stats_frame, text=f"Win Rate: {stats['win_rate']:.1f}%").pack(anchor=tk.W)
        
        # Recent bets
        bets_frame = ttk.LabelFrame(frame, text="Ultime Scommesse Simulate", padding=10)
        bets_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        bets = self.db.get_simulation_bets(limit=10)
        
        columns = ('evento', 'stake', 'profitto', 'data')
        bets_tree = ttk.Treeview(bets_frame, columns=columns, show='headings', height=6)
        bets_tree.heading('evento', text='Evento')
        bets_tree.heading('stake', text='Stake')
        bets_tree.heading('profitto', text='Profitto')
        bets_tree.heading('data', text='Data')
        
        bets_tree.column('evento', width=150)
        bets_tree.column('stake', width=80)
        bets_tree.column('profitto', width=80)
        bets_tree.column('data', width=120)
        
        for bet in bets:
            date_str = bet['placed_at'][:16] if bet.get('placed_at') else '-'
            bets_tree.insert('', tk.END, values=(
                bet.get('event_name', '-')[:20],
                format_currency(bet.get('total_stake', 0)),
                format_currency(bet.get('potential_profit', 0)),
                date_str
            ))
        
        bets_tree.pack(fill=tk.BOTH, expand=True)
        
        ttk.Button(frame, text="Chiudi", command=dialog.destroy).pack(pady=10)
    
    def _reset_simulation(self):
        """Reset simulation balance and history."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Reset Simulazione")
        dialog.geometry("300x200")
        dialog.transient(self.root)
        dialog.grab_set()
        
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text="Nuovo Saldo Iniziale (EUR):").pack(anchor=tk.W)
        balance_var = tk.StringVar(value="1000.00")
        ttk.Entry(frame, textvariable=balance_var, width=15).pack(anchor=tk.W, pady=5)
        
        ttk.Label(frame, text="Questo cancellera tutto lo storico\ndelle scommesse simulate.", 
                  foreground='gray').pack(pady=10)
        
        def do_reset():
            try:
                new_balance = float(balance_var.get())
                if new_balance <= 0:
                    raise ValueError()
                self.db.reset_simulation(new_balance)
                self._update_simulation_balance_display()
                dialog.destroy()
                messagebox.showinfo("Reset Completato", 
                    f"Simulazione resettata.\nNuovo saldo: {format_currency(new_balance)}")
            except ValueError:
                messagebox.showerror("Errore", "Inserisci un importo valido")
        
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Reset", command=do_reset).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Annulla", command=dialog.destroy).pack(side=tk.LEFT, padx=5)
    
    def run(self):
        """Start the application."""
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.root.mainloop()
    
    def _on_closing(self):
        """Handle application closing - cleanup resources."""
        try:
            # Stop Telegram listener if running
            if self.telegram_listener:
                try:
                    self.telegram_listener.stop()
                except:
                    pass
            
            # Close database connection
            if self.db:
                self.db.close()
        except:
            pass
        finally:
            self.root.destroy()


def main():
    # Check for single instance
    lock = check_single_instance()
    if lock is None:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Pickfair", "Pickfair è già in esecuzione.\n\nChiudi l'altra finestra prima di aprirne una nuova.")
        root.destroy()
        return
    
    app = PickfairApp()
    app.run()


if __name__ == "__main__":
    main()
