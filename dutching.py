"""
Dutching calculation logic for Betfair.
Calculates optimal stake distribution for equal profit on selected outcomes.
"""

from typing import List, Dict, Tuple

# Italian regulations
MIN_BACK_STAKE = 1.00  # EUR - Italian regulatory minimum
MAX_WINNINGS = 10000.00  # EUR

def calculate_dutching_stakes(
    selections: List[Dict],
    total_stake: float,
    bet_type: str = 'BACK',
    commission: float = 4.5
) -> Tuple[List[Dict], float, float]:
    """
    Calculate dutching stakes for equal profit distribution.
    
    Args:
        selections: List of {'selectionId': int, 'runnerName': str, 'price': float}
        total_stake: Total amount to stake
        bet_type: 'BACK' or 'LAY'
        commission: Betfair commission percentage (default 2%)
    
    Returns:
        Tuple of (selections_with_stakes, potential_profit, implied_probability)
    """
    if not selections:
        raise ValueError("Nessuna selezione")
    
    if total_stake <= 0:
        raise ValueError("Stake deve essere positivo")
    
    # Filter out selections without valid prices
    valid_selections = [s for s in selections if s.get('price') and s['price'] > 1.0]
    
    if not valid_selections:
        raise ValueError("Nessuna quota valida")
    
    if bet_type == 'BACK':
        return _calculate_back_dutching(valid_selections, total_stake, commission)
    else:
        return _calculate_lay_dutching(valid_selections, total_stake, commission)


def _calculate_back_dutching(
    selections: List[Dict],
    total_stake: float,
    commission: float = 4.5
) -> Tuple[List[Dict], float, float]:
    """
    Calculate BACK dutching: Bet on multiple outcomes to win same profit.
    
    Formula:
    - implied_prob = 1 / price
    - total_implied = sum(1/price for each selection)
    - stake_i = total_stake * (1/price_i) / total_implied
    - profit = total_stake * (1 / total_implied - 1) if total_implied < 1
    """
    commission_mult = 1 - (commission / 100.0)
    
    # Calculate implied probabilities
    implied_probs = []
    for sel in selections:
        prob = 1.0 / sel['price']
        implied_probs.append(prob)
    
    total_implied = sum(implied_probs)
    
    # Distribute stakes proportionally
    results = []
    total_actual_stake = 0
    
    for i, sel in enumerate(selections):
        stake = total_stake * implied_probs[i] / total_implied
        
        # Round to 2 decimal places
        stake = round(stake, 2)
        
        # Check minimum stake
        if stake < MIN_BACK_STAKE:
            stake = MIN_BACK_STAKE
        
        potential_return = stake * sel['price']
        
        results.append({
            'selectionId': sel['selectionId'],
            'runnerName': sel['runnerName'],
            'price': sel['price'],
            'stake': stake,
            'potentialReturn': round(potential_return, 2),
            'impliedProbability': round(implied_probs[i] * 100, 2)
        })
        
        total_actual_stake += stake
    
    # For dutching, profit should be calculated per winning outcome
    # If one selection wins: profit = (stake * price) - total_stake_all
    # Apply commission to profits
    for r in results:
        gross_profit = r['potentialReturn'] - total_actual_stake
        net_profit = gross_profit * commission_mult if gross_profit > 0 else gross_profit
        r['profitIfWins'] = round(net_profit, 2)
        r['grossProfit'] = round(gross_profit, 2)
    
    # Calculate average profit
    if results:
        potential_profit = sum(r['profitIfWins'] for r in results) / len(results)
    else:
        potential_profit = 0
    
    # Validate max winnings
    max_return = max(r['potentialReturn'] for r in results)
    if max_return > MAX_WINNINGS:
        raise ValueError(f"Vincita massima superata: {max_return:.2f} EUR (max: {MAX_WINNINGS:.2f})")
    
    return results, round(potential_profit, 2), round(total_implied * 100, 2)


