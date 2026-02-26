
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from typing import List, Dict, Optional
from pydantic import BaseModel
import uvicorn
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
import logging
import pandas as pd

# Standardize Logging Levels (Silence Verbose Libraries)
logging.getLogger("socketio").setLevel(logging.WARNING)
logging.getLogger("engineio").setLevel(logging.WARNING)
logging.getLogger("socketio.client").setLevel(logging.WARNING) # MetaApi SDK
logging.getLogger("engineio.client").setLevel(logging.WARNING) # MetaApi SDK
logging.getLogger("urllib3").setLevel(logging.WARNING)

import time
from datetime import datetime
import socketio
from dotenv import load_dotenv
import uuid

# Setup Logging
# Setup Logging
from backend.core.logger import setup_logger
logger = setup_logger("API")

# Load Environment
# Load Environment
BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(BACKEND_DIR / ".env") 

# --- New Imports (Phase 6) ---
from backend.core.database import DatabasePool
from backend.core.meta_api_client import meta_api_singleton
from backend.core.cache import cache
from backend.models.schemas import LoginRequest, AnalysisRequest, TradeRequest
from backend.services.agent_service import MacroLensAgentV2
from backend.services.credit_service import credit_service

from backend.services.technical_analysis import TechnicalAnalyzer
from backend.orchestration.session_man import SessionManager
from backend.middleware.auth import get_current_user
from backend.firebase_setup import initialize_firebase

# Import Services
from backend.services.metaapi_service import (
    execute_trade as meta_execute_trade,
    fetch_candles as meta_fetch_candles,
    get_account_information as meta_get_account_info,
    fetch_history as meta_fetch_history,
    get_symbol_price as meta_get_symbol_price
)
from backend.services.streaming_service import stream_manager
from backend.services.trade_manager_service import trade_manager
from backend.workers.firestore_listener import start_firestore_listener
from backend.workers.signal_evaluator import start_signal_evaluator
from backend.middleware.rate_limiter import rate_limiter
from backend.services.event_monitor import event_monitor
from backend.services.cognitive_loop import cognitive_engine

from backend.config import settings

from backend.services.websocket_manager import websocket_manager
from backend.services.ai_engine import get_usd_engine # Stream Injection

FIRESTORE_DB = initialize_firebase()
STATUS_CACHE = {} 
USER_MAPPING_CACHE = {} 
DEFAULT_ACCOUNT_ID = settings.META_API_ACCOUNT_ID or None  # No hardcoded fallback — accounts come from Firestore

# --- Globals ---
agent = MacroLensAgentV2() 
session_man = SessionManager()
ANALYSIS_SEMAPHORE = asyncio.Semaphore(20) # Increased for 50 User Capacity (w/ Cache)

# --- Lifespan Manager (Startup/Shutdown) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("--------------------------------------------------")
    logger.info("!!! MACROLENS BACKEND v4.0 (Production) STARTING UP !!!")
    logger.info("--------------------------------------------------")
    
    # 1. DB Health Check
    db_ok = await DatabasePool.health_check()
    logger.info(f"Database Health Check: {'OK' if db_ok else 'FAIL'}")
    
    # 2. Warmup Stream
    # await stream_manager.start()
    # asyncio.create_task(stream_manager.start_stream(DEFAULT_ACCOUNT_ID, "warmup_user"))
    
    # [DEBUG] Force Reload Triggered
    
    # 3. Start Firestore Listener (Re-enabled for Account Provisioning)
    asyncio.create_task(start_firestore_listener(fetch_bridge_candles, process_firestore_trade))
    # import subprocess
    # logger.info("Launching Isolated Firestore Listener...")
    # subprocess.Popen([sys.executable, "backend/run_listener.py"], cwd=os.getcwd())
    
    # 4. Start Trade Manager Service
    asyncio.create_task(trade_manager.start())
    logger.info("Trade Manager Service initialized.")
    
    # 5. Start Market Data Scheduler (Background Loop)
    asyncio.create_task(schedule_market_data_updates())

    # 5.5 Start Signal Evaluator (Duration tracking & Expire by Price)
    asyncio.create_task(start_signal_evaluator())

    # 6. Start Deployment Reconciliation (Phase 12: Zombie Reaper)
    asyncio.create_task(schedule_reconciliation()) # New Task

    # 6. Start Quantitative Event Monitor
    await event_monitor.start()

    # 7. Start Cognitive Engine (Self-Awareness Loop)
    asyncio.create_task(cognitive_engine.start_loop())
    logger.info("Cognitive Engine (OODA Loop) Started.")

    # 8. Seed Neural Stream (Immediate Data for UI)
    try:
        usd_engine = get_usd_engine()
        if usd_engine:
            data = usd_engine.get_latest()
            from backend.core.system_state import world_state
            
            world_state.add_log("System", "Neural Stream Connection Established.", "INFO")
            if data:
                strength = "STRONG" if data['signal_value'] > 0 else "WEAK"
                msg = f"USD Index Analysis: The Dollar is currently {data['signal']} (Score: {data['composite_index']:.2f}). This indicates {strength} momentum in the Greenback, likely impacting major pairs."
                world_state.add_log("Global Macro", msg, "MACRO")
        
        # Seed COT
        from backend.services.cot.api import engine as cot_engine
        cot_data = cot_engine.get_latest_sentiment("EURUSD")
        if cot_data:
            cot_bias = "Bullish" if cot_data['smart_sentiment'] > 0 else "Bearish"
            world_state.add_log("Institutional", f"Institutional Data Online. Smart Money is currently {cot_bias} on EURUSD (Willco: {cot_data['willco_index']:.1f}). Monitoring order flow...", "INFO")

    except Exception as e:
        logger.warning(f"Startup Seed Failed: {e}")

    yield
    
    # Shutdown
    logger.info("Shutting down resources...")
    await trade_manager.stop()
    await stream_manager.stop_all()
    await event_monitor.stop()
    await DatabasePool.close()
    logger.info("Shutdown Complete.")

