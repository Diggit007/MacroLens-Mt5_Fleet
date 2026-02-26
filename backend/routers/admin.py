"""
Admin Routes
============
Protected admin-only endpoints for dashboard statistics and management.
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Optional, Dict, List
from datetime import datetime, timedelta
import logging
import sqlite3
from collections import defaultdict

from backend.middleware.auth import get_current_user
from backend.firebase_setup import initialize_firebase
from backend.models.payment import PaymentTransaction

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])

# Initialize Firestore
FIRESTORE_DB = initialize_firebase()


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Dependency that ensures user is an admin"""
    if user.get("role") != "admin":
        # Check Firestore for role
        try:
            doc = FIRESTORE_DB.collection("users").document(user["uid"]).get()
            if doc.exists and doc.to_dict().get("role") == "admin":
                return user
        except Exception as e:
            logger.error(f"Admin check error: {e}")
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/online")
async def get_online_users(user: dict = Depends(require_admin)):
    """Get real-time count of authenticated WebSocket users."""
    from backend.services.websocket_manager import websocket_manager
    return {
        "online_count": websocket_manager.online_count,
        "online_user_ids": websocket_manager.online_user_ids
    }


@router.get("/stats")
async def get_admin_stats(user: dict = Depends(require_admin)):
    """
    Get overview statistics for admin dashboard.
    Returns user counts, revenue summary, and system status.
    """
    try:
        stats = {
            "users": {"total": 0, "active": 0, "new_this_week": 0, "by_tier": {}},
            "revenue": {"total": 0, "this_month": 0, "pending": 0},
            "credits": {"total_consumed": 0, "low_balance_users": 0},
            "trading": {"open_positions": 0, "trades_today": 0},
            "system": {"status": "healthy", "uptime": "99.9%"},
            "growth_chart": [],
        }
        daily_signups = defaultdict(int)

        # 1. User Statistics
        users_ref = FIRESTORE_DB.collection("users").get()
        now = datetime.utcnow()
        week_ago = now - timedelta(days=7)
        tier_counts = defaultdict(int)
        low_balance_count = 0
        active_count = 0

        for doc in users_ref:
            data = doc.to_dict()
            stats["users"]["total"] += 1

            # Count by tier
            tier = data.get("tier", "free")
            tier_counts[tier] += 1

            # Check credits
            credits = data.get("credits", 0)
            if credits < 10:
                low_balance_count += 1

            # Check if active (subscription_status or recent login)
            if data.get("subscription_status") == "active" or data.get("subscriptionStatus") == "active":
                active_count += 1

            # Check new users (if createdAt exists)
            created_at = data.get("createdAt")
            if created_at:
                try:
                    if hasattr(created_at, "timestamp"):
                        created_dt = datetime.fromtimestamp(created_at.timestamp())
                    else:
                        created_dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                    created_naive = created_dt.replace(tzinfo=None)
                    if created_naive > week_ago:
                        stats["users"]["new_this_week"] += 1
                    # Track daily signups for growth chart (last 30 days)
                    thirty_days_ago = now - timedelta(days=30)
                    if created_naive > thirty_days_ago:
                        day_key = created_naive.strftime("%Y-%m-%d")
                        daily_signups[day_key] += 1
                except:
                    pass

        stats["users"]["active"] = active_count
        stats["users"]["by_tier"] = dict(tier_counts)

        # Build growth chart (fill missing days with 0)
        for i in range(30, -1, -1):
            d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            stats["growth_chart"].append({"date": d, "signups": daily_signups.get(d, 0)})
        stats["credits"]["low_balance_users"] = low_balance_count

        # 2. Revenue Statistics (from SQLite)
        try:
            conn = sqlite3.connect(PaymentTransaction.DB_PATH)
            cursor = conn.cursor()

            # Total revenue (completed payments)
            cursor.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM payment_transactions WHERE status = 'completed'"
            )
            stats["revenue"]["total"] = cursor.fetchone()[0]

            # This month revenue
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            cursor.execute(
                """SELECT COALESCE(SUM(amount), 0) FROM payment_transactions 
                   WHERE status = 'completed' AND created_at >= ?""",
                (month_start.isoformat(),),
            )
            stats["revenue"]["this_month"] = cursor.fetchone()[0]

            # Pending payments
            cursor.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM payment_transactions WHERE status = 'pending'"
            )
            stats["revenue"]["pending"] = cursor.fetchone()[0]

            conn.close()
        except Exception as e:
            logger.error(f"Revenue stats error: {e}")

        # 3. Trading Statistics
        try:
            trades_ref = FIRESTORE_DB.collection("trades").get()
            today = now.replace(hour=0, minute=0, second=0, microsecond=0)

            for doc in trades_ref:
                data = doc.to_dict()
                if data.get("status") != "CLOSED":
                    stats["trading"]["open_positions"] += 1

                entry_time = data.get("entryTime")
                if entry_time:
                    try:
                        if hasattr(entry_time, "timestamp"):
                            entry_dt = datetime.fromtimestamp(entry_time.timestamp())
                        else:
                            entry_dt = datetime.fromisoformat(str(entry_time).replace("Z", "+00:00"))
                        if entry_dt.replace(tzinfo=None) >= today:
                            stats["trading"]["trades_today"] += 1
                    except:
                        pass
        except Exception as e:
            logger.error(f"Trading stats error: {e}")

        return stats

    except Exception as e:
        logger.error(f"Admin stats error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users")
