"""
Trading Charts - Grafici real-time per quote Betfair

Widget matplotlib con aggiornamento asincrono (nessun freeze UI).
"""

import logging
import threading
import tkinter as tk
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Lazy import - matplotlib loaded only when needed (prevents startup crash)
_matplotlib_loaded = False
_plt = None
_FigureCanvasTkAgg = None
_Figure = None
_Rectangle = None

def _lazy_import_matplotlib():
    """Import matplotlib lazily to avoid startup crashes with PyInstaller."""
    global _matplotlib_loaded, _plt, _FigureCanvasTkAgg, _Figure, _Rectangle
    
    if _matplotlib_loaded:
        return _matplotlib_loaded
    
    try:
        logger.debug("Lazy loading matplotlib...")
        import matplotlib
        matplotlib.use('TkAgg')  # Use TkAgg for Tkinter integration
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure
        from matplotlib.patches import Rectangle
        
        _plt = plt
        _FigureCanvasTkAgg = FigureCanvasTkAgg
        _Figure = Figure
        _Rectangle = Rectangle
        _matplotlib_loaded = True
        logger.info(f"matplotlib {matplotlib.__version__} loaded successfully (lazy)")
    except ImportError as ie:
        logger.warning(f"matplotlib not available: {ie}")
        _matplotlib_loaded = False
    except Exception as e:
        logger.warning(f"matplotlib load error: {e}")
        _matplotlib_loaded = False
    
    return _matplotlib_loaded

def HAS_MATPLOTLIB():
    """Check if matplotlib is available (loads it lazily on first call)."""
    return _lazy_import_matplotlib()


CHART_COLORS = {
    'bg': '#1a1a2e',
    'grid': '#2d2d44',
    'text': '#b0b0b0',
    'back': '#1e88e5',
    'lay': '#e5399b',
    'up': '#4caf50',
    'down': '#f44336',
    'volume': '#666688'
}


