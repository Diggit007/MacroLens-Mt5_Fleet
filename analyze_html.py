from bs4 import BeautifulSoup

def analyze():
    with open("c:/MacroLens/backend/debug_te.html", "r", encoding="utf-8") as f:
        html = f.read()
        
    soup = BeautifulSoup(html, 'html.parser')
    
    # search for string
    targets = soup.find_all(string=lambda text: "United States" in text if text else False)
    
    print(f"Found {len(targets)} occurrences of 'United States'")
    
    for i, t in enumerate(targets):
        print(f"\n--- Occurrence {i+1} ---")
        parent = t.parent
        print(f"Parent Tag: {parent.name}")
        print(f"Parent Class: {parent.get('class')}")
        print(f"Parent ID: {parent.get('id')}")
        
        # Go up to find a table
        curr = parent
        while curr and curr.name != 'table':
            curr = curr.parent
            
        if curr:
            print(f"✅ Found inside TABLE. ID: {curr.get('id')}, Class: {curr.get('class')}")
            # Print the row
            row = t.find_parent('tr')
            if row:
                print("ROW CONTENT:")
                print(row.prettify()[:500]) # First 500 chars of row
        else:
            print("❌ Not inside a table.")

if __name__ == "__main__":
    analyze()
