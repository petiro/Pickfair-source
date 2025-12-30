# Changelog

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
