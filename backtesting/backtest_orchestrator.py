
import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Callable
import pandas as pd

# Add backend to path
import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from backend.services.agent_service import MacroLensAgentV2

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("BacktestOrchestrator")

DATA_DIR = Path(__file__).parent / "data"

class BacktestOrchestrator:
    """
    The 'Time Machine' Engine.
    Replays history candle-by-candle and asks the Agent for analysis.
    """
    def __init__(self, symbol: str, start_date: datetime, end_date: datetime):
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self.agent = MacroLensAgentV2()
        self.history_data = {} # Cache for all TFs
        
    async def load_data(self):
        """Loads locally stored JSON candle data for the backtest period"""
        tfs = ["M5", "M15", "H1", "H4", "D1"]
        logger.info(f"Loading historical data for {self.symbol}...")
        
        for tf in tfs:
            file_path = DATA_DIR / f"{self.symbol}_{tf}.json"
            if not file_path.exists():
                logger.error(f"Missing data file: {file_path}")
                continue
                
            with open(file_path, "r") as f:
                raw_data = json.load(f)
                
            # Convert to DataFrame for easier slicing by time
            df = pd.DataFrame(raw_data)
            # Ensure time column is datetime
            # Handle potential ISO formats: "2024-01-01T00:00:00Z"
            df['time'] = pd.to_datetime(df['time'], utc=True)
            df.set_index('time', inplace=True)
            df.sort_index(inplace=True)
            
            self.history_data[tf] = df
            logger.info(f"Loaded {len(df)} candles for {tf}")

    def get_market_snapshot(self, simulated_time: datetime, tf: str) -> List[Dict]:
        """
        Returns candles that would have been 'visible' at simulated_time.
        Simulates 'fetch_candles' during backtest.
        """
        if tf not in self.history_data:
            return []
            
        df = self.history_data[tf]
        # Filter: Time <= simulated_time
        # We assume 'time' in candle is Open Time. 
        # So we can see candles where open_time <= simulated_time. 
        # Strictly speaking, if it's 10:02, we can see the 10:00 M5 candle (if it closed? No, M5 closes at 10:05).
        # Actually, we usually want COMPLETED candles. 
        # For simplicity, we take all candles with timestamp < simulated_time.
        
        visible_df = df[df.index <= simulated_time].tail(100) # Agent needs ~50-100 candles
        
        # Convert back to list of dicts
        candles = []
        for index, row in visible_df.iterrows():
            c = row.to_dict()
            c['time'] = index.isoformat()
            candles.append(c)
            
        return candles

    async def run(self, step_callback: Callable = None):
        """
        Main Loop. Iterates through M5 candles.
        """
        if "M5" not in self.history_data:
            logger.error("M5 Data required for clock tick.")
            return

        m5_df = self.history_data["M5"]
        # Filter for requested date range
        # Ensure timezones match (UTC)
        if self.start_date.tzinfo is None:
            self.start_date = self.start_date.replace(tzinfo=datetime.utcnow().tzinfo)
        if self.end_date.tzinfo is None:
            self.end_date = self.end_date.replace(tzinfo=datetime.utcnow().tzinfo)

        # Slice the timeline
        # We interpret start_date as the beginning of the REPLAY.
        # Convert to pd.Timestamp and ensure UTC for robust comparison
        start_ts = pd.Timestamp(self.start_date)
        if start_ts.tz is None:
            start_ts = start_ts.tz_localize("UTC")
        else:
            start_ts = start_ts.tz_convert("UTC")
            
        end_ts = pd.Timestamp(self.end_date)
        if end_ts.tz is None:
            end_ts = end_ts.tz_localize("UTC")
        else:
            end_ts = end_ts.tz_convert("UTC")
        
        timeline = m5_df[(m5_df.index >= start_ts) & (m5_df.index <= end_ts)].index
        
        print(f"--- STARTING SIMULATION: {len(timeline)} Steps ---")
        
        logs = []

        for current_time in timeline:
            # We treat the 'current_time' as the moment the M5 candle CLOSED (approximate for replay speed)
            # Or Open? Let's say we are AT this time.
            
            sim_time_str = current_time.strftime("%Y-%m-%d %H:%M:%S")
            # print(f"[CLOCK] {sim_time_str}")
            
            # 1. Define Callback for Agent to "Fetch" data from our history
            async def backtest_fetch(sym, tf):
                return self.get_market_snapshot(current_time, tf)
            
            # 2. Run Agent Analysis
            # We only run Full Analysis periodically (e.g. every hour) OR if there is news?
            # Running LLM every M5 step is expensive/slow.
            # Strategy: Run Deterministic Check every M5. If 'Confluence > X', Run LLM.
            
            # For now, let's run the PRE-ANALYSIS part only (modify agent to allow skipping LLM?)
            # Or just run full analysis but mock the LLM for speed in tests?
            # User wants to test ACCURACY. So we need the LLM output.
            # Let's run it HOURLY.
            
            if current_time.minute == 0: # Top of the hour
                logger.info(f"🤖 Analyzing at {sim_time_str}...")
                
                try:
                    result = await self.agent.process_single_request(
                        self.symbol, 
                        fetch_callback=backtest_fetch,
                        simulated_time=current_time
                    )
                    
                    signal = result.get("direction", "WAIT")
                    confidence = result.get("confidence", 0)
                    
                    if signal != "WAIT":
                        entry = {
                            "time": sim_time_str,
                            "signal": signal,
                            "confidence": confidence,
                            "price": result.get("technical_data", {}).get("M5", {}).get("price"),
                            "reason": result.get("reasoning", "")[:100] + "..."
                        }
                        logs.append(entry)
                        logger.info(f"⚡ SIGNAL: {signal} ({confidence}%)")
                        
                        if step_callback:
                            await step_callback(entry)
                            
                except Exception as e:
                    logger.error(f"Agent Error at {sim_time_str}: {e}")
        
        # Save Report
        with open(DATA_DIR / f"backtest_results_{self.symbol}.json", "w") as f:
            json.dump(logs, f, indent=2)
            
        print(f"--- SIMULATION COMPLETE. {len(logs)} Signals Generated. ---")
        await self.agent.close()

if __name__ == "__main__":
    # Test Run
    from datetime import timezone

    async def main():
        # Define range (Must match data we fetched)
        # Data ends around 2026-01-30 based on 'tail' check
        end = datetime(2026, 1, 30, 20, 0, 0, tzinfo=timezone.utc)
        start = end - timedelta(hours=6) 
        
        orch = BacktestOrchestrator("EURUSD", start, end)
        await orch.load_data()
        await orch.run()
        
    asyncio.run(main())
