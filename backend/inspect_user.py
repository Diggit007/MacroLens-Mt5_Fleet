
import firebase_admin
from firebase_admin import credentials, firestore
import json
from datetime import datetime

# Initialize Firebase Admin
if not firebase_admin._apps:
    cred = credentials.Certificate("c:/MacroLens/backend/serviceAccountKey.json")
    firebase_admin.initialize_app(cred)

db = firestore.client()

user_id = "wj7Fa28HxZhwoYAuxTLDzoNyg7y2"

def serialize_doc(doc):
    d = doc.to_dict()
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d

try:
    doc_ref = db.collection('users').document(user_id)
    doc = doc_ref.get()
    
    if doc.exists:
        print(json.dumps(serialize_doc(doc), indent=2))
        
        # Check for subcollections (like referrals)
        cols = doc.reference.collections()
        for col in cols:
            print(f"Subcollection: {col.id}")
            docs = col.stream()
            for d in docs:
                print(f"  Doc ID: {d.id} => {d.to_dict()}")
                
    else:
        print(f"User document {user_id} does not exist.")
        
    # Also check Auth user status if possible (requires firebase-admin auth)
    from firebase_admin import auth
    try:
        user = auth.get_user(user_id)
        print("\nAuth User Record:")
        print(f"UID: {user.uid}")
        print(f"Email: {user.email}")
        print(f"Disabled: {user.disabled}")
        print(f"Email Verified: {user.email_verified}")
        print(f"Tokens Valid After: {user.tokens_valid_after_timestamp}")
    except Exception as e:
        print(f"Error getting auth user: {e}")

except Exception as e:
    print(f"An error occurred: {e}")
