import firebase_admin
from firebase_admin import credentials, firestore

# Initialize (if not already)
try:
    cred = credentials.Certificate("backend/serviceAccountKey.json")
    firebase_admin.initialize_app(cred)
except:
    pass

db = firestore.client()

USER_ID = "0AoEssZoiOVVFZBLmKFXGE5Z1M82"

print(f"--- Checking User: {USER_ID} ---")
doc = db.collection('users').document(USER_ID).get()

if not doc.exists:
    print("❌ User Document NOT FOUND.")
else:
    data = doc.to_dict()
    print("✅ User Found.")
    print(f"Role: {data.get('role')}")
    print(f"Active Account ID: {data.get('activeAccountId')}")
    print(f"MetaApi Account ID: {data.get('metaapiAccountId')}")
    print(f"Generic Account ID: {data.get('accountId')}")

    # Check for linked MT5 accounts
    print("\n--- Checking Linked Accounts ---")
    accounts = db.collection('mt5_accounts').where('userId', '==', USER_ID).get()
    if not accounts:
        print("❌ No accounts found in 'mt5_accounts' collection for this user.")
    else:
        for acc in accounts:
            print(f"📄 Account Doc: {acc.id} => {acc.to_dict()}")
