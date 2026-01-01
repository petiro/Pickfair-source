"""
MiniLadder - Mini ladder inline PRO per dutching UI

Mostra 3 livelli BACK/LAY per ogni runner con highlight del best price.
Stile professionale tipo Bet Angel.

v3.65 - One-Click Actions con preflight automatico
"""

import customtkinter as ctk
from typing import Dict, List, Optional, Callable, TYPE_CHECKING

from theme import COLORS

if TYPE_CHECKING:
    from controllers.dutching_controller import DutchingController


class MiniLadder(ctk.CTkFrame):
    """
    Mini ladder inline che mostra 3 livelli BACK e LAY.
    
    Features:
    - Best price evidenziato con bordo verde
    - Click su prezzo per selezione rapida
    - Aggiornamento real-time via update_prices()
    """
    
    def __init__(
        self, 
        parent, 
        runner: Dict,
        on_price_click: Optional[Callable] = None,
        levels: int = 3
    ):
        """
        Args:
            parent: Widget parent
            runner: Dict con runnerName, selectionId, back_ladder, lay_ladder
            on_price_click: Callback(selection_id, side, price) su click prezzo
            levels: Numero livelli da mostrare (default 3)
        """
        super().__init__(parent, fg_color="transparent")
        
        self.runner = runner
        self.on_price_click = on_price_click
        self.levels = levels
        
        self.back_labels = []
        self.lay_labels = []
        
        self._build()
    
    def _build(self):
        """Costruisce UI della mini ladder."""
        # Header con nome runner
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", pady=(0, 2))
        
        ctk.CTkLabel(
            header,
            text=self.runner.get("runnerName", "Runner"),
            font=("Roboto", 12, "bold"),
            anchor="w"
        ).pack(side="left", fill="x", expand=True)
        
        # Container prezzi
        prices_frame = ctk.CTkFrame(self, fg_color=COLORS.get("bg_secondary", "#2b2b2b"))
        prices_frame.pack(fill="x")
        
        # Colonna BACK (sinistra)
        back_col = ctk.CTkFrame(prices_frame, fg_color="transparent")
        back_col.pack(side="left", expand=True, fill="both", padx=1)
        
        ctk.CTkLabel(
            back_col,
            text="BACK",
            font=("Roboto", 9),
            text_color=COLORS.get("back", "#1e88e5")
        ).pack()
        
        # Crea label per livelli BACK
        for i in range(self.levels):
            lbl = ctk.CTkLabel(
                back_col,
                text="-",
                font=("Roboto", 10),
                fg_color=COLORS.get("back_bg", "#1e3a5f"),
                corner_radius=3,
                width=70,
                height=22
            )
            lbl.pack(pady=1)
            lbl.bind("<Button-1>", lambda e, idx=i: self._on_back_click(idx))
            self.back_labels.append(lbl)
        
        # Colonna LAY (destra)
        lay_col = ctk.CTkFrame(prices_frame, fg_color="transparent")
        lay_col.pack(side="left", expand=True, fill="both", padx=1)
        
        ctk.CTkLabel(
            lay_col,
            text="LAY",
            font=("Roboto", 9),
            text_color=COLORS.get("lay", "#e5399b")
        ).pack()
        
        # Crea label per livelli LAY
        for i in range(self.levels):
            lbl = ctk.CTkLabel(
                lay_col,
                text="-",
                font=("Roboto", 10),
                fg_color=COLORS.get("lay_bg", "#5f1e3a"),
                corner_radius=3,
                width=70,
                height=22
            )
            lbl.pack(pady=1)
            lbl.bind("<Button-1>", lambda e, idx=i: self._on_lay_click(idx))
            self.lay_labels.append(lbl)
        
        # Aggiorna con dati iniziali
        self.update_prices(self.runner)
    
    def update_prices(self, runner: Dict):
        """
        Aggiorna prezzi visualizzati.
        
        Args:
            runner: Dict con back_ladder e lay_ladder aggiornati
        """
        self.runner = runner
        
        back_ladder = runner.get("back_ladder", [])[:self.levels]
        lay_ladder = runner.get("lay_ladder", [])[:self.levels]
        
        # Best prices
        best_back = back_ladder[0]["price"] if back_ladder else None
        best_lay = lay_ladder[0]["price"] if lay_ladder else None
        
        # Aggiorna BACK labels
        for i, lbl in enumerate(self.back_labels):
            if i < len(back_ladder):
                p = back_ladder[i]
                price = p.get("price", 0)
                size = p.get("size", 0)
                lbl.configure(text=f"{price:.2f} (€{size:.0f})")
                
                # Highlight best price
                if price == best_back:
                    lbl.configure(
                        fg_color=COLORS.get("back", "#1e88e5"),
                        text_color="white"
                    )
                else:
                    lbl.configure(
                        fg_color=COLORS.get("back_bg", "#1e3a5f"),
                        text_color=COLORS.get("text", "#ffffff")
                    )
            else:
                lbl.configure(text="-")
                lbl.configure(
                    fg_color=COLORS.get("back_bg", "#1e3a5f"),
                    text_color=COLORS.get("text_secondary", "#888888")
                )
        
        # Aggiorna LAY labels
        for i, lbl in enumerate(self.lay_labels):
            if i < len(lay_ladder):
                p = lay_ladder[i]
                price = p.get("price", 0)
                size = p.get("size", 0)
                lbl.configure(text=f"{price:.2f} (€{size:.0f})")
                
                # Highlight best price
                if price == best_lay:
                    lbl.configure(
                        fg_color=COLORS.get("lay", "#e5399b"),
                        text_color="white"
                    )
                else:
                    lbl.configure(
                        fg_color=COLORS.get("lay_bg", "#5f1e3a"),
                        text_color=COLORS.get("text", "#ffffff")
                    )
            else:
                lbl.configure(text="-")
                lbl.configure(
                    fg_color=COLORS.get("lay_bg", "#5f1e3a"),
                    text_color=COLORS.get("text_secondary", "#888888")
                )
    
    def _on_back_click(self, index: int):
        """Handler click su prezzo BACK."""
        if not self.on_price_click:
            return
        
        back_ladder = self.runner.get("back_ladder", [])
        if index < len(back_ladder):
            price = back_ladder[index].get("price", 0)
            self.on_price_click(
                self.runner.get("selectionId"),
                "BACK",
                price
            )
    
    def _on_lay_click(self, index: int):
        """Handler click su prezzo LAY."""
        if not self.on_price_click:
            return
        
        lay_ladder = self.runner.get("lay_ladder", [])
        if index < len(lay_ladder):
            price = lay_ladder[index].get("price", 0)
            self.on_price_click(
                self.runner.get("selectionId"),
                "LAY",
                price
            )
    
    def set_highlight(self, side: str, enabled: bool = True):
        """
        Evidenzia un lato (per mostrare selezione AI).
        
        Args:
            side: 'BACK' o 'LAY'
            enabled: True per evidenziare
        """
        if side == "BACK":
            for lbl in self.back_labels:
                if enabled:
                    lbl.configure(border_width=2, border_color="#00ff00")
                else:
                    lbl.configure(border_width=0)
        else:
            for lbl in self.lay_labels:
                if enabled:
                    lbl.configure(border_width=2, border_color="#00ff00")
                else:
                    lbl.configure(border_width=0)
    
    def set_edge_badge(self, edge_score: float, confidence: float):
        """
        Mostra badge edge AI sotto la ladder.
        
        Args:
            edge_score: Score [-1, 1] dove + = BACK, - = LAY
            confidence: Confidenza [0, 1]
        """
        if not hasattr(self, "_edge_badge"):
            self._edge_badge = ctk.CTkLabel(
                self,
                text="",
                font=("Roboto", 9),
                corner_radius=3,
                width=60,
                height=18
            )
            self._edge_badge.pack(pady=(2, 0))
        
        if abs(edge_score) < 0.1:
            self._edge_badge.configure(
                text=f"NEUTRAL {confidence:.0%}",
                fg_color=COLORS.get("bg_tertiary", "#444444"),
                text_color=COLORS.get("text_secondary", "#888888")
            )
        elif edge_score > 0:
            strength = "STRONG " if edge_score > 0.5 else ""
            self._edge_badge.configure(
                text=f"{strength}BACK {confidence:.0%}",
                fg_color=COLORS.get("back", "#1e88e5"),
                text_color="white"
            )
        else:
            strength = "STRONG " if edge_score < -0.5 else ""
            self._edge_badge.configure(
                text=f"{strength}LAY {confidence:.0%}",
                fg_color=COLORS.get("lay", "#e5399b"),
                text_color="white"
            )


