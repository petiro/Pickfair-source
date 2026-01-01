"""
AIPatternEngine - Analisi Weight of Money per auto-entry BACK/LAY

Decide automaticamente BACK o LAY per ogni runner basandosi
sul Weight of Money (rapporto liquidità BACK vs LAY).
"""

from typing import Dict, List
import logging

logger = logging.getLogger(__name__)


class AIPatternEngine:
    """
    Decide BACK o LAY per runner basato su Weight of Money (WoM).
    
    WoM = back_volume / (back_volume + lay_volume)
    - WoM > 0.55: forte pressione BACK → BACK
    - WoM < 0.45: forte pressione LAY → LAY
    - Altrimenti: neutro → default BACK
    
    Forza sempre almeno 1 BACK + 1 LAY se possibile (mixed dutching).
    """
    
    def __init__(self, wom_back_threshold: float = 0.55, 
                 wom_lay_threshold: float = 0.45):
        """
        Args:
            wom_back_threshold: Soglia WoM sopra cui → BACK
            wom_lay_threshold: Soglia WoM sotto cui → LAY
        """
        self.wom_back_threshold = wom_back_threshold
        self.wom_lay_threshold = wom_lay_threshold
    
    def calculate_wom(self, selection: Dict) -> float:
        """
        Calcola Weight of Money per una selezione.
        
        Args:
            selection: Dict con back_ladder e lay_ladder
            
        Returns:
            WoM tra 0 e 1 (0.5 = neutro)
        """
        back_ladder = selection.get("back_ladder", [])
        lay_ladder = selection.get("lay_ladder", [])
        
        back_vol = sum(p.get("size", 0) for p in back_ladder)
        lay_vol = sum(p.get("size", 0) for p in lay_ladder)
        
        total = back_vol + lay_vol
        if total == 0:
            return 0.5  # Neutro se nessuna liquidità
        
        return back_vol / total
    
    def decide(self, selections: List[Dict]) -> Dict[int, str]:
        """
        Decide BACK o LAY per ogni runner.
        
        Args:
            selections: Lista selezioni con:
                - selectionId: int
                - back_ladder: List[{price, size}]
                - lay_ladder: List[{price, size}]
                
        Returns:
            Dict {selectionId: 'BACK' o 'LAY'}
        """
        decisions = {}
        wom_values = {}
        
        for sel in selections:
            selection_id = sel.get("selectionId")
            if not selection_id:
                continue
            
            wom = self.calculate_wom(sel)
            wom_values[selection_id] = wom
            
            # Logica WoM
            if wom > self.wom_back_threshold:
                decisions[selection_id] = "BACK"
            elif wom < self.wom_lay_threshold:
                decisions[selection_id] = "LAY"
            else:
                decisions[selection_id] = "BACK"  # Default neutro
        
        # Forza almeno un BACK e un LAY se possibile (mixed dutching requirement)
        sides = set(decisions.values())
        if len(sides) == 1 and len(selections) > 1:
            # Trova la selezione con WoM più lontano dalla media
            avg_wom = sum(wom_values.values()) / len(wom_values) if wom_values else 0.5
            
            # Scegli il runner da invertire (quello più lontano dalla media)
            best_candidate = None
            best_distance = -1  # Usa -1 per trovare sempre un candidato
            
            for sel_id, wom in wom_values.items():
                distance = abs(wom - avg_wom)
                if distance > best_distance:
                    best_distance = distance
                    best_candidate = sel_id
            
            # Se tutti hanno stessa distanza (es. tutti 0.5), prendi il primo
            if best_candidate is None and wom_values:
                best_candidate = list(wom_values.keys())[0]
            
            if best_candidate:
                current = decisions[best_candidate]
                decisions[best_candidate] = "LAY" if current == "BACK" else "BACK"
                logger.info(f"[AI] Forzato {best_candidate} a {decisions[best_candidate]} per mixed")
        
        logger.info(f"[AI] Decisions: {decisions}, WoM: {wom_values}")
        return decisions
    
    def get_wom_analysis(self, selections: List[Dict]) -> List[Dict]:
        """
        Ritorna analisi WoM dettagliata per UI.
        
        Returns:
            Lista di dict con selectionId, wom, suggested_side, confidence
        """
        analysis = []
        
        for sel in selections:
            selection_id = sel.get("selectionId")
            if not selection_id:
                continue
            
            wom = self.calculate_wom(sel)
            
            # Confidence basata su distanza da 0.5
            confidence = abs(wom - 0.5) * 2  # 0-1
            
            if wom > self.wom_back_threshold:
                side = "BACK"
            elif wom < self.wom_lay_threshold:
                side = "LAY"
            else:
                side = "NEUTRAL"
            
            analysis.append({
                "selectionId": selection_id,
                "runnerName": sel.get("runnerName", ""),
                "wom": round(wom, 3),
                "suggested_side": side,
                "confidence": round(confidence, 2)
            })
        
        return analysis
