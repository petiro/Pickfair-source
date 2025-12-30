"""
Dutching Engine per Betfair Exchange.
Calcolo stake ottimali per profitto uniforme su multiple selezioni.
"""

import logging
from typing import List, Dict, Tuple

logger = logging.getLogger(__name__)

# Costanti Betfair Italia
MIN_STAKE = 1.0
MAX_WIN = 10000.0


def validate_selections(results: List[Dict]) -> List[str]:
    """Valida selezioni per requisiti Betfair."""
    errors = []
    for r in results:
        if r.get("stake", 0) < MIN_STAKE:
            errors.append(f"Stake troppo basso: {r.get('stake', 0):.2f} < {MIN_STAKE}")
        
        win = r.get("profit_if_win", r.get("profitIfWins", 0))
        if win > MAX_WIN:
            errors.append(f"Vincita massima superata: {win:.2f} > {MAX_WIN}")
    
    return errors


def calculate_dutching_stakes(
    selections: List[Dict],
    total_stake: float,
    side: str = "BACK",
    commission: float = 4.5
) -> Tuple[List[Dict], float, float]:
    """
    Entry point principale per calcolo dutching.
    
    Args:
        selections: Lista di {'selectionId', 'runnerName', 'price'}
        total_stake: Stake totale da distribuire
        side: 'BACK' o 'LAY'
        commission: Commissione Betfair (default 4.5% Italia)
    
    Returns:
        Tuple (results, uniform_profit, implied_probability)
    """
    logger.info(f"[DUTCHING] calculate_dutching_stakes: side={side}, total_stake={total_stake}, selections={len(selections)}")
    for s in selections:
        logger.info(f"[DUTCHING] Input: {s.get('runnerName', 'N/A')} @ {s.get('price', 0)}")
    
    if not selections:
        raise ValueError("Nessuna selezione")
    
    if total_stake <= 0:
        raise ValueError("Stake deve essere positivo")
    
    valid_selections = [s for s in selections if s.get('price') and s['price'] > 1.0]
    
    if not valid_selections:
        raise ValueError("Nessuna quota valida")
    
    if side == "BACK":
        return _calculate_back_dutching(valid_selections, total_stake, commission)
    elif side == "LAY":
        return _calculate_lay_dutching(valid_selections, total_stake, commission)
    else:
        raise ValueError("side deve essere BACK o LAY")


