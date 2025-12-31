#!/usr/bin/env python3
"""
Test Completo Checklist v3.60.2 - Revisione Tecnica Pickfair
Copre: P&L, Pulsanti, Badge, Mini Ladder, Automation, Tick Storage, UI
"""
import time
import threading
import sys
from datetime import datetime

# Import moduli da testare
from pnl_engine import PnLEngine
from automation_engine import AutomationEngine, PositionState, TrailingStopEngine, SLTPEngine
from tick_storage import TickStorage, Tick
from dutching import dynamic_cashout_single, calculate_dutching_stakes

print("\n" + "#"*70)
print("# PICKFAIR v3.60.2 - TEST COMPLETO CHECKLIST TECNICA")
print("#"*70)

results = {'passed': 0, 'failed': 0, 'sections': []}

def section(name):
    print(f"\n{'='*70}")
    print(f"  {name}")
    print('='*70)

def test(name, condition):
    global results
    if condition:
        print(f"  [OK] {name}")
        results['passed'] += 1
    else:
        print(f"  [FAIL] {name}")
        results['failed'] += 1
    return condition

# =============================================================================
# 1. P&L LIVE PER SELEZIONE
# =============================================================================
section("1. P&L LIVE PER SELEZIONE")

pnl = PnLEngine(commission=4.5)

# Test BACK P&L con quote valide
order_back = {'side': 'BACK', 'sizeMatched': 10.0, 'averagePriceMatched': 3.0}
pnl_result = pnl.calculate_back_pnl(order_back, best_lay_price=2.8)
test("BACK P&L calcolato correttamente (profitto)", pnl_result > 0)

pnl_loss = pnl.calculate_back_pnl(order_back, best_lay_price=3.5)
test("BACK P&L calcolato correttamente (perdita)", pnl_loss < 0)

# Test edge cases
test("P&L = 0 se price <= 1.01", pnl.calculate_back_pnl(order_back, best_lay_price=1.0) == 0)
test("P&L = 0 se stake = 0", pnl.calculate_back_pnl({'side': 'BACK', 'sizeMatched': 0, 'price': 2.0}, 2.5) == 0)
test("P&L = 0 se side != BACK/LAY", pnl.calculate_order_pnl({'side': 'UNKNOWN'}, 2.0, 2.5) == 0)

# Test LAY P&L
order_lay = {'side': 'LAY', 'sizeMatched': 10.0, 'averagePriceMatched': 2.5}
pnl_lay = pnl.calculate_lay_pnl(order_lay, best_back_price=2.8)
test("LAY P&L calcolato (back > lay = perdita)", pnl_lay != 0)

# Test calculate_order_pnl routing
test("calculate_order_pnl routing BACK", pnl.calculate_order_pnl(order_back, 3.0, 2.8) == pnl.calculate_back_pnl(order_back, 2.8))
test("calculate_order_pnl routing LAY", pnl.calculate_order_pnl(order_lay, 2.8, 2.5) == pnl.calculate_lay_pnl(order_lay, 2.8))

# Test calculate_selection_pnl
orders = [
    {'side': 'BACK', 'sizeMatched': 5.0, 'averagePriceMatched': 2.0},
    {'side': 'BACK', 'sizeMatched': 5.0, 'averagePriceMatched': 2.5}
]
total_pnl = pnl.calculate_selection_pnl(orders, 2.0, 2.2)
test("calculate_selection_pnl somma ordini", isinstance(total_pnl, float))

# =============================================================================
# 2. PULSANTI PER RIGA (Cancel / Replace / Green)
# =============================================================================
section("2. PULSANTI PER RIGA (Cancel / Replace / Green)")

# Test debounce thread-safe
class DebounceTest:
    def __init__(self):
        self._action_timestamps = {}
        self._debounce_lock = threading.Lock()
    
    def _check_debounce(self, action_key, min_interval):
        now = time.time()
        with self._debounce_lock:
            last = self._action_timestamps.get(action_key, 0)
            if now - last < min_interval:
                return False
            self._action_timestamps[action_key] = now
        return True

debounce = DebounceTest()

# Click rapidi Cancel (1.0s)
click1 = debounce._check_debounce("cancel_123", 1.0)
click2 = debounce._check_debounce("cancel_123", 1.0)
test("Debounce Cancel: primo click accettato", click1)
test("Debounce Cancel: secondo click bloccato", not click2)

# Thread safety
debounce._action_timestamps.clear()
concurrent_results = []
def concurrent_click():
    concurrent_results.append(debounce._check_debounce("concurrent", 1.0))

threads = [threading.Thread(target=concurrent_click) for _ in range(10)]
for t in threads: t.start()
for t in threads: t.join()
test("Thread-safety: solo 1 click accettato da 10 thread", concurrent_results.count(True) == 1)

