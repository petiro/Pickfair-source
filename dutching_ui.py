"""
Dutching Confirmation Window - UI stile Bet Angel
Finestra separata per conferma e gestione dutching avanzato.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk
from typing import Callable, Optional, Dict, List

from theme import COLORS, FONTS
from dutching_state import DutchingState, DutchingMode, RunnerState
from dutching import (
    calculate_dutching_stakes, 
    calculate_mixed_dutching,
    validate_selections,
    format_currency,
    MIN_BACK_STAKE
)


class DutchingConfirmationWindow:
    """
    Finestra Dutching Confirmation con:
    - Header mercato
    - Selezione modalità (Stake Available / Required Profit)
    - Tabella runner reattiva
    - Footer con controlli globali
    """
    
    def __init__(self, parent, state: DutchingState, 
                 on_submit: Callable[[List[Dict]], None],
                 on_refresh_odds: Optional[Callable] = None):
        """
        Args:
            parent: Finestra padre
            state: DutchingState con dati mercato/runner
            on_submit: Callback per submit ordini
            on_refresh_odds: Callback per aggiornare quote live
        """
        self.parent = parent
        self.state = state
        self.on_submit = on_submit
        self.on_refresh_odds = on_refresh_odds
        
        # Crea finestra
        self.window = ctk.CTkToplevel(parent)
        self.window.title("Dutching Confirmation")
        self.window.configure(fg_color=COLORS['bg_dark'])
        
        # Dimensioni e posizione
        width, height = 800, 600
        x = parent.winfo_x() + (parent.winfo_width() - width) // 2
        y = parent.winfo_y() + (parent.winfo_height() - height) // 2
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.window.minsize(700, 500)
        
        # Modal
        self.window.transient(parent)
        self.window.grab_set()
        
        # Variabili UI
        self._mode_var = tk.StringVar(value="stake")
        self._stake_var = tk.StringVar(value=str(self.state.total_stake))
        self._profit_var = tk.StringVar(value=str(self.state.target_profit))
        self._auto_ratio_var = tk.BooleanVar(value=self.state.auto_ratio)
        self._live_odds_var = tk.BooleanVar(value=self.state.live_odds)
        self._global_offset_var = tk.StringVar(value="0")
        self._swap_all_var = tk.BooleanVar(value=False)
        
        # Mappa checkbox per runner
        self._runner_checkboxes: Dict[int, tk.BooleanVar] = {}
        self._runner_swap_vars: Dict[int, tk.BooleanVar] = {}
        self._runner_offset_vars: Dict[int, tk.StringVar] = {}
        self._runner_odds_vars: Dict[int, tk.StringVar] = {}
        
        # Mappa widget righe per aggiornamento
        self._runner_widgets: Dict[int, Dict] = {}
        
        # Costruisci UI
        self._build_ui()
        
        # Connetti callback stato
        self.state.set_callback(self._on_state_change)
        
        # Calcolo iniziale
        self._recalculate()
    
    def _build_ui(self):
        """Costruisce interfaccia completa."""
        main_frame = ctk.CTkFrame(self.window, fg_color=COLORS['bg_dark'])
        main_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)
        
        # 1. HEADER - Info mercato
        self._build_header(main_frame)
        
        # 2. DUTCHING TYPE - Selezione modalità
        self._build_mode_section(main_frame)
        
        # 3. TABELLA RUNNER
        self._build_runner_table(main_frame)
        
        # 4. FOOTER - Controlli globali
        self._build_footer(main_frame)
    
    def _build_header(self, parent):
        """Header con info mercato."""
        header_frame = ctk.CTkFrame(parent, fg_color=COLORS['bg_panel'], corner_radius=8)
        header_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Market info
        market_label = ctk.CTkLabel(
            header_frame, 
            text=self.state.market_display,
            font=FONTS['heading'],
            text_color=COLORS['text_primary']
        )
        market_label.pack(side=tk.LEFT, padx=15, pady=10)
        
        # Status badge
        status = self.state.market_status
        status_color = COLORS['profit'] if status == "OPEN" else COLORS['warning']
        status_text = "LIVE" if status == "OPEN" else status
        
        self.status_badge = ctk.CTkLabel(
            header_frame,
            text=status_text,
            font=FONTS['small'],
            text_color=COLORS['bg_dark'],
            fg_color=status_color,
            corner_radius=4,
            width=80
        )
        self.status_badge.pack(side=tk.RIGHT, padx=15, pady=10)
    
    def _build_mode_section(self, parent):
        """Sezione Dutching Type."""
        mode_frame = ctk.CTkFrame(parent, fg_color=COLORS['bg_surface'], corner_radius=8)
        mode_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Titolo
        title = ctk.CTkLabel(mode_frame, text="Dutching Type", 
                            font=FONTS['heading'], text_color=COLORS['text_primary'])
        title.grid(row=0, column=0, columnspan=4, sticky="w", padx=15, pady=(10, 5))
        
        # Radio: Stake Available
        stake_radio = ctk.CTkRadioButton(
            mode_frame, text="Stake Available",
            variable=self._mode_var, value="stake",
            command=self._on_mode_change,
            text_color=COLORS['text_primary'],
            fg_color=COLORS['back'],
            hover_color=COLORS['back_hover']
        )
        stake_radio.grid(row=1, column=0, padx=15, pady=5, sticky="w")
        
        # Input stake
        self.stake_entry = ctk.CTkEntry(
            mode_frame, textvariable=self._stake_var,
            width=100, font=FONTS['default'],
            fg_color=COLORS['bg_panel'],
            text_color=COLORS['text_primary'],
            border_color=COLORS['border']
        )
        self.stake_entry.grid(row=1, column=1, padx=5, pady=5)
        self.stake_entry.bind('<Return>', lambda e: self._recalculate())
        self.stake_entry.bind('<FocusOut>', lambda e: self._recalculate())
        
        # Radio: Required Profit
        profit_radio = ctk.CTkRadioButton(
            mode_frame, text="Required Profit",
            variable=self._mode_var, value="profit",
            command=self._on_mode_change,
            text_color=COLORS['text_primary'],
            fg_color=COLORS['back'],
            hover_color=COLORS['back_hover']
        )
        profit_radio.grid(row=2, column=0, padx=15, pady=5, sticky="w")
        
        # Input profit
        self.profit_entry = ctk.CTkEntry(
            mode_frame, textvariable=self._profit_var,
            width=100, font=FONTS['default'],
            fg_color=COLORS['bg_panel'],
            text_color=COLORS['text_primary'],
            border_color=COLORS['border']
        )
        self.profit_entry.grid(row=2, column=1, padx=5, pady=5)
        self.profit_entry.bind('<Return>', lambda e: self._recalculate())
        self.profit_entry.bind('<FocusOut>', lambda e: self._recalculate())
        
        # Checkbox: Automatic Ratio
        auto_check = ctk.CTkCheckBox(
            mode_frame, text="Automatic Ratio",
            variable=self._auto_ratio_var,
            command=self._on_auto_ratio_change,
            text_color=COLORS['text_primary'],
            fg_color=COLORS['back'],
            hover_color=COLORS['back_hover']
        )
        auto_check.grid(row=1, column=2, rowspan=2, padx=30, pady=5)
        
        # Book Value
        self.book_label = ctk.CTkLabel(
            mode_frame, text="Book Value: 0%",
            font=FONTS['default'],
            text_color=COLORS['text_secondary']
        )
        self.book_label.grid(row=1, column=3, rowspan=2, padx=15, pady=5, sticky="e")
        
        mode_frame.grid_columnconfigure(3, weight=1)
    
    def _build_runner_table(self, parent):
        """Tabella runner principale."""
        table_frame = ctk.CTkFrame(parent, fg_color=COLORS['bg_surface'], corner_radius=8)
        table_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # Header tabella
        headers = ["", "Selection", "Offset", "Swap", "Odds", "Stake", "Profit/Loss"]
        widths = [40, 200, 80, 60, 100, 100, 120]
        
        header_frame = ctk.CTkFrame(table_frame, fg_color=COLORS['bg_panel'], corner_radius=0)
        header_frame.pack(fill=tk.X)
        
        for i, (header, width) in enumerate(zip(headers, widths)):
            lbl = ctk.CTkLabel(
                header_frame, text=header,
                font=('Segoe UI', 10, 'bold'),
                text_color=COLORS['text_primary'],
                width=width
            )
            lbl.grid(row=0, column=i, padx=2, pady=8, sticky="w" if i < 2 else "")
        
        # Scrollable frame per runner
        self.runners_scroll = ctk.CTkScrollableFrame(
            table_frame, fg_color=COLORS['bg_surface'],
            scrollbar_button_color=COLORS['bg_panel'],
            scrollbar_button_hover_color=COLORS['bg_hover']
        )
        self.runners_scroll.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Popola righe
        self._populate_runner_rows()
    
    def _populate_runner_rows(self):
        """Crea righe per ogni runner."""
        # Pulisci righe esistenti
        for widget in self.runners_scroll.winfo_children():
            widget.destroy()
        
        self._runner_checkboxes.clear()
        self._runner_swap_vars.clear()
        self._runner_offset_vars.clear()
        self._runner_odds_vars.clear()
        
        for idx, runner in enumerate(self.state.runners):
            self._create_runner_row(idx, runner)
    
    def _create_runner_row(self, idx: int, runner: RunnerState):
        """Crea singola riga runner."""
        row_frame = ctk.CTkFrame(
            self.runners_scroll,
            fg_color=COLORS['bg_panel'] if idx % 2 == 0 else COLORS['bg_surface'],
            corner_radius=4
        )
        row_frame.pack(fill=tk.X, pady=1)
        
        sel_id = runner.selection_id
        
        # Checkbox inclusione
        include_var = tk.BooleanVar(value=runner.included)
        self._runner_checkboxes[sel_id] = include_var
        
        check = ctk.CTkCheckBox(
            row_frame, text="",
            variable=include_var,
            command=lambda s=sel_id: self._toggle_runner(s),
            width=40,
            fg_color=COLORS['back'],
            hover_color=COLORS['back_hover']
        )
        check.grid(row=0, column=0, padx=5, pady=8)
        
        # Nome selezione
        name_label = ctk.CTkLabel(
            row_frame, text=runner.runner_name,
            font=FONTS['default'],
            text_color=COLORS['text_primary'] if runner.included else COLORS['text_tertiary'],
            width=200, anchor="w"
        )
        name_label.grid(row=0, column=1, padx=5, pady=8, sticky="w")
        
        # Offset spinbox
        offset_var = tk.StringVar(value=str(runner.offset))
        self._runner_offset_vars[sel_id] = offset_var
        
        offset_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
        offset_frame.grid(row=0, column=2, padx=5, pady=8)
        
        offset_down = ctk.CTkButton(
            offset_frame, text="-", width=25,
            command=lambda s=sel_id: self._adjust_offset(s, -1),
            fg_color=COLORS['button_secondary']
        )
        offset_down.pack(side=tk.LEFT)
        
        offset_entry = ctk.CTkEntry(
            offset_frame, textvariable=offset_var,
            width=40, font=FONTS['small'],
            fg_color=COLORS['bg_surface'],
            text_color=COLORS['text_primary'],
            justify="center"
        )
        offset_entry.pack(side=tk.LEFT, padx=2)
        
        offset_up = ctk.CTkButton(
            offset_frame, text="+", width=25,
            command=lambda s=sel_id: self._adjust_offset(s, 1),
            fg_color=COLORS['button_secondary']
        )
        offset_up.pack(side=tk.LEFT)
        
        # Swap checkbox (BACK/LAY)
        swap_var = tk.BooleanVar(value=runner.swap)
        self._runner_swap_vars[sel_id] = swap_var
        
        swap_check = ctk.CTkCheckBox(
            row_frame, text="",
            variable=swap_var,
            command=lambda s=sel_id: self._toggle_swap(s),
            width=60,
            fg_color=COLORS['lay'],
            hover_color=COLORS['lay_hover']
        )
        swap_check.grid(row=0, column=3, padx=5, pady=8)
        
        # Odds entry
        odds_var = tk.StringVar(value=f"{runner.effective_odds:.2f}")
        self._runner_odds_vars[sel_id] = odds_var
        
        odds_entry = ctk.CTkEntry(
            row_frame, textvariable=odds_var,
            width=80, font=FONTS['mono'],
            fg_color=COLORS['bg_surface'],
            text_color=COLORS['back'] if not runner.swap else COLORS['lay'],
            justify="center"
        )
        odds_entry.grid(row=0, column=4, padx=5, pady=8)
        odds_entry.bind('<Return>', lambda e, s=sel_id: self._on_odds_change(s))
        odds_entry.bind('<FocusOut>', lambda e, s=sel_id: self._on_odds_change(s))
        
        # Stake label
        stake_text = f"€{runner.stake:.2f}" if runner.included else "€0"
        stake_label = ctk.CTkLabel(
            row_frame, text=stake_text,
            font=FONTS['mono'],
            text_color=COLORS['text_primary'],
            width=100
        )
        stake_label.grid(row=0, column=5, padx=5, pady=8)
        
        # Profit/Loss label
        profit = runner.profit_if_wins
        if runner.included:
            profit_color = COLORS['profit'] if profit >= 0 else COLORS['loss']
            profit_text = f"€{profit:.2f}"
        else:
            profit_color = COLORS['loss']
            profit_text = f"-€{self.state.get_total_stake():.2f}"
        
        profit_label = ctk.CTkLabel(
            row_frame, text=profit_text,
            font=('Segoe UI', 11, 'bold'),
            text_color=profit_color,
            width=120
        )
        profit_label.grid(row=0, column=6, padx=5, pady=8)
        
        # Salva riferimenti per update
        self._runner_widgets[sel_id] = {
            'stake_label': stake_label,
            'profit_label': profit_label,
            'name_label': name_label,
            'odds_entry': odds_entry,
        }
    
    def _build_footer(self, parent):
        """Footer con controlli globali."""
        footer_frame = ctk.CTkFrame(parent, fg_color=COLORS['bg_panel'], corner_radius=8)
        footer_frame.pack(fill=tk.X)
        
        # Riga 1: Controlli
        controls_frame = ctk.CTkFrame(footer_frame, fg_color="transparent")
        controls_frame.pack(fill=tk.X, padx=15, pady=10)
        
        # Live Odds checkbox
        live_check = ctk.CTkCheckBox(
            controls_frame, text="Live Odds",
            variable=self._live_odds_var,
            text_color=COLORS['text_primary'],
            fg_color=COLORS['back'],
            hover_color=COLORS['back_hover']
        )
        live_check.pack(side=tk.LEFT, padx=(0, 20))
        
        # Global Offset
        offset_label = ctk.CTkLabel(controls_frame, text="Global Offset:",
                                    text_color=COLORS['text_primary'])
        offset_label.pack(side=tk.LEFT, padx=(0, 5))
        
        offset_entry = ctk.CTkEntry(
            controls_frame, textvariable=self._global_offset_var,
            width=60, font=FONTS['small'],
            fg_color=COLORS['bg_surface'],
            text_color=COLORS['text_primary'],
            justify="center"
        )
        offset_entry.pack(side=tk.LEFT, padx=(0, 20))
        offset_entry.bind('<Return>', self._on_global_offset_change)
        
        # Swap All checkbox
        swap_all = ctk.CTkCheckBox(
            controls_frame, text="Swap All",
            variable=self._swap_all_var,
            command=self._on_swap_all,
            text_color=COLORS['text_primary'],
            fg_color=COLORS['lay'],
            hover_color=COLORS['lay_hover']
        )
        swap_all.pack(side=tk.LEFT, padx=(0, 20))
        
        # Total label
        self.total_label = ctk.CTkLabel(
            controls_frame, text="Total: €0.00",
            font=FONTS['heading'],
            text_color=COLORS['back']
        )
        self.total_label.pack(side=tk.RIGHT)
        
        # Riga 2: Pulsanti
        buttons_frame = ctk.CTkFrame(footer_frame, fg_color="transparent")
        buttons_frame.pack(fill=tk.X, padx=15, pady=(0, 15))
        
        # Select All
        select_all_btn = ctk.CTkButton(
            buttons_frame, text="Select All",
            command=self._select_all,
            fg_color=COLORS['button_secondary'],
            hover_color=COLORS['bg_hover'],
            width=100
        )
        select_all_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        # Select None
        select_none_btn = ctk.CTkButton(
            buttons_frame, text="Select None",
            command=self._select_none,
            fg_color=COLORS['button_secondary'],
            hover_color=COLORS['bg_hover'],
            width=100
        )
        select_none_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        # Refresh Odds
        refresh_btn = ctk.CTkButton(
            buttons_frame, text="Refresh Odds",
            command=self._refresh_odds,
            fg_color=COLORS['info'],
            hover_color=COLORS['info_hover'],
            width=120
        )
        refresh_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        # Spacer
        spacer = ctk.CTkFrame(buttons_frame, fg_color="transparent")
        spacer.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # Cancel
        cancel_btn = ctk.CTkButton(
            buttons_frame, text="Cancel",
            command=self._on_cancel,
            fg_color=COLORS['button_secondary'],
            hover_color=COLORS['bg_hover'],
            width=100
        )
        cancel_btn.pack(side=tk.RIGHT, padx=(10, 0))
        
        # Submit
        self.submit_btn = ctk.CTkButton(
            buttons_frame, text="Submit",
            command=self._on_submit,
            fg_color=COLORS['profit'],
            hover_color=COLORS['button_success'],
            width=120
        )
        self.submit_btn.pack(side=tk.RIGHT)
    
    # === EVENT HANDLERS ===
    
    def _on_mode_change(self):
        """Cambio modalità dutching."""
        mode = self._mode_var.get()
        self.state.mode = DutchingMode.STAKE_AVAILABLE if mode == "stake" else DutchingMode.REQUIRED_PROFIT
        self._recalculate()
    
    def _on_auto_ratio_change(self):
        """Toggle auto ratio."""
        self.state.auto_ratio = self._auto_ratio_var.get()
        self._recalculate()
    
    def _toggle_runner(self, selection_id: int):
        """Toggle inclusione runner."""
        self.state.toggle_included(selection_id)
        self._recalculate()
    
    def _toggle_swap(self, selection_id: int):
        """Toggle BACK/LAY."""
        self.state.toggle_swap(selection_id)
        self._recalculate()
    
    def _adjust_offset(self, selection_id: int, delta: int):
        """Adjust offset tick."""
        for r in self.state.runners:
            if r.selection_id == selection_id:
                new_offset = r.offset + delta
                self.state.set_offset(selection_id, new_offset)
                self._runner_offset_vars[selection_id].set(str(new_offset))
                break
        self._recalculate()
    
    def _on_odds_change(self, selection_id: int):
        """Cambio manuale quota."""
        try:
            new_odds = float(self._runner_odds_vars[selection_id].get())
            if new_odds >= 1.01:
                self.state.set_odds(selection_id, new_odds)
                self._recalculate()
        except ValueError:
            pass
    
    def _on_global_offset_change(self, event=None):
        """Cambio offset globale."""
        try:
            offset = int(self._global_offset_var.get())
            self.state.global_offset = offset
            # Update tutti gli offset vars
            for sel_id in self._runner_offset_vars:
                self._runner_offset_vars[sel_id].set(str(offset))
            self._recalculate()
        except ValueError:
            pass
    
    def _on_swap_all(self):
        """Swap all BACK/LAY."""
        self.state.swap_all()
        # Update swap vars
        for runner in self.state.runners:
            if runner.selection_id in self._runner_swap_vars:
                self._runner_swap_vars[runner.selection_id].set(runner.swap)
        self._recalculate()
    
    def _select_all(self):
        """Seleziona tutti."""
        self.state.select_all()
        for runner in self.state.runners:
            if runner.selection_id in self._runner_checkboxes:
                self._runner_checkboxes[runner.selection_id].set(runner.included)
        self._recalculate()
    
    def _select_none(self):
        """Deseleziona tutti."""
        self.state.select_none()
        for sel_id in self._runner_checkboxes:
            self._runner_checkboxes[sel_id].set(False)
        self._recalculate()
    
    def _refresh_odds(self):
        """Aggiorna quote live."""
        if self.on_refresh_odds:
            self.on_refresh_odds()
    
    def _on_cancel(self):
        """Chiudi finestra."""
        self.window.destroy()
    
    def _on_submit(self):
        """Submit ordini."""
        orders = self.state.get_orders_to_place()
        
        if not orders:
            messagebox.showwarning("Attenzione", "Nessun ordine da piazzare.", parent=self.window)
            return
        
        # Validazione
        errors = validate_selections(
            [{'runnerName': o['runnerName'], 'stake': o['size'], 'price': o['price']} for o in orders],
            bet_type='BACK'
        )
        
        if errors:
            messagebox.showerror("Errori Validazione", "\n".join(errors), parent=self.window)
            return
        
        # Conferma
        total = sum(o['size'] for o in orders)
        msg = f"Piazzare {len(orders)} ordini per totale €{total:.2f}?"
        
        if messagebox.askyesno("Conferma", msg, parent=self.window):
            self.on_submit(orders)
            self.window.destroy()
    
    def _on_state_change(self):
        """Callback da DutchingState - ricalcola."""
        if self.state.auto_ratio:
            self._recalculate()
    
    def _recalculate(self):
        """Ricalcola stake e aggiorna UI."""
        try:
            # Leggi parametri
            mode = self._mode_var.get()
            
            if mode == "stake":
                try:
                    total = float(self._stake_var.get())
                except ValueError:
                    total = 100.0
                self.state._total_stake = total
            else:
                try:
                    profit = float(self._profit_var.get())
                except ValueError:
                    profit = 10.0
                self.state._target_profit = profit
            
            # Ottieni selezioni
            selections = self.state.get_selections_for_engine()
            
            if not selections:
                self._update_totals(0, 0)
                return
            
            # Determina se mixed
            has_back = any(s['effectiveType'] == 'BACK' for s in selections)
            has_lay = any(s['effectiveType'] == 'LAY' for s in selections)
            
            if mode == "stake":
                total_stake = self.state.total_stake
            else:
                # Per Required Profit, calcola stake necessario
                # Semplificazione: usa formula base
                avg_odds = sum(s['price'] for s in selections) / len(selections)
                total_stake = self.state.target_profit / (avg_odds - 1) * len(selections)
            
            # Calcola
            if has_back and has_lay:
                results, avg_profit, book = calculate_mixed_dutching(
                    selections, total_stake, self.state.commission
                )
            elif has_lay and not has_back:
                results, avg_profit, book = calculate_dutching_stakes(
                    selections, total_stake, 'LAY', self.state.commission
                )
            else:
                results, avg_profit, book = calculate_dutching_stakes(
                    selections, total_stake, 'BACK', self.state.commission
                )
            
            # Applica risultati
            self.state.apply_calculation_results(results)
            
            # Aggiorna UI
            self._update_runner_display()
            self._update_totals(self.state.get_total_stake(), book)
            
        except Exception as e:
            print(f"[Dutching] Errore calcolo: {e}")
            self._update_totals(0, 0)
    
    def _update_runner_display(self):
        """Aggiorna display righe runner."""
        for runner in self.state.runners:
            sel_id = runner.selection_id
            
            if sel_id not in self._runner_widgets:
                continue
            
            widgets = self._runner_widgets[sel_id]
            
            # Stake
            stake_text = f"€{runner.stake:.2f}" if runner.included else "€0"
            widgets['stake_label'].configure(text=stake_text)
            
            # Profit
            profit = runner.profit_if_wins
            total_stake = self.state.get_total_stake()
            
            if runner.included:
                profit_color = COLORS['profit'] if profit >= 0 else COLORS['loss']
                profit_text = f"€{profit:.2f}"
            else:
                profit_color = COLORS['loss']
                profit_text = f"-€{total_stake:.2f}"
            
            widgets['profit_label'].configure(text=profit_text, text_color=profit_color)
            
            # Nome colore
            name_color = COLORS['text_primary'] if runner.included else COLORS['text_tertiary']
            widgets['name_label'].configure(text_color=name_color)
            
            # Odds colore
            odds_color = COLORS['lay'] if runner.swap else COLORS['back']
            widgets['odds_entry'].configure(text_color=odds_color)
    
    def _update_totals(self, total_stake: float, book_value: float):
        """Aggiorna totali footer."""
        self.total_label.configure(text=f"Total: €{total_stake:.2f}")
        self.book_label.configure(text=f"Book Value: {book_value:.1f}%")


def open_dutching_window(parent, market_data: Dict, runners: List[Dict],
                         on_submit: Callable, on_refresh: Optional[Callable] = None):
    """
    Helper per aprire finestra dutching.
    
    Args:
        parent: Finestra padre
        market_data: {'marketId', 'marketName', 'eventName', 'startTime', 'status'}
        runners: [{'selectionId', 'runnerName', 'price'}]
        on_submit: Callback(orders: List[Dict])
        on_refresh: Callback per refresh odds
    """
    state = DutchingState()
    
    state.set_market_info(
        market_id=market_data.get('marketId', ''),
        market_name=market_data.get('marketName', ''),
        event_name=market_data.get('eventName', ''),
        start_time=market_data.get('startTime', ''),
        status=market_data.get('status', 'OPEN')
    )
    
    state.load_runners(runners)
    
    window = DutchingConfirmationWindow(
        parent=parent,
        state=state,
        on_submit=on_submit,
        on_refresh_odds=on_refresh
    )
    
    return window