def _calculate_back_dutching(
    selections: List[Dict],
    total_stake: float,
    commission: float = 4.5
) -> Tuple[List[Dict], float, float]:
    """
    BACK Dutching - Profitto NETTO Uniforme.
    
    Formula corretta per profitto netto identico:
    - Peso: w_i = 1/price_i
    - Stake: stake_i = total_stake * w_i / sum(w_j)
    - Profitto netto se vince i = (stake_i * price_i - total_stake) * (1 - commission%)
    
    Questa formula garantisce che vincendo QUALSIASI selezione,
    il profitto netto (dopo commissione e sottratti gli stake persi) sia identico.
    """
    commission_mult = 1 - (commission / 100.0)
    
    # Pesi inversi: 1/price (NON 1/(price-1)!)
    # Questo equalizza il RITORNO (stake*price), non il profitto lordo
    inv_weights = []
    for sel in selections:
        weight = 1.0 / sel['price'] if sel['price'] > 0 else 0
        inv_weights.append(weight)
    
    weight_sum = sum(inv_weights)
    if weight_sum <= 0:
        raise ValueError("Quote non valide per dutching")
    
    # Verifica: somma pesi > 1 significa dutching non profittevole (over-round)
    implied_prob = weight_sum * 100
    is_profitable = weight_sum < 1.0
    
    results = []
    net_profits = []
    
    for i, sel in enumerate(selections):
        stake = total_stake * inv_weights[i] / weight_sum
        stake = round(stake, 2)
        
        # Profitto NETTO = ritorno se vince - stake totale investito
        gross_return = stake * sel['price']
        gross_profit = gross_return - total_stake  # Sottrae TUTTI gli stake!
        
        # Commissione solo se profitto positivo
        if gross_profit > 0:
            net_profit = gross_profit * commission_mult
        else:
            net_profit = gross_profit
        
        results.append({
            'selectionId': sel['selectionId'],
            'runnerName': sel['runnerName'],
            'price': sel['price'],
            'stake': stake,
            'side': 'BACK',
            'grossProfit': round(gross_profit, 2),
            'profitIfWins': round(net_profit, 2),
            'potentialReturn': round(gross_return, 2),
            'impliedProbability': round((1.0 / sel['price']) * 100, 2)
        })
        net_profits.append(net_profit)
    
    # Correggi arrotondamento per mantenere stake totale esatto
    actual_total = sum(r['stake'] for r in results)
    diff = round(total_stake - actual_total, 2)
    if diff != 0 and results:
        max_idx = max(range(len(results)), key=lambda i: results[i]['stake'])
        results[max_idx]['stake'] = round(results[max_idx]['stake'] + diff, 2)
        # Ricalcola profitto per lo stake aggiustato
        adjusted_return = results[max_idx]['stake'] * results[max_idx]['price']
        adjusted_gross = adjusted_return - total_stake
        adjusted_net = adjusted_gross * commission_mult if adjusted_gross > 0 else adjusted_gross
        results[max_idx]['grossProfit'] = round(adjusted_gross, 2)
        results[max_idx]['profitIfWins'] = round(adjusted_net, 2)
        results[max_idx]['potentialReturn'] = round(adjusted_return, 2)
        net_profits[max_idx] = adjusted_net
    
    uniform_profit = round(min(net_profits), 2)
    
    # Log risultati dettagliati
    logger.info(f"[DUTCHING] BACK Dutching - Profitto NETTO Uniforme")
    logger.info(f"[DUTCHING] Total stake: {total_stake:.2f}, Implied prob: {implied_prob:.1f}%, Profitable: {is_profitable}")
    for r in results:
        logger.info(f"[DUTCHING] {r['runnerName']} @ {r['price']:.2f} -> stake={r['stake']:.2f}, net_profit={r['profitIfWins']:.2f}")
    logger.info(f"[DUTCHING] Uniform NET profit: {uniform_profit:.2f}")
    
    return results, uniform_profit, round(implied_prob, 2)


