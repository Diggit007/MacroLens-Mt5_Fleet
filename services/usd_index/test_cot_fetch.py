
import os
from fredapi import Fred
from dotenv import load_dotenv
from pathlib import Path

# Load API Key
try:
    load_dotenv("c:/MacroLens/backend/.env")
except: pass

key = os.environ.get("FRED_API_KEY")
fred = Fred(api_key=key)

# Common COT IDs
# CFTC/098662/F_L_ALL : Non Commercial Net for USD Index?
# WDT/WDT_USD_WOM : COT USD Net?
# Check known IDs
ids = [
    "WDT/WDT_USD_WOM", # Sometimes works
    "CFTC_098662_FO_L_ALL", # USD Index Futures only (Legacy)
    "CFTC_098662_F_L_ALL",
    "FUT_DIS_NON_COM_NET_POS_USD"
]

print("Testing direct fetch...")
for i in ids:
    try:
        print(f"Fetching {i}...")
        s = fred.get_series(i, limit=5)
        print(f"Success! {i}")
        print(s.tail())
    except Exception as e:
        print(f"Failed {i}: {e}")