async def schedule_market_data_updates():
    """Runs scrapers every 1 hour in background"""
    # Wait 30s after startup to not slow down boot
    await asyncio.sleep(30)
    
    while True:
        try:
            logger.info("Starting Scheduled Market Data Refresh...")
            from backend.scrapers.update_market_data import update_hourly_tasks, update_retail_tasks
            
            # 1. Run Hourly Tasks (Calendar, News, Institutional, History)
            await update_hourly_tasks()
            
            # 2. Run 24-Hour Tasks (Retail Sentiment) - ONCE PER DAY
            if not hasattr(schedule_market_data_updates, 'counter'):
                schedule_market_data_updates.counter = 0
            
            if schedule_market_data_updates.counter % 24 == 0:
                logger.info("Running Daily Tasks (Retail Sentiment)...")
                await update_retail_tasks()
            
            schedule_market_data_updates.counter += 1
            
            # 3. USD Index Pulse (Every Hour)
            try:
                usd_engine = get_usd_engine()
                if usd_engine:
                    data = usd_engine.get_latest()
                    if data:
                        from backend.core.system_state import world_state
                        strength = "STRONG" if data['signal_value'] > 0 else "WEAK"
                        msg = f"USD Index Update: The Dollar remains {data['signal']} (Score: {data['composite_index']:.2f}). Market structure suggests {strength} USD performance."
                        world_state.add_log("Global Macro", msg, "MACRO")
                        logger.info(f"Scheduled USD Index Update: {msg}")
            except Exception as e:
                logger.error(f"Scheduled USD Field: {e}")

            # 4. COT Pulse (Every 4 Hours - concurrent with Retail)
            if schedule_market_data_updates.counter % 4 == 0:
                try:
                    from backend.services.cot.api import engine as cot_engine
                    # Flash COT for major pairs
                    for sym in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]:
                         cot_data = cot_engine.get_latest_sentiment(sym)
                         if cot_data:
                             bias = "Bullish" if cot_data['smart_sentiment'] > 0 else "Bearish"
                             if cot_data['willco_index'] > 80 or cot_data['willco_index'] < 20: 
                                 bias += " (Extreme)"
                             bias_desc = "Accumulating" if cot_data['smart_sentiment'] > 0 else "Distributing"
                             if cot_data['willco_index'] > 80 or cot_data['willco_index'] < 20: 
                                 bias += " (Extreme)"
                             msg = f"Institutional Flow ({sym}): Smart Money is {bias_desc} {sym}. Net positioning is {bias} ({cot_data['smart_sentiment']:.1f}%). Willco Index: {cot_data['willco_index']:.1f}."
                             world_state.add_log("Institutional", msg, "COT")
                except Exception as e:
                    logger.error(f"Scheduled COT Pulse Failed: {e}")

            logger.info("Scheduled Data Refresh Success.")
        except Exception as e:
            logger.error(f"Scheduled Data Refresh Failed: {e}")
            
        # Wait 1 hour (3600 seconds)
        await asyncio.sleep(3600)


async def schedule_reconciliation():
    """
    Runs every 10 minutes to cleanup Zombie Terminals.
    Ensures that if server restarts, deployed terminals are undeployed if user is offline.
    """
    # Wait 2 mins after startup to let users reconnect naturally
    await asyncio.sleep(120)
    
    while True:
        try:
            logger.info("[Reaper] Starting Deployment Reconciliation...")
            
            # Get Online Users from WebSocket Manager
            online_users = list(websocket_manager._connected_users.values())
            # Deduplicate
            online_users = list(set(online_users))
            
            from backend.services.metaapi_service import reconcile_deployments
            await reconcile_deployments(online_users)
            
            logger.info("[Reaper] Reconciliation Complete.")
            
        except Exception as e:
            logger.error(f"[Reaper] Task Failed: {e}")
            
        # Run every 10 minutes
        await asyncio.sleep(600)


app = FastAPI(lifespan=lifespan, title="MacroLens Trading API")

# --- API Endpoints (Phase 4: Dashboard Intelligence) ---
from backend.core.system_state import world_state

@app.get("/api/world-state")
async def get_world_state():
    """Returns the AI's current global view (Bias, Risk, Session)."""
    return world_state

@app.get("/api/agent/stream")
async def agent_stream():
    """Returns the latest system logs/thoughts for the UI Stream."""
    from backend.core.system_state import world_state
    return world_state.logs

@app.get("/api/agent-stream")
async def agent_stream_alias():
    """Alias for legacy frontend calls."""
    return await agent_stream()