async def get_admin_users(
    extended: bool = True,
    limit: int = 100,
    offset: int = 0,
    user: dict = Depends(require_admin),
):
    """
    Get all users with extended profile information.
    Includes credits, tier, payment history, and last activity.
    """
    try:
        users = []
        users_ref = FIRESTORE_DB.collection("users").get()

        for doc in users_ref:
            data = doc.to_dict()
            user_data = {
                "id": doc.id,
                "email": data.get("email", ""),
                "role": data.get("role", "user"),
                "mt5_login": data.get("mt5_login") or data.get("mt5Login"),
                "mt5_server": data.get("mt5_server") or data.get("mt5Server"),
                "subscription_status": data.get("subscription_status") or data.get("subscriptionStatus", "inactive"),
            }

            if extended:
                user_data.update({
                    "credits": data.get("credits", 0),
                    "tier": data.get("tier", "free"),
                    "lastPaymentDate": data.get("lastPaymentDate"),
                    "activeAccountId": data.get("activeAccountId"),
                    "createdAt": str(data.get("createdAt", "")),
                    "lastLogin": str(data.get("lastLogin", "")),
                })

            users.append(user_data)

        # Sort by email
        users.sort(key=lambda x: x.get("email", "").lower())

        return {
            "users": users[offset : offset + limit],
            "total": len(users),
            "limit": limit,
            "offset": offset,
        }

    except Exception as e:
        logger.error(f"Admin users error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/users/{user_id}")
async def update_admin_user(
    user_id: str,
    update_data: dict,
    user: dict = Depends(require_admin)
):
    """
    Securely update a user's profile data (plan, role, config) as an admin.
    """
    try:
        user_ref = FIRESTORE_DB.collection("users").document(user_id)
        doc = user_ref.get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail="User not found")

        # Basic validation to ensure we only update allowed fields
        allowed_fields = [
            "mt5_login", "mt5_server", "mt5_password", "subscription_status", 
            "role", "tier", "credits", "activeAccountId"
        ]
        
        updates = {}
        for key, value in update_data.items():
            if key in allowed_fields:
                updates[key] = value

        if updates:
            user_ref.update(updates)
            logger.info(f"Admin {user['uid']} updated user {user_id}: {list(updates.keys())}")

        return {"success": True, "updated": updates}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users/{user_id}")
