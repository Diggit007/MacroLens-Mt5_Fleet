import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))

from backend.services.tools.research import research_tool

def test_search():
    print("Testing Web Search...")
    results = research_tool.search("current price of gold", max_results=3)
    print(f"Results Found: {len(results)}")
    for r in results:
        print(f"- {r.get('title')}: {r.get('link')}")

if __name__ == "__main__":
    test_search()
