
import requests
import concurrent.futures
import time

URL = "http://localhost:8000/api/analysis/new"
HEADERS = {"Authorization": "Bearer mock_test_token"}
PAYLOAD = {"symbol": "BTC/USDT", "timeframe": "H1", "risk_profile": "aggressive"}

def send_request(i):
    try:
        start = time.time()
        # Add a tiny delay to ensure they are processed slightly sequentially by the limiter
        time.sleep(i * 0.1) 
        print(f"Req {i} sending...")
        resp = requests.post(URL, json=PAYLOAD, headers=HEADERS, timeout=10)
        end = time.time()
        print(f"Req {i}: Status {resp.status_code} (Took {end-start:.2f}s)")
        return resp.status_code
    except Exception as e:
        print(f"Req {i}: Error {e}")
        return 500

def test_rate_limit():
    print("--- Testing Rate Limit (Max 2/min) ---")
    # Send 5 requests. 
    # Expect: 200, 200, 429, 429, 429
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(send_request, range(5)))
    
    success_count = results.count(200)
    blocked_count = results.count(429)
    
    print("\nResults:")
    print(f"Success (200): {success_count}")
    print(f"Blocked (429): {blocked_count}")
    
    if success_count <= 2 and blocked_count >= 3:
        print("[PASS] Rate Limiter is working.")
    else:
        print("[FAIL] Rate Limiter check failed.")

if __name__ == "__main__":
    # Wait for server to be fully up
    time.sleep(5)
    test_rate_limit()