# Payment Router Integration
from backend.routers import payment
app.include_router(payment.router)

# Admin Router Integration
from backend.routers import admin
app.include_router(admin.router)

# USD Index Integration
from backend.services.usd_index.api import router as usd_index_router
app.include_router(usd_index_router)

# COT Router Integration
from backend.services.cot import api as cot_api
app.include_router(cot_api.router)

# Trade Execution Router
from backend.routers import trade
app.include_router(trade.router)

# Copy Trading Router (Phase 13: Algo Bot)
from backend.routers import copy_trading
app.include_router(copy_trading.router)

# Algo Bot Config Router (Phase 14)
from backend.routers import algo_routes
app.include_router(algo_routes.router)

# Market Scanner Heatmap Router (Phase 15)
from backend.routers import heatmap_routes
app.include_router(heatmap_routes.router)

# Scanner Dashboard Router (Admin — Full Pipeline View)
from backend.routers import scanner_routes
app.include_router(scanner_routes.router)

# Support Chat Router
from backend.routers import support
app.include_router(support.router)

# Macro Intelligence Router (Dashboard Redesign)
from backend.routers import macro
app.include_router(macro.router)

# CORS Configuration
origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8000",
    "https://macrolens-ai.com",
    "https://www.macrolens-ai.com",
    "https://api.macrolens-ai.com",
    "https://macrolens-ai3.web.app",
    "https://macrolens-ai3.firebaseapp.com",
    "http://158.220.82.187:5173",
    "http://158.220.82.187:8000"
]

# --- SIMPLIFIED CORS HANDLING ---
# Replaces CORSMiddleware and Deduplication logic with a single manual implementation.
# This avoids "Multiple values" and ensuring headers are ALWAYS present.

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

class ManualCORSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 0. BYPASS Socket.IO (It handles its own CORS via python-socketio)
        # preventing "Multiple Values" errors.
        # Check both path and query for "socket.io" to be safe, though path should suffice.
        if "/socket.io" in str(request.url.path):
            return await call_next(request)

        # 1. Handle Preflight OPTIONS requests directly
        if request.method == "OPTIONS":
            response = Response()
        else:
            try:
                response = await call_next(request)
            except Exception as e:
                # Fallback for crashes
                logger.error(f"Middleware Exception: {e}")
                response = Response("Internal Server Error", status_code=500)

        # 2. Add CORS Headers to ALL responses (Simple & Robust)
        # FORCE CLEAN SLATE: Remove any existing CORS headers to prevent duplicates
        cors_keys = [
            "access-control-allow-origin",
            "access-control-allow-credentials", 
            "access-control-allow-methods",
            "access-control-allow-headers"
        ]
        for key in cors_keys:
            if key in response.headers:
                del response.headers[key]
                
        # Now set exact headers
        request_origin = request.headers.get("origin")
        ALLOWED_ORIGINS = {
            "http://localhost:3000",
            "http://localhost:5173",
            "http://localhost:8000",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:8000",
            "https://macrolens-ai.com",
            "https://www.macrolens-ai.com",
            "https://api.macrolens-ai.com",
            "https://macrolens-ai3.web.app",
            "https://macrolens-ai3.firebaseapp.com",
            "http://158.220.82.187:5173",
            "http://158.220.82.187:8000"
        }
        
        if request_origin:
            # PRODUCTION: Only allow whitelisted origins
            if request_origin in ALLOWED_ORIGINS:
                response.headers["Access-Control-Allow-Origin"] = request_origin
                response.headers["Access-Control-Allow-Credentials"] = "true"
            else:
                # Unknown origin — still allow for dev but log it
                logger.debug(f"CORS: Unknown origin {request_origin} — allowing for compatibility")
                response.headers["Access-Control-Allow-Origin"] = request_origin
                response.headers["Access-Control-Allow-Credentials"] = "true"
        
        elif not request_origin:
            response.headers["Access-Control-Allow-Origin"] = "*"

        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With, Accept, Origin"
        
        return response

app.add_middleware(ManualCORSMiddleware)
# Removed CORSMiddleware and CORSDeduplicationMiddleware

# --- WebSocket Integration (Phase 7) ---
from backend.services.websocket_manager import websocket_manager
# Mount Socket.IO App (Standard Pattern: wrap FastAPI with SocketIO or mount SocketIO as sub-app)
# Here we mount it at the root effectively by using `app.mount` or simply wrapping it in run.
# Ideally, we use socketio.ASGIApp to wrap FastAPI, but since we are inside FastAPI defining routes,
# we need to mount it.
# app.mount("/socket.io", websocket_manager.app)


@app.get("/health")
async def health_check():
    """
    Production Health Check for Load Balancers.
    """
    # 1. Check Database (Primary)
    db_status = await DatabasePool.health_check()
    
    # 2. Check Cache (Redis or Memory)
    cache_status = True
    try:
        await cache.set("health", "ok", ttl=5)
        val = await cache.get("health")
        if val != "ok": cache_status = False
    except Exception:
        cache_status = False
        
    status = 200 if (db_status and cache_status) else 503
    
    return {
        "status": "ok" if status == 200 else "degraded",
        "timestamp": datetime.utcnow().isoformat(),
        "components": {
            "database": "connected" if db_status else "disconnected",
            "cache": "operational" if cache_status else "failed",
            "workers": os.cpu_count() or 1
        }
    }


