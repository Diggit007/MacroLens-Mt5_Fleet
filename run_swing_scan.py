
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from backend.services.macro_divergence import MacroDivergence

async def main():
    print("Initializing Macro Swing Scanner...")
    scanner = MacroDivergence()
    
    print("Running Scan...")
    results = scanner.scan_for_divergence()
    
    print(f"\n{'PAIR':<10} | {'DIR':<6} | {'SCORE':<5} | {'RATIONALE'}")
    print("-" * 100)
    
    for op in results[:5]: 
        rationale = op['rationale']
        if len(rationale) > 60:
            rationale = rationale[:57] + "..."
        print(f"{op['symbol']:<10} | {op['recommendation']:<6} | {op['divergence_score']:<5} | {rationale}")

if __name__ == "__main__":
    asyncio.run(main())
