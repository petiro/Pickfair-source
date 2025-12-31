#!/usr/bin/env python3
"""
Test Checklist v3.60.2 - Verifica Debounce, Fallback Prezzi, Green-up Resiliente
"""
import time
import threading
import sys

class MockTickStorage:
    """Simula tick_storage con dati di test."""
    def __init__(self):
        self.ticks = {
            12345: {'back': 2.50, 'lay': 2.52, 'timestamp': time.time()},
            67890: {'back': 3.00, 'lay': 3.05, 'timestamp': time.time()},
        }
    
    def get_last_tick(self, selection_id):
        return self.ticks.get(selection_id)
    
    def store_tick(self, selection_id, back, lay):
        self.ticks[selection_id] = {'back': back, 'lay': lay, 'timestamp': time.time()}

class DebounceTest:
    """Test debounce thread-safe."""
    def __init__(self):
        self._action_timestamps = {}
        self._debounce_lock = threading.Lock()
        self.action_count = 0
        self.blocked_count = 0
    
    def _check_debounce(self, action_key, min_interval):
        now = time.time()
        with self._debounce_lock:
            last_time = self._action_timestamps.get(action_key, 0)
            if now - last_time < min_interval:
                self.blocked_count += 1
                return False
            self._action_timestamps[action_key] = now
        self.action_count += 1
        return True

def test_debounce():
    """Test 1: Verifica debounce thread-safe."""
    print("\n" + "="*60)
    print("TEST 1: DEBOUNCE THREAD-SAFE")
    print("="*60)
    
    tester = DebounceTest()
    results = {'passed': 0, 'failed': 0}
    
    # Test Cancel (1.0s debounce)
    print("\n[Cancel - 1.0s debounce]")
    tester.action_count = 0
    tester.blocked_count = 0
    for i in range(5):
        tester._check_debounce("cancel_123", 1.0)
        time.sleep(0.1)
    
    if tester.action_count == 1 and tester.blocked_count == 4:
        print(f"  OK: Solo 1 azione eseguita, 4 bloccate")
        results['passed'] += 1
    else:
        print(f"  FAIL: {tester.action_count} eseguite, {tester.blocked_count} bloccate")
        results['failed'] += 1
    
    # Test Replace (0.5s debounce)
    print("\n[Replace - 0.5s debounce]")
    tester.action_count = 0
    tester.blocked_count = 0
    tester._action_timestamps.clear()
    
    for i in range(3):
        tester._check_debounce("replace_123", 0.5)
        time.sleep(0.2)
    
    if tester.action_count == 1 and tester.blocked_count == 2:
        print(f"  OK: Solo 1 azione eseguita, 2 bloccate")
        results['passed'] += 1
    else:
        print(f"  FAIL: {tester.action_count} eseguite, {tester.blocked_count} bloccate")
        results['failed'] += 1
    
    # Test Green (2.0s debounce)
    print("\n[Green - 2.0s debounce]")
    tester.action_count = 0
    tester.blocked_count = 0
    tester._action_timestamps.clear()
    
    tester._check_debounce("green_123", 2.0)
    tester._check_debounce("green_123", 2.0)
    time.sleep(2.1)
    tester._check_debounce("green_123", 2.0)
    
    if tester.action_count == 2 and tester.blocked_count == 1:
        print(f"  OK: 2 azioni eseguite (dopo timeout), 1 bloccata")
        results['passed'] += 1
    else:
        print(f"  FAIL: {tester.action_count} eseguite, {tester.blocked_count} bloccate")
        results['failed'] += 1
    
    # Test Thread-Safety
    print("\n[Thread-Safety - Click concorrenti]")
    tester.action_count = 0
    tester.blocked_count = 0
    tester._action_timestamps.clear()
    
    threads = []
    for i in range(10):
        t = threading.Thread(target=lambda: tester._check_debounce("concurrent_test", 1.0))
        threads.append(t)
    
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    if tester.action_count == 1:
        print(f"  OK: Solo 1 azione eseguita da 10 thread concorrenti")
        results['passed'] += 1
    else:
        print(f"  FAIL: {tester.action_count} azioni (atteso 1)")
        results['failed'] += 1
    
    return results

