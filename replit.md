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
    - Profit variance guard: raises ValueError if profit variance > PROFIT_EPSILON (€0.50) after normalization
  - **Auto-Green Toggle**: UI toggle that adds metadata to orders for downstream monitoring
    - Grace period (AUTO_GREEN_DELAY_SEC = 2.5s) before auto-green becomes eligible
    - is_auto_green_eligible() checks elapsed time from placed_at timestamp
  - **Simulation Mode Toggle**: Test strategies without real money risk
    - Red banner in header when active
    - Blocks real order submission when enabled
    - DutchingState.simulation_mode property for backend sync
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
- **Safety Package (v3.61)**: Comprehensive validation and security safeguards
  - **MarketValidator** (`market_validator.py`): Validates markets for dutching compatibility
    - DUTCHING_READY: MATCH_ODDS, WINNER, CORRECT_SCORE, MONEYLINE
    - NON_DUTCHING: OVER_UNDER, ASIAN_HANDICAP, CORNER_MATCH_BET, BTTS
    - UI auto-blocks AI Mixed for incompatible market types with warning banner
  - **Profit Uniformity Validation**: `_validate_uniform_profit()` ensures equal profit distribution
    - PROFIT_EPSILON = €0.50 max variance tolerance
    - Raises `MixedDutchingError` if variance exceeds threshold
  - **Auto-Green Security**: `should_auto_green()` with multi-layer protection
    - Requires valid `placed_at` timestamp (blocks if missing/zero/None)
    - Enforces 2.5s grace period (AUTO_GREEN_DELAY_SEC)
    - Blocks in simulation mode or when market not OPEN
  - **Test Coverage**: 33 pytest tests in `tests/test_dutching_safety.py`
    - Market validation (9 tests)
    - Profit uniformity (6 tests)
    - Auto-green delay/simulation/status/flag (15 tests)
    - AI Mixed stakes (3 tests)
- **Safety Logger** (`safety_logger.py`): Automatic logging for audit and debugging
  - Thread-safe singleton with daily log rotation
  - Logs to `%APPDATA%/Pickfair/logs/safety_YYYYMMDD.log`
  - Event types: MIXED_DUTCH_ERR, AI_BLOCKED, AUTO_GREEN_DENIED, SAFE_MODE
  - Dedicated methods: `log_mixed_dutching_error()`, `log_ai_blocked()`, `log_auto_green_denied()`, `log_safe_mode_triggered()`
- **Safe Mode Manager** (`safe_mode.py`): Auto-disable after consecutive errors
  - Triggers after 2 consecutive errors (CONSECUTIVE_ERRORS_THRESHOLD)
  - `report_error()` increments counter, triggers safe mode if threshold reached
  - `report_success()` resets error counter
  - `reset()` for manual recovery (requires explicit user action)
  - Callback registration for UI integration (auto-disable AI toggle)
- **Performance Tests** (`tests/test_performance.py`): Load and stress testing
  - 100 dutching calculations in <1s
  - 100 AI Mixed attempts in <5s
  - 50 concurrent calculations thread-safe
  - 1000 tick updates with auto-green in <0.5s
  - 1000+ log entries under load
  - Concurrent error reporting thread-safety
  - Full workflow stress test (69 tests total, 68 pass + 1 intentional skip)
- **Performance Optimizations (v3.62)**: Enterprise-grade performance enhancements
  - **Tick Dispatcher** (`tick_dispatcher.py`): Tick coalescing with separate consumer throttling
    - UI updates max 4/sec (MIN_UPDATE_INTERVAL = 250ms)
    - Storage receives 100% of ticks at full speed
    - Simulation mode with extended intervals
  - **P&L Cache** (`pnl_cache.py`): Short-circuit and caching for P&L calculations
    - Hash-based cache key from prices and orders
    - TTL-based invalidation (CACHE_TTL = 5.0s)
    - Short-circuit returns zero immediately when no orders
  - **Dutching Cache** (`dutching_cache.py`): LRU cache for repeated calculations
    - Hash key from (prices, stake, side, commission)
    - MAX_CACHE_SIZE = 100 entries with LRU eviction
    - 10× speedup on repeated calculations
  - **Automation Optimizer** (`automation_optimizer.py`): Early exit stack
    - 6 ordered checks from cheapest to most expensive
    - SIM_CHECK_INTERVAL = 1.0s for simulation (throttled, not blocked)
    - MIN_CHECK_INTERVAL = 0.1s for live trading
    - Reduces automation evaluations by ~50%
  - **UI Optimizer** (`ui_optimizer.py`): Diff-based updates
    - Skip .configure() when value unchanged
    - Float tolerance comparison (FLOAT_TOLERANCE = 0.001)
    - Widget state tracking with hash comparison
    - Zero flicker, pro-level reactivity
  - **Simulation Speed Controller** (`simulation_speed.py`): Multi-speed simulation
    - Realtime (1×), Fast (5×), Ultra Fast (10×) modes
    - Separate buffers for UI and automation throttling
    - Time compression for sleeps
- **PRO UI Components (v3.63)**: Advanced UI/AI features
  - **DutchingController** (`controllers/dutching_controller.py`): Unified orchestrator
    - Single entry point for all dutching operations
    - Coordinates UI → validation → AI → dutching → broker
    - Safe mode integration with automatic blocking
    - Market validation before AI operations
    - Auto-registers SL/TP/Trailing for placed orders
  - **AIPatternEngine** (`ai/ai_pattern_engine.py`): Weight of Money analysis
    - Calculates WoM = back_vol / (back_vol + lay_vol)
    - WoM > 0.55 → BACK, WoM < 0.45 → LAY, else BACK default
    - Forces at least 1 BACK + 1 LAY for mixed dutching
    - `get_wom_analysis()` for UI preview with confidence scores
  - **MiniLadder** (`ui/mini_ladder.py`): Inline ladder display
    - Shows 3 BACK + 3 LAY levels per runner
    - Best price highlighted with distinct color
    - Click-on-price for quick selection
    - Real-time updates via `update_prices()`
  - **DraggableRunner** (`ui/draggable_runner.py`): Drag & drop reordering
    - Visual drag with lift and cursor feedback
    - `on_move` callback for order updates
    - `DraggableRunnerList` container for auto-management
    - Zero impact on dutching calculations
  - **Test Coverage**: 24 additional tests in `tests/test_new_components.py`
    - AIPatternEngine WoM calculations (8 tests)
    - DutchingController validation and submit (9 tests)
    - MiniLadder/DraggableRunner data structures (4 tests)
    - Integration tests (3 tests)

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