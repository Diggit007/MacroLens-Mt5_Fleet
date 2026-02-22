"""
MT5 Event Reactions Backfill
============================
Connects to MT5 directly to fetch historical price data for economic events
and populates the event_reactions table.

Requirements:
- MetaTrader5 Python package: pip install MetaTrader5
- MT5 terminal running on the server

Usage:
    python backfill_mt5_reactions.py              # Backfill all events
    python backfill_mt5_reactions.py --symbol EURUSD --limit 100
    python backfill_mt5_reactions.py --event "CPI" --symbol EURUSD
"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import argparse
import time

# Try to import MT5
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    print("WARNING: MetaTrader5 package not installed. Run: pip install MetaTrader5")

logger = logging.getLogger("MT5Backfill")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "market_data.db"

# Symbols to backfill
DEFAULT_SYMBOLS = ["EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "USDCAD", "USDCHF"]

# Pip multipliers
PIP_MULTIPLIER = {
    "EURUSD": 10000, "GBPUSD": 10000, "AUDUSD": 10000, "NZDUSD": 10000,
    "USDCAD": 10000, "USDCHF": 10000, "EURGBP": 10000, "EURJPY": 100,
    "USDJPY": 100, "GBPJPY": 100, "AUDJPY": 100, "CADJPY": 100,
    "XAUUSD": 10,  # Gold
    "BTCUSD": 1,   # Bitcoin
}


class MT5BackfillService:
    """
    Service to backfill event_reactions from MT5 historical data.
    """
    
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.mt5_connected = False
        
    def connect_mt5(self) -> bool:
        """Initialize connection to MT5."""
        if not MT5_AVAILABLE:
            logger.error("MetaTrader5 package not available")
            return False
            
        if not mt5.initialize():
            logger.error(f"MT5 initialization failed: {mt5.last_error()}")
            return False
            
        logger.info(f"MT5 connected: {mt5.terminal_info().name}")
        self.mt5_connected = True
        return True
    
    def disconnect_mt5(self):
        """Shutdown MT5 connection."""
        if MT5_AVAILABLE and self.mt5_connected:
            mt5.shutdown()
            self.mt5_connected = False
            logger.info("MT5 disconnected")
    
    def get_events_to_backfill(self, event_filter: str = None, 
                                currency_filter: str = None,
                                limit: int = 500) -> List[Dict]:
        """
        Get economic events that have actual values (released events).
        
        Args:
            event_filter: Optional event name filter (LIKE match)
            currency_filter: Optional currency filter
            limit: Maximum events to fetch
            
        Returns:
            List of event dictionaries
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        query = """
            SELECT event_name, event_date, event_time, currency,
                   actual_value, forecast_value, previous_value, impact_level
            FROM economic_events
            WHERE actual_value IS NOT NULL
            AND impact_level = 'High'
        """
        params = []
        
        if event_filter:
            query += " AND event_name LIKE ?"
            params.append(f"%{event_filter}%")
            
        if currency_filter:
            query += " AND currency = ?"
            params.append(currency_filter)
            
        query += " ORDER BY event_date DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        return [
            {
                "event_name": r[0],
                "event_date": r[1],
                "event_time": r[2] or "12:00",
                "currency": r[3],
                "actual": r[4],
                "forecast": r[5],
                "previous": r[6],
                "impact": r[7]
            }
            for r in rows
        ]
    
    def get_price_at_time(self, symbol: str, dt: datetime, 
                          timeframe=None) -> Optional[float]:
        """
        Get the closing price at a specific time.
        
        Args:
            symbol: Trading symbol (e.g., EURUSD)
            dt: Target datetime
            timeframe: MT5 timeframe (default M5)
            
        Returns:
            Close price or None
        """
        if not self.mt5_connected:
            return None
            
        if timeframe is None:
            timeframe = mt5.TIMEFRAME_M5
            
        # Fetch a few candles around the target time
        rates = mt5.copy_rates_from(symbol, timeframe, dt, 5)
        
        if rates is None or len(rates) == 0:
            return None
            
        # Return the close of the most recent candle at or before target time
        return float(rates[-1]['close'])
    
    def get_candle_at_time(self, symbol: str, dt: datetime,
                           timeframe) -> Optional[Dict]:
        """
        Get the full candle data at a specific time.
        
        Returns:
            Dict with open, high, low, close, time
        """
        if not self.mt5_connected:
            return None
            
        rates = mt5.copy_rates_from(symbol, timeframe, dt, 1)
        
        if rates is None or len(rates) == 0:
            return None
            
        r = rates[0]
        return {
            "time": datetime.fromtimestamp(r['time']),
            "open": float(r['open']),
            "high": float(r['high']),
            "low": float(r['low']),
            "close": float(r['close']),
            "volume": int(r['tick_volume'])
        }
    
    def calculate_pip_change(self, symbol: str, price1: float, 
                              price2: float) -> float:
        """Calculate pip change between two prices."""
        multiplier = PIP_MULTIPLIER.get(symbol, 10000)
        return round((price2 - price1) * multiplier, 1)
    
    def classify_deviation(self, forecast: float, actual: float) -> str:
        """Classify the event outcome."""
        if forecast == 0:
            return "IN_LINE"
            
        deviation_pct = (actual - forecast) / abs(forecast)
        
        if deviation_pct > 0.15:
            return "BIG_BEAT"
        elif deviation_pct > 0.05:
            return "SMALL_BEAT"
        elif deviation_pct < -0.15:
            return "BIG_MISS"
        elif deviation_pct < -0.05:
            return "SMALL_MISS"
        return "IN_LINE"
    
    def backfill_event(self, event: Dict, symbol: str) -> Optional[Dict]:
        """
        Backfill price reaction for a single event.
        
        Args:
            event: Event dictionary
            symbol: Trading symbol
            
        Returns:
            Reaction dictionary or None
        """
        try:
            # Parse event datetime (Handle HH:MM and HH:MM:SS)
            time_str = event['event_time']
            if len(time_str.split(':')) == 2:
                fmt = "%Y-%m-%d %H:%M"
            else:
                fmt = "%Y-%m-%d %H:%M:%S"
                
            event_dt = datetime.strptime(
                f"{event['event_date']} {time_str}", 
                fmt
            )
        except ValueError as e:
            logger.warning(f"Invalid datetime for {event['event_name']}: {event['event_date']} {event['event_time']} ({e})")
            return None
        
        # Get prices at various intervals
        release_price = self.get_price_at_time(symbol, event_dt)
        
        if release_price is None:
            logger.debug(f"No price data for {symbol} at {event_dt}")
            return None
        
        # Get prices at T+5m, T+15m, T+1H, T+4H
        m5_price = self.get_price_at_time(symbol, event_dt + timedelta(minutes=5))
        m15_price = self.get_price_at_time(symbol, event_dt + timedelta(minutes=15))
        h1_price = self.get_price_at_time(symbol, event_dt + timedelta(hours=1))
        h4_price = self.get_price_at_time(symbol, event_dt + timedelta(hours=4))
        
        # Calculate pip changes
        m5_pips = self.calculate_pip_change(symbol, release_price, m5_price) if m5_price else None
        m15_pips = self.calculate_pip_change(symbol, release_price, m15_price) if m15_price else None
        h1_pips = self.calculate_pip_change(symbol, release_price, h1_price) if h1_price else None
        h4_pips = self.calculate_pip_change(symbol, release_price, h4_price) if h4_price else None
        
        # Determine reaction direction (based on H1 move)
        if h1_pips is not None:
            if h1_pips > 10:
                direction = "BULLISH"
            elif h1_pips < -10:
                direction = "BEARISH"
            else:
                direction = "NEUTRAL"
        else:
            direction = None
        
        # Classify deviation
        deviation_category = self.classify_deviation(
            event.get('forecast', 0) or 0,
            event.get('actual', 0) or 0
        )
        
        return {
            "event_name": event['event_name'],
            "event_date": event['event_date'],
            "event_time": event['event_time'],
            "currency": event['currency'],
            "symbol": symbol,
            "release_price": release_price,
            "m5_change_pips": m5_pips,
            "m15_change_pips": m15_pips,
            "h1_change_pips": h1_pips,
            "h4_change_pips": h4_pips,
            "reaction_direction": direction,
            "deviation_category": deviation_category
        }
    
    def save_reaction(self, reaction: Dict) -> bool:
        """Save a reaction to the database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO event_reactions (
                    event_name, event_date, event_time, currency, symbol,
                    release_price, m5_change_pips, m15_change_pips,
                    h1_change_pips, h4_change_pips,
                    reaction_direction, deviation_category
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                reaction['event_name'],
                reaction['event_date'],
                reaction['event_time'],
                reaction['currency'],
                reaction['symbol'],
                reaction['release_price'],
                reaction.get('m5_change_pips'),
                reaction.get('m15_change_pips'),
                reaction.get('h1_change_pips'),
                reaction.get('h4_change_pips'),
                reaction.get('reaction_direction'),
                reaction.get('deviation_category')
            ))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to save reaction: {e}")
            return False
        finally:
            conn.close()
    
    def run_backfill(self, symbols: List[str] = None, 
                     event_filter: str = None,
                     currency_filter: str = None,
                     limit: int = 500) -> Dict:
        """
        Run the full backfill process.
        
        Args:
            symbols: List of symbols to process
            event_filter: Filter by event name
            currency_filter: Filter by currency
            limit: Max events to process
            
        Returns:
            Stats dictionary
        """
        if symbols is None:
            symbols = DEFAULT_SYMBOLS
            
        # Connect to MT5
        if not self.connect_mt5():
            return {"error": "Failed to connect to MT5"}
        
        try:
            # Get events
            events = self.get_events_to_backfill(event_filter, currency_filter, limit)
            logger.info(f"Found {len(events)} events to backfill")
            
            stats = {
                "events_processed": 0,
                "reactions_saved": 0,
                "errors": 0,
                "symbols": symbols
            }
            
            for i, event in enumerate(events):
                if i % 50 == 0:
                    logger.info(f"Processing event {i+1}/{len(events)}: {event['event_name']}")
                
                # Determine relevant symbols for this event's currency
                relevant_symbols = self._get_relevant_symbols(event['currency'], symbols)
                
                for symbol in relevant_symbols:
                    reaction = self.backfill_event(event, symbol)
                    
                    if reaction:
                        if self.save_reaction(reaction):
                            stats["reactions_saved"] += 1
                        else:
                            stats["errors"] += 1
                    else:
                        stats["errors"] += 1
                
                stats["events_processed"] += 1
                
                # Rate limit MT5 requests
                time.sleep(0.1)
            
            return stats
            
        finally:
            self.disconnect_mt5()
    
    def _get_relevant_symbols(self, currency: str, symbols: List[str]) -> List[str]:
        """Get symbols that contain the event's currency."""
        return [s for s in symbols if currency in s]


