import sys
import os
from pathlib import Path
from tabulate import tabulate

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from backend.services.macro_divergence import MacroDivergence

def run_dashboard():
    print("\n" + "="*80)
    print(" 🌎 GLOBAL MACRO LEAGUE TABLE (INSTITUTIONAL VIEW) 🌎")
    print("="*80 + "\n")
    
    scanner = MacroDivergence()
    profiles = scanner.data_engine.get_all_country_health()
    
    # 1. Prepare League Table Data
    table_data = []
    
    # Sort profiles by Health Score
    sorted_profiles = sorted(profiles.values(), key=lambda x: x.health_score, reverse=True)
    
    for rank, p in enumerate(sorted_profiles, 1):
        # Format Trend arrows
        gdp_trend = "↗️" if (p.gdp_momentum or 0) > 0.1 else "↘️" if (p.gdp_momentum or 0) < -0.1 else "➡️"
        cpi_trend = "↗️" if (p.cpi_momentum or 0) > 0.1 else "↘️" if (p.cpi_momentum or 0) < -0.1 else "➡️"
        
        # Format Bias color/icon
        bias = p.policy_bias or "NEUTRAL"
        if bias == "HAWKISH": bias = "🦅 HAWK"
        elif bias == "DOVISH": bias = "🕊️ DOVE"
        
        # Format PMI
        pmi_m = f"{p.pmi_manufacturing:.1f}" if p.pmi_manufacturing else "?"
        pmi_s = f"{p.pmi_services:.1f}" if p.pmi_services else "?"
        pmi_display = f"{pmi_m}/{pmi_s}"
        
        # Color PMI (Red if < 50)
        # Note: Tabulate doesn't support color codes easily without ansi, but let's assume raw text
        
        # Format CPI (Headline / Core)
        cpi = f"{p.inflation_rate:.1f}%" if p.inflation_rate is not None else "?"
        core = f"{p.core_inflation:.1f}%" if p.core_inflation is not None else "?"
        cpi_display = f"{cpi} / {core}"
        
        row = [
            rank,
            p.currency,
            f"{p.health_score:.2f}",
            pmi_display,
            cpi_display,
            f"{p.real_rate:.2f}%" if p.real_rate is not None else "N/A",
            bias,
            f"{gdp_trend} {p.gdp_momentum or 0.0:+.2f}",
            f"{p.govt_debt_gdp:.0f}%" if p.govt_debt_gdp else "?"
        ]
        table_data.append(row)
        
    print(tabulate(table_data, headers=["Rank", "Ccy", "Score", "PMI(M/S)", "CPI(H/C)", "Real Rate", "Bias", "Growth", "Debt"], tablefmt="simple_grid"))
    
    print("\n" + "="*80)
    print(" 🚀 TOP INSTITUTIONAL TRADE SIGNALS 🚀")
    print("="*80 + "\n")
    
    # 2. Get Top Signals
    signals = scanner.scan_for_divergence()
    
    top_signals = signals[:3]
    
    for i, s in enumerate(top_signals, 1):
        direction = "🟢 LONG" if s['recommendation'] == "LONG" else "🔴 SHORT"
        print(f"#{i} {s['symbol']}  {direction}")
        print(f"   Score Divergence: {s['divergence_score']}")
        print(f"   Conviction:       {s.get('conviction', 'LOW')}")
        print(f"   Carry Spread:     {s.get('carry_spread', 0)}%")
        print(f"   Rationale:        {s['rationale']}")
        print("-" * 80)

if __name__ == "__main__":
    run_dashboard()
