import asyncio
import logging
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent.parent))

from crawl4ai import AsyncWebCrawler
from backend.core.database import DatabasePool

logger = logging.getLogger("FXStreetScraper")
logging.basicConfig(level=logging.INFO)

# Suppress noisy library logs
logging.getLogger("crawl4ai").setLevel(logging.WARNING)

class FXStreetScraper:
    """
    Scrapes FXStreet news for specific currency pairs using crawl4ai.
    Runs 3x daily (8AM, 11AM, 1PM) via Windows Task Scheduler.
    """
    
    def __init__(self):
        self.pairs_map = {
            "usdjpy": "JPY",
            "eurusd": "EUR",
            "audusd": "AUD",
            "gbpusd": "GBP",
            "nzdusd": "NZD",
            "usdchf": "CHF",
            "usdcad": "CAD",
        }
        self.base_url = "https://www.fxstreet.com/currencies/{}"
        self.page_timeout = 30  # seconds per page

    async def run(self):
        """Main entry point to scrape all configured pairs."""
        async with AsyncWebCrawler(verbose=False) as crawler:
            for pair, currency in self.pairs_map.items():
                url = self.base_url.format(pair)
                logger.info(f"Scraping {pair} -> ({currency})")
                
                try:
                    # Timeout per page to prevent hanging
                    result = await asyncio.wait_for(
                        crawler.arun(url=url),
                        timeout=self.page_timeout
                    )
                    
                    if not result.success:
                        logger.warning(f"Failed to crawl {url}")
                        continue
                    
                    articles_found = 0
                    if hasattr(result, 'links'):
                        internal_links = result.links.get("internal", [])
                        
                        # Filter for news/analysis article links with meaningful titles
                        news_links = [
                            l for l in internal_links
                            if ("/news/" in l.get('href', '') or "/analysis/" in l.get('href', ''))
                            and len(l.get('text', '')) > 15
                        ]
                        
                        # Deduplicate by href
                        seen = set()
                        unique_links = []
                        for link in news_links:
                            href = link.get('href', '')
                            if href not in seen:
                                seen.add(href)
                                unique_links.append(link)
                        
                        # Save top 5
                        for link in unique_links[:5]:
                            title = link.get('text', '').strip()
                            article_url = link.get('href', '')
                            if not article_url.startswith("http"):
                                article_url = "https://www.fxstreet.com" + article_url
                            
                            await self._save_article(title, article_url, currency)
                            articles_found += 1
                            
                    logger.info(f"  -> {articles_found} articles saved for {currency}")

                except asyncio.TimeoutError:
                    logger.warning(f"Timeout scraping {pair} (>{self.page_timeout}s). Skipping.")
                except Exception as e:
                    logger.error(f"Error scraping {pair}: {e}")

    async def _save_article(self, title: str, url: str, currency: str):
        """Saves the article to the DB (upsert: update currency if exists)."""
        check_query = "SELECT id FROM articles WHERE url = ?"
        exists = await DatabasePool.fetch_one(check_query, (url,))
        
        now_str = datetime.utcnow().isoformat()
        
        if exists:
            # Update currency mapping (fixes generic USD tagging from other scrapers)
            update_query = "UPDATE articles SET currency = ?, scraped_at = ? WHERE url = ?"
            await DatabasePool.execute_commit(update_query, (currency, now_str, url))
        else:
            insert_query = """
                INSERT INTO articles (title, url, source, currency, publish_date, summary, content, scraped_at)
                VALUES (?, ?, 'FXStreet', ?, ?, ?, ?, ?)
            """
            await DatabasePool.execute_commit(
                insert_query, 
                (title, url, currency, now_str, title, title, now_str)
            )

if __name__ == "__main__":
    async def main():
        scraper = FXStreetScraper()
        await scraper.run()
        
    asyncio.run(main())
