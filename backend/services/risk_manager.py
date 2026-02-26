import logging
import asyncio
import math
from typing import Dict, List, Optional, Tuple
from datetime import datetime

# Import MetaApi Singleton for data fetching
from backend.core.meta_api_client import meta_api_singleton

logger = logging.getLogger("RiskManager")

class RiskManager:
    """
    The Guardian Agent.
    Monitors account health, enforces safety limits, and calculates risk-adjusted position sizes.
    Includes Internal Correlation Engine.
    """
    
    def __init__(self):
        # Default fallback settings
        self.default_risk_pct = 0.01  # 1%
        self.max_lots = 5.0
        self.min_lots = 0.01
        
        # Guardian Limits
        self.max_daily_loss_pct = 0.05  # 5% max daily drawdown
        self.max_open_positions = 10
        self.max_symbol_exposure = 2.0  # Max lots per symbol
        self.max_correlation_score = 0.8  # Max Pearson Correlation (0.8 = 80%)
        
        # State Tracking
        self.daily_start_equity: Dict[str, float] = {}  # user_id -> equity
        self.daily_pnl: Dict[str, float] = {}           # user_id -> pnl
        self.last_reset_date = datetime.utcnow().date()
        
        # Cache for correlation data to avoid spamming API
        self.correlation_cache: Dict[str, float] = {} # "SymA:SymB" -> score
        self.cache_timestamp: Dict[str, float] = {}

    async def get_risk_report(self, user_id: str, symbol: str, current_exposure: float, equity: float, account_id: str, open_positions: List[Dict]) -> Dict:
        """
        Generates a detailed risk report for the CIO Agent.
        Does NOT block trades, just reports facts.
        """
        self._check_daily_reset(user_id, equity)
        
        current_pnl = self.daily_pnl.get(user_id, 0)
        daily_loss_pct = (abs(current_pnl) / equity) if equity > 0 else 0
        
        # Check Correlation Risk
        correlation_warning = "None"
        high_risk_pairs = []
        if open_positions:
            high_risk_pairs = await self.get_correlation_risk(symbol, open_positions, account_id)
            if high_risk_pairs:
                correlation_warning = ", ".join([f"{p['symbol']} ({score:.2f})" for p, score in high_risk_pairs])

        return {
            "daily_pnl": round(current_pnl, 2),
            "daily_pnl_pct": round(daily_loss_pct * 100, 2), # e.g. 1.5%
            "total_exposure": round(current_exposure, 2),
            "equity": round(equity, 2),
            "correlation_warning": correlation_warning,
            "max_daily_loss_threshold": self.max_daily_loss_pct * 100,
            "exposure_threshold": self.max_symbol_exposure
        }

    async def check_trade_safety(self, user_id: str, symbol: str, current_exposure: float, equity: float, account_id: str, open_positions: List[Dict]) -> Tuple[bool, str]:
        """
        Circuit Breaker check.
        Returns (IsSafe, Reason).
        Now ASYNC to allow data fetching.
        """
        self._check_daily_reset(user_id, equity)
        
        # 1. Daily Drawdown Limit
        daily_loss_pct = (abs(self.daily_pnl.get(user_id, 0)) / equity) if equity > 0 else 0
        current_pnl = self.daily_pnl.get(user_id, 0)
        
        if current_pnl < 0 and daily_loss_pct >= self.max_daily_loss_pct:
            return False, f"Daily Loss Limit Hit ({daily_loss_pct*100:.1f}%). Trading Halted."
            
        # 2. Symbol Exposure Limit
        if current_exposure >= self.max_symbol_exposure:
             return False, f"Max Exposure Reached for {symbol} ({current_exposure} lots)."
             
        # 3. Correlation Check (Real Calculation)
        # We check the new symbol against all currently open positions
        if open_positions:
            high_risk_pairs = await self.get_correlation_risk(symbol, open_positions, account_id)
            if high_risk_pairs:
                pairs_str = ", ".join([f"{p['symbol']} ({score:.2f})" for p, score in high_risk_pairs])
                return False, f"Correlation Too High with: {pairs_str}"
            
        return True, "Safe"

    async def get_correlation_risk(self, symbol: str, open_positions: List[Dict], account_id: str) -> List[Tuple[Dict, float]]:
        """
        Calculates correlation between target symbol and all open positions.
        Returns list of (position_dict, correlation_score) for matches > max_correlation_score.
        """
        risky_matches = []
        
        # Fetch target symbol data once
        target_closes = await self._fetch_historical_prices(symbol, account_id)
        if not target_closes:
            return [] # Cannot calc checks, default safe (or fail closed?) -> Default safe for now
            
        for pos in open_positions:
            pos_symbol = pos.get('symbol')
            if pos_symbol == symbol:
                continue # Ignore self
                
            # Check Cache first (valid for 1 hour)
            cache_key = f"{min(symbol, pos_symbol)}:{max(symbol, pos_symbol)}"
            cached_val = self.correlation_cache.get(cache_key)
            cached_time = self.cache_timestamp.get(cache_key, 0)
            
            if cached_val is not None and (time.time() - cached_time) < 3600:
                score = cached_val
            else:
                # Calculate Live
                pos_closes = await self._fetch_historical_prices(pos_symbol, account_id)
                if not pos_closes:
                    continue
                    
                score = self._calculate_pearson_correlation(target_closes, pos_closes)
                
                # Update Cache
                self.correlation_cache[cache_key] = score
                self.cache_timestamp[cache_key] = time.time()
            
            if abs(score) >= self.max_correlation_score:
                risky_matches.append((pos, score))
                
        return risky_matches

    async def _fetch_historical_prices(self, symbol: str, account_id: str) -> List[float]:
        """Fetch last 50 H1 closing prices for correlation calc"""
        try:
            connection = await meta_api_singleton.get_rpc_connection(account_id)
            # Fetch 100 candles on H1
            candles = await connection.get_candles(symbol, "H1", time=None, limit=100)
            
            # Extract closing prices
            closes = [c['close'] for c in candles]
            return closes
        except Exception as e:
            logger.error(f"Error fetching correlation data for {symbol}: {e}")
            return []

    def _calculate_pearson_correlation(self, x: List[float], y: List[float]) -> float:
        """
        Pure Python Pearson Correlation Calculation.
        No external dependencies (numpy/pandas) for maximum portability.
        """
        n = min(len(x), len(y))
        if n < 10: return 0.0 # Not enough data
        
        x = x[-n:] # Trim to same length
        y = y[-n:]
        
        sum_x = sum(x)
        sum_y = sum(y)
        sum_xy = sum(i*j for i, j in zip(x, y))
        sum_x_sq = sum(i**2 for i in x)
        sum_y_sq = sum(i**2 for i in y)
        
        numerator = (n * sum_xy) - (sum_x * sum_y)
        denominator = math.sqrt((n * sum_x_sq - sum_x**2) * (n * sum_y_sq - sum_y**2))
        
        if denominator == 0:
            return 0.0
            
        return numerator / denominator

    def calculate_lots(self, equity: float, symbol: str, sl_pips: int, confidence: str, user_id: str = "default") -> float:
        """
        Calculate recommended lot size with dynamic, synergy-aware scaling.
        """
        if equity <= 0 or sl_pips <= 0:
            return self.min_lots

        # 1. Base Risk Percentage
        risk_pct = 0.01
        if confidence == "HIGH": risk_pct = 0.015
        elif confidence == "MEDIUM": risk_pct = 0.010
        else: risk_pct = 0.005
        
        # 2. Dynamic Dampener (Synergy)
        current_dd = self.daily_pnl.get(user_id, 0)
        if current_dd < 0:
            dampener = 1.0 - (abs(current_dd) / (equity * self.max_daily_loss_pct))
            risk_pct *= max(0.1, dampener)
            
        # 3. Calculate Risk Amount ($)
        risk_amount = equity * risk_pct
        
        # 4. Pip Value Params
        pip_value_per_lot = 10.0 
        if "JPY" in symbol: pip_value_per_lot = 7.0
        elif "GBP" in symbol[:3]: pip_value_per_lot = 10.0
        elif "CAD" in symbol: pip_value_per_lot = 7.5
            
        # 5. Calculate Lots
        raw_lots = risk_amount / (pip_value_per_lot * sl_pips)
        
        # 6. Apply Limits
        lots = round(raw_lots, 2)
        lots = max(self.min_lots, min(lots, self.max_lots))
        
        logger.info(f"💰 Risk Calc: Equity=${equity:.0f} Risk={risk_pct*100:.2f}% (${risk_amount:.0f}) SL={sl_pips} -> {lots} Lots")
        
        return lots

    def _check_daily_reset(self, user_id: str, current_equity: float):
        """Resets daily stats if new day"""
        today = datetime.utcnow().date()
        if today > self.last_reset_date:
            self.daily_pnl = {}
            self.daily_start_equity = {}
            self.last_reset_date = today
            logger.info("🔄 Daily Risk Stats Reset")
            
        if user_id not in self.daily_start_equity:
            self.daily_start_equity[user_id] = current_equity

    def update_pnl(self, user_id: str, realized_pnl: float):
        """Update Daily PnL tracker"""
        current = self.daily_pnl.get(user_id, 0)
        self.daily_pnl[user_id] = current + realized_pnl

# Singleton Instance
risk_manager = RiskManager()
