"""
MyFxBook Retail Sentiment Scraper
Uses subprocess isolation to run Playwright in a fresh Python process,
preventing asyncio event loop conflicts when called from the backend worker.
"""
import asyncio
import json
import logging
import os
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Dict
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("RetailSentimentAgent")

# ─── Subprocess worker script (runs in its own Python process) ───
_WORKER_SCRIPT = r'''
import asyncio
import json
import sys
import platform
from playwright.async_api import async_playwright

async def scrape():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        try:
            await page.goto("https://www.myfxbook.com/community/outlook", timeout=30000, wait_until="networkidle")
        except:
            pass
        
        await asyncio.sleep(3)
        html = await page.content()
        await browser.close()
        return html

if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

html = asyncio.run(scrape())

# Write HTML to the output file path passed as argv[1]
with open(sys.argv[1], "w", encoding="utf-8") as f:
    f.write(html)
'''


class RetailSentimentAgent:
    """
    Scrapes MyFxBook Community Outlook to gauge Retail Sentiment.
    Uses subprocess isolation so Playwright never conflicts with the main event loop.
    Applies Contrarian Logic.
    """
    
    def __init__(self):
        self.url = "https://www.myfxbook.com/community/outlook"

    async def fetch_sentiment(self) -> List[Dict]:
        """
        Launches a SEPARATE Python process to run Playwright,
        reads back the HTML from a temp file, and parses it.
        """
        logger.info(f"Launching subprocess to crawl {self.url}...")
        
        # Write HTML to temp file from subprocess
        tmp_path = os.path.join(tempfile.gettempdir(), "myfxbook_outlook.html")
        
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c", _WORKER_SCRIPT, tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            
            if proc.returncode != 0:
                logger.error(f"Subprocess failed (rc={proc.returncode}): {stderr.decode()[:500]}")
                return []
            
            if not os.path.exists(tmp_path):
                logger.error("Subprocess did not produce HTML output file.")
                return []
            
            with open(tmp_path, "r", encoding="utf-8") as f:
                html = f.read()
            
            # Cleanup
            try:
                os.remove(tmp_path)
            except:
                pass
            
            if not html or len(html) < 1000:
                logger.error(f"HTML too short ({len(html)} chars), scrape likely failed.")
                return []
            
            logger.info(f"Got {len(html)} chars of HTML. Parsing...")
            return self._parse_html(html)
            
        except asyncio.TimeoutError:
            logger.error("Subprocess timed out after 60s.")
            return []
        except Exception as e:
            logger.error(f"Subprocess launch failed: {e}")
            return []

    def _parse_html(self, html: str) -> List[Dict]:
        """
        Parses the HTML table to extract Short/Long percentages per symbol.
        MyFxBook actual row format (from dump):
          Short row: SYMBOL | Short | XX% | N.NN lots | COUNT
          Long row:  Long   | XX%   | N.NN lots | COUNT
        The symbol only appears in the Short row. Long row follows immediately.
        """
        soup = BeautifulSoup(html, 'html.parser')
        data = []
        
        rows = soup.select('tr')
        current_symbol = None
        current_short = None
        
        for row in rows:
            cells = row.find_all('td')
            if not cells:
                continue
            
            text_cells = [c.get_text(strip=True) for c in cells]
            full_text = ' '.join(text_cells)
            
            # --- SHORT ROW: contains symbol name + "Short" + percentage ---
            if 'Short' in full_text and '%' in full_text:
                # Find the symbol (6+ uppercase alphanumeric chars)
                sym_match = re.search(r'([A-Z]{3,6}[A-Z0-9]{0,4})', full_text)
                pct_match = re.search(r'(\d+)%', full_text)
                
                if sym_match and pct_match:
                    candidate = sym_match.group(1)
                    # Filter out non-symbol matches like "Short"
                    if candidate not in ('Short', 'Long', 'Lots') and len(candidate) >= 6:
                        current_symbol = candidate
                        current_short = int(pct_match.group(1))
                        continue
            
            # --- LONG ROW: starts with "Long" + percentage ---
            if current_symbol is not None and 'Long' in full_text and '%' in full_text:
                pct_match = re.search(r'(\d+)%', full_text)
                if pct_match:
                    long_pct = int(pct_match.group(1))
                    
                    # Validate percentages add up roughly to 100
                    if abs((current_short + long_pct) - 100) <= 2:
                        analysis = self._analyze_contrarian(current_symbol, long_pct, current_short)
                        data.append(analysis)
                    
                    current_symbol = None
                    current_short = None

        logger.info(f"Extracted sentiment for {len(data)} instruments.")
        return data

    def _analyze_contrarian(self, symbol: str, long_pct: int, short_pct: int) -> Dict:
        """Applies Contrarian Logic."""
        signal = "NEUTRAL"
        strength = "Weak"
        
        STRONG_THRESHOLD = 70
        MODERATE_THRESHOLD = 55
        
        if long_pct >= STRONG_THRESHOLD:
            signal = "SELL"
            strength = "Strong"
        elif long_pct >= MODERATE_THRESHOLD:
            signal = "SELL"
            strength = "Moderate"
        elif short_pct >= STRONG_THRESHOLD:
            signal = "BUY"
            strength = "Strong"
        elif short_pct >= MODERATE_THRESHOLD:
            signal = "BUY"
            strength = "Moderate"
            
        return {
            "symbol": symbol,
            "long_pct": long_pct,
            "short_pct": short_pct,
            "signal": signal,
            "strength": strength,
            "bias": f"{'Long' if long_pct > short_pct else 'Short'} dominance ({max(long_pct, short_pct)}%)"
        }


# Runnable script
if __name__ == "__main__":
    if platform.system() == 'Windows':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    async def main():
        agent = RetailSentimentAgent()
        results = await agent.fetch_sentiment()
        
        print(f"\n{'SYMBOL':<10} | {'LONG %':<8} | {'SHORT %':<8} | {'SIGNAL':<8} | {'STRENGTH'}")
        print("-" * 55)
        for r in results:
            print(f"{r['symbol']:<10} | {r['long_pct']:<8} | {r['short_pct']:<8} | {r['signal']:<8} | {r['strength']}")
        
        # Save to JSON
        output_path = Path(__file__).parent.parent / "retail_sentiment.json"
        with open(output_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nSaved {len(results)} items to {output_path}")

    asyncio.run(main())