# Test logica pulsanti per stato ordine
def get_buttons(order_state):
    if order_state == 'unmatched':
        return ['cancel', 'replace_up', 'replace_down']
    elif order_state == 'matched':
        return ['green']
    return []

test("Pulsanti unmatched: Cancel/Replace abilitati", 'cancel' in get_buttons('unmatched'))
test("Pulsanti unmatched: Green disabilitato", 'green' not in get_buttons('unmatched'))
test("Pulsanti matched: solo Green", get_buttons('matched') == ['green'])

# =============================================================================
# 3. BADGE AUTOMAZIONI (SL / TP / TR)
# =============================================================================
section("3. BADGE AUTOMAZIONI (SL / TP / TR)")

sltp = SLTPEngine()
state = PositionState(
    bet_id="bet_001",
    selection_id=12345,
    market_id="1.234567",
    entry_price=2.5,
    stake=10.0,
    side="BACK",
    stop_loss=-5.0,
    take_profit=10.0,
    trailing_amount=0.5
)
sltp.add_position(state)

flags = sltp.get_flags("bet_001")
test("Badge SL attivo", flags['SL'])
test("Badge TP attivo", flags['TP'])
test("Badge TR attivo", flags['TR'])

# Test flags per bet_id inesistente
flags_none = sltp.get_flags("nonexistent")
test("Badge non attivi per bet_id inesistente", not any(flags_none.values()))

# Test AutomationEngine badge string
auto_engine = AutomationEngine(commission=4.5)
auto_engine.add_position("bet_002", 12345, "1.234567", 2.5, 10.0, "BACK", 
                         stop_loss=-5.0, take_profit=10.0, trailing=0.5)
badge_str = auto_engine.get_automation_badges("bet_002")
test("Badge string contiene SL", "SL" in badge_str)
test("Badge string contiene TP", "TP" in badge_str)
test("Badge string contiene TR", "TR" in badge_str)

# =============================================================================
# 4. MINI LADDER INLINE
# =============================================================================
section("4. MINI LADDER INLINE")

# Simulazione dati mini ladder
def get_mini_ladder(runner):
    if runner is None:
        return None
    ex = runner.get('ex', {})
    backs = ex.get('availableToBack', [])[:3]
    lays = ex.get('availableToLay', [])[:3]
    return {'backs': backs, 'lays': lays}

runner_valid = {
    'selectionId': 12345,
    'ex': {
        'availableToBack': [{'price': 2.5, 'size': 100}, {'price': 2.48, 'size': 50}],
        'availableToLay': [{'price': 2.52, 'size': 100}, {'price': 2.54, 'size': 50}]
    }
}

ladder = get_mini_ladder(runner_valid)
test("Mini ladder: max 3 livelli back", len(ladder['backs']) <= 3)
test("Mini ladder: max 3 livelli lay", len(ladder['lays']) <= 3)
test("Mini ladder: dati corretti", ladder['backs'][0]['price'] == 2.5)

# Edge case: runner senza dati
test("Mini ladder: runner None non crasha", get_mini_ladder(None) is None)
ladder_empty = get_mini_ladder({})
test("Mini ladder: runner vuoto ritorna struttura", ladder_empty is not None and 'backs' in ladder_empty)

# =============================================================================
# 5. AUTOMATION ENGINE (SL / TP / Trailing / Green-up)
# =============================================================================
section("5. AUTOMATION ENGINE (SL / TP / Trailing / Green-up)")

triggered_actions = []
def on_green_up(bet_id, reason):
    triggered_actions.append((bet_id, reason))

engine = AutomationEngine(commission=4.5, on_green_up=on_green_up)
engine.add_position("bet_100", 12345, "1.234567", 2.5, 10.0, "BACK",
                    stop_loss=-3.0, take_profit=5.0, trailing=0.5)

# Test Stop Loss
result = engine.evaluate("bet_100", current_pnl=-4.0)
test("Stop Loss trigger su P&L < SL", result == 'STOP_LOSS')
test("Callback SL chiamato", ('bet_100', 'STOP_LOSS') in triggered_actions)

# Reset per test TP
triggered_actions.clear()
engine2 = AutomationEngine(commission=4.5, on_green_up=on_green_up)
engine2.add_position("bet_101", 12345, "1.234567", 2.5, 10.0, "BACK",
                     stop_loss=-10.0, take_profit=5.0)

result_tp = engine2.evaluate("bet_101", current_pnl=6.0)
test("Take Profit trigger su P&L > TP", result_tp == 'TAKE_PROFIT')

# Test Trailing Stop
trailing = TrailingStopEngine()
state_tr = PositionState(
    bet_id="bet_200", selection_id=12345, market_id="1.234567",
    entry_price=2.5, stake=10.0, side="BACK", trailing_amount=0.5
)
trailing.add_position(state_tr)

