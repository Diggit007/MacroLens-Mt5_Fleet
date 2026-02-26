import asyncio
import json
import logging
from playwright.async_api import async_playwright
import pandas as pd
from pathlib import Path

# Config
BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "news_memory.db"
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SentimentMiner")

class SentimentHistoryMiner:
    def __init__(self):
        self.base_url = "https://www.myfxbook.com/community/outlook"
        self.pairs = ['EURUSD', 'GBPUSD', 'USDJPY', 'XAUUSD', 'AUDUSD', 'USDCAD', 'USDCHF'] # Add more as needed

    async def fetch_chart_data(self, symbol: str):
        # The URL for specific pair is usually /outlook/{symbol}
        # But myfxbook structure is: /community/outlook/{pair}
        # We need to navigate and extract Highcharts data.
        
        url = f"{self.base_url}/{symbol}"
        logger.info(f"Navigating to {url}...")
        
        async with async_playwright() as p:
            # Headless=False to bypass basic bot detection
            browser = await p.chromium.launch(headless=False)
            page = await browser.new_page()
            
            try:
                await page.goto(url, timeout=90000)
                # Wait for Cloudflare/Human check
                await page.wait_for_timeout(4000)
                
                # Check for Cloudflare/Access Denied
                title = await page.title()
                if "Just a moment" in title or "Access denied" in title:
                    logger.error(f"Blocked by Cloudflare for {symbol}")
                    await browser.close()
                    return None
                
                # Network Interception
                final_data = None
                
                async def handle_response(response):
                    nonlocal final_data
                    if "getHistoricalSentiment" in response.url or "get-community-outlook" in response.url or "chart" in response.url:
                        try:
                            json_data = await response.json()
                            logger.info(f"Captured JSON from {response.url}")
                            # Inspect structure
                            final_data = json_data
                        except:
                            pass

                page.on("response", handle_response)
                
                await page.goto(url, timeout=90000)
                await page.wait_for_timeout(10000) # Wait for charts to load
                
                if final_data:
                    return final_data
                
                # Fallback to JS if interception fails
                logger.info("Network interception failed, trying JS fallback...")
                
                await browser.close()
                return None
                
            except Exception as e:
                logger.error(f"Error fetching {symbol}: {e}")
                await browser.close()
                return None

    def process_and_save(self, symbol, raw_data):
        if not raw_data or isinstance(raw_data, str):
            logger.warning(f"No valid data for {symbol}")
            return
            
        shorts = pd.DataFrame(raw_data['shorts'])
        longs = pd.DataFrame(raw_data['longs'])
        
        if shorts.empty or longs.empty: return
        
        # Merge on timestamp 't'
        df = pd.merge(shorts, longs, on='t', suffixes=('_short', '_long'))
        df['datetime'] = pd.to_datetime(df['t'], unit='ms')
        df['symbol'] = symbol
        
        # Calculate Percentages
        df['total_vol'] = df['v_short'] + df['v_long']
        df['short_pct'] = (df['v_short'] / df['total_vol']) * 100
        df['long_pct'] = (df['v_long'] / df['total_vol']) * 100
        
        # Save to CSV for analysis (or DB)
        output_file = BASE_DIR / f"sentiment_history_{symbol}.csv"
        df.to_csv(output_file, index=False)
        logger.info(f"Saved {len(df)} rows to {output_file}")
        
    async def run(self):
        for pair in self.pairs:
            data = await self.fetch_chart_data(pair)
            self.process_and_save(pair, data)

if __name__ == "__main__":
    miner = SentimentHistoryMiner()
    asyncio.run(miner.run())
