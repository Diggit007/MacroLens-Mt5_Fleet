
import sys
import os
from pathlib import Path

# Add backend directory to sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BACKEND_DIR))

from backend.firebase_setup import initialize_firebase

# Force native DNS resolver to avoid gRPC hangs
os.environ["GRPC_DNS_RESOLVER"] = "native"

def debug_users():
    try:
        db = initialize_firebase()
        print("Firestore initialized.")
        
        users_ref = db.collection("users")
        docs = list(users_ref.stream())
        
        print(f"Found {len(docs)} users in Firestore.")
        
        for doc in docs[:5]:
            data = doc.to_dict()
            print(f"- User: {doc.id}, Email: {data.get('email')}, Role: {data.get('role')}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    debug_users()
