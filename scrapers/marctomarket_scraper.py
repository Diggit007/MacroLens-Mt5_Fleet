import asyncio
import os
import sqlite3
import logging
from datetime import datetime
from crawl4ai import AsyncWebCrawler
from bs4 import BeautifulSoup
import re

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MarcToMarketScraper")

# Suppress noisy library logs that cause Windows encoding errors
logging.getLogger("crawl4ai").setLevel(logging.CRITICAL)
logging.getLogger("rich").setLevel(logging.CRITICAL)
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "market_data.db"

class MarcToMarketScraper:
    def __init__(self):
        self.base_url = "https://www.marctomarket.com/"
        self.init_db()

    def init_db(self):
        """Create articles table if it doesn't exist (Unified Schema)"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id TEXT,
                title TEXT NOT NULL,
                summary TEXT, 
                content TEXT,
                url TEXT NOT NULL UNIQUE,
                publish_date TEXT,
                currency TEXT,
                pair TEXT,
                source TEXT,
                scraped_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    def save_article(self, title, content, date_str, currency="USD", url=None):
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # Simple summary (first 300 chars)
            summary = content[:300] + "..."
            article_id = f"MarcToMarket_{date_str}_{title[:10]}"
            
            cursor.execute("""
                INSERT OR IGNORE INTO articles (title, content, summary, publish_date, currency, source, url, article_id)
                VALUES (?, ?, ?, ?, ?, 'MarcToMarket', ?, ?)
            """, (title, content, summary, date_str, currency, url, article_id))
            
            conn.commit()
            conn.close()
            logger.info(f"Saved article: {title}")
        except Exception as e:
            logger.error(f"Error saving to DB: {e}")

    async def run(self):
        logger.info("Starting MarcToMarket Crawl...")
        
        # Configure crawler to be headless and verbose=False to minimize console noise
        async with AsyncWebCrawler(verbose=False) as crawler:
            # 1. Crawl the Homepage
            result = await crawler.arun(url=self.base_url)
            if not result.success:
                logger.error("Failed to crawl homepage")
                return

            soup = BeautifulSoup(result.html, 'html.parser')
            
            # MarcToMarket: Use URL pattern matching instead of fragile CSS classes
            # We look for links containing current year/month
            current_year = datetime.now().strftime("%Y") 
            soup = BeautifulSoup(result.html, 'html.parser')
            all_links = soup.find_all("a", href=True)
            
            seen_urls = set()
            candidate_posts = []

            for link in all_links:
                href = link['href']
                text = link.get_text(strip=True)
                
                # Filter for blog posts (e.g., /2026/01/some-post.html)
                # Exclude social shares (facebook, twitter, etc.)
                if f"/{current_year}/" in href and ".html" in href:
                    if "facebook.com" in href or "twitter.com" in href or "pinterest.com" in href:
                        continue
                    
                    if href not in seen_urls and len(text) > 10: # Ensure meaningful title
                        seen_urls.add(href)
                        candidate_posts.append({"href": href, "title": text})

            logger.info(f"Found {len(candidate_posts)} recent posts.")
            
            for post in candidate_posts[:5]: 
                link = post['href']
                # Encode/Decode to avoid Windows Console crashes
                title = post['title'].encode('ascii', 'ignore').decode('ascii')
                
                logger.info(f"Processing: {title}")
                
                # 2. Crawl individual article
                article_result = await crawler.arun(url=link)
                if not article_result.success:
                    continue
                    
                # Extract full text
                art_soup = BeautifulSoup(article_result.html, 'html.parser')
                content_div = art_soup.select_one("div.post-body.entry-content")
                
                if content_div:
                    # Clean text: remove scripts and styles
                    for script in content_div(["script", "style"]):
                        script.decompose()
                        
                    full_text = content_div.get_text(separator="\n", strip=True)
                    
                    # 3. Currency Tagging (Naive)
                    # We can tag based on keywords in title/text
                    currency = "Global"
                    if "dollar" in full_text.lower() or "usd" in full_text.lower(): currency = "USD"
                    if "euro" in full_text.lower() or "eur" in full_text.lower(): currency = "EUR"
                    if "jen" in full_text.lower() or "jpy" in full_text.lower(): currency = "JPY"
                    if "pound" in full_text.lower() or "sterling" in full_text.lower(): currency = "GBP"
                    
                    # Date extraction (try to find date-header)
                    date_header = art_soup.select_one("h2.date-header")
                    date_str = date_header.get_text(strip=True) if date_header else datetime.now().strftime('%Y-%m-%d')
                    
                    self.save_article(title, full_text, date_str, currency, link)
                    
        logger.info("Crawl Complete.")

if __name__ == "__main__":
    scraper = MarcToMarketScraper()
    asyncio.run(scraper.run())
