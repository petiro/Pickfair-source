# Changelog

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