# --- Helper Functions (Preserved) ---

# Valid UUID Helper
def is_valid_uuid(val):
    try:
        uuid.UUID(str(val))
        return True
    except ValueError:
        return False

# --- Helper Functions ---
async def resolve_account_id(user_id: str, requested_id: Optional[str] = None, strict: bool = False) -> str:
    """
    Intelligent Account Resolver
    1. If requested_id is provided and VALID -> Use it
    2. If user has a cached/mapped account -> Use it
    3. If user has a Firestore account linked -> Use it and Cache it
    4. Fallback -> DEFAULT_ACCOUNT_ID (Read Only Pool)
    """
    # 0. Validate Request Param
    if requested_id:
        if is_valid_uuid(requested_id):
            return requested_id
        else:
            logger.warning(f"Invalid requested account_id: {requested_id}. Falling back to default resolution.")

    cache_key = f"user_mapping:{user_id}"

    # 1. Check Cache
    if user_id in USER_MAPPING_CACHE:
        cached = USER_MAPPING_CACHE[user_id]
        c_id = cached.get("account_id")
        if c_id != DEFAULT_ACCOUNT_ID and not is_valid_uuid(c_id):
             logger.warning(f"Invalid Cached Account ID {c_id} for user {user_id}. Ignoring.")
        else:
            # Strict Mode Check
            if strict and c_id == DEFAULT_ACCOUNT_ID:
                if not cached.get("is_privileged"): 
                    raise HTTPException(status_code=403, detail="No linked trading account found.")
            return c_id

        
    # Lookup in Firestore
    account_id = None
    is_privileged = False
    
    if FIRESTORE_DB:
        try:
            doc = FIRESTORE_DB.collection('users').document(user_id).get()
            if doc.exists:
                data = doc.to_dict()
                if data.get('role') == 'admin':
                    is_privileged = True
                found = data.get('activeAccountId') or data.get('metaapiAccountId') or data.get('accountId')
                
                # --- AUTO-HEAL: Validate Account Exists ---
                valid_account_id = None
                
                # 1. If we have a candidate, check if it exists in mt5_accounts
                if found and found != "2oCCIawGhcpflqdPguRl":
                    # Query mt5_accounts where accountId == found
                    # Note: We can't check by Doc ID, we must query field
                    acct_query = FIRESTORE_DB.collection('mt5_accounts').where('accountId', '==', found).limit(1).get()
                    if len(acct_query) > 0:
                        valid_account_id = found
                    else:
                        logger.warning(f"Active Account {found} not found in DB. Searching for alternative...")
                
                # 2. If no valid account yet, search for ANY valid account for this user
                if not valid_account_id:
                     user_accounts = FIRESTORE_DB.collection('mt5_accounts').where('userId', '==', user_id).limit(1).get()
                     if len(user_accounts) > 0:
                         valid_account_id = user_accounts[0].to_dict().get('accountId')
                         logger.info(f"Full-Healing: Switching User {user_id} to Account {valid_account_id}")
                         # Update User Doc asynchronously-ish
                         FIRESTORE_DB.collection('users').document(user_id).update({"activeAccountId": valid_account_id})
                
                if valid_account_id:
                     account_id = valid_account_id
                     
        except Exception as e:
            logger.error(f"Firestore Account Lookup Failed for {user_id}: {e}")
            
    if not account_id and is_privileged:
        account_id = DEFAULT_ACCOUNT_ID

    # If found (either specific or privileged default), return and cache
    if account_id:
        cache_data = {
            "account_id": account_id,
            "is_privileged": is_privileged
        }
        await cache.set(cache_key, cache_data, ttl=3600)
        return account_id

    # Fallback
    if strict:
        raise HTTPException(status_code=403, detail="No linked trading account found.")

    # Modified behavior: Return None instead of Default for regular users
    # This ensures Agent Chat sees "No Account" instead of Admin Data
    return None

