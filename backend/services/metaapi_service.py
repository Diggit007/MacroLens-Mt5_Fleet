import asyncio
import os
import logging
import httpx
from datetime import datetime
from backend.firebase_setup import initialize_firebase
from backend.core.cache import cache

logger = logging.getLogger("FleetServiceClient")
db = initialize_firebase()

FLEET_MANAGER_URL = os.getenv("FLEET_MANAGER_URL", "http://127.0.0.1:8000")
client = httpx.AsyncClient(timeout=30.0)

# Local Cache for Account Credentials to avoid spamming Firestore
_CRED_CACHE = {}

async def _get_credentials(account_id: str):
    """Fetch MT5 credentials from Firestore (mt5_accounts collection)"""
    if account_id in _CRED_CACHE:
        if _CRED_CACHE[account_id] is None:
            raise Exception(f"Account {account_id} not found in Firestore (Cached)")
        return _CRED_CACHE[account_id]
        
    try:
        # First try finding by document ID
        doc = db.collection("mt5_accounts").document(account_id).get()
        data = None
        if doc.exists:
            data = doc.to_dict()
        else:
            # Fallback to querying by 'id' or 'accountId' fields (legacy compat)
            docs = db.collection("mt5_accounts").where("id", "==", account_id).limit(1).get()
            if not docs:
                docs = db.collection("mt5_accounts").where("accountId", "==", account_id).limit(1).get()
            if docs:
                data = docs[0].to_dict()
                
        if not data:
            _CRED_CACHE[account_id] = None # Cache the missing status
            raise Exception(f"Account {account_id} not found in Firestore")
            
        login = data.get("login")
        password = data.get("password")
        server = data.get("server")
        
        if not login or not password or not server:
            _CRED_CACHE[account_id] = None
            raise Exception(f"Missing login credentials for account {account_id} in DB")
            
        creds = {"login": str(login), "password": password, "server": server}
        _CRED_CACHE[account_id] = creds
        return creds
        
    except Exception as e:
        if "Cached" not in str(e): # Only log the first time it fails
            logger.error(f"Failed to fetch credentials for {account_id}: {e}")
        raise

async def get_account(account_id: str):
    """
    Ensures the account is connected on the Fleet Manager.
    Instead of returning a complex object, it returns the connection port/status.
    """
    creds = await _get_credentials(account_id)
    login = creds["login"]
    
    # Check connect Status
    payload = {
        "account_id": login,
        "password": creds["password"],
        "server": creds["server"]
    }
    
    try:
         resp = await client.post(f"{FLEET_MANAGER_URL}/connect", json=payload)
         if resp.status_code != 200:
             raise Exception(f"Fleet Manager connect failed: {resp.text}")
         
         data = resp.json()
         # Return a stub that mimics old `{'connection': ...}` logic loosely, 
         # but actually we'll rewrite the consuming functions below to not need it.
         return {"account_id": login, "status": data.get("status")}
    except httpx.ConnectError:
         logger.error(f"Fleet Manager at {FLEET_MANAGER_URL} is OFFLINE. Cannot connect account {login}.")
         raise Exception("Trading server is currently offline. Please try again later.")
    except Exception as e:
         logger.error(f"Failed to connect terminal {login}: {e}")
         raise

# --- API METHODS ---

async def resolve_symbol(account_id, symbol):
    """Resolves generic symbol to broker specific (pass-through for now)"""
    if not symbol: return symbol
    clean_symbol = symbol.replace("/", "").upper()
    try:
        creds = await _get_credentials(account_id)
        login = creds["login"]
        resp = await client.get(f"{FLEET_MANAGER_URL}/accounts/{login}/symbols")
        if resp.status_code == 200:
            symbols = resp.json()
            # Exact
            if clean_symbol in symbols: return clean_symbol
            # Suffix
            for s in symbols:
                if s.startswith(clean_symbol) and len(s) <= len(clean_symbol) + 3:
                     return s
    except Exception:
        pass
    return clean_symbol