def _calculate_lay_dutching(
    selections: List[Dict],
    total_stake: float,
    commission: float = 4.5
) -> Tuple[List[Dict], float, float]:
    """
    Calculate LAY dutching: Lay multiple outcomes for balanced profit/loss.
    
    For LAY bets:
    - stake = what we receive from backer (lay stake)  
    - liability = stake × (price - 1) = what we pay if runner wins
    - profit if runner loses = stake
    - loss if runner wins = liability
    
    Example: LAY at odds 1.5 with stake €1
    - profit if runner loses = €1
    - loss if runner wins = €1 × (1.5 - 1) = €0.50
    
    User enters total_stake as budget for lay stakes.
    We distribute proportionally by 1/(price-1) to equalize outcomes.
    Net profit = sum(other stakes) - liability for any winner.
    """
    commission_mult = 1 - (commission / 100.0)
    results = []
    
    # Calculate lay exposure weights: 1/(price-1) for equalized outcomes
    exposure_weights = []
    for sel in selections:
        weight = 1.0 / (sel['price'] - 1) if sel['price'] > 1 else 0
        exposure_weights.append(weight)
    
    total_weight = sum(exposure_weights)
    
    # Distribute stakes proportionally by exposure weight
    raw_stakes = []
    for i, sel in enumerate(selections):
        stake = total_stake * exposure_weights[i] / total_weight if total_weight > 0 else 0
        raw_stakes.append(stake)
    
    # Round stakes and distribute rounding error
    rounded_stakes = [round(s, 2) for s in raw_stakes]
    stake_diff = round(total_stake - sum(rounded_stakes), 2)
    
    # Distribute rounding error across selections proportionally
    if stake_diff != 0 and len(rounded_stakes) > 0:
        # Add to largest stake first for least impact
        max_idx = rounded_stakes.index(max(rounded_stakes))
        rounded_stakes[max_idx] = round(rounded_stakes[max_idx] + stake_diff, 2)
    
    # Build results
    for i, sel in enumerate(selections):
        price = sel['price']
        stake = rounded_stakes[i]
        liability = round(stake * (price - 1), 2)
        
        results.append({
            'selectionId': sel['selectionId'],
            'runnerName': sel['runnerName'],
            'price': price,
            'stake': stake,
            'liability': liability,
            'profitIfLoses': stake,   # Profit if this runner loses
            'lossIfWins': liability   # Loss if this runner wins
        })
    
    # Calculate totals
    total_stakes = sum(r['stake'] for r in results)
    total_liab = sum(r['liability'] for r in results)
    
    # For each selection, calculate net P&L if that runner wins
    # If runner X wins: we pay X's liability, collect stakes from all others (who lost)
    for r in results:
        other_stakes = total_stakes - r['stake']
        gross_profit = other_stakes - r['liability']
        # Apply commission only on positive profits
        net_profit = gross_profit * commission_mult if gross_profit > 0 else gross_profit
        r['profitIfWins'] = round(net_profit, 2)
        r['grossProfit'] = round(gross_profit, 2)
        r['potentialReturn'] = r['stake']
    
    # Best case: all laid selections lose (we keep all stakes, minus commission)
    best_case_gross = total_stakes
    best_case_profit = best_case_gross * commission_mult
    
    # Worst case: the most expensive one wins
    worst_case_profit = min(r['profitIfWins'] for r in results) if results else 0
    
    for r in results:
        r['worstCase'] = round(worst_case_profit, 2)
        r['bestCase'] = round(best_case_profit, 2)
        r['profitIfLoses'] = round(r['stake'] * commission_mult, 2)  # Net profit after commission
    
    # Validate max winnings (best case profit)
    if best_case_profit > MAX_WINNINGS:
        raise ValueError(f"Vincita massima superata: {best_case_profit:.2f} EUR (max: {MAX_WINNINGS:.2f})")
    
    implied_prob = sum(1 / s['price'] for s in selections) * 100
    
    return results, round(best_case_profit, 2), round(implied_prob, 2)


def validate_selections(selections: List[Dict], bet_type: str = 'BACK') -> List[str]:
    """
    Validate selections before placing bets.
    Returns list of validation errors.
    """
    errors = []
    
    if not selections:
        errors.append("Nessuna selezione")
        return errors
    
    total_stake = sum(s.get('stake', 0) for s in selections)
    
    if bet_type == 'BACK':
        for sel in selections:
            stake = sel.get('stake', 0)
            if stake < MIN_BACK_STAKE:
                errors.append(
                    f"{sel.get('runnerName', 'Selezione')}: "
                    f"stake {stake:.2f} EUR < minimo {MIN_BACK_STAKE:.2f} EUR"
                )
    
    # Check max winnings
    for sel in selections:
        potential_return = sel.get('stake', 0) * sel.get('price', 1)
        if potential_return > MAX_WINNINGS:
            errors.append(
                f"{sel.get('runnerName', 'Selezione')}: "
                f"vincita potenziale {potential_return:.2f} EUR > max {MAX_WINNINGS:.2f} EUR"
            )
    
    return errors


def format_currency(amount: float) -> str:
    """Format amount as Italian currency."""
    return f"{amount:,.2f} EUR".replace(",", "X").replace(".", ",").replace("X", ".")


