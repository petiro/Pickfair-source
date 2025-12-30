# Changelog

## v3.58.3
- TUNING H24 SAFE: soglie ottimizzate per Betfair Exchange live
- API calls target < 60/min (Betfair attenzione sopra 80-100)
- Replace threshold NORMAL 0.15€ (target replace_rate 15-30%)
- HIGH polling 3.5s (era 3.0s), threshold 0.30€
- Loop latency target < 50ms, telegram queue < 3
- Commenti documentazione KPI target nel codice

## v3.58.2
- NUOVO: WAL checkpoint automatico ogni 12 minuti
- Esegue PRAGMA wal_checkpoint(TRUNCATE) per ridurre file WAL
- Checkpoint finale a shutdown applicazione
- Evita file WAL enormi in sessioni h24

## v3.58.1
- FIX: Isteresi auto_throttle per evitare oscillazioni (ping-pong)
- NORMAL -> HIGH: condizione deve persistere 5 secondi
- HIGH -> NORMAL: deve migliorare per 10 secondi
- CRITICAL: transizione immediata (emergenza)
- Stats mostra stato isteresi pendente

## v3.58.0
- NUOVO: Moduli separati per ottimizzazione performance
- order_manager.py: Replace intelligenti con profit threshold, guard rail anti-loop
- market_tracker.py: Cache market book + delta detection (riduce API calls)
- telegram_sender.py: Rate limit adattivo (aumenta dopo FloodWait, diminuisce dopo successi)
- auto_throttle.py: Regolatore automatico carico (polling, replace, telegram adattivi)
- database.py: WAL mode + 11 indici ottimizzati per query frequenti
- Metriche dettagliate per ogni modulo

## v3.57.4
- NUOVO: Tab "Performance" nella Dashboard con metriche sistema
- MarketCache per ridurre API calls Betfair (hit rate, calls risparmiate)
- PerformanceMetrics: API calls/min, latenza media, replace eseguiti/saltati
- Rate limit Telegram dinamico (adattivo in base a FloodWait)
- Metriche coda Telegram e tempo elaborazione

## v3.57.3
- NUOVO: Grafico Equity Curve nel tab "Storico P&L" (matplotlib)
- Curva cumulativa profitti con area colorata (verde/rosso)
- Linea zero di riferimento

## v3.57.2
- INTEGRAZIONE: bet_logger in telegram_listener per tutti i segnali (COPY_BET, COPY_CASHOUT, COPY_DUTCHING, BOOKING, segnali normali)
- INTEGRAZIONE: bet_logger in cashout handler (log_cashout_completed, log_cashout_failed)
- Logging tempo di processing per ogni segnale Telegram (ms)

## v3.57.1
- FIX: Integrato bet_logger in order_manager per logging ordini replaced
- FIX: rebuild_daily_pnl() ricostruisce P&L giornaliero da bet_history
- FIX: Aggiunto close_connection() per cleanup thread-safe
- FIX: P&L giornaliero calcolato automaticamente al refresh dashboard

## v3.57.0
- NUOVO: Sistema persistenza completo con SQLite (storage.py, bet_logger.py)
- Database tabelle: bet_history, telegram_audit, error_log, cashout_history, open_positions
- Dashboard nuovi tab: Storico P&L, Audit Telegram, Log Errori
- Recovery automatico posizioni aperte dopo riavvio
- Logging completo: ogni scommessa, cashout, errore salvato su DB
- KPI da database: scommesse totali, P&L, win rate, commissioni
- P&L giornaliero ultimi 30 giorni
- Esportazione CSV log errori
- Risolto: cronologia non si perde piu dopo riavvio

## v3.56.0
- NUOVO: In MIXED mode, ogni risultato mostra 2 righe: [B] BACK e [L] LAY
- Colori distinti: sfondo blu chiaro per BACK, rosa chiaro per LAY
- Selezione indipendente per ogni riga
- Interfaccia più intuitiva per dutching misto

## v3.55.6
- FIX: Minimo stake 1€ per selezione (dal febbraio 2022)

## v3.55.5
- FIX: Betfair Italia - minimo 2€ PER SELEZIONE (ERRATO, era vecchia regola)

## v3.55.4
- REVERT: Minimo era 2€ corretto (regola Betfair Italia)

## v3.55.3
- FIX: Auto-updater ora forza chiusura istanza precedente con taskkill

## v3.55.2
- FIX: Validazione pre-piazzamento dutching
- Controllo stake minimo >= 2€ (Betfair Italia)
- Controllo payout massimo <= 10.000€
- Normalizzazione prezzo al tick ladder Betfair
- Messaggi errore dettagliati con codici Betfair

## v3.55.1
- FIX: Cartella logs creata correttamente in %APPDATA%\Pickfair\logs\

## v3.55.0
- NUOVO: Pulsante MIXED nella finestra Dutching
- Permette selezioni miste BACK + LAY individualmente
- Doppio-click su riga per cambiare BACK ↔ LAY
- Indicatore mostra conteggio: "MIXED: 2B + 1L"
- Calcolo dutching misto con calculate_mixed_dutching()