async def execute_trade(account_id, symbol, action, volume, sl=None, tp=None, comment="MacroLens AI", ticket=None, value=None):
    try:
        creds = await _get_credentials(account_id)
        login = creds["login"]
        resolved_symbol = await resolve_symbol(account_id, symbol)
        
        # Ensure connected
        await get_account(account_id)
        
        payload = {
            "symbol": resolved_symbol,
            "action": action,
            "volume": volume,
            "sl": sl,
            "tp": tp,
            "ticket": ticket,
            "comment": comment
        }
        
        resp = await client.post(f"{FLEET_MANAGER_URL}/accounts/{login}/execute", json=payload)
        
        if resp.status_code != 200:
            return {"success": False, "error": resp.text}
            
        data = resp.json()
        return {
            "success": True, 
            "ticket": data.get("ticket"),
            "price": data.get("price"),
            "comment": "Filled by FleetManager"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

async def get_account_information(account_id):
    cache_key = f"account_info:{account_id}"
    cached_info = await cache.get(cache_key)
    if cached_info: return cached_info

    try:
        creds = await _get_credentials(account_id)
        login = creds["login"]
        await get_account(account_id)
        
        info_resp = await client.get(f"{FLEET_MANAGER_URL}/accounts/{login}/account_info")
        pos_resp = await client.get(f"{FLEET_MANAGER_URL}/accounts/{login}/positions")
        
        if info_resp.status_code != 200:
             return {"status": "error", "error": info_resp.text}
             
        info = info_resp.json()
        positions = pos_resp.json() if pos_resp.status_code == 200 else []
        
        # Normalize positions for existing frontend/backend expectations
        safe_positions = []
        for p in positions:
            safe_positions.append({
                "id": str(p.get("ticket")),
                "ticket": p.get("ticket"),
                "symbol": p.get("symbol"),
                "type": "POSITION_TYPE_BUY" if p.get("type") == 0 else "POSITION_TYPE_SELL",
                "volume": p.get("volume"),
                "openPrice": p.get("price_open"),
                "currentPrice": p.get("price_current"),
                "sl": p.get("sl"),
                "tp": p.get("tp"),
                "swap": p.get("swap"),
                "commission": 0, # MT5 positions_get doesn't always show commission
                "profit": p.get("profit"),
                "comment": p.get("comment"),
                "time": datetime.utcfromtimestamp(p.get("time")).isoformat() + "Z" if p.get("time") else None
            })
            
        is_real = 'REAL' in str(info.get('trade_mode', '')).upper()

        result = {
            "balance": info.get('balance'),
            "equity": info.get('equity'),
            "margin": info.get('margin'),
            "free_margin": info.get('margin_free'),
            "leverage": info.get('leverage'),
            "currency": info.get('currency'),
            "positions": safe_positions,
            "status": "connected",
            "is_real": is_real
        }
        
        await cache.set(cache_key, result, ttl=5)
        return result
    except Exception as e:
        return {"status": "error", "error": str(e)}

async def get_symbol_price(account_id, symbol):
    try:
        creds = await _get_credentials(account_id)
        login = creds["login"]
        await get_account(account_id)
        
        resolved_symbol = await resolve_symbol(account_id, symbol)
        resp = await client.get(f"{FLEET_MANAGER_URL}/accounts/{login}/symbol/{resolved_symbol}")
        if resp.status_code == 200:
            data = resp.json()
            return {
                "bid": data.get("bid", 0),
                "ask": data.get("ask", 0),
                "symbol": resolved_symbol
            }
        return {"bid": 0, "ask": 0, "symbol": resolved_symbol}
    except Exception as e:
        if "Cached" not in str(e):
            logger.error(f"Fetch Price Error: {e}")
        return {"bid": 0, "ask": 0, "symbol": symbol}

async def fetch_candles(account_id, symbol, timeframe="1h", limit=500):
     try:
         creds = await _get_credentials(account_id)
         login = creds["login"]
         await get_account(account_id)
         
         resp = await client.get(
             f"{FLEET_MANAGER_URL}/accounts/{login}/candles/{symbol}",
             params={"timeframe": timeframe, "limit": limit}
         )
         if resp.status_code == 200:
             return resp.json()
         return []
     except Exception as e:
         logger.error(f"Fetch Candles Error: {e}")
         return []

async def fetch_history(account_id, period="today"):
    try:
        creds = await _get_credentials(account_id)
        login = creds["login"]
        await get_account(account_id)
        
        now = datetime.now()
        start_ts = int(now.replace(hour=0, minute=0, second=0).timestamp()) # Default today
        
        if period == 'last-week' or period == 'this-week':
            start_ts = int((now.timestamp() - 7*86400))
        elif period == 'last-month' or period == 'this-month':
            start_ts = int((now.timestamp() - 30*86400))
            
        end_ts = int(now.timestamp())
        
        resp = await client.get(
            f"{FLEET_MANAGER_URL}/accounts/{login}/history",
            params={"start_ts": start_ts, "end_ts": end_ts}
        )
        
        if resp.status_code != 200:
            return {"history": [], "error": resp.text}
            
        deals = resp.json()
        
        # Minimal parsing for frontend history view
        # We need to map MT5 deals (Entry/Exit) to completed trades
        history = []
        deals_by_pos = {}
        for d in deals:
            pid = d.get('position_id')
            if pid not in deals_by_pos: deals_by_pos[pid] = []
            deals_by_pos[pid].append(d)
            
        # Extract closed trades
        for pid, pos_deals in deals_by_pos.items():
            if len(pos_deals) >= 2:
                # Find IN and OUT
                entry = next((x for x in pos_deals if x.get('entry') == 0), None)
                exit_d = next((x for x in pos_deals if x.get('entry') in [1, 2]), None) # OUT or INOUT
                
                if entry and exit_d:
                    history.append({
                        "id": str(pid),
                        "positionId": str(pid),
                        "symbol": entry.get("symbol"),
                        "type": entry.get("type"), # 0 Buy, 1 Sell
                        "volume": entry.get("volume"),
                        "openPrice": entry.get("price"),
                        "closePrice": exit_d.get("price"),
                        "profit": exit_d.get("profit") + exit_d.get("swap") + exit_d.get("commission"),
                        "time": datetime.utcfromtimestamp(exit_d.get("time")).isoformat() + "Z",
                        "openTime": datetime.utcfromtimestamp(entry.get("time")).isoformat() + "Z"
                    })
                    
        # Sort newest first
        history.sort(key=lambda x: x.get('time', ''), reverse=True)
        return {"history": history, "status": "success"}
        
    except Exception as e:
        logger.error(f"History Fetch Failed: {e}")
        return {"history": [], "error": str(e)}

async def deploy_all_for_user(user_id: str):
    # Deployment is now just connecting to FleetManager lazy-loaded when needed
    pass

async def schedule_undeploy_for_user(user_id: str, delay_seconds: int = 180):
    pass

async def reconcile_deployments(connected_user_ids: list[str]):
    pass
