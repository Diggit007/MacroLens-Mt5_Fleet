import sqlite3
import pandas as pd

def inspect():
    conn = sqlite3.connect("C:/MacroLens/backend/market_data.db")
    
    # Check count
    count = pd.read_sql("SELECT COUNT(*) FROM event_reactions", conn).iloc[0,0]
    print(f"Total Rows in event_reactions: {count}")
    
    if count > 0:
        # Check sample
        df = pd.read_sql("SELECT * FROM event_reactions LIMIT 10", conn)
        print("\n--- Sample Data ---")
        print(df[['event_name', 'event_date', 'h1_change_pips']].to_string())
        
        # Check if ALL are zero
        zero_count = pd.read_sql("SELECT COUNT(*) FROM event_reactions WHERE h1_change_pips = 0.0", conn).iloc[0,0]
        non_zero_count = count - zero_count
        print(f"\nRows with 0.0 pips: {zero_count} ({(zero_count/count)*100:.1f}%)")
        print(f"Rows with valid pips: {non_zero_count}")
        
        if non_zero_count > 0:
            df_valid = pd.read_sql("SELECT * FROM event_reactions WHERE h1_change_pips != 0.0 LIMIT 5", conn)
            print("\n--- Valid Data Sample ---")
            print(df_valid[['event_name', 'event_date', 'h1_change_pips']].to_string())

    conn.close()

if __name__ == "__main__":
    inspect()