def calculate_correlation_stats(db_path: Path = DB_PATH) -> Dict:
    """
    Calculate correlation statistics from the reactions data.
    
    Returns:
        Dict with correlation analysis per event/symbol pair
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Get unique event/symbol pairs with sufficient data
    cursor.execute("""
        SELECT event_name, symbol, 
               COUNT(*) as sample_size,
               AVG(h1_change_pips) as avg_h1_move,
               SUM(CASE WHEN reaction_direction = 'BULLISH' THEN 1 ELSE 0 END) as bullish_count,
               SUM(CASE WHEN deviation_category IN ('BIG_BEAT', 'SMALL_BEAT') THEN 1 ELSE 0 END) as beat_count
        FROM event_reactions
        WHERE h1_change_pips IS NOT NULL
        GROUP BY event_name, symbol
        HAVING COUNT(*) >= 5
        ORDER BY sample_size DESC
    """)
    
    results = []
    for row in cursor.fetchall():
        event_name, symbol, sample_size, avg_move, bullish_count, beat_count = row
        
        bullish_rate = bullish_count / sample_size if sample_size > 0 else 0.5
        beat_rate = beat_count / sample_size if sample_size > 0 else 0.5
        
        # Determine correlation direction
        # If beats tend to result in bullish moves, correlation is POSITIVE
        # (This is simplified; full correlation requires per-event analysis)
        
        results.append({
            "event_name": event_name,
            "symbol": symbol,
            "sample_size": sample_size,
            "avg_h1_move": round(avg_move, 1) if avg_move else 0,
            "bullish_rate": round(bullish_rate, 2),
            "beat_rate": round(beat_rate, 2),
            "typical_direction": "BULLISH" if bullish_rate > 0.55 else "BEARISH" if bullish_rate < 0.45 else "NEUTRAL"
        })
    
    conn.close()
    return results


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill event reactions from MT5")
    parser.add_argument("--symbol", type=str, help="Specific symbol to process")
    parser.add_argument("--event", type=str, help="Filter by event name")
    parser.add_argument("--currency", type=str, help="Filter by currency")
    parser.add_argument("--limit", type=int, default=500, help="Max events to process")
    parser.add_argument("--stats", action="store_true", help="Show correlation stats")
    
    args = parser.parse_args()
    
    if args.stats:
        print("\n=== Correlation Statistics ===")
        stats = calculate_correlation_stats()
        for s in stats[:20]:  # Top 20
            print(f"{s['event_name'][:30]:30} | {s['symbol']:8} | n={s['sample_size']:3} | "
                  f"Avg H1: {s['avg_h1_move']:+6.1f} pips | {s['typical_direction']}")
    else:
        service = MT5BackfillService()
        
        symbols = [args.symbol] if args.symbol else None
        
        print(f"\n=== MT5 Event Reactions Backfill ===")
        print(f"Symbols: {symbols or DEFAULT_SYMBOLS}")
        print(f"Event Filter: {args.event or 'All'}")
        print(f"Currency Filter: {args.currency or 'All'}")
        print(f"Limit: {args.limit}")
        print()
        
        stats = service.run_backfill(
            symbols=symbols,
            event_filter=args.event,
            currency_filter=args.currency,
            limit=args.limit
        )
        
        print(f"\n=== Backfill Complete ===")
        print(f"Events Processed: {stats.get('events_processed', 0)}")
        print(f"Reactions Saved: {stats.get('reactions_saved', 0)}")
        print(f"Errors: {stats.get('errors', 0)}")
        
        if stats.get('reactions_saved', 0) > 0:
            print("\nRun with --stats to see correlation analysis")
