import requests
import os

API_KEY = "AIzaSyABYabkK_24JgwkGgUgettJOIZm6l1u7fU"
URL = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"

try:
    print(f"Querying Google API: {URL.replace(API_KEY, 'HIDDEN')}")
    response = requests.get(URL)
    
    if response.status_code == 200:
        data = response.json()
        print("\n--- AVAILABLE MODELS ---")
        if 'models' in data:
            for m in data['models']:
                if 'generateContent' in m.get('supportedGenerationMethods', []):
                    print(f"ID: {m['name']} | Display: {m.get('displayName')} | Version: {m.get('version')}")
        else:
            print("No models found in response.")
            print(data)
    else:
        print(f"Error {response.status_code}: {response.text}")

except Exception as e:
    print(f"Script Error: {e}")
