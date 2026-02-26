"""
Event Reactions Database
========================
Creates and manages the event_reactions table for storing
historical price movements at economic event times.

This enables correlation analysis:
"When US CPI beats, EURUSD drops 85% of the time with avg -40 pips"

Usage:
    python event_reactions_db.py setup   # Create table
    python event_reactions_db.py capture # Capture reactions for past events (requires price data)
"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger("EventReactionsDB")

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "market_data.db"


def create_event_reactions_table(db_path: Path = DB_PATH):
    """
    Create the event_reactions table.
    
    Schema:
    - event_id: References economic_events (composite key: event_name + event_date)
    - symbol: Trading pair (e.g., EURUSD)
    - release_price: Price at event release time
    - m1_change_pips: Price change after 1 minute
    - m5_change_pips: Price change after 5 minutes
    - h1_change_pips: Price change after 1 hour
    - h4_change_pips: Price change after 4 hours
    - reaction_direction: BULLISH / BEARISH / NEUTRAL
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS event_reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_name TEXT NOT NULL,
            event_date TEXT NOT NULL,
            event_time TEXT,
            currency TEXT,
            symbol TEXT NOT NULL,
            
            -- Price at event release
            release_price REAL,
            
            -- Price changes (in pips)
            m1_change_pips REAL,
            m5_change_pips REAL,
            m15_change_pips REAL,
            h1_change_pips REAL,
            h4_change_pips REAL,
            
            -- Derived fields
            reaction_direction TEXT,  -- BULLISH, BEARISH, NEUTRAL
            deviation_category TEXT,  -- BIG_BEAT, SMALL_BEAT, IN_LINE, etc.
            
            -- Metadata
            captured_at TEXT DEFAULT CURRENT_TIMESTAMP,
            
            -- Unique constraint
            UNIQUE(event_name, event_date, symbol)
        )
    """)
    
    # Create indexes for fast lookups
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_event_reactions_event_name 
        ON event_reactions(event_name)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_event_reactions_symbol 
        ON event_reactions(symbol)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_event_reactions_currency 
        ON event_reactions(currency)
    """)
    
    conn.commit()
    logger.info("event_reactions table created successfully")
    
    # Show table info
    cursor.execute("SELECT COUNT(*) FROM event_reactions")
    count = cursor.fetchone()[0]
    logger.info(f"Current reaction records: {count}")
    
    conn.close()
    return True


def insert_reaction(db_path: Path, reaction: Dict) -> bool:
    """
    Insert a price reaction record.
    
    Args:
        reaction: Dict with keys:
            - event_name, event_date, event_time, currency
            - symbol, release_price
            - m1_change_pips, m5_change_pips, h1_change_pips, h4_change_pips
            - reaction_direction, deviation_category
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT OR REPLACE INTO event_reactions (
                event_name, event_date, event_time, currency, symbol,
                release_price, m1_change_pips, m5_change_pips, m15_change_pips,
                h1_change_pips, h4_change_pips,
                reaction_direction, deviation_category
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            reaction.get("event_name"),
            reaction.get("event_date"),
            reaction.get("event_time"),
            reaction.get("currency"),
            reaction.get("symbol"),
            reaction.get("release_price"),
            reaction.get("m1_change_pips"),
            reaction.get("m5_change_pips"),
            reaction.get("m15_change_pips"),
            reaction.get("h1_change_pips"),
            reaction.get("h4_change_pips"),
            reaction.get("reaction_direction"),
            reaction.get("deviation_category")
        ))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to insert reaction: {e}")
        return False
    finally:
        conn.close()


def get_reactions_for_event(db_path: Path, event_name: str, 
                             symbol: str = None, limit: int = 50) -> List[Dict]:
    """
    Get historical price reactions for an event.
    
    Args:
        event_name: Partial or full event name
        symbol: Optional symbol filter (e.g., EURUSD)
        limit: Maximum results
        
    Returns:
        List of reaction records
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    query = """
        SELECT event_name, event_date, event_time, currency, symbol,
               release_price, m1_change_pips, m5_change_pips, m15_change_pips,
               h1_change_pips, h4_change_pips,
               reaction_direction, deviation_category
        FROM event_reactions
        WHERE event_name LIKE ?
    """
    params = [f"%{event_name}%"]
    
    if symbol:
        query += " AND symbol = ?"
        params.append(symbol)
        
    query += " ORDER BY event_date DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {
            "event_name": r[0],
            "event_date": r[1],
            "event_time": r[2],
            "currency": r[3],
            "symbol": r[4],
            "release_price": r[5],
            "m1_change_pips": r[6],
            "m5_change_pips": r[7],
            "m15_change_pips": r[8],
            "h1_change_pips": r[9],
            "h4_change_pips": r[10],
            "reaction_direction": r[11],
            "deviation_category": r[12]
        }
        for r in rows
    ]


