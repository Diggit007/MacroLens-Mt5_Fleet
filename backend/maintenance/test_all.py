import requests
import time
import json

BASE_URL = "http://127.0.0.1:8000/api"
USER_ID = "test_user"
HEADERS = {"Authorization": "Bearer mock_test_token"}

def print_result(name, success, data=None):
    symbol = "[PASS]" if success else "[FAIL]"
    print(f"{symbol} {name}")
    if data:
        print(json.dumps(data, indent=2))
    print("-" * 40)

def test_login():
    try:
        res = requests.post(f"{BASE_URL}/auth/login", json={"user_id": USER_ID}, headers=HEADERS)
        if res.status_code == 200:
            print_result("Login", True, res.json())
            return True
        else:
            print_result("Login", False, res.text)
            return False
    except Exception as e:
        print_result("Login", False, str(e))
        return False

def test_market_data_ccxt():
    # Test Crypto (CCXT)
    try:
        res = requests.get(f"{BASE_URL}/market/data?symbol=BTC/USDT&timeframe=H1&count=5&rsi=14", headers=HEADERS)
        if res.status_code == 200:
            data = res.json()
            # Check for generic list or dict depending on implementation
            # Current implementation returns {"symbol":..., "candles": [], "indicators": ...}
            if "candles" in data and len(data["candles"]) > 0:
                print_result("Market Data (Crypto/CCXT)", True, {"candles": len(data["candles"]), "indicators": data.get("indicators")})
            else:
                print_result("Market Data (Crypto/CCXT)", False, data)
        else:
            print_result("Market Data (Crypto/CCXT)", False, res.text)
    except Exception as e:
        print_result("Market Data (Crypto/CCXT)", False, str(e))

def test_market_data_mt5():
    # Test Forex (MT5) - Attempting EURUSD
    print("Testing MT5 Data... (May timeout if EA not running)")
    try:
        res = requests.get(f"{BASE_URL}/market/data?symbol=EURUSD&timeframe=H1&count=5", headers=HEADERS)
        if res.status_code == 200:
            data = res.json()
            if "candles" in data and len(data["candles"]) > 0:
                print_result("Market Data (Forex/MT5)", True, {"candles": len(data["candles"])})
            else:
                 # It might return error if EA not connected
                print_result("Market Data (Forex/MT5)", False, data)
        else:
            print_result("Market Data (Forex/MT5)", False, res.text)
    except Exception as e:
        print_result("Market Data (Forex/MT5)", False, str(e))

def test_analysis():
    print("Testing AI Analysis...")
    try:
        # Note: Analysis triggers LLM, might be slow or fail if no API Key
        # But we want to see if it reaches the agent.
        res = requests.post(f"{BASE_URL}/analysis/new", json={"user_id": USER_ID, "symbol": "BTC/USDT", "mode": "scalping"}, headers=HEADERS)
        if res.status_code == 200:
            print_result("Analysis Agent", True, res.json())
        else:
            print_result("Analysis Agent", False, res.text)
    except Exception as e:
        print_result("Analysis Agent", False, str(e))

def test_trade_status():
    try:
        res = requests.get(f"{BASE_URL}/trade/status?user_id={USER_ID}", headers=HEADERS)
        if res.status_code == 200:
             print_result("Trade Status", True, res.json())
        else:
             print_result("Trade Status", False, res.text)
    except Exception as e:
         print_result("Trade Status", False, str(e))

def test_trade_history():
    print(f"Testing Trade History ({BASE_URL})...")
    try:
        # Request history for 'today'
        res = requests.get(f"{BASE_URL}/trade/history", params={"user_id": USER_ID, "period": "today"}, headers=HEADERS)
        if res.status_code == 200:
            print_result("Trade History (Today)", True, res.json())
        else:
            print_result("Trade History (Today)", False, res.text)
    except Exception as e:
        print_result("Trade History", False, str(e))
    print("-" * 40)

if __name__ == "__main__":
    if test_login():
        test_market_data_ccxt()
        test_market_data_mt5() # Might fail if no MT5
        test_trade_status()
        test_trade_history()
        test_analysis() # Skipping to save tokens/time unless requested