async def get_admin_user_detail(
    user_id: str, user: dict = Depends(require_admin)
):
    """
    Get detailed information about a specific user.
    Includes full profile, payment history, and recent activity.
    """
    try:
        logger.info(f"Fetching details for user: {user_id}")
        # Get user profile
        doc = FIRESTORE_DB.collection("users").document(user_id).get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail="User not found")

        data = doc.to_dict()
        profile = {
            "id": doc.id,
            "email": data.get("email", ""),
            "role": data.get("role", "user"),
            "credits": data.get("credits", 0),
            "tier": data.get("tier", "free"),
            "mt5_login": data.get("mt5_login") or data.get("mt5Login"),
            "mt5_server": data.get("mt5_server") or data.get("mt5Server"),
            "subscription_status": data.get("subscription_status") or data.get("subscriptionStatus", "inactive"),
            "activeAccountId": data.get("activeAccountId"),
            "lastPaymentDate": data.get("lastPaymentDate"),
            "subscriptionEndDate": data.get("subscriptionEndDate"),
            "createdAt": str(data.get("createdAt", "")),
            "lastLogin": str(data.get("lastLogin", "")),
            "referralCode": data.get("referralCode"),
            "referredBy": data.get("referredBy"),
            "suspended": data.get("suspended", False),
            "adminNotes": data.get("adminNotes", ""),
            "tags": data.get("tags", []),
        }

        # Add Real-time Deployment Status
        try:
            from backend.core.meta_api_client import meta_api_singleton
            active_acc = profile.get("activeAccountId")
            if active_acc:
                # Use get_latest_state (async) to fetch real status from MetaAPI
                profile["deployment_status"] = await meta_api_singleton.get_latest_state(active_acc)
            else:
                profile["deployment_status"] = "N/A"
        except Exception as e:
            logger.error(f"Deployment status check failed for {user_id}: {e}")
            profile["deployment_status"] = "Error"

        # Get analysis requests (last 30)
        analyses = []
        try:
            analysis_ref = (
                FIRESTORE_DB.collection("analysis_requests")
                .where("userId", "==", user_id)
                .limit(30)
                .get()
            )
            for a in analysis_ref:
                ad = a.to_dict()
                result = ad.get("result") or {}
                analyses.append({
                    "id": a.id,
                    "symbol": ad.get("symbol"),
                    "timeframe": ad.get("timeframe", "H1"),
                    "model": ad.get("model"),
                    "status": ad.get("status"),
                    "direction": result.get("direction") or ad.get("recommendation", ""),
                    "entry": result.get("entry"),
                    "sl": result.get("sl_suggested"),
                    "tp": result.get("tp_suggested"),
                    "confidence": result.get("confidence"),
                    "error_message": ad.get("error_message"),
                    "created_at": str(ad.get("created_at", ad.get("createdAt", ""))),
                    "completed_at": str(ad.get("completed_at", "")),
                })
        except Exception as e:
            logger.error(f"Analysis fetch error for {user_id}: {e}")

        # Get recent trades
        trades = []
        try:
            trades_ref = (
                FIRESTORE_DB.collection("trades")
                .where("userId", "==", user_id)
                .limit(20)
                .get()
            )
            for t in trades_ref:
                td = t.to_dict()
                trades.append({
                    "id": t.id,
                    "symbol": td.get("symbol"),
                    "type": td.get("type"),
                    "volume": td.get("volume"),
                    "profit": td.get("profit"),
                    "status": td.get("status"),
                    "entryTime": str(td.get("entryTime", "")),
                    "entryPrice": td.get("entryPrice"),
                    "closePrice": td.get("closePrice"),
                })
        except Exception as e:
            logger.error(f"Trades fetch error: {e}")

        # Get recent commands
        commands = []
        try:
            cmd_ref = (
                FIRESTORE_DB.collection("management_commands")
                .where("createdBy", "==", user_id)
                .limit(20)
                .get()
            )
            for c in cmd_ref:
                cd = c.to_dict()
                commands.append({
                    "id": c.id,
                    "type": cd.get("type"),
                    "status": cd.get("status"),
                    "createdAt": str(cd.get("createdAt", "")),
                })
        except Exception as e:
            logger.error(f"Commands fetch error: {e}")

        # Build usage chart (daily analysis counts, last 30 days)
        usage_chart = []
        try:
            now = datetime.utcnow()
            daily_counts = defaultdict(int)
            for a in analyses:
                ts = a.get("completed_at") or a.get("created_at") or ""
                if ts and ts not in ("None", ""):
                    try:
                        day_key = str(ts)[:10]
                        daily_counts[day_key] += 1
                    except:
                        pass
            for i in range(29, -1, -1):
                d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
                usage_chart.append({"date": d, "count": daily_counts.get(d, 0)})
        except Exception as e:
            logger.error(f"Usage chart error: {e}")

        # Get credit history
        credit_history = []
        try:
            ch_ref = (
                FIRESTORE_DB.collection("users").document(user_id)
                .collection("credit_history")
                .order_by("timestamp", direction="DESCENDING")
                .limit(30)
                .get()
            )
            for ch in ch_ref:
                chd = ch.to_dict()
                credit_history.append({
                    "amount": chd.get("amount"),
                    "reason": chd.get("reason"),
                    "admin": chd.get("admin"),
                    "balance_after": chd.get("balance_after"),
                    "timestamp": str(chd.get("timestamp", "")),
                })
        except Exception as e:
            logger.error(f"Credit history fetch error: {e}")

        # Get payment history
        payments = PaymentTransaction.get_user_transactions(user_id, limit=20)

        return {
            "profile": profile,
            "payments": payments,
            "analyses": analyses,
            "trades": trades,
            "commands": commands,
            "usage_chart": usage_chart,
            "credit_history": credit_history,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"User detail error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/revenue")
