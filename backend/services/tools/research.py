import requests
from bs4 import BeautifulSoup
import logging
import re
from typing import List, Dict, Optional
import urllib.parse

logger = logging.getLogger("ResearchTool")

class WebSearchTool:
    """
    A tool for independent web research. 
    Uses 'requests' and 'BeautifulSoup' to scrape search results and page content.
    """
    
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

    def search(self, query: str, max_results: int = 5) -> List[Dict]:
        """
        Performs a web search using DuckDuckGo HTML (No API key required).
        """
        logger.info(f"Searching Web for: {query}")
        try:
            url = "https://html.duckduckgo.com/html/"
            payload = {'q': query}
            
            resp = requests.post(url, data=payload, headers=self.headers, timeout=10)
            resp.raise_for_status()
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            results = []
            
            # DDG HTML Structure (subject to change, so we add robust guards)
            # Usually .result__body 
            for result in soup.select('.result__body')[:max_results]:
                try:
                    title_tag = result.select_one('.result__a')
                    snippet_tag = result.select_one('.result__snippet')
                    
                    if title_tag and snippet_tag:
                        results.append({
                            "title": title_tag.get_text(strip=True),
                            "link": title_tag['href'],
                            "snippet": snippet_tag.get_text(strip=True)
                        })
                except Exception:
                    continue
                    
            return results
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return [{"error": str(e)}]

    def scrape_url(self, url: str) -> str:
        """
        Visits a URL and extracts the main text content.
        """
        logger.info(f"Scraping URL: {url}")
        try:
            resp = requests.get(url, headers=self.headers, timeout=10)
            resp.raise_for_status()
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Remove script and style elements
            for script in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                script.decompose()
            
            # Get text
            text = soup.get_text(separator='\n')
            
            # Break into lines and remove leading and trailing space on each
            lines = (line.strip() for line in text.splitlines())
            # Break multi-headlines into a line each
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            # Drop blank lines
            text = '\n'.join(chunk for chunk in chunks if chunk)
            
            # Truncate to avoid context overflow limit (e.g. 5000 chars)
            return text[:5000]
            
        except Exception as e:
            logger.error(f"Scraping failed: {e}")
            return f"Failed to read content from {url}: {e}"

# Singleton Instance
research_tool = WebSearchTool()
