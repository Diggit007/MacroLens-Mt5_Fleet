import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict

from backend.services.event_predictor import EventPredictor
from backend.services.event_executor import EventExecutor, TradingMode
from backend.core.database import DatabasePool
from backend.config import settings
from backend.firebase_setup import initialize_firebase # <--- Import Firebase
from backend.services.metaapi_service import fetch_candles, get_account_information
from backend.services.technical_analysis import TechnicalAnalyzer

logger = logging.getLogger("EventMonitor")
# Import System State for Neural Stream
from backend.core.system_state import world_state

class EventMonitorService:
    """
    Monitors economic calendar for upcoming high-impact events.
    Triggers prediction and execution pipeline.
    """
    
    def __init__(self):
        self.predictor = EventPredictor()
        # Initialize executor based on config
        mode = TradingMode.SIGNAL_ONLY
        if hasattr(settings, "EVENT_TRADING_MODE"):
            try:
                mode = TradingMode(settings.EVENT_TRADING_MODE)
            except ValueError:
                logger.warning(f"Invalid EVENT_TRADING_MODE {settings.EVENT_TRADING_MODE}, defaulting to SIGNAL_ONLY")
                
        self.executor = EventExecutor(mode=mode)
        self.db = initialize_firebase() # <--- Connect DB
        self.running = False
        self._task = None
        self.analyzed_events = set() # Cache to avoid re-analyzing same event loop
        
    async def start(self):
        if self.running: return
        self.running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(f"Event Monitor Started. Mode: {self.executor.mode.value}")

    async def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Event Monitor Stopped.")

    async def _monitor_loop(self):
        """Check for events every minute."""
        logger.info("Event Monitor Loop Active - Scanning for opportunities...")
        
        while self.running:
            try:
                await self._check_upcoming_events()
            except Exception as e:
                logger.error(f"Monitor Loop Error: {e}")
                
            # Wait 60s
            await asyncio.sleep(60)

    async def _check_upcoming_events(self):
        """
        Query DB for events in next 24 hours (expanded for Demo).
        Standard would be 15-30 mins.
        """
        # Time window
        now = datetime.now()
        future = now + timedelta(hours=24) # Demo window
        
        query = """
            SELECT event_id, event_name, event_date, event_time, currency, 
                   forecast_value, previous_value, impact_level
            FROM economic_events
            WHERE event_date BETWEEN ? AND ?
            AND forecast_value IS NOT NULL
        """
        
        rows = await DatabasePool.fetch_all(query, (now.strftime("%Y-%m-%d"), future.strftime("%Y-%m-%d")))
        
        for row in rows:
            event_id = row[0]
            if event_id in self.analyzed_events:
                continue
                
            # Parse time to check if it's truly upcoming
            # For demo, we process ALL found in window once
            
            logger.info(f"🔎 DETECTED EVENT: {row[1]} ({row[4]}) at {row[2]} {row[3]}")
            world_state.add_log("Risk Manager", f"Economic Event Detected: {row[1]} ({row[4]}). Monitoring for volatility impact...", "RISK")
            
            # 1. Run Prediction
            prediction = self.predictor.predict_event(
                event_name=row[1],
                forecast=row[5],
                previous=row[6],
                currency=row[4]
            )
            
            # 1.5 Fetch Technicals (Smart Entry)
            technicals = {}
            symbol = self._get_primary_pair(row[4])
            
            if symbol:
                try:
                    # Fetch M15 candles for RSI
                    account_id = settings.META_API_ACCOUNT_ID
                    if account_id:
                        candles = await fetch_candles(account_id, symbol, "15m", limit=30)
                        if candles:
                            analyzer = TechnicalAnalyzer(candles)
                            technicals["rsi"] = analyzer.get_rsi(14)
                            # Could add Bollinger here too if needed
                            logger.info(f"📊 Technicals for {symbol}: RSI={technicals.get('rsi')}")
                except Exception as e:
                    logger.error(f"Failed to fetch technicals: {e}")

            # 1.6 Use Generic Reference Equity for Public Feed/Signals
            # We do NOT use the Admin account equity here to avoid data leaks in the public feed.
            equity = 10000.0 # Standard Reference Equity for "1 Lot = $10k" approximation logic if needed
            
            # (Optional) Log that we are using reference equity
            # logger.info(f"💰 Using Reference Equity: ${equity} for signal generation")

            # Log Prediction
            logger.info(f"\n{self.predictor.format_for_prompt(prediction)}")
            
            # 2. Generate Signal
            if symbol:
                signal = self.executor.generate_signal(
                    prediction, 
                    symbol, 
                    technicals=technicals,
                    equity=equity # <--- Pass Generic Equity
                )
                
                # 3. Log Signal
                logger.info(f"\n{self.executor.format_signal(signal)}")
                
                # 3.5 Push to Firestore (Frontend Feed)
                if self.db:
                    try:
                        doc_data = {
                            "type": signal.direction,
                            "pair": signal.symbol,
                            "price": "Market", # Open price
                            "sl": f"{signal.stop_loss_pips} pips",
                            "tp": f"{signal.take_profit_pips} pips",
                            "confidence": int(signal.probability * 100),
                            "validity": "Pre-Release",
                            "window": "Immediate",
                            "strategy": "Event Sniper",
                            "reasoning": f"{signal.event_name}: {prediction.predicted_outcome} ({signal.probability:.0%}). Bias: {signal.trend_forecast}",
                            "timestamp": datetime.utcnow().isoformat(),
                            "forecast_pips": signal.avg_pips,   
                            "trend_bias": signal.trend_forecast,
                            "order_type": signal.order_type,
                            "volume": signal.volume # New Field
                        }
                        self.db.collection("signals").add(doc_data)
                    except Exception as e:
                        logger.error(f"Failed to push signal to Firestore: {e}")
                
                # 4. Execute (If enabled)
                result = self.executor.execute_signal(signal)
                if result.get("executed"):
                    logger.info(f"⚡ EXECUTION: {result['message']}")
                    world_state.add_log("Trade Manager", f"Event Execution Triggered: {result['message']}", "DECISION")
            
            # Mark as analyzed so we don't spam logs every minute
            self.analyzed_events.add(event_id)
            
            # THROTTLE: Sleep between events to prevent "TooManyRequestsError" on MetaApi
            await asyncio.sleep(2)

    def _get_primary_pair(self, currency: str) -> str:
        """Get best liquid pair for currency."""
        map = {
            "USD": "EURUSD", # Trade EURUSD inverted? Or USDJPY? 
                             # Predictor returns general bias.
                             # If prediction is Bullish USD, we Sell EURUSD or Buy USDJPY.
                             # Executor handles direction logic if we pass primary pair.
                             # Let's use USDJPY for direct correlation or EURUSD.
            "EUR": "EURUSD",
            "GBP": "GBPUSD",
            "JPY": "USDJPY",
            "AUD": "AUDUSD",
            "CAD": "USDCAD",
            "CHF": "USDCHF",
            "NZD": "NZDUSD"
        }
        return map.get(currency, "EURUSD")

event_monitor = EventMonitorService()