async def get_admin_revenue(
    period: str = "month", user: dict = Depends(require_admin)
):
    """
    Get revenue analytics.
    Period: 'day', 'week', 'month', 'year', 'all'
    """
    try:
        now = datetime.utcnow()
        
        if period == "day":
            start_date = now - timedelta(days=1)
        elif period == "week":
            start_date = now - timedelta(days=7)
        elif period == "month":
            start_date = now - timedelta(days=30)
        elif period == "year":
            start_date = now - timedelta(days=365)
        else:
            start_date = datetime(2020, 1, 1)

        conn = sqlite3.connect(PaymentTransaction.DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get transactions in period
        cursor.execute(
            """SELECT * FROM payment_transactions 
               WHERE created_at >= ? 
               ORDER BY created_at DESC""",
            (start_date.isoformat(),),
        )
        transactions = [dict(row) for row in cursor.fetchall()]

        # Calculate summaries
        total_revenue = sum(t["amount"] for t in transactions if t["status"] == "completed")
        pending_revenue = sum(t["amount"] for t in transactions if t["status"] == "pending")
        failed_count = len([t for t in transactions if t["status"] == "failed"])

        # Revenue by tier
        by_tier = defaultdict(float)
        for t in transactions:
            if t["status"] == "completed":
                by_tier[t.get("tier", "unknown")] += t["amount"]

        # Daily breakdown (last 30 days)
        daily = defaultdict(float)
        for t in transactions:
            if t["status"] == "completed":
                date_str = t["created_at"][:10] if t["created_at"] else "unknown"
                daily[date_str] += t["amount"]

        # Get pending approvals (Manual Payments) - No Limit
        cursor.execute(
            """SELECT * FROM payment_transactions 
               WHERE status = 'pending_verification' 
               ORDER BY created_at ASC"""
        )
        pending_approvals = [dict(row) for row in cursor.fetchall()]

        conn.close()

        return {
            "period": period,
            "total_revenue": total_revenue,
            "pending_revenue": pending_revenue,
            "failed_count": failed_count,
            "by_tier": dict(by_tier),
            "daily_breakdown": dict(sorted(daily.items())),
            "transactions": transactions[:50],  # Latest 50
            "pending_approvals": pending_approvals
        }

    except Exception as e:
        logger.error(f"Revenue analytics error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/system")
async def get_admin_system_health(user: dict = Depends(require_admin)):
    """
    Get system health information.
    """
    try:
        from backend.core.database import DatabasePool
        from backend.core.cache import cache

        health = {
            "timestamp": datetime.utcnow().isoformat(),
            "status": "healthy",
            "components": {},
        }

        # Database check
        try:
            db_ok = await DatabasePool.health_check()
            health["components"]["database"] = {
                "status": "connected" if db_ok else "disconnected",
                "type": "SQLite",
            }
        except Exception as e:
            health["components"]["database"] = {"status": "error", "error": str(e)}

        # Cache check
        try:
            await cache.set("admin_health_check", "ok", ttl=5)
            val = await cache.get("admin_health_check")
            health["components"]["cache"] = {
                "status": "operational" if val == "ok" else "degraded"
            }
        except Exception as e:
            health["components"]["cache"] = {"status": "error", "error": str(e)}

        # Firestore check
        try:
            test_doc = FIRESTORE_DB.collection("_health").document("check")
            test_doc.set({"timestamp": datetime.utcnow().isoformat()})
            health["components"]["firestore"] = {"status": "connected"}
        except Exception as e:
            health["components"]["firestore"] = {"status": "error", "error": str(e)}

        # Overall status
        all_ok = all(
            c.get("status") in ["connected", "operational"]
            for c in health["components"].values()
        )
        health["status"] = "healthy" if all_ok else "degraded"

        return health

    except Exception as e:
        logger.error(f"System health error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/users/{user_id}/credits")
async def adjust_user_credits(
    user_id: str,
    amount: int,
    reason: str = "Admin adjustment",
    user: dict = Depends(require_admin),
):
    """
    Adjust a user's credit balance.
    Positive amount adds credits, negative subtracts.
    """
    try:
        user_ref = FIRESTORE_DB.collection("users").document(user_id)
        doc = user_ref.get()
        
        if not doc.exists:
            raise HTTPException(status_code=404, detail="User not found")

        current_credits = doc.to_dict().get("credits", 0)
        new_credits = max(0, current_credits + amount)

        user_ref.update({"credits": new_credits})

        # Log credit history
        try:
            user_ref.collection("credit_history").add({
                "amount": amount,
                "reason": reason,
                "admin": user.get("email", user["uid"]),
                "balance_after": new_credits,
                "timestamp": datetime.utcnow(),
            })
        except Exception as e:
            logger.error(f"Credit history log error: {e}")

        logger.info(
            f"Admin {user['uid']} adjusted credits for {user_id}: "
            f"{current_credits} -> {new_credits} (reason: {reason})"
        )

        return {
            "success": True,
            "previous_credits": current_credits,
            "new_credits": new_credits,
            "adjustment": amount,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Credit adjustment error: {e}")
        raise HTTPException(status_code=500, detail=str(e))



@router.post("/users/{user_id}/deploy")
async def manual_deploy_user(
    user_id: str, user: dict = Depends(require_admin)
):
    """Manually deploy all accounts for a user (Synchronous REST)."""
    try:
        from backend.core.meta_api_client import meta_api_singleton
        
        # 1. Get User Accounts
        doc = FIRESTORE_DB.collection("users").document(user_id).get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail="User not found")
            
        data = doc.to_dict()
        accounts = data.get("mt5_accounts", [])
        
        results = []
        for acc in accounts:
            acc_id = acc.get("id")
            if acc_id:
                try:
                    await meta_api_singleton.deploy_account_rest(acc_id)
                    results.append({"id": acc_id, "status": "deployed"})
                except Exception as ex:
                    logger.error(f"Deploy failed for {acc_id}: {ex}")
                    results.append({"id": acc_id, "status": "failed", "error": str(ex)})

        return {"success": True, "results": results}
    except Exception as e:
        logger.error(f"Manual deploy error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/users/{user_id}/undeploy")
async def manual_undeploy_user(
    user_id: str, user: dict = Depends(require_admin)
):
    """Manually undeploy (pause) all accounts for a user (Synchronous REST)."""
    try:
        from backend.core.meta_api_client import meta_api_singleton
        
        # 1. Get User Accounts
        doc = FIRESTORE_DB.collection("users").document(user_id).get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail="User not found")
            
        data = doc.to_dict()
        accounts = data.get("mt5_accounts", [])
        
        results = []
        for acc in accounts:
            acc_id = acc.get("id")
            if acc_id:
                try:
                    await meta_api_singleton.undeploy_account_rest(acc_id)
                    results.append({"id": acc_id, "status": "undeployed"})
                except Exception as ex:
                    logger.error(f"Undeploy failed for {acc_id}: {ex}")
                    results.append({"id": acc_id, "status": "failed", "error": str(ex)})

        return {"success": True, "results": results}
    except Exception as e:
        logger.error(f"Manual undeploy error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/users/{user_id}/suspend")
