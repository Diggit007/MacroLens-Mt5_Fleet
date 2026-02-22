"""
Event Executor Service
======================
Handles trade execution based on event predictions.

IMPORTANT: Auto-trading is DISABLED by default.
Set EVENT_TRADING_MODE in config.py to enable.

Modes:
- SIGNAL_ONLY (Default): Log predictions, no trades
- STRADDLE: Place pending orders above/below price
- DIRECTIONAL: Pre-emptive trade based on prediction
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Literal, Any
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum
from backend.services.risk_manager import RiskManager

logger = logging.getLogger("EventExecutor")


class TradingMode(Enum):
    """Trading mode for event execution."""
    SIGNAL_ONLY = "SIGNAL_ONLY"      # Log only, no trades
    STRADDLE = "STRADDLE"            # Pending orders both directions
    DIRECTIONAL = "DIRECTIONAL"      # Pre-emptive position


@dataclass
class EventSignal:
    """Signal generated for an event."""
    event_name: str
    symbol: str
    direction: Literal["BUY", "SELL", "HOLD"]
    probability: float
    confidence: str
    entry_logic: str
    stop_loss_pips: int
    take_profit_pips: int
    risk_reward: float
    avg_pips: float = 0.0           # New
    trend_forecast: str = "NEUTRAL" # New
    order_type: str = "MARKET"      # New (MARKET / LIMIT)
    volume: float = 0.01            # New (Lot Size)
    executed: bool = False
    execution_id: Optional[str] = None
    timestamp: str = None # ISO format


class EventExecutor:
    """
    Executes trades based on event predictions.
    
    Default mode is SIGNAL_ONLY - predictions are logged but not executed.
    """
    
    # Default pip ranges for different event types
    EVENT_PIP_RANGES = {
        "NFP": {"sl": 50, "tp": 100},           # Non-Farm Payrolls - high volatility
        "CPI": {"sl": 40, "tp": 80},            # CPI - medium-high volatility
        "GDP": {"sl": 35, "tp": 70},            # GDP - medium volatility
        "FOMC": {"sl": 60, "tp": 120},          # FOMC - very high volatility
        "Interest Rate": {"sl": 50, "tp": 100}, # Rate decisions
        "PMI": {"sl": 25, "tp": 50},            # PMI - lower volatility
        "Retail Sales": {"sl": 30, "tp": 60},   # Retail sales
        "DEFAULT": {"sl": 30, "tp": 60}         # Default for unknown events
    }
    
    def __init__(self, mode: TradingMode = TradingMode.SIGNAL_ONLY):
        self.mode = mode
        self.pending_signals: List[EventSignal] = []
        self.executed_signals: List[EventSignal] = []
        self.risk_manager = RiskManager()
        
    def set_mode(self, mode: TradingMode):
        """Change trading mode."""
        logger.info(f"Trading mode changed: {self.mode.value} -> {mode.value}")
        self.mode = mode
        
    def generate_signal(self, prediction: Any, symbol: str,
                        current_price: float = None, technicals: Dict = None, equity: float = 10000.0) -> EventSignal:
        """
        Generate a trading signal from an event prediction using technicals for entry type.
        """
        # Determine direction (existing logic)
        if prediction.predicted_outcome == "BEAT":
            if prediction.currency == symbol[:3]:
                direction = "BUY"
            else:
                direction = "SELL"
        elif prediction.predicted_outcome == "MISS":
            if prediction.currency == symbol[:3]:
                direction = "SELL"
            else:
                direction = "BUY"
        else:
            direction = "HOLD"
            
        # Get pip ranges
        pip_range = self._get_pip_range(prediction.event_name)
        
        # Calculate risk-reward
        rr = pip_range["tp"] / pip_range["sl"] if pip_range["sl"] > 0 else 0
        
        # Entry logic (Base)
        if prediction.probability >= 0.7 and prediction.confidence == "HIGH":
            entry_logic = "AGGRESSIVE"
        elif prediction.probability >= 0.6:
            entry_logic = "MODERATE"
        else:
            entry_logic = "CONSERVATIVE"
            
        # SMART ENTRY LOGIC (RSI Filtering)
        order_type = "MARKET" # Default
        rsi_val = 50.0
        
        if technicals and "rsi" in technicals:
            rsi_val = technicals["rsi"]
            
            if direction == "BUY":
                if rsi_val > 70:
                    order_type = "LIMIT (Pullback)"
                    entry_logic += " + RSI Overbought (>70)"
                else:
                    order_type = "MARKET"
            
            elif direction == "SELL":
                if rsi_val < 30:
                    order_type = "LIMIT (Pullback)"
                    entry_logic += " + RSI Oversold (<30)"
                else:
                    order_type = "MARKET"

        # RISK MANAGER (Lot Sizing)
        volume = self.risk_manager.calculate_lots(
            equity=equity,
            symbol=symbol,
            sl_pips=pip_range["sl"],
            confidence=prediction.confidence
        )

        signal = EventSignal(
            event_name=prediction.event_name,
            symbol=symbol,
            direction=direction,
            probability=prediction.probability,
            confidence=prediction.confidence,
            entry_logic=entry_logic,
            stop_loss_pips=pip_range["sl"],
            take_profit_pips=pip_range["tp"],
            risk_reward=round(rr, 2),
            avg_pips=prediction.avg_pips,
            trend_forecast=prediction.trend_forecast,
            order_type=order_type, 
            volume=volume, # Calculated Volume
            timestamp=datetime.utcnow().isoformat(),
            executed=False
        )
        
        self.pending_signals.append(signal)
        return signal
    
    def _get_pip_range(self, event_name: str) -> Dict:
        """Get SL/TP pip range based on event type."""
        event_name_upper = event_name.upper()
        
        for key, value in self.EVENT_PIP_RANGES.items():
            if key.upper() in event_name_upper:
                return value
                
        return self.EVENT_PIP_RANGES["DEFAULT"]
    
    def execute_signal(self, signal: EventSignal, 
                        trade_callback=None) -> Dict:
        """
        Execute a trading signal.
        
        Args:
            signal: EventSignal to execute
            trade_callback: Async function to place trade (from MetaAPI)
            
        Returns:
            Dict with execution result
        """
        if self.mode == TradingMode.SIGNAL_ONLY:
            logger.info(f"[SIGNAL_ONLY] Would execute: {signal.direction} {signal.symbol}")
            return {
                "status": "LOGGED",
                "message": f"Signal logged (SIGNAL_ONLY mode): {signal.direction} {signal.symbol}",
                "signal": signal,
                "executed": False
            }
            
        if signal.direction == "HOLD":
            logger.info(f"[HOLD] No trade for {signal.event_name}")
            return {
                "status": "SKIPPED",
                "message": "Direction is HOLD, no trade placed",
                "executed": False
            }
            
        if self.mode == TradingMode.DIRECTIONAL:
            # Place market order
            if trade_callback:
                # This would integrate with MetaAPI
                # result = await trade_callback(signal.symbol, signal.direction, ...)
                logger.warning("[DIRECTIONAL] Trade callback not implemented")
                return {
                    "status": "PENDING",
                    "message": "Trade callback required for execution",
                    "executed": False
                }
            else:
                logger.warning("No trade callback provided")
                return {
                    "status": "ERROR",
                    "message": "No trade callback provided",
                    "executed": False
                }
                
        elif self.mode == TradingMode.STRADDLE:
            # Place pending orders above and below
            logger.warning("[STRADDLE] Straddle mode not yet implemented")
            return {
                "status": "NOT_IMPLEMENTED",
                "message": "Straddle mode pending implementation",
                "executed": False
            }
            
        return {"status": "UNKNOWN", "executed": False}
    
    def get_pending_signals(self) -> List[EventSignal]:
        """Get all pending (unexecuted) signals."""
        return [s for s in self.pending_signals if not s.executed]
    
    def clear_signals(self):
        """Clear all pending signals."""
        self.pending_signals = []
        
    def format_signal(self, signal: EventSignal) -> str:
        """Format signal for display/logging."""
        return f"""🎯 **EVENT TRADE SIGNAL**

