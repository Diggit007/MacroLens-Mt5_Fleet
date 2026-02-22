import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

# Config
BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "market_data.db"

logger = logging.getLogger("MarketContext")

class MarketContext:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MarketContext, cls).__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self.db_path = DB_PATH

    def get_connection(self):
        return sqlite3.connect(self.db_path)

    def get_active_sessions(self) -> List[str]:
        """
        Returns active market sessions based on UTC time.
        Simplified schedule (UTC):
        - Sydney: 22:00 - 07:00
        - Tokyo: 00:00 - 09:00
        - London: 08:00 - 17:00
        - New York: 13:00 - 22:00
        """
        now = datetime.utcnow()
        hour = now.hour
        
        sessions = []
        if 22 <= hour or hour < 7:
            sessions.append("Sydney")
        if 0 <= hour < 9:
            sessions.append("Tokyo")
        if 8 <= hour < 17:
            sessions.append("London")
        if 13 <= hour < 22:
            sessions.append("New York")
            
        return sessions

    def get_session_status_message(self) -> Optional[str]:
        """Returns a message about session transitions (Open/Close)"""
        now = datetime.utcnow()
        hour = now.hour
        minute = now.minute
        
        # Check for Closes (Liquidity Drop) - 1 hour before close
        # London Close (16:00 - 17:00 UTC)
        if hour == 16:
            return "⚠️ London Session closing in 1 hour. Liquidity may drop."
        # NY Close (21:00 - 22:00 UTC)
        if hour == 21:
             return "⚠️ US Session closing. Sprads may widen."
             
        # Check for Opens (Volatility Spike)
        # London Open (08:00)
        if hour == 8 and minute < 30:
            return "🔔 London Session Just Opened. Expect volatility."
        # NY Open (13:00)
        if hour == 13 and minute < 30:
            return "🔔 New York Session Just Opened. High impact probable."
            
        return None

    def get_upcoming_news(self, currency: str, minutes: int = 60) -> List[Dict]:
        """Check for high/moderate impact news for a currency in the next X minutes"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            now = datetime.utcnow()
            future = now + timedelta(minutes=minutes)
            
            # Format dates for query
            # DB stores event_date as YYYY-MM-DD and event_time as HH:MM
            # simple filter: Select current date and next date
            
            query = """
                SELECT event_name, event_time, impact_level, currency 
                FROM economic_events
                WHERE currency = ? 
                AND impact_level IN ('High', 'Moderate')
                AND event_date = ?
            """
            
            date_str = now.strftime("%Y-%m-%d")
            cursor.execute(query, (currency, date_str))
            rows = cursor.fetchall()
            
            # Filter by time manually to handle exact time diffs
            relevant_news = []
            for r in rows:
                event_name, event_time_str, impact, curr = r
                try:
                    # Construct full dt
                    if event_time_str == "All Day":
                        continue
                        
                    event_dt = datetime.strptime(f"{date_str} {event_time_str}", "%Y-%m-%d %H:%M")
                    
                    if now <= event_dt <= future:
                        relevant_news.append({
                            "event": event_name,
                            "time": event_time_str,
                            "impact": impact,
                            "minutes_until": int((event_dt - now).total_seconds() / 60)
                        })
                except Exception as e:
                    logger.warning(f"Date parse error: {e}")
                    continue
                    
            return relevant_news
            
        except Exception as e:
            logger.error(f"News check error: {e}")
            return []
        finally:
            conn.close()

# Singleton Instance
market_context = MarketContext()