## v3.54.0
- COPY DUTCHING: Messaggio unificato per dutching in Copy Trading
- _broadcast_copy_dutching() invia un solo messaggio con tutte le selezioni
- parse_copy_dutching() parsa il messaggio unificato dal Master
- _process_telegram_copy_dutching() processa e piazza dutching nel Follower
- Follower ricalcola stake con quote correnti e profit target
- Supporto BACK, LAY, MIXED dutching in Copy Trading
- Logging completo con prefisso [COPY DUTCHING]

## v3.53.0
- NUOVO: bot_logger.py - Sistema logging professionale
- RotatingFileHandler: 5 file x 10MB con rotazione automatica
- Console colorata + file completo
- TelegramHandler per alert critici con rate limit
- ModuleLogger con prefissi dedicati per ogni modulo
- AuditLogger per tracciamento operazioni trading (CSV-like)
- PerfLogger per misurare tempi esecuzione
- Log salvati in %APPDATA%\Pickfair\logs

## v3.52.0
- Logging COMPLETO per debug e troubleshooting
- Log dettagliati per tutte le funzioni trading_engine_pro.py
- Traceback completi su eccezioni
- Log prefissi: [TICK], [MIXED], [SPREAD], [AUTO-FOLLOW], [TRAILING], [CASHOUT], [TG SEND], [TG WORKER], [ENGINE PRO]
- Metriche progressive e summary per ogni operazione

## v3.51.0
- NUOVO: trading_engine_pro.py - Engine Enterprise completo
- Mixed BACK+LAY Solver con NumPy (sistema lineare)
- Auto-follow Best Price con tick ladder dinamico
- Trailing Cashout: chiude su ritracciamento
- Telegram Broadcast con audit SQLite + retry + flood handling
- TradingEnginePro orchestratore che integra tutto
- Metriche delivery rate, failure rate, flood incidents

## v3.50.0
- UPGRADE PRO: order_manager.py completamente riscritto
- State Machine per betId (OrderStatus enum, anti-bug fantasma)
- Profit Delta Check: replace solo se Δprofit > soglia (0.10 EUR)
- Priority Engine: FRONT/BACK of queue
- Green Book Cashout: profitto uniforme multi-selezione
- OrderManagerPro con smart_replace completo
- batch_smart_replace() per operazioni multiple

## v3.49.0
- NUOVO: order_manager.py - Auto-Follow Best Price
- Tick Ladder Betfair ufficiale (normalize_price)
- ReplaceGuard anti-loop protection
- OrderManager con replaceOrders + fallback cancel+place
- Tracking automatico betId cambiati
- batch_follow_orders() per update multipli

## v3.48.0
- Migliorato replace_orders() con gestione nuovo betId
- NUOVO: replace_orders_batch() per modifiche multiple
- Tracciamento automatico betId cambiato su partial match
- Logging dettagliato per debug replace

## v3.47.0
- NUOVO: dynamic_cashout_single() per cashout singola posizione BACK
- Migliorato dynamic_cashout() multi-ordine con piu dettagli
- Formula corretta: lay_stake = (back_stake * back_price) / lay_price
- Profitto netto uniforme garantito con quote live
- Logging dettagliato per debug cashout

## v3.46.1
- FIX CRITICO: Target Profit ora calcola correttamente stake totale
- Formula: S = target / (1/sum(1/price) - 1), poi pesi 1/price
- Ora mettendo 20 EUR ottieni esattamente ~20 EUR netti su ogni selezione

## v3.46.0
- Nuova funzione calculate_back_target_profit() dedicata
- Profitto netto uniforme garantito con target profit fisso

## v3.45.0
- NUOVO: Indicatore visivo "MIXED MODE (xB + yL)" nel dutching
- Righe swappate evidenziate in arancione
- Tipo scommessa [BACK]/[LAY] mostrato nel nome runner
- Indicatore [B]/[L] nella colonna selezione

## v3.44.0
- NUOVO: Mixed BACK+LAY dutching con profitto NETTO uniforme
- Sistema lineare numpy per calcolo stake ottimali
- Supporto commissione Betfair nel calcolo misto
- Livello Bet Angel PRO grade

## v3.43.0
- FIX CRITICO: Dutching BACK ora usa profitto NETTO uniforme
- Formula corretta: peso = 1/price (non 1/(price-1))
- Profitto = (stake * price - stake_totale) * (1 - commissione)
- Ora il profitto e' IDENTICO su qualsiasi selezione vinca

## v3.42.3
- Version sync and build trigger

## v3.42.2
- Version sync with git tag

## v3.42.1
- Fix LAY dutching: use min() for guaranteed profit instead of average

## v3.42.0
- Complete rewrite of dutching engine
- Uniform profit BACK dutching with correct 1/(price-1) formula
- LAY dutching with proportional liability distribution
- Mixed BACK+LAY dutching via linear system (Bet Angel PRO level)
- Partial fill & slippage recalculation
- Multi-market swap hedging
- Live dynamic cashout & green-up
- Betfair-compliant stake/winnings validation (1 EUR min, 10000 EUR max)

## v3.41.1
- Add detailed dutching logging for debugging

## v3.41.0
- Fix Copy Trading broadcast IndexError on empty instructionReports
- Fix dutching BACK formula from 1/price to 1/(price-1) for true equal profit

## v3.40.6
- Hardware-based licensing system
- Telegram signal processing with dutching support
- Copy Trading with Master/Follower modes
- Real-time order tracking via Betfair Stream API
- Simulation mode
- Plugin system with secure sandbox
