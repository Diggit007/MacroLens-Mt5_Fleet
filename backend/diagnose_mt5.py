import MetaTrader5 as mt5
from datetime import datetime, timedelta
import pandas as pd

def diagnose():
    if not mt5.initialize():
        print(f"FAILED to initialize MT5: {mt5.last_error()}")
        return

    print(f"Connected to: {mt5.terminal_info().name}")
    print(f"Account: {mt5.account_info().login} ({mt5.account_info().server})")

    # 1. Check Symbols
    print("\n--- Symbol Check ---")
    symbols = mt5.symbols_get()
    print(f"Total Symbols Found: {len(symbols)}")
    
    eurusd_variants = [s.name for s in symbols if "EURUSD" in s.name]
    print(f"EURUSD Variants found: {eurusd_variants}")
    
    if not eurusd_variants:
        print("CRITICAL: No EURUSD symbol found!")
        return
        
    symbol = eurusd_variants[0]
    print(f"Using symbol: {symbol} for testing")

    # 2. Check Recent Data (Yesterday)
    print("\n--- Recent Data Test (Yesterday) ---")
    yesterday = datetime.now() - timedelta(days=1)
    rates = mt5.copy_rates_from(symbol, mt5.TIMEFRAME_M1, yesterday, 10)
    
    if rates is None or len(rates) == 0:
        print("FAILED to get recent data.")
    else:
        print(f"SUCCESS: Retrieved {len(rates)} candles.")
        print(f"Sample: {rates[0]}")

    # 3. Check Historical Data (1 Year Ago)
    print("\n--- Historical Data Test (Jan 2025) ---")
    old_date = datetime(2025, 1, 15)
    rates_old = mt5.copy_rates_from(symbol, mt5.TIMEFRAME_M1, old_date, 10)
    
    if rates_old is None or len(rates_old) == 0:
        print("FAILED to get historical data (Jan 2025).")
        print("Reason: Data likely not downloaded to terminal.")
    else:
        print(f"SUCCESS: Retrieved {len(rates_old)} candles.")
        print(f"Sample: {rates_old[0]}")

    mt5.shutdown()

if __name__ == "__main__":
    diagnose()
