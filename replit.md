# Pickfair - Betfair Exchange Trading Application

## Overview

Pickfair is a Python-based Windows desktop application for automated sports betting on the Betfair Exchange Italy API. It focuses on advanced dutching, live betting, and automated bet placement, integrated with Telegram for signal processing. The application offers a modern, dark-themed trading experience, multi-market monitoring, simulation mode, and compliance with Italian commission handling. Its ambition is to provide a robust, efficient, and user-friendly platform for sophisticated sports trading strategies.

## User Preferences

- Communication: Simple, everyday language (Italian)
- Design: Dark trading interface with professional aesthetics
- No popup windows: All functionality must be in tabs
- Colors: BACK (blue #1e88e5), LAY (pink #e5399b), profit (green), loss (red)

## System Architecture

### UI/UX Decisions
The application features a tab-based, dark-themed trading interface built with `CustomTkinter` and `ttk` for modern and complex widgets. A consistent dark theme is enforced via `theme.py`, with specific color assignments for BACK bets (blue), LAY bets (pink), profit (green), and loss (red).

### Technical Implementations
The core application is managed by `main.py`, interacting with `betfair_client.py` for API calls, `betfair_stream.py` for real-time market data (MCM) and order updates (OCM), and `telegram_listener.py` for signal processing. `database.py` handles SQLite operations, and `dutching.py` performs complex dutching calculations. Key features include:

-   **Advanced Dutching**: Supports mixed BACK/LAY bets with features like best odds highlighting, auto-removal of suspended runners, book percentage warnings, P&L preview, preset stakes, and AI Mixed Auto-Entry for automatic BACK/LAY classification. It includes budget protection, profit verification, and a profit variance guard.
-   **Auto-Green Toggle**: Automatically manages order green-up after a grace period.
-   **Simulation Mode**: Allows strategy testing without real money, indicated by a red banner and blocking real order submission.
-   **Real-time Live Betting**: Provides live odds and quick bet placement.
-   **Telegram Integration**: Monitors chats for betting signals, supporting various market types and enabling auto-betting and copy trading (Master/Follower modes).
-   **Cashout Management**: Manual and automatic cashout options.
-   **Multi-Market Monitoring**: Tracks multiple events via a Market Watch List.
-   **Automatic Result Settlement**: Tracks and settles bets, updating statistics.
-   **Custom Parsing Rules**: User-defined regex for Telegram signal processing.
-   **Plugin System**: A secure, sandboxed environment for custom scripts with a defined API.
-   **My Bets Panel**: Real-time order tracking with P&L display and inline actions (Cancel, Replace, Green-up).
-   **Trading Automation PRO**: Includes a P&L Engine, Stop Loss/Take Profit, Trailing Stop, Partial Green, Tick Storage, and a Simulation Broker with exchange-realistic behavior. Features a Book Optimizer for stake redistribution and a Tick Replay Engine for backtesting.
-   **Safety Package**: Comprehensive validation including `MarketValidator` for dutching compatibility, `Profit Uniformity Validation` (with a €0.50 tolerance), `Auto-Green Security` with grace periods, and `Safety Logger` for auditing.
-   **Safe Mode Manager**: Automatically disables trading functions after consecutive errors, with a manual reset option.
-   **Performance Optimizations**: Features a `Tick Dispatcher` for coalescing UI updates, `P&L Cache` and `Dutching Cache` for calculation speedup, `Automation Optimizer` for early exit checks, `UI Optimizer` for diff-based updates, and `Simulation Speed Controller` for multi-speed simulations.
-   **PRO UI Components**: `DutchingController` as a unified orchestrator, `AIPatternEngine` for Weight of Money (WoM) analysis, `MiniLadder` for inline odds display, and `DraggableRunner` for reordering runners.
-   **Preflight Check + Dry Run**: A pre-order validation system checking stake, liquidity, spread, price, and book percentage, with a `dry_run` parameter for previewing orders.
-   **Enterprise WoM + One-Click**: `WoM Engine` for historical tick analysis with time-window aggregation, `Enhanced AI Analysis` combining instant and historical WoM, and `OneClickLadder` for single-click order placement with preflight validation.
-   **Toolbar + Live UI (v3.66)**: Advanced `Toolbar` with Simulation/Auto-Green/AI Mixed toggles, market status indicator, and preset stake buttons. `LiveMiniLadder` with 500ms auto-refresh, [BACK]/[LAY] badges from WoM, P&L preview inline, and best price highlight. Controller supports `auto_green_enabled`, `ai_enabled`, `preset_stake_pct` flags. PnL Engine adds `calculate_preview()` for pre-order estimation.
-   **v3.67 Advanced Features**:
    - **WoM Time-Window Engine**: Multi-timeframe WoM analysis (5s/15s/30s/60s), delta pressure, momentum, volatility. Thread-safe snapshot-based calculations.
    - **AI Guardrail**: Protection system with market readiness check, WoM data validation, auto-green grace period (3s), order rate limiting (10/min), consecutive error circuit breaker.
    - **One-Click MiniLadder**: Click → preflight → submit with auto-green support.
-   **v3.68 Liquidity Guard**:
    - Configurable liquidity protection: blocks orders when available liquidity < stake × multiplier (default 3x)
    - Absolute minimum check: €50 required (prevents dead market orders)
    - Warning-only mode available for experienced traders
    - Test suite: 121 tests passed (72 core + 37 WoM/Guardrail + 12 Liquidity Guard).
-   **v3.71 Antifreeze Architecture**:
    - **BetfairExecutor**: Singleton `ThreadPoolExecutor(max_workers=1)` for ALL Betfair API calls, ensuring serialized execution.
    - **UIWatchdog**: Monitors main thread responsiveness with 15s timeout; dumps all thread stacks on freeze detection.
    - **poll_future()**: Non-blocking Future handling via Tkinter mainloop - UI never blocks on API calls.
    - **guarded() decorator**: Blocks order operations when safe mode is active, applied before executor submission.
    - **ZERO main-thread API calls**: All Betfair calls go through `_execute_order_operation()` or `_execute_betfair_call()`.
-   **v3.72 Live Match Timeline**:
    - **MatchTimeline Widget**: Trading-style progress bar showing minute, injury time, and goal markers with tooltips.
    - **API-Football Integration**: Background worker polls live match data every 15s (uses `API_FOOTBALL_KEY` secret).
    - **HardSyncController**: Betfair = MASTER, API-Football = sensor. Trading decisions never based on API-Football.
    - **LiveContext**: Thread-safe cache for match data between API thread and UI.
    - **Goal Alerts**: Visual flash + optional sound on goal detection (anti-spam, single trigger per goal).
    - **Dynamic Colors**: Green (normal), Orange (danger/late game), Red (suspended), Gray (N/A).
    - **Fuzzy Team Matching**: Handles U21/Women/friendly naming differences automatically.
-   **v3.73 Follower PRO**:
    - **Fixed Stake Mode**: Follower can use fixed stake instead of percentage, configurable in Telegram settings.
    - **CycleManager**: Thread-safe P&L tracking with target (+5% default) and stop (-3% default) thresholds.
    - **Cycle Gate**: Blocks COPY_BET/COPY_CASHOUT/COPY_DUTCHING signals when cycle threshold reached (TARGET_HIT or STOPPED).
    - **Persistence**: Cycle state persisted to `follower_cycle_state` table, survives app restart.
    - **UI Controls**: Stake type selector, cycle enable toggle, target/stop inputs, live status display, reset button.
    - **Callback System**: Notifies user with dialog when cycle ends (target hit or stopped).
    - **Test Suite**: 48 tests (38 core + 10 CycleManager).

### Frozen API Signatures (v3.66-enterprise)
**DO NOT MODIFY** - Core dutching signatures are frozen for stability:
```python
# Dutching Core
calculate_dutching_stakes(selections, total_stake, bet_type="BACK", commission=0.0) -> Tuple[List[Dict], float, float]
calculate_mixed_dutching(selections, total_stake, commission=0.0) -> Tuple[List[Dict], float, float]
dynamic_cashout_single(back_stake, back_price, lay_price, commission=4.5) -> Dict  # returns: lay_stake, net_profit, profit_if_wins, profit_if_loses

# Simulation Broker
SimulationBroker.place_order(selection_id, price, stake, side, order_type) -> str
SimulationBroker.orders: Dict[str, SimulatedOrder]
```

### System Design Choices
Data is stored in an SQLite database (`pickfair.db`) within `%APPDATA%\Pickfair`, including application data, Telegram session files, plugin configurations, and license keys. `theme.py` provides consistent dark theming. The database uses persistent connections, retry mechanisms, and WAL mode for concurrency. The plugin system incorporates code validation, sandboxing, timeout protection, and thread-safe execution for security.

## External Dependencies

-   **customtkinter**: For the modern GUI.
-   **betfairlightweight**: Python wrapper for the Betfair Exchange API.
-   **telethon**: For Telegram client operations.
-   **numpy**: For various numerical calculations.
-   **PyInstaller**: For packaging the application into executables.