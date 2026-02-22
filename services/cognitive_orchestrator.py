import asyncio
import logging
from enum import Enum, auto
from typing import Dict, List, Optional, Any
from datetime import datetime

# Configure logging
logger = logging.getLogger("CognitiveOrchestrator")

class AgentState(Enum):
    IDLE = auto()
    PERCEIVING = auto()
    REASONING = auto()
    EXECUTING = auto()
    REFLECTING = auto()

class CognitiveOrchestrator:
    """
    The 'Consciousness Layer' of the agent.
    Orchestrates the Perception -> Reasoning -> Execution -> Reflection loop.
    """
    
    def __init__(self, agent_instance):
        """
        Initialize with the main agent instance to access its tools and memory.
        :param agent_instance: Instance of MacroLensAgentV2
        """
        self.agent = agent_instance
        self.state = AgentState.IDLE
        self.short_term_memory: List[Dict] = []
        self._stop_loop = False
        self.loop_interval = 60.0 # Seconds between Anti-WAIT scans

    async def think(self, user_query: Optional[str] = None):
        """
        The main cognitive loop.
        If user_query is provided, it's a reactive flow.
        If None, it might be a proactive Anti-WAIT flow (background).
        """
        
        # 1. PERCEPTION
        self._transition_to(AgentState.PERCEIVING)
        perception_data = await self._perceive(user_query)
        
        # 2. REASONING
        self._transition_to(AgentState.REASONING)
        plan = await self._reason(perception_data, user_query)
        
        # 3. EXECUTION
        self._transition_to(AgentState.EXECUTING)
        result = await self._execute(plan)
        
        # 4. REFLECTION (Self-Correction/Learning)
        self._transition_to(AgentState.REFLECTING)
        final_response = await self._reflect(result, user_query)
        
        self._transition_to(AgentState.IDLE)
        return final_response

    async def start_background_loop(self):
        """
        Anti-WAIT System: Runs in the background to scan for high-conviction events.
        """
        logger.info("Anti-WAIT Background Loop Application Started.")
        while not self._stop_loop:
            try:
                # Proactive thought injection
                # For now, we just log "Thinking..."
                # In future, this calls self.think() with a generated system prompt
                pass
            except Exception as e:
                logger.error(f"Error in background loop: {e}")
            
            await asyncio.sleep(self.loop_interval)

    def _transition_to(self, new_state: AgentState):
        logger.info(f"Transitioning: {self.state.name} -> {new_state.name}")
        self.state = new_state

    # --- STAGE HANDLERS ---

    async def _perceive(self, user_query: Optional[str]) -> Dict:
        """
        Gather raw data from sensors (Market Data, News, User Input).
        """
        data = {
            "timestamp": datetime.utcnow(),
            "user_query": user_query,
            "market_context": {}, # Placeholder for quick market health check
        }
        
        # Integration point: Call agent's existing efficient data gatherers
        # e.g. data["market_context"] = await self.agent.get_market_health_summary()
        
        return data

    async def _reason(self, perception_data: Dict, user_query: Optional[str]) -> Dict:
        """
        Synthesize data and decide on a plan (Tools to use).
        """
        # This replaces the logic currently in agent_service._decide_tools
        # We can reuse the AgentRouter logic here
        
        if user_query:
            # Reactive Reasoning: "User asked X, so I should do Y"
            # Delegate to existing router for now, but wrapper in a "Plan" object
            return {"type": "reactive", "data": perception_data}
        else:
            # Proactive Reasoning: "Market did X, is it interesting?"
            return {"type": "proactive", "data": perception_data}

    async def _execute(self, plan: Dict) -> Any:
        """
        Execute the plan (Call Tools, Run Analysis).
        """
        # If reactive, we might call the agent.ask logic (or parts of it)
        # For this refactor, we are building the shell first.
        return "Execution Result Placeholder"

    async def _reflect(self, execution_result: Any, user_query: Optional[str]) -> str:
        """
        Review the result. Did it answer the question? Is it safe?
        Construct the final response.
        """
        if user_query:
            return f"Reflected Response: {execution_result}"
        return ""
