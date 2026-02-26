
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent.parent))

import logging
import asyncio
import re
import httpx
from bs4 import BeautifulSoup
from typing import Optional, Dict
from datetime import datetime

# Import AIEngine for Sentiment Analysis
# Assuming AIEngine is available and configured
try:
    from backend.services.ai_engine import AIEngine
    from backend.config import settings
except ImportError:
    AIEngine = None

from backend.services.news_retriever import NewsRetriever

logger = logging.getLogger("CentralBankScraper")

class CentralBankScraper:
    """
    Fetches and analyzes Central Bank monetary policy statements.
    Specific scrapers for RBA, FOMC. 
    Fallback to NewsRetriever for others.
    """
    
    def __init__(self):
        self.http_client = httpx.AsyncClient(timeout=10.0, follow_redirects=True)
        self.news_retriever = NewsRetriever()
        
    async def close(self):
        await self.http_client.aclose()
        
    async def get_latest_statement(self, currency: str) -> Dict[str, str]:
        """
        Returns { "text": "...", "source": "RBA", "date": "...", "url": "..." }
        """
        if currency == "AUD":
            return await self._scrape_rba()
        elif currency == "USD":
            return await self._scrape_fomc_mock() # Real FOMC scraping is hard due to JS, fallback to generic for now or mock
        else:
            return await self._get_from_news(currency)

    async def _scrape_rba(self) -> Dict:
        """
        Scrapes RBA Media Releases for the latest Board Decision.
        """
        url = "https://www.rba.gov.au/media-releases/"
        try:
            resp = await self.http_client.get(url)
            if resp.status_code != 200:
                logger.warning(f"RBA Scrape failed: {resp.status_code}")
                return None
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Find first link containing "Statement by the Monetary Policy Board"
            # RBA structure is usually a list of <li>
            links = soup.find_all('a', href=True)
            target_link = None
            
            for link in links:
                if "Statement by the Monetary Policy Board" in link.text:
                    target_link = link['href']
                    break
            
            if not target_link:
                return await self._get_from_news("AUD")
                
            # Handle relative URL
            if not target_link.startswith("http"):
                target_link = f"https://www.rba.gov.au{target_link}" if target_link.startswith("/") else f"https://www.rba.gov.au/media-releases/{target_link}"

            # Fetch Statement
            stmt_resp = await self.http_client.get(target_link)
            stmt_soup = BeautifulSoup(stmt_resp.text, 'html.parser')
            
            # Extract content (usually in <div id="content"> or <div class="article-content">)
            # RBA simple content extraction:
            content_div = stmt_soup.find('div', id='content') or stmt_soup.find('div', class_='box-content')
            text = content_div.get_text(strip=True) if content_div else stmt_soup.get_text(strip=True)
            
            # Clean up Text
            clean_text = " ".join(text.split()[:500]) # Limit to 500 words for analysis
            
            return {
                "text": clean_text,
                "source": "RBA Official Site",
                "date": datetime.utcnow().strftime("%Y-%m-%d"), # Approximation
                "url": target_link
            }
            
        except Exception as e:
            logger.error(f"RBA Scrape Error: {e}")
            return await self._get_from_news("AUD")

    async def _scrape_fomc_mock(self) -> Dict:
        # Fallback to news for now as FOMC page is complex
        return await self._get_from_news("USD")

        return None

    async def _get_from_news(self, currency: str) -> Dict:
        """
        Fallback: Search DB for "Statement" or "Decision"
        """
        try:
            articles = await self.news_retriever.get_article_context(currency)
            if not articles or currency not in articles:
                return None
                
            # Try to find best match
            best_art = None
            for art in articles[currency]:
                if "Statement" in art.title or "Decision" in art.title or "Rate" in art.title:
                    best_art = art
                    break
            
            if not best_art and articles[currency]:
                best_art = articles[currency][0]
                
            if best_art:
                return {
                    "text": f"{best_art.title}: {best_art.summary}",
                    "source": best_art.source,
                    "date": best_art.publish_date,
                    "url": best_art.url
                }
            return None
        except Exception as e:
            logger.error(f"News Fallback Error: {e}")
            return None

    async def analyze_statement(self, text: str, currency: str) -> Dict:
        """
        Uses AIEngine to determine Policy Bias and Cycle Phase.
        """
        if not AIEngine or not settings.DEEPSEEK_API_KEY:
            return {"policy_bias": "NEUTRAL", "cycle_phase": "HOLD", "hawkish_score": 0}

        try:
            # We need a direct prompt, assuming we can access AIEngine internal or use a helper
            # For now, let's construct a simple prompt and use the engine's provider logic if accessible, 
            # or just rely on a new method in AIEngine if we had time. 
            # Since AIEngine is complex, let's use a direct HTTP call here mimicking the engine for simplicity
            # OR better: Add a classification method to AIEngine?
            # Let's try to misuse construct_prompt + standard ask if possible, but `ask` is in AgentService.
            
            # FAST PATH: We will implement a lightweight direct call here using the settings creds
            # to avoid circular dependency hell with AgentService.
            
            api_key = settings.DEEPSEEK_API_KEY.get_secret_value()
            model = "deepseek-chat" # or settings.LLM_MODEL
            
            prompt = f"""
            Analyze this Central Bank Statement for {currency}.
            
            TEXT: "{text[:1500]}..."
            
            Determine:
            1. Policy Bias: HAWKISH, DOVISH, or NEUTRAL
            2. Cycle Phase: HIKING, CUTTING, or PEAK_HOLD
            3. Hawkish Score: -10 (Max Dovish) to +10 (Max Hawkish)
            
            Return JSON ONLY: {{"bias": "...", "phase": "...", "score": 0}}
            """
            
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "response_format": {"type": "json_object"}
            }
            
            # Use specific provider URL
            url = "https://api.deepseek.com/v1/chat/completions" # Default Deepseek
            
            resp = await self.http_client.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                content = data['choices'][0]['message']['content']
                import json
                result = json.loads(content)
                return {
                    "policy_bias": result.get("bias", "NEUTRAL").upper(),
                    "cycle_phase": result.get("phase", "HOLD").upper(),
                    "hawkish_score": result.get("score", 0)
                }
            else:
                logger.error(f"AI Analysis Failed: {resp.status_code}")
                return {"policy_bias": "NEUTRAL", "cycle_phase": "HOLD", "hawkish_score": 0}
                
        except Exception as e:
            logger.error(f"AI Analysis Exception: {e}")
            return {"policy_bias": "NEUTRAL", "cycle_phase": "HOLD", "hawkish_score": 0}

if __name__ == "__main__":
    import asyncio
    
    # Mock settings if needed for standalone test
    if not settings.DEEPSEEK_API_KEY:
        print("WARNING: No API Key found. AI analysis will fail.")

    async def main():
        scraper = CentralBankScraper()
        
        print("\n--- Testing RBA ---")
        rba = await scraper.get_latest_statement("AUD")
        if rba:
            print(f"Found Statement: {rba['text'][:100]}...")
            print("Analyzing...")
            analysis = await scraper.analyze_statement(rba['text'], "AUD")
            print(f"Analysis Result: {analysis}")
        else:
            print("RBA Not Found")
        
        print("\n--- Testing USD (Fallback) ---")
        usd = await scraper.get_latest_statement("USD")
        if usd:
            print(f"Found News: {usd['text'][:100]}...")
            print("Analyzing...")
            analysis = await scraper.analyze_statement(usd['text'], "USD")
            print(f"Analysis Result: {analysis}")
        else:
            print("USD Not Found")
            
        await scraper.close()
        
    asyncio.run(main())