def calculate_back_target_profit(
    selections: List[Dict],
    target_profit: float,
    commission: float = 4.5
) -> Tuple[List[Dict], float, float]:
    """
    BACK Dutching con Target Profit Fisso - Formula CORRETTA.
    
    "Voglio esattamente +X EUR netti qualunque selezione vinca"
    
    Formula matematica:
        1. Calcola somma pesi: sum_w = sum(1/price_i)
        2. Stake totale necessario: S = target / (1/sum_w - 1)
        3. Stake per selezione: stake_i = S * (1/price_i) / sum_w
        4. Profitto netto = target * (1 - commission)
    
    Garantisce profitto NETTO identico su TUTTE le selezioni.
    """
    commission_mult = 1 - (commission / 100.0)
    
    if not selections:
        raise ValueError("Nessuna selezione")
    
    valid_selections = [s for s in selections if s.get('price') and s['price'] > 1.0]
    if not valid_selections:
        raise ValueError("Nessuna quota valida")
    
    # Calcola somma pesi (implied probability decimale)
    weights = [1.0 / sel['price'] for sel in valid_selections]
    sum_weights = sum(weights)
    
    # Verifica: sum_weights deve essere < 1 per avere profitto
    if sum_weights >= 1.0:
        raise ValueError(f"Book value >= 100% ({sum_weights*100:.1f}%), profitto non garantito")
    
    # Calcola stake totale necessario per ottenere target_profit
    # Formula: profit = S * (1/sum_w) - S = S * (1/sum_w - 1)
    # Quindi: S = profit / (1/sum_w - 1)
    gross_target = target_profit / commission_mult  # Target lordo per compensare commissione
    total_stake = gross_target / (1.0 / sum_weights - 1)
    
    logger.info(f"[DUTCHING] BACK Target Profit: target_net={target_profit:.2f}, gross={gross_target:.2f}")
    logger.info(f"[DUTCHING] sum_weights={sum_weights:.4f}, total_stake_needed={total_stake:.2f}")
    
    results = []
    
    for i, sel in enumerate(valid_selections):
        price = sel['price']
        # Stake proporzionale al peso
        stake = total_stake * weights[i] / sum_weights
        stake = round(stake, 2)
        
        # Profitto lordo = ritorno - stake totale
        gross_return = stake * price
        
        results.append({
            'selectionId': sel['selectionId'],
            'runnerName': sel['runnerName'],
            'price': price,
            'stake': stake,
            'side': 'BACK',
            'grossProfit': 0,  # Calcolato dopo
            'profitIfWins': 0,  # Calcolato dopo
            'potentialReturn': round(gross_return, 2),
            'impliedProbability': round((1.0 / price) * 100, 2)
        })
    
    # Correggi arrotondamento per mantenere stake totale esatto
    actual_total = sum(r['stake'] for r in results)
    diff = round(total_stake - actual_total, 2)
    if diff != 0 and results:
        max_idx = max(range(len(results)), key=lambda i: results[i]['stake'])
        results[max_idx]['stake'] = round(results[max_idx]['stake'] + diff, 2)
        actual_total = sum(r['stake'] for r in results)
    
    # Ricalcola profitto netto effettivo per ogni selezione
    net_profits = []
    for r in results:
        gross_profit = r['stake'] * r['price'] - actual_total
        net_profit = gross_profit * commission_mult if gross_profit > 0 else gross_profit
        r['grossProfit'] = round(gross_profit, 2)
        r['profitIfWins'] = round(net_profit, 2)
        net_profits.append(net_profit)
    
    uniform_profit = round(min(net_profits), 2)
    implied_prob = sum_weights * 100
    
    logger.info(f"[DUTCHING] Total stake: {actual_total:.2f}, Uniform net profit: {uniform_profit:.2f}")
    for r in results:
        logger.info(f"[DUTCHING] {r['runnerName']} @ {r['price']:.2f} -> stake={r['stake']:.2f}, net={r['profitIfWins']:.2f}")
    
    return results, uniform_profit, round(implied_prob, 2)


def _calculate_lay_dutching(
    selections: List[Dict],
    total_liability: float,
    commission: float = 4.5
) -> Tuple[List[Dict], float, float]:
    """
    LAY Dutching - Distribuzione proporzionale della liability.
    
    Formula: liability_i = total_liability * (1/(price_i-1)) / sum(1/(price_j-1))
    stake_i = liability_i / (price_i - 1)
    """
    commission_mult = 1 - (commission / 100.0)
    
    weights = []
    for sel in selections:
        weight = 1.0 / (sel['price'] - 1) if sel['price'] > 1 else 0
        weights.append(weight)
    
    weight_sum = sum(weights)
    if weight_sum <= 0:
        raise ValueError("Quote non valide per LAY dutching")
    
    results = []
    
    for i, sel in enumerate(selections):
        liability = total_liability * weights[i] / weight_sum
        stake = liability / (sel['price'] - 1) if sel['price'] > 1 else 0
        stake = round(stake, 2)
        liability = round(liability, 2)
        
        results.append({
            'selectionId': sel['selectionId'],
            'runnerName': sel['runnerName'],
            'price': sel['price'],
            'stake': stake,
            'side': 'LAY',
            'liability': liability,
            'profitIfLoses': round(stake * commission_mult, 2),
            'lossIfWins': liability,
            'potentialReturn': stake,
            'impliedProbability': round((1.0 / sel['price']) * 100, 2)
        })
    
    # Calcola profitto per ogni scenario
    total_stakes = sum(r['stake'] for r in results)
    
    profits_if_win = []
    for r in results:
        other_stakes = total_stakes - r['stake']
        gross_profit = (other_stakes - r['liability'])
        profits_if_win.append(gross_profit)
    
    # Profitto garantito uniforme: usa min() per worst case (non average!)
    theoretical_profit = min(profits_if_win) if profits_if_win else 0
    net_profit = theoretical_profit * commission_mult if theoretical_profit > 0 else theoretical_profit
    
    # Best case: tutti perdono
    best_case = total_stakes * commission_mult
    worst_case = net_profit
    
    for r in results:
        r['profitIfWins'] = round(net_profit, 2)
        r['grossProfit'] = round(theoretical_profit, 2)
        r['bestCase'] = round(best_case, 2)
        r['worstCase'] = round(worst_case, 2)
    
    implied_prob = sum(1.0 / sel['price'] for sel in selections) * 100
    
    return results, round(best_case, 2), round(implied_prob, 2)


