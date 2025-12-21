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
    bet_type: str = 'BACK'
) -> Tuple[List[Dict], float, float]:
    """
    Calculate dutching stakes for equal profit distribution.
    
    Args:
        selections: List of {'selectionId': int, 'runnerName': str, 'price': float}
        total_stake: Total amount to stake
        bet_type: 'BACK' or 'LAY'
    
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
        return _calculate_back_dutching(valid_selections, total_stake)
    else:
        return _calculate_lay_dutching(valid_selections, total_stake)


def _calculate_back_dutching(
    selections: List[Dict],
    total_stake: float
) -> Tuple[List[Dict], float, float]:
    """
    Calculate BACK dutching: Bet on multiple outcomes to win same profit.
    
    Formula:
    - implied_prob = 1 / price
    - total_implied = sum(1/price for each selection)
    - stake_i = total_stake * (1/price_i) / total_implied
    - profit = total_stake * (1 / total_implied - 1) if total_implied < 1
    """
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
    
    # Calculate profit (same for all outcomes in perfect dutching)
    # Profit = Return - Total Stake
    if results:
        avg_return = sum(r['potentialReturn'] for r in results) / len(results)
        potential_profit = avg_return - total_actual_stake
    else:
        potential_profit = 0
    
    # For dutching, profit should be calculated per winning outcome
    # If one selection wins: profit = (stake * price) - total_stake_all
    for r in results:
        r['profitIfWins'] = round(r['potentialReturn'] - total_actual_stake, 2)
    
    # Validate max winnings
    max_return = max(r['potentialReturn'] for r in results)
    if max_return > MAX_WINNINGS:
        raise ValueError(f"Vincita massima superata: {max_return:.2f} EUR (max: {MAX_WINNINGS:.2f})")
    
    return results, round(potential_profit, 2), round(total_implied * 100, 2)


def _calculate_lay_dutching(
    selections: List[Dict],
    total_stake: float
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
        r['profitIfWins'] = round(other_stakes - r['liability'], 2)
        r['potentialReturn'] = r['stake']
    
    # Best case: all laid selections lose (we keep all stakes)
    best_case_profit = total_stakes
    
    # Worst case: the most expensive one wins
    worst_case_profit = min(r['profitIfWins'] for r in results) if results else 0
    
    for r in results:
        r['worstCase'] = worst_case_profit
        r['bestCase'] = best_case_profit
    
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
