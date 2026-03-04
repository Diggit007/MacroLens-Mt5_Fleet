import asyncio
import logging
import psutil
from typing import Dict, Any, Optional
import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from pydantic import BaseModel

from provisioner import FleetProvisioner

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] FleetManager: %(message)s')
logger = logging.getLogger("FleetManager")

app = FastAPI(title="MT5 Fleet Manager GateWay", version="1.0.0")

# --- CONFIGURATION ---
import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_MT5_PATH = "C:\\Program Files\\MetaTrader 5"  # You must install MT5 here first!
INSTANCES_DIR = os.path.join(BASE_DIR, "instances")

provisioner = FleetProvisioner(base_mt5_path=BASE_MT5_PATH, instances_dir=INSTANCES_DIR)

# State Management: Dict[account_id, dict(port, pid, status)]
ACTIVE_WORKERS: Dict[str, dict] = {}
http_client = httpx.AsyncClient(timeout=15.0)

# The lock prevents race conditions on startup
provision_lock = asyncio.Lock()

class ConnectRequest(BaseModel):
    account_id: str
    password: str
    server: str
    user_id: str = ""  # Firebase UID for Firestore trade history sync

@app.post("/connect")
async def connect_terminal(req: ConnectRequest):
    """Provisions and connects a new terminal, or returns existing connection info"""
    acc = req.account_id
    
    async with provision_lock:
        if acc in ACTIVE_WORKERS:
            worker = ACTIVE_WORKERS[acc]
            
            # 1. Check if the process is physically alive first!
            if psutil.pid_exists(worker['pid']):
                # It's alive. Let's see if the web port is ready yet.
                try:
                    resp = await http_client.get(f"http://127.0.0.1:{worker['port']}/health", timeout=2.0)
                    if resp.status_code == 200:
                        logger.info(f"Account {acc} already active on port {worker['port']}")
                        return {"status": "success", "message": "Already connected", "worker": worker}
                except Exception:
                    # Port not ready, but process is alive. Do NOT kill it!
                    logger.info(f"Worker {acc} is still booting MT5... Please wait.")
                    return {"status": "pending", "message": "Still starting up", "worker": worker}
            else:
                # Process actually died
                logger.warning(f"Worker {acc} physically died. Restarting...")
                ACTIVE_WORKERS.pop(acc, None)
                
        # Start new worker
        if acc not in ACTIVE_WORKERS:
            try:
                worker_info = await provisioner.provision_terminal(acc, req.password, req.server, user_id=req.user_id)
                ACTIVE_WORKERS[acc] = worker_info
                return {"status": "success", "message": "Connected", "worker": worker_info}
            except Exception as e:
                logger.error(f"Failed to connect account {acc}: {e}")
                raise HTTPException(status_code=500, detail=str(e))

@app.post("/disconnect/{account_id}")
async def disconnect_terminal(account_id: str):
    if account_id in ACTIVE_WORKERS:
        worker = ACTIVE_WORKERS[account_id]
        provisioner.kill_worker(worker['pid'])
        ACTIVE_WORKERS.pop(account_id, None)
        return {"status": "success", "message": "Disconnected"}
    return {"status": "info", "message": "Not running"}

# --- GATEWAY / PROXY ROUTES ---
async def proxy_request(account_id: str, method: str, path: str, json_body: dict = None, params: dict = None):
    if account_id not in ACTIVE_WORKERS:
        raise HTTPException(status_code=404, detail="Account not connected. Call /connect first.")
        
    worker = ACTIVE_WORKERS[account_id]
    
    if not psutil.pid_exists(worker['pid']):
        ACTIVE_WORKERS.pop(account_id, None)
        raise HTTPException(status_code=503, detail="Worker process crashed. Reconnect required.")

    worker_port = worker['port']
    url = f"http://127.0.0.1:{worker_port}{path}"
    
    try:
        if method == "GET":
            response = await http_client.get(url, params=params)
        elif method == "POST":
            response = await http_client.post(url, json=json_body)
        else:
             raise Exception(f"Unsupported method {method}")
             
        if response.status_code != 200:
             raise HTTPException(status_code=response.status_code, detail=response.text)
             
        return response.json()
    except httpx.RequestError as e:
        logger.error(f"Proxy request failed for {account_id}, it might still be booting: {e}")
        raise HTTPException(status_code=502, detail="Worker process unreachable. Wait for MT5 to load.")

@app.get("/accounts/{account_id}/account_info")
async def account_info(account_id: str):
    return await proxy_request(account_id, "GET", "/account_info")

@app.get("/accounts/{account_id}/positions")
async def positions(account_id: str):
    return await proxy_request(account_id, "GET", "/positions")

@app.get("/accounts/{account_id}/symbols")
async def symbols(account_id: str):
    return await proxy_request(account_id, "GET", "/symbols")

@app.get("/accounts/{account_id}/symbol/{symbol}")
async def symbol_info(account_id: str, symbol: str):
    return await proxy_request(account_id, "GET", f"/symbol/{symbol}")

@app.post("/accounts/{account_id}/execute")
async def execute(account_id: str, request: Request):
    body = await request.json()
    return await proxy_request(account_id, "POST", "/execute", json_body=body)

@app.get("/accounts/{account_id}/candles/{symbol}")
async def fetch_candles(account_id: str, symbol: str, timeframe: str = "H1", limit: int = 500):
    return await proxy_request(account_id, "GET", f"/candles/{symbol}", params={"timeframe": timeframe, "limit": limit})
    
@app.get("/accounts/{account_id}/history")
async def history(account_id: str, start_ts: int, end_ts: int):
    return await proxy_request(account_id, "GET", "/history", params={"start_ts": start_ts, "end_ts": end_ts})

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