def calculate_mixed_dutching(
    selections: List[Dict],
    target_profit: float,
    commission: float = 4.5
) -> Tuple[List[Dict], float, float]:
    """
    BACK + LAY Misto - Profitto NETTO Uniforme (Bet Angel PRO grade).
    
    Risolve sistema lineare per garantire lo STESSO profitto netto
    in TUTTI gli esiti, con mix di selezioni BACK e LAY.
    
    Args:
        selections: Lista di {'selectionId', 'runnerName', 'price', 'effectiveType': 'BACK'|'LAY'}
        target_profit: Profitto target desiderato
        commission: Commissione Betfair (default 4.5% Italia)
    
    Modello matematico:
        Per ogni esito k:
        sum(back_wins_k) - sum(lay_losses_k) - sum(back_stakes) + sum(lay_stakes) = TARGET
    """
    import numpy as np
    
    if not selections:
        raise ValueError("Nessuna selezione")
    
    valid_selections = [s for s in selections if s.get('price') and s['price'] > 1.0]
    if not valid_selections:
        raise ValueError("Nessuna quota valida")
    
    back_sels = [s for s in valid_selections if s.get('effectiveType', 'BACK') == 'BACK']
    lay_sels = [s for s in valid_selections if s.get('effectiveType', 'BACK') == 'LAY']
    
    # Fallback a dutching puro se non misto
    if not lay_sels:
        # Calcola stake totale dal target profit per BACK puro
        # profit = stake * price - total_stake => total_stake = profit / (avg_return - 1)
        weights = [1/s['price'] for s in valid_selections]
        w_sum = sum(weights)
        if w_sum >= 1:
            raise ValueError("Quote non profittevoli per BACK dutching")
        total_stake = target_profit / (1/w_sum - 1)
        return _calculate_back_dutching(valid_selections, total_stake, commission)
    
    if not back_sels:
        return _calculate_lay_dutching(valid_selections, target_profit, commission)
    
    n = len(valid_selections)
    commission_mult = 1 - (commission / 100.0)
    
    logger.info(f"[MIXED DUTCHING] {len(back_sels)} BACK + {len(lay_sels)} LAY, target={target_profit}")
    
    # Costruisci sistema lineare: A * stakes = b
    # Per ogni esito k, il profitto deve essere = target_profit
    A = []
    b = []
    
    for k in range(n):
        row = []
        for i, sel in enumerate(valid_selections):
            price = sel['price']
            is_back = sel.get('effectiveType', 'BACK') == 'BACK'
            
            if is_back:
                # BACK: se vince k=i, guadagno = stake * price * (1-comm)
                # se perde, perdo stake
                if i == k:
                    row.append(price * commission_mult)
                else:
                    row.append(0)
            else:
                # LAY: se vince k=i (runner vince), perdo liability = stake * (price-1)
                # se perde, guadagno stake * (1-comm)
                if i == k:
                    row.append(-(price - 1))
                else:
                    row.append(commission_mult)
        
        A.append(row)
        b.append(target_profit)
    
    A = np.array(A, dtype=float)
    b = np.array(b, dtype=float)
    
    try:
        # Risolvi sistema sovradeterminato con least squares se necessario
        if n == len(A):
            stakes = np.linalg.solve(A, b)
        else:
            stakes, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
        
        # Verifica stakes validi
        if np.any(stakes < 0):
            logger.warning(f"[MIXED DUTCHING] Stakes negativi rilevati: {stakes}")
            raise ValueError("Combinazione non risolvibile - stakes negativi")
        
        stakes = np.round(stakes, 2)
        
    except np.linalg.LinAlgError as e:
        logger.error(f"[MIXED DUTCHING] Errore sistema lineare: {e}")
        raise ValueError("Sistema non risolvibile - combinazione non valida")
    
    # Calcola profitti reali per verifica
    profits = []
    for k in range(n):
        profit_k = 0
        for i, sel in enumerate(valid_selections):
            price = sel['price']
            stake = stakes[i]
            is_back = sel.get('effectiveType', 'BACK') == 'BACK'
            
            if is_back:
                if i == k:
                    profit_k += stake * price * commission_mult
            else:
                if i == k:
                    profit_k -= stake * (price - 1)
                else:
                    profit_k += stake * commission_mult
        profits.append(profit_k)
    
    uniform_profit = round(min(profits), 2)
    
    # Costruisci risultati
    results = []
    total_stake = sum(stakes)
    
    for i, sel in enumerate(valid_selections):
        stake = float(stakes[i])
        eff_type = sel.get('effectiveType', 'BACK')
        price = sel['price']
        
        if eff_type == 'BACK':
            liability = 0
            potential_return = stake * price
        else:
            liability = stake * (price - 1)
            potential_return = stake
        
        results.append({
            'selectionId': sel['selectionId'],
            'runnerName': sel['runnerName'],
            'price': price,
            'stake': round(stake, 2),
            'side': eff_type,
            'effectiveType': eff_type,
            'profitIfWins': uniform_profit,
            'grossProfit': round(uniform_profit / commission_mult, 2) if uniform_profit > 0 else uniform_profit,
            'potentialReturn': round(potential_return, 2),
            'liability': round(liability, 2)
        })
        
        logger.info(f"[MIXED DUTCHING] {sel['runnerName']} ({eff_type}) @ {price:.2f} -> stake={stake:.2f}")
    
    logger.info(f"[MIXED DUTCHING] Total stake: {total_stake:.2f}, Uniform profit: {uniform_profit:.2f}")
    
    implied_prob = sum(1/s['price'] for s in back_sels) * 100 if back_sels else 0
    
    return results, uniform_profit, round(implied_prob, 2)


