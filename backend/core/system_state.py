from pydantic import BaseModel, Field
from typing import List, Deque
from collections import deque
import datetime

# --- Models ---
class LogEntry(BaseModel):
    timestamp: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)
    agent: str = "System"
    message: str
    type: str = "INFO" # INFO, ALERT, THOUGHT, TRADE

# --- World Model ---
class WorldState(BaseModel):
    bias: str = Field(default="NEUTRAL", description="Current Market Bias (BULLISH/BEARISH/RANGING)")
    risk_mode: str = Field(default="NORMAL", description="Risk Appetite (DEFENSIVE/AGGRESSIVE/NORMAL)")
    active_session: str = Field(default="UNKNOWN", description="Current Active Trading Session")
    last_updated: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)
    
    # In-Memory Log Buffer (Not persisted to DB, just for UI Stream)
    logs: List[LogEntry] = Field(default_factory=list)

    def update(self, bias=None, risk=None, session=None):
        if bias: self.bias = bias
        if risk: self.risk_mode = risk
        if session: self.active_session = session
        self.last_updated = datetime.datetime.utcnow()

    def add_log(self, agent: str, message: str, type: str = "INFO"):
        entry = LogEntry(agent=agent, message=message, type=type)
        self.logs.insert(0, entry) # Prepend for newest first
        if len(self.logs) > 50: # Keep last 50
            self.logs.pop()

world_state = WorldState() # Global Singleton
