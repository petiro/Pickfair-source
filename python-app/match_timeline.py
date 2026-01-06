"""
MatchTimeline Widget
====================
Barra trading-style che mostra tempo + goal live.
Stile professionale come Betfair/Fairbot.

Features:
- Colore dinamico (verde/arancio/rosso/grigio)
- Marker goal con tooltip
- Flash su nuovo goal
- Countdown recupero
- 100% non-bloccante
"""

import tkinter as tk
from tkinter import ttk
import logging

from live_context import LiveContext

log = logging.getLogger("MatchTimeline")


class MatchTimeline(ttk.Frame):
    """
    Widget barra timeline match.
    
    Colori:
    - Verde: match regolare
    - Arancio: ultimi 10 minuti / recupero
    - Rosso: mercato sospeso
    - Grigio: dati non disponibili
    """
    
    def __init__(self, parent, width=500, height=22, **kwargs):
        super().__init__(parent, **kwargs)
        
        self.width = width
        self.height = height
        self.max_min = 95
        
        self.ctx = LiveContext()
        self._seen_goals = set()
        self._injury_seconds = 0
        self.enable_goal_sound = True
        
        self._build_ui()
        
    def _build_ui(self):
        self.header_frame = ttk.Frame(self)
        self.header_frame.pack(fill="x", pady=(0, 2))
        
        self.team_label = ttk.Label(
            self.header_frame,
            text="",
            font=("Segoe UI", 9, "bold")
        )
        self.team_label.pack(side="left")
        
        self.score_label = ttk.Label(
            self.header_frame,
            text="",
            font=("Segoe UI", 10, "bold"),
            foreground="#2ecc71"
        )
        self.score_label.pack(side="right")
        
        self.canvas = tk.Canvas(
            self,
            width=self.width,
            height=self.height,
            bg="#1a1a2e",
            highlightthickness=0
        )
        self.canvas.pack(fill="x")
        
        self.period_frame = ttk.Frame(self)
        self.period_frame.pack(fill="x", pady=(2, 0))
        
        self.period_1t = ttk.Label(
            self.period_frame,
            text="1T",
            font=("Segoe UI", 8)
        )
        self.period_1t.pack(side="left")
        
        self.period_ht = ttk.Label(
            self.period_frame,
            text="HT",
            font=("Segoe UI", 8)
        )
        self.period_ht.pack(side="left", padx=(self.width//4, 0))
        
        self.period_2t = ttk.Label(
            self.period_frame,
            text="2T",
            font=("Segoe UI", 8)
        )
        self.period_2t.pack(side="left", padx=(self.width//4, 0))
        
        self.minute_label = ttk.Label(
            self.period_frame,
            text="",
            font=("Segoe UI", 9, "bold"),
            foreground="#2ecc71"
        )
        self.minute_label.pack(side="right")
        
        self.tooltip = ttk.Label(
            self,
            text="",
            background="#333",
            foreground="white",
            padding=4
        )
        
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda e: self.tooltip.place_forget())
        
    def update_context(self, ctx: LiveContext):
        """Aggiorna contesto e ridisegna."""
        old_goals = set(self.ctx.goal_minutes)
        self.ctx = ctx
        
        if ctx.injury_time and ctx.injury_time > 0:
            self._injury_seconds = ctx.injury_time * 60
        
        new_goals = set(ctx.goal_minutes) - old_goals - self._seen_goals
        if new_goals:
            self._flash_goal()
            if self.enable_goal_sound:
                self._play_goal_sound()
            self._seen_goals |= new_goals
            
        self._draw()
        self._update_labels()
        
    def _bar_color(self) -> str:
        """Determina colore barra basato su stato."""
        if self.ctx.market_status == "SUSPENDED":
            return "#e74c3c"
        if self.ctx.market_status == "CLOSED":
            return "#2c3e50"
        if self.ctx.market_status == "UNKNOWN" or self.ctx.minute is None:
            return "#555"
        
        if self.ctx.danger or (self.ctx.minute and self.ctx.minute >= 80):
            return "#f39c12"
        return "#2ecc71"
        
    def _draw(self):
        """Disegna la barra."""
        c = self.canvas
        c.delete("all")
        
        c.create_rectangle(0, 0, self.width, self.height, fill="#333", outline="")
        
        if self.ctx.minute is None:
            c.create_text(
                self.width // 2,
                self.height // 2,
                text="LIVE DATA N/A",
                fill="#888",
                font=("Segoe UI", 9)
            )
            return
            
        progress = min(self.ctx.minute / self.max_min, 1.0)
        bar_color = self._bar_color()
        
        c.create_rectangle(
            0, 0,
            int(self.width * progress),
            self.height,
            fill=bar_color,
            outline=""
        )
        
        half_x = int((45 / self.max_min) * self.width)
        c.create_line(half_x, 0, half_x, self.height, fill="#666", dash=(2, 2))
        
        for g in self.ctx.goal_minutes:
            x = int((g / self.max_min) * self.width)
            x = max(6, min(x, self.width - 6))
            c.create_oval(
                x - 5, (self.height // 2) - 5,
                x + 5, (self.height // 2) + 5,
                fill="white",
                outline="#333",
                tags=("goal", f"g{g}")
            )
            c.create_text(
                x, self.height // 2,
                text="\u26bd",
                font=("Segoe UI", 6),
                fill="#333"
            )
            
    def _update_labels(self):
        """Aggiorna label testuali."""
        if self.ctx.home_team and self.ctx.away_team:
            self.team_label.config(text=f"{self.ctx.home_team} vs {self.ctx.away_team}")
        
        self.score_label.config(
            text=f"{self.ctx.goals_home} - {self.ctx.goals_away}",
            foreground=self._bar_color()
        )
        
        if self.ctx.minute is not None:
            label = f"{self.ctx.minute}'"
            if self._injury_seconds > 0:
                mins = self._injury_seconds // 60
                secs = self._injury_seconds % 60
                label += f" +{mins}:{secs:02d}"
                self._injury_seconds = max(0, self._injury_seconds - 1)
            elif self.ctx.injury_time:
                label += f" +{self.ctx.injury_time}"
            self.minute_label.config(text=label, foreground=self._bar_color())
        else:
            self.minute_label.config(text="--'", foreground="#888")
            
    def _on_motion(self, event):
        """Mostra tooltip sui goal."""
        for g in self.ctx.goal_minutes:
            x = int((g / self.max_min) * self.width)
            if abs(event.x - x) < 8:
                text = self.ctx.goal_events.get(g, f"Goal {g}'")
                self.tooltip.config(text=text)
                self.tooltip.place(x=event.x + 10, y=event.y - 30)
                return
        self.tooltip.place_forget()
        
    def _flash_goal(self):
        """Flash visivo su nuovo goal."""
        original_bg = self.canvas.cget("bg")
        self.canvas.configure(bg="#f1c40f")
        self.after(150, lambda: self.canvas.configure(bg=original_bg))
        
    def _play_goal_sound(self):
        """Suono goal (Windows, non bloccante)."""
        try:
            import winsound
            winsound.PlaySound(
                "SystemExclamation",
                winsound.SND_ALIAS | winsound.SND_ASYNC
            )
        except Exception:
            pass
            
    def set_goal_sound(self, enabled: bool):
        """Abilita/disabilita suono goal."""
        self.enable_goal_sound = enabled
