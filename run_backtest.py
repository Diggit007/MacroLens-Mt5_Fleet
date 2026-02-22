
import asyncio
import logging
import sys
from pathlib import Path

# Setup path
sys.path.append(str(Path(__file__).parent.parent))

from services.backtest_engine import BacktestEngine

def main():
    # Setup Logging
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    logger = logging.getLogger("BacktestRunner")
    
    print("\n" + "="*50)
    print(" QUANTITATIVE EVENT TRADER - BACKTEST v1.0")
    print("="*50 + "\n")
    
    engine = BacktestEngine("C:/MacroLens/backend/market_data.db")
    
    print("Running simulation... (This may take a moment)\n")
    
    # Run at different confidence levels
    results = engine.run_backtest(min_confidence="MEDIUM")
    
    if not results:
        print("No results generated. Check database data.")
        return

    print("\n" + "="*50)
    print(" BACKTEST RESULTS")
    print("="*50)
    
    print(f"\nStats (Min Confidence: MEDIUM):")
    print(f"Total Events Scanned:   {results.total_events}")
    print(f"Trades Taken:           {results.trades_taken}")
    print(f"Fund. Accuracy:         {results.fundamental_accuracy:.1%} (Beat/Miss Pred)")
    print(f"Win Rate (PnL):         {results.win_rate:.1%}")
    print(f"Total PnL:              {results.total_pips:.1f} pips")
    print(f"Profit Factor:          {results.profit_factor:.2f}")
    print(f"Best Trade:             {results.best_trade:.1f} pips")
    print(f"Worst Trade:            {results.worst_trade:.1f} pips")
    
    print("\n\nPerformance by Confidence:")
    print("-" * 45)
    print(f"{'CONFIDENCE':<12} | {'TRADES':<8} | {'WIN RATE':<10} | {'PNL':<10}")
    print("-" * 45)
    
    for conf, data in results.by_confidence.items():
        print(f"{conf:<12} | {data['count']:<8} | {data['win_rate']:<10.1%} | {data['pips']:<10.1f}")
        
    print("-" * 45)
    
    print("\n\nEquity Curve (Last 10 points):")
    print(results.equity_curve[-10:])

if __name__ == "__main__":
    main()
