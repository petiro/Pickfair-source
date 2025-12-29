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
    BACK Dutching - Profitto Uniforme.
    
    Formula: stake_i = total_stake * (1/(price_i-1)) / sum(1/(price_j-1))
    Garantisce profitto identico su qualsiasi selezione vinca.
    """
    commission_mult = 1 - (commission / 100.0)
    
    # Pesi inversi: 1/(price-1)
    inv_weights = []
    for sel in selections:
        weight = 1.0 / (sel['price'] - 1) if sel['price'] > 1 else 0
        inv_weights.append(weight)
    
    weight_sum = sum(inv_weights)
    if weight_sum <= 0:
        raise ValueError("Quote non valide per dutching")
    
    results = []
    profits = []
    
    for i, sel in enumerate(selections):
        stake = total_stake * inv_weights[i] / weight_sum
        stake = round(stake, 2)
        
        gross_profit = stake * (sel['price'] - 1)
        net_profit = gross_profit * commission_mult
        
        results.append({
            'selectionId': sel['selectionId'],
            'runnerName': sel['runnerName'],
            'price': sel['price'],
            'stake': stake,
            'side': 'BACK',
            'grossProfit': round(gross_profit, 2),
            'profitIfWins': round(net_profit, 2),
            'potentialReturn': round(stake * sel['price'], 2),
            'impliedProbability': round((1.0 / sel['price']) * 100, 2)
        })
        profits.append(net_profit)
    
    # Correggi arrotondamento per mantenere stake totale
    actual_total = sum(r['stake'] for r in results)
    diff = round(total_stake - actual_total, 2)
    if diff != 0 and results:
        max_idx = max(range(len(results)), key=lambda i: results[i]['stake'])
        results[max_idx]['stake'] = round(results[max_idx]['stake'] + diff, 2)
    
    uniform_profit = round(min(profits), 2)
    implied_prob = sum(1.0 / sel['price'] for sel in selections) * 100
    
    # Log risultati
    logger.info(f"[DUTCHING] BACK Results:")
    for r in results:
        logger.info(f"[DUTCHING] Output: {r['runnerName']} @ {r['price']:.2f} -> stake={r['stake']:.2f}, profit={r['profitIfWins']:.2f}")
    logger.info(f"[DUTCHING] Uniform profit: {uniform_profit:.2f}")
    
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
    total_stake: float,
    commission: float = 4.5
) -> Tuple[List[Dict], float, float]:
    """
    BACK + LAY Misto - Sistema Lineare (livello Bet Angel PRO).
    
    Risolve sistema di equazioni per profitto uniforme con mix di BACK e LAY.
    Ogni selezione ha 'effectiveType': 'BACK' o 'LAY'.
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
        return _calculate_back_dutching(valid_selections, total_stake, commission)
    if not back_sels:
        return _calculate_lay_dutching(valid_selections, total_stake, commission)
    
    n = len(valid_selections)
    commission_mult = 1 - (commission / 100.0)
    
    # Costruisci sistema lineare: A * [stakes, profit] = b
    A = np.zeros((n + 1, n + 1))
    b = np.zeros(n + 1)
    
    for k in range(n):
        for i in range(n):
            sel = valid_selections[i]
            price = sel['price']
            is_back = sel.get('effectiveType', 'BACK') == 'BACK'
            
            if i == k:
                A[k, i] = (price - 1) if is_back else -(price - 1)
            else:
                A[k, i] = -1 if is_back else 1
        A[k, n] = -1
        b[k] = 0
    
    # Vincolo stake totale
    for i in range(n):
        A[n, i] = 1
    A[n, n] = 0
    b[n] = total_stake
    
    try:
        solution = np.linalg.solve(A, b)
        stakes = solution[:n]
        profit = solution[n]
        
        if np.any(stakes < 0):
            raise ValueError("Combinazione non risolvibile - stakes negativi")
    except np.linalg.LinAlgError:
        raise ValueError("Sistema non risolvibile - combinazione non valida")
    
    # Costruisci risultati
    results = []
    for i, sel in enumerate(valid_selections):
        stake = max(round(stakes[i], 2), MIN_STAKE)
        eff_type = sel.get('effectiveType', 'BACK')
        
        net_profit = profit * commission_mult if profit > 0 else profit
        
        results.append({
            'selectionId': sel['selectionId'],
            'runnerName': sel['runnerName'],
            'price': sel['price'],
            'stake': stake,
            'side': eff_type,
            'effectiveType': eff_type,
            'profitIfWins': round(net_profit, 2),
            'grossProfit': round(profit, 2),
            'potentialReturn': round(stake * sel['price'], 2) if eff_type == 'BACK' else stake,
            'liability': round(stake * (sel['price'] - 1), 2) if eff_type == 'LAY' else 0
        })
    
    implied_prob = sum(1/s['price'] for s in back_sels) * 100 if back_sels else 0
    
    return results, round(profit * commission_mult, 2), round(implied_prob, 2)


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


def dynamic_cashout(
    orders: List[Dict],
    live_odds: Dict,
    commission: float = 4.5
) -> Dict:
    """
    Cashout Dinamico Live - Identico all'exchange.
    
    Calcola valore cashout basato su quote correnti.
    """
    commission_mult = 1 - (commission / 100.0)
    profit = 0.0
    hedges = []
    
    for o in orders:
        sel_id = o.get('selectionId', o.get('selection', ''))
        side = o.get('side', 'BACK')
        stake = o.get('stake', 0)
        entry_price = o.get('price', o.get('odds', 1))
        live_price = live_odds.get(sel_id, entry_price)
        
        if side == 'BACK':
            hedge_stake = (entry_price / live_price) * stake if live_price > 0 else 0
            hedge_liability = hedge_stake * (live_price - 1)
            profit_if_wins = stake * (entry_price - 1) - hedge_liability
            profit_if_loses = hedge_stake - stake
            cashout = min(profit_if_wins, profit_if_loses)
        else:
            entry_liability = stake * (entry_price - 1)
            hedge_stake = entry_liability / (live_price - 1) if live_price > 1 else 0
            profit_if_loses = stake - hedge_stake
            profit_if_wins = hedge_stake * (live_price - 1) - entry_liability
            cashout = min(profit_if_wins, profit_if_loses)
        
        profit += cashout
        hedges.append({
            'selectionId': sel_id,
            'originalSide': side,
            'hedgeSide': 'LAY' if side == 'BACK' else 'BACK',
            'hedgeStake': round(hedge_stake, 2),
            'hedgePrice': live_price,
            'cashoutGross': round(cashout, 2)
        })
    
    net_profit = profit * commission_mult if profit > 0 else profit
    
    return {
        'cashoutValue': round(net_profit, 2),
        'grossProfit': round(profit, 2),
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