async def execute_trade_logic(user_id: str, action: str, symbol: str, volume: Optional[float], sl: Optional[float], tp: Optional[float], ticket: int, value: Optional[float] = None):
    target_account_id = await resolve_account_id(user_id, strict=True) 
    
    # --- PHASE 12: EXECUTION SAFETY ---
    # Smart Order Logic for New Trades (BUY/SELL)
    final_action = action.upper()
    
    if final_action in ['BUY', 'SELL'] and symbol:
        try:
             # 1. Fetch Live Price (Fastest available)
             quote = await meta_get_symbol_price(target_account_id, symbol)
             
             if quote:
                 bid = quote['bid']
                 ask = quote['ask']
                 
                 # 2. Pre-Flight Validation (Invalidation Check)
                 if sl:
                     if final_action == 'SELL' and bid > sl:
                         return {"success": False, "error": f"⛔ Trade Invalidated: Price ({bid}) is already above Stop Loss ({sl})."}
                     if final_action == 'BUY' and ask < sl:
                         return {"success": False, "error": f"⛔ Trade Invalidated: Price ({ask}) is already below Stop Loss ({sl})."}

                 # 3. Smart Order Type Selection (Pending vs Market)
                 # Note: 'value' (entry price) is passed from frontend for Pending Orders usually, 
                 # but for "Market" requests, we might want to check price deviation.
                 # If 'value' is None, it implies Market execution was requested at "Current Price".
                 # If 'value' IS present, it implies a desired Entry Price level.
                 
                 desired_entry = value if value else (ask if final_action == 'BUY' else bid)
                 
                 if final_action == 'SELL':
                     # If Current Price is significantly LOWER than Desired Entry -> Breakout hasn't happened or missed?
                     # Logic: Sell Stop if Price > Entry? No, Sell Stop is for Sell BELOW market.
                     # Context: 
                     # - Scenario A: Price (1.1820) > Entry (1.1810) -> Better Price/Retracement -> MARKET SELL (or Limit)
                     # - Scenario B: Price (1.1800) < Entry (1.1810) -> Breakout already happened? OR Missed?
                     # User Request: "If entry price best price possible... but current market is... recommend pending"
                     
                     # Let's assume 'value' is the SIGNAL ENTRY PRICE given by AI.
                     if value: 
                         # If Bid < Value (Price Below Entry) -> We missed it? Or it's a breakdown? 
                         # Actually usually SELL STOP is placed *below* market. 
                         # If Price > Entry (1.1820 vs 1.1810) -> Better Entry -> MARKET SELL.
                         # If Price < Entry (1.1800 vs 1.1810) -> We are late. Chase or Pending? 
                         # User wanted "Smart Order".
                         
                         # STRICT RULE: 
                         # If Bid > Value: MARKET SELL (Better Price)
                         # If Bid < Value: SELL LIMIT (Wait for pullback) OR MARKET (Chase)
                         # Wait, standard definitions:
                         # Sell Limit: Place ABOVE current price.
                         # Sell Stop: Place BELOW current price.
                         
                         # If Signal Entry (1.1810) is ABOVE current market (1.1800) -> Sell Limit (Wait for rise to 1.1810).
                         # If Signal Entry (1.1810) is BELOW current market (1.1820) -> Sell Stop (Wait for drop to 1.1810).
                         
                         if bid < value:
                             # Current: 1.1800, Entry: 1.1810. Price is BELOW entry. 
                             # We want to sell at 1.1810. That is ABOVE market. -> SELL LIMIT.
                             final_action = 'SELL_LIMIT'
                             
                         elif bid > value:
                             # Current: 1.1820, Entry: 1.1810. Price is ABOVE entry.
                             # We want to sell at 1.1810 (Breakout level?).
                             # If it's a breakout strategy, we use SELL STOP.
                             # If it's a retracement strategy, we take MARKET (Better price).
                             # AI usually gives "Entry" as the trigger.
                             # User said: "agent send signal below current market price... for sell... entry 1.1810 from analysis... current 1.1800? No."
                             
                             # Let's stick to simple logic:
                             # If Price is BETTER than Entry (Higher for Sell), take MARKET.
                             # If Price is WORSE than Entry (Lower for Sell), use LIMIT to wait for pullback.
                             pass # Default to MARKET (as it's better)
                         
                 elif final_action == 'BUY':
                     if value:
                         if ask > value:
                             # Current: 1.1820, Entry: 1.1810. Price is ABOVE entry.
                             # We want to buy at 1.1810. That is BELOW market. -> BUY LIMIT.
                             final_action = 'BUY_LIMIT'
                         # Else (Ask < Value): Better Price -> MARKET.

        except Exception as e:
            logger.warning(f"Smart Order Logic Failed: {e}")
            # Fallback to standard execution

    try:
        res = await meta_execute_trade(
            account_id=target_account_id, 
            symbol=symbol, 
            action=final_action, 
            volume=volume, 
            sl=sl, 
            tp=tp,
            ticket=ticket,
            value=value
        )
        return res
    except Exception as e:
        logger.error(f"Trade Error: {e}")
        return {"success": False, "error": str(e)}

async def process_firestore_trade(user_id: str, cmd_type: str, payload: dict):
    logger.info(f"Firestore Trade: {cmd_type} for {user_id}")
    try:
        if cmd_type == 'PLACE_ORDER':
            return await execute_trade_logic(user_id, payload.get('side', 'BUY'), payload.get('symbol', ''), 
                                             float(payload.get('lots', 0.01)), float(payload.get('sl', 0)), float(payload.get('tp', 0)), 0)
        elif cmd_type in ['CLOSE_TRADE', 'CLOSE_POS']:
            return await execute_trade_logic(user_id, 'close', '', None, None, None, int(payload.get('ticket', 0)))
        elif cmd_type == 'MODIFY_SL':
            return await execute_trade_logic(user_id, 'modify', '', None, float(payload.get('sl', 0)), None, int(payload.get('ticket', 0)))
        elif cmd_type == 'MODIFY_TP':
            return await execute_trade_logic(user_id, 'modify', '', None, None, float(payload.get('tp', 0)), int(payload.get('ticket', 0)))
        elif cmd_type == 'CLOSE_ALL':
            return await execute_trade_logic(user_id, 'close_all', '', None, None, None, 0)
    except HTTPException as he:
        logger.warning(f"Trade Blocked for {user_id}: {he.detail}")
        return {"status": "error", "msg": he.detail}
    return {"status": "ignored", "msg": f"Unknown command type: {cmd_type}"}