def adjust_for_slippage(
    matched_orders: List[Dict],
    remaining_odds: List[Dict],
    target_profit: float,
    commission: float = 4.5
) -> Tuple[List[Dict], float, float]:
    """
    Slippage Live - Ricalcolo per partial fill.
    
    Quando le scommesse sono parzialmente matchate, ricalcola
    gli stake rimanenti con le quote live per mantenere il target.
    """
    commission_mult = 1 - (commission / 100.0)
    
    # Profitto già bloccato dai matched
    matched_profit = sum(
        o.get('stake', 0) * (o.get('price', 1) - 1) 
        for o in matched_orders 
        if o.get('side', 'BACK') == 'BACK'
    ) * commission_mult
    
    remaining_profit = target_profit - matched_profit
    
    if remaining_profit <= 0:
        return [], 0.0, matched_profit
    
    if not remaining_odds:
        return [], remaining_profit, matched_profit
    
    return _calculate_back_dutching(remaining_odds, remaining_profit, commission)


def multi_market_swap(
    markets: List[Dict],
    target_profit: float,
    commission: float = 4.5
) -> Dict:
    """
    Swap Multi-Market - Hedging tra mercati diversi.
    
    Calcola esposizione attraverso Match Odds, Correct Score, O/U, BTTS.
    """
    commission_mult = 1 - (commission / 100.0)
    exposure = 0.0
    stakes = []
    
    for m in markets:
        odds = m.get('odds', m.get('price', 1))
        side = m.get('side', 'BACK')
        
        if side == 'BACK':
            stake = target_profit / (odds - 1) / commission_mult if odds > 1 else 0
            exp = stake
        else:
            stake = target_profit / odds / commission_mult if odds > 0 else 0
            exp = stake * (odds - 1)
        
        exposure += exp
        stakes.append({
            'marketId': m.get('marketId', ''),
            'selectionId': m.get('selectionId', ''),
            'side': side,
            'odds': odds,
            'stake': round(stake, 2),
            'exposure': round(exp, 2)
        })
    
    return {
        'totalExposure': round(exposure, 2),
        'targetProfit': round(target_profit * commission_mult, 2),
        'stakes': stakes
    }


