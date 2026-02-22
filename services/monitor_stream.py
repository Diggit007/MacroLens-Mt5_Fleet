import time
import requests
import sys

def monitor_stream():
    url = "http://localhost:8000/api/agent/stream"
    print(f"Polling {url}...")
    
    seen_logs = set()
    
    while True:
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                logs = data.get("logs", [])
                
                for log in logs:
                    # Create a unique signature
                    sig = f"{log.get('timestamp')}-{log.get('message')}"
                    
                    if sig not in seen_logs:
                        seen_logs.add(sig)
                        timestamp = log.get('timestamp', '').split('T')[1].split('.')[0]
                        agent = log.get('agent', 'SYSTEM').upper()
                        msg = log.get('message', '')
                        print(f"[{timestamp}] [{agent}] {msg}")

    # --- SIMULATION INJECTION FOR TESTING ---
    # In a real scenario, this script just reads. 
    # But to test the UI, we can push a log to the backend if we had an endpoint.
    # Since we can't easily push from here without an endpoint, 
    # we rely on the heartbeat or manual interaction.
    # ----------------------------------------
            
        except Exception as e:
            print(f"Error: {e}")
        
        time.sleep(2)

if __name__ == "__main__":
    monitor_stream()