async def fetch_bridge_candles(user_id: str, symbol: str, timeframe: str, count: int = 100):
    # Relaxed Strictness: Allow all users to see charts using Local MT5 data
    target_account_id = await resolve_account_id(user_id, strict=False)
    if not target_account_id: target_account_id = "shared_terminal"
    
    logger.info(f"[{user_id}] Fetching Bridge Candles for {symbol} via {target_account_id}")
    mt5_symbol = symbol.replace("/", "").replace("USDT", "USD")
    try:
        candles = await meta_fetch_candles(target_account_id, mt5_symbol, timeframe, count)
        formatted_candles = []
        if candles:
            for c in candles:
                candle = c if isinstance(c, dict) else c.__dict__
                if 'time' in candle:
                     if isinstance(candle['time'], datetime):
                         candle['datetime'] = candle['time'].isoformat()
                         candle['time'] = int(candle['time'].timestamp()) 
                     elif isinstance(candle['time'], str):
                         candle['datetime'] = candle['time'] 
                formatted_candles.append(candle)
        return formatted_candles
    except Exception as e:
        logger.error(f"MetaApi Candle Fetch Error: {e}")
        return None

# --- Dependencies ---
async def check_rate_limit(request: Request, user: dict = Depends(get_current_user)):
    user_id = user['uid']
    path = request.url.path
    if "analysis" in path:
        allowed = await rate_limiter.is_allowed(user_id, "analysis", limit=2, window=60)
    elif "trade" in path:
        allowed = await rate_limiter.is_allowed(user_id, "trade", limit=10, window=60)
    else:
        allowed = True
    if not allowed:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Please wait.")
    return True

# --- Endpoints ---

@app.post("/api/auth/login")
async def login_and_warmup(req: LoginRequest, background_tasks: BackgroundTasks, user: dict = Depends(get_current_user)):
    return await session_man.start_session(user['uid'])

@app.post("/api/analysis/new")
async def perform_analysis(req: AnalysisRequest, user: dict = Depends(get_current_user), rate_check: bool = Depends(check_rate_limit)):
    if float(ANALYSIS_SEMAPHORE._value) <= 0:
        logger.warning("Analysis Queue Full. Waiting...")
    async with ANALYSIS_SEMAPHORE:
        user_id = user['uid']
        COST_PER_ANALYSIS = 1 # 1 Credit per analysis (approx 2k tokens = $0.015 cost)
        
        # 0. Pre-Check: Ensure MT5 Account is Connected
        # If no account, fail immediately (don't deduct credits)
        try:
            # Validate UUID before calling resolve_account_id or inside it
            # But here we just ensure we are passing valid data
            acc_id = await resolve_account_id(user_id, strict=True)
            if not is_valid_uuid(acc_id):
                 raise HTTPException(status_code=400, detail="Invalid Account ID format (must be UUID).")
        except HTTPException:
            raise HTTPException(
                status_code=400, 
                detail="No MT5 Account Connected. Please connect your trading account in Settings to perform analysis."
            )

        # 1. Credit Check & Deduction (Atomic-ish via Firestore)
        try:
            # Use CreditService
            if not await credit_service.deduct_credits(user_id, COST_PER_ANALYSIS, "AI Analysis"):
                current_credits = await credit_service.get_balance(user_id)
                logger.warning(f"User {user_id} Insufficient Credits: {current_credits} < {COST_PER_ANALYSIS}")
                raise HTTPException(
                    status_code=402, 
                    detail=f"Insufficient Credits. Required: {COST_PER_ANALYSIS}, Available: {current_credits}. Please top up."
                )
            
        except HTTPException as he:
            raise he
        except Exception as e:
            logger.error(f"Credit Check Error: {e}")
            raise HTTPException(status_code=500, detail="Credit check failed. Please contact support.")

        # 2. Proceed with Analysis
        check_candles = await fetch_bridge_candles(user_id, req.symbol, req.timeframe, 100)
        if not check_candles:
            raise HTTPException(504, "Timeout fetching market data from MT5.")
            
        async def agent_fetch_callback(account_id_ignored, symbol, timeframe):
            return await fetch_bridge_candles(user_id, symbol, timeframe, 100)
            
        result = await agent.process_single_request(req.symbol, req.timeframe, fetch_callback=agent_fetch_callback, user_id=user_id)
        
        if result.get("status") == "error":
            # Refund credits on AI failure
            try: 
                await credit_service.refund_credits(user_id, COST_PER_ANALYSIS, "Refund: Analysis Failed")
                logger.info(f"Analysis Failed. Refunded {COST_PER_ANALYSIS} credits to {user_id}.")
            except Exception as refund_err:
                logger.error(f"Credit refund failed for {user_id}: {refund_err}")
            
            raise HTTPException(status_code=500, detail=result["message"])
            
        return result

@app.post("/api/trade/execute")
async def execute_trade(req: TradeRequest, user: dict = Depends(get_current_user), rate_check: bool = Depends(check_rate_limit)):
    return await execute_trade_logic(user['uid'], req.action, req.symbol, req.volume, req.sl, req.tp, req.ticket, req.value)

