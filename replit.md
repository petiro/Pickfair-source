# Pickfair - Betfair Exchange Trading Application

## Overview

Pickfair is a Windows desktop application for automated sports betting through Betfair Exchange Italy API. Built with Python, it features advanced dutching, live betting, Telegram integration, and a modern dark trading interface.

**Current Version**: v3.16.0 (Plugin System)

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

### Data Storage
- **SQLite database**: `%APPDATA%\Pickfair\pickfair.db` (1-5 MB)
- **Telegram session**: `%APPDATA%\Pickfair\telegram_session`
- **Plugins**: `%APPDATA%\Pickfair\plugins` (.py files)
- **Plugin data**: `%APPDATA%\Pickfair\data` (JSON config)

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