def calculate_mixed_dutching(
    selections: List[Dict],
    total_stake: float,
    commission: float = 0.0
) -> Tuple[List[Dict], float, float]:
    """
    Calculate mixed BACK/LAY dutching stakes for equal profit distribution.
    Uses linear system solving for mathematically correct equal profit.
    
    Each selection has:
    - selectionId, runnerName, price
    - effectiveType: 'BACK' or 'LAY'
    
    For BACK bet: profit if wins = stake * (price - 1), loss if loses = stake
    For LAY bet: loss if wins = stake * (price - 1), profit if loses = stake
    
    commission: Betfair commission percentage (e.g., 2.0 for 2%)
    """
    import numpy as np
    
    if not selections:
        raise ValueError("Nessuna selezione")
    
    if total_stake <= 0:
        raise ValueError("Stake deve essere positivo")
    
    valid_selections = [s for s in selections if s.get('price') and s['price'] > 1.0]
    
    if not valid_selections:
        raise ValueError("Nessuna quota valida")
    
    back_sels = [s for s in valid_selections if s.get('effectiveType', 'BACK') == 'BACK']
    lay_sels = [s for s in valid_selections if s.get('effectiveType', 'BACK') == 'LAY']
    
    if not lay_sels:
        return _calculate_back_dutching(valid_selections, total_stake)
    if not back_sels:
        return _calculate_lay_dutching(valid_selections, total_stake)
    
    n = len(valid_selections)
    
    A_full = np.zeros((n + 1, n + 1))
    b_full = np.zeros(n + 1)
    
    for k in range(n):
        for i in range(n):
            sel = valid_selections[i]
            price = sel['price']
            is_back = sel.get('effectiveType', 'BACK') == 'BACK'
            
            if i == k:
                if is_back:
                    A_full[k, i] = price - 1
                else:
                    A_full[k, i] = -(price - 1)
            else:
                if is_back:
                    A_full[k, i] = -1
                else:
                    A_full[k, i] = 1
        A_full[k, n] = -1
        b_full[k] = 0
    
    for i in range(n):
        A_full[n, i] = 1
    A_full[n, n] = 0
    b_full[n] = total_stake
    
    try:
        solution = np.linalg.solve(A_full, b_full)
        stakes = solution[:n]
        profit = solution[n]
        
        if np.any(stakes < 0):
            raise ValueError("Combinazione non risolvibile - stakes negativi")
        
    except np.linalg.LinAlgError:
        raise ValueError("Sistema non risolvibile - combinazione di selezioni non valida")
    
    final_stakes = []
    for i in range(n):
        s = max(stakes[i], MIN_BACK_STAKE)
        final_stakes.append(round(s, 2))
    
    commission_mult = 1 - (commission / 100.0)
    
    results = []
    for i, sel in enumerate(valid_selections):
        stake = final_stakes[i]
        price = sel['price']
        eff_type = sel.get('effectiveType', 'BACK')
        
        gross_profit = 0.0
        for j, other in enumerate(valid_selections):
            other_stake = final_stakes[j]
            other_price = other['price']
            other_is_back = other.get('effectiveType', 'BACK') == 'BACK'
            
            if j == i:
                if other_is_back:
                    gross_profit += other_stake * (other_price - 1)
                else:
                    gross_profit -= other_stake * (other_price - 1)
            else:
                if other_is_back:
                    gross_profit -= other_stake
                else:
                    gross_profit += other_stake
        
        net_profit = gross_profit * commission_mult if gross_profit > 0 else gross_profit
        
        results.append({
            'selectionId': sel['selectionId'],
            'runnerName': sel['runnerName'],
            'price': price,
            'stake': stake,
            'effectiveType': eff_type,
            'profitIfWins': round(net_profit, 2),
            'grossProfit': round(gross_profit, 2),
            'potentialReturn': round(stake * price, 2) if eff_type == 'BACK' else stake,
            'liability': round(stake * (price - 1), 2) if eff_type == 'LAY' else 0
        })
    
    avg_profit = sum(r['profitIfWins'] for r in results) / len(results) if results else 0
    implied_back = sum(1/s['price'] for s in back_sels) * 100 if back_sels else 0
    
    return results, round(avg_profit, 2), round(implied_back, 2)


