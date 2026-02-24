import argparse
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List
from datetime import datetime
import json
import uvicorn
from fastapi import FastAPI, HTTPException, Request
import MetaTrader5 as mt5

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] Worker: %(message)s')
logger = logging.getLogger("MT5Worker")

# --- ARGUMENTS ---
parser = argparse.ArgumentParser(description="MT5 Python Worker")
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--account", type=int, required=True)
parser.add_argument("--password", type=str, required=True)
parser.add_argument("--server", type=str, required=True)
parser.add_argument("--path", type=str, required=True)
args, _ = parser.parse_known_args()

# --- GLOBALS ---
MT5_READY = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    global MT5_READY
    logger.info(f"Initializing MT5 terminal from {args.path}")
    
    # 1. Initialize MT5
    # The portable flag ensures it uses the local folder for data
    initialized = mt5.initialize(
        path=args.path,
        login=args.account,
        password=args.password,
        server=args.server,
        portable=True
    )
    
    if not initialized:
        logger.error(f"MT5 init failed, error code = {mt5.last_error()}")
        MT5_READY = False
    else:
        # 2. Re-login explicitly just to be safe
        authorized = mt5.login(
            login=args.account, 
            password=args.password, 
            server=args.server
        )
        if authorized:
            logger.info(f"MT5 Login Success for {args.account} on {args.server}")
            MT5_READY = True
            
            # 3. Minimize the GUI to save system rendering resources!
            try:
                import ctypes
                import time
                # Give the window a second to physically render before minimizing
                time.sleep(2)
                user32 = ctypes.windll.user32
                # The internal Win32 class name for the MT5 terminal
                hwnd = user32.FindWindowW("MetaQuotes::MetaTrader::5.00", None)
                if hwnd:
                    user32.ShowWindow(hwnd, 6) # 6 = SW_MINIMIZE
                    logger.info("Terminal window successfully minimized.")
            except Exception as e:
                logger.warning(f"Could not minimize window: {e}")
                
        else:
            logger.error(f"MT5 login failed, error code = {mt5.last_error()}")
            MT5_READY = False
            
    yield
    
    # Shutdown
    logger.info("Shutting down MT5 terminal...")
    mt5.shutdown()

app = FastAPI(lifespan=lifespan)

# --- DEPENDENCY/CHECK ---
def check_mt5():
    if not MT5_READY:
        raise HTTPException(status_code=503, detail="MT5 is not initialized or failed to connect to broker")
    # Quick health check
    if mt5.terminal_info() is None:
         # Try to reconnect
         logger.warning("Terminal disconnected. Attempting reconnect...")
         mt5.login(login=args.account, password=args.password, server=args.server)
         if mt5.terminal_info() is None:
             raise HTTPException(status_code=503, detail="Terminal disconnected.")

# --- API ROUTES ---
@app.get("/health")
async def health():
    return {
        "status": "up" if MT5_READY else "down", 
        "account": args.account,
        "connected": mt5.terminal_info().connected if MT5_READY and mt5.terminal_info() else False
    }

@app.get("/account_info")
async def get_account_info():
    check_mt5()
    info = mt5.account_info()
    if info is None:
        raise HTTPException(status_code=500, detail="Failed to get account info")
    return info._asdict()

@app.get("/positions")
async def get_positions():
    check_mt5()
    positions = mt5.positions_get()
    if positions is None:
        return []
    return [p._asdict() for p in positions]

@app.get("/orders")
async def get_orders():
    check_mt5()
    orders = mt5.orders_get()
    if orders is None:
        return []
    return [o._asdict() for o in orders]

@app.post("/execute")
async def execute_trade(req: Request):
    """
    Expects JSON:
    {
        "symbol": "EURUSD",
        "action": "BUY" | "SELL" | "CLOSE" | "MODIFY",
        "volume": 0.01,
        "sl": 1.1000,
        "tp": 1.1100,
        "ticket": 1234567, # only for close/modify
        "comment": "Trade context"
    }
    """
    check_mt5()
    body = await req.json()
    action = body.get("action", "").upper()
    symbol = body.get("symbol")
    
    if action in ["BUY", "SELL"]:
        # Prepare Order
        order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
        price = mt5.symbol_info_tick(symbol).ask if action == "BUY" else mt5.symbol_info_tick(symbol).bid
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(body.get("volume", 0.01)),
            "type": order_type,
            "price": price,
            "sl": float(body.get("sl", 0.0)),
            "tp": float(body.get("tp", 0.0)),
            "deviation": 20,
            "magic": 1001,
            "comment": body.get("comment", ""),
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise HTTPException(status_code=400, detail=f"Order failed: {result.comment} (Code: {result.retcode})")
            
        return {"success": True, "ticket": result.order, "price": result.price}
        
    elif action == "CLOSE":
        ticket = body.get("ticket")
        if not ticket: raise HTTPException(status_code=400, detail="ticket required")
        pos = mt5.positions_get(ticket=ticket)
        if not pos: raise HTTPException(status_code=404, detail="Position not found")
        p = pos[0]
        
        order_type = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = mt5.symbol_info_tick(p.symbol).bid if p.type == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(p.symbol).ask
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": p.symbol,
            "volume": p.volume,
            "type": order_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": 1001,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise HTTPException(status_code=400, detail=f"Close failed: {result.comment}")
        return {"success": True, "ticket": ticket}
        
    elif action == "MODIFY":
        ticket = body.get("ticket")
        sl = float(body.get("sl", 0.0))
        tp = float(body.get("tp", 0.0))
        
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": sl,
            "tp": tp
        }
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise HTTPException(status_code=400, detail=f"Modify failed: {result.comment}")
        return {"success": True, "ticket": ticket}
        
    raise HTTPException(status_code=400, detail="Invalid action")

@app.get("/symbols")
async def get_symbols():
    check_mt5()
    symbols = mt5.symbols_get()
    if not symbols: return []
    return [s.name for s in symbols]

@app.get("/symbol/{symbol}")
async def get_symbol_info(symbol: str):
    check_mt5()
    info = mt5.symbol_info(symbol)
    if not info: raise HTTPException(status_code=404, detail="Symbol not found")
    return info._asdict()

@app.get("/candles/{symbol}")
async def fetch_candles(symbol: str, timeframe: str = "H1", limit: int = 500):
    check_mt5()
    tf_map = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1, "W1": mt5.TIMEFRAME_W1, "MN1": mt5.TIMEFRAME_MN1
    }
    tf = tf_map.get(timeframe.upper(), mt5.TIMEFRAME_H1)
    
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, limit)
    if rates is None:
        raise HTTPException(status_code=500, detail=f"Failed to fetch rates for {symbol}")
        
    # Convert numpy array to list of dicts
    result = []
    for r in rates:
        result.append({
            "time": datetime.utcfromtimestamp(r['time']).isoformat() + "Z",
            "open": float(r['open']),
            "high": float(r['high']),
            "low": float(r['low']),
            "close": float(r['close']),
            "tick_volume": int(r['tick_volume'])
        })
    return result

@app.get("/history")
async def get_history(start_ts: int, end_ts: int):
    check_mt5()
    start_dt = datetime.utcfromtimestamp(start_ts)
    end_dt = datetime.utcfromtimestamp(end_ts)
    deals = mt5.history_deals_get(start_dt, end_dt)
    if deals is None:
        return []
    return [d._asdict() for d in deals]

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
