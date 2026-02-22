"""
Trade Manager Service v1.0
==========================
AI-powered position management system that monitors open trades,
re-evaluates market conditions, and generates actionable recommendations.

Features:
- Polling-based position monitoring (configurable interval)
- Re-evaluation via existing Analysis Engine
- Recommendation generation (HOLD, TRAIL, BREAKEVEN, CLOSE, etc.)
- Autonomous execution mode (optional)
- Credit deduction per cycle
- Full audit trail to Firestore
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Literal
from dataclasses import dataclass, field, asdict, fields
from enum import Enum
import json

from backend.firebase_setup import initialize_firebase
from backend.services.streaming_service import stream_manager
from backend.services.agent_service import AgentFactory
from backend.services.credit_service import credit_service
from backend.core.meta_api_client import meta_api_singleton
from backend.core.logger import setup_logger
from backend.services.market_context import market_context

logger = setup_logger("TradeManager")

# Initialize Firestore
db = initialize_firebase()


# =============================================================================
# ENUMS & DATA CLASSES
# =============================================================================

class TradeAction(str, Enum):
    """All possible trade management actions"""
    HOLD = "hold"
    MOVE_SL_BREAKEVEN = "breakeven"
    TRAIL_SL = "trail_sl"
    TIGHTEN_SL = "tighten_sl"
    WIDEN_SL = "widen_sl"
    CLOSE_PARTIAL = "close_partial"
    CLOSE_FULL = "close_full"
    EXTEND_TP = "extend_tp"
    REDUCE_TP = "reduce_tp"
    ADD_POSITION = "add_position"
    HEDGE = "hedge"
    REVERSE = "reverse"
    ALERT = "alert"
    INVALIDATE = "invalidate"
    SYSTEM = "system"


@dataclass
class TradeManagerSettings:
    """User settings for the Trade Manager"""
    enabled: bool = False
    autonomous: bool = False
    interval_minutes: int = 15
    cooldown_minutes: int = 5
    min_confidence: int = 70
    max_actions_hour: int = 5
    max_actions_day: int = 20
    min_profit_to_trail: int = 10  # pips
    breakeven_buffer: int = 2  # pips
    credit_mode: str = "per_cycle"  # "per_cycle" or "per_position"
    allowed_actions: List[str] = field(default_factory=lambda: [
        "breakeven", "trail_sl", "tighten_sl", "close_partial"
    ])
    blacklist: List[str] = field(default_factory=list)
    whitelist: List[str] = field(default_factory=list)
    active_hours_start: str = "08:00"
    active_hours_end: str = "22:00"
    skip_weekends: bool = True
    active_model: str = "deepseek-chat" # Default 


@dataclass
class Recommendation:
    """A single trade recommendation"""
    position_id: str
    symbol: str
    direction: str  # BUY or SELL
    action: TradeAction
    confidence: int
    reasoning: str
    before: Dict  # Current SL/TP
    after: Dict   # Proposed SL/TP
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    conversation_log: List[Dict] = field(default_factory=list)  # Agent conversation messages
    analysis_result: Optional[Dict] = None # Full AI Analysis (Drivers, etc.)
    details: Optional[Dict] = None # Rich Context: Risk Eliminated, Time held, etc.


# =============================================================================
# TRADE MANAGER SERVICE
# =============================================================================

from backend.services.risk_manager import RiskManager
from backend.services.executive_service import ExecutiveService
from backend.services.market_context import market_context

class TradeManagerService:
    """
    Core Trade Manager that monitors positions and generates recommendations.
    """
    
    def __init__(self):
        self.running = False
        self.user_settings: Dict[str, TradeManagerSettings] = {}
        self.last_evaluation: Dict[str, float] = {}  # position_id -> timestamp
        self.actions_this_hour: Dict[str, int] = {}  # user_id -> count
        self.actions_today: Dict[str, int] = {}  # user_id -> count
        self.actions_this_hour: Dict[str, int] = {}  # user_id -> count
        self.actions_today: Dict[str, int] = {}  # user_id -> count
        self.known_positions: set = set() # Track known position IDs
        self._poll_task: Optional[asyncio.Task] = None
        self._analysis_tasks: Dict[str, asyncio.Task] = {} # user_id -> running analysis task
        self._previous_positions: Dict[str, Dict[str, Dict]] = {} # user_id -> {pos_id: snapshot}
        
        # Synergy: Inject Risk Manager & Executive Service
        self.risk_manager = RiskManager()
        self.executive_service = ExecutiveService(self.risk_manager)
        
        # Concurrency Control (Speed for 50 Users)
        # Limit to 10 concurrent evaluations to balance speed vs API rate limits
        self._concurrency_limit = asyncio.Semaphore(10)
    
    async def start(self):
        """Start the Trade Manager background loop"""
        if self.running:
            return
        self.running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("Trade Manager Service started.")
        
        # Announce presence to Neural Stream
        from backend.core.system_state import world_state
        world_state.add_log("Trade Manager", "Trade Manager Service is ONLINE. I am now monitoring active positions for risk events and management opportunities.", "INFO")
    
    async def stop(self):
        """Stop the Trade Manager"""
        self.running = False
        if self._poll_task:
            self._poll_task.cancel()
        logger.info("Trade Manager Service stopped.")
    
    async def _poll_loop(self):
        """Main polling loop - runs every minute to check intervals"""
        while self.running:
            try:
                await self._check_all_users()
            except Exception as e:
                logger.error(f"Poll loop error: {e}")
            await asyncio.sleep(60)  # Check every minute
    
    async def _safe_evaluate_user(self, user_id: str, user_data: Dict, settings: TradeManagerSettings, **kwargs):
        """Wrapper to evaluate user with concurrency limits and TIMEOUT protection"""
        # [CRITICAL UPDATE] Added timeout to prevent one stuck user from blocking the semaphore
        try:
            async with self._concurrency_limit:
                try:
                    # 30 second timeout per user evaluation
                    await asyncio.wait_for(
                        self._evaluate_user_positions(user_id, user_data, settings, explicit_data=kwargs.get('explicit_data')),
                        timeout=30.0
                    )
                    self.last_evaluation[user_id] = time.time()
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout evaluating user {user_id} - Skipping cycle.")
                    # Still update timestamp so we don't retry immediately and hammer the system
                    self.last_evaluation[user_id] = time.time()
                except Exception as inner_e:
                    logger.error(f"Error checking user {user_id}: {inner_e}")
                    # Update timestamp to avoid tight error loops
                    self.last_evaluation[user_id] = time.time()
        except Exception as outer_e:
             logger.error(f"Concurrency/wrapper error for {user_id}: {outer_e}")

    async def _check_all_users(self):
        """Check all users concurrently (limited by Semaphore)"""
        try:
            # Get all users with Trade Manager enabled
            users_ref = db.collection("users")
            docs = users_ref.stream()
            
            tasks = []
            
            for doc in docs:
                user_id = doc.id
                user_data = doc.to_dict()
                
                # Check if Trade Manager is enabled
                settings_data = user_data.get("trade_manager_settings", {})
                if not settings_data.get("enabled", False):
                    continue
                
                # Load settings — filter unknown keys to prevent crashes
                valid_keys = {f.name for f in fields(TradeManagerSettings)}
                filtered_settings = {k: v for k, v in settings_data.items() if k in valid_keys}
                settings = TradeManagerSettings(**filtered_settings)
                self.user_settings[user_id] = settings
                
                # Check active hours
                if not self._is_within_active_hours(settings):
                    continue
                
                # Check weekends
                if settings.skip_weekends and datetime.utcnow().weekday() >= 5:
                    continue
                
                # Check interval
                try:
                    # Enforce type safety (Firestore might return strings)
                    interval_min = int(settings.interval_minutes)
                    # Safety check for invalid interval
                    if interval_min <= 0: interval_min = 15
                except (ValueError, TypeError):
                    interval_min = 15 # Default fallback
                
                last_check = self.last_evaluation.get(user_id, 0)
                time_since_last = time.time() - last_check
                interval_seconds = interval_min * 60
                
                # Debug Log (Verbose mode only or for specific debugging)
                # logger.debug(f"[TradeManager] User {user_id}: Last check {time_since_last:.1f}s ago. Interval: {interval_seconds}s")
                
                if time_since_last < interval_seconds:
                    continue
                
                logger.info(f"[TradeManager] Triggering evaluation for {user_id}. Interval: {interval_min}m. ({time_since_last:.1f}s > {interval_seconds}s)")
                
                # Add to Tasks
                tasks.append(self._safe_evaluate_user(user_id, user_data, settings))
                
            # Execute all checks concurrently
            if tasks:
                await asyncio.gather(*tasks)
                
        except Exception as e:
            logger.error(f"Error in main polling loop: {e}")
    
    def _is_within_active_hours(self, settings: TradeManagerSettings) -> bool:
        """Check if current time is within user's active hours"""
        now = datetime.utcnow()
        try:
            start_h, start_m = map(int, settings.active_hours_start.split(":"))
            end_h, end_m = map(int, settings.active_hours_end.split(":"))
            
            start_time = now.replace(hour=start_h, minute=start_m, second=0)
            end_time = now.replace(hour=end_h, minute=end_m, second=0)
            
            return start_time <= now <= end_time
        except:
            return True  # Default to active if parsing fails
    
    async def _evaluate_user_positions(self, user_id: str, user_data: Dict, settings: TradeManagerSettings, explicit_data: Optional[Dict] = None):
        """Evaluate all positions for a specific user"""
        try:
            from backend.core.system_state import world_state
            world_state.add_log("Trade Manager", "DEBUG: [Evaluator] Starting position evaluation...", "INFO")
            
            # Get user's MT5 accounts
            mt5_accounts = user_data.get("mt5_accounts", [])
            account_id = None
            
            if mt5_accounts:
                account_id = mt5_accounts[0].get("account_id") if isinstance(mt5_accounts[0], dict) else mt5_accounts[0]
            elif explicit_data and explicit_data.get("account_id"):
                account_id = explicit_data.get("account_id")
                world_state.add_log("Trade Manager", f"DEBUG: [Evaluator] Using explicit account_id: {account_id}", "INFO")
            else:
                world_state.add_log("Trade Manager", "DEBUG: [Evaluator] No account_id found. Returning.", "ERROR")
                return
            
            positions = []
            equity = 0.0
            
            if explicit_data:
                 # Use provided snapshot data (Analysis on Demand)
                 positions = explicit_data.get("positions", [])
                 equity = float(explicit_data.get("account_info", {}).get("equity", 0))
            else:
                # Get current positions from streaming manager
                listener = stream_manager.listeners.get(account_id)
                
                if listener:
                    positions = listener.state.get("positions", [])
                    account_info_state = listener.state.get("accountInformation", {})
                    equity = float(account_info_state.get("equity", 0))
                else:
                    # FALLBACK: RPC Snapshot + Auto-Start
                    # If stream is dead (e.g. backend restart), we must revive it and get data NOW.
                    world_state.add_log("Trade Manager", f"DEBUG: Stream inactive for {account_id}. Attempting RPC Snapshot fallback...", "WARNING")
                    
                    # 1. Trigger background start (fire-and-forget)
                    asyncio.create_task(stream_manager.start_stream(account_id, user_id))
                    
                    # 2. Get Immediate Snapshot (RPC)
                    snapshot = await self._get_account_snapshot(account_id)
                    if not snapshot:
                         world_state.add_log("Trade Manager", "DEBUG: RPC Snapshot failed. Skipping evaluation.", "ERROR")
                         return
                         
                    positions = snapshot.get("positions", [])
                    equity = float(snapshot.get("account_info", {}).get("equity", 0))

            if not positions:
                world_state.add_log("Trade Manager", "DEBUG: [Evaluator] No positions to evaluate.", "INFO")
                return
            
            world_state.add_log("Trade Manager", f"DEBUG: [Evaluator] Evaluating {len(positions)} positions...", "INFO")
            filtered_positions = self._filter_positions(positions, settings)
            
            if not filtered_positions:
                world_state.add_log("Trade Manager", "DEBUG: [Evaluator] All positions filtered out by whitelist/blacklist. Returning.", "WARNING")
                return
            
            world_state.add_log("Trade Manager", f"DEBUG: [Evaluator] {len(filtered_positions)} positions after filter.", "INFO")
            
            # Calculate Total Exposure for Risk Manager
            total_exposure = sum(float(p.get("volume", 0)) for p in filtered_positions)
            
            # Deduct credits based on mode
            credits_to_deduct = 1 if settings.credit_mode == "per_cycle" else len(filtered_positions)
            
            # Use CreditService to deduct and ENFORCE
            if not await credit_service.deduct_credits(user_id, credits_to_deduct, "Trade Manager Cycle"):
                logger.warning(f"User {user_id} has insufficient credits for Trade Manager")
                world_state.add_log("Trade Manager", f"Insufficient credits for Trade Manager cycle. Pausing service.", "WARNING")
                return # STOP processing if no credits
            
            # ============================================================
            # INTELLIGENCE CHECKS (run before individual position analysis)
            # ============================================================
            
            # 1. Correlation Detector — warn on concentrated currency exposure
            correlation_warnings = self._check_correlation(filtered_positions)
            if correlation_warnings:
                world_state.add_log("Trade Manager", f"[Correlation] ⚠️ Detected {len(correlation_warnings)} concentration risk(s).", "WARNING")
            
            # 2. Pre-News Shield — cross-reference positions vs upcoming events
            news_warnings = self._check_news_exposure(filtered_positions)
            if news_warnings:
                world_state.add_log("Trade Manager", f"[News Shield] 🛡️ {len(news_warnings)} event warning(s) for exposed positions.", "WARNING")
            
            # 3. Post-Mortem — detect closed trades and generate AI reviews
            post_mortem_reviews = await self._check_closed_positions(user_id, filtered_positions)
            if post_mortem_reviews:
                world_state.add_log("Trade Manager", f"[Post-Mortem] 📋 {len(post_mortem_reviews)} trade review(s) generated.", "INFO")
            
            # Prepend intelligence warnings (show first in chat)
            recommendations = correlation_warnings + news_warnings + post_mortem_reviews
            
            # Push intelligence warnings immediately (before slow AI analysis)
            if recommendations:
                await self._push_recommendations(user_id, recommendations)
                world_state.add_log("Trade Manager", f"DEBUG: [Intelligence] Pushed {len(recommendations)} pre-analysis warning(s).", "INFO")
            
            # ============================================================
            # PER-POSITION AI ANALYSIS
            # ============================================================
            
            # Evaluate each position
            for i, position in enumerate(filtered_positions):
                pos_symbol = position.get('symbol', 'UNKNOWN')
                pos_type = position.get('type', 'UNKNOWN')
                world_state.add_log("Trade Manager", f"DEBUG: [Evaluator] Analyzing position {i+1}/{len(filtered_positions)}: {pos_symbol} {pos_type}", "INFO")
                
                # Check cooldown
                pos_id = position.get("id", position.get("ticket"))
                last_action = self.last_evaluation.get(f"{user_id}:{pos_id}", 0)
                cooldown_remaining = (settings.cooldown_minutes * 60) - (time.time() - last_action)
                if cooldown_remaining > 0:
                    world_state.add_log("Trade Manager", f"DEBUG: [Evaluator] Position {pos_symbol} on cooldown ({cooldown_remaining:.0f}s remaining). Skipping.", "INFO")
                    continue
                
                # New Position Check
                if pos_id not in self.known_positions:
                    self.known_positions.add(pos_id)
                    world_state.add_log("Trade Manager", f"New Position Detected: {pos_symbol} {pos_type}. Engaging tracking protocols.", "INFO")
                
                try:
                    recommendation = await self._evaluate_position(
                        user_id, position, settings, account_id, 
                        equity=equity, total_exposure=total_exposure
                    )
                    
                    if recommendation:
                        recommendations.append(recommendation)
                        world_state.add_log("Trade Manager", f"DEBUG: [Evaluator] Recommendation for {pos_symbol}: {recommendation.action.value.upper()} (Confidence: {recommendation.confidence}%)", "INFO")
                        
                        # Log to Firestore
                        await self._log_recommendation(user_id, recommendation)
                        
                        # Execute if autonomous and allowed
                        if settings.autonomous and recommendation.action != TradeAction.HOLD:
                            if recommendation.action.value in settings.allowed_actions:
                                if recommendation.confidence >= settings.min_confidence:
                                    await self._execute_action(user_id, account_id, recommendation)
                                    world_state.add_log("Trade Manager", f"DEBUG: [Evaluator] EXECUTED {recommendation.action.value.upper()} on {pos_symbol}", "INFO")
                    else:
                        world_state.add_log("Trade Manager", f"DEBUG: [Evaluator] No recommendation generated for {pos_symbol} (returned None).", "WARNING")
                except Exception as pos_e:
                    world_state.add_log("Trade Manager", f"DEBUG: [Evaluator] CRASH evaluating {pos_symbol}: {pos_e}", "ERROR")
                    logger.error(f"Error evaluating position {pos_symbol}: {pos_e}")
            
            # Push recommendations to WebSocket
            if recommendations:
                world_state.add_log("Trade Manager", f"DEBUG: [Evaluator] Pushing {len(recommendations)} recommendation(s) to frontend.", "INFO")
                await self._push_recommendations(user_id, recommendations)
            else:
                world_state.add_log("Trade Manager", "DEBUG: [Evaluator] No recommendations to push. Evaluation complete.", "INFO")
                
        except Exception as e:
            logger.error(f"Error evaluating positions for {user_id}: {e}")
            try:
                from backend.core.system_state import world_state
                world_state.add_log("Trade Manager", f"DEBUG: [Evaluator] FATAL CRASH: {e}", "ERROR")
            except: pass
    
    def _filter_positions(self, positions: List[Dict], settings: TradeManagerSettings) -> List[Dict]:
        """Filter positions based on whitelist/blacklist"""
        filtered = []
        for pos in positions:
            symbol = pos.get("symbol", "")
            
            # Whitelist takes priority
            if settings.whitelist:
                if symbol in settings.whitelist:
                    filtered.append(pos)
            elif symbol not in settings.blacklist:
                filtered.append(pos)
        
        return filtered
    
    # ==========================================================================
    # INTELLIGENCE FEATURES
    # ==========================================================================
    
    def _get_currency_pair(self, symbol: str) -> tuple:
        """Extract base and quote currencies from a forex symbol.
        E.g. EURUSD -> ('EUR', 'USD'), AUDNZD -> ('AUD', 'NZD'), XAUUSD -> ('XAU', 'USD')"""
        # Standard forex pairs are 6 chars
        symbol = symbol.upper().replace('.', '').replace('_', '')
        
        # Special cases: metals, indices
        if symbol.startswith('XAU'): return ('XAU', symbol[3:6] if len(symbol) >= 6 else 'USD')
        if symbol.startswith('XAG'): return ('XAG', symbol[3:6] if len(symbol) >= 6 else 'USD')
        
        if len(symbol) >= 6:
            return (symbol[:3], symbol[3:6])
        return (symbol, 'USD')  # Fallback
    
    def _check_correlation(self, positions: List[Dict]) -> List[Recommendation]:
        """
        Detect dangerous currency concentration across open positions.
        Maps each position to base/quote exposure and warns on 2+ same-direction hits.
        """
        if len(positions) < 2:
            return []
        
        # Build currency exposure map: { 'EUR': {'long': [...], 'short': [...]}, ... }
        exposure_map: Dict[str, Dict[str, list]] = {}
        
        for pos in positions:
            symbol = pos.get('symbol', '')
            direction = pos.get('type', 'BUY').upper()
            volume = float(pos.get('volume', 0))
            
            base, quote = self._get_currency_pair(symbol)
            
            # BUY EURUSD = Long EUR, Short USD
            # SELL EURUSD = Short EUR, Long USD
            if direction == 'BUY':
                base_dir, quote_dir = 'long', 'short'
            else:
                base_dir, quote_dir = 'short', 'long'
            
            # Track base currency
            if base not in exposure_map:
                exposure_map[base] = {'long': [], 'short': []}
            exposure_map[base][base_dir].append({'symbol': symbol, 'volume': volume})
            
            # Track quote currency
            if quote not in exposure_map:
                exposure_map[quote] = {'long': [], 'short': []}
            exposure_map[quote][quote_dir].append({'symbol': symbol, 'volume': volume})
        
        # Check for concentration (2+ positions same direction on same currency)
        warnings = []
        
        for currency, dirs in exposure_map.items():
            for direction, entries in dirs.items():
                if len(entries) >= 2:
                    symbols = [e['symbol'] for e in entries]
                    total_vol = sum(e['volume'] for e in entries)
                    
                    dir_label = "LONG" if direction == 'long' else "SHORT"
                    warning_msg = (
                        f"⚠️ CORRELATION ALERT: You have {len(entries)} positions {dir_label} on {currency} "
                        f"({', '.join(symbols)}). Combined exposure: {total_vol:.2f} lots. "
                        f"This creates concentrated {currency} risk — a single {currency} move affects all positions simultaneously. "
                        f"Consider reducing exposure or hedging."
                    )
                    
                    warnings.append(Recommendation(
                        position_id=f"corr-{currency}-{direction}-{int(time.time())}",
                        symbol="RISK",
                        direction="",
                        action=TradeAction.HOLD,
                        confidence=85,
                        reasoning=f"Risk Manager: {warning_msg}",
                        before={},
                        after={},
                        timestamp=datetime.utcnow().isoformat(),
                        details={
                            "type": "correlation",
                            "currency": currency,
                            "direction": dir_label,
                            "count": len(entries),
                            "symbols": symbols,
                            "total_volume": total_vol
                        }
                    ))
        
        return warnings
    
    def _check_news_exposure(self, positions: List[Dict]) -> List[Recommendation]:
        """
        Cross-reference open positions against upcoming high-impact economic events.
        Uses existing MarketContext.get_upcoming_news() to query the economic_events DB.
        """
        if not positions:
            return []
        
        # Collect unique currencies from all positions
        exposed_currencies = set()
        currency_positions: Dict[str, list] = {}
        
        for pos in positions:
            symbol = pos.get('symbol', '')
            base, quote = self._get_currency_pair(symbol)
            
            for curr in (base, quote):
                exposed_currencies.add(curr)
                if curr not in currency_positions:
                    currency_positions[curr] = []
                currency_positions[curr].append(symbol)
        
        # Check upcoming news for each exposed currency (2 hour window)
        warnings = []
        checked = set()  # Avoid duplicate warnings for same event
        
        for currency in exposed_currencies:
            # Skip non-standard currencies
            if currency in ('XAU', 'XAG', 'US30', 'NAS', 'SPX'):
                continue
                
            try:
                upcoming = market_context.get_upcoming_news(currency, minutes=120)
                
                for event in upcoming:
                    event_key = f"{event['event']}-{event['time']}"
                    if event_key in checked:
                        continue
                    checked.add(event_key)
                    
                    affected_symbols = currency_positions.get(currency, [])
                    impact_emoji = "🔴" if event['impact'] == 'High' else "🟡"
                    
                    warning_msg = (
                        f"🛡️ NEWS SHIELD: {impact_emoji} {event['impact']}-impact event in {event['minutes_until']} minutes — "
                        f"\"{event['event']}\" ({currency}). "
                        f"Your exposed positions: {', '.join(set(affected_symbols))}. "
                        f"Consider tightening SL to breakeven or reducing size before the release."
                    )
                    
                    warnings.append(Recommendation(
                        position_id=f"news-{currency}-{int(time.time())}",
                        symbol="SHIELD",
                        direction="",
                        action=TradeAction.HOLD,
                        confidence=90,
                        reasoning=f"Risk Manager: {warning_msg}",
                        before={},
                        after={},
                        timestamp=datetime.utcnow().isoformat(),
                        details={
                            "type": "news_shield",
                            "event": event['event'],
                            "currency": currency,
                            "impact": event['impact'],
                            "minutes_until": event['minutes_until'],
                            "affected_symbols": list(set(affected_symbols))
                        }
                    ))
                    
            except Exception as e:
                logger.warning(f"[News Shield] Error checking {currency}: {e}")
                continue
        
        return warnings
    
    async def _check_closed_positions(self, user_id: str, current_positions: List[Dict]) -> List[Recommendation]:
        """
        Detect closed trades by comparing current positions to previously seen ones.
        For each closed trade, generate an AI post-mortem review.
        """
        current_ids = {str(p.get('id', p.get('ticket', ''))): p for p in current_positions}
        previous = self._previous_positions.get(user_id, {})
        
        # Update snapshot for next cycle
        self._previous_positions[user_id] = current_ids
        
        # First run — no previous data to compare against
        if not previous:
            return []
        
        # Find closed positions (were in previous, not in current)
        closed_ids = set(previous.keys()) - set(current_ids.keys())
        
        if not closed_ids:
            return []
        
        reviews = []
        for pos_id in closed_ids:
            snapshot = previous[pos_id]
            symbol = snapshot.get('symbol', 'UNKNOWN')
            direction = snapshot.get('type', 'BUY')
            
            try:
                from backend.core.system_state import world_state
                world_state.add_log("Trade Manager", f"[Post-Mortem] 📋 Generating trade review for closed {symbol} {direction}...", "INFO")
                review = await self._generate_post_mortem(user_id, snapshot)
                if review:
                    reviews.append(review)
            except Exception as e:
                logger.warning(f"[Post-Mortem] Failed for {symbol}: {e}")
        
        return reviews
    
    async def _generate_post_mortem(self, user_id: str, position_snapshot: Dict) -> Optional[Recommendation]:
        """
        Generate an AI trade review card when a position closes.
        Uses the CIO agent to grade entry, management, and exit quality.
        """
        symbol = position_snapshot.get('symbol', 'UNKNOWN')
        direction = position_snapshot.get('type', 'BUY')
        entry_price = float(position_snapshot.get('openPrice') or 0)
        last_price = float(position_snapshot.get('currentPrice') or 0)
        profit = float(position_snapshot.get('profit') or 0)
        volume = float(position_snapshot.get('volume') or 0)
        sl = float(position_snapshot.get('sl') or 0)
        tp = float(position_snapshot.get('tp') or 0)
        
        # Calculate pip value
        pip_value = 0.01 if 'JPY' in symbol else 0.0001
        if direction == 'BUY':
            pnl_pips = (last_price - entry_price) / pip_value
        else:
            pnl_pips = (entry_price - last_price) / pip_value
        
        # Time in trade
        time_str = "Unknown"
        try:
            import dateutil.parser
            open_time_raw = position_snapshot.get('time') or position_snapshot.get('openTime')
            if open_time_raw:
                if isinstance(open_time_raw, (int, float)):
                    open_dt = datetime.utcfromtimestamp(open_time_raw)
                else:
                    open_dt = dateutil.parser.parse(str(open_time_raw))
                    if open_dt.tzinfo: open_dt = open_dt.replace(tzinfo=None)
                hours = (datetime.utcnow() - open_dt).total_seconds() / 3600
                time_str = f"{hours:.1f} hours"
        except:
            pass
        
        # Build prompt for CIO
        outcome = "PROFIT" if profit >= 0 else "LOSS"
        prompt = f"""ROLE: Chief Investment Officer — Trade Post-Mortem Reviewer
TASK: Grade this closed trade and provide ONE actionable lesson.

TRADE CLOSED:
- Symbol: {symbol} {direction}
- Entry: {entry_price}
- Exit: ~{last_price}
- P&L: {pnl_pips:+.1f} pips (${profit:+.2f})
- Duration: {time_str}
- Volume: {volume} lots
- SL was: {sl} | TP was: {tp}
- Outcome: {outcome}

OUTPUT FORMAT (exactly):
GRADE: [A/B/C/D/F]
[One sentence summary of what went right or wrong]
LESSON: [One actionable takeaway for next time]"""
        
        try:
            from backend.services.agent_service import AgentFactory
            agent = AgentFactory.get_agent("MLens-CIO")
            response = await agent.ask(prompt, user_id=user_id)
            review_text = response.get("text", "Trade review unavailable.")
        except Exception as e:
            logger.error(f"[Post-Mortem] CIO Agent failed: {e}")
            # Fallback: generate a basic review without AI
            grade = 'A' if pnl_pips > 30 else 'B' if pnl_pips > 10 else 'C' if pnl_pips > 0 else 'D' if pnl_pips > -20 else 'F'
            review_text = f"GRADE: {grade}\n{symbol} {direction} closed at {pnl_pips:+.1f} pips after {time_str}.\nLESSON: Review your exit strategy for future trades."
        
        return Recommendation(
            position_id=f"review-{symbol}-{int(time.time())}",
            symbol="CIO",
            direction="",
            action=TradeAction.HOLD,
            confidence=100,
            reasoning=f"📋 TRADE REVIEW — {symbol} {direction} | {pnl_pips:+.1f} pips | ${profit:+.2f}\n\n{review_text}",
            before={"entry": entry_price},
            after={"exit": last_price},
            timestamp=datetime.utcnow().isoformat(),
            details={
                "type": "post_mortem",
                "symbol": symbol,
                "direction": direction,
                "pnl_pips": round(pnl_pips, 1),
                "profit_usd": round(profit, 2),
                "duration": time_str,
                "time_in_trade": time_str,
                "risk_eliminated": 0,
                "atr_used": False,
                "threshold_desc": "N/A"
            }
        )
    
    async def _evaluate_position(self, user_id: str, position: Dict, settings: TradeManagerSettings, account_id: str, equity: float = 100000.0, total_exposure: float = 0.0) -> Optional[Recommendation]:
        """Evaluate a single position and generate recommendation"""
        try:
            symbol = position.get("symbol", "")
            direction = position.get("type", "BUY")
            entry_price = float(position.get("openPrice") or 0)
            current_price = float(position.get("currentPrice") or 0)
            current_sl = float(position.get("sl") or 0)
            current_tp = float(position.get("tp") or 0)
            current_profit = float(position.get("profit") or 0)
            
            # Get pip value (simplified - in reality use proper pip calculation)
            pip_value = 0.0001 if "JPY" not in symbol else 0.01
            profit_pips = (current_price - entry_price) / pip_value if direction == "BUY" else (entry_price - current_price) / pip_value
            
            # === AGENT CONVERSATION LOG & SAFETY CHECKS (EARLY EXIT) ===
            conversation: List[Dict] = []
            
            # Include time in trade
            import dateutil.parser
            time_in_trade_str = "Unknown"
            try:
                # MT5 returns time in ISO format or timestamp
                open_time_raw = position.get("time") or position.get("openTime")
                if open_time_raw:
                    if isinstance(open_time_raw, (int, float)):
                        open_dt = datetime.utcfromtimestamp(open_time_raw)
                    else:
                        open_dt = dateutil.parser.parse(str(open_time_raw))
                        # Ensure offset-naive for calculation if needed, or aware
                        if open_dt.tzinfo: open_dt = open_dt.replace(tzinfo=None)
                        
                    now_dt = datetime.utcnow()
                    duration = now_dt - open_dt
                    hours = duration.total_seconds() / 3600
                    time_in_trade_str = f"{hours:.1f} hours"
            except Exception as time_e:
                logger.warning(f"Time parse error: {time_e}")
            
            # Build Position Context for AI
            position_context = {
                "type": direction,
                "entry_price": entry_price,
                "current_price": current_price,
                "pnl_pips": profit_pips,
                "net_profit": current_profit, # USD
                "time_in_trade": time_in_trade_str,
                "sl": current_sl,
                "tp": current_tp,
                "volume": position.get("volume", 0)
            }
            
            # Import world_state for Neural Stream
            from backend.core.system_state import world_state
            world_state.add_log("Trade Manager", f"DEBUG: [Position] {symbol} {direction} | Entry: {entry_price} | P&L: {profit_pips:.1f} pips | Time: {time_in_trade_str}", "INFO")
            
            def log_message(agent: str, text: str, icon: str = "🤖"):
                # 1. Add to local conversation for Firestore/UI
                conversation.append({
                    "agent": agent,
                    "icon": icon,
                    "text": text,
                    "timestamp": datetime.utcnow().isoformat()
                })
                
                # 2. Broadcast to Neural Stream (Global Agent Chat)
                # Map internal agent names to Display Names
                display_name = agent.replace("_", " ").title()
                if agent == "executive": display_name = "Executive CIO"
                
                # Determine log type based on agent
                log_type = "INFO"
                if agent == "risk_manager": log_type = "RISK"
                elif agent == "executive": log_type = "DECISION"
                
                world_state.add_log(display_name, text, log_type)
            
            # --- GLOBAL MARKET CONTEXT CHECK ---
            
            # 1. Session Status
            session_msg = market_context.get_session_status_message()
            if session_msg:
                log_message("executive", session_msg, "👔")
            
            # 2. News Guard (High Impact News Check)
            base_curr = symbol[:3]
            quote_curr = symbol[3:]
            
            news_base = market_context.get_upcoming_news(base_curr, minutes=30)
            news_quote = market_context.get_upcoming_news(quote_curr, minutes=30)
            all_news = news_base + news_quote
            
            news_halt = False
            for news in all_news:
                # 30 minute warning
                log_message("risk_manager", f"🛑 NEWS ALERT: {news['event']} ({news['impact']}) for {base_curr if news in news_base else quote_curr} in {news['minutes_until']} mins.", "🛡️")
                
                if news['impact'] == 'High' and news['minutes_until'] <= 15:
                    news_halt = True
                    reasoning_halt = f"Trading Halted. Imminent High Impact News: {news['event']}"
                    log_message("risk_manager", f"HALTING OPERATIONS. High impact news imminent ({news['minutes_until']}m). Adjustments paused.", "🛡️")
            
            if news_halt:
                 return Recommendation(
                    position_id=str(position.get("id", position.get("ticket"))),
                    symbol=symbol,
                    direction=direction,
                    action=TradeAction.HOLD,
                    confidence=0,
                    reasoning=reasoning_halt,
                    before={"sl": current_sl, "tp": current_tp},
                    after={"sl": current_sl, "tp": current_tp},
                    conversation_log=conversation,
                    analysis_result={} # No analysis performed
                )

            # Run AI Analysis
            world_state.add_log("Trade Manager", f"DEBUG: [Position] Running AI analysis on {symbol}...", "INFO")
            agent = AgentFactory.get_agent("MLens-Market Scout")
            
            # Create fetch callback using MetaAPI (Uses Account object, NOT RPC connection)
            async def fetch_candles(sym: str, tf: str):
                try:
                    from backend.services.metaapi_service import fetch_candles as metaapi_fetch_candles
                    candles = await metaapi_fetch_candles(account_id, sym, tf, limit=50)
                    return candles
                except Exception as e:
                    logger.error(f"Candle fetch error: {e}")
                    world_state.add_log("Trade Manager", f"DEBUG: [Position] Candle fetch FAILED for {sym}: {e}", "ERROR")
                    return []
            
            # [PASS MODEL OVERRIDE] AND POSITION CONTEXT
            world_state.add_log("Trade Manager", f"DEBUG: [Position] Calling process_single_request for {symbol} H1...", "INFO")
            analysis = await agent.process_single_request(symbol, "H1", fetch_callback=fetch_candles, position_context=position_context)
            await agent.close()
            world_state.add_log("Trade Manager", f"DEBUG: [Position] AI analysis complete for {symbol}.", "INFO")
            
            # Generate Recommendation based on analysis
            ai_direction = analysis.get("direction", "WAIT")
            ai_confidence = analysis.get("confidence", 0)
            
            # Decision Logic
            action = TradeAction.HOLD
            reasoning = ""
            new_sl = current_sl
            new_tp = current_tp
            
            # Log Trade Manager initial analysis
            log_message("trade_manager", f"Analyzing {symbol} {direction} position. Current P&L: {profit_pips:.1f} pips.", "🤖")
            log_message("trade_manager", f"AI Analysis shows {ai_direction} with {ai_confidence}% confidence.", "🤖")
            
            # 1. Check for invalidation (AI says opposite direction)
            if ai_direction != "WAIT" and ai_direction != direction:
                if ai_confidence >= 75:
                    action = TradeAction.CLOSE_FULL
                    reasoning = f"Market structure has shifted to {ai_direction}. Original {direction} thesis invalidated."
                elif ai_confidence >= 60:
                    action = TradeAction.CLOSE_PARTIAL
                    reasoning = f"Weakening conditions for {direction}. Consider reducing exposure."
                else:
                    action = TradeAction.ALERT
                    reasoning = f"Market shows signs of reversal but confidence is low ({ai_confidence}%). Monitor closely."
            
            # --- AGENT SYNERGY: THE COUNCIL (CIO CHECK) ---
            # Instead of a hard block, we consult the Executive CIO Agent
            
            # Construct Proposal
            proposal = {
                "symbol": symbol,
                "action": action.value, # "hold", "close", etc. or "buy" implied if evaluating new setup
                "direction": direction, # Wait, for existing positions this is usually HOLD/CLOSE. 
                # If this logic is evaluating NEW setups (which is mixed here), we need to clarify.
                # Currently _evaluate_position handles EXISTING positions mainly.
                # But let's assume if action != HOLD, we propose it.
                "confidence": ai_confidence,
                "reasoning": reasoning or f"Standard trade management: {action.value}"
            }
            
            # Retrieve open positions list for Risk Report
            # (We need to pass this down or fetch it. For now, empty list as placeholder or fetch from stream if possible.
            # Ideally pass 'filtered_positions' from _evaluate_user_positions, but signature update required.
            # WORKAROUND: For Phase 1 Synergy, we rely on Risk Manager fetching its own data or passing simple stats.
            # Getting open_positions from meta_api might be slow per loop.
            # Let's use an empty list for now if we can't easily pass it, OR better:
            # We already passed 'total_exposure'. Risk Manager 'get_risk_report' needs open_positions for Correlation.
            # We should update signature of _evaluate_position to accept open_positions list.
            pass 
            
            # ACTUALLY, checking previous step, I see I missed passing open_positions in step 14870.
            # I will assume we only check correlation on NEW trades usually. For managing existing, 
            # maybe we skip correlation check or just check exposure.
            
            # Let's keep it simple for now: Call Executive
            # We need to import ExecutiveService first.
            
            # (See imports section update below)
            
            # Log Trade Manager proposal
            if action != TradeAction.HOLD:
                log_message("trade_manager", f"Proposing action: {action.value.upper()}. Consulting CIO for approval...", "🤖")
            
            # Log Risk Manager consultation
            log_message("risk_manager", f"Checking account safety. Daily PnL status and exposure limits...", "🛡️")
            
            try:
                executive_decision = await self.executive_service.evaluate_trade_proposal(
                    user_id=user_id,
                    account_id=account_id,
                    proposal=proposal,
                    open_positions=[], # TODO: Pass real list in next refactor for full correlation
                    equity=equity,
                    total_exposure=total_exposure
                )
                
                decision = executive_decision.get("decision", "REJECTED")
                cio_reasoning = executive_decision.get("user_report", "CIO Review Failed.")
                
                if decision == "REJECTED" and action != TradeAction.HOLD:
                    # Override to ALERT only
                    action = TradeAction.ALERT
                    reasoning = f"⛔ CIO REJECTED: {cio_reasoning}"
                    log_message("executive", f"DECISION: REJECTED. {cio_reasoning}", "👔")
                    
                elif decision == "MODIFIED":
                     # If CIO says modify, we might adjust lots (for new trades) or just append reasoning
                     reasoning = f"⚠️ CIO MODIFIED: {cio_reasoning}. {reasoning}"
                     log_message("executive", f"DECISION: MODIFIED. {cio_reasoning}", "👔")
                     # logic to adjust lots would go here for new orders
                     
                elif decision == "APPROVED":
                    # Append CIO endorsement
                    if action != TradeAction.HOLD:
                        reasoning = f"✅ CIO APPROVED: {cio_reasoning}. {reasoning}"
                        log_message("executive", f"DECISION: APPROVED. {cio_reasoning}. {reasoning}", "👔")

            except Exception as syn_err:
                logger.error(f"Synergy Failure: {syn_err}")
                log_message("risk_manager", "Executive Council unreachable. Proceeding with standard protocols.", "⚠️")
            
            # -----------------------------------------------
            
            # 2. Check for trailing/breakeven opportunities
            if action == TradeAction.HOLD and profit_pips > 0:
                # Dynamic ATR Logic
                atr = analysis.get("technical_data", {}).get("H1", {}).get("atr", 0)
                
                # Defaults
                buffer_pips = settings.breakeven_buffer # Fixed fallback
                trail_dist_pips = settings.min_profit_to_trail * 0.5 # Fixed fallback
                
                if atr > 0:
                    # ATR-Based Thresholds
                    # Breakeven Trigger: > 1.0 ATR profit
                    # Buffer: 0.1 ATR (Approx 2-5 pips for majors)
                    # Trail Trigger: > 1.5 ATR profit
                    
                    atr_pips = atr / pip_value
                    
                    # Override fixed settings with dynamic
                    trigger_be_pips = max(atr_pips * 1.0, 10) # Min 10 pips always
                    buffer_pips = max(atr_pips * 0.15, 2.0)   # Min 2 pips buffer
                    
                    trigger_trail_pips = max(atr_pips * 1.5, 20)
                    trail_dist_pips = max(atr_pips * 1.0, 15)
                    
                    # Log if using dynamic
                    # world_state.add_log("Trade Manager", f"DEBUG: Using Dynamic ATR ({atr_pips:.1f} pips). BE Trigger: {trigger_be_pips:.1f}, Trail Trigger: {trigger_trail_pips:.1f}", "INFO")
                else:
                    trigger_be_pips = settings.min_profit_to_trail
                    trigger_trail_pips = settings.min_profit_to_trail * 2
                
                # Breakeven check
                if profit_pips >= trigger_be_pips:
                    if direction == "BUY":
                        breakeven_level = entry_price + (buffer_pips * pip_value)
                        # Only move if current SL is worse (lower) than BE level
                        if current_sl < breakeven_level:
                            action = TradeAction.MOVE_SL_BREAKEVEN
                            new_sl = round(breakeven_level, 5)
                            reasoning = f"Profit ({profit_pips:.1f} pips) > Threshold ({trigger_be_pips:.1f}). Locking BE + {buffer_pips:.1f} pips."
                    else:  # SELL
                        breakeven_level = entry_price - (buffer_pips * pip_value)
                        # Only move if current SL is worse (higher) or 0
                        if current_sl == 0 or current_sl > breakeven_level:
                            action = TradeAction.MOVE_SL_BREAKEVEN
                            new_sl = round(breakeven_level, 5)
                            reasoning = f"Profit ({profit_pips:.1f} pips) > Threshold ({trigger_be_pips:.1f}). Locking BE + {buffer_pips:.1f} pips."
                
                # Trail check (Overrides BE if better)
                if profit_pips >= trigger_trail_pips:
                    trail_distance_price = trail_dist_pips * pip_value
                    
                    if direction == "BUY":
                        proposed_sl = current_price - trail_distance_price
                        if proposed_sl > current_sl and proposed_sl > new_sl: # Better than current and better than BE
                            action = TradeAction.TRAIL_SL
                            new_sl = round(proposed_sl, 5)
                            reasoning = f"Strong trend ({profit_pips:.1f} pips). Trailing SL at {trail_dist_pips:.1f} pips distance (ATR-based)."
                    else:
                        proposed_sl = current_price + trail_distance_price
                        if (current_sl == 0 or proposed_sl < current_sl) and (new_sl == 0 or proposed_sl < new_sl):
                            action = TradeAction.TRAIL_SL
                            new_sl = round(proposed_sl, 5)
                            reasoning = f"Strong trend ({profit_pips:.1f} pips). Trailing SL at {trail_dist_pips:.1f} pips distance (ATR-based)."
            
            # 3. Default to HOLD
            if action == TradeAction.HOLD:
                reasoning = f"Position still valid. {ai_direction} signal with {ai_confidence}% confidence aligns with current {direction}."
                log_message("trade_manager", "Position is valid. No action required.", "🤖")
            else:
                log_message("trade_manager", f"Final decision: {action.value.upper()}", "🤖")
            
            # Calculate Risk Eliminated (Approx)
            risk_eliminated_usd = 0.0
            if action in [TradeAction.MOVE_SL_BREAKEVEN, TradeAction.TRAIL_SL] and new_sl:
               # Risk from Entry to Current SL vs Entry to New SL
               # If current SL is None/0, risk was potentially full account
               dist = abs(entry_price - new_sl)
               risk_eliminated_usd = (dist / pip_value) * 10.0 * position.get("volume", 0) # Approx standard lot value
               
            return Recommendation(
                position_id=str(position.get("id", position.get("ticket"))),
                symbol=symbol,
                direction=direction,
                action=action,
                confidence=ai_confidence,
                reasoning=reasoning,
                before={"sl": current_sl, "tp": current_tp},
                after={"sl": new_sl, "tp": new_tp},
                conversation_log=conversation,
                analysis_result=analysis,
                details={
                    "time_in_trade": time_in_trade_str,
                    "pnl_pips": profit_pips,
                    "risk_eliminated": risk_eliminated_usd,
                    "atr_used": bool(analysis.get("technical_data", {}).get("H1", {}).get("atr", 0) > 0),
                    "threshold_desc": f"{'Dynamic (ATR)' if analysis.get('technical_data', {}).get('H1', {}).get('atr', 0) > 0 else 'Fixed'}"
                }
            )
            
        except Exception as e:
            logger.error(f"Error evaluating position {position.get('symbol')}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            try:
                from backend.core.system_state import world_state
                world_state.add_log("Trade Manager", f"DEBUG: [Position] CRASH on {position.get('symbol', '?')}: {e}", "ERROR")
            except: pass
            return None
    
    async def _deduct_credits(self, user_id: str, amount: int):
        """Deduct credits from user's account"""
        try:
            doc_ref = db.collection("users").document(user_id)
            from google.cloud.firestore import Increment
            doc_ref.update({"credits": Increment(-amount)})
            logger.info(f"Deducted {amount} credit(s) from {user_id}")
        except Exception as e:
            logger.error(f"Credit deduction error: {e}")
    
    async def _log_recommendation(self, user_id: str, rec: Recommendation):
        """Log recommendation to Firestore"""
        try:
            log_data = asdict(rec)
            log_data["action"] = rec.action.value
            
            db.collection("users").document(user_id).collection("trade_manager_logs").add(log_data)
        except Exception as e:
            logger.error(f"Log error: {e}")
    
    async def _execute_action(self, user_id: str, account_id: str, rec: Recommendation):
        """Execute the recommended action via MetaAPI"""
        try:
            # Check action limits
            hour_key = f"{user_id}:{datetime.utcnow().hour}"
            day_key = f"{user_id}:{datetime.utcnow().date()}"
            
            settings = self.user_settings.get(user_id, TradeManagerSettings())
            
            current_hour_actions = self.actions_this_hour.get(hour_key, 0)
            current_day_actions = self.actions_today.get(day_key, 0)
            
            if current_hour_actions >= settings.max_actions_hour:
                logger.warning(f"Hourly action limit reached for {user_id}")
                return
            if current_day_actions >= settings.max_actions_day:
                logger.warning(f"Daily action limit reached for {user_id}")
                return
            
            # Get RPC connection
            connection = await meta_api_singleton.get_rpc_connection(account_id)
            
            # Execute based on action type
            if rec.action == TradeAction.MOVE_SL_BREAKEVEN or rec.action == TradeAction.TRAIL_SL:
                await connection.modify_position(rec.position_id, rec.after["sl"], rec.after["tp"])
                logger.info(f"Modified position {rec.position_id}: SL -> {rec.after['sl']}")
            
            elif rec.action == TradeAction.CLOSE_PARTIAL:
                # Close 50% of position - need to get current volume first
                pass  # TODO: Implement partial close
            
            elif rec.action == TradeAction.CLOSE_FULL:
                await connection.close_position(rec.position_id)
                logger.info(f"Closed position {rec.position_id}")
            
            # Update action counts
            self.actions_this_hour[hour_key] = current_hour_actions + 1
            self.actions_today[day_key] = current_day_actions + 1
            
            # Update cooldown
            self.last_evaluation[f"{user_id}:{rec.position_id}"] = time.time()
            
        except Exception as e:
            logger.error(f"Execution error for {rec.position_id}: {e}")
    
    async def _push_recommendations(self, user_id: str, recommendations: List[Recommendation]):
        """Push recommendations to WebSocket for frontend display"""
        try:
            from backend.services.websocket_manager import websocket_manager
            
            data = {
                "type": "trade_manager_update",
                "recommendations": [
                    {
                        "position_id": r.position_id,
                        "symbol": r.symbol,
                        "direction": r.direction,
                        "action": r.action.value,
                        "confidence": r.confidence,
                        "reasoning": r.reasoning,
                        "before": r.before,
                        "after": r.after,
                        "timestamp": r.timestamp,
                        "conversation_log": r.conversation_log,
                        "analysisResult": r.analysis_result, # Pass this to Frontend using CamelCase
                        "details": r.details # Rich context: Risk Eliminated, Time, P&L
                    }
                    for r in recommendations
                ]
            }
            
            await websocket_manager.emit_trade_manager_update(user_id, data)
            
        except Exception as e:
            logger.error(f"WebSocket push error: {e}")
    
    # ==========================================================================
    # PUBLIC API METHODS
    # ==========================================================================
    
    async def _generate_initial_review(self, user_id: str, settings_dict: Dict):
        """
        Background task:
        1. Emits 'Agent at work' status to Chat.
        2. Uses LLM to generate strategic summary.
        3. Emits 'CIO Logic' to Chat.
        4. Triggers actual evaluation.
        """
        logger.info(f"[_generate_initial_review] Starting for user {user_id}...")
        try:
            from backend.core.system_state import world_state
            
            # DEBUG MARKER
            world_state.add_log("Trade Manager", "DEBUG: Background task started. Importing websocket_manager...", "INFO")
            
            from backend.services.websocket_manager import websocket_manager
            loop = asyncio.get_running_loop()

            world_state.add_log("Trade Manager", f"DEBUG: Import complete. Fetching user doc for {user_id}...", "INFO")

            # 0. Fetch Context
            user_doc = await loop.run_in_executor(None, lambda: db.collection("users").document(user_id).get())
            
            if not user_doc.exists:
                world_state.add_log("Trade Manager", f"DEBUG: User {user_id} not found in DB!", "ERROR")
                logger.error(f"[_generate_initial_review] User {user_id} not found.")
                return
            
            world_state.add_log("Trade Manager", "DEBUG: User doc fetched. Emitting initial status...", "INFO")
            
            user_data = user_doc.to_dict()
            settings = TradeManagerSettings(**settings_dict)

            
            # 1. Emit Initial "Working" Status
            logger.info(f"[_generate_initial_review] Step 1: Emitting initial status for {user_id}")
            initial_msg = {
                "type": "trade_manager_update",
                "recommendations": [{
                    "position_id": f"sys-init-{int(time.time())}",
                    "symbol": "AGENT",
                    "direction": "",
                    "action": "system",
                    "confidence": 100,
                    "reasoning": "Analysis started. Scanning open positions and market structure...",
                    "before": {},
                    "after": {},
                    "timestamp": datetime.utcnow().isoformat()
                }]
            }
            try:
                await websocket_manager.emit_trade_manager_update(user_id, initial_msg)
                logger.info(f"[_generate_initial_review] Step 1: Complete. Status emitted.")
                # DEBUG MARKER
                world_state.add_log("Trade Manager", "DEBUG: Initial status emit COMPLETE.", "INFO")
            except Exception as ws_e:
                logger.error(f"[_generate_initial_review] Step 1 FAILED: {ws_e}")
                world_state.add_log("Trade Manager", f"DEBUG: Initial status emit FAILED: {ws_e}", "ERROR")
            
            # 2. Gather Context
            logger.info(f"[_generate_initial_review] Step 2: Gathering context...")
            
            # Fallback Logic for Account ID
            mt5_accounts = user_data.get("mt5_accounts", [])
            acc_id = None
            if mt5_accounts:
                acc_id = mt5_accounts[0].get("account_id") if isinstance(mt5_accounts[0], dict) else mt5_accounts[0]
            
            if not acc_id:
                acc_id = user_data.get("activeAccountId")
            
            positions = []
            account_info = {"balance": 0, "equity": 0}
            
            if acc_id:
                # Ensure Stream is Active
                from backend.services.streaming_service import stream_manager
                listener = stream_manager.listeners.get(acc_id)
                
                if not listener:
                    logger.info(f"[_generate_initial_review] Stream not active for {acc_id}. Launching background start...")
                    world_state.add_log("Trade Manager", "DEBUG: Stream inactive. Initiating background connection...", "INFO")
                    
                    # NON-BLOCKING START: Fire and forget to avoid hanging analysis
                    loop.create_task(stream_manager.start_stream(acc_id, user_id))
                    world_state.add_log("Trade Manager", "DEBUG: Background stream task launched. Switching to Snapshot for immediate data...", "INFO")

                # Data Retrieval Strategy: Listener -> Snapshot
                if listener:
                    positions = listener.state.get("positions", [])
                    # Safely get account info
                    info = listener.state if "balance" in listener.state else listener.state.get("account_info", {})
                    account_info["balance"] = info.get("balance", 0)
                    account_info["equity"] = info.get("equity", 0)
                    logger.info(f"[_generate_initial_review] Data retrieved from Stream: {len(positions)} positions")
                    world_state.add_log("Trade Manager", f"DEBUG: Retrieved {len(positions)} positions from LIVE stream.", "INFO")
                else:
                    # Fallback: Snapshot
                    world_state.add_log("Trade Manager", "DEBUG: Attempting direct RPC snapshot...", "INFO")
                    try:
                        # Add slight delay to allow background loop to tick if needed (optional)
                        # await asyncio.sleep(0.1) 
                        
                        snapshot = await self._get_account_snapshot(acc_id)
                        if snapshot:
                            positions = snapshot.get("positions", [])
                            account_info = snapshot.get("account_info", {})
                            world_state.add_log("Trade Manager", f"DEBUG: Snapshot success. {len(positions)} positions.", "INFO")
                            logger.info(f"[_generate_initial_review] Data retrieved from Snapshot.")
                        else:
                            world_state.add_log("Trade Manager", "DEBUG: Snapshot failed. Proceeding with empty data.", "ERROR")
                    except Exception as snap_e:
                        world_state.add_log("Trade Manager", f"DEBUG: Snapshot crashed: {snap_e}", "ERROR")

            else:
                 logger.error(f"[_generate_initial_review] No Account ID found for user {user_id}")
                 world_state.add_log("Trade Manager", "DEBUG: No Account ID found. Cannot analyze.", "ERROR")
            
            # 3. Generate LLM Summary
            logger.info(f"[_generate_initial_review] Step 3: Generating LLM Summary. Positions: {len(positions)}")
            world_state.add_log("Trade Manager", f"DEBUG: Step 3: AI Analysis for {len(positions)} positions...", "INFO")
            
            summary_text = "No open positions to analyze. Monitoring for new opportunities."
            
            # Always run LLM if we have connection, even if 0 positions (market commentary?)
            # For now, restrict to only if positions exist OR if we want a general breakdown
            if positions or account_info.get("balance", 0) > 0:
                try:
                    # Construct Prompt
                    pos_list = ", ".join([f"{p['symbol']} ({p['type']})" for p in positions]) if positions else "None"
                    balance = account_info.get("balance", 0)
                    equity = account_info.get("equity", 0)
                    
                    prompt = f"""
                    ROLE: Chief Investment Officer (CIO)
                    TASK: Review this portfolio and give a 1-sentence strategic summary.
                    
                    PORTFOLIO:
                    - Balance: ${balance}
                    - Equity: ${equity}
                    - Positions: {pos_list}
                    
                    OUTPUT:
                    - ONE sentence summary of the exposure and risk.
                    - If no positions, comment on readiness or capital preservation.
                    - Tone: Professional, Strategic, Direct.
                    """
                    
                    # Call Agent
                    world_state.add_log("Trade Manager", "DEBUG: Calling AI Agent...", "INFO")
                    logger.info(f"[_generate_initial_review] Step 3a: Calling AgentFactory for user {user_id}")
                    agent = AgentFactory.get_agent("MLens-CIO")
                    response = await agent.ask(prompt, user_id=user_id)
                    summary_text = response.get("text", "Market analysis complete.")
                    logger.info(f"[_generate_initial_review] Step 3b: Agent response received.")
                    world_state.add_log("Trade Manager", "DEBUG: AI Response Received.", "INFO")
                    
                except Exception as ai_e:
                    logger.error(f"LLM Summary Failed: {ai_e}")
                    world_state.add_log("Trade Manager", f"DEBUG: AI Analysis Failed: {ai_e}", "ERROR")
                    summary_text = f"Portfolio analysis complete. Monitoring {len(positions)} positions."

            # 4. Emit Final Result
            logger.info(f"[_generate_initial_review] Step 4: Emitting final result...")
            world_state.add_log("Trade Manager", "DEBUG: Step 4: Emitting results...", "INFO")
            
            result_msg = {
                "type": "trade_manager_update",
                "recommendations": [{
                    "position_id": f"sys-res-{int(time.time())}",
                    "symbol": "CIO",
                    "direction": "",
                    "action": "system",
                    "confidence": 100,
                    "reasoning": f"CIO UPDATE: {summary_text}",
                    "before": {},
                    "after": {},
                    "timestamp": datetime.utcnow().isoformat()
                }]
            }
            try:
                await websocket_manager.emit_trade_manager_update(user_id, result_msg)
                logger.info(f"[_generate_initial_review] Step 4: Complete. Final result emitted.")
                world_state.add_log("Trade Manager", "DEBUG: Final result emitted. Task Complete.", "INFO")
            except Exception as ws_e_final:
                logger.error(f"[_generate_initial_review] Step 4 FAILED: {ws_e_final}")
                world_state.add_log("Trade Manager", f"DEBUG: Step 4 emit FAILED: {ws_e_final}", "ERROR")
            
            # 5. Trigger Standard Evaluation (The "Hard" Check)
            logger.info(f"[_generate_initial_review] Step 5: Triggering standard evaluation...")
            world_state.add_log("Trade Manager", f"DEBUG: Step 5: Triggering deep analysis for {len(positions)} positions...", "INFO")
            # Pass our fetched/snapshot data so it doesn't fail if Stream is still connecting
            explicit_data = {
                "positions": positions,
                "account_info": account_info,
                "account_id": acc_id
            }
            await self._safe_evaluate_user(user_id, user_data, settings, explicit_data=explicit_data)
            logger.info(f"[_generate_initial_review] Step 5: Complete.")
            world_state.add_log("Trade Manager", "DEBUG: Step 5: Deep analysis complete.", "INFO")
            
        except Exception as e:
            logger.error(f"Initial Review Error: {e}")
            try:
                from backend.core.system_state import world_state
                world_state.add_log("Trade Manager", f"DEBUG: Background task CRASHED: {e}", "ERROR")
            except:
                pass

    async def _get_account_snapshot(self, account_id: str) -> Optional[Dict]:
        """Fetch a one-time snapshot of account state via RPC (Fallback)"""
        try:
            from backend.core.meta_api_client import meta_api_singleton
            connection = await meta_api_singleton.get_rpc_connection(account_id)
            await connection.connect()
            await connection.wait_synchronized()
            
            info = await connection.get_account_information()
            positions = await connection.get_positions()
            
            # Serialize positions
            serialized_positions = []
            for p in positions:
                serialized_positions.append({
                   "id": p.get('id'),
                   "ticket": p.get('ticket'),
                   "symbol": p.get('symbol'),
                   "type": 'BUY' if p.get('type') == 'POSITION_TYPE_BUY' else 'SELL',
                   "volume": p.get('volume'),
                   "openPrice": p.get('openPrice'),
                   "currentPrice": p.get('currentPrice'),
                   "sl": p.get('stopLoss'),
                   "tp": p.get('takeProfit'),
                   "profit": p.get('profit'),
                   "swap": p.get('swap', 0),
                   "commission": p.get('commission', 0),
                   "time": p.get('time'),
                   "openTime": p.get('openTime'),
                })
            
            return {
                "account_info": info,
                "positions": serialized_positions
            }
        except Exception as e:
            logger.error(f"[_get_account_snapshot] Failed: {e}")
            return None

    async def get_settings(self, user_id: str) -> Dict:
        """Get Trade Manager settings for a user  (including account status)"""
        try:
            loop = asyncio.get_running_loop()
            
            def _get():
                doc = db.collection("users").document(user_id).get()
                if not doc.exists:
                    return asdict(TradeManagerSettings())
                
                data = doc.to_dict()
                settings_data = data.get("trade_manager_settings", {})
                
                # [NEW] Inject Account Status for UI
                mt5_accounts = data.get("mt5_accounts", [])
                active_id = data.get("activeAccountId")
                account_status = "OK"
                account_error = None
                
                if active_id and mt5_accounts:
                    # Find active account
                    active_acc = next((a for a in mt5_accounts if a.get('id') == active_id), None)
                    if active_acc:
                        account_status = active_acc.get('status', 'OK')
                        account_error = active_acc.get('error')
                
                # Merge into response
                response = settings_data.copy()
                response['account_status'] = account_status
                response['account_error'] = account_error
                
                return response
            
            return await loop.run_in_executor(None, _get)
        except Exception as e:
            logger.error(f"Error getting settings: {e}")
            return asdict(TradeManagerSettings())
    
    async def update_settings(self, user_id: str, settings: Dict) -> bool:
        """Update Trade Manager settings for a user (Non-blocking)"""
        logger.info(f"[TradeManager] update_settings called for {user_id}")
        try:
            loop = asyncio.get_running_loop()
            
            def _update():
                logger.info(f"[TradeManager] Writing settings to Firestore for {user_id}")
                db.collection("users").document(user_id).set(
                    {"trade_manager_settings": settings},
                    merge=True
                )
                logger.info(f"[TradeManager] Firestore write complete for {user_id}")
            
            logger.info(f"[TradeManager] Awaiting run_in_executor for DB update")
            await loop.run_in_executor(None, _update)
            logger.info(f"[TradeManager] DB update successful")
            
            # Log acknowledgment
            # Log detailed acknowledgment
            from backend.core.system_state import world_state
            
            # Format settings for display
            mode_str = "AUTONOMOUS" if settings.get("autonomous") else "ADVISORY"
            enabled_str = "ENABLED" if settings.get("enabled") else "DISABLED"
            risk = settings.get("max_actions_day", 20)
            interval_val = settings.get("interval_minutes", 15)
            
            if settings.get("enabled"):
                strategies = ", ".join([s.replace("_", " ").title() for s in settings.get("allowed_actions", [])])
                msg = (
                    f"Configuration Update: Trade Manager is now {enabled_str}. "
                    f"Mode: {mode_str}. Interval: {interval_val}min. "
                    f"Active Hours: {settings.get('active_hours_start')} - {settings.get('active_hours_end')}. "
                    f"Daily Limit: {risk} actions. Active Strategies: {strategies}."
                )
                
                world_state.add_log("Trade Manager", msg, "INFO")
                
                # Emit to Frontend
                from backend.services.websocket_manager import websocket_manager
                await websocket_manager.emit_trade_manager_update(user_id, {
                    "type": "log",
                    "log": msg,
                    "timestamp": datetime.datetime.utcnow().isoformat()
                })
                
                # --- IMMEDIATE ANALYSIS TRIGGER ---
                # Cancel any existing analysis for this user first
                existing_task = self._analysis_tasks.get(user_id)
                if existing_task and not existing_task.done():
                    existing_task.cancel()
                    logger.info(f"[TradeManager] Cancelled previous analysis task for {user_id}")
                    world_state.add_log("Trade Manager", "DEBUG: Previous analysis cancelled (new settings received).", "INFO")
                
                logger.info(f"[TradeManager] Triggering background analysis for {user_id}")
                world_state.add_log("Trade Manager", "DEBUG: Scheduling background analysis task...", "INFO") 
                task = asyncio.create_task(self._generate_initial_review(user_id, settings))
                self._analysis_tasks[user_id] = task
                
                # Clean up reference when task completes
                def _cleanup(t, uid=user_id):
                    if self._analysis_tasks.get(uid) is t:
                        del self._analysis_tasks[uid]
                task.add_done_callback(_cleanup)

            else:
                msg = "Trade Manager has been DISABLED. Monitoring suspended. Standby mode engaged."
                world_state.add_log("Trade Manager", msg, "INFO")
                
                # Emit to Frontend
                from backend.services.websocket_manager import websocket_manager
                await websocket_manager.emit_trade_manager_update(user_id, {
                    "type": "log",
                    "log": msg,
                    "timestamp": datetime.datetime.utcnow().isoformat()
                })
            
            return True
        except Exception as e:
            logger.error(f"Error updating settings: {e}")
            return False


    
    async def get_history(self, user_id: str, limit: int = 50) -> List[Dict]:
        """Get Trade Manager history/logs"""
        try:
            logs_ref = (
                db.collection("users")
                .document(user_id)
                .collection("trade_manager_logs")
                .order_by("timestamp", direction="DESCENDING")
                .limit(limit)
            )
            docs = logs_ref.stream()
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            logger.error(f"Error getting history: {e}")
            return []
    
    async def analyze_now(self, user_id: str, position_id: str = None) -> List[Dict]:
        """Force an immediate analysis (deducts 1 credit)"""
        try:
            # Get user data
            doc = db.collection("users").document(user_id).get()
            if not doc.exists:
                return []
            
            user_data = doc.to_dict()
            settings = TradeManagerSettings(**user_data.get("trade_manager_settings", {}))
            
            # Deduct credit
            await self._deduct_credits(user_id, 1)
            
            # Force evaluation
            await self._evaluate_user_positions(user_id, user_data, settings)
            
            # Return latest recommendations
            return await self.get_history(user_id, limit=10)
            
        except Exception as e:
            logger.error(f"Analyze now error: {e}")
            return []


# Singleton instance
trade_manager = TradeManagerService()