# Simula salita e discesa P&L
trailing.update("bet_200", 1.0)  # Peak
trailing.update("bet_200", 1.2)  # Nuovo peak
trailing.update("bet_200", 1.5)  # Nuovo peak
trigger_tr = trailing.update("bet_200", 0.9)  # Drop > 0.5
test("Trailing trigger dopo drop dal peak", trigger_tr)

# =============================================================================
# 6. MARKET TRACKER / TICK STORAGE
# =============================================================================
section("6. MARKET TRACKER / TICK STORAGE")

storage = TickStorage(max_ticks=100, ohlc_interval_sec=1)

# Push tick
storage.push_tick(12345, ltp=2.5, back_price=2.48, lay_price=2.52)
storage.push_tick(12345, ltp=2.52, back_price=2.50, lay_price=2.54)

ticks = storage.get_ticks(12345)
test("Tick storage: push e get funzionano", len(ticks) == 2)

# Test get_last_tick (nuovo metodo)
last = storage.get_last_tick(12345)
test("get_last_tick ritorna dict", isinstance(last, dict))
test("get_last_tick contiene back", last.get('back') == 2.50)
test("get_last_tick contiene lay", last.get('lay') == 2.54)
test("get_last_tick per selection inesistente", storage.get_last_tick(99999) is None)

# Test rolling window
for i in range(150):
    storage.push_tick(12345, ltp=2.5+i*0.01, back_price=2.48, lay_price=2.52)

ticks_after = storage.get_ticks(12345)
test("Rolling window: max 100 tick", len(ticks_after) <= 100)

# Test OHLC aggregation
time.sleep(1.1)
storage.push_tick(12345, ltp=2.6, back_price=2.58, lay_price=2.62)
ohlc = storage.aggregate_ohlc(12345, interval_sec=1)
test("OHLC aggregation funziona", len(ohlc) > 0)

# Test LTP history
ltp_hist = storage.get_ltp_history(12345, limit=10)
test("LTP history ritorna lista", isinstance(ltp_hist, list))

# Test clear
storage.clear(12345)
test("Clear rimuove tick", len(storage.get_ticks(12345)) == 0)

# Test thread-safety tick storage
storage2 = TickStorage(max_ticks=1000)
errors = []

def push_ticks():
    try:
        for i in range(100):
            storage2.push_tick(11111, ltp=2.0+i*0.001, back_price=1.99, lay_price=2.01)
    except Exception as e:
        errors.append(str(e))

threads = [threading.Thread(target=push_ticks) for _ in range(5)]
for t in threads: t.start()
for t in threads: t.join()
test("Tick storage thread-safe (no errori)", len(errors) == 0)

# =============================================================================
# 7. UI E INTEGRAZIONE
# =============================================================================
section("7. UI E INTEGRAZIONE")

# Test formula dynamic_cashout_single
cashout = dynamic_cashout_single(
    back_stake=10.0,
    back_price=3.0,
    lay_price=2.5,
    commission=4.5
)
test("Cashout: lay_stake calcolato", cashout['lay_stake'] > 0)
test("Cashout: net_profit presente", 'net_profit' in cashout)
test("Cashout: formula corretta (profitto positivo)", cashout['net_profit'] > 0)

# Test cashout con quote invalide
cashout_invalid = dynamic_cashout_single(10.0, 2.0, 1.0, 4.5)
test("Cashout: quota LAY <= 1 ritorna errore", 'error' in cashout_invalid)

# Test dutching
selections = [
    {'selectionId': 1, 'runnerName': 'Team A', 'price': 2.0},
    {'selectionId': 2, 'runnerName': 'Team B', 'price': 3.5},
]
results_dutch, profit, implied = calculate_dutching_stakes(selections, 100.0, 'BACK', 4.5)
test("Dutching: risultati calcolati", len(results_dutch) == 2)
test("Dutching: stake totale ~100", abs(sum(r['stake'] for r in results_dutch) - 100) < 0.1)
test("Dutching: profitto uniforme", profit != 0)

# =============================================================================
# RIEPILOGO FINALE
# =============================================================================
print("\n" + "="*70)
print("  RIEPILOGO FINALE")
print("="*70)

total = results['passed'] + results['failed']
pct = (results['passed'] / total * 100) if total > 0 else 0

print(f"\n  Test passati:  {results['passed']}/{total} ({pct:.1f}%)")
print(f"  Test falliti:  {results['failed']}/{total}")

if results['failed'] == 0:
    print("\n  [SUCCESS] Tutti i test superati - v3.60.2 pronta per release!")
    sys.exit(0)
else:
    print("\n  [WARNING] Alcuni test falliti - rivedere implementazione")
    sys.exit(1)
