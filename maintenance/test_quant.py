import asyncio
import os
import sys

# Ensure backend directory is in path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import aiosqlite
from workers.analysis import MacroLensAgentV2

# Fake Event Data to Test Quant Logic directly
# Mocks an upcoming NFP-like event that has history in your DB
TEST_EVENT = {
    "event_name": "Nonfarm Payrolls",  # Must match a name in your DB history
    "event_time": "13:30",
    "impact_level": "High",
    "previous_value": "150", 
    "forecast_value": "170",
    "actual_value": "200" # Let's simulate a Beat
}

async def test_quant_analysis():
    print("--- Testing MacroLens Quant Analysis Module ---")
    
    agent = MacroLensAgentV2() # Initialize
    
    # 1. Test Math Logic directly
    print("\n[Step 1] Testing Math Logic on Mock Event...")
    # We need to manually inject history stat for the test to work fully for Z-Score
    # But let's let the DB query find the real history first? 
    # The _analyze_event_math method expects the event dict to ALREADY have 'hist_std_dev'
    # which is populated by get_upcoming_events.
    
    # So let's run get_upcoming_events but mock the time/query?
    # Easier: Manually query DB for history stats first, then feed to math.
    
    db_path = os.getenv("DB_PATH", "market_data.db")
    async with aiosqlite.connect(db_path) as db:
        print(f"Connected to {db_path}")
        
        # Fetch history for "Nonfarm Payrolls" or "CPI" or whatever exists in your data
        cursor = await db.execute("SELECT DISTINCT event_name FROM economic_events WHERE currency='USD' LIMIT 20")
        names = await cursor.fetchall()
        print(f"Scanning {len(names)} Events for valid history...")
        
        # Helper to find a good event
        target_event = None
        std_dev = None
        streak = 0
        
        for name_tuple in names:
            e_name = name_tuple[0]
            # Check if this one has enough history
            h_query = """
                SELECT actual_value, forecast_value 
                FROM economic_events 
                WHERE event_name = ? 
                AND actual_value IS NOT NULL 
                AND forecast_value IS NOT NULL
                ORDER BY event_date DESC 
                LIMIT 30
            """
            h_cursor = await db.execute(h_query, (e_name,))
            h_rows = await h_cursor.fetchall()
            
            if len(h_rows) >= 4:
                target_event = e_name
                diffs = [(float(r[0]) - float(r[1])) for r in h_rows]
                if diffs:
                    import numpy as np
                    std_dev = float(np.std(diffs))
                    # Copy-paste streak logic
                    first_sign = 1 if diffs[0] > 0 else -1 if diffs[0] < 0 else 0
                    if first_sign != 0:
                        for d in diffs:
                            s = 1 if d > 0 else -1 if d < 0 else 0
                            if s == first_sign: streak += s
                            else: break
                break

        if not target_event:
            print("No event with sufficient history found in the first 5 events (try increasing LIMIT in script).")
            return

        print(f"\nTargeting Real Event: {target_event}")
        
        print(f"   > Calculated Hist StdDev (Sigma): {std_dev}")
        print(f"   > Calculated Streak: {streak}")
        
        # Construct Event Object
        # We simulate a "Live" event using the stats we just found
        mock_ev = {
            "event_name": target_event,
            "event_time": "14:00",
            "impact_level": "High",
            "previous_value": "2.0", # Mock
            "forecast_value": "2.2", # Mock
            "actual_value": "2.8",   # Mock explicit beat to trigger Z-Score
            "hist_std_dev": std_dev,
            "streak": streak
        }
        
        # Run Analysis
        result = agent._analyze_event_math(mock_ev)
        
        print("\n[Step 2] QUANT LAB OUTPUT:")
        print("---------------------------------------------------")
        print(f"Momentum:     {result.get('momentum')}")
        print(f"Surprise:     {result.get('surprise_pct_str')}")
        print(f"Z-Score:      {result.get('z_score_str')}  <-- KEY METRIC")
        print(f"Streak:       {result.get('streak_str')}")
        print(f"Reliability:  {result.get('reliability_str')}")
        print(f"Signal:       {result.get('bias')} (Score: {result.get('qt_score')})")
        print("---------------------------------------------------")

if __name__ == "__main__":
    try:
        if os.name == 'nt':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(test_quant_analysis())
    except Exception as e:
        print(f"Error: {e}")
