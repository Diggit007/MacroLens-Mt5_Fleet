
import asyncio
import logging
import datetime
from typing import Optional
import os

# Internal Services
from backend.services.memory_store import memory_store
# We import Agent specifically or init inside run to avoid cycles if any
# But AgentService imports MemoryStore, so CognitiveLoop importing AgentService is OK.
from backend.services.agent_service import MacroLensAgentV2, CIOAgent

from backend.core.system_state import world_state

logger = logging.getLogger("CognitiveEngine")

def _get_provider_config():
    """Returns API config for the active LLM provider."""
    from backend.config import settings
    model = settings.LLM_MODEL
    
    if "kimi" in model or "moonshot" in model:
        return {
            "base_url": "https://integrate.api.nvidia.com/v1/chat/completions",
            "api_key": settings.NVIDIA_API_KEY.get_secret_value() if settings.NVIDIA_API_KEY else None,
            "model_id": "moonshotai/kimi-k2.5",
            "provider": "nvidia"
        }
    elif "glm" in model:
        return {
            "base_url": "https://api.z.ai/api/paas/v4/chat/completions",
            "api_key": settings.GLM_API_KEY.get_secret_value() if settings.GLM_API_KEY else None,
            "model_id": "glm-4.7",
            "provider": "glm"
        }
    else:  # deepseek (default)
        return {
            "base_url": "https://api.deepseek.com/chat/completions",
            "api_key": settings.DEEPSEEK_API_KEY.get_secret_value() if settings.DEEPSEEK_API_KEY else None,
            "model_id": "deepseek-chat",
            "provider": "deepseek"
        }

class CognitiveEngine:
    def __init__(self):
        self.is_running = False
        self.agent = None
        self.interval_seconds = 900 # 15 Minutes (Cost Optimization)

    async def start_loop(self):
        """
        Main entry point for the background thought process.
        """
        if self.is_running:
            return

        self.is_running = True
        logger.info("🧠 Cognitive Engine: Awakening...")

        # Initialize the Agent Personality (CIO implies high level strategy)
        self.agent = CIOAgent() 
        
        # Give the server a moment to startup fully before first thought
        await asyncio.sleep(10)

        while self.is_running:
            try:
                # 1. Check Schedule (Sleep on Weekends)
                # UTC Weekday: 0=Mon, 4=Fri, 5=Sat, 6=Sun
                weekday = datetime.datetime.utcnow().weekday()
                if weekday > 4: 
                    logger.info("🧠 Cognitive Engine: Market Closed (Weekend). Sleeping...")
                    await asyncio.sleep(3600) # Sleep 1 hour
                    continue

                # 2. The Thought Pulse
                await self._tick()

                # 3. Wait for next cycle
                logger.info(f"🧠 Cognitive Engine: Resting for {self.interval_seconds}s...")
                await asyncio.sleep(self.interval_seconds)

            except asyncio.CancelledError:
                logger.info("🧠 Cognitive Engine: Shutting down.")
                break
            except Exception as e:
                logger.error(f"🧠 Cognitive Engine Error: {e}")
                await asyncio.sleep(60) # Backoff on error

    async def _tick(self):
        """
        The OODA Loop: Observe -> Orient -> Decide (Internal Monologue)
        """
        logger.info("🧠 Cognitive Engine: Thinking...")

        # B. Orient (The Internal Prompt)
        # This prompt is unique: It asks the agent to talk to ITSELF, not the user.
        
        internal_prompt = """
        SYSTEM: You are the 'Subconscious' of the MacroLens Trading System.
        You are currently running in a background loop. The user is NOT watching.
        
        GOAL: Reflect on the current market environment (Time of day, Session) and your recent memories.
        
        INSTRUCTIONS:
        1. Check the Time (UTC). Identify which Session is active (Asian, London, NY).
        2. Recall recent user interactions from your Memory.
        3. Formulate a 'Bias' (BULLISH/BEARISH/RANGING) and 'Risk Mode' (DEFENSIVE/AGGRESSIVE).
        4. Output a JSON object:
        {
            "thought": "Your internal monologue here...",
            "bias": "BULLISH",
            "risk_mode": "NORMAL",
            "session": "LONDON"
        }
        """
        
        # C. Decide/Act (Call LLM via active provider)
        config = _get_provider_config()
        api_key = config['api_key']
        
        if not api_key:
            logger.warning("Cognitive Engine: No API Key. Creating dummy thought.")
            thought_text = "I cannot see the world (No API Key)."
        else:
             try:
                 payload = {
                     "model": config['model_id'],
                     "messages": [{"role": "user", "content": internal_prompt}],
                     "temperature": 0.7,
                     "max_tokens": 1024
                 }
                 
                 # DeepSeek supports JSON mode
                 if config['provider'] == "deepseek":
                     payload["response_format"] = {"type": "json_object"}
                 
                 resp = await self.agent.http_client.post(
                    config['base_url'],
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload
                 )
                 if resp.status_code == 200:
                     content = resp.json()['choices'][0]['message']['content']
                     
                     # Clean markdown wrappers if present
                     if "```json" in content:
                         content = content.split("```json")[1].split("```")[0]
                     elif "```" in content:
                         content = content.split("```")[1].split("```")[0]
                     
                     # Parse JSON
                     import json
                     data = json.loads(content.strip())
                     thought_text = data.get("thought", "No thought")
                     
                     # Update World Model
                     world_state.update(
                         bias=data.get("bias"),
                         risk=data.get("risk_mode"),
                         session=data.get("session")
                     )
                     
                     # Push to Live Feed
                     world_state.add_log(
                         agent="Cognitive Engine",
                         message=f"{thought_text[:120]}..." if len(thought_text) > 120 else thought_text,
                         type="THOUGHT"
                     )
                     logger.info(f"🧠 World State Updated: {world_state.bias} | {world_state.risk_mode}")
                     
                 else:
                     thought_text = f"Confusion ({resp.status_code})"
             except Exception as ex:
                 thought_text = f"Mental Block: {ex}"

        # D. Store (Memory)
        logger.info(f"🧠 Thought: {thought_text}")
        if memory_store:
            memory_store.add_memory(
                f"[INTERNAL THOUGHT] {thought_text}",
                {"type": "internal_monologue", "model": config['model_id']}
            )

# Global Instance
cognitive_engine = CognitiveEngine()