def dynamic_cashout_single(
    back_stake: float,
    back_price: float,
    lay_price: float,
    commission: float = 4.5
) -> Dict:
    """
    Cashout Dinamico Singolo - Formula semplificata per singola posizione BACK.
    
    Calcola lo stake LAY necessario per chiudere la posizione
    con profitto netto uniforme (green-up).
    
    Formula:
        lay_stake = (back_stake * back_price) / lay_price
        profit_win = back_stake * (back_price - 1) - lay_stake * (lay_price - 1)
        profit_lose = lay_stake - back_stake
        net_profit = min(profit_win, profit_lose) * (1 - commission)
    
    Args:
        back_stake: Stake della scommessa BACK originale
        back_price: Quota BACK originale
        lay_price: Quota LAY live attuale
        commission: Commissione Betfair (default 4.5%)
    
    Returns:
        Dict con lay_stake e net_profit
    """
    commission_mult = 1 - (commission / 100.0)
    
    if lay_price <= 1:
        return {'lay_stake': 0, 'net_profit': 0, 'error': 'Quota LAY non valida'}
    
    # Formula: lay_stake = (back_stake * back_price) / lay_price
    lay_stake = (back_stake * back_price) / lay_price
    
    # Profitto se VINCE: guadagno dal BACK - liability del LAY
    profit_win = back_stake * (back_price - 1) - lay_stake * (lay_price - 1)
    
    # Profitto se PERDE: guadagno dal LAY - stake BACK perso
    profit_lose = lay_stake - back_stake
    
    # Cashout = profitto garantito (minimo tra i due scenari)
    gross_profit = min(profit_win, profit_lose)
    
    # Applica commissione solo se profitto positivo
    net_profit = gross_profit * commission_mult if gross_profit > 0 else gross_profit
    
    logger.info(f"[CASHOUT] BACK {back_stake:.2f}@{back_price:.2f} -> LAY {lay_stake:.2f}@{lay_price:.2f}")
    logger.info(f"[CASHOUT] profit_win={profit_win:.2f}, profit_lose={profit_lose:.2f}, net={net_profit:.2f}")
    
    return {
        'lay_stake': round(lay_stake, 2),
        'gross_profit': round(gross_profit, 2),
        'net_profit': round(net_profit, 2),
        'profit_if_wins': round(profit_win, 2),
        'profit_if_loses': round(profit_lose, 2)
    }


