
import logging
import sqlite3
import pandas as pd
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass

from backend.services.event_predictor import EventPredictor

logger = logging.getLogger("BacktestEngine")

@dataclass
class BacktestResult:
    total_events: int
    trades_taken: int
    win_rate: float
    total_pips: float
    profit_factor: float
    best_trade: float
    worst_trade: float
    fundamental_accuracy: float # New metric
    by_confidence: Dict
    equity_curve: List[float]

class BacktestEngine:
    """
    Simulates historical event trading to measure predictive performance.
    """
    
    def __init__(self, db_path: str = "C:/MacroLens/backend/market_data.db"):
        self.db_path = db_path
        self.predictor = EventPredictor(Path(db_path))
        
    def run_backtest(self, min_confidence: str = "MEDIUM") -> BacktestResult:
        """
        Run backtest on all available event reactions.
        """
        conn = sqlite3.connect(self.db_path)
        
        # Join reactions with event data to get inputs (forecast/previous)
        # Note: We match on Name, Date, Currency. Time might differ slightly so we ignore it or be careful.
        query = """
            SELECT 
                r.event_name, 
                r.event_date, 
                r.currency, 
                r.symbol, 
                r.h1_change_pips,
                e.forecast_value, 
                e.previous_value,
                e.actual_value
            FROM event_reactions r
            JOIN economic_events e 
              ON r.event_name = e.event_name 
              AND r.event_date = e.event_date 
              AND r.currency = e.currency
            WHERE e.forecast_value IS NOT NULL
            ORDER BY r.event_date ASC
        """
        
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        if df.empty:
            logger.warning("No linked event data found for backtest.")
            return None
            
        logger.info(f"Loaded {len(df)} events for backtesting...")
        
        results = []
        equity = [1000.0] # Mock starting balance or just pip accumulation
        cum_pips = 0.0
        
        # Iterate history
        for idx, row in df.iterrows():
            event_date = row['event_date']
            
            # 1. Generate Prediction (Time Travel Mode)
            try:
                prediction = self.predictor.predict_event(
                    event_name=row['event_name'],
                    forecast=row['forecast_value'],
                    previous=row['previous_value'],
                    currency=row['currency'],
                    simulation_date=event_date # <--- CRITICAL: Hides future data
                )
            except Exception as e:
                logger.error(f"Prediction error for {row['event_name']}: {e}")
                continue
                
            # Filter by confidence
            if self._score_confidence(prediction.confidence) < self._score_confidence(min_confidence):
                continue
                
            # 2. Determine Trade Direction
            # Predictor output: "BEAT" or "MISS" (or NEUTRAL)
            if prediction.predicted_outcome == "NEUTRAL":
                continue
                
            impact = self.predictor.get_symbol_impact(
                row['currency'], 
                prediction.predicted_outcome, 
                row['symbol']
            )
            
            trade_direction = impact['direction'] # BULLISH or BEARISH
            if trade_direction == "NEUTRAL":
                continue
                
            # 3. Evaluate Result
            
            # A. Fundamental Accuracy (Did we predict Beat/Miss correctly?)
            actual_val = row['actual_value']
            forecast_val = row['forecast_value']
            
            fundamental_win = False
            if actual_val is not None and forecast_val is not None:
                real_outcome = "BEAT" if actual_val > forecast_val else "MISS" if actual_val < forecast_val else "NEUTRAL"
                if prediction.predicted_outcome == real_outcome:
                    fundamental_win = True
            
            # B. Price Accuracy
            actual_move = row['h1_change_pips']
            
            # Did we win on price?
            
            pnl = 0
            if trade_direction == "BULLISH":
                pnl = actual_move
            elif trade_direction == "BEARISH":
                pnl = -actual_move
            
            # Store result
            cum_pips += pnl
            equity.append(equity[-1] + pnl) # Simple pip equity curve
            
            results.append({
                "date": event_date,
                "event": row['event_name'],
                "symbol": row['symbol'],
                "prediction": prediction.predicted_outcome,
                "confidence": prediction.confidence,
                "trade": "LONG" if trade_direction == "BULLISH" else "SHORT",
                "actual_move": actual_move,
                "pnl": pnl,
                "win": pnl > 0,
                "fundamental_win": fundamental_win
            })
            
            if len(results) % 50 == 0:
                logger.info(f"Processed {len(results)} trades...")
                
        # 4. Compile Stats
        if not results:
            return BacktestResult(0, 0, 0, 0, 0, 0, 0, {}, [])
            
        wins = [r for r in results if r['win']]
        losses = [r for r in results if not r['win']]
        fundamental_wins = [r for r in results if r['fundamental_win']]
        
        win_rate = len(wins) / len(results) if results else 0
        fundamental_accuracy = len(fundamental_wins) / len(results) if results else 0
        total_pips = sum(r['pnl'] for r in results)
        
        gross_profit = sum(r['pnl'] for r in wins)
        gross_loss = abs(sum(r['pnl'] for r in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.0
        
        # Group by confidence
        by_conf = {}
        for conf in ["HIGH", "MEDIUM", "LOW"]:
            subset = [r for r in results if r['confidence'] == conf]
            if subset:
                sub_wins = len([r for r in subset if r['win']])
                by_conf[conf] = {
                    "count": len(subset),
                    "win_rate": sub_wins / len(subset),
                    "pips": sum(r['pnl'] for r in subset)
                }
                
        return BacktestResult(
            total_events=len(df),
            trades_taken=len(results),
            win_rate=win_rate,
            total_pips=total_pips,
            profit_factor=profit_factor,
            best_trade=max(r['pnl'] for r in results),
            worst_trade=min(r['pnl'] for r in results),
            fundamental_accuracy=fundamental_accuracy,
            by_confidence=by_conf,
            equity_curve=equity
        )

    def _score_confidence(self, conf: str) -> int:
        return {"LOW": 1, "MEDIUM": 2, "HIGH": 3}.get(conf, 0)

