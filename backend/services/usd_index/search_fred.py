
import os
from fredapi import Fred
from dotenv import load_dotenv
from pathlib import Path

# Load API Key
try:
    env_path = Path("c:/MacroLens/backend/.env")
    load_dotenv(env_path)
except:
    pass

key = os.environ.get("FRED_API_KEY")
fred = Fred(api_key=key)

terms = [
    "Net Non-Commercial", 
    "CFTC Net",
    "Commitment of Traders",
    "Managed Money Net"
]

print("Searching FRED...")
for t in terms:
    try:
        print(f"--- Search: {t} ---")
        res = fred.search(t, limit=5)
        if res is None:
            print("No results found (None returned).")
            continue
            
        if not res.empty:
            print(res[['id', 'title', 'frequency_short']])
        else:
            print("No results found (Empty DataFrame).")
    except Exception as e:
        print(f"Error: {e}")
