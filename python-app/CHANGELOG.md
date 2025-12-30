# Changelog

## v3.46.0
- FIX: Target Profit BACK usa formula corretta: stake = target / (price - 1)
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