class OneClickLadder(MiniLadder):
    """
    MiniLadder con supporto one-click order.
    
    Click su best price:
    1. Esegue preflight_check automatico
    2. Piazza ordine singolo (non dutching) se preflight OK
    3. Attiva Auto-Green se toggle abilitato
    """
    
    def __init__(
        self,
        parent,
        runner: Dict,
        controller: Optional["DutchingController"] = None,
        market_id: str = "",
        market_type: str = "MATCH_ODDS",
        default_stake: float = 10.0,
        auto_green: bool = False,
        on_order_result: Optional[Callable] = None,
        **kwargs
    ):
        """
        Args:
            controller: DutchingController per piazzamento ordini
            market_id: ID mercato Betfair
            market_type: Tipo mercato
            default_stake: Stake default per one-click
            auto_green: Se abilitare auto-green
            on_order_result: Callback(result_dict) dopo piazzamento
        """
        self.controller = controller
        self.market_id = market_id
        self.market_type = market_type
        self.default_stake = default_stake
        self.auto_green_enabled = auto_green
        self.on_order_result = on_order_result
        
        super().__init__(parent, runner, on_price_click=self._handle_one_click, **kwargs)
    
    def _handle_one_click(self, selection_id: int, side: str, price: float):
        """
        Gestisce one-click order.
        
        Args:
            selection_id: ID runner
            side: 'BACK' o 'LAY'
            price: Prezzo cliccato
        """
        if not self.controller:
            return
        
        selection = {
            "selectionId": selection_id,
            "runnerName": self.runner.get("runnerName", f"Runner {selection_id}"),
            "price": price,
            "back_ladder": self.runner.get("back_ladder", []),
            "lay_ladder": self.runner.get("lay_ladder", [])
        }
        
        result = self.controller.submit_dutching(
            market_id=self.market_id,
            market_type=self.market_type,
            selections=[selection],
            total_stake=self.default_stake,
            mode=side,
            ai_enabled=False,
            auto_green=self.auto_green_enabled,
            dry_run=False
        )
        
        if self.on_order_result:
            self.on_order_result(result)
    
    def set_default_stake(self, stake: float):
        """Imposta stake default per one-click."""
        self.default_stake = stake
    
    def set_auto_green(self, enabled: bool):
        """Abilita/disabilita auto-green per one-click."""
        self.auto_green_enabled = enabled
