import firebase_admin
from firebase_admin import credentials, firestore
import json
from datetime import datetime

# Initialize Firebase Admin
cred = credentials.Certificate("c:/MacroLens/backend/serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# Fetch latest signal
signals_ref = db.collection('signals').order_by('timestamp', direction=firestore.Query.DESCENDING).limit(1)
docs = signals_ref.stream()

for doc in docs:
    data = doc.to_dict()
    # Convert datetime objects to string for JSON serialization
    for key, value in data.items():
        if isinstance(value, datetime):
            data[key] = value.isoformat()
            
    print(json.dumps(data, indent=4))
