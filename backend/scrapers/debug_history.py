from bs4 import BeautifulSoup
import re

with open("c:/MacroLens/backend/scrapers/History2.html", "r", encoding="utf-8") as f:
    html = f.read()

soup = BeautifulSoup(html, 'html.parser')
rows = soup.find_all("tr")
dates_found = set()

for row in rows:
    if "theDay" in row.get("class", []) or row.find("td", class_="theDay"):
        text = row.get_text(strip=True)
        dates_found.add(text)

print(f"Dates found in History2.html: {sorted(list(dates_found))[:10]} ... and total {len(dates_found)} days")
