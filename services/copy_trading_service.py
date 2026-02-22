import asyncio
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional
from backend.config import settings
from backend.core.database import DatabasePool
from metaapi_cloud_sdk import MetaApi

logger = logging.getLogger("CopyTradingService")

from metaapi_cloud_copyfactory_sdk import CopyFactory

class CopyTradingService:
    """
    Manages CopyFactory interactions: Subscription, Strategy Management, and Risk Settings.
    """
    def __init__(self):
        # Lazy Initialization to prevent Event Loop errors during import
        self.api = None
        self._cf_client = None
        self.copy_factory = None

    def _ensure_init(self):
        if not self._cf_client:
            self.api = MetaApi(token=settings.META_API_TOKEN.get_secret_value())
            self._cf_client = CopyFactory(token=settings.META_API_TOKEN.get_secret_value())
            self.copy_factory = self._cf_client.configuration_api

    async def get_strategy_id(self, account_id: str) -> Optional[str]:
        """
        Retrieves (or creates) the Strategy ID for a given Master Account ID.
        """
        self._ensure_init()
        try:
            # Check if this account is already providing a strategy
            # FIX: Use pagination method
            strategies = await self.copy_factory.get_strategies_with_classic_pagination()
            for s in strategies:
                if s['accountId'] == account_id:
                    return s['_id']  # Assuming _id or id is returned
            
            # If not, create one
            logger.info(f"Creating new CopyFactory Strategy for Account {account_id}")
            
            # FIX: Generate ID first, then Update (Create)
            strat_id = await self.copy_factory.generate_strategy_id()
            
            strategy_body = {
                "name": f"MacroLens AI Master {account_id[:8]}",
                "description": "Autonomous AI Trading Strategy by MacroLens",
                "accountId": account_id,
                "type": "MEMBER" # 'MEMBER' means it's based on a MetaApi account
            }
            
            await self.copy_factory.update_strategy(strat_id, strategy_body)
            # Fetch it back or just return ID
            return strat_id
            
        except Exception as e:
            logger.error(f"Failed to get/create strategy: {e}")
            return None

    async def subscribe_user(self, user_account_id: str, master_account_id: str, risk_multiplier: float = 1.0) -> Dict:
        """
        Subscribes a User Account to the Master Strategy.
        """
        self._ensure_init()
        try:
            strategy_id = await self.get_strategy_id(master_account_id)
            if not strategy_id:
                return {"error": "Could not identify Master Strategy"}

            # Check existing subscriptions
            # FIX: Use pagination method
            subs = await self.copy_factory.get_subscribers_with_classic_pagination()
            existing = next((s for s in subs if s.get('accountId') == user_account_id), None)

            # Note: A subscriber can have multiple subscriptions (strategies).
            # We need to see if they are subscribed to THIS strategy.
            # But the 'subscriber' object usually contains 'subscriptions' list.
            
            if existing:
                # Check if already subscribed to this strategy
                current_subs = existing.get('subscriptions', [])
                target_sub = next((x for x in current_subs if x['strategyId'] == strategy_id), None)
                
                if target_sub:
                     # Update Risk if needed
                     # We need to update the WHOLE subscriber object or just the sub list?
                     # update_subscriber replaces the config? Or patches?
                     # Docs usually imply PUT (Replace). So we must preserve other subs if any.
                     
                     # Update the specific subscription
                     target_sub['multiplier'] = risk_multiplier
                else:
                     # Add new subscription to list
                     current_subs.append({
                         "strategyId": strategy_id,
                         "multiplier": risk_multiplier,
                         "symbolMapping": {}
                     })
                
                logger.info(f"Updating subscription for {user_account_id}")
                
                # Construct update body
                update_body = {
                    "name": existing.get('name'),
                    "accountId": existing.get('accountId'),
                    "subscriptions": current_subs
                }
                
                await self.copy_factory.update_subscriber(existing['_id'], update_body)
                return {"status": "updated", "risk": risk_multiplier}
            
            # Create New Subscription
            logger.info(f"Creating subscription for {user_account_id} to {strategy_id}")
            
            # FIX: Generate ID first
            sub_id = await self.copy_factory.generate_account_id()
            
            new_sub_body = {
                "name": f"User {user_account_id[:8]} Sub",
                "accountId": user_account_id,
                "subscriptions": [{
                    "strategyId": strategy_id,
                    "multiplier": risk_multiplier 
                }]
            }
            
            await self.copy_factory.update_subscriber(sub_id, new_sub_body)
            
            # Persist to Local DB for UI mirroring
            await self._save_subscription_to_db(user_account_id, master_account_id, risk_multiplier)
            
            return {"status": "subscribed", "risk": risk_multiplier}

        except Exception as e:
            logger.error(f"Subscription Failed: {e}")
            return {"error": str(e)}

    async def unsubscribe_user(self, user_account_id: str) -> bool:
        """
        Removes all subscriptions for a user account.
        """
        self._ensure_init()
        try:
            subs = await self.copy_factory.get_subscribers_with_classic_pagination()
            target = next((s for s in subs if s.get('accountId') == user_account_id), None)
            
            if target:
                # FIX: Use remove_subscriber
                await self.copy_factory.remove_subscriber(target['_id'])
                await self._remove_subscription_from_db(user_account_id)
                return True
            return False
        except Exception as e:
            logger.error(f"Unsubscribe Failed: {e}")
            return False

    async def _save_subscription_to_db(self, user_acc_id: str, master_acc_id: str, multiplier: float):
        """Saves subscription state to SQLite for Frontend access"""
        try:
            db = await DatabasePool.get_connection()
            await db.execute("""
                INSERT OR REPLACE INTO copy_subscriptions (user_account_id, master_account_id, risk_multiplier, status, updated_at)
                VALUES (?, ?, ?, 'ACTIVE', ?)
            """, (user_acc_id, master_acc_id, multiplier, datetime.utcnow().isoformat()))
            await db.commit()
        except Exception as e:
            logger.error(f"DB Save Failed: {e}")

    async def _remove_subscription_from_db(self, user_acc_id: str):
        try:
            db = await DatabasePool.get_connection()
            await db.execute("UPDATE copy_subscriptions SET status = 'INACTIVE' WHERE user_account_id = ?", (user_acc_id,))
            await db.commit()
        except Exception as e:
            logger.error(f"DB Remove Failed: {e}")

copy_trading_service = CopyTradingService()
