
import requests
import json

try:
    # Try localhost:8001 (temporary test server)
    url = "http://127.0.0.1:8001/api/usd_index/latest"
    print(f"Testing URL: {url}")
    res = requests.get(url, timeout=5)
    print(f"Status Code: {res.status_code}")
    if res.status_code == 200:
        print("Response:")
        print(json.dumps(res.json(), indent=2))
    else:
        print(f"Error: {res.text}")
except Exception as e:
    print(f"Failed to connect: {e}")