def calculate_ai_mixed_stakes(
    selections: List[Dict],
    total_stake: float,
    commission: float = 4.5,
    min_stake: float = 2.0
) -> Tuple[List[Dict], float, float]:
    """
    AI Mixed Auto-Entry: Calcola automaticamente BACK o LAY per ogni runner
    basandosi sulla probabilità implicita per massimizzare il profitto uniforme.
    
    Logica:
    - Calcola probabilità implicita: impl_prob = 1 / odds
    - Runner con impl_prob > soglia (1/N media) → BACK (sottovalutato dal mercato)
    - Runner con impl_prob < soglia → LAY (sopravvalutato dal mercato)
    - Calcola stakes per profitto uniforme indipendentemente dall'esito
    
    Args:
        selections: Lista di {'selectionId': int, 'runnerName': str, 'price': float}
        total_stake: Stake totale da distribuire
        commission: Commissione Betfair (default 4.5%)
        min_stake: Stake minimo per ordine (default €2)
    
    Returns:
        Tuple di (selections_with_stakes, guaranteed_profit, book_percentage)
    """
    import numpy as np
    
    if not selections:
        raise ValueError("Nessuna selezione")
    
    if total_stake <= 0:
        raise ValueError("Stake deve essere positivo")
    
    valid_selections = [s for s in selections if s.get('price') and s['price'] > 1.0]
    
    if not valid_selections:
        raise ValueError("Nessuna quota valida")
    
    n = len(valid_selections)
    
    # Calcola probabilità implicite
    impl_probs = [1.0 / s['price'] for s in valid_selections]
    total_impl = sum(impl_probs)
    avg_prob = total_impl / n  # Soglia media
    
    # Determina BACK o LAY per ogni runner
    # BACK se probabilità implicita > media (sottovalutato, buon value)
    # LAY se probabilità implicita < media (sopravvalutato)
    ai_selections = []
    for i, sel in enumerate(valid_selections):
        side = 'BACK' if impl_probs[i] >= avg_prob else 'LAY'
        ai_selections.append({
            **sel,
            'effectiveType': side,
            'impliedProbability': impl_probs[i] * 100
        })
    
    back_count = sum(1 for s in ai_selections if s['effectiveType'] == 'BACK')
    lay_count = n - back_count
    
    # Se tutti BACK o tutti LAY, usa calcolo standard
    if lay_count == 0:
        results, profit, book = _calculate_back_dutching(valid_selections, total_stake, commission)
        for r in results:
            r['effectiveType'] = 'BACK'
            r['aiSelected'] = True
        return results, profit, book
    
    if back_count == 0:
        results, profit, book = _calculate_lay_dutching(valid_selections, total_stake, commission)
        for r in results:
            r['effectiveType'] = 'LAY'
            r['aiSelected'] = True
        return results, profit, book
    
    # Mixed: calcola con sistema lineare
    A_full = np.zeros((n + 1, n + 1))
    b_full = np.zeros(n + 1)
    
    for k in range(n):
        for i in range(n):
            sel = ai_selections[i]
            price = sel['price']
            is_back = sel['effectiveType'] == 'BACK'
            
            if i == k:
                if is_back:
                    A_full[k, i] = price - 1
                else:
                    A_full[k, i] = -(price - 1)
            else:
                if is_back:
                    A_full[k, i] = -1
                else:
                    A_full[k, i] = 1
        A_full[k, n] = -1
        b_full[k] = 0
    
    for i in range(n):
        A_full[n, i] = 1
    A_full[n, n] = 0
    b_full[n] = total_stake
    
    try:
        solution = np.linalg.solve(A_full, b_full)
        stakes = solution[:n]
        profit = solution[n]
        
        if np.any(stakes < 0):
            raise ValueError("Combinazione AI non risolvibile - stakes negativi")
        
    except np.linalg.LinAlgError:
        raise ValueError("Sistema AI non risolvibile")
    
    # Applica min_stake e ricalcola iterativamente
    final_stakes = []
    for i in range(n):
        s = max(stakes[i], min_stake)
        final_stakes.append(round(s, 2))
    
    commission_mult = 1 - (commission / 100.0)
    
    results = []
    for i, sel in enumerate(ai_selections):
        stake = final_stakes[i]
        price = sel['price']
        eff_type = sel['effectiveType']
        
        # Calcola P&L per ogni scenario (se runner i vince)
        gross_profit = 0.0
        for j, other in enumerate(ai_selections):
            other_stake = final_stakes[j]
            other_price = other['price']
            other_is_back = other['effectiveType'] == 'BACK'
            
            if j == i:
                if other_is_back:
                    gross_profit += other_stake * (other_price - 1)
                else:
                    gross_profit -= other_stake * (other_price - 1)
            else:
                if other_is_back:
                    gross_profit -= other_stake
                else:
                    gross_profit += other_stake
        
        net_profit = gross_profit * commission_mult if gross_profit > 0 else gross_profit
        
        results.append({
            'selectionId': sel['selectionId'],
            'runnerName': sel['runnerName'],
            'price': price,
            'stake': stake,
            'effectiveType': eff_type,
            'profitIfWins': round(net_profit, 2),
            'grossProfit': round(gross_profit, 2),
            'potentialReturn': round(stake * price, 2) if eff_type == 'BACK' else stake,
            'liability': round(stake * (price - 1), 2) if eff_type == 'LAY' else 0,
            'impliedProbability': sel['impliedProbability'],
            'aiSelected': True
        })
    
    avg_profit = sum(r['profitIfWins'] for r in results) / len(results) if results else 0
    book_pct = total_impl * 100
    
    return results, round(avg_profit, 2), round(book_pct, 2)