def calculate_reaction_stats(db_path: Path, event_name: str, 
                              symbol: str) -> Dict:
    """
    Calculate aggregate reaction statistics for an event-symbol pair.
    
    Returns:
        Dict with avg_move_pips, bullish_rate, typical_reaction
    """
    reactions = get_reactions_for_event(db_path, event_name, symbol)
    
    if len(reactions) < 3:
        return {
            "sample_size": len(reactions),
            "sufficient_data": False,
            "avg_h1_move": 0,
            "bullish_rate": 0.5,
            "typical_direction": "NEUTRAL"
        }
    
    h1_moves = [r["h1_change_pips"] for r in reactions if r["h1_change_pips"] is not None]
    bullish_count = sum(1 for r in reactions if r["reaction_direction"] == "BULLISH")
    
    avg_move = sum(h1_moves) / len(h1_moves) if h1_moves else 0
    bullish_rate = bullish_count / len(reactions)
    
    return {
        "sample_size": len(reactions),
        "sufficient_data": len(reactions) >= 10,
        "avg_h1_move": round(avg_move, 1),
        "bullish_rate": round(bullish_rate, 2),
        "typical_direction": "BULLISH" if bullish_rate > 0.55 else "BEARISH" if bullish_rate < 0.45 else "NEUTRAL"
    }


# =============================================================================
# LIVE CAPTURE INTEGRATION
# =============================================================================

class ReactionCapture:
    """
    Captures price reactions at event release times.
    
    Integration point: Call this from the event monitoring system
    when a High Impact event is released.
    """
    
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        
    def capture_reaction(self, event_data: Dict, price_data: Dict) -> bool:
        """
        Capture a price reaction after an event release.
        
        Args:
            event_data: Dict with event_name, event_date, event_time, currency, deviation_category
            price_data: Dict with symbol, release_price, m1_price, m5_price, h1_price, h4_price
            
        Returns:
            True if captured successfully
        """
        symbol = price_data.get("symbol", "")
        release = price_data.get("release_price", 0)
        
        # Calculate pip changes (assuming 4 decimal pairs, adjust for JPY pairs)
        pip_multiplier = 100 if "JPY" in symbol else 10000
        
        reaction = {
            "event_name": event_data.get("event_name"),
            "event_date": event_data.get("event_date"),
            "event_time": event_data.get("event_time"),
            "currency": event_data.get("currency"),
            "symbol": symbol,
            "release_price": release,
            "m1_change_pips": self._calc_pips(release, price_data.get("m1_price"), pip_multiplier),
            "m5_change_pips": self._calc_pips(release, price_data.get("m5_price"), pip_multiplier),
            "m15_change_pips": self._calc_pips(release, price_data.get("m15_price"), pip_multiplier),
            "h1_change_pips": self._calc_pips(release, price_data.get("h1_price"), pip_multiplier),
            "h4_change_pips": self._calc_pips(release, price_data.get("h4_price"), pip_multiplier),
            "deviation_category": event_data.get("deviation_category")
        }
        
        # Determine reaction direction from H1 move
        h1 = reaction["h1_change_pips"]
        if h1 and h1 > 5:
            reaction["reaction_direction"] = "BULLISH"
        elif h1 and h1 < -5:
            reaction["reaction_direction"] = "BEARISH"
        else:
            reaction["reaction_direction"] = "NEUTRAL"
            
        return insert_reaction(self.db_path, reaction)
    
    def _calc_pips(self, release: float, current: float, multiplier: int) -> Optional[float]:
        if release is None or current is None:
            return None
        return round((current - release) * multiplier, 1)


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        
        if cmd == "setup":
            create_event_reactions_table()
            print("✅ event_reactions table created")
            
        elif cmd == "stats":
            # Example usage
            event = sys.argv[2] if len(sys.argv) > 2 else "CPI"
            symbol = sys.argv[3] if len(sys.argv) > 3 else "EURUSD"
            stats = calculate_reaction_stats(DB_PATH, event, symbol)
            print(f"Stats for {event} on {symbol}:")
            print(f"  Sample Size: {stats['sample_size']}")
            print(f"  Avg H1 Move: {stats['avg_h1_move']} pips")
            print(f"  Bullish Rate: {stats['bullish_rate']:.0%}")
            print(f"  Typical Direction: {stats['typical_direction']}")
            
        else:
            print("Usage: python event_reactions_db.py [setup|stats <event> <symbol>]")
    else:
        # Default: setup
        create_event_reactions_table()
