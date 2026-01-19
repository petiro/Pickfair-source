# Pickfair - Betfair Exchange Trading Application

## Overview

Pickfair is a Python-based Windows desktop application designed for automated sports betting on the Betfair Exchange Italy API. Its primary purpose is to offer a robust, efficient, and user-friendly platform for sophisticated sports trading strategies, focusing on advanced dutching, live betting, and automated bet placement. The application integrates with Telegram for signal processing and features a modern, dark-themed trading experience, multi-market monitoring, a simulation mode, and compliance with Italian commission handling.

## User Preferences

- Communication: Simple, everyday language (Italian)
- Design: Dark trading interface with professional aesthetics
- No popup windows: All functionality must be in tabs
- Colors: BACK (blue #1e88e5), LAY (pink #e5399b), profit (green), loss (red)

## System Architecture

### UI/UX Decisions
The application utilizes `CustomTkinter` and `ttk` for a tab-based, dark-themed trading interface, ensuring a modern and consistent aesthetic. Specific color assignments are used for BACK bets (blue), LAY bets (pink), profit (green), and loss (red).

### Technical Implementations
The core application (`main.py`) manages interactions with the Betfair API (`betfair_client.py`), real-time market data and order updates (`betfair_stream.py`), Telegram signal processing (`telegram_listener.py`), and SQLite database operations (`database.py`). Key features include:

-   **Advanced Dutching**: Supports mixed BACK/LAY bets with features like best odds highlighting, auto-removal of suspended runners, book percentage warnings, P&L preview, preset stakes, and AI Mixed Auto-Entry.
-   **Simulation Mode**: Allows strategy testing without real money.
-   **Real-time Live Betting**: Provides live odds and quick bet placement.
-   **Telegram Integration**: Monitors chats for betting signals, supporting auto-betting and copy trading (Master/Follower modes), with follower enhancements like fixed stake mode and cycle management.
-   **Cashout Management**: Manual and automatic cashout options.
-   **Multi-Market Monitoring**: Tracks multiple events via a Market Watch List.
-   **Automatic Result Settlement**: Tracks and settles bets, updating statistics.
-   **Plugin System**: A secure, sandboxed environment for custom scripts.
-   **My Bets Panel**: Real-time order tracking with P&L display and inline actions.
-   **Trading Automation PRO**: Includes a P&L Engine, Stop Loss/Take Profit, Trailing Stop, Partial Green, Tick Storage, Simulation Broker, Book Optimizer, and Tick Replay Engine.
-   **Safety Package**: Comprehensive validation including market and profit uniformity validation, auto-green security, and a safety logger.
-   **Safe Mode Manager**: Automatically disables trading functions after consecutive errors.
-   **Performance Optimizations**: Features a Tick Dispatcher, P&L/Dutching Caches, Automation Optimizer, UI Optimizer, and Simulation Speed Controller.
-   **PRO UI Components**: DutchingController, AIPatternEngine for Weight of Money (WoM) analysis, MiniLadder, and DraggableRunner.
-   **Preflight Check + Dry Run**: A pre-order validation system checking stake, liquidity, spread, price, and book percentage.
-   **Enterprise WoM + One-Click**: WoM Engine for historical tick analysis, enhanced AI analysis, and OneClickLadder for single-click order placement.
-   **Liquidity Guard**: Configurable protection that blocks orders when liquidity is insufficient.
-   **Antifreeze Architecture**: Ensures UI responsiveness through serialized Betfair API calls via `BetfairExecutor`, UI watchdog, non-blocking future handling (`poll_future()`), and a `guarded()` decorator for safe operations. Includes circuit breakers, rate limiters, a prioritized UI queue (`UIUpdateQueue`), health monitoring, and graceful shutdown procedures. Non-blocking implementations for stream stops, Telegram operations, and database access prevent UI freezes. Thread guards (`@assert_not_ui_thread`) prevent critical API methods from being called on the main UI thread. **CRITICAL**: All UI updates from background threads MUST use `self.uiq.post(fn, key="...", debug_name="...")` - NEVER `root.after(0, ...)` which can cause race conditions and freezes. Exception: `root.after(delay, fn)` is OK on main thread for scheduling future work.
-   **Live Match Timeline**: Integrates with API-Football to display real-time match progress, goal markers, and dynamic color-coded states on a MatchTimeline widget, without influencing trading decisions directly.

### System Design Choices
Data is persistently stored in an SQLite database (`pickfair.db`) using WAL mode for concurrency, located in `%APPDATA%\Pickfair`. This includes application data, Telegram session files, plugin configurations, and license keys. The plugin system emphasizes security through sandboxing and thread-safe execution.

## External Dependencies

-   **customtkinter**: For the modern GUI.
-   **betfairlightweight**: Python wrapper for the Betfair Exchange API.
-   **telethon**: For Telegram client operations.
-   **numpy**: For numerical calculations.
-   **PyInstaller**: For packaging the application.