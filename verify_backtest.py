
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from backend.services.backtest_engine import BacktestEngine

def main():
    print("Starting verification backtest...")
    engine = BacktestEngine("C:/MacroLens/backend/market_data.db")
    results = engine.run_backtest(min_confidence="MEDIUM")
    
    if not results:
        print("No results returned.")
        return

    print("\n--- RESULTS ---")
    print(f"Trades Taken: {results.trades_taken}")
    print(f"Win Rate: {results.win_rate:.2%}")
    print(f"Total PnL: {results.total_pips:.2f}")
    print(f"Profit Factor: {results.profit_factor:.2f}")
    print(f"Fundamental Accuracy: {results.fundamental_accuracy:.2%}")
    print("Done.")

if __name__ == "__main__":
    main()