@app.get("/api/trade/status")
async def get_status(account_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    try:
        user_id = user['uid']
        if account_id and not is_valid_uuid(account_id):
             # Just ignore invalid IDs and let resolver find the correct one
             logger.warning(f"Ignored invalid account_id param: {account_id}")
             account_id = None

        target_account_id = await resolve_account_id(user_id, account_id, strict=True)
        
        # 1. Try Stream First (Fastest)
        try:
             listener = await stream_manager.start_stream(target_account_id, user_id)
        except Exception as stream_err:
             logger.error(f"Stream Start Error: {stream_err}")
             listener = None

        # Check if stream has valid data (balance defined)
        if listener and listener.state and (listener.state.get('balance', 0) > 0 or listener.state.get('status') == 'connected'):
            # v4.1 Update: Only return stream state if it has DATA or is explicity CONNECTED.
            # Just checking "Not None" allowed 0-balance initialized state to pass through.
            return listener.state
            
        # 2. Fallback to Direct API Call (Like History) - Ensures reliable loading
        logger.info(f"Stream data not ready for {user_id}. Fetching direct snapshot.")
        try:
            snapshot = await meta_get_account_info(target_account_id)
            if "error" not in snapshot:
                # Update the stream listener state so subsequent calls are fast
                if listener:
                     try: listener.update_state(snapshot)
                     except: pass # Ignore listener update failures
                return snapshot
        except Exception as e:
            logger.error(f"Snapshot failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            
        return {"status": "connecting", "message": "Stream initializing..."}
    except HTTPException as http_ex:
        # Handle 403/404 gracefully
        logger.warning(f"Status Check Warning: {http_ex.detail}")
        return {"status": "no_account", "message": http_ex.detail}
    except Exception as outer_e:
        logger.error(f"CRITICAL STATUS 500: {outer_e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"status": "error", "message": "Internal Service Error"}

@app.post("/api/trade/stream/reset")
async def reset_stream_endpoint(user: dict = Depends(get_current_user)):
    """Forcefully resets the real-time stream for the user's account."""
    try:
        user_id = user['uid']
        account_id = await resolve_account_id(user_id, strict=True)
        await stream_manager.reset_stream(account_id)
        return {"status": "ok", "message": "Stream reset successfully. Please reconnect."}
    except Exception as e:
        logger.error(f"Stream Reset Failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/trade/history")
async def get_trade_history(period: str = "today", user: dict = Depends(get_current_user)):
    try:
        user_id = user['uid']
        target_account_id = await resolve_account_id(user_id, strict=True)
        res = await meta_fetch_history(target_account_id, period)
        if "error" in res:
             return {"status": "error", "message": res["error"]}
        return res["history"]
    except Exception as e:
        logger.error(f"History Endpoint Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"status": "error", "message": str(e)}

@app.post("/api/trade/sync-assets")
async def sync_assets(user: dict = Depends(get_current_user)):
    """
    Fetches all tradable symbols from the user's active broker account
    and saves them to their profile for the Frontend to use.
    """
    try:
        user_id = user['uid']
        user_ref = FIRESTORE_DB.collection("users").document(user_id)
        user_doc = user_ref.get()
        
        if not user_doc.exists:
            raise HTTPException(status_code=404, detail="User Profile Not Found")
             
        profile = user_doc.to_dict()
        account_id = profile.get('activeAccountId')
        
        if not account_id:
            raise HTTPException(status_code=400, detail="No Active Trading Account Linked")
             
        # Fetch Symbols from Broker
        symbols = await meta_get_all_symbols(account_id)
        
        if not symbols:
            raise HTTPException(status_code=500, detail="Failed to fetch symbols from broker.")
             
        # Save to user's Firestore profile
        user_ref.update({
            "settings.availableSymbols": symbols,
            "settings.lastSymbolSync": datetime.utcnow().isoformat()
        })
        
        return {
            "status": "success", 
            "count": len(symbols), 
            "message": f"Successfully synced {len(symbols)} assets from broker."
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Asset Sync Failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/market/data")
async def get_market_data(symbol: str, timeframe: str = "H1", count: int = 100, api_key: str = "", rsi: Optional[int] = None, bb: Optional[str] = None, user_id: str = "default"):
    try:
        candles = await fetch_bridge_candles(user_id, symbol, timeframe, count)
        if not candles: return []
        if rsi or bb:
            df = pd.DataFrame(candles)
            if not df.empty and 'close' in df.columns:
                analyzer = TechnicalAnalyzer(candles) 
                result = {"symbol": symbol, "candles": candles[-count:], "indicators": {}}
                if rsi: result["indicators"][f"rsi_{rsi}"] = analyzer.get_rsi(rsi)
                if bb:
                    parts = bb.split(',')
                    period = int(parts[0]) if len(parts) > 0 else 20
                    dev = float(parts[1]) if len(parts) > 1 else 2.0
                    result["indicators"][f"bb_{period}"] = analyzer.get_bollinger_bands(period, dev)
                return result
        return []
    except HTTPException as he:
        # Expected behavior for users without accounts (Strict Mode)
        if he.status_code == 403:
            # logger.debug(f"Market Data Skipped: {he.detail}")
            return []
        raise he
    except Exception as e:
        logger.error(f"Market Data Error: {e}")
        return []

@app.get("/api/market/quote")
async def get_market_quote(symbol: str, user_id: str = "default", user: dict = Depends(get_current_user)):
    try:
        user_id = user['uid']
        target_account_id = await resolve_account_id(user_id, strict=False)
        quote = await meta_get_symbol_price(target_account_id, symbol)
        if not quote:
             # Fallback to candle close if quote fails?
             # No, better to return 404 or empty to indicate live data unavailable
             return {"symbol": symbol, "status": "unavailable"}
        return quote
    except Exception as e:
        logger.error(f"Quote Error: {e}")
        return {"status": "error", "message": str(e)}

class ChatRequest(BaseModel):
    message: str

@app.post("/api/agent/chat")
async def chat_with_agent(req: ChatRequest, user: dict = Depends(get_current_user)):
    """
    Direct chat interface with the MacroLens Agent.
    Now supports Context-Aware responses for the specific user.
    """
    try:
        # Resolve Account ID for Context Awareness
        try:
            acc_id = await resolve_account_id(user['uid'])
        except:
            acc_id = None
            
        # Try to get User's active model from Trade Manager settings
        model_override = None
        if user['uid'] in trade_manager.user_settings:
            model_override = trade_manager.user_settings[user['uid']].active_model
            
        # [NEW] Deduct Credit for Agent Chat
        if not await credit_service.deduct_credits(user['uid'], 1, "Agent Chat"):
             raise HTTPException(status_code=402, detail="Insufficient credits for Agent Chat. Please top up.")

        response = await agent.ask(req.message, user_id=user['uid'], account_id=acc_id, model_override=model_override, user_data=user)
        return {"response": response}
    except Exception as e:
        logger.error(f"Chat Endpoint Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Trade Manager Endpoints ---
# --- Trade Manager Endpoints ---

class TradeManagerSettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    autonomous: Optional[bool] = None
    interval_minutes: Optional[int] = None
    cooldown_minutes: Optional[int] = None
    min_confidence: Optional[int] = None
    max_actions_hour: Optional[int] = None
    max_actions_day: Optional[int] = None
    min_profit_to_trail: Optional[int] = None
    breakeven_buffer: Optional[int] = None
    credit_mode: Optional[str] = None
    allowed_actions: Optional[List[str]] = None
    blacklist: Optional[List[str]] = None
    whitelist: Optional[List[str]] = None
    active_hours_start: Optional[str] = None
    active_hours_end: Optional[str] = None
    skip_weekends: Optional[bool] = None

@app.get("/api/trade-manager/settings")
async def get_trade_manager_settings(user: dict = Depends(get_current_user)):
    """Get Trade Manager settings for current user"""
    return await trade_manager.get_settings(user['uid'])

@app.put("/api/trade-manager/settings")
async def update_trade_manager_settings(
    settings: TradeManagerSettingsUpdate,
    user: dict = Depends(get_current_user)
):
    """Update Trade Manager settings"""
    """Update Trade Manager settings"""
    logger.info(f">>> [DEBUG] Route reached. User: {user.get('uid')} <<<")
    # Filter out None values
    # Filter out None values
    settings_dict = {k: v for k, v in settings.dict().items() if v is not None}
    success = await trade_manager.update_settings(user['uid'], settings_dict)
    if success:
        return {"status": "ok", "message": "Settings updated"}
    raise HTTPException(status_code=500, detail="Failed to update settings")

@app.get("/api/trade-manager/history")
async def get_trade_manager_history(
    limit: int = 50,
    user: dict = Depends(get_current_user)
):
    """Get Trade Manager action history/logs"""
    return await trade_manager.get_history(user['uid'], limit)

@app.post("/api/trade-manager/analyze")
async def trigger_trade_manager_analysis(
    position_id: Optional[str] = None,
    user: dict = Depends(get_current_user)
):
    """Manually trigger Trade Manager analysis (costs 1 credit)"""
    # Credit check
    if not await credit_service.deduct_credits(user['uid'], 1, "Manual Analysis"):
        raise HTTPException(status_code=402, detail="Insufficient credits")
    
    results = await trade_manager.analyze_now(user['uid'], position_id)
    return {"status": "ok", "recommendations": results}

@app.get("/api/events/analyze")
async def analyze_event_manually(
    event_name: str, 
    currency: str = "USD", 
    forecast: Optional[float] = None, 
    previous: Optional[float] = None
):
    """
    Manually analyze an event to see the 'EventPredictor' output.
    Used by Frontend to display the 'Prediction Card'.
    """
    from backend.services.event_predictor import EventPredictor
    predictor = EventPredictor()
    
    # If values not provided, try to fetch latest from DB
    if forecast is None or previous is None:
        # Simple lookup logic (optional/simplified)
        pass 

    prediction = predictor.predict_event(event_name, forecast or 0.0, previous or 0.0, currency)
    
    return {
        "event": event_name,
        "prediction": prediction.predicted_outcome, # BEAT / MISS
        "confidence": prediction.confidence,
        "forecast_pips": prediction.avg_pips,   # Part 1 Feature
        "trend_direction": prediction.trend_direction, # Part 2 Feature
        "details": prediction.rationale
    }

# --- Socket.IO Integration ---
# Wrap FastAPI with Socket.IO to support WebSockets properly (Bypassing BaseHTTPMiddleware)
app = socketio.ASGIApp(websocket_manager.sio, other_asgi_app=app)

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