**Event:** {signal.event_name}
**Symbol:** {signal.symbol}
**Direction:** {signal.direction}
**Probability:** {signal.probability:.0%}
**Confidence:** {signal.confidence}

**ENTRY LOGIC:** {signal.entry_logic}

**RISK MANAGEMENT:**
- Stop Loss: {signal.stop_loss_pips} pips
- Take Profit: {signal.take_profit_pips} pips
- Risk/Reward: 1:{signal.risk_reward}

**STATUS:** {"⚡ EXECUTED" if signal.executed else "⏳ PENDING"}
**MODE:** {self.mode.value}"""

    def get_status(self) -> Dict:
        """Get executor status."""
        return {
            "mode": self.mode.value,
            "pending_signals": len(self.pending_signals),
            "executed_signals": len(self.executed_signals),
            "auto_trading_enabled": self.mode != TradingMode.SIGNAL_ONLY
        }


# =============================================================================
# STANDALONE TEST
# =============================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Create executor in default (safe) mode
    executor = EventExecutor(mode=TradingMode.SIGNAL_ONLY)
    
    print(f"=== Event Executor Status ===")
    print(f"Mode: {executor.get_status()['mode']}")
    print(f"Auto Trading: {executor.get_status()['auto_trading_enabled']}")
    
    # Simulate a prediction (normally from EventPredictor)
    class MockPrediction:
        event_name = "US CPI"
        currency = "USD"
        predicted_outcome = "BEAT"
        probability = 0.75
        confidence = "HIGH"
        avg_pips = 25.0
        trend_forecast = "BULLISH"
        
        
    mock_pred = MockPrediction()
    
    # Generate signal
    print("\n=== Generating Signal ===")
    signal = executor.generate_signal(mock_pred, "EURUSD")
    print(executor.format_signal(signal))
    
    # Try to execute (will be logged only in SIGNAL_ONLY mode)
    print("\n=== Execution Attempt ===")
    result = executor.execute_signal(signal)
    print(f"Result: {result['status']} - {result['message']}")
