from bs4 import BeautifulSoup

def analyze():
    with open("c:/MacroLens/backend/debug_te_matrix.html", "r", encoding="utf-8") as f:
        html = f.read()
        
    soup = BeautifulSoup(html, 'html.parser')
    
    # Try to find the header row
    headers = []
    
    # Look for a table with 'GDP' in it
    for table in soup.find_all("table"):
        header_row = table.find("tr")
        if header_row and "GDP" in header_row.get_text():
            print(f"✅ Found Table with GDP!")
            cols = header_row.find_all("th")
            if not cols:
                cols = header_row.find_all("td")
            
            headers = [c.get_text(strip=True) for c in cols]
            print("HEADERS:")
            for i, h in enumerate(headers):
                print(f"  {i}: {h}")
            break
            
    if not headers:
        print("❌ Could not find a table with 'GDP' in headers.")

if __name__ == "__main__":
    analyze()