async def toggle_user_suspension(
    user_id: str, user: dict = Depends(require_admin)
):
    """Toggle user suspension status."""
    try:
        user_ref = FIRESTORE_DB.collection("users").document(user_id)
        doc = user_ref.get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail="User not found")
        
        current = doc.to_dict().get("suspended", False)
        user_ref.update({"suspended": not current})
        logger.info(f"Admin {user['uid']} {'suspended' if not current else 'unsuspended'} user {user_id}")
        return {"success": True, "suspended": not current}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Suspend error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/users/{user_id}/notes")
async def save_user_notes(
    user_id: str, body: dict, user: dict = Depends(require_admin)
):
    """Save admin notes and tags for a user."""
    try:
        user_ref = FIRESTORE_DB.collection("users").document(user_id)
        doc = user_ref.get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail="User not found")
        
        update = {}
        if "adminNotes" in body:
            update["adminNotes"] = body["adminNotes"]
        if "tags" in body:
            update["tags"] = body["tags"]
        if update:
            user_ref.update(update)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Notes save error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/users/bulk/credits")
async def bulk_adjust_credits(
    body: dict, user: dict = Depends(require_admin)
):
    """Bulk adjust credits for multiple users."""
    try:
        user_ids = body.get("user_ids", [])
        amount = body.get("amount", 0)
        reason = body.get("reason", "Bulk admin adjustment")
        results = []
        for uid in user_ids:
            try:
                user_ref = FIRESTORE_DB.collection("users").document(uid)
                doc = user_ref.get()
                if doc.exists:
                    current = doc.to_dict().get("credits", 0)
                    new_credits = max(0, current + amount)
                    user_ref.update({"credits": new_credits})
                    user_ref.collection("credit_history").add({
                        "amount": amount, "reason": reason,
                        "admin": user.get("email", user["uid"]),
                        "balance_after": new_credits, "timestamp": datetime.utcnow(),
                    })
                    results.append({"uid": uid, "success": True, "new_credits": new_credits})
            except Exception as e:
                results.append({"uid": uid, "success": False, "error": str(e)})
        return {"success": True, "results": results}
    except Exception as e:
        logger.error(f"Bulk credits error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/users/bulk/tier")