def dynamic_cashout(
    orders: List[Dict],
    live_odds: Dict,
    commission: float = 4.5
) -> Dict:
    """
    Cashout Dinamico Live Multi-Ordine - Identico all'exchange.
    
    Calcola valore cashout per multiple posizioni basato su quote correnti.
    Supporta sia BACK che LAY come posizioni originali.
    
    Args:
        orders: Lista di ordini {'selectionId', 'side', 'stake', 'price'}
        live_odds: Dict {selectionId: live_price} con quote attuali
        commission: Commissione Betfair (default 4.5%)
    
    Returns:
        Dict con cashoutValue totale e dettaglio hedges
    """
    commission_mult = 1 - (commission / 100.0)
    total_profit = 0.0
    hedges = []
    
    for o in orders:
        sel_id = o.get('selectionId', o.get('selection', ''))
        side = o.get('side', 'BACK')
        stake = o.get('stake', 0)
        entry_price = o.get('price', o.get('odds', 1))
        live_price = live_odds.get(sel_id, entry_price)
        
        if live_price <= 1:
            continue
        
        if side == 'BACK':
            # BACK -> LAY hedge
            # Formula: lay_stake = (back_stake * back_price) / lay_price
            hedge_stake = (stake * entry_price) / live_price
            
            # Profitto se vince = back_profit - lay_liability
            profit_if_wins = stake * (entry_price - 1) - hedge_stake * (live_price - 1)
            # Profitto se perde = lay_stake - back_stake
            profit_if_loses = hedge_stake - stake
            
            cashout = min(profit_if_wins, profit_if_loses)
            hedge_side = 'LAY'
        else:
            # LAY -> BACK hedge
            # Formula: back_stake = lay_liability / (back_price - 1)
            entry_liability = stake * (entry_price - 1)
            hedge_stake = entry_liability / (live_price - 1) if live_price > 1 else 0
            
            # Profitto se perde = lay_stake - back_stake
            profit_if_loses = stake - hedge_stake
            # Profitto se vince = back_profit - lay_liability
            profit_if_wins = hedge_stake * (live_price - 1) - entry_liability
            
            cashout = min(profit_if_wins, profit_if_loses)
            hedge_side = 'BACK'
        
        total_profit += cashout
        hedges.append({
            'selectionId': sel_id,
            'runnerName': o.get('runnerName', ''),
            'originalSide': side,
            'originalStake': stake,
            'originalPrice': entry_price,
            'hedgeSide': hedge_side,
            'hedgeStake': round(hedge_stake, 2),
            'hedgePrice': live_price,
            'profitIfWins': round(profit_if_wins, 2),
            'profitIfLoses': round(profit_if_loses, 2),
            'cashoutGross': round(cashout, 2)
        })
        
        logger.info(f"[CASHOUT] {o.get('runnerName', sel_id)} {side}@{entry_price:.2f} -> {hedge_side}@{live_price:.2f}: gross={cashout:.2f}")
    
    net_profit = total_profit * commission_mult if total_profit > 0 else total_profit
    
    logger.info(f"[CASHOUT] Total gross={total_profit:.2f}, net={net_profit:.2f}")
    
    return {
        'cashoutValue': round(net_profit, 2),
        'grossProfit': round(total_profit, 2),
        'hedges': hedges
    }


def calculate_green_up(
    order: Dict,
    live_price: float,
    commission: float = 4.5
) -> Tuple[float, float]:
    """
    Green-Up - Blocca profitto su singola posizione.
    
    Piazza scommessa opposta per garantire profitto uniforme.
    """
    commission_mult = 1 - (commission / 100.0)
    
    side = order.get('side', 'BACK')
    stake = order.get('stake', 0)
    entry_price = order.get('price', order.get('odds', 1))
    
    if side == 'BACK':
        hedge = (entry_price / live_price) * stake if live_price > 0 else 0
        profit = stake * (entry_price - 1) - hedge * (live_price - 1)
    else:
        hedge = (live_price / entry_price) * stake if entry_price > 0 else 0
        profit = stake - hedge * (live_price - 1)
    
    if profit > 0:
        profit *= commission_mult
    
    return round(profit, 2), round(hedge, 2)


def format_currency(amount: float) -> str:
    """Formatta importo in valuta italiana."""
    return f"{amount:,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", ".")
