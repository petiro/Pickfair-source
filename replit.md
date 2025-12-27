# Pickfair - Betfair Exchange Trading Application

## Overview

Pickfair is a Windows desktop application for automated sports betting through Betfair Exchange Italy API. Built with Python, it features advanced dutching, live betting, Telegram integration, and a modern dark trading interface.

**Current Version**: v3.27.7 (Database Lock Fix)

## User Preferences

- Communication: Simple, everyday language (Italian)
- Design: Dark trading interface with professional aesthetics
- **No popup windows**: All functionality must be in tabs
- Colors: BACK (blue #1e88e5), LAY (pink #e5399b), profit (green), loss (red)

## System Architecture

### UI Framework
- **CustomTkinter** (ctk) for modern dark theme widgets
- **ttk** for Treeview and complex widgets (no CTk equivalent)
- Tab-based interface using CTkTabview
- Dark theme configuration in `theme.py`

### Core Modules
- `main.py` - Main application with UI and trading logic
- `betfair_client.py` - Betfair Exchange API integration
- `telegram_listener.py` - Telegram chat monitoring with Telethon
- `database.py` - SQLite database operations
- `dutching.py` - Dutching calculations (BACK/LAY mixed)
- `auto_updater.py` - Automatic update system
- `theme.py` - Dark theme colors and configuration
- `plugin_manager.py` - Plugin system with security sandbox
- `license_manager.py` - License validation and hardware ID
- `license_generator.py` - License key generator (private tool)

### Data Storage
- **SQLite database**: `%APPDATA%\Pickfair\pickfair.db` (1-5 MB)
- **Telegram session**: `%APPDATA%\Pickfair\telegram_session`
- **Plugins**: `%APPDATA%\Pickfair\plugins` (.py files)
- **Plugin data**: `%APPDATA%\Pickfair\data` (JSON config)
- **License file**: `%APPDATA%\Pickfair\license.key`

## Theme Colors (from theme.py)

```python
COLORS = {
    'bg_dark': '#0b111a',
    'bg_card': '#111827',
    'bg_hover': '#1f2937',
    'back': '#1e88e5',      # Blue for BACK bets
    'lay': '#e5399b',       # Pink for LAY bets
    'success': '#10b981',   # Green for profit
    'loss': '#ef4444',      # Red for loss
    'text': '#f3f4f6',
    'text_secondary': '#9ca3af'
}
```

## Key Features

- **Dutching**: Advanced calculations with mixed BACK/LAY support
- **Live Betting**: Real-time odds streaming
- **Bet Booking**: Track potential bets before placing
- **Manual/Auto Cashout**: Position management
- **Telegram Integration**: Chat monitoring and auto-bet placement
- **Multi-Market Monitoring**: Track multiple events
- **Simulation Mode**: Test strategies with virtual balance
- **Italian Compliance**: 4.5% commission handling

## Recent Changes (December 2024)

- **v3.27.7**: Database Lock Fix (Telegram)
  - Aggiunto pattern retry a get_telegram_settings, save_telegram_settings, save_telegram_session
  - Aumentati retry da 3 a 5 con delay ridotto (0.3s invece di 0.5s)
  - Migliorata gestione errori in _add_selected_available_chats con try/except
  - Aggiunto logging per debug dei retry database

- **v3.27.6**: Statistics Fix
  - Corretto bug nella tab Statistiche: ora mostra tutte le scommesse salvate
  - Rimosso filtro WHERE bet_id IS NOT NULL che escludeva le scommesse dutching
  - Query ora conta tutti i record nella tabella bets
  - Aggiunto logging per debug in get_bet_statistics e save_bet_order

- **v3.27.5**: Database Row Factory Fix
  - Corretto conflitto row_factory impostando una sola volta nella connessione persistente

- **v3.27.4**: CTkOptionMenu Fix
  - Corretto bug sintassi CTkOptionMenu che causava dropdown mercati vuoto
  - Ora usa configure(values=...) e .set() invece di ['values'] e .current()

- **v3.27.3**: File Logging
  - Aggiunto sistema di logging su file in %APPDATA%\Pickfair\pickfair.log
  - Rotazione automatica a 5MB con 3 backup

- **v3.27.1**: Database Locking Fix
  - Risolti problemi critici di locking database con architettura connessione persistente
  - Threading RLock, WAL mode, timeout 30s

- **v3.26.0**: Copy Trading
  - Nuova sezione Copy Trading nella tab Telegram con radio buttons Off/Master/Follower
  - Master mode: trasmette tutte le scommesse e cashout ai follower via Telegram
  - Broadcast integrato in: quick bets, dutching, auto-bet, cashout
  - Formato messaggio: COPY BET con evento, mercato, selezione, tipo, quota, stake percentuale
  - Formato cashout: COPY CASHOUT con nome evento
  - Stake percentuale calcolata su saldo disponibile per compatibilità cross-bankroll
  - Campo Chat ID per specificare canale/gruppo di destinazione
  - Nuovi campi database: copy_mode, copy_chat_id in telegram_settings

- **v3.25.2**: LIVE/PRE-MATCH Filter + Refertazione Automatica
  - Nuovo filtro evento nel parser Telegram: riconosce LIVE, IN PLAY, DIRETTA per eventi live
  - Riconosce PRE-MATCH, ANTE-MATCH, NON LIVE per eventi pre-match
  - Ricerca mirata su Betfair: se specificato LIVE cerca solo negli eventi in corso
  - Se specificato PRE-MATCH cerca solo negli eventi futuri (esclude live)
  - Refertazione automatica: controlla ogni 60 secondi le scommesse settate su Betfair
  - Metodo `get_cleared_orders` in betfair_client.py per ottenere scommesse settate
  - Metodo `get_market_status` per monitorare stato mercati
  - Nuova colonna `outcome` nel database (WON/LOST/VOID)
  - Nuova sub-tab "Statistiche" nella Dashboard con totali/vinte/perse/win rate/P&L

- **v3.24.0**: Custom Parsing Rules
  - Nuova sezione "Regole di Parsing" nella tab Telegram
  - Tabella database `signal_patterns` per salvare regole personalizzate
  - UI inline (no popup) per aggiungere/modificare/eliminare regole
  - Ogni regola ha: nome, descrizione, pattern regex, tipo mercato, attivo/disattivo
  - Validazione regex prima del salvataggio
  - Pattern personalizzati applicati PRIMA di quelli predefiniti nel parsing
  - Aggiornamento automatico listener quando le regole cambiano
  - Esempi pattern inclusi nell'editor (Over, Under, GG/BTTS, 1X2)

- **v3.23.0**: Pre-Match Support
  - Auto-bet ora cerca prima negli eventi LIVE, poi negli eventi PRE-MATCH
  - Supporto completo per scommesse pre-match (fino a 2 giorni in anticipo)
  - Algoritmo di matching evento migliorato

- **v3.22.0**: Full Market Support
  - Aggiunto supporto completo per tutti i mercati Betfair principali:
    - MATCH_ODDS (1X2 - Esito finale)
    - CORRECT_SCORE (Risultato esatto)
    - ASIAN_HANDICAP (Handicap asiatico)
    - DRAW_NO_BET (Pareggio no scommessa)
    - HALF_TIME_FULL_TIME (Parziale/Finale)
  - Parser Telegram aggiornato con nuovi pattern regex
  - Auto-bet supporta tutti i 9 tipi di mercato

- **v3.21.0**: Multi-Market Auto-Bet
  - Parser Telegram ora riconosce: Over/Under, GG/NG (BTTS), 1T Over/Under, Doppia Chance
  - Auto-bet funziona per tutti i tipi di mercato supportati
  - Aggiornamento automatico stato segnale (PENDING → PLACED/FAILED)
  - Refresh automatico tabella segnali dopo piazzamento

- **v3.20.1**: Signal Status Updates
  - Aggiornamento stato segnale dopo piazzamento
  - Refresh automatico dashboard

- **v3.19.0**: License System
  - Added `license_manager.py` for hardware ID and license validation
  - Added `license_generator.py` with GUI for generating license keys (private tool)
  - Added license activation screen that blocks app until activated
  - Hardware ID based on MAC address and system info
  - License keys stored in `%APPDATA%\Pickfair\license.key`
  - Separate workflow `build-license-generator.yml` for generator exe (not in public releases)

- **v3.16.0**: Plugin System
  - Added `plugin_manager.py` with comprehensive security features:
    - Code validation blocking dangerous functions (eval, exec, os.system, subprocess, etc.)
    - Sandbox file access limiting plugins to allowed directories
    - Timeout protection (10s max execution time)
    - Thread-safe plugin execution preventing UI freezes
    - Dynamic library installation from requirements.txt
    - Resource monitoring capabilities
    - Plugin API for safe app interaction
  - Added Plugin Tab to main interface with:
    - Install/Uninstall plugins from .py files
    - Enable/Disable plugins dynamically
    - Plugin details view (version, author, execution stats)
    - Security info panel
    - Open plugins folder button
  - Created plugin template and example plugin (odds_alert)
  - Added build.bat for PyInstaller packaging

- **v3.15.0**: Full CustomTkinter Tab Conversion
  - **Dashboard Tab**: Converted to CTkFrame, CTkLabel, CTkButton with CTkTabview sub-tabs
  - **Telegram Tab**: Full CTk conversion with CTkFrame panels, CTkEntry inputs, CTkCheckBox, CTkButton
  - **Strumenti Tab**: Converted to CTkFrame and CTkButton with proper theming
  - **Impostazioni Tab**: Full CTk conversion with grid layout for credentials form
  - **Simulazione Tab**: Converted stat cards to CTkFrame with proper color theming
  - Updated all .config() to .configure() for CTkLabel and CTkButton widgets
  - Fixed Treeview tag colors to use COLORS dictionary (success/loss)
  - All tabs now use COLORS and FONTS from theme.py for consistency

- **v3.14.0**: CustomTkinter conversion STARTED
  - Converted main window (CTk) with dark trading theme
  - Converted tab system (CTkTabview)
  - Converted all trading panel buttons (dutch_modal_btn, back_btn, lay_btn, market_cashout_btn) to CTkButton
  - Converted market_status_label to CTkLabel
  - Enhanced COLORS palette with info_hover for consistent theming
  - Treeview widgets remain ttk (styled via configure_ttk_dark_theme())

## Dependencies

- customtkinter - Modern dark UI widgets
- betfairlightweight - Betfair Exchange API
- telethon - Telegram client
- numpy - Calculations
- PyInstaller - Executable packaging