async def bulk_change_tier(
    body: dict, user: dict = Depends(require_admin)
):
    """Bulk change tier for multiple users."""
    try:
        user_ids = body.get("user_ids", [])
        tier = body.get("tier", "free")
        for uid in user_ids:
            try:
                FIRESTORE_DB.collection("users").document(uid).update({"tier": tier})
            except Exception as e:
                logger.error(f"Bulk tier change error for {uid}: {e}")
        return {"success": True, "updated": len(user_ids), "tier": tier}
    except Exception as e:
        logger.error(f"Bulk tier error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/metaapi/settings")
async def update_metaapi_settings(
    settings: dict,
    user: dict = Depends(require_admin)
):
    """
    Update MetaAPI billing settings.
    Now supports hourly_rate for precise G2 infrastructure tracking.
    Default G2 High Reliability = 0.0126/hr.
    """
    try:
        # Validate input
        balance = settings.get("balance")
        # Default to $0.0126 (G2 High Reliability London) if not provided
        hourly_rate = settings.get("hourly_rate", 0.0126) 
        
        if balance is None:
            raise HTTPException(status_code=400, detail="Balance is required")

        data = {
            "balance": float(balance),
            "hourly_rate": float(hourly_rate),
            "last_updated": datetime.utcnow().isoformat(),
            "updated_by": user["uid"]
        }
        
        # Store in Firestore
        FIRESTORE_DB.collection("settings").document("metaapi").set(data)
        
        return {"success": True, "data": data}
        
    except Exception as e:
        logger.error(f"MetaAPI settings update error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/metaapi/billing")
