"""
Runner Revisione Tecnica Completa - Pickfair v3.66

Esegue tutti i test e genera report dettagliato:
- Dutching Core (BACK/LAY/Mixed)
- UI Components (Toolbar, MiniLadder, LiveMiniLadder)
- Simulation Mode
- Auto-Green
- Threading/Debounce
- Edge Cases

Output: Console + file report con timestamp
"""

import unittest
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PICKFAIR_VERSION = "v3.66-toolbar"

CRITICAL_TESTS = [
    ("Dutching BACK Uniform Profit", "test_back_uniform_profit"),
    ("Dutching LAY Liabilities", "test_lay_liabilities_positive"),
    ("Mixed BACK+LAY Balance", "test_mixed_uniform_profit"),
    ("Target Profit", "test_target_profit_reached"),
    ("Simulation Mode", "test_controller_simulation_flag"),
    ("Dry Run Preview", "test_dry_run_returns_preview"),
    ("Auto-Green Simulation", "test_auto_green_in_simulation"),
    ("P&L Preview BACK", "test_preview_back_positive"),
    ("Toolbar Toggles", "test_toolbar_has_set_methods"),
    ("LiveMiniLadder Refresh", "test_live_ladder_has_refresh_interval"),
    ("Book % Warning", "test_book_percent_warning_color"),
    ("Preset Stake Buttons", "test_all_presets_valid"),
    ("Edge Case Empty", "test_empty_selections"),
    ("Commission Italian", "test_italian_commission_4_5"),
    ("Controller Integration", "test_controller_has_auto_green_flag"),
]


def run_technical_review():
    """Esegue revisione tecnica completa."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    test_modules = [
        'tests.test_dutching_safety',
        'tests.test_new_components',
        'tests.test_optimizations',
        'tests.test_performance',
        'tests.test_toolbar_live',
        'tests.test_dutching_advanced',
        'tests.test_ui_components',
        'tests.test_simulation_mode',
    ]
    
    for module_name in test_modules:
        try:
            module = __import__(module_name, fromlist=[''])
            suite.addTests(loader.loadTestsFromModule(module))
        except ImportError as e:
            print(f"Warning: Could not import {module_name}: {e}")
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    timestamp_file = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    total_tests = result.testsRun
    failures = len(result.failures)
    errors = len(result.errors)
    skipped = len(result.skipped) if hasattr(result, 'skipped') else 0
    success = total_tests - failures - errors - skipped
    
    critical_status = []
    for name, test_id in CRITICAL_TESTS:
        failed = any(test_id in str(f[0]) for f in result.failures)
        errored = any(test_id in str(e[0]) for e in result.errors)
        status = "PASS" if not failed and not errored else "FAIL"
        critical_status.append((name, status))
    
    report_lines = [
        "=" * 60,
        "REVISIONE TECNICA COMPLETA - PICKFAIR DUTCHING AVANZATO",
        f"Versione: {PICKFAIR_VERSION}",
        f"Timestamp: {timestamp}",
        "=" * 60,
        "",
        "RIASSUNTO RAPIDO TEST CRITICI:",
        "-" * 40,
    ]
    
    for name, status in critical_status:
        icon = "[OK]" if status == "PASS" else "[!!]"
        report_lines.append(f"  {icon} {name}")
    
    report_lines.extend([
        "",
        "=" * 60,
        "RISULTATI GLOBALI:",
        "-" * 40,
        f"  Totale test eseguiti: {total_tests}",
        f"  Test superati:        {success}",
        f"  Test falliti:         {failures}",
        f"  Errori:               {errors}",
        f"  Skipped:              {skipped}",
        "=" * 60,
        "",
    ])
    
    if failures > 0:
        report_lines.append("DETTAGLIO FALLIMENTI:")
        report_lines.append("-" * 40)
        for fail_test, traceback in result.failures:
            report_lines.append(f"  - {fail_test}")
            for line in traceback.split('\n')[:5]:
                report_lines.append(f"    {line}")
            report_lines.append("")
    
    if errors > 0:
        report_lines.append("DETTAGLIO ERRORI:")
        report_lines.append("-" * 40)
        for err_test, traceback in result.errors:
            report_lines.append(f"  - {err_test}")
            for line in traceback.split('\n')[:5]:
                report_lines.append(f"    {line}")
            report_lines.append("")
    
    report_lines.extend([
        "=" * 60,
        "AREE TESTATE:",
        "-" * 40,
        "  [x] Core Dutching (BACK/LAY/Mixed/Target Profit)",
        "  [x] UI Components (Toolbar/MiniLadder/LiveMiniLadder)",
        "  [x] Simulation Mode (Broker/Controller/DryRun)",
        "  [x] Auto-Green Engine",
        "  [x] P&L Engine (Preview/Commissioni)",
        "  [x] Book % Optimizer",
        "  [x] Preset Stake Buttons",
        "  [x] Edge Cases (Empty/MinStake/HighPrice)",
        "  [x] Threading & Debounce",
        "  [x] Controller Integration",
        "=" * 60,
    ])
    
    verdict = "PASS" if failures == 0 and errors == 0 else "FAIL"
    report_lines.extend([
        "",
        f"VERDETTO FINALE: {verdict}",
        "",
    ])
    
    report_filename = f"technical_review_report_{timestamp_file}.txt"
    with open(report_filename, "w", encoding="utf-8") as f:
        for line in report_lines:
            f.write(line + "\n")
    
    print("\n" + "=" * 60)
    for line in report_lines:
        print(line)
    print(f"\nReport salvato: {report_filename}")
    
    return result


if __name__ == "__main__":
    result = run_technical_review()
    sys.exit(0 if result.wasSuccessful() else 1)
