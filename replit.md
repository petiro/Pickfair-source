# Pickfair - Betfair Exchange Trading Application

## Overview

Pickfair is a Python-based Windows desktop application designed for automated sports betting via the Betfair Exchange Italy API. Its primary purpose is to provide a robust platform for advanced dutching, live betting, and automated bet placement, integrating with Telegram for signal processing. The application aims to offer a modern, dark-themed trading experience with features like multi-market monitoring, simulation mode, and compliance with Italian commission handling.

## User Preferences

- Communication: Simple, everyday language (Italian)
- Design: Dark trading interface with professional aesthetics
- No popup windows: All functionality must be in tabs
- Colors: BACK (blue #1e88e5), LAY (pink #e5399b), profit (green), loss (red)

## System Architecture

### UI/UX Decisions
The application utilizes a dark trading interface with professional aesthetics, employing `CustomTkinter` for modern widgets and `ttk` for complex elements like Treeview. The interface is tab-based, ensuring no popup windows, with a consistent dark theme defined in `theme.py`. Specific color preferences are set for BACK bets (blue), LAY bets (pink), profit indicators (green), and loss indicators (red).

### Technical Implementations
The core application logic is managed by `main.py`, integrating with `betfair_client.py` for API interactions, `betfair_stream.py` for real-time order and market updates via Betfair Stream API (OCM for orders, MCM for quotes), and `telegram_listener.py` for Telegram chat monitoring and signal processing. `database.py` handles SQLite operations, while `dutching.py` manages complex dutching calculations. An `auto_updater.py` is in place for seamless updates. A `plugin_manager.py` offers a secure sandbox environment for extending functionality, complemented by `license_manager.py` for hardware-based license validation.

### Feature Specifications
Key features include:
- **Advanced Dutching**: Supports mixed BACK/LAY bets with PRO features:
  - Best odds highlighting (green border on best quote)
  - Auto-remove suspended runners checkbox
  - Book warning at 105% (orange), blocking at 110% (red, disables Submit)
  - P&L preview showing guaranteed profit and worst-case loss
  - Preset stake buttons (25% / 50% / 100%)
  - **AI Mixed Auto-Entry**: Automatic BACK/LAY classification based on implied probability
    - Calculates avg_prob = sum(1/price)/n as threshold
    - Forces mixed positions (at least 1 BACK + 1 LAY) when n>1
    - Explicit error handling: raises ValueError with Italian messages when allocation fails
    - Budget protection: validates total stake does not exceed limit after min_stake clamping
    - Profit verification: ensures avg_profit > 0 after all normalizations
  - **Auto-Green Toggle**: UI toggle that adds metadata to orders for downstream monitoring
  - **Simulation Mode Toggle**: Test strategies without real money risk
- **Real-time Live Betting**: Offers live odds streaming and quick bet placement.
- **Telegram Integration**: Monitors chats for betting signals, supporting various market types (e.g., Match Odds, Correct Score, Over/Under, BTTS, Asian Handicap) and enabling auto-betting and copy trading (Master/Follower modes).
- **Cashout Management**: Manual and automatic cashout options.
- **Multi-Market Monitoring**: Tracks multiple events concurrently via Market Watch List.
- **Simulation Mode**: Allows strategy testing with a virtual balance.
- **Automatic Result Settlement**: Tracks and settles bets, updating statistics.
- **Custom Parsing Rules**: Users can define custom regex patterns for Telegram signal processing.
- **Plugin System**: Secure, sandboxed environment for custom scripts with a defined API.
- **My Bets Panel**: Real-time order tracking with Pending/Unmatched/Matched sections, P&L live display, and inline action buttons (Cancel, Replace ±1 tick, Green-up).
- **Trading Automation PRO (v3.60.3)**: 
  - P&L Engine for real-time profit/loss calculation (BACK and LAY)
  - Stop Loss / Take Profit automation with thread-safe triggers
  - Trailing Stop for profit protection with peak tracking
  - Partial Green (hedge) for risk management
  - Tick Storage for historical quote data and OHLC aggregation
  - Thread-safe debounce for action buttons (Cancel 1.0s, Replace 0.5s, Green 2.0s)
  - Price fallback system using tick_storage when market data unavailable
  - Green-up works independently of UI market selection
  - Simulation Broker for testing strategies without real money
    - Exchange-realistic behavior: reserves full stake/liability at placement
    - Partial matching support with slippage simulation via price_ladder
    - Proper liability accounting for LAY with price improvement (price_requested vs price)
    - cancel_order handles partial matches correctly
    - settle_market releases unmatched exposure and settles matched portions
  - Book Optimizer for automatic stake redistribution when book > 105%
    - Iterative rebalancing after min_stake clamping (€2 minimum)
    - Maintains equal-profit distribution
  - Tick Replay Engine for backtesting with historical data
  - Centralized configuration in trading_config.py (BOOK_WARNING, BOOK_BLOCK, MIN_STAKE)

### System Design Choices
- **Data Storage**: Uses an SQLite database (`pickfair.db`) for application data, Telegram session files, plugin data (JSON configs), and license keys, all stored within `%APPDATA%\Pickfair`.
- **Theming**: A centralized `theme.py` defines a consistent dark color palette and fonts.
- **Database Management**: Employs persistent connections with retry mechanisms and WAL mode to handle concurrent access and prevent locking issues.
- **Security**: The plugin system includes code validation, sandboxed file access, timeout protection, and thread-safe execution to ensure stability and security.

## External Dependencies

- **customtkinter**: For building the modern dark-themed graphical user interface.
- **betfairlightweight**: Python wrapper for interacting with the Betfair Exchange API.
- **telethon**: For Telegram client operations, enabling chat monitoring and message processing.
- **numpy**: Utilized for various calculations within the application.
- **PyInstaller**: Used for packaging the Python application into standalone executable files.