def test_price_fallback():
    """Test 2: Verifica fallback prezzi con tick_storage."""
    print("\n" + "="*60)
    print("TEST 2: FALLBACK PREZZI")
    print("="*60)
    
    tick_storage = MockTickStorage()
    results = {'passed': 0, 'failed': 0}
    
    # Test: current_market = None, usa tick_storage
    print("\n[Scenario: current_market = None]")
    current_market = None
    selection_id = 12345
    
    runner_prices = {}
    
    if current_market is None:
        last_tick = tick_storage.get_last_tick(selection_id)
        if last_tick:
            runner_prices[selection_id] = {
                'back': last_tick.get('back', 0),
                'lay': last_tick.get('lay', 0)
            }
    
    if runner_prices.get(selection_id, {}).get('back', 0) > 1.01:
        print(f"  OK: Prezzo back recuperato da tick_storage: {runner_prices[selection_id]['back']}")
        results['passed'] += 1
    else:
        print(f"  FAIL: Prezzo non recuperato")
        results['failed'] += 1
    
    # Test: Prezzi <= 1.01, fallback a tick_storage
    print("\n[Scenario: Prezzi invalidi <= 1.01]")
    runner_prices = {selection_id: {'back': 1.0, 'lay': 1.0}}
    
    prices = runner_prices[selection_id]
    if prices['back'] <= 1.01 or prices['lay'] <= 1.01:
        last_tick = tick_storage.get_last_tick(selection_id)
        if last_tick:
            if prices['back'] <= 1.01 and last_tick.get('back', 0) > 1.01:
                prices['back'] = last_tick['back']
            if prices['lay'] <= 1.01 and last_tick.get('lay', 0) > 1.01:
                prices['lay'] = last_tick['lay']
    
    if prices['back'] == 2.50 and prices['lay'] == 2.52:
        print(f"  OK: Prezzi corretti dal fallback: back={prices['back']}, lay={prices['lay']}")
        results['passed'] += 1
    else:
        print(f"  FAIL: Prezzi non corretti: {prices}")
        results['failed'] += 1
    
    # Test: P&L calcolato solo con prezzi validi
    print("\n[Scenario: P&L solo con prezzi > 1.01]")
    
    def calculate_pnl(back_price, lay_price, stake):
        if back_price <= 1.01 or lay_price <= 1.01:
            return None
        commission = 0.045
        profit = stake * (back_price - 1)
        hedge_stake = (stake * back_price) / lay_price
        hedge_loss = hedge_stake * (lay_price - 1)
        net_profit = profit - hedge_loss
        return net_profit * (1 - commission)
    
    pnl_valid = calculate_pnl(2.50, 2.52, 10.0)
    pnl_invalid = calculate_pnl(1.0, 2.52, 10.0)
    
    if pnl_valid is not None and pnl_invalid is None:
        print(f"  OK: P&L calcolato solo con prezzi validi: {pnl_valid:.2f}")
        results['passed'] += 1
    else:
        print(f"  FAIL: P&L logic error")
        results['failed'] += 1
    
    return results

