
import logging
from typing import Dict, Optional
import json
import httpx
from backend.config import settings

from backend.core.system_state import world_state
try:
    from backend.services.memory_store import memory_store
except ImportError:
    memory_store = None

logger = logging.getLogger("DebateRoom")

def _get_provider_config():
    """Returns API config for the active LLM provider."""
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

class DebateOrchestrator:
    def __init__(self):
        # Independent client to avoid circular imports with AgentService
        self.http_client = httpx.AsyncClient(timeout=30.0)

    async def conduct_round_table(self, proposal: Dict, user_context: str) -> Dict:
        """
        Conducts a debate to validate a Trade Proposal.
        
        Flow:
        1. Scout (Input): "I want to Buy X."
        2. Guardian (Risk Check): Checks WorldState Risk + User PnL.
        3. CIO (Verdict): Final Decision.
        """
        symbol = proposal.get("symbol")
        action = proposal.get("action")
        
        logger.info(f"🏛️ Trading Council: Debating {action} on {symbol}")

        # Step 1: The Guardian's Critique (Risk Check)
        # We simulate the Guardian by checking the WorldState
        risk_mode = world_state.risk_mode
        bias = world_state.bias
        
        risk_objection = None
        
        # Simple Logic for Guardian (Faster than LLM)
        if risk_mode == "DEFENSIVE" and action == "BUY" and bias == "BEARISH":
            risk_objection = "High Risk: Buying against Bearish Bias in Defensive Mode."
        elif risk_mode == "DEFENSIVE":
            risk_objection = "Caution: Defensive Mode is active. Reduce position size."
            
        # Step 2: The Council Review (Prompting the CIO to Judge)
        debate_prompt = f"""
        SYSTEM: You are the CIO. You are presiding over the Trading Council.
        
        PROPOSAL (SCOUT):
        {json.dumps(proposal, indent=2)}
        
        RISK REPORT (GUARDIAN):
        Global Risk Mode: {risk_mode}
        Global Bias: {bias}
        Objection: {risk_objection or "None. Cleared for engagement."}
        
        USER CONTEXT:
        {user_context}
        
        INSTRUCTIONS:
        1. Review the Scout's proposal against the Guardian's report.
        2. If the Guardian objects strongly, provide a WARNING and suggest modifications (e.g., lower leverage, wait for confirmation). 
           Do NOT Reject unless the trade is catastrophic (e.g. Buying into a crash).
        3. Even if "Defensive Mode" is active, try to find a way to execute the trade SAFELY (e.g. reduced size).
        4. If aligned, APPROVE.
        
        OUTPUT JSON:
        {{
            "verdict": "APPROVED" | "CAUTION" | "MODIFIED",
            "modified_action": "...",
            "reasoning": "Explain your decision to the user in 1 sentence.",
            "debate_log": "Scout proposed x, Guardian warned y, Council advises z."
        }}
        """
        
        # Call LLM via active provider config
        try:
            config = _get_provider_config()
            
            if not config['api_key']:
                logger.warning("Debate Room: No API Key.")
                return {
                     "verdict": "ERROR",
                     "reasoning": "Strategy Center Offline (No API Key).",
                     "debate_log": "ABORTED"
                }
            
            payload = {
                "model": config['model_id'],
                "messages": [{"role": "user", "content": debate_prompt}],
                "temperature": 0.2,
                "max_tokens": 1024
            }
            
            # DeepSeek supports JSON mode
            if config['provider'] == "deepseek":
                payload["response_format"] = {"type": "json_object"}
            
            resp = await self.http_client.post(
                config['base_url'],
                headers={"Authorization": f"Bearer {config['api_key']}"},
                json=payload
            )
            
            result = resp.json()['choices'][0]['message']['content']
            
            # Clean markdown wrappers if present
            if "```json" in result:
                result = result.split("```json")[1].split("```")[0]
            elif "```" in result:
                result = result.split("```")[1].split("```")[0]
            
            verdict_data = json.loads(result.strip())
            
            # Log to Memory
            if memory_store:
                memory_store.add_memory(
                    f"[TRADING COUNCIL VERDICT] {symbol} {action}: {verdict_data['verdict']} | {verdict_data['reasoning']}",
                    {"type": "debate_verdict", "symbol": symbol}
                )
                
            # Push to Live Feed - REMOVED for Privacy
            # world_state.add_log(
            #     agent="CIO",
            #     message=f"Verdict: {verdict_data['verdict']} on {symbol}. {verdict_data.get('reasoning', '')[:80]}...",
            #     type="ALERT"
            # )
            
            return verdict_data

        except Exception as e:
            logger.error(f"Debate failed: {e}")
            # Fallback: Approve if no error, or Safer to Reject?
            return {
                "verdict": "ERROR",
                "reasoning": f"War Room disrupted: {e}",
                "debate_log": "Communication Error"
            }

debate_room = DebateOrchestrator()
