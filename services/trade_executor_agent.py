
import asyncio
import logging
from typing import Dict, Optional, List
from datetime import datetime
from backend.core.logger import setup_logger
from backend.services.metaapi_service import execute_trade, get_account_information
from backend.firebase_setup import initialize_firebase
from firebase_admin import firestore

logger = setup_logger("TRADE_EXECUTOR")

class TradeExecutorAgent:
    """
    Dedicated Agent for managing autonomous trade execution.
    Handles:
    1. User Permission Checks (Is Bot Enabled?)
    2. Risk Management (Position Sizing based on User Settings)
    3. Execution via MetaApi
    4. Logging to Firestore (for "AI Activity" feed)
    """

    def __init__(self):
        self.db = initialize_firebase()
        # Cache for user settings to reduce DB reads? 
        # For now, we fetch fresh to ensure instant toggle response.
        pass

    async def get_user_settings(self, user_id: str) -> Dict:
        """Fetches algo settings for a specific user."""
        try:
            # Check for 'algo_settings' subcollection or field in user doc
            # We'll use a subcollection 'settings' -> doc 'algo' for scalability
            # Or just fields in user doc for simplicity. 
            # Let's use a dedicated collection 'algo_settings' keyed by user_id
            
            doc = self.db.collection('algo_settings').document(user_id).get()
            
            default_settings = {
                "enabled": False,
                "risk_multiplier": 1.0, # 1.0 = 0.01 lots / $1000 or fixed 0.01? 
                # Let's define: Base Lot = 0.01. Multiplier * 0.01
                "excluded_pairs": [],
                "max_daily_loss": 50, # USD
                "mode": "conservative" # conservative, balanced, aggressive
            }
            
            if doc.exists:
                data = doc.to_dict()
                # Merge with defaults
                return {**default_settings, **data}
            
            return default_settings
        except Exception as e:
            logger.error(f"Error fetching settings for {user_id}: {e}")
            return {"enabled": False, "error": str(e)}

    async def calculate_position_size(self, account_id: str, risk_multiplier: float, symbol: str) -> float:
        """
        Calculates lot size based on equity and risk multiplier.
        Simple Model: 0.01 lots per $1000 equity * multiplier.
        """
        try:
            # 1. Get Equity
            acct = await get_account_information(account_id)
            if not acct or 'equity' not in acct:
                return 0.01 # Fallback safe
            
            equity = acct['equity']
            
            # 2. Calculate Base Lots (Generic rule: 0.01 per $1000)
            base_lots = (equity / 1000) * 0.01
            
            # 3. Apply Multiplier
            final_lots = base_lots * risk_multiplier
            
            # 4. Round/Normalize (Step 0.01, Min 0.01)
            final_lots = max(0.01, round(final_lots, 2))
            
            # [SAFETY] Cap at reasonable max (e.g. 5.0 lots) to prevent disaster
            if final_lots > 5.0: final_lots = 5.0
            
            return final_lots
        except Exception as e:
            logger.error(f"Error calculating size: {e}")
            return 0.01

    async def execute_strategy(self, user_id: str, account_id: str, signal: Dict) -> Dict:
        """
        Main Execution Method.
        Args:
            user_id: Owner of the account
            account_id: MetaApi Account ID
            signal: { "symbol": "EURUSD", "direction": "BUY", "confidence": 85, "sl_suggested": ..., "tp_suggested": ... }
        """
        symbol = signal.get("symbol")
        direction = signal.get("direction", "WAIT").upper()
        confidence = signal.get("confidence", 0)
        
        # 1. Validate Signal
        if direction not in ["BUY", "SELL"]:
            return {"status": "skipped", "reason": "Signal is WAIT"}
            
        if confidence < 80: # Hardcoded min threshold for auto-trade
            return {"status": "skipped", "reason": f"Low Confidence ({confidence}%)"}

        # 2. Check User Settings
        settings = await self.get_user_settings(user_id)
        
        if not settings.get("enabled"):
            self._log_activity(user_id, symbol, "LOG", "Signal detected but Bot is DISABLED.", confidence)
            return {"status": "skipped", "reason": "Bot Disabled"}
            
        if symbol in settings.get("excluded_pairs", []):
             return {"status": "skipped", "reason": "Pair Excluded"}

        # 3. Calculate Size
        vol = await self.calculate_position_size(account_id, settings.get("risk_multiplier", 1.0), symbol)
        
        # 4. Execute
        try:
            logger.info(f"EXECUTING {direction} {symbol} for {user_id} (Lots: {vol})")
            
            result = await execute_trade(
                account_id=account_id,
                symbol=symbol,
                action=direction,
                volume=vol,
                sl=signal.get('sl_suggested'),
                tp=signal.get('tp_suggested'),
                comment=f"AutoBot {confidence}%"
            )
            
            if result.get('error'):
                 self._log_activity(user_id, symbol, "ERROR", f"Execution Failed: {result['error']}", confidence)
                 return {"status": "error", "message": result['error']}
            
            # 5. Log Success
            self._log_activity(user_id, symbol, direction, f"Copied {direction} {vol} lots. Conf: {confidence}%", confidence)
            return {"status": "executed", "volume": vol, "ticket": result.get("orderId")}

        except Exception as e:
            logger.error(f"Execution Exception: {e}")
            self._log_activity(user_id, symbol, "ERROR", f"System Error: {str(e)}", confidence)
            return {"status": "error", "message": str(e)}

    async def execute_master_strategy(self, master_account_id: str, signal: Dict) -> Dict:
        """
        Executes a trade on the Master AI Account.
        If successful, broadcasts it to all subscribed users via copy trading dispatcher.
        """
        symbol = signal.get("symbol")
        direction = signal.get("direction", "WAIT").upper()
        confidence = signal.get("confidence", 0)
        
        # 1. Validate Signal
        if direction not in ["BUY", "SELL"]:
            return {"status": "skipped", "reason": "Signal is WAIT"}
            
        if confidence < 80: # Hardcoded min threshold for auto-trade
            return {"status": "skipped", "reason": f"Low Confidence ({confidence}%)"}

        # Master always uses 1.0 risk multiplier logic
        vol = await self.calculate_position_size(master_account_id, 1.0, symbol)
        
        # Execute Master Trade
        try:
            logger.info(f"MASTER AI EXECUTING {direction} {symbol} (Lots: {vol})")
            
            result = await execute_trade(
                account_id=master_account_id,
                symbol=symbol,
                action=direction,
                volume=vol,
                sl=signal.get('sl_suggested'),
                tp=signal.get('tp_suggested'),
                comment=f"MasterBot {confidence}%"
            )
            
            if result.get('error'):
                 logger.error(f"Master Execution Failed: {result['error']}")
                 return {"status": "error", "message": result['error']}
            
            # Log Master Success (Using 'master' as userId)
            self._log_activity('master', symbol, direction, f"Master Executed {direction} {vol} lots. Conf: {confidence}%", confidence)
            
            # BROADCAST TO SUBSCRIBERS
            asyncio.create_task(self.broadcast_trade(signal))
            
            return {"status": "executed", "volume": vol, "ticket": result.get("orderId")}

        except Exception as e:
            logger.error(f"Master Execution Exception: {e}")
            return {"status": "error", "message": str(e)}

    async def broadcast_trade(self, signal: Dict):
        """
        Copy Trading Dispatcher: Sends trade to all users with algo_settings.enabled == True
        """
        symbol = signal.get("symbol")
        direction = signal.get("direction", "WAIT").upper()
        
        if direction not in ["BUY", "SELL"]:
            return

        logger.info(f"BROADCASTING {direction} {symbol} to all subscribed users...")
        
        try:
            # 1. Fetch all users
            users_doc = self.db.collection('users').stream()
            
            tasks = []
            for doc in users_doc:
                user_data = doc.to_dict()
                user_id = doc.id
                account_id = user_data.get('activeAccountId') or user_data.get('metaapiAccountId')
                
                if not account_id:
                    continue
                    
                # Premium check: Only broadcast to premium/pro/admin users
                user_plan = user_data.get('plan', 'standard').lower()
                if user_plan not in ['premium', 'pro', 'admin']:
                    continue
                    
                # We will process each user in a separate async task to not block
                tasks.append(self.execute_strategy(user_id, account_id, signal))
                
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                success_count = sum(1 for r in results if isinstance(r, dict) and r.get('status') == 'executed')
                logger.info(f"Broadcast Complete. Copied to {success_count}/{len(tasks)} accounts.")
                
        except Exception as e:
            logger.error(f"Broadcast Error: {e}")

    def _log_activity(self, user_id: str, symbol: str, signal: str, reasoning: str, confidence: int):
        """
        Writes to Firestore 'bot_activity' collection for the Frontend feed.
        """
        try:
            self.db.collection('bot_activity').add({
                "userId": user_id,
                "symbol": symbol,
                "signal": signal,
                "reasoning": reasoning,
                "confidence": confidence,
                "timestamp": firestore.SERVER_TIMESTAMP
            })
        except Exception as e:
            logger.error(f"Failed to log activity: {e}")

# Singleton Instance
trade_executor = TradeExecutorAgent()
