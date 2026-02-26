
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent.parent))

from typing import List, Dict, Tuple
from itertools import combinations
from backend.services.macro_data_engine import MacroDataEngine
from backend.models.macro_health import CountryHealth

logger = logging.getLogger("MacroDivergence")

class MacroDivergence:
    """
    Analyzes policy and economic divergence between countries to identify Swing Trade opportunities.
    """
    
    def __init__(self):
        self.data_engine = MacroDataEngine()
        # Define major pairs (subset) or generate all combinations
        self.tradable_pairs = [
            "AUDNZD", "AUDUSD", "AUDJPY", "AUDCAD", "AUDCHF",
            "EURUSD", "EURGBP", "EURJPY", "EURAUD", "EURCAD",
            "GBPUSD", "GBPJPY", "GBPAUD", "GBPNZD", "GBPCAD",
            "NZDUSD", "NZDJPY", "NZDCAD",
            "USDCAD", "USDJPY", "USDCHF"
        ]

    def scan_for_divergence(self) -> List[Dict]:
        """
        Returns a sorted list of pairs with the highest fundamental divergence.
        """
        # 1. Get Health Profiles
        profiles = self.data_engine.get_all_country_health()
        results = []
        
        # 2. Iterate Tradable Pairs
        for pair in self.tradable_pairs:
            # Split pair (assuming 6 char)
            base, quote = pair[:3], pair[3:]
            
            pA = profiles.get(base)
            pB = profiles.get(quote)
            
            if not pA or not pB:
                continue
                
            # 3. Calculate Divergence Score (Composite)
            divergence = abs(pA.health_score - pB.health_score)
            
            # 4. Carry Spread (Real Rate Differential)
            # Positive = Base yields more (Real).
            carry_spread = (pA.real_rate or 0) - (pB.real_rate or 0)
            
            # 5. Momentum Divergence (Dynamic)
            # Compare growth/inflation delta
            # A positive delta means Base is ACCELERATING relative to Quote
            growth_delta = (pA.gdp_momentum or 0) - (pB.gdp_momentum or 0)
            
            # 6. COT Confirmation
            direction = "LONG" if pA.health_score > pB.health_score else "SHORT"
            
            cot_aligned = False
            strong_p = pA if direction == "LONG" else pB
            weak_p = pB if direction == "LONG" else pA
            
            # Simple check: Stronger currency has Willco > 40 (Not Bearish)
            # Weaker currency has Willco < 60 (Not Bullish)
            if (strong_p.cot_willco or 50) > 40 and (weak_p.cot_willco or 50) < 60:
                cot_aligned = True
                
            # 7. Conviction Tier
            # HIGH: Div > 2.0 + Carry + COT
            # MEDIUM: Div > 1.5 + Carry OR Momentum
            conviction = "LOW"
            if divergence > 2.0 and cot_aligned and abs(carry_spread) > 0.5:
                conviction = "HIGH"
            elif divergence > 1.5:
                # Upgrade conviction if Momentum supports the trade
                if direction == "LONG" and growth_delta > 0: conviction = "MEDIUM"
                elif direction == "SHORT" and growth_delta < 0: conviction = "MEDIUM"
                elif abs(carry_spread) > 0.5: conviction = "MEDIUM"
            
            # Create Report
            results.append({
                "symbol": pair,
                "divergence_score": round(divergence, 2),
                "recommendation": direction,
                "conviction": conviction,
                "carry_spread": round(carry_spread, 2),
                "momentum_delta": round(growth_delta, 2),
                "cot_aligned": cot_aligned,
                "base_score": pA.health_score,
                "quote_score": pB.health_score,
                "rationale": self._generate_rationale(pair, direction, pA, pB, carry_spread, cot_aligned)
            })
            
        # 8. Sort by Divergence (Descending)
        results.sort(key=lambda x: x['divergence_score'], reverse=True)
        
        # 9. Correlation Guard
        self._apply_correlation_guard(results)
        
        return results

    def _apply_correlation_guard(self, results: List[Dict]):
        """Flags if top ideas are highly correlated (e.g. All Short USD)."""
        if len(results) < 3: return
        
        top_3 = results[:3]
        directions = []
        for r in top_3:
            pair = r['symbol']
            action = r['recommendation'] # LONG or SHORT
            
            # Normalize to USD perspective
            if "USD" in pair:
                if pair.startswith("USD"):
                    # USDBase (USDCAD): LONG = Long USD
                    usd_dir = "LONG_USD" if action == "LONG" else "SHORT_USD"
                else:
                    # USDQuote (EURUSD): LONG = Short USD
                    usd_dir = "SHORT_USD" if action == "LONG" else "LONG_USD"
                directions.append(usd_dir)
        
        if len(directions) == 3 and all(d == directions[0] for d in directions):
            warning = f"⚠️ CORRELATION WARNING: Top 3 trades are all {directions[0]}. Split risk."
            for r in top_3:
                r['rationale'] += f" || {warning}"

    def _generate_rationale(self, pair, direction, A: CountryHealth, B: CountryHealth, carry: float, cot: bool) -> str:
        """
        Generates a human-readable thesis with institutional details.
        """
        strong = A if direction == "LONG" else B
        weak = B if direction == "LONG" else A
        strong_ccy = pair[:3] if direction == "LONG" else pair[3:]
        weak_ccy = pair[3:] if direction == "LONG" else pair[:3]
        
        thesis = f"{strong_ccy} ({strong.health_score}) > {weak_ccy} ({weak.health_score}). "
        
        # Fundamental Drivers
        if strong.growth_score > 6: thesis += f"{strong_ccy} Growth is robust. "
        if strong.monetary_score > 6: thesis += f"{strong_ccy} Yields attractive. "
        
        if weak.growth_score < 4: thesis += f"{weak_ccy} Growth slowing. "
        if weak.inflation_score < 4: thesis += f"{weak_ccy} Dovish/Disinflationary. "
        
        # Carry
        if abs(carry) > 0.5:
             thesis += f"Carry: {abs(carry):.1f}% benefit. "
        
        # COT
        if cot:
            thesis += f"Institutions aligned (Willco {strong.cot_willco:.0f} vs {weak.cot_willco:.0f})."
        else:
            thesis += f"COT Divergence (Inst not fully aligned)."
            
        return thesis.strip()

if __name__ == "__main__":
    scanner = MacroDivergence()
    opps = scanner.scan_for_divergence()
    
    print(f"\n{'PAIR':<10} | {'DIR':<6} | {'SCORE':<5} | {'RATIONALE'}")
    print("-" * 80)
    for op in opps[:5]: # Top 5
        print(f"{op['symbol']:<10} | {op['recommendation']:<6} | {op['divergence_score']:<5} | {op['rationale'][:50]}...")