class QuoteLineChart:
    """Grafico a linea per quote real-time."""
    
    def __init__(self, parent: tk.Widget, width: int = 400, height: int = 200,
                 tick_storage=None, uiq=None):
        self.parent = parent
        self.tick_storage = tick_storage
        self.uiq = uiq
        self.selection_id = None
        self.selection_name = ""
        self._update_job = None
        self._destroyed = False
        
        if not HAS_MATPLOTLIB():
            self.frame = tk.Frame(parent, bg=CHART_COLORS['bg'])
            tk.Label(self.frame, text="Grafici non disponibili", 
                     fg='gray', bg=CHART_COLORS['bg']).pack()
            return
        
        self.frame = tk.Frame(parent, bg=CHART_COLORS['bg'])
        
        self.fig = _Figure(figsize=(width/100, height/100), dpi=100, 
                          facecolor=CHART_COLORS['bg'])
        self.ax = self.fig.add_subplot(111)
        self._setup_axes()
        
        self.canvas = _FigureCanvasTkAgg(self.fig, master=self.frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        self.back_line, = self.ax.plot([], [], color=CHART_COLORS['back'], 
                                        linewidth=1.5, label='BACK')
        self.lay_line, = self.ax.plot([], [], color=CHART_COLORS['lay'], 
                                       linewidth=1.5, label='LAY')
        self.ax.legend(loc='upper left', fontsize=8, facecolor=CHART_COLORS['bg'],
                       labelcolor=CHART_COLORS['text'])
    
    def _setup_axes(self):
        self.ax.set_facecolor(CHART_COLORS['bg'])
        self.ax.tick_params(colors=CHART_COLORS['text'], labelsize=8)
        self.ax.spines['top'].set_visible(False)
        self.ax.spines['right'].set_visible(False)
        self.ax.spines['bottom'].set_color(CHART_COLORS['grid'])
        self.ax.spines['left'].set_color(CHART_COLORS['grid'])
        self.ax.grid(True, alpha=0.3, color=CHART_COLORS['grid'])
        self.ax.set_ylabel('Quota', fontsize=9, color=CHART_COLORS['text'])
        self.fig.tight_layout(pad=1)
    
    def set_selection(self, selection_id: int, name: str = ""):
        self.selection_id = selection_id
        self.selection_name = name
        if HAS_MATPLOTLIB():
            self.ax.set_title(name[:30] if name else f"Sel {selection_id}", 
                              fontsize=10, color=CHART_COLORS['text'])
    
    def update_async(self):
        if self._destroyed or not self.tick_storage or not self.selection_id:
            return
        
        def fetch_data():
            try:
                ticks = self.tick_storage.get_ticks(self.selection_id, limit=120)
                if not ticks:
                    return None
                
                times = list(range(len(ticks)))
                backs = [t.back_price for t in ticks]
                lays = [t.lay_price for t in ticks]
                return times, backs, lays
            except Exception as e:
                logger.error(f"Chart data fetch error: {e}")
                return None
        
        def on_complete(data):
            if self._destroyed or data is None:
                return
            self._render_data(data)
        
        def thread_func():
            data = fetch_data()
            if self.uiq:
                self.uiq.post(lambda: on_complete(data), key=f"chart_{self.selection_id}")
            elif not self._destroyed and self.frame.winfo_exists():
                self.frame.after(0, lambda: on_complete(data))
        
        threading.Thread(target=thread_func, daemon=True).start()
    
    def _render_data(self, data: Tuple):
        if self._destroyed or not HAS_MATPLOTLIB():
            return
        
        times, backs, lays = data
        
        try:
            self.back_line.set_data(times, backs)
            self.lay_line.set_data(times, lays)
            
            if backs and lays:
                all_prices = [p for p in backs + lays if p > 0]
                if all_prices:
                    min_p = min(all_prices) * 0.98
                    max_p = max(all_prices) * 1.02
                    self.ax.set_xlim(0, len(times))
                    self.ax.set_ylim(min_p, max_p)
            
            self.canvas.draw_idle()
        except Exception as e:
            logger.error(f"Chart render error: {e}")
    
    def start_auto_update(self, interval_ms: int = 1000):
        if self._destroyed:
            return
        
        if self._update_job is not None:
            return
        
        def update_loop():
            if self._destroyed or not self.frame.winfo_exists():
                self._update_job = None
                return
            self.update_async()
            self._update_job = self.frame.after(interval_ms, update_loop)
        
        update_loop()
    
    def stop_auto_update(self):
        if self._update_job:
            try:
                self.frame.after_cancel(self._update_job)
            except:
                pass
            self._update_job = None
    
    def destroy(self):
        self._destroyed = True
        self.stop_auto_update()
        if HAS_MATPLOTLIB():
            plt.close(self.fig)
    
    def pack(self, **kwargs):
        self.frame.pack(**kwargs)
    
    def grid(self, **kwargs):
        self.frame.grid(**kwargs)


class CandlestickChart:
    """Grafico candlestick OHLC."""
    
    def __init__(self, parent: tk.Widget, width: int = 400, height: int = 200,
                 tick_storage=None, uiq=None):
        self.parent = parent
        self.tick_storage = tick_storage
        self.uiq = uiq
        self.selection_id = None
        self.selection_name = ""
        self._update_job = None
        self._destroyed = False
        self.ohlc_interval = 5
        
        if not HAS_MATPLOTLIB():
            self.frame = tk.Frame(parent, bg=CHART_COLORS['bg'])
            tk.Label(self.frame, text="Grafici non disponibili", 
                     fg='gray', bg=CHART_COLORS['bg']).pack()
            return
        
        self.frame = tk.Frame(parent, bg=CHART_COLORS['bg'])
        
        self.fig = _Figure(figsize=(width/100, height/100), dpi=100, 
                          facecolor=CHART_COLORS['bg'])
        self.ax = self.fig.add_subplot(111)
        self._setup_axes()
        
        self.canvas = _FigureCanvasTkAgg(self.fig, master=self.frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
    
    def _setup_axes(self):
        self.ax.set_facecolor(CHART_COLORS['bg'])
        self.ax.tick_params(colors=CHART_COLORS['text'], labelsize=8)
        self.ax.spines['top'].set_visible(False)
        self.ax.spines['right'].set_visible(False)
        self.ax.spines['bottom'].set_color(CHART_COLORS['grid'])
        self.ax.spines['left'].set_color(CHART_COLORS['grid'])
        self.ax.grid(True, alpha=0.3, color=CHART_COLORS['grid'])
        self.ax.set_ylabel('Quota', fontsize=9, color=CHART_COLORS['text'])
        self.fig.tight_layout(pad=1)
    
    def set_selection(self, selection_id: int, name: str = ""):
        self.selection_id = selection_id
        self.selection_name = name
        if HAS_MATPLOTLIB():
            self.ax.set_title(f"OHLC {name[:25]}" if name else f"OHLC Sel {selection_id}", 
                              fontsize=10, color=CHART_COLORS['text'])
    
    def update_async(self):
        if self._destroyed or not self.tick_storage or not self.selection_id:
            return
        
        def fetch_data():
            try:
                candles = self.tick_storage.aggregate_ohlc(self.selection_id, 
                                                           interval_sec=self.ohlc_interval)
                return candles[-60:] if len(candles) > 60 else candles
            except Exception as e:
                logger.error(f"OHLC fetch error: {e}")
                return None
        
        def on_complete(candles):
            if self._destroyed or candles is None:
                return
            self._render_candles(candles)
        
        def thread_func():
            data = fetch_data()
            if self.uiq:
                self.uiq.post(lambda: on_complete(data), key=f"ohlc_{self.selection_id}")
            elif not self._destroyed and self.frame.winfo_exists():
                self.frame.after(0, lambda: on_complete(data))
        
        threading.Thread(target=thread_func, daemon=True).start()
    
    def _render_candles(self, candles: List):
        if self._destroyed or not HAS_MATPLOTLIB() or not candles:
            return
        
        try:
            self.ax.clear()
            self._setup_axes()
            
            width = 0.6
            
            for i, c in enumerate(candles):
                color = CHART_COLORS['up'] if c.close >= c.open else CHART_COLORS['down']
                
                self.ax.plot([i, i], [c.low, c.high], color=color, linewidth=1)
                
                body_bottom = min(c.open, c.close)
                body_height = abs(c.close - c.open)
                rect = Rectangle((i - width/2, body_bottom), width, body_height,
                                   facecolor=color, edgecolor=color)
                self.ax.add_patch(rect)
            
            if candles:
                all_prices = [c.high for c in candles] + [c.low for c in candles]
                self.ax.set_xlim(-1, len(candles))
                self.ax.set_ylim(min(all_prices) * 0.98, max(all_prices) * 1.02)
            
            self.ax.set_title(f"OHLC {self.selection_name[:25]}" if self.selection_name 
                              else f"OHLC Sel {self.selection_id}", 
                              fontsize=10, color=CHART_COLORS['text'])
            
            self.canvas.draw_idle()
        except Exception as e:
            logger.error(f"Candle render error: {e}")
    
    def start_auto_update(self, interval_ms: int = 2000):
        if self._destroyed:
            return
        
        if self._update_job is not None:
            return
        
        def update_loop():
            if self._destroyed or not self.frame.winfo_exists():
                self._update_job = None
                return
            self.update_async()
            self._update_job = self.frame.after(interval_ms, update_loop)
        
        update_loop()
    
    def stop_auto_update(self):
        if self._update_job:
            try:
                self.frame.after_cancel(self._update_job)
            except:
                pass
            self._update_job = None
    
    def destroy(self):
        self._destroyed = True
        self.stop_auto_update()
        if HAS_MATPLOTLIB():
            plt.close(self.fig)
    
    def pack(self, **kwargs):
        self.frame.pack(**kwargs)
    
    def grid(self, **kwargs):
        self.frame.grid(**kwargs)


class DepthChart:
    """Grafico profondit\u00e0 liquidit\u00e0 (Depth Chart) - BACK/LAY levels."""
    
    def __init__(self, parent: tk.Widget, width: int = 300, height: int = 150,
                 uiq=None):
        self.parent = parent
        self.uiq = uiq
        self._destroyed = False
        self._update_job = None
        
        self.back_levels = []
        self.lay_levels = []
        
        if not HAS_MATPLOTLIB():
            self.frame = tk.Frame(parent, bg=CHART_COLORS['bg'])
            tk.Label(self.frame, text="Grafici non disponibili", 
                     fg='gray', bg=CHART_COLORS['bg']).pack()
            return
        
        self.frame = tk.Frame(parent, bg=CHART_COLORS['bg'])
        
        self.fig = _Figure(figsize=(width/100, height/100), dpi=100, 
                          facecolor=CHART_COLORS['bg'])
        self.ax = self.fig.add_subplot(111)
        self._setup_axes()
        
        self.canvas = _FigureCanvasTkAgg(self.fig, master=self.frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
    
    def _setup_axes(self):
        self.ax.set_facecolor(CHART_COLORS['bg'])
        self.ax.tick_params(colors=CHART_COLORS['text'], labelsize=8)
        self.ax.spines['top'].set_visible(False)
        self.ax.spines['right'].set_visible(False)
        self.ax.spines['bottom'].set_color(CHART_COLORS['grid'])
        self.ax.spines['left'].set_color(CHART_COLORS['grid'])
        self.ax.set_xlabel('Quota', fontsize=9, color=CHART_COLORS['text'])
        self.ax.set_ylabel('Liquidita EUR', fontsize=9, color=CHART_COLORS['text'])
        self.ax.set_title('Depth Chart', fontsize=10, color=CHART_COLORS['text'])
        self.fig.tight_layout(pad=1)
    
    def set_levels(self, back_levels: List[Tuple[float, float]], 
                   lay_levels: List[Tuple[float, float]]):
        self.back_levels = back_levels
        self.lay_levels = lay_levels
    
    def update_sync(self):
        if self._destroyed or not HAS_MATPLOTLIB():
            return
        
        try:
            self.ax.clear()
            self._setup_axes()
            
            if self.back_levels:
                back_prices = [l[0] for l in self.back_levels]
                back_cumsum = []
                total = 0
                for l in reversed(self.back_levels):
                    total += l[1]
                    back_cumsum.insert(0, total)
                
                self.ax.fill_between(back_prices, back_cumsum, alpha=0.4, 
                                      color=CHART_COLORS['back'], step='post', label='BACK')
                self.ax.step(back_prices, back_cumsum, where='post', 
                             color=CHART_COLORS['back'], linewidth=1.5)
            
            if self.lay_levels:
                lay_prices = [l[0] for l in self.lay_levels]
                lay_cumsum = []
                total = 0
                for l in self.lay_levels:
                    total += l[1]
                    lay_cumsum.append(total)
                
                self.ax.fill_between(lay_prices, lay_cumsum, alpha=0.4, 
                                      color=CHART_COLORS['lay'], step='pre', label='LAY')
                self.ax.step(lay_prices, lay_cumsum, where='pre', 
                             color=CHART_COLORS['lay'], linewidth=1.5)
            
            self.ax.legend(loc='upper right', fontsize=8, facecolor=CHART_COLORS['bg'],
                           labelcolor=CHART_COLORS['text'])
            
            self.canvas.draw_idle()
        except Exception as e:
            logger.error(f"Depth chart render error: {e}")
    
    def destroy(self):
        self._destroyed = True
        if HAS_MATPLOTLIB():
            plt.close(self.fig)
    
    def pack(self, **kwargs):
        self.frame.pack(**kwargs)
    
    def grid(self, **kwargs):
        self.frame.grid(**kwargs)


class ChartPanel:
    """Pannello combinato con tutti i grafici per una selezione."""
    
    def __init__(self, parent: tk.Widget, tick_storage=None, uiq=None):
        self.parent = parent
        self.tick_storage = tick_storage
        self.uiq = uiq
        self.selection_id = None
        self._destroyed = False
        
        self.frame = tk.Frame(parent, bg=CHART_COLORS['bg'])
        
        title_frame = tk.Frame(self.frame, bg=CHART_COLORS['bg'])
        title_frame.pack(fill=tk.X, pady=5)
        
        self.title_label = tk.Label(title_frame, text="Seleziona un runner", 
                                     fg=CHART_COLORS['text'], bg=CHART_COLORS['bg'],
                                     font=('Segoe UI', 11, 'bold'))
        self.title_label.pack(side=tk.LEFT, padx=10)
        
        self.close_btn = tk.Button(title_frame, text="X", command=self._on_close,
                                    bg=CHART_COLORS['bg'], fg=CHART_COLORS['text'],
                                    relief='flat', font=('Segoe UI', 10))
        self.close_btn.pack(side=tk.RIGHT, padx=10)
        
        charts_frame = tk.Frame(self.frame, bg=CHART_COLORS['bg'])
        charts_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        left_frame = tk.Frame(charts_frame, bg=CHART_COLORS['bg'])
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        right_frame = tk.Frame(charts_frame, bg=CHART_COLORS['bg'])
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(10, 0))
        
        self.line_chart = QuoteLineChart(left_frame, width=350, height=150, 
                                          tick_storage=tick_storage, uiq=uiq)
        self.line_chart.pack(fill=tk.BOTH, expand=True)
        
        self.candle_chart = CandlestickChart(right_frame, width=350, height=150,
                                              tick_storage=tick_storage, uiq=uiq)
        self.candle_chart.pack(fill=tk.BOTH, expand=True)
        
        self.depth_chart = DepthChart(left_frame, width=350, height=120, uiq=uiq)
        self.depth_chart.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        
        self._on_close_callback = None
    
    def set_selection(self, selection_id: int, name: str = ""):
        self.selection_id = selection_id
        self.title_label.configure(text=name or f"Selezione {selection_id}")
        
        self.line_chart.set_selection(selection_id, name)
        self.candle_chart.set_selection(selection_id, name)
    
    def set_depth_levels(self, back_levels: List[Tuple[float, float]], 
                          lay_levels: List[Tuple[float, float]]):
        self.depth_chart.set_levels(back_levels, lay_levels)
        self.depth_chart.update_sync()
    
    def start_updates(self):
        if self._destroyed:
            return
        self.line_chart.start_auto_update(1000)
        self.candle_chart.start_auto_update(2000)
    
    def stop_updates(self):
        self.line_chart.stop_auto_update()
        self.candle_chart.stop_auto_update()
    
    def set_close_callback(self, callback: Callable):
        self._on_close_callback = callback
    
    def _on_close(self):
        self.stop_updates()
        if self._on_close_callback:
            self._on_close_callback()
    
    def destroy(self):
        self._destroyed = True
        self.stop_updates()
        self.line_chart.destroy()
        self.candle_chart.destroy()
        self.depth_chart.destroy()
    
    def pack(self, **kwargs):
        self.frame.pack(**kwargs)
    
    def grid(self, **kwargs):
        self.frame.grid(**kwargs)
    
    def pack_forget(self):
        self.frame.pack_forget()
