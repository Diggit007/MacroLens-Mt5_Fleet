import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from backend.services.cognitive_engine import CognitiveEngine

class TestCognitiveEngine(unittest.IsolatedAsyncioTestCase): # Python 3.8+
    async def test_tick_lifecycle(self):
        engine = CognitiveEngine()
        engine.agent = AsyncMock()
        engine.agent.http_client = AsyncMock()
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'choices': [{
                'message': {
                    'content': '{"thought": "Test Thought", "bias": "BULLISH", "risk_mode": "AGGRESSIVE", "session": "NY"}'
                }
            }]
        }
        engine.agent.http_client.post.return_value = mock_response
        
        with patch("backend.services.cognitive_engine.world_state") as mock_world:
            await engine._tick()
            
            # Verify update called with specific kwargs
            mock_world.update.assert_called_with(
                bias="BULLISH",
                risk="AGGRESSIVE",
                session="NY"
            )
            
            # Verify add_log called with correct agent name "Cognitive Engine"
            # Code uses kwargs: agent="Cognitive Engine", message=..., type=...
            call_kwargs = mock_world.add_log.call_args.kwargs
            self.assertEqual(call_kwargs.get("agent"), "Cognitive Engine")
            self.assertEqual(call_kwargs.get("type"), "THOUGHT")

if __name__ == '__main__':
    unittest.main()
