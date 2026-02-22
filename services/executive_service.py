import logging
import json
from typing import Dict, List, Optional
from backend.services.agent_service import AgentFactory, CIOAgent
from backend.services.risk_manager import RiskManager

logger = logging.getLogger("ExecutiveService")

class ExecutiveService:
    """
    The Orchestrator for the "Council of Agents".
    Coordinates the Scout (Trade Manager), Guardian (Risk Manager), and CIO (LLM).
    """
    
    def __init__(self, risk_manager: RiskManager):
        self.risk_manager = risk_manager
        # Lazy load agent to avoid circular imports or early init issues
        self.cio_agent: Optional[CIOAgent] = None
        
    def _get_agent(self) -> CIOAgent:
        if not self.cio_agent:
            agent = AgentFactory.get_agent("MLens-CIO")
            if isinstance(agent, CIOAgent):
                self.cio_agent = agent
            else:
                logger.error("Failed to load CIO Agent. Falling back to base.")
                # This explicitly requires CIOAgent class methods, so this is critical.
                raise ValueError("AgentFactory did not return a CIOAgent")
        return self.cio_agent

    async def evaluate_trade_proposal(self, user_id: str, account_id: str, 
                                      proposal: Dict, open_positions: List[Dict], 
                                      equity: float, total_exposure: float) -> Dict:
        """
        Main Entry Point.
        1. Gathers Risk Report from Guardian.
        2. Submits Proposal + Report to CIO Agent.
        3. Returns Final Decision.
        """
        try:
            # 1. Get Risk Report (The Guardian)
            risk_report = await self.risk_manager.get_risk_report(
                user_id=user_id, 
                symbol=proposal.get('symbol'),
                current_exposure=total_exposure,
                equity=equity,
                account_id=account_id,
                open_positions=open_positions
            )
            
            # 2. Consult CIO (The Boss)
            agent = self._get_agent()
            decision_packet = await agent.review_proposal(
                proposal=proposal, 
                risk_report=risk_report
            )
            
            # 3. Log the "Council Meeting"
            logger.info(f"CIO Decision for {proposal.get('symbol')}: {decision_packet.get('decision')}")
            
            # Attach the risk report for context if needed downstream
            decision_packet['risk_context'] = risk_report
            
            return decision_packet
            
        except Exception as e:
            logger.error(f"Executive Evaluation Failed: {e}")
            # Fail Safe: Propose REJECT if the executive layer crashes
            return {
                "decision": "REJECTED", 
                "reasoning": "Executive Service Error (Fail Safe)",
                "user_report": "System safety check failed. Trade rejected."
            }
