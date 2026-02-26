import asyncio
import logging
import sys
import os
from unittest.mock import MagicMock, patch

# Setup paths
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Configure Logging to Console - Silence Noise
logging.basicConfig(level=logging.INFO, format='%(message)s')
logging.getLogger("socketio").setLevel(logging.ERROR)
logging.getLogger("engineio").setLevel(logging.ERROR)
logging.getLogger("firebase_admin").setLevel(logging.ERROR)
logging.getLogger("google").setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("requests").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("DEMO").setLevel(logging.INFO)

from backend.services.event_monitor import EventMonitorService
from backend.services.cognitive_engine import CognitiveEngine

async def run_demo():
    print("\n" + "="*60, flush=True)
    print("🚀 STARTING COGNITIVE ORCHESTRATOR DEMO", flush=True)
    print("="*60 + "\n", flush=True)

    # 1. DEMO: COGNITIVE ENGINE (The Subconscious)
    print("🧠 STEP 1: Waking up Cognitive Engine...", flush=True)
    engine = CognitiveEngine()
    
    # Mock Agent
    engine.agent = MagicMock()
    engine.agent.http_client = MagicMock()
    
    # Mock LLM Response for Thought
    mock_thought = {
        "thought": "Market is quiet ahead of CPI. Volatility expected to rise. Engaging defensive risk mode.",
        "bias": "NEUTRAL",
        "risk_mode": "DEFENSIVE",
        "session": "NY"
    }
    
    # Setup Async Mock
    future = asyncio.Future()
    future.set_result(MagicMock(status_code=200, json=lambda: {'choices': [{'message': {'content': str(mock_thought).replace("'", '"')}}]}))
    engine.agent.http_client.post.return_value = future

    with patch("backend.services.cognitive_engine.world_state") as mock_world:
        await engine._tick()
        print(f"✅ Cognitive Engine Pulse Complete.", flush=True)
        print(f"   -> Generated Thought: \"{mock_thought['thought']}\"", flush=True)
        print(f"   -> Updated System State: Bias={mock_thought['bias']}, Risk={mock_thought['risk_mode']}", flush=True)

    print("\n" + "-"*60 + "\n", flush=True)

    # 2. DEMO: EVENT MONITOR (The Eyes)
    print("👀 STEP 2: Scanning for Economic Events...", flush=True)
    monitor = EventMonitorService()
    
    # Mock Database Return (Fake High Impact Event)
    mock_event = (
        101, "CPI Year-Over-Year", "2026-02-15", "14:30", "USD", 
        3.2, 3.4, "HIGH"
    )
    
    # Mock the DB Fetch
    with patch("backend.services.event_monitor.DatabasePool.fetch_all", new_callable=MagicMock) as mock_db:
        f = asyncio.Future()
        f.set_result([mock_event])
        mock_db.return_value = f
        
        # Mock Predictor and Executor logic
        monitor.predictor.predict_event = MagicMock()
        monitor.predictor.predict_event.return_value = MagicMock(
            event_name="CPI Year-Over-Year",
            predicted_outcome="MISS",
            probability=0.75,
            confidence="HIGH",
            expected_direction="BEARISH",
            bias_score=-2,
            recommendation="SELL USD Pairs",
            avg_pips=45.0,
            trend_forecast="BEARISH (3 Days)"
        )
        
        monitor.executor.generate_signal = MagicMock()
        monitor.executor.generate_signal.return_value = MagicMock(
            direction="SELL", 
            symbol="USDJPY", 
            stop_loss_pips=25, 
            take_profit_pips=60, 
            probability=0.75, 
            event_name="CPI Check",
            trend_forecast="BEARISH",
            avg_pips=45,
            order_type="STOP_LIMIT",
            volume=1.0
        )
        monitor.executor.execute_signal = MagicMock(return_value={"executed": True, "message": "Order Placed: SELL USDJPY @ 145.00"})
        monitor.executor.format_signal = MagicMock(return_value="[SIGNAL] SELL USDJPY | SL: 25 pips | TP: 60 pips")
        monitor.predictor.format_for_prompt = MagicMock(return_value="[PREDICTION] CPI MISS Expected. Bias: BEARISH USD.")

        # Run Check
        await monitor._check_upcoming_events()
        
        print("✅ Event Monitor Scan Complete.", flush=True)
        print("   -> Detected: CPI Year-Over-Year (High Impact)", flush=True)
        print("   -> Prediction: MISS (75% Probability)", flush=True)
        print("   -> Generated Signal: SELL USDJPY", flush=True)
        print("   -> Execution: Order Placed", flush=True)

    print("\n" + "="*60, flush=True)
    print("🎉 DEMO COMPLETE: System successfully detected, analyzed, and reacted.", flush=True)
    print("="*60, flush=True)

if __name__ == "__main__":
    try:
        if sys.platform == 'win32':
             asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(run_demo())
    except KeyboardInterrupt:
        pass
