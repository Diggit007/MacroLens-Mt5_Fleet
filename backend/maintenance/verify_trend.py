import MetaTrader5 as mt5
import sqlite3
import pandas as pd
from datetime import datetime, timedelta

def verify_trend(symbol="EURUSD", event_name="CPI", days=3, invert=False):
    if not mt5.initialize():
        print(f"FAILED to initialize MT5")
        return

    # 1. Get Event Data (Actual vs Previous)
    conn = sqlite3.connect("C:/MacroLens/backend/market_data.db")
    query = """
        SELECT event_date, actual_value, previous_value, impact_level 
        FROM economic_events 
        WHERE event_name LIKE ? 
        AND actual_value IS NOT NULL 
        AND previous_value IS NOT NULL
        ORDER BY event_date ASC
    """
    df_events = pd.read_sql_query(query, conn, params=[f"%{event_name}%"])
    conn.close()

    print(f"Analying {len(df_events)} {event_name} events for {symbol} Trend (D1-D{days})...")
    
    results = []
    
    for _, row in df_events.iterrows():
        try:
            event_date = datetime.strptime(row['event_date'], "%Y-%m-%d")
            
            # 2. Determine Signal direction
            actual = row['actual_value']
            previous = row['previous_value']
            
            if invert:
                # Lower is Better (e.g. Unemployment) -> BUY
                if actual < previous:
                    direction = "BUY"
                elif actual > previous:
                    direction = "SELL"
                else:
                    continue
            else:
                # Higher is Better -> BUY
                if actual > previous:
                    direction = "BUY"
                elif actual < previous:
                    direction = "SELL"
                else:
                    continue

            # 3. Get Price Data (D1 candles starting from event date)
            # We want price change from Close of Event Day to Close of Event Day + N
            rates = mt5.copy_rates_from(symbol, mt5.TIMEFRAME_D1, event_date + timedelta(days=days+2), 10)
            
            if rates is None or len(rates) == 0:
                continue

            # Find the candle for the event date
            # Note: rates are returned in chronological order
            # We need to find the specific dates
            rates_df = pd.DataFrame(rates)
            rates_df['time'] = pd.to_datetime(rates_df['time'], unit='s')
            
            # Filter for our window
            # Start: Close of Event Day (or Day 1 Open)
            # End: Close of Event Day + Days
            
            try:
                start_price = rates_df[rates_df['time'].dt.date == event_date.date()]['close'].values[0]
                end_date = event_date.date() + timedelta(days=days)
                
                # Find closest available date for end (in case of weekend)
                end_candidates = rates_df[rates_df['time'].dt.date >= end_date]
                if end_candidates.empty:
                    continue
                end_price = end_candidates.iloc[0]['close']
                
                # Calculate PnL
                if direction == "BUY":
                    pnl = (end_price - start_price) * 10000 if "JPY" not in symbol else (end_price - start_price) * 100
                else:
                    pnl = (start_price - end_price) * 10000 if "JPY" not in symbol else (start_price - end_price) * 100
                    
                results.append({
                    "date": row['event_date'],
                    "direction": direction,
                    "actual": actual,
                    "previous": previous,
                    "pnl": pnl
                })
                
            except IndexError:
                continue

        except Exception as e:
            # print(f"Error processing {row['event_date']}: {e}")
            continue

    mt5.shutdown()
    
    if not results:
        print("No valid trades found (data gaps?).")
        return

    df_res = pd.DataFrame(results)
    print("\n--- Results ---")
    print(f"Total Trades: {len(df_res)}")
    print(f"Win Rate: {len(df_res[df_res['pnl'] > 0]) / len(df_res):.1%}")
    print(f"Total PnL: {df_res['pnl'].sum():.1f} pips")
    print(f"Avg PnL: {df_res['pnl'].mean():.1f} pips")

if __name__ == "__main__":
    # USDCAD Analysis (USD is Base)
    
    # Test 1: US CPI (Higher -> Strong USD -> USDCAD UP)
    # Standard Logic: Higher > Previous -> BUY.
    print("--- Test 1: US CPI on USDCAD (Standard Logic: Higher=Buy) ---")
    verify_trend(symbol="USDCAD", event_name="CPI", days=3, invert=False)

    # Test 2: US Unemployment (Higher -> Weak USD -> USDCAD DOWN)
    # Inverted Logic: Higher > Previous -> SELL.
    print("\n--- Test 2: US Unemployment on USDCAD (Inverted Logic: Higher=Sell) ---")
    verify_trend(symbol="USDCAD", event_name="Unemployment Rate", days=3, invert=True)