async def get_metaapi_billing(user: dict = Depends(require_admin)):
    """
    Get MetaAPI billing information.
    Uses precise hourly calculation: 
    Balance - (ActiveAccounts * HourlyRate * HoursElapsed)
    """
    try:
        from backend.core.meta_api_client import meta_api_singleton
        
        # 1. Get Real-time Trading Info
        real_info = await meta_api_singleton.get_billing_info()
        
        # 2. Get Service Credit Settings
        doc = FIRESTORE_DB.collection("settings").document("metaapi").get()
        settings = doc.to_dict() if doc.exists else {}
        
        stored_balance = settings.get("balance", 0.0)
        # Default to 0.0126 if not set (fallback to old cost_per_account logic if needed, but prefer new)
        hourly_rate = settings.get("hourly_rate")
        
        # Migration from old "cost_per_account" if hourly_rate is missing
        if hourly_rate is None and settings.get("cost_per_account"):
             # Convert monthly to hourly roughly: Cost / 720
             hourly_rate = settings.get("cost_per_account") / 720.0
        elif hourly_rate is None:
             hourly_rate = 0.0126

        last_updated = settings.get("last_updated")
        
        # Calculate Service Usage since last update
        # For simplicity, we can do a real-time estimate or just return stored
        # But user wants to see degradation of balance.
        # Let's trust the frontend or a scheduled task to update the stored balance?
        # Or calculate explicitly here:
        estimated_balance = stored_balance
        
        if last_updated:
            try:
                last_dt = datetime.fromisoformat(last_updated)
                now = datetime.utcnow()
                hours_diff = (now - last_dt).total_seconds() / 3600.0
                
                # We need active accounts count over time... hard to do exactly without timeseries
                # Assume current active accounts apply to the window (approximation)
                active_count = real_info.get("activeAccounts", 0)
                
                cost = active_count * hourly_rate * hours_diff
                estimated_balance = stored_balance - cost
            except:
                pass
        
        return {
            "success": True,
            "trading": {
                "balance": real_info.get("balance"),
                "equity": real_info.get("equity"),
                "credit": real_info.get("credit"),
                "active_accounts": real_info.get("activeAccounts")
            },
            "service": {
                "estimated_balance": estimated_balance,
                "hourly_rate": hourly_rate,
                "monthly_run_rate": hourly_rate * 720 * real_info.get("activeAccounts", 0),
                "last_updated": last_updated
            }
        }
        
    except Exception as e:
        logger.error(f"Billing info error: {e}")
        return {"success": False, "error": str(e)}
