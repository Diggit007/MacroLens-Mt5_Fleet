import logging
import asyncio
from typing import List, Dict, Optional
from pathlib import Path
from dataclasses import dataclass
from backend.core.database import DatabasePool
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("NewsRetriever")

@dataclass
class Article:
    title: str
    publish_date: str
    summary: str
    url: str
    source: str
    currency: str

class NewsRetriever:
    """
    Async Context-Aware News Retrieval Service.
    Enforces the 'Top 3 Per Currency' rule for any given symbol.
    Uses DatabasePool for high-concurrency access.
    """
    
    def __init__(self):
        # Uses Singleton DatabasePool
        pass

    async def get_article_context(self, symbol: str, simulated_time: datetime = None) -> Dict[str, List[Article]]:
        """
        Main Entry Point (Async).
        Input: "EURUSD"
        Output: { "EUR": [Art1, Art2, Art3], "USD": [Art4, Art5, Art6] }
        """
        currencies = self._split_symbol(symbol)
        context = {}
        
        for curr in currencies:
            articles = await self._fetch_top_articles(curr, limit=3, simulated_time=simulated_time)
            if articles:
                context[curr] = articles
            else:
                # Fallback logic could go here
                pass
                
        return context

    def _split_symbol(self, symbol: str) -> List[str]:
        """
        Splits standard Forex pairs (EURUSD) or Commodities (XAUUSD).
        """
        symbol = symbol.upper().replace("/", "").strip()
        
        # Standard 6-char pairs
        if len(symbol) == 6:
            return [symbol[:3], symbol[3:]]
            
        # Gold/Silver
        if "XAU" in symbol: return ["XAU", "USD"] 
        if "XAG" in symbol: return ["XAG", "USD"]
        
        # Indices (US30, SPX500) - Naive mapping
        if "US" in symbol or "SPX" in symbol or "NAS" in symbol:
            return ["USD"]
        if "GER" in symbol or "DE30" in symbol:
            return ["EUR"]
            
        return [symbol] # Default fallback

    async def _fetch_top_articles(self, currency: str, limit: int = 3, simulated_time: datetime = None) -> List[Article]:
        """
        Queries DB for latest articles matching the currency.
        Uses DatabasePool for async access to prevent blocking the event loop.
        """
        try:
            # Query logic: 
            # 1. Matches exact currency column
            # 2. OR title contains currency (for untagged usage)
            # Ordered by publish_date DESC (Primary) then scraped_at (Secondary)
            
            # NOTE: DatabasePool returns tuples (unless row_factory set globally), 
            # but to be safe we access by index properly.
            query = """
                SELECT title, publish_date, summary, url, source, currency
                FROM articles
                WHERE currency = ? 
                   OR title LIKE ? 
                   OR content LIKE ?
                ORDER BY date(publish_date) DESC, scraped_at DESC
                LIMIT ?
            """
            
            wildcard = f"%{currency}%"
            params = (currency, wildcard, wildcard, limit)

            if simulated_time:
                query = """
                    SELECT title, publish_date, summary, url, source, currency
                    FROM articles
                    WHERE (currency = ? OR title LIKE ? OR content LIKE ?)
                    AND publish_date <= ?
                    ORDER BY date(publish_date) DESC, scraped_at DESC
                    LIMIT ?
                """
                # SQLite Date format usually YYYY-MM-DD
                sim_date_str = simulated_time.strftime("%Y-%m-%d %H:%M:%S")
                params = (currency, wildcard, wildcard, sim_date_str, limit)
            
            rows = await DatabasePool.fetch_all(query, params)
            
            articles = []
            for row in rows:
                # row is tuple: (title, publish_date, summary, url, source, currency)
                articles.append(Article(
                    title=row[0],
                    publish_date=row[1],
                    summary=row[2],
                    url=row[3],
                    source=row[4],
                    currency=row[5] or currency
                ))
            
            return articles
            
        except Exception as e:
            logger.error(f"Error fetching for {currency}: {e}")
            return []

# Helper for testing
if __name__ == "__main__":
    async def main():
        retriever = NewsRetriever()
        res = await retriever.get_article_context("EURUSD")
        print(res)
        await DatabasePool.close()
    
    # asyncio.run(main())
