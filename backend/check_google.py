
import requests
import sys

try:
    print("Checking connectivity to firestore.googleapis.com...")
    response = requests.get("https://firestore.googleapis.com", timeout=10)
    print(f"Status Code: {response.status_code}")
except Exception as e:
    print(f"Error: {e}")
