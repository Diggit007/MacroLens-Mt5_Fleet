import asyncio
import logging
from typing import Dict, List, Optional
from crawl4ai import AsyncWebCrawler
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TradingEconomicsScraper:
    """
    Scrapes Trading Economics (specifically the G20/Matrix page) using crawl4ai
    to bypass 403 blocks and get institutional macro data.
    """
    
    BASE_URL = "https://tradingeconomics.com/matrix"
    COUNTRIES_URL = "https://tradingeconomics.com/countries"
    
    # Mapping our currency codes to TE Country Names
    CURRENCY_TO_COUNTRY = {
        "USD": "United States",
        "EUR": "Euro Area",
        "JPY": "Japan",
        "GBP": "United Kingdom",
        "AUD": "Australia",
        "CAD": "Canada",
        "CHF": "Switzerland",
        "NZD": "New Zealand",
        "CNY": "China" 
    }

    def __init__(self):
        self.crawler = AsyncWebCrawler(verbose=True)

    async def fetch_macro_matrix(self) -> Dict[str, Dict]:
        """
        Fetches the main macro table and returns a dict keyed by Currency.
        Data: GDP, PMI, CPI, Core CPI, Unemployment, Rate, Debt/GDP, Budget.
        """
        logger.info(f"🕷️ Starting Trading Economics Scrape (Target: {self.BASE_URL})...")
        
        async with AsyncWebCrawler(verbose=True) as crawler:
            # We wait for the table to appear. The matrix page usually has a table class 'table'
            result = await crawler.arun(
                url=self.BASE_URL,
                wait_for="table" 
            )
            
            if not result.success:
                logger.error("❌ Failed to fetch TE Matrix page.")
                return {}
                
            logger.info("✅ Successfully fetched TE Matrix page. Parsing...")
            
            # Dump success HTML for column mapping debug
            with open("c:/MacroLens/backend/debug_te_matrix.html", "w", encoding="utf-8") as f:
                f.write(result.html)
                
            return self._parse_table(result.html)

    def _parse_table(self, html: str) -> Dict[str, Dict]:
        soup = BeautifulSoup(html, 'html.parser')
        data = {}
        
        # The main table usually has id="table" or class="table-hover"
        table = soup.find("table", {"id": "table-countries"}) # Verify ID later, usually table-countries on /countries
        
        if not table:
            # Try finding by class if ID fails
            table = soup.find("table", class_="table")
            
        if not table:
            logger.error("❌ Could not find macro table in HTML.")
            # Dump HTML for debugging
            with open("c:/MacroLens/backend/debug_te.html", "w", encoding="utf-8") as f:
                f.write(soup.prettify())
            return {}
            
        # Iterate rows
        for row in table.find_all("tr"):
            cols = row.find_all("td")
            if not cols: continue
            
            # Country Name (Column 0)
            country_name = cols[0].get_text(strip=True)
            
            # Identify Currency from Country
            currency = None
            for code, name in self.CURRENCY_TO_COUNTRY.items():
                if name.lower() == country_name.lower():
                    currency = code
                    break
            
            if not currency:
                continue
                
            # Parse Columns (Approximate layout on /countries)
            # GDP, GDP YoY, GDP QoQ, Interest Rate, Inflation Rate, Jobless Rate, Gov. Budget, Debt/GDP, Current Account
            
            try:
                # Helper to clean valid float
                def clean(txt):
                    txt = txt.replace('%','').replace(',','')
                    try: return float(txt)
                    except: return None

                # Indices based on analyze_matrix_headers.py:
                # 0: Country
                # 1: GDP (Absolute)
                # 2: GDP Growth (YoY)
                # 3: Interest Rate
                # 4: Inflation Rate
                # 5: Jobless Rate
                # 6: Gov. Budget
                # 7: Debt/GDP
                # 8: Current Account
                # 9: Population
                
                profile = {
                    "gdp_growth_yoy": clean(cols[2].get_text(strip=True)),
                    "interest_rate": clean(cols[3].get_text(strip=True)),
                    "inflation_rate": clean(cols[4].get_text(strip=True)),
                    "unemployment_rate": clean(cols[5].get_text(strip=True)),
                    "budget_balance": clean(cols[6].get_text(strip=True)),
                    "debt_to_gdp": clean(cols[7].get_text(strip=True)),
                    "current_account": clean(cols[8].get_text(strip=True)),
                }
                
                data[currency] = profile
                logger.info(f"   Parsed {currency}: {profile}")
                
            except Exception as e:
                logger.warning(f"Error parsing row for {country_name}: {e}")
                continue
                
        return data

    async def fetch_details(self, currency: str, country_name: str) -> Dict[str, float]:
        """
        Fetches specific indicators (PMI, Core CPI) from the country's detail page.
        URL: https://tradingeconomics.com/{country}/indicators
        """
        slug = country_name.lower().replace(" ", "-")
        url = f"https://tradingeconomics.com/{slug}/indicators"
        logger.info(f"   🕷️ Fetching details for {currency} ({url})...")
        
        data = {}
        
        async with AsyncWebCrawler(verbose=True) as crawler:
            result = await crawler.arun(url=url, wait_for="table")
            
            if not result.success:
                logger.warning(f"Failed to fetch details for {currency}")
                return {}

            soup = BeautifulSoup(result.html, 'html.parser')
            
            # The indicators page has multiple tables. We need to search all rows.
            # Typical structure: <tr><td><a>Indicator Name</a></td><td>Value</td>...</tr>
            
            def parse_row(name_fragment, key):
                # Search for a link containing the text
                link = soup.find("a", string=lambda t: t and name_fragment.lower() in t.lower())
                if link:
                    # The value is usually in the next 'td' or the one after (Actual)
                    # Structure: <td><a>Name</a></td> <td class='datatable-item'>Actual</td>
                    row = link.find_parent("tr")
                    if row:
                        cols = row.find_all("td")
                        if len(cols) > 1:
                            val_text = cols[1].get_text(strip=True)
                            try:
                                val = float(val_text.replace(',',''))
                                data[key] = val
                                logger.info(f"      found {key}: {val}")
                            except: pass

            # 1. CORE INFLATION
            # US: "Core Inflation Rate"
            parse_row("Core Inflation Rate", "core_inflation")

            # 2. PMI (Prioritize Composite, then Manufacturing)
            # US: "Composite PMI", "Manufacturing PMI", "Services PMI"
            # We want Composite if available, else Manufacturing + Services average?
            # Let's try to get all and decide logic later or just store them.
            parse_row("Composite PMI", "pmi_composite")
            parse_row("Manufacturing PMI", "pmi_manufacturing")
            parse_row("Services PMI", "pmi_services")
            
        return data

# Test runner
if __name__ == "__main__":
    async def main():
        scraper = TradingEconomicsScraper()
        
        # 1. Fetch Matrix
        matrix_data = await scraper.fetch_macro_matrix()
        print(f"\nFetched Matrix data for {len(matrix_data)} currencies.")
        
        # 2. Fetch Details for mapped currencies
        for ccy, profile in matrix_data.items():
            print(f"\n--- Enriching {ccy} ---")
            country_name = scraper.CURRENCY_TO_COUNTRY.get(ccy)
            if country_name:
                details = await scraper.fetch_details(ccy, country_name)
                profile.update(details)
                print(f"FINAL {ccy}: {profile}")

    asyncio.run(main())