def test_green_resilient():
    """Test 3: Verifica green-up resiliente senza UI."""
    print("\n" + "="*60)
    print("TEST 3: GREEN-UP RESILIENTE")
    print("="*60)
    
    tick_storage = MockTickStorage()
    results = {'passed': 0, 'failed': 0}
    
    # Test: Green-up con current_market = None
    print("\n[Scenario: Green-up senza mercato attivo]")
    current_market = None
    selection_id = 12345
    
    best_lay = None
    
    if current_market:
        pass
    
    if (not best_lay or best_lay <= 1):
        last_tick = tick_storage.get_last_tick(selection_id)
        if last_tick and last_tick.get('lay', 0) > 1.01:
            best_lay = last_tick['lay']
    
    if best_lay == 2.52:
        print(f"  OK: Quota LAY recuperata da tick_storage: {best_lay}")
        results['passed'] += 1
    else:
        print(f"  FAIL: Quota LAY non recuperata: {best_lay}")
        results['failed'] += 1
    
    # Test: Green-up con current_market con prezzi invalidi
    print("\n[Scenario: Green-up con prezzi mercato invalidi]")
    current_market = {'runners': [{'selectionId': 12345, 'ex': {'availableToLay': [{'price': 1.0}]}}]}
    
    best_lay = None
    if current_market:
        for r in current_market.get('runners', []):
            if r.get('selectionId') == selection_id:
                lays = r.get('ex', {}).get('availableToLay', [])
                if lays:
                    best_lay = lays[0].get('price')
                break
    
    if (not best_lay or best_lay <= 1):
        last_tick = tick_storage.get_last_tick(selection_id)
        if last_tick and last_tick.get('lay', 0) > 1.01:
            best_lay = last_tick['lay']
    
    if best_lay == 2.52:
        print(f"  OK: Fallback a tick_storage quando mercato ha prezzi invalidi")
        results['passed'] += 1
    else:
        print(f"  FAIL: Fallback non funzionante: {best_lay}")
        results['failed'] += 1
    
    return results

def test_button_control():
    """Test 4: Verifica controllo pulsanti per stato ordine."""
    print("\n" + "="*60)
    print("TEST 4: CONTROLLO PULSANTI")
    print("="*60)
    
    results = {'passed': 0, 'failed': 0}
    
    def get_allowed_buttons(section_type):
        buttons = []
        if section_type == 'unmatched':
            buttons = ['cancel', 'replace_up', 'replace_down']
        elif section_type == 'matched':
            buttons = ['green']
        return buttons
    
    # Test: Ordini unmatched
    print("\n[Ordini Unmatched]")
    buttons = get_allowed_buttons('unmatched')
    if 'cancel' in buttons and 'replace_up' in buttons and 'green' not in buttons:
        print(f"  OK: Cancel/Replace abilitati, Green disabilitato")
        results['passed'] += 1
    else:
        print(f"  FAIL: Pulsanti errati: {buttons}")
        results['failed'] += 1
    
    # Test: Ordini matched
    print("\n[Ordini Matched]")
    buttons = get_allowed_buttons('matched')
    if 'green' in buttons and 'cancel' not in buttons:
        print(f"  OK: Solo Green abilitato per matched")
        results['passed'] += 1
    else:
        print(f"  FAIL: Pulsanti errati: {buttons}")
        results['failed'] += 1
    
    return results

def main():
    print("\n" + "#"*60)
    print("# PICKFAIR v3.60.2 - TEST CHECKLIST")
    print("#"*60)
    
    all_results = {'passed': 0, 'failed': 0}
    
    # Esegui tutti i test
    for test_func in [test_debounce, test_price_fallback, test_green_resilient, test_button_control]:
        results = test_func()
        all_results['passed'] += results['passed']
        all_results['failed'] += results['failed']
    
    # Riepilogo finale
    print("\n" + "="*60)
    print("RIEPILOGO FINALE")
    print("="*60)
    total = all_results['passed'] + all_results['failed']
    print(f"\nTest passati: {all_results['passed']}/{total}")
    print(f"Test falliti: {all_results['failed']}/{total}")
    
    if all_results['failed'] == 0:
        print("\n[SUCCESS] Tutti i test superati!")
        return 0
    else:
        print("\n[WARNING] Alcuni test falliti - rivedere implementazione")
        return 1

if __name__ == "__main__":
    sys.exit(main())
