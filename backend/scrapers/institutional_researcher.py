"""
Institutional Research Station
------------------------------
A sophisticated AI-powered research engine.
1. SCRAPER: Crawls FXStreet (Banks) & PoundSterlingLive using Crawl4AI.
2. CLEANER: Uses LLM Extraction to structure raw text into specific 'Bank Views' and 'Fundamental Articles'.
3. ANALYZER: Calculates sentiment per currency and saves to 'news_memory.db'.

Usage:
    pip install crawl4ai playwright pydantic httpx python-dotenv
    playwright install
    python institutional_researcher.py
"""

import asyncio
import sqlite3
import os
import logging
import json
import httpx
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Import Crawl4AI - Robust Import Strategy
try:
    from crawl4ai import AsyncWebCrawler
    from crawl4ai.extraction_strategy import LLMExtractionStrategy
    from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig, CacheMode
    
    # Try finding LLMConfig
    try:
        from crawl4ai import LLMConfig
    except ImportError:
        try:
            from crawl4ai.extraction_strategy import LLMConfig
        except ImportError:
            try:
                from crawl4ai.dataschema import LLMConfig
            except ImportError:
                print("CRITICAL: Could not find LLMConfig in crawl4ai. Please allow us to debug version.")
                exit(1)

except ImportError as e:
    print(f"CRITICAL: crawl4ai import failed. Error: {e}")
    print("This implies a dependency issue. Please share this error.")
    exit(1)

# Load Local Env (API Key)
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent # backend/
env_path = BASE_DIR / ".env.local"
load_dotenv(env_path)

# Provider-agnostic API config
def _get_provider_config():
    """Returns API config for the active LLM provider (for extraction calls)."""
    # Check env vars in priority order: DeepSeek > GLM > NVIDIA
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    glm_key = os.getenv("GLM_API_KEY")
    nvidia_key = os.getenv("NVIDIA_API_KEY")
    
    if deepseek_key:
        return {
            "base_url": "https://api.deepseek.com/chat/completions",
            "api_key": deepseek_key,
            "model_id": "deepseek-chat",
            "provider": "deepseek"
        }
    elif glm_key:
        return {
            "base_url": "https://api.z.ai/api/paas/v4/chat/completions",
            "api_key": glm_key,
            "model_id": "glm-4.7",
            "provider": "glm"
        }
    elif nvidia_key:
        return {
            "base_url": "https://integrate.api.nvidia.com/v1/chat/completions",
            "api_key": nvidia_key,
            "model_id": "moonshotai/kimi-k2.5",
            "provider": "nvidia"
        }
    else:
        # Fallback: check legacy OpenAI key
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            return {
                "base_url": "https://api.deepseek.com/chat/completions",
                "api_key": openai_key,
                "model_id": "deepseek-chat",
                "provider": "deepseek"
            }
        return None

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("InstitutionalResearcher")

# Unified Database Path (Same as analysis.py)
DB_PATH = BASE_DIR / "market_data.db"

# --- 1. DATA MODELS (The Schema) ---

class InstitutionalView(BaseModel):
    """Structure for a single bank/institution's opinion"""
    institution: str = Field(..., description="Name of the Bank/Institution (e.g. UOB, Commerzbank, Citi)")
    asset: str = Field(..., description="The financial asset discussed (e.g. EUR/USD, GBP, Gold, XAU/USD). Standardize to Pairs if possible.")
    bias: str = Field(..., description="Directional bias: 'Bullish', 'Bearish', or 'Neutral'")
    key_level: str = Field(None, description="Any specific price level mentioned (targets, support, resistance)")
    rationale: str = Field(..., description="Brief summary of the fundamental reason provided")

class ArticleLink(BaseModel):
    """Structure for extracting links from a listing page"""
    url: str
    title: str
    approx_date: str = Field(..., description="Date textual representation e.g. '28 Jan', 'Yesterday'")

