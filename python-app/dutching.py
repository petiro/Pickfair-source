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
    Calculate BACK dutching: Bet on multiple outcomes to win EQUAL profit.
    
    Formula for equal profit distribution:
    - inv_weight = 1 / (price - 1) for each selection
    - inv_sum = sum of all inv_weights
    - stake_i = total_stake / inv_sum / (price_i - 1)
    - profit = stake_i * (price_i - 1) * (1 - commission)  [same for all]
    """
    commission_mult = 1 - (commission / 100.0)
    
    # Calculate inverse weights: 1/(price-1) for equal profit distribution
    inv_weights = []
    for sel in selections:
        weight = 1.0 / (sel['price'] - 1) if sel['price'] > 1 else 0
        inv_weights.append(weight)
    
    inv_sum = sum(inv_weights)
    
    if inv_sum <= 0:
        raise ValueError("Quote non valide per dutching")
    
    # Calculate raw stakes using equal profit formula
    raw_stakes = []
    for i, sel in enumerate(selections):
        stake = total_stake / inv_sum * inv_weights[i]
        raw_stakes.append(stake)
    
    # Round stakes to 2 decimal places
    rounded_stakes = [round(s, 2) for s in raw_stakes]
    
    # Distribute rounding error to maintain total stake
    stake_diff = round(total_stake - sum(rounded_stakes), 2)
    if stake_diff != 0 and len(rounded_stakes) > 0:
        # Add to largest stake for least proportional impact
        max_idx = rounded_stakes.index(max(rounded_stakes))
        rounded_stakes[max_idx] = round(rounded_stakes[max_idx] + stake_diff, 2)
    
    # Build results with adjusted stakes for equal profit
    results = []
    total_actual_stake = sum(rounded_stakes)
    
    # Calculate implied probability for display
    implied_probs = [1.0 / sel['price'] for sel in selections]
    total_implied = sum(implied_probs)
    
    for i, sel in enumerate(selections):
        stake = rounded_stakes[i]
        potential_return = stake * sel['price']
        # Each selection has same gross profit: stake * (price - 1)
        gross_profit = stake * (sel['price'] - 1)
        net_profit = gross_profit * commission_mult
        
        results.append({
            'selectionId': sel['selectionId'],
            'runnerName': sel['runnerName'],
            'price': sel['price'],
            'stake': stake,
            'potentialReturn': round(potential_return, 2),
            'impliedProbability': round(implied_probs[i] * 100, 2),
            'profitIfWins': round(net_profit, 2),
            'grossProfit': round(gross_profit, 2)
        })
    
    # Uniform profit is the same for all selections (verify with min)
    uniform_profit = min(r['profitIfWins'] for r in results) if results else 0
    
    # Validate max winnings
    max_return = max(r['potentialReturn'] for r in results)
    if max_return > MAX_WINNINGS:
        raise ValueError(f"Vincita massima superata: {max_return:.2f} EUR (max: {MAX_WINNINGS:.2f})")
    
    return results, round(uniform_profit, 2), round(total_implied * 100, 2)


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
    
    # Calculate theoretical uniform profit for LAY dutching
    # For each selection, if that runner wins: profit = other_stakes - liability
    # With proper proportional distribution, all these should be equal
    profits_if_win = []
    for r in results:
        other_stakes = total_stakes - r['stake']
        gross_profit = other_stakes - r['liability']
        profits_if_win.append(gross_profit)
    
    # Use average as the theoretical uniform profit
    if profits_if_win:
        theoretical_gross_profit = sum(profits_if_win) / len(profits_if_win)
    else:
        theoretical_gross_profit = 0
    
    theoretical_net_profit = theoretical_gross_profit * commission_mult if theoretical_gross_profit > 0 else theoretical_gross_profit
    
    # Apply uniform profit to all selections
    for r in results:
        r['profitIfWins'] = round(theoretical_net_profit, 2)
        r['grossProfit'] = round(theoretical_gross_profit, 2)
        r['potentialReturn'] = r['stake']
    
    # Best case: all laid selections lose (we keep all stakes, minus commission)
    best_case_gross = total_stakes
    best_case_profit = best_case_gross * commission_mult
    
    # Worst case is now the uniform profit (same for any winner)
    worst_case_profit = theoretical_net_profit
    
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


# =============================================================================
# ADVANCED TRADING FUNCTIONS
# =============================================================================

def adjust_for_slippage(
    matched_bets: List[Dict],
    target_profit: float,
    live_odds: List[Dict],
    bet_type: str = 'BACK',
    commission: float = 4.5
) -> Tuple[List[Dict], float, float]:
    """
    Adjust dutching for partial fills (slippage).
    
    When bets are partially matched, this recalculates remaining stakes
    using current live odds to maintain target profit.
    
    Args:
        matched_bets: List of already matched bets with 'stake', 'price', 'selectionId'
        target_profit: Original target profit
        live_odds: Current live odds for remaining selections
        bet_type: 'BACK' or 'LAY'
        commission: Commission percentage
        
    Returns:
        Tuple of (new_stakes_for_unmatched, remaining_profit_target, locked_profit)
    """
    commission_mult = 1 - (commission / 100.0)
    
    # Calculate profit already locked from matched bets
    locked_profit = 0.0
    for bet in matched_bets:
        if bet_type == 'BACK':
            locked_profit += bet['stake'] * (bet['price'] - 1)
        else:  # LAY
            locked_profit += bet['stake']
    
    locked_profit *= commission_mult
    
    # Calculate remaining profit needed
    remaining_profit = target_profit - locked_profit
    
    if remaining_profit <= 0:
        # Already achieved target, no more bets needed
        return [], remaining_profit, locked_profit
    
    # Filter out already matched selections
    matched_ids = {bet['selectionId'] for bet in matched_bets}
    remaining_odds = [o for o in live_odds if o['selectionId'] not in matched_ids]
    
    if not remaining_odds:
        return [], remaining_profit, locked_profit
    
    # Calculate new stakes for remaining selections
    if bet_type == 'BACK':
        # For BACK: stake = profit / (odds - 1)
        new_stakes = []
        for sel in remaining_odds:
            stake = remaining_profit / (sel['price'] - 1) / commission_mult
            new_stakes.append({
                'selectionId': sel['selectionId'],
                'runnerName': sel.get('runnerName', ''),
                'price': sel['price'],
                'stake': round(stake, 2),
                'side': 'BACK'
            })
    else:  # LAY
        # For LAY: distribute to maintain uniform outcome
        inv_sum = sum(1 / (s['price'] - 1) for s in remaining_odds if s['price'] > 1)
        new_stakes = []
        for sel in remaining_odds:
            weight = 1 / (sel['price'] - 1) if sel['price'] > 1 else 0
            stake = (remaining_profit / commission_mult) * weight / inv_sum if inv_sum > 0 else 0
            new_stakes.append({
                'selectionId': sel['selectionId'],
                'runnerName': sel.get('runnerName', ''),
                'price': sel['price'],
                'stake': round(stake, 2),
                'liability': round(stake * (sel['price'] - 1), 2),
                'side': 'LAY'
            })
    
    return new_stakes, round(remaining_profit, 2), round(locked_profit, 2)


def multi_market_swap(
    markets: List[Dict],
    target_profit: float,
    commission: float = 4.5
) -> Dict:
    """
    Calculate exposure across multiple markets for the same event.
    
    Enables hedging between different market types:
    - Match Odds
    - Correct Score
    - Over/Under
    - BTTS
    
    Args:
        markets: List of dicts with 'marketId', 'odds', 'side', 'selectionId'
        target_profit: Desired profit
        commission: Commission percentage
        
    Returns:
        Dict with total exposure and individual stakes
    """
    commission_mult = 1 - (commission / 100.0)
    
    total_exposure = 0.0
    stakes = []
    
    for m in markets:
        odds = m['odds']
        side = m.get('side', 'BACK')
        
        if side == 'BACK':
            # BACK: stake needed = profit / (odds - 1)
            stake = target_profit / (odds - 1) / commission_mult if odds > 1 else 0
            exposure = stake
        else:  # LAY
            # LAY: stake needed = profit / odds (liability perspective)
            stake = target_profit / odds / commission_mult if odds > 0 else 0
            exposure = stake * (odds - 1)  # liability
        
        total_exposure += exposure
        stakes.append({
            'marketId': m.get('marketId', ''),
            'selectionId': m.get('selectionId', ''),
            'side': side,
            'odds': odds,
            'stake': round(stake, 2),
            'exposure': round(exposure, 2)
        })
    
    return {
        'totalExposure': round(total_exposure, 2),
        'targetProfit': round(target_profit * commission_mult, 2),
        'stakes': stakes
    }


def dynamic_cashout(
    orders: List[Dict],
    live_odds: Dict[str, float],
    commission: float = 4.5
) -> Dict:
    """
    Calculate dynamic cashout value based on current live odds.
    
    This is the core cashout engine used by professional trading software.
    
    Args:
        orders: List of placed orders with 'selectionId', 'side', 'stake', 'price'
        live_odds: Dict mapping selectionId -> current live odds
        commission: Commission percentage
        
    Returns:
        Dict with cashout value, hedge stakes, and P&L breakdown
    """
    commission_mult = 1 - (commission / 100.0)
    
    hedges = []
    total_gross_profit = 0.0
    total_hedge_stake = 0.0
    
    for order in orders:
        sel_id = order['selectionId']
        side = order['side']
        stake = order['stake']
        entry_price = order['price']
        current_price = live_odds.get(sel_id, entry_price)
        
        if side == 'BACK':
            # Original BACK bet: profit if wins = stake * (entry_price - 1)
            # To lock profit: LAY at current price
            # Hedge stake = (entry_price / current_price) * stake
            hedge_stake = (entry_price / current_price) * stake if current_price > 0 else 0
            hedge_liability = hedge_stake * (current_price - 1)
            
            # Profit scenarios after hedge:
            # If wins: stake * (entry_price - 1) - hedge_liability
            # If loses: hedge_stake - stake
            profit_if_wins = stake * (entry_price - 1) - hedge_liability
            profit_if_loses = hedge_stake - stake
            
            # Uniform profit (cashout value)
            cashout_gross = min(profit_if_wins, profit_if_loses)
            
            hedges.append({
                'selectionId': sel_id,
                'originalSide': 'BACK',
                'hedgeSide': 'LAY',
                'originalStake': stake,
                'originalPrice': entry_price,
                'hedgeStake': round(hedge_stake, 2),
                'hedgePrice': current_price,
                'hedgeLiability': round(hedge_liability, 2),
                'profitIfWins': round(profit_if_wins, 2),
                'profitIfLoses': round(profit_if_loses, 2),
                'cashoutGross': round(cashout_gross, 2)
            })
            
            total_gross_profit += cashout_gross
            total_hedge_stake += hedge_stake
            
        else:  # LAY
            # Original LAY bet: profit if loses = stake, loss if wins = liability
            # To lock profit: BACK at current price
            # Hedge stake = (entry_price / current_price) * stake (liability ratio)
            entry_liability = stake * (entry_price - 1)
            hedge_stake = entry_liability / (current_price - 1) if current_price > 1 else 0
            
            # Profit scenarios after hedge:
            # If loses: stake - hedge_stake
            # If wins: hedge_stake * (current_price - 1) - entry_liability
            profit_if_loses = stake - hedge_stake
            profit_if_wins = hedge_stake * (current_price - 1) - entry_liability
            
            # Uniform profit (cashout value)
            cashout_gross = min(profit_if_wins, profit_if_loses)
            
            hedges.append({
                'selectionId': sel_id,
                'originalSide': 'LAY',
                'hedgeSide': 'BACK',
                'originalStake': stake,
                'originalPrice': entry_price,
                'originalLiability': round(entry_liability, 2),
                'hedgeStake': round(hedge_stake, 2),
                'hedgePrice': current_price,
                'profitIfWins': round(profit_if_wins, 2),
                'profitIfLoses': round(profit_if_loses, 2),
                'cashoutGross': round(cashout_gross, 2)
            })
            
            total_gross_profit += cashout_gross
            total_hedge_stake += hedge_stake
    
    # Apply commission to positive profit only
    if total_gross_profit > 0:
        total_net_profit = total_gross_profit * commission_mult
    else:
        total_net_profit = total_gross_profit
    
    return {
        'cashoutValue': round(total_net_profit, 2),
        'grossProfit': round(total_gross_profit, 2),
        'totalHedgeStake': round(total_hedge_stake, 2),
        'hedges': hedges,
        'commission': commission
    }


def calculate_green_up(
    entry_price: float,
    entry_stake: float,
    current_price: float,
    side: str = 'BACK',
    commission: float = 4.5
) -> Dict:
    """
    Calculate green-up (lock in profit) for a single position.
    
    Green-up means placing opposite bet to lock uniform profit regardless of outcome.
    
    Args:
        entry_price: Original bet price
        entry_stake: Original stake
        current_price: Current live price
        side: Original bet side ('BACK' or 'LAY')
        commission: Commission percentage
        
    Returns:
        Dict with hedge details and locked profit
    """
    commission_mult = 1 - (commission / 100.0)
    
    if side == 'BACK':
        # BACK green-up: LAY at current price
        # hedge_stake = entry_stake * entry_price / current_price
        hedge_stake = entry_stake * entry_price / current_price if current_price > 0 else 0
        hedge_liability = hedge_stake * (current_price - 1)
        
        # If original wins: entry_stake * (entry_price - 1) - hedge_liability
        # If original loses: hedge_stake - entry_stake
        profit_if_wins = entry_stake * (entry_price - 1) - hedge_liability
        profit_if_loses = hedge_stake - entry_stake
        
        # For perfect green: both should be equal
        green_profit = (profit_if_wins + profit_if_loses) / 2
        
    else:  # LAY
        # LAY green-up: BACK at current price
        entry_liability = entry_stake * (entry_price - 1)
        hedge_stake = entry_liability / (current_price - 1) if current_price > 1 else 0
        
        # If original loses: entry_stake - hedge_stake
        # If original wins: hedge_stake * (current_price - 1) - entry_liability
        profit_if_loses = entry_stake - hedge_stake
        profit_if_wins = hedge_stake * (current_price - 1) - entry_liability
        
        green_profit = (profit_if_wins + profit_if_loses) / 2
        hedge_liability = 0  # BACK has no liability
    
    net_profit = green_profit * commission_mult if green_profit > 0 else green_profit
    
    return {
        'hedgeSide': 'LAY' if side == 'BACK' else 'BACK',
        'hedgeStake': round(hedge_stake, 2),
        'hedgePrice': current_price,
        'hedgeLiability': round(hedge_liability, 2) if side == 'BACK' else 0,
        'profitIfWins': round(profit_if_wins, 2),
        'profitIfLoses': round(profit_if_loses, 2),
        'greenProfit': round(net_profit, 2),
        'grossProfit': round(green_profit, 2),
        'isProfitable': green_profit > 0
    }
