import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from backend.services.macro_divergence import MacroDivergence

OUTPUT_FILE = "C:/MacroLens/backend/test_output.txt"

def run():
    lines = []
    def p(text=""):
        lines.append(text)
    
    scanner = MacroDivergence()
    profiles = scanner.data_engine.get_all_country_health()
    
    p("=" * 90)
    p(" SECTION 1: INDIVIDUAL CURRENCY HEALTH PROFILES")
    p("=" * 90)
    
    sorted_profiles = sorted(profiles.values(), key=lambda x: x.health_score, reverse=True)
    
    for rank, pr in enumerate(sorted_profiles, 1):
        bias = pr.policy_bias or "NEUTRAL"
        p(f"\n{'-' * 90}")
        p(f"  #{rank} {pr.currency}  |  COMPOSITE SCORE: {pr.health_score:.2f} / 10  |  Bias: {bias}")
        p(f"{'-' * 90}")
        p(f"  RAW DATA:")
        p(f"    CPI (Inflation):    {pr.inflation_rate or 'N/A'}%")
        p(f"    GDP Growth:         {pr.gdp_growth or 'N/A'}%")
        p(f"    Unemployment:       {pr.unemployment_rate or 'N/A'}%")
        p(f"    Interest Rate:      {pr.interest_rate or 'N/A'}%")
        p(f"    Real Rate:          {pr.real_rate or 'N/A'}%")
        p(f"    Trade Balance:      {pr.trade_balance or 'N/A'}")
        p()
        p(f"  MOMENTUM (3-Month Slope):")
        gdp_m = pr.gdp_momentum
        cpi_m = pr.cpi_momentum
        unemp_m = pr.unemployment_momentum
        gdp_label = '(Accelerating)' if gdp_m and gdp_m > 0 else '(Decelerating)' if gdp_m and gdp_m < 0 else ''
        cpi_label = '(Inflation Rising)' if cpi_m and cpi_m > 0 else '(Disinflation)' if cpi_m and cpi_m < 0 else ''
        unemp_label = '(Labor Cooling)' if unemp_m and unemp_m > 0 else '(Labor Tightening)' if unemp_m and unemp_m < 0 else ''
        p(f"    GDP Momentum:       {f'{gdp_m:+.2f}' if gdp_m is not None else 'N/A'}  {gdp_label}")
        p(f"    CPI Momentum:       {f'{cpi_m:+.2f}' if cpi_m is not None else 'N/A'}  {cpi_label}")
        p(f"    Unemployment Mom:   {f'{unemp_m:+.2f}' if unemp_m is not None else 'N/A'}  {unemp_label}")
        p()
        p(f"  FACTOR SUB-SCORES (each 0-10):")
        p(f"    Growth:     {pr.growth_score:.1f}  (Weight: 25%)")
        p(f"    Inflation:  {pr.inflation_score:.1f}  (Weight: 20%)")
        p(f"    Monetary:   {pr.monetary_score:.1f}  (Weight: 25%)")
        p(f"    Real Rate:  {pr.real_rate_score:.1f}  (Weight: 15%)")
        p(f"    COT:        {pr.cot_score:.1f}  (Weight: 15%)")
        p()
        p(f"  COT POSITIONING:")
        p(f"    Willco Index:       {pr.cot_willco or 'N/A'}")
        p(f"    COT Bias:           {pr.cot_bias or 'N/A'}")
        p(f"    Smart Money Net:    {pr.cot_smart_net or 'N/A'}")

    p("\n\n" + "=" * 90)
    p(" SECTION 2: DIVERGENCE MATRIX (ALL 21 PAIRS)")
    p("=" * 90)
    
    results = scanner.scan_for_divergence()
    
    for i, r in enumerate(results, 1):
        conv = r.get('conviction', 'LOW')
        p(f"\n{'-' * 90}")
        p(f"  #{i} {r['symbol']}  {r['recommendation']}  |  Divergence: {r['divergence_score']}  |  Conviction: {conv}")
        p(f"{'-' * 90}")
        p(f"  Base Score:       {r['base_score']}")
        p(f"  Quote Score:      {r['quote_score']}")
        p(f"  Carry Spread:     {r.get('carry_spread', 0)}%")
        p(f"  Momentum Delta:   {r.get('momentum_delta', 0)}")
        p(f"  COT Aligned:      {r.get('cot_aligned', False)}")
        p(f"  Corr. Warning:    {r.get('correlation_warning', 'None')}")
        p(f"  RATIONALE:        {r['rationale']}")

    if results:
        top = results[0]
        base_ccy = top['symbol'][:3]
        quote_ccy = top['symbol'][3:]
        pA = profiles.get(base_ccy)
        pB = profiles.get(quote_ccy)
        
        p("\n\n" + "=" * 90)
        p(f" SECTION 3: DEEP DIVE -- {top['symbol']}")
        p("=" * 90)
        
        p(f"\n  {'FACTOR':<25} | {base_ccy:>10} | {quote_ccy:>10} | {'EDGE':>10}")
        p(f"  {'-' * 60}")
        
        if pA and pB:
            factors = [
                ("Health Score",     pA.health_score,     pB.health_score),
                ("Growth Score",     pA.growth_score,     pB.growth_score),
                ("Inflation Score",  pA.inflation_score,  pB.inflation_score),
                ("Monetary Score",   pA.monetary_score,   pB.monetary_score),
                ("Real Rate Score",  pA.real_rate_score,  pB.real_rate_score),
                ("COT Score",        pA.cot_score,        pB.cot_score),
                ("Real Rate",        pA.real_rate or 0,   pB.real_rate or 0),
                ("GDP Momentum",     pA.gdp_momentum or 0, pB.gdp_momentum or 0),
                ("CPI Momentum",     pA.cpi_momentum or 0, pB.cpi_momentum or 0),
                ("PMI (Mfg)",        pA.pmi_manufacturing or 0, pB.pmi_manufacturing or 0),
                ("Core CPI",         pA.core_inflation or 0,    pB.core_inflation or 0),
                ("Debt/GDP",         pA.govt_debt_gdp or 0,     pB.govt_debt_gdp or 0),
            ]
            
            for name, a_val, b_val in factors:
                edge = a_val - b_val
                winner = base_ccy if edge > 0 else quote_ccy if edge < 0 else "EVEN"
                p(f"  {name:<25} | {a_val:>10.2f} | {b_val:>10.2f} | {winner:>10}")
        
        p(f"\n  VERDICT: {top['recommendation']} {top['symbol']}")
        p(f"  CONVICTION: {top.get('conviction', 'LOW')}")
        p(f"  CARRY: {top.get('carry_spread', 0)}% (Real Rate Differential)")
        p(f"  THESIS: {top['rationale']}")
    
    p("\n" + "=" * 90)
    p(" END OF REPORT")
    p("=" * 90)

    # Write to file
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
    
    print(f"Full report written to: {OUTPUT_FILE}")

if __name__ == "__main__":
    run()