class FundamentalArticle(BaseModel):
    """Structure for a full fundamental news article"""
    title: str = Field(..., description="Headline of the article")
    summary: str = Field(..., description="Concise summary of the fundamental/economic logic")
    content: str = Field(..., description="The main body text, focusing on economist commentary")
    currency: str = Field(..., description="Primary currency involved (e.g. USD)")
    pair: str = Field(None, description="Specific pair if applicable (e.g. GBP/USD)")
    publish_date: str = Field(..., description="Date of publication in YYYY-MM-DD format")
    is_technical_only: bool = Field(False, description="True if the article is purely Technical Analysis (charts, indicators). False if Fundamental/Economist view.")

# --- 2. DATABASE MANAGER ---

class IntelligenceDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 1. Articles (Compatible with news_scraper.py)
        cursor.execute('''CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id TEXT UNIQUE,
                title TEXT NOT NULL,
                summary TEXT,
                content TEXT,
                url TEXT NOT NULL UNIQUE,
                publish_date DATE,
                currency TEXT,
                pair TEXT,
                source TEXT,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
        
        # 2. Structured Institutional Intelligence
        cursor.execute('''CREATE TABLE IF NOT EXISTS institutional_intelligence (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            timestamp DATETIME,
                            currency TEXT,
                            institution TEXT,
                            bias TEXT,
                            rationale TEXT,
                            levels TEXT,
                            source_url TEXT,
                            UNIQUE(currency, institution, timestamp)
                        )''')
                        
        conn.commit()
        conn.close()

    def save_insight(self, view: InstitutionalView, url: str):
        target = view.asset.upper()
        if "/" in target:
            base, quote = target.split("/")
            currencies = [base, quote]
        else:
            currencies = [target]
            
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            for curr in currencies:
                if curr not in ["EUR", "USD", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "XAU", "GOLD"]:
                    continue
                    
                cursor.execute('''
                    INSERT OR IGNORE INTO institutional_intelligence 
                    (timestamp, currency, institution, bias, rationale, levels, source_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    datetime.utcnow().strftime("%Y-%m-%d"),
                    curr,
                    view.institution,
                    view.bias,
                    view.rationale,
                    view.key_level or "N/A",
                    url
                ))
        except Exception as e:
            logger.error(f"DB Error (Insight): {e}")
        finally:
            conn.commit()
            conn.close()

    def save_article(self, article: FundamentalArticle, url: str, source: str):
        if article.is_technical_only:
            logger.info(f"Skipping Technical Article (Filter Active): {article.title}")
            return

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            # Generate simple ID
            article_id = f"{source}_{url.split('/')[-1]}"
            
            cursor.execute('''
                INSERT OR IGNORE INTO articles
                (article_id, title, summary, content, url, publish_date, currency, pair, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                article_id,
                article.title,
                article.summary,
                article.content,
                url,
                article.publish_date,
                article.currency,
                article.pair,
                source
            ))
            if cursor.rowcount > 0:
                logger.info(f"Saved Article: {article.title}")
        except Exception as e:
            logger.error(f"DB Error (Article): {e}")
        finally:
            conn.commit()
            conn.close()

# (Old Analyzer Removed - Moved to Async Section)

# --- 3. SOURCES LOGIC (Explicit Extraction) ---

async def smart_extract(content: str, prompt: str, schema: dict) -> List[dict]:
    """Helper to call the active LLM provider for extraction (replaces OpenAI SDK)"""
    config = _get_provider_config()
    if not config:
        logger.error("No API key configured for any provider. Cannot extract.")
        return []
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            payload = {
                "model": config['model_id'],
                "messages": [
                    {"role": "system", "content": "You are a specialized financial data extractor. Return strictly valid JSON matching the schema."},
                    {"role": "user", "content": f"{prompt}\n\nCount limit 20 items.\n\nCONTENT:\n{content[:25000]}"} # Limit context
                ],
                "temperature": 0.1,
                "max_tokens": 4096
            }
            
            # DeepSeek supports JSON mode
            if config['provider'] == "deepseek":
                payload["response_format"] = {"type": "json_object"}
            
            response = await client.post(
                config['base_url'],
                headers={
                    "Authorization": f"Bearer {config['api_key']}",
                    "Content-Type": "application/json"
                },
                json=payload
            )
            
            if response.status_code != 200:
                logger.error(f"LLM API Error ({response.status_code}): {response.text[:200]}")
                return []
            
            raw_json = response.json()['choices'][0]['message']['content']
            
            # Clean markdown wrappers if present
            if "```json" in raw_json:
                raw_json = raw_json.split("```json")[1].split("```")[0]
            elif "```" in raw_json:
                raw_json = raw_json.split("```")[1].split("```")[0]
            
            data = json.loads(raw_json.strip())
            
            # Unwrap
            if "views" in data: return data["views"]
            if "articles" in data: return data["articles"]
            if "items" in data: return data["items"]
            return data if isinstance(data, list) else [data]
            
        except Exception as e:
            logger.error(f"LLM Extraction Error: {e}")
            return []

async def scrape_fxstreet_banks(db: IntelligenceDB):
    url = "https://www.fxstreet.com/news?dFR%5BCategory%5D%5B0%5D=News&dFR%5BTags%5D%5B0%5D=Banks"
    logger.info(f"Crawling FXStreet Banks: {url}")
    
    async with AsyncWebCrawler(verbose=True) as crawler:
        # Step 1: Get the List
        result = await crawler.arun(url=url, bypass_cache=True, wait_for="css:.fxs_c_news_list")
        
        if result.success and result.markdown:
            # EXTRACT LINKS - With Ad Filtering
            prompt = """
            Extract article links. 
            Schema: { 'articles': [ { 'url': '...', 'title': '...', 'approx_date': '...' } ] }
            
            CRITICAL FILTERS: 
            1. IGNORE any titles related to "Competitions", "Bonuses", "Register", "Webinar", "Trade for your share", "Deposit".
            2. IGNORE general scraping noise or navigation links.
            3. ONLY extract actual market news or bank research.
            """
            links = await smart_extract(result.markdown, prompt, {})
            
            logger.info(f"Found {len(links)} Bank articles. Scanning top 5...")
            
            for link_obj in links[:5]:
                full_url = link_obj.get('url')
                if not full_url: continue
                
                # Handle relative URLs often found on FXStreet
                if full_url.startswith('/'): 
                    full_url = "https://www.fxstreet.com" + full_url
                
                logger.info(f"Deep Crawling FXStreet: {full_url}")
                art_res = await crawler.arun(url=full_url, bypass_cache=True)
                
                if art_res.success and art_res.markdown:
                    today_str = datetime.utcnow().strftime('%Y-%m-%d')
                    
                    # EXTRACT FULL CONTENT
                    art_prompt = f"""
                    Analyze this text. Current Date: {today_str}.
                    Schema: {{ 'title': '...', 'summary': '...', 'content': '...', 'currency': 'USD', 'pair': 'GBP/USD', 'publish_date': 'YYYY-MM-DD', 'is_technical_only': boolean }}
                    
                    1. Extract the REAL publication date.
                    2. If date starts with "Today", use {today_str}.
                    3. Extract the FULL article content, especially the bank's reasoning.
                    4. Mark is_technical_only=False (Bank views are fundamental).
                    5. Context: Source is FXStreet Banks.
                    """
                    
                    art_data_list = await smart_extract(art_res.markdown, art_prompt, {})
                    
                    for c in art_data_list:
                        try:
                            # Create Article Object
                            art = FundamentalArticle(**c)
                            
                            # Fallback Logic for Pair/Currency if missing
                            if not art.pair:
                                if "EUR" in art.title: art.pair = "EUR/USD"
                                elif "GBP" in art.title: art.pair = "GBP/USD"
                                elif "JPY" in art.title: art.pair = "USD/JPY"
                            
                            if not art.currency and art.pair:
                                art.currency = art.pair[:3] # Naive but works for majors
                                
                            db.save_article(art, full_url, "FXStreet")
                            
                            # ALSO Save as Insight (Dual-Save for backward compatibility with Analyzer)
                            # We synthesize the "View" from the Article summary
                            try:
                                view = InstitutionalView(
                                    institution="FXStreet Bank Desk",
                                    asset=art.pair or art.currency or "Global",
                                    bias="Neutral", # Hard to infer perfectly without specific prompt, usually mixed
                                    rationale=art.summary,
                                    key_level=None
                                )
                                db.save_insight(view, full_url)
                            except:
                                pass # Insight fail is non-critical
                                
                        except Exception as e:
                            logger.error(f"FXStreet Article Error: {e}")

async def scrape_poundsterlinglive_fundamentals(db: IntelligenceDB):
    # Strategy: Scrape Central Bank News + Specific Currency Feeds (Requested by User)
    feed_configs = [
        {"url": "https://www.poundsterlinglive.com/central-bank-news", "tag": "macro"},
        {"url": "https://www.poundsterlinglive.com/pound-news", "tag": "GBP"},
        {"url": "https://www.poundsterlinglive.com/euro-news", "tag": "EUR"},
        {"url": "https://www.poundsterlinglive.com/us-dollar-news", "tag": "USD"},
        {"url": "https://www.poundsterlinglive.com/australian-dollar-news", "tag": "AUD"},
        {"url": "https://www.poundsterlinglive.com/canadian-dollar-news", "tag": "CAD"},
        {"url": "https://www.poundsterlinglive.com/new-zealand-dollar-news", "tag": "NZD"},
        {"url": "https://www.poundsterlinglive.com/japanese-yen-news", "tag": "JPY"},
        {"url": "https://www.poundsterlinglive.com/swiss-franc-news", "tag": "CHF"},
    ]
    
    async with AsyncWebCrawler(verbose=True) as crawler:
        for config in feed_configs:
            feed_url = config['url']
            feed_tag = config['tag']
            
            logger.info(f"Scanning Feed: {feed_url} [{feed_tag}]")
            res = await crawler.arun(url=feed_url, bypass_cache=True) # Default wait
            
            if res.success and res.markdown:
                # Step A: Get Links (General Search)
                # Filter out pure crypto spam (XRP, BTC mining)
                prompt = "Extract article links. Schema: { 'articles': [ { 'url': '...', 'title': '...', 'approx_date': '...' } ] }. Ignore articles about 'Passive Income', 'Mining', 'XRP', 'BTC' or Crypto Ads."
                links = await smart_extract(res.markdown, prompt, {})
                
                logger.info(f"Found {len(links)} articles in feed. Filtering for Major Pairs...")
                
                # Filter: Top 3 per category to keep it fast but broad
                logger.info(f"Found {len(links)} articles in {feed_tag}. Scanning top 3...")
                
                for link_obj in links[:3]:
                    full_url = link_obj.get('url')
                    if not full_url: continue
                    if full_url.startswith('/'): full_url = "https://www.poundsterlinglive.com" + full_url
                    
                    logger.info(f"Deep Crawling: {full_url}")
                    art_res = await crawler.arun(url=full_url, bypass_cache=True)
                    
                    if art_res.success and art_res.markdown:
                        today_str = datetime.utcnow().strftime('%Y-%m-%d')
                        art_prompt = f"""
                        Analyze this text. Current Date: {today_str}.
                        Schema: {{ 'title': '...', 'summary': '...', 'content': '...', 'currency': '{feed_tag if feed_tag != 'macro' else 'USD'}', 'pair': 'GBP/USD', 'publish_date': 'YYYY-MM-DD', 'is_technical_only': boolean }}
                        
                        1. Extract the REAL publication date.
                        2. If date says "Today" or is missing, use {today_str}.
                        3. Mark is_technical_only=True if it is just charts/levels.
                        4. Context: This article was found in the {feed_tag} section.
                        """
                        art_data_list = await smart_extract(art_res.markdown, art_prompt, {})
                        
                        for c in art_data_list:
                            try:
                                art = FundamentalArticle(**c)
                                # Force currency from tag if not explicit
                                if feed_tag != "macro" and not art.currency: 
                                    art.currency = feed_tag
                                
                                # Better defaulting for pairs based on section
                                if not art.pair:
                                    if feed_tag == "EUR": art.pair = "EUR/USD"
                                    elif feed_tag == "GBP": art.pair = "GBP/USD"
                                    elif feed_tag == "AUD": art.pair = "AUD/USD"
                                    elif feed_tag == "CAD": art.pair = "USD/CAD"
                                    elif feed_tag == "JPY": art.pair = "USD/JPY"
                                
                                db.save_article(art, full_url, "PoundSterlingLive")
                            except:
                                pass

# --- 4. ASYNC ANALYZER ---

async def run_analysis_rollup(db: IntelligenceDB):
    conn = sqlite3.connect(db.db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    currencies = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD"]
    today = datetime.utcnow().strftime("%Y-%m-%d")
    
    print("\n--- INSTITUTIONAL INTELLIGENCE REPORT (" + today + ") ---")
    
    for curr in currencies:
        # Get Institution views
        cursor.execute("SELECT bias, institution, rationale FROM institutional_intelligence WHERE currency = ? AND timestamp >= ?", (curr, today))
        rows = cursor.fetchall()
        
        # Get Article headlines for context
        cursor.execute("SELECT title FROM articles WHERE (currency = ? OR pair LIKE ?) AND date(scraped_at) >= date('now', '-1 day')", (curr, f"%{curr}%"))
        art_rows = cursor.fetchall()
        
        if not rows and not art_rows:
            print(f"{curr}: No Recent Data")
            continue
            
        # Stats
        bulls = sum(1 for r in rows if "Bullish" in r['bias'])
        bears = sum(1 for r in rows if "Bearish" in r['bias'])
        sentiment = "NEUTRAL"
        if bulls > bears: sentiment = "BULLISH"
        elif bears > bulls: sentiment = "BEARISH"
        
        # Generator Summary
        context_text = ""
        for r in rows:
            context_text += f"- {r['institution']} ({r['bias']}): {r['rationale']}\n"
        for a in art_rows:
            context_text += f"- News: {a['title']}\n"
            
        summary = "No consensus data."
        if context_text:
            prompt = f"""Summarize the institutional sentiment for {curr} in exactly 3 sentences:
1. Overall institutional bias and consensus (bullish/bearish/mixed).
2. Key fundamental drivers mentioned (e.g., inflation, central bank policy, economic data).
3. Key levels or outlook mentioned by banks.
Return JSON: {{ "summary": "Your 3-sentence summary here." }}"""
            
            # Pass context as content
            res = await smart_extract(context_text, prompt, {})
            
            # Debug: See what we get
            logger.debug(f"Summary LLM Response for {curr}: {res}")
            
            # Handle various response formats
            if isinstance(res, list) and len(res) > 0:
                first_item = res[0]
                if isinstance(first_item, dict):
                    summary = first_item.get('summary', first_item.get('text', str(first_item)))
                elif isinstance(first_item, str):
                    summary = first_item
            elif isinstance(res, dict):
                summary = res.get('summary', res.get('text', str(res)))
        
        print(f"\n{curr} [{sentiment}]: {summary}")
        print(f"   (Votes: {bulls} Bull / {bears} Bear | Articles: {len(art_rows)})")

    conn.close()

async def main():
    db = IntelligenceDB(DB_PATH)
    logger.info("Initializing Institutional Researcher (Crawl4AI)...")
    
    # 1. Scrape & Clean
    await scrape_fxstreet_banks(db)
    await scrape_poundsterlinglive_fundamentals(db)
    
    # 2. Analyze
    await run_analysis_rollup(db)

if __name__ == "__main__":
    asyncio.run(main())
