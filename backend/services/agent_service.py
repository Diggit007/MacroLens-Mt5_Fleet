import asyncio
import httpx
import pandas as pd
import numpy as np
import os
import aiosqlite
from typing import Dict, List, Optional, Literal
from datetime import datetime, timedelta
import json
import logging
import time
import re
from pathlib import Path
from dotenv import load_dotenv
from backend.services.tools.research import research_tool


# Load environment variables from .env.local (search multiple locations)
BASE_DIR = Path(__file__).parent.parent
SENTIMENT_FILE = BASE_DIR / "retail_sentiment.json"
ENV_PATHS = [
    BASE_DIR / ".env.local",
    BASE_DIR / ".env",
    BASE_DIR / "macrolens_ai" / "MacroLens_Ai_Analyzer" / ".env.local",
    Path(__file__).parent / ".env.local",
]
for env_path in ENV_PATHS:
    if env_path.exists():
        load_dotenv(env_path)
        break

from backend.config import settings
# from backend.services.websocket_manager import websocket_manager

# Configuration
NEWS_API_KEY = os.getenv("NEWS_API_KEY") # Keep as os.getenv if not in settings yet, or add to settings
# Derive API key from the active provider (no longer hardcoded to OpenAI)
def _get_active_api_key():
    """Returns the API key for the currently configured LLM provider."""
    provider = settings.AI_PROVIDER
    if provider == "nvidia":
        return settings.NVIDIA_API_KEY.get_secret_value() if settings.NVIDIA_API_KEY else None
    elif provider == "glm":
        return settings.GLM_API_KEY.get_secret_value() if settings.GLM_API_KEY else None
    else:  # deepseek (default)
        return settings.DEEPSEEK_API_KEY.get_secret_value() if settings.DEEPSEEK_API_KEY else None

LLM_API_KEY = _get_active_api_key()
# SQLite DB Path (Default to market_data.db in the same folder or root)
# DB_PATH moved to backend/services/database.py
SENTIMENT_FILE = Path(__file__).parent.parent / "retail_sentiment.json"

# Global limit to prevent 429 errors (Max 5 allowed by provider, we use 2 to be safe)
# [SCALABILITY FIX] Increased to 15 for 50-user support. 
# Note: Ensure OpenAI Tier 2+ or similar limits are active.
RATE_LIMITER = asyncio.Semaphore(15)

logger = logging.getLogger("MacroLensAgent")
logging.basicConfig(level=logging.INFO)

# Memory Service Integration
try:
    from backend.services.memory_store import memory_store
except ImportError:
    logger.warning("MemoryStore not found or dependencies missing (chromadb). Memory disabled.")
    memory_store = None

# Debate Room Integration
try:
    from backend.services.debate_room import debate_room
except ImportError:
    logger.warning("DebateRoom not found.")
    debate_room = None


# =============================================================================
# PYDANTIC MODELS (Validation)
# =============================================================================




# =============================================================================
# DATABASE CONNECTION POOL
# =============================================================================






# =============================================================================
# TECHNICAL ANALYZER
# =============================================================================
# Refactored to backend/services/technical_analysis.py
from backend.services.technical_analysis import TechnicalAnalyzer, SymbolBehaviorAnalyzer
from backend.services.ai_engine import AIEngine
from backend.core.database import DatabasePool
from backend.services.cot.engine import COTEngine
from backend.services.news_retriever import NewsRetriever
from backend.services.macro_divergence import MacroDivergence

# =============================================================================
# NEWS SENTIMENT ANALYZER
# =============================================================================
from backend.services.metaapi_service import get_symbol_price, fetch_candles


class NewsSentimentAnalyzer:
    """
    Scores the news headlines to give the AI a weighted opinion.
    """
    def analyze(self, news_text: str) -> int:
        if not news_text: return 0
        
        bullish_words = ["growth", "surplus", "rally", "strong", "gain", "rise", "positive", "bull", "support"]
        bearish_words = ["loss", "fall", "drop", "decline", "recession", "weak", "risk", "bear", "resistance"]
        
        score = 0
        text = news_text.lower()
        
        for word in bullish_words:
            if word in text: score += 1
        for word in bearish_words:
            if word in text: score -= 1
            
        return max(min(score, 5), -5)


# =============================================================================
# MAIN AGENT CLASS
# =============================================================================

class MacroLensAgentV2:
    def __init__(self):
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.behavior_analyzer = SymbolBehaviorAnalyzer()

        self.cot_engine = COTEngine()
        try:
            self.cot_engine.load_data()  # Pre-load COT data
        except Exception as e:
            logger.error(f"Failed to load COT data: {e}")
            
        self.ai_engine = AIEngine(LLM_API_KEY, self.http_client)
        self.news_retriever = NewsRetriever()
        try:
            self.macro_divergence = MacroDivergence()
        except Exception as e:
            logger.error(f"Failed to init MacroDivergence: {e}")
            self.macro_divergence = None
        
    async def close(self):
        await self.http_client.aclose()

    async def fetch_candles(self, symbol: str, timeframe: str, limit: int = 50) -> List[Dict]:
        """Fetch candles - to be overridden by fetch_callback in process_single_request"""
        return []

    async def get_upcoming_events(self, currency: str = "USD", simulated_time: datetime = None) -> List[Dict]:
        """Fetch High Impact events using Connection Pool"""
        try:
            # Use the pool instead of creating new connection
            db = await DatabasePool.get_connection()
            
            # Enable WAL mode for better concurrency
            await db.execute("PRAGMA journal_mode=WAL")
            db.row_factory = aiosqlite.Row
            
            now = simulated_time if simulated_time else datetime.utcnow()
            future = now + timedelta(hours=12)
            
            query = """
                SELECT event_name, event_date, event_time, impact_level 
                FROM economic_events 
                WHERE impact_level IN ('High', 'Medium')
                AND currency = ?
                AND (event_date || ' ' || event_time) BETWEEN ? AND ?
            """
            
            # Format without seconds to match DB "YYYY-MM-DD HH:MM"
            now_str = now.strftime("%Y-%m-%d %H:%M")
            future_str = future.strftime("%Y-%m-%d %H:%M")

            cursor = await db.execute(query, (currency, now_str, future_str))
            rows = await cursor.fetchall()
            
            events = [dict(row) for row in rows]
            
            # QUANT UPGRADE: Attach Historical Statistics for Z-Score Calculation
            for ev in events:
                try:
                    # Query history for this specific event name
                    h_query = """
                        SELECT actual_value, forecast_value 
                        FROM economic_events 
                        WHERE event_name = ? 
                        AND actual_value IS NOT NULL 
                        AND forecast_value IS NOT NULL
                        ORDER BY event_date DESC 
                        LIMIT 30
                    """
                    h_cursor = await db.execute(h_query, (ev['event_name'],))
                    h_rows = await h_cursor.fetchall()
                    
                    if len(h_rows) >= 4:
                        # Calculate Deviation StdDev (The "Sigma")
                        diffs = [(float(r[0]) - float(r[1])) for r in h_rows]
                        if diffs:
                            std_dev = float(np.std(diffs))
                            ev['hist_std_dev'] = std_dev
                            
                            # QUANT: SURPRISE STREAK
                            streak = 0
                            if diffs:
                                first_sign = 1 if diffs[0] > 0 else -1 if diffs[0] < 0 else 0
                                if first_sign != 0:
                                    for d in diffs:
                                        s = 1 if d > 0 else -1 if d < 0 else 0
                                        if s == first_sign:
                                            streak += s
                                        else:
                                            break
                            ev['streak'] = streak
                        else:
                             ev['hist_std_dev'] = None
                             ev['streak'] = 0
                    else:
                        ev['hist_std_dev'] = None
                        ev['streak'] = 0
                        
                except Exception as ex:
                    logger.warning(f"History Stat Error for {ev['event_name']}: {ex}")
                    ev['hist_std_dev'] = None

            return events

        except Exception as e:
            logger.error(f"DB Error (SQLite): {e}")
            return []

    async def get_institutional_bias(self, symbol: str, simulated_time: datetime = None) -> str:
        """
        Fetch context-aware institutional views using NewsRetriever.
        Input: "EURUSD" -> returns formatted string with EUR and USD context.
        """
        try:
            # Use the Async NewsRetriever
            context_data = await self.news_retriever.get_article_context(symbol, simulated_time=simulated_time)
            
            if not context_data:
                return "No recent institutional data found."
                
            text = [f"### INSTITUTIONAL CONTEXT FOR {symbol}"]
            
            for currency, articles in context_data.items():
                text.append(f"\n--- {currency} DRIVERS ---")
                for art in articles:
                    # Parse simplified summary for prompt efficiency
                    text.append(f"- [{art.source}] {art.title}")
                    if art.summary:
                        # Truncate summary to avoid token bloat
                        clean_summary = art.summary[:200] + "..." if len(art.summary) > 200 else art.summary
                        text.append(f"  > Insight: {clean_summary}")
                        
            return "\n".join(text)
        except Exception as e:
            logger.error(f"News Context Error: {e}")
            return "Error reading institutional data."

    def get_cached_sentiment(self, symbol: str) -> str:
        """Reads retail_sentiment.json and finds the symbol."""
        if not SENTIMENT_FILE.exists(): 
            return "No Retail Sentiment Data."
        try:
            with open(SENTIMENT_FILE, "r") as f:
                data = json.load(f)
            item = next((x for x in data if x['symbol'] == symbol), None)
            if item:
                return f"MyFxBook Retail Crowd: {item['long_pct']}% Long vs {item['short_pct']}% Short.\nSignal: {item['signal']} ({item['strength']}).\nBias: {item['bias']}"
            return "Symbol not found in retail sentiment data."
        except Exception as e:
            return f"Error reading sentiment file: {e}"

    def _get_retail_sentiment_data(self, symbol: str) -> Optional[Dict]:
        """Returns structured retail sentiment data for frontend consumption."""
        try:
            logger.info(f"[SENTIMENT] Looking up {symbol} in {SENTIMENT_FILE} (exists={SENTIMENT_FILE.exists()})")
            if not SENTIMENT_FILE.exists():
                logger.warning(f"[SENTIMENT] File does not exist: {SENTIMENT_FILE}")
                return None
            with open(SENTIMENT_FILE, "r") as f:
                data = json.load(f)
            logger.info(f"[SENTIMENT] Loaded {len(data)} items. Symbols: {[d['symbol'] for d in data[:5]]}...")
            result = next((x for x in data if x['symbol'] == symbol), None)
            logger.info(f"[SENTIMENT] Lookup result for {symbol}: {result}")
            return result
        except Exception as e:
            logger.warning(f"Failed to fetch retail sentiment from json: {e}")
            return None

    # _analyze_event_math moved to AIEngine


    def check_imminent_news(self, events: List[Dict]) -> bool:
        """Returns True if there is high impact news in < 1 hour"""
        return len(events) > 0



    async def _decide_tools(self, user_query: str, context_summary: str) -> Dict:
        """
        Asks the LLM to decide which tool to use.
        Returns: {"tool": "SEARCH"|"RISK"|"MACRO"|"TRADE"|"NONE", "args": "..."}
        """
        system_prompt = """
        You are the Routing System for a Hedge Fund AI.
        Your job is to select the single best tool to answer the user's request.
        
        AVAILABLE TOOLS:
        1. [SEARCH] - For news, external events, "why is X moving", or specific questions not about the user's account.
        2. [RISK] - For questions about specific account drawdown, exposure, leverage, or "risk check".
        3. [MACRO] - For economic calendar, currency strength, or broad market divergence.
        4. [TRADE] - ONLY for explicit requests to "find a trade", "scan for setups", or "analyze [SYMBOL]".
        5. [NONE] - For greetings, philosophy, or simple chat.

        RESPONSE FORMAT (JSON ONLY):
        {
            "tool": "TOOL_NAME",
            "reason": "Brief reason",
            "search_query": "Optimized search query (only if tool is SEARCH or MACRO)",
            "symbol": "extracted symbol (only if tool is TRADE or RISK or MACRO and specific asset mentioned)"
        }
        """
        
        user_prompt = f"User Query: {user_query}\nContext: {context_summary}"
        
        try:
            # Quick call to LLM (using a cheaper/faster model if possible, but reusing main engine for now)
            # using 'json_object' mode if supported, or just prompt engineering
            response = await self.ai_engine.get_completion(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=150,
                temperature=0.1
            )
            
            # Simple JSON parsing
            clean_text = response.replace("```json", "").replace("```", "").strip()
            decision = json.loads(clean_text)
            return decision
        except Exception as e:
            logger.warning(f"Tool Decision Failed: {e}")
            return {"tool": "NONE"}

    async def ask(self, user_query: str, user_id: str = None, account_id: str = None, model_override: str = None, user_data: Dict = None) -> Dict:
        """
        Main Agent Entry Point.
        Unrestricted flow: Plan -> Tool -> Answer.
        """
        start_time = time.time()
        
        # 1. Gather Context (Lite)
        # We fetch basic account info to help the router, but don't do full analysis yet.
        context_block = ""
        account_summary = "No Account Connected"
        
        if account_id and user_id:
             # Just get header info (Equity, Margin)
             # Use Cache/Snapshot if possible
             pass 

        # 2. DECIDE TOOL
        decision = await self._decide_tools(user_query, context_block)
        selected_tool = decision.get("tool", "NONE")
        tool_output = ""
        
        logger.info(f"Agent Router: {user_query} -> {selected_tool}")

        # 3. EXECUTE TOOL
        agent_identity = "CIO" # Default
        
        if selected_tool == "SEARCH":
            agent_identity = "RESEARCHER"
            query = decision.get("search_query", user_query)
            results = research_tool.search(query, max_results=4)
            tool_output = f"[WEB SEARCH RESULTS for '{query}']\n"
            for r in results:
                tool_output += f"- {r.get('title')}: {r.get('snippet')} ({r.get('link')})\n"
                
        elif selected_tool == "RISK":
            agent_identity = "RISK MANAGER"
            if account_id:
                try:
                    from backend.services.metaapi_service import get_account_information, get_positions
                    acct = await get_account_information(account_id)
                    positions = await get_positions(account_id)
                    
                    tool_output = f"[RISK DATA for Account {account_id}]\n"
                    tool_output += f"Balance: {acct.get('balance')}\n"
                    tool_output += f"Equity: {acct.get('equity')}\n"
                    tool_output += f"Margin Level: {acct.get('marginLevel')}%\n"
                    tool_output += f"Free Margin: {acct.get('freeMargin')}\n"
                    tool_output += f"Open Positions: {len(positions)}\n"
                    
                    # Calculate Exposure
                    total_lots = sum(p.get('volume', 0) for p in positions)
                    tool_output += f"Total Exposure (Lots): {total_lots}\n"
                    
                except Exception as e:
                    tool_output = f"Error fetching risk data: {e}"
            else:
                tool_output = "No account connected. Cannot perform risk analysis."
            
        elif selected_tool == "MACRO":
            agent_identity = "MACRO ANALYST"
            # 1. Calendar
            try:
                events = await self.get_upcoming_events()
                ev_str = "\n".join([f"- {e['event_time']} {e['currency']} {e['event_name']} ({e['impact_level']})" for e in events[:5]])
                tool_output = f"[UPCOMING HIGH IMPACT EVENTS]\n{ev_str}\n"
            except: 
                tool_output = "[CALENDAR] No data.\n"
                
            # 2. Divergence
            if self.macro_divergence:
                try:
                   scan_results = await asyncio.to_thread(self.macro_divergence.scan_for_divergence)
                   top = scan_results[:3]
                   div_str = "\n".join([f"- {s['symbol']}: {s['recommendation']} (Score: {s['divergence_score']})" for s in top])
                   tool_output += f"\n[MACRO DIVERGENCE OPPORTUNITIES]\n{div_str}"
                except Exception as e:
                   tool_output += f"\n[DIVERGENCE SCAN FAILED] {e}"
            
        elif selected_tool == "TRADE":
            agent_identity = "TRADE MANAGER"
            symbol = decision.get("symbol")
            if symbol:
                symbol = symbol.upper().replace("/", "")
                # Quick Price Check
                try:
                    from backend.services.metaapi_service import get_symbol_price
                    # Resolve ID if needed (router usually passes it)
                    target_acc = account_id if account_id else settings.DEFAULT_ACCOUNT_ID
                    price = await get_symbol_price(target_acc, symbol)
                    
                    # Institutional Context
                    bias = await self.get_institutional_bias(symbol)
                    
                    tool_output = f"[MARKET DATA for {symbol}]\n"
                    tool_output += f"Price: {price.get('bid')} / {price.get('ask')}\n"
                    tool_output += f"\n{bias}\n"
                    
                    # If user asked for analysis, we might trigger it, but for chat we keep it "Advice" level
                    # unless we want to call get_trading_signal (COSTLY).
                    # Let's add a note.
                    tool_output += "\n[SYSTEM NOTE] Full technical analysis requires the 'Analyze' button, but you can provide a strategic outlook based on this data."
                    
                except Exception as e:
                    tool_output = f"Error fetching data for {symbol}: {e}"
            else:
                tool_output = "No symbol specified in query. Ask user to clarify which asset to analyze."
            
        # 4. FINAL SYNTHESIS
        # Construct Final Prompt
        system_persona = f"You are the {agent_identity} of a Hedge Fund."
        
        full_prompt = f"""
        SYSTEM: {system_persona}
        
        USER QUERY: {user_query}
        
        TOOL OUTPUT:
        {tool_output}
        
        INSTRUCTIONS:
        - Answer the user's question using the Tool Output.
        - Be professional, concise, and insightful.
        - If the tool output is empty or irrelevant, apologize and answer to the best of your ability.
        """
        
        # Call LLM
        reply_text = await self.ai_engine.get_completion(
            system_prompt=system_persona, # Or generic system prompt
            user_prompt=full_prompt,
            max_tokens=800
        )
        
        # Logic for Rich Responses (JSON/Analysis) remains distinct if needed, 
        # but for Chat we return text.
        
        return {
            "text": reply_text,
            "agent": agent_identity,
            "tool_used": selected_tool
        }
    
    def _construct_prompt(self, symbol: str, multi_tf_data: Dict, calendar: List, 
                          behavior_report: str, institutional_bias: str, 
                          retail_sentiment: str, risk_params: Dict, 
                          confluence_score: Optional[Dict] = None,
                          price_action_score: Optional[Dict] = None,
                          position_context: Optional[Dict] = None) -> str:
        """
        Builds the enhanced prompt with strict checklist and data injection.
        Delegates to AIEngine.
        """
        prompt = self.ai_engine.construct_prompt(
            symbol, multi_tf_data, calendar, 
            behavior_report, institutional_bias, retail_sentiment, 
            risk_params, confluence_score, price_action_score
        )
        
        # Inject Position Context (Trade Management Mode)
        if position_context:
            pos_block = f"""
### 🚨 ACTIVE TRADE CONTEXT (MANAGE THIS POSITION) 🚨
You are managing an OPEN position. Do NOT give generic analysis. Decide to HOLD, CLOSE, or TRAIL.

POSITION DETAILS:
- Symbol: {symbol}
- Type: {position_context.get('type', 'UNKNOWN')}
- Entry Price: {position_context.get('entry_price', 0)}
- Current Price: {position_context.get('current_price', 0)}
- P&L: {position_context.get('pnl_pips', 0):.1f} pips ({position_context.get('net_profit', 0):.2f} USD)
- Time in Trade: {position_context.get('time_in_trade', 'Unknown')}
- Current SL: {position_context.get('sl', 'None')}
- Current TP: {position_context.get('tp', 'None')}

YOUR TASK:
1. Analyze if the original trade thesis is still valid.
2. If Price Action supports the direction, recommend HOLD or ADD.
3. If Price Action shows weakness/reversal, recommend CLOSE (Partial or Full).
4. If in profit, recommend BREAKEVEN or TRAIL SL logic based on Volatility (ATR).
"""
            # Insert before "INSTRUCTIONS" or append to context
            prompt = prompt.replace("USER INQUIRY:", f"{pos_block}\n\nUSER INQUIRY:")
            
        return prompt

    async def ask(self, user_query: str, user_id: str = None, account_id: str = None, model_override: str = None, user_data: Dict = None) -> Dict:
        """
        ChatKit Coordinator.
        Routes the user's query to the specialized agent (Persona) with injected context.
        Returns: { "text": str, "agent": str }
        """
        try:
            # --- 1. Intent Detection & ACCESS CONTROL ---
            # --- 1. Intent Detection & ACCESS CONTROL ---
            # [ENTERPRISE] Trial Logic REMOVED per user request
            
            query_upper = user_query.upper()

            query_upper = user_query.upper()
            
            # Default Persona
            agent_identity = "CIO" # Default to Strategy/Analysis
            system_persona = (
                "You are the CIO (Chief Investment Officer). You provide strategic market analysis and high-level guidance. "
                "PRIORITY FOCUS: Prioritize Major FX Pairs (EURUSD, GBPUSD), Gold (XAUUSD), Bitcoin (BTCUSD), and US Indices (US30, NAS100). "
                "Always check these first if the user asks for a general scan."
            )
            
            # Keywords
            risk_keywords = ["RISK", "MARGIN", "DRAWDOWN", "EXPOSURE", "SAFE", "LOT SIZE", "STOP LOSS", "SL", "PROTECT"]
            trade_keywords = ["CLOSE", "OPEN", "BUY", "SELL", "ENTRY", "EXIT", "TP", "TARGET", "TRADE", "POSITION"]
            
            if any(k in query_upper for k in risk_keywords):
                agent_identity = "RISK MANAGER"
                system_persona = (
                    "You are the Guardian (Risk Manager). Your ONLY focus is capital preservation, margin safety, and risk limits. "
                    "Be strict and protective. "
                    "PRIORITY MONITORING: Pay extra attention to volatility in Gold, BTC, and US30."
                )
            elif any(k in query_upper for k in trade_keywords):
                agent_identity = "TRADE MANAGER"
                system_persona = (
                    "You are the Scout (Trade Manager). You monitor open positions and execute trades. You are tactical and action-oriented. "
                    "PRIORITY MARKETS: Focus scanning on Majors, Crypto (BTC), and US Indices."
                )
            elif "SWING" in query_upper or "DIVERGENCE" in query_upper or "MACRO" in query_upper:
                agent_identity = "SWING TRADER"
                system_persona = (
                    "You are the Macro Swing Trader. You focus on Fundamental Divergence between economies. "
                    "Analyze the provided Country Health Scores and Policy Divergence to suggest multi-day swing trades. "
                    "Look for Strong vs Weak economies."
                )
                if self.macro_divergence:
                    try:
                        # Run scan in thread to avoid blocking
                        scan_results = await asyncio.to_thread(self.macro_divergence.scan_for_divergence)
                        
                        # Format top 3 opportunities
                        top_opps = scan_results[:3]
                        opp_str = "\n".join([
                            f"- {op['symbol']} ({op['recommendation']}): Score {op['divergence_score']}. Conviction: {op.get('conviction', 'LOW')}. Carry: {op.get('carry_spread', 0)}%. {op['rationale']}"
                            for op in top_opps
                        ])
                        context_block += f"\n[MACRO DIVERGENCE SCAN]\n{opp_str}\n"
                        
                        # If user asked about specific symbol, inject its specific macro data
                        if found_symbol:
                            specific_opp = next((op for op in scan_results if op['symbol'] == found_symbol), None)
                            if specific_opp:
                                context_block += f"\n[MACRO DETAIL FOR {found_symbol}]\n"
                                context_block += f"Rationale: {specific_opp['rationale']}\n"
                                context_block += f"Base Score: {specific_opp['base_score']} vs Quote Score: {specific_opp['quote_score']}\n"
                    except Exception as e:
                        logger.error(f"Macro Scan Error: {e}")
                        context_block += "\n[MACRO SCAN ERROR] Could not run divergence scan.\n"
            
            # --- 2. Context Gathering (The Knowledge Graph) ---
            context_block = ""
            
            # [NEW] COGNITIVE ENGINE CONTEXT (The Subconscious)
            try:
                from backend.core.system_state import world_state
                
                # Get the last few thoughts from the log
                recent_logs = world_state.get_logs(limit=5)
                thoughts = [l['message'] for l in recent_logs if l['agent'] == 'Cognitive Engine']
                last_thought = thoughts[-1] if thoughts else "No recent thoughts."
                
                cognitive_context = f"""
[COGNITIVE STATE (INTERNAL MONOLOGUE)]
- Market Bias: {world_state.bias}
- Risk Mode: {world_state.risk_mode}
- Active Session: {world_state.session}
- Recent Thought: "{last_thought}"
"""
                context_block += cognitive_context
            except Exception as e:
                logger.warning(f"Failed to inject Cognitive Context: {e}")

            # Shared: Fetch User History & Live State
            if user_id:
                try:
                    from backend.services.trade_manager_service import trade_manager
                    history = await trade_manager.get_history(user_id, limit=5)
                    hist_str = "\n".join([f"- {h['timestamp'].split('T')[1][:5]} {h['symbol']} {h['action']}: {h['reasoning']}" for h in history])
                    context_block += f"\n[RECENT SYSTEM ACTIONS]\n{hist_str}\n"

                    if account_id:
                        try:
                            from backend.services.metaapi_service import get_account_information
                            acct_data = await get_account_information(account_id)
                            if "error" not in acct_data:
                                balance = acct_data.get('balance', 0)
                                equity = acct_data.get('equity', 0)
                                positions = acct_data.get('positions', [])
                                open_pnl = sum([p.get('profit', 0) for p in positions])
                                pos_str = "No active positions."
                                if positions:
                                    pos_lines = []
                                    for p in positions:
                                        profit = p.get('profit', 0)
                                        icon = "🟢" if profit >= 0 else "🔴"
                                        pos_lines.append(f"{icon} {p['symbol']} {p['type'].split('_')[-1]} {p['volume']}lots @ {p['openPrice']} | PnL: ${profit:.2f}")
                                    pos_str = "\n".join(pos_lines)
                                context_block += f"\n[LIVE ACCOUNT STATUS]\nBalance: ${balance:,.2f}\nEquity: ${equity:,.2f}\nOpen PnL: ${open_pnl:,.2f}\n\n[OPEN POSITIONS]\n{pos_str}\n"
                        except Exception as acct_err:
                             logger.warning(f"Live account fetch failed: {acct_err}")
                except Exception as ex: 
                    logger.warning(f"Context fetch warning: {ex}")
            else:
                # Explicitly inform Agent that User has NO account
                context_block += "\n[LIVE ACCOUNT STATUS]\nStatus: No Trading Account Linked.\nConstraint: You cannot check positions or balance.\nInstruction: If user asks for trade, suggest setup providing Entry/SL/TP but clarify you cannot execute it.\n"

            # Shared: Long Term Memory
            if memory_store and user_id:
                try:
                    # STRICT PRIVACY: Only recall memories belonging to THIS user
                    past_mems = memory_store.recall(
                        user_query, 
                        n_results=2,
                        filter_meta={"user_id": user_id}
                    )
                    if past_mems:
                        mem_str = "\n".join([f"- ({m['metadata']['timestamp'][:10]}) {m['text'][:150]}..." for m in past_mems])
                        # SANITIZE: Rename War Room to Trading Council in memory
                        mem_str = mem_str.replace("War Room", "Trading Council")
                        context_block += f"\n[LONG TERM MEMORY / PAST EXPERIENCE]\n{mem_str}\n"
                except Exception as mem_err:
                    logger.warning(f"Memory recall failed: {mem_err}")

            # --- DYNAMIC SYMBOL EXTRACTION (Universal Support) ---
            import re
            words = query_upper.split()
            found_symbol = None
            
            # 1. Check for exact 6-letter FX pairs (e.g. AUDNZD, EURUSD)
            # Regex: 6 uppercase letters
            fx_match = re.search(r'\b[A-Z]{6}\b', query_upper)
            if fx_match:
                found_symbol = fx_match.group(0)
            
            # 2. Check for Crypto/Indices shortcuts
            if not found_symbol:
                common_tickers = ["BTC", "ETH", "XAU", "GOLD", "US30", "DJI", "NAS100", "NDX", "SPX500", "SPX", "GER30", "DAX", "UK100"]
                shortcut_match = next((w for w in words if w in common_tickers), None)
                if shortcut_match:
                    if shortcut_match in ["BTC", "ETH"]: found_symbol = shortcut_match + "USD"
                    elif shortcut_match == "GOLD" or shortcut_match == "XAU": found_symbol = "XAUUSD"
                    elif shortcut_match in ["US30", "DJI"]: found_symbol = "US30" # Broker dependent, normalized to US30 usually
                    elif shortcut_match in ["NAS100", "NDX"]: found_symbol = "NAS100"
                    elif shortcut_match in ["SPX", "SPX500"]: found_symbol = "SPX500"
                    else: found_symbol = shortcut_match # GER30 etc.
            
            if found_symbol:
                 bias = await self.get_institutional_bias(found_symbol)
                 context_block += f"\n[MARKET INTEL for {found_symbol}]\n{bias}\n"
                 
                 # --- RICH ANALYSIS CARD GENERATION ---
                 # If user explicitly asks for analysis/prediction/signal, we generate a Structured Card.
                 analysis_keywords = ["ANALYZE", "PREDICTION", "FORECAST", "SIGNAL", "THOUGHTS ON", "OUTLOOK", "CHART", "SETUP", "TRADE"]
                 is_analysis_request = any(k in query_upper for k in analysis_keywords)
                 
                 # Only trigger rich analysis if we have an account to fetch data OR if we just want fundamental analysis
                 if is_analysis_request:
                     try:
                         # 1. Fetch Real-Time Data (if possible)
                         current_price = 0.0
                         atr_value = 0.0010 # Default fallback
                         
                         if account_id:
                             # Parallel Fetch
                             price_task = get_symbol_price(account_id, found_symbol)
                             candles_task = fetch_candles(account_id, found_symbol, "H1", limit=50) # H1 for ATR
                             
                             price_res, candles = await asyncio.gather(price_task, candles_task)
                             
                             if price_res:
                                 current_price = (price_res['bid'] + price_res['ask']) / 2
                                 
                             if candles:
                                 tech = TechnicalAnalyzer(candles)
                                 atr_value = tech.get_atr(14)
                         
                         # 2. Generate Structured Signal via AI Engine
                         # Use existing context (History, Sentiment, COT)
                         
                         # Get COT Data specifically for this symbol to pass to Engine
                         # (Reuse institutional bias string or fetch specifically if Engine needs dict)
                         # Engine uses `behavior_report` string, `institutional_bias` string. 
                         
                         # We'll use a simplified call to get_trading_signal
                         # Note: get_trading_signal expects specific args. 
                         # We need to construct them or use a helper.
                         # Since get_trading_signal constructs its OWN prompt, we might need to pass the context we already built.
                         # Actuall AIEngine.get_trading_signal builds the prompt internally. 
                         # We should probably pass "institutional_bias" as the 'institutional_bias' arg.
                         
                         # Construct minimal Multi-TF data for the engine (since we didn't do full scan)
                         # If we have candles, we can make it real.
                         multi_tf_data = {}
                         if account_id and candles:
                             # Just pass H1 data we have
                             tech = TechnicalAnalyzer(candles)
                             multi_tf_data = {
                                 "H1": {
                                     "price": current_price,
                                     "rsi": tech.get_rsi(14),
                                     "structure": tech.get_market_structure(),
                                     "zones": tech.get_support_resistance()
                                 }
                             }
                         
                         calendar = await self.get_upcoming_events(found_symbol[:3]) # Base currency events
                         
                         signal_data = await self.ai_engine.get_trading_signal(
                            symbol=found_symbol,
                            current_price=current_price,
                            atr=atr_value,
                            multi_tf_data=multi_tf_data,
                            calendar=calendar,
                            institutional_bias=bias,
                            retail_sentiment=self.get_cached_sentiment(found_symbol),
                            risk_params={"balance": 0} # Placeholder
                         )
                         
                         if signal_data and "symbol" in signal_data:
                             # Append the plain text summary to the chat for history, but return the object for UI
                             # The frontend handles `analysisData` in the message object.
                             
                             return {
                                 "text": signal_data.get("summary") or f"Analysis for {found_symbol} ready.",
                                 "agent": agent_identity,
                                 "visualVariant": "analysis",
                                 "analysisData": signal_data
                             }
                             
                     except Exception as analysis_err:
                         logger.error(f"Rich Analysis Generation Failed: {analysis_err}")
                         # Fallback to normal chat if fails
                         context_block += f"\n[SYSTEM ALERT] Structured analysis failed ({str(analysis_err)}). Reverting to text chat.\n"


            # --- 3. Prompt Construction ---
            # Remove "Priority Focus" constraint to allow universal analysis
            full_prompt = f"""
            SYSTEM: {system_persona}
            
            CONTEXT DATA:
            {context_block}
            
            USER INQUIRY: "{user_query}"
            
            INSTRUCTIONS:
            - Answer as your Persona ({agent_identity}).
            - The user is asking about {found_symbol if found_symbol else "the market"}. Focus your analysis on this specific request.
            - Use the Context Data to support your answer.
            - Keep it under 3 sentences. Be direct.
            - If suggesting an action, do NOT output a JSON signal, just give advice text.
            """
            
            # --- 4. LLM Call (Standard) ---
            # If Trade Manager, we attempt to turn this into a Debate IF it sounds like a proposal
            # For simplicity in this iteration, we just APPEND the War Room context if it's a trade question
            
            is_trade_intent = agent_identity == "TRADE MANAGER"
            debate_result = None
            
            if is_trade_intent and debate_room:
                # Mock Proposal Extraction (In reality, we'd ask LLM to extract JSON first)
                # But to save latency, let's just ask the CIO to review the USER's text directly
                
                proposal = {
                    "symbol": found_symbol or "UNKNOWN",
                    "action": "TRADE_QUERY",
                    "raw_text": user_query
                }
                
                # Run the Debate ONLY if we have a valid symbol (Proposal Validation)
                # If no symbol, it's likely a general "Find me a trade" request -> Skip Debate, let Agent generate.
                if found_symbol:
                    debate_result = await debate_room.conduct_round_table(proposal, context_block)
                
                    # Inject Verdict into final Prompt
                    full_prompt += f"\n\n[TRADING COUNCIL VERDICT]\nVerdict: {debate_result['verdict']}\nReasoning: {debate_result['reasoning']}\nLog: {debate_result['debate_log']}\n\nINSTRUCTION: The Trading Council has spoken. Communicate this verdict to the user clearly."

            # --- ML-SWING-TRADER INTEGRATION (For All Trade Queries) ---
            if is_trade_intent and self.macro_divergence:
                 try:
                     # Quick scan for context (if not already done)
                     scan_results = await asyncio.to_thread(self.macro_divergence.scan_for_divergence)
                     # Find if current symbol has ML data
                     ml_data = next((op for op in scan_results if op['symbol'] == found_symbol), None)
                     if ml_data:
                         full_prompt += f"\n\n[ML-SWING-TRADER ANALYSIS]\nScore: {ml_data['divergence_score']}\nSignal: {ml_data['recommendation']}\nRationale: {ml_data['rationale']}\n"
                 except Exception as e:
                     logger.warning(f"ML-Swing-Trader Context Error: {e}")



            # --- 4. LLM Call (Standard with Multi-Provider Support) ---
            # Reuse AIEngine's Provider Logic for Consistency
            # We access the internal logic or just replicate it slightly here for the Chat endpoint
            model_target = model_override or settings.LLM_MODEL
            config = self.ai_engine.get_provider_config(model_target)
            
            # Log provider usage
            logger.info(f"Agent Chat using provider: {config['provider']} ({config['model_id']})")
            
            payload = {
                "model": config['model_id'],
                "messages": [{"role": "user", "content": full_prompt}],
                "temperature": 0.7
            }
            
            # Provider specific adjustments
            if config['provider'] != "openai":
                payload["max_tokens"] = 1024 # Safe default for others

            resp = await self.http_client.post(
                config['base_url'],
                headers={"Authorization": f"Bearer {config['api_key']}"},
                json=payload
            )

            
            reply_text = resp.json()['choices'][0]['message']['content']
            
            # --- 5. Save to Long Term Memory ---
            if memory_store:
                # Run as background task or fire-and-forget to not block response
                # For simplicity in async, we just await it or call it if sync. memory_store.add_memory is sync.
                # However, Chroma might block slightly. Let's just run it.
                # Only store if it was a meaningful answer (not error)
                memory_store.add_memory(
                    f"User: {user_query} | Agent ({agent_identity}): {reply_text}",
                    {"user_id": user_id or "anon", "agent": agent_identity}
                )

            # --- 6. Return Structured Response ---
            return {
                "text": reply_text,
                "agent": agent_identity
            }

        except Exception as e:
            logger.error(f"Agent Chat Error: {e}")
            return {
                "text": f"System Error: {str(e)}\n(Please check backend logs)",
                "agent": "SYSTEM"
            }



    def calculate_confluence_score(self, tf_data: Dict) -> Dict:
        """
        Deterministic Python-based scoring to validate setups before LLM.
        Returns score (0-5) and details.
        """
        score_buy = 0
        score_sell = 0
        details = []

        # 1. Trend Alignment (D1/H4)
        d1_struct = tf_data.get('D1', {}).get('structure', 'Ranges')
        h4_struct = tf_data.get('H4', {}).get('structure', 'Ranges')
        
        if "BULLISH" in d1_struct or "BULLISH" in h4_struct:
            score_buy += 1
        if "BEARISH" in d1_struct or "BEARISH" in h4_struct:
            score_sell += 1
            
        # 2. RSI Extremes (H1/M15)
        h1_rsi = tf_data.get('H1', {}).get('rsi', 50)
        if h1_rsi < 35: score_buy += 1
        if h1_rsi > 65: score_sell += 1
        
        # 3. Key Levels (S/R)
        current_price = tf_data.get('M5', {}).get('price', 0)
        h1_zones = tf_data.get('H1', {}).get('zones', {})
        supports = h1_zones.get('support', [])
        resistances = h1_zones.get('resistance', [])
        
        # Check proximity (0.15% tolerance)
        tolerance = current_price * 0.0015
        if any(abs(current_price - s) < tolerance for s in supports):
            score_buy += 1
            details.append("At Support")
        if any(abs(current_price - r) < tolerance for r in resistances):
            score_sell += 1
            details.append("At Resistance")
            
        # 4. Patterns
        patterns = tf_data.get('H1', {}).get('patterns', [])
        if any("Bullish" in p for p in patterns): score_buy += 1
        if any("Bearish" in p for p in patterns): score_sell += 1
        
        # 5. DNA (Wick Pressure) - could be added later
        
        # FIX: Handle Ties and Low Scores properly
        if score_buy == score_sell:
            final_bias = "NEUTRAL"
            final_score = score_buy  # They are equal
        elif score_buy > score_sell:
            final_bias = "BUY"
            final_score = score_buy
        else:
            final_bias = "SELL"
            final_score = score_sell
        
        # Additional check: If max score is 0 or 1, force Neutral
        if final_score <= 1:
            final_bias = "NEUTRAL"
        
        return {
            "bias": final_bias,
            "score": final_score,
            "buy_score": score_buy,
            "sell_score": score_sell,
            "details": details
        }

    async def process_single_request(self, symbol: str, timeframe: str = "1h", 
                                      doc_id: str = "doc_1", fetch_callback=None, user_id: str = None, 
                                      progress_callback=None, simulated_time: datetime = None,
                                      position_context: Optional[Dict] = None, account_id: Optional[str] = None) -> Dict:
        """
        Safe Wrapper for Multi-Timeframe Analysis.
        Catches unhandled exceptions and emits error events to the frontend.
        """
        try:
            # [SAFETY] Global Timeout for Analysis Process (2 minutes max)
            return await asyncio.wait_for(
                self._process_analysis_logic(symbol, timeframe, doc_id, fetch_callback, user_id, progress_callback, simulated_time, position_context, account_id),
                timeout=120.0
            )
        except Exception as e:
            logger.error(f"Critical Analysis Error for {symbol}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            
            # Emit Error Event to Socket so UI stops spinner
            if user_id:
                data = {
                    "symbol": symbol, 
                    "progress": 0, 
                    "stage": "Analysis Failed", 
                    "elapsed_ms": 0,
                    "status": "ERROR", 
                    "error": str(e)
                }
                
                if progress_callback:
                    await progress_callback(user_id, data)
                else:
                    try:
                        from backend.services.websocket_manager import websocket_manager
                        await websocket_manager.emit_analysis_progress(user_id, data)
                    except: pass
            
            return {
                "status": "error",
                "message": f"Analysis failed: {str(e)}",
                "symbol": symbol
            }

    async def _process_analysis_logic(self, symbol: str, timeframe: str = "1h", 
                                      doc_id: str = "doc_1", fetch_callback=None, user_id: str = None, 
                                      progress_callback=None, simulated_time: datetime = None,
                                      position_context: Optional[Dict] = None, account_id: Optional[str] = None) -> Dict:
        """
        Multi-Timeframe Analysis with full confluence detection.
        Fetches D1, H4, H1, M15, M5 using fetch_callback.
        
        Args:
            user_id: Optional Firebase user ID for progress broadcasting
            progress_callback: Optional async callback(user_id, data) for external progress handling (e.g. from subprocess)
            account_id: Optional Account ID to use for fetching data (Defaults to settings.DEFAULT_ACCOUNT_ID)
        """
        # Resolve Account ID
        target_account_id = settings.DEFAULT_ACCOUNT_ID
        if account_id:
            target_account_id = account_id
        elif position_context and 'account_id' in position_context:
            target_account_id = position_context['account_id']
        elif user_id and user_id != "auto_agent":
            # In future we might look up user's active account here
            pass
            
        # Initialize timing
        start_time = time.time()
        
        async def emit_progress(progress: int, stage: str, **kwargs):
            """Helper to broadcast progress updates"""
            if not user_id:
                return

            elapsed_ms = int((time.time() - start_time) * 1000)
            data = {
                "symbol": symbol,
                "progress": progress,
                "stage": stage,
                "elapsed_ms": elapsed_ms
            }
            data.update(kwargs)

            if progress_callback:
                # Use provided callback (e.g. for IPC/HTTP to main process)
                await progress_callback(user_id, data)
            else:
                # Use in-process websocket manager (Lazy Import to avoid v4/v5 conflict)
                try:
                    from backend.services.websocket_manager import websocket_manager
                    await websocket_manager.emit_analysis_progress(user_id, data)
                except ImportError:
                    logger.warning("WebsocketManager not available for progress updates.")
        
        timeframes = ["D1", "H4", "H1", "M15", "M5"]
        multi_tf_results = {}
        raw_candles_cache = {}  # Cache to prevent re-fetching
        behavior_dna_data = {}
        cache_hit = False
        
        logger.info(f"Starting Multi-Timeframe Analysis for {symbol}...")
        await emit_progress(0, "Starting analysis...")

        # =====================================================================
        # SCANNER CACHE CHECK — Skip candle fetching if fresh data available
        # =====================================================================
        try:
            from backend.services.market_scanner import scanner_cache
            cached = scanner_cache.get(symbol.upper())
            if cached and cached.get("age_seconds", 9999) < 600:  # Cache valid for 10 min
                logger.info(f"⚡ Cache HIT for {symbol} (age: {cached['age_seconds']:.0f}s). Skipping MetaApi fetch.")
                multi_tf_results = cached.get("technical", {})
                behavior_dna_data = cached.get("behavior_dna", {})
                cache_hit = True
                await emit_progress(70, "Using pre-computed analysis (cache hit)...")
        except ImportError:
            pass  # Scanner not available, continue with normal flow
        except Exception as e:
            logger.debug(f"Scanner cache check failed: {e}. Proceeding with MetaApi.")

        
        async def fetch_tf_safe(tf: str):
            """Helper to fetch a single TF with Rate Limit logic"""
            async with RATE_LIMITER:
                logger.debug(f"Fetching {tf}...")
                for attempt in range(3): 
                    try:
                        await asyncio.sleep(0.5)
                        # Check cache first (Though tasks run concurrent, redundant requests might happen if we expanded logic)
                        # [SAFETY] Wrap callback in timeout to prevent infinite hang if Bridge/MetaApi locks up
                        # FIX: Pass account_id as first argument to fetch_candles (metaapi_service)
                        candles = await asyncio.wait_for(fetch_callback(target_account_id, symbol, tf), timeout=45.0)
                        
                        if not candles:
                            return tf, {"structure": "Insufficient Data", "price": 0}
                        
                        # Store in cache for DNA analysis later
                        raw_candles_cache[tf] = candles

                        # Analyze
                        tech = TechnicalAnalyzer(candles)
                        data = {
                            "price": candles[-1].get('close', 0),
                            "atr": tech.get_atr(14),
                            "adr": tech.get_adr(5),
                            "pivots": tech.get_pivot_points(),
                            "structure": tech.get_market_structure(),
                            "rsi": tech.get_rsi(14),
                            "macd": tech.get_macd(),
                            "bollinger": tech.get_bollinger_bands(),
                            "patterns": tech.get_candle_patterns(),
                            "zones": tech.get_support_resistance()
                        }
                        return tf, data

                    except Exception as e:
                        if "429" in str(e) or "Too Many Requests" in str(e):
                            wait_time = 2 ** attempt
                            logger.warning(f"Rate limit hit for {tf}. Retrying in {wait_time}s...")
                            await asyncio.sleep(wait_time)
                        else:
                            logger.error(f"Failed to fetch {tf}: {e}")
                            if attempt == 2: break 
            
            return tf, {"structure": "Error/Timeout", "price": 0}

        # =====================================================================
        # PATH A: CACHE HIT — Skip candle fetch, use pre-computed data
        # PATH B: CACHE MISS — Full MetaApi fetch (existing behavior)
        # =====================================================================
        if not cache_hit:
            # Launch All Data Fetching Tasks Concurrently (Performance Optimization)
            await emit_progress(10, "Fetching market data & intelligence...")
            
            # 1. Candle Fetching Task
            async def fetch_all_candles():
                if fetch_callback:
                    tasks = [fetch_tf_safe(tf) for tf in timeframes]
                    return await asyncio.gather(*tasks)
                return []

            # 2. Institutional Bias Task (News)
            async def fetch_news_context():
                return await self.get_institutional_bias(symbol, simulated_time=simulated_time)

            # 3. Calendar Task
            async def fetch_calendar():
                target_ccy = "USD" 
                return await self.get_upcoming_events(target_ccy, simulated_time=simulated_time)

            # EXECUTE PARALLEL FETCH
            results_candles, institutional_bias, calendar_events = await asyncio.gather(
                fetch_all_candles(),
                fetch_news_context(),
                fetch_calendar()
            )

            # Process Candle Results
            if not results_candles:
                 logger.warning("No fetch_callback provided or empty results.")
                 return {"status": "error", "message": "No data fetch callback"}

            for tf, data in results_candles:
                multi_tf_results[tf] = data

            # Get current price from M5 or H1
            await emit_progress(30, "Analyzing technical indicators...")
            m5_price = multi_tf_results.get('M5', {}).get('price', 0)
            h1_atr = multi_tf_results.get('H1', {}).get('atr', 0.0010)
            current_price = m5_price if m5_price else multi_tf_results.get('H1', {}).get('price', 0)

            # Generate Multi-Timeframe Behavior DNA (Use Cached Data!)
            await emit_progress(50, "Analyzing symbol behavior patterns...")
            behavior_report = ""
            behavior_dna_data = {}
            dna_timeframes = ["M5", "M15", "H1", "H4"]
            
            try:
                dna_reports = []
                for tf in dna_timeframes:
                    candles = raw_candles_cache.get(tf)
                    if candles and len(candles) >= 24:
                        behavior_dna_data[tf] = self.behavior_analyzer.analyze(candles, symbol, tf)
                        dna_reports.append(self.behavior_analyzer.generate_report(candles, symbol, tf))
                    else:
                        behavior_dna_data[tf] = {"error": "Insufficient data/Fetch failed", "timeframe": tf}
                
                behavior_report = "\n\n".join(dna_reports) if dna_reports else "Behavior analysis unavailable."

                if behavior_dna_data:
                    dominances = [d.get("power_analysis", {}).get("dominance", "N/A") 
                                  for d in behavior_dna_data.values() if "error" not in d]
                    pressures = [d.get("wick_pressure", {}).get("pressure", "N/A") 
                                 for d in behavior_dna_data.values() if "error" not in d]
                                 
                    behavior_dna_data["_summary"] = {
                        "consensus_dominance": max(set(dominances), key=dominances.count) if dominances else "MIXED",
                        "consensus_pressure": max(set(pressures), key=pressures.count) if pressures else "MIXED",
                        "timeframes_analyzed": list(behavior_dna_data.keys())
                    }

            except Exception as e:
                logger.error(f"Behavior DNA error: {e}")
                behavior_report = "Behavior analysis unavailable."
                behavior_dna_data = {"error": str(e)}

        else:
            # CACHE HIT PATH — Still need news & calendar (fast, from SQLite)
            institutional_bias = await self.get_institutional_bias(symbol, simulated_time=simulated_time)
            calendar_events = await self.get_upcoming_events("USD", simulated_time=simulated_time)
            
            # Extract price & ATR from cached technical data
            h1_data = multi_tf_results.get('H1', {})
            current_price = h1_data.get('price', 0) or multi_tf_results.get('H4', {}).get('price', 0)
            h1_atr = h1_data.get('atr', 0.0010)
            
            # Build behavior report from cached DNA data
            behavior_report = "Behavior analysis from scanner cache."

        # =====================================================================
        # RISK PARAMS + PRICE VALIDATION (Runs for both cache hit and miss)
        # =====================================================================
        atr = h1_atr if h1_atr > 0 else 0.0010
        risk_params = {
            "atr_val": atr,
            "buy_sl": round(current_price - (1.5 * atr), 5),
            "buy_tp": round(current_price + (2.5 * atr), 5),
            "sell_sl": round(current_price + (1.5 * atr), 5),
            "sell_tp": round(current_price - (2.5 * atr), 5)
        }

        if current_price <= 0:
            logger.error(f"Analysis Aborted: invalid price {current_price} for {symbol}")
            return {"status": "error", "message": "Market Data Unavailable (Price=0)"}

        # PYTHON PRE-ANALYSIS SCORING
        await emit_progress(70, "Gathering market context...")
        confluence_score = self.calculate_confluence_score(multi_tf_results)
        
        # [NEW] PRICE ACTION ENGINE (Backtested Strategy)
        # We need D1 Structure, H1 Structure, and M5 DNA Report
        d1_struct = multi_tf_results.get('D1', {}).get('structure', 'Ranges')
        h1_struct = multi_tf_results.get('H1', {}).get('structure', 'Ranges')
        m5_dna = behavior_dna_data.get('M5', {}) # Use M5 DNA if available
        
        price_action_score = None
        if "error" not in m5_dna:
            # We need an instance of TechnicalAnalyzer to check this? 
            # Or we can just make the method static or instantiate one briefly.
            # The method is on the instance, but relies on passed data.
            # We can use any instance or creating a temporary one.
            # Let's instantiate a lightweight one or move method to static. 
            # For now, create temp instance with dummy data since method uses arguments purely (mostly).
            # Wait, get_price_action_score is inside TechnicalAnalyzer.
            # We have instances created during fetch inside the loop, but discarded.
            # We can re-instantiate or just add the method to the Agent class?
            # Better: Let's use the method from the class.
            
            # Since get_price_action_score uses 'self' only for... wait, looking at my previous edit:
            # It DOES NOT use 'self'. It uses passed arguments.
            # I can call it on a temp instance.
            temp_ta = TechnicalAnalyzer([{"open":1, "close": 1}]) # Dummy
            price_action_score = temp_ta.get_price_action_score(d1_struct, h1_struct, m5_dna)
        
        # Retail Sentiment (Sync - Fast)
        retail_sentiment = self.get_cached_sentiment(symbol)
        
        if self.check_imminent_news(calendar_events):
            # [USER REQUEST]: Enable Signal Generation (Prediction Mode) instead of Hard Block
            logger.info(f"High impact news detected for {symbol}. Proceeding in PREDICTION MODE.")
            # return {
            #     "symbol": symbol, 
            #     "direction": "WAIT", 
            #     "reason": f"High Impact News Imminent: {calendar_events[0]['event_name']}",
            #     "confidence": 100
            # }

        # Construct Prompt
        await emit_progress(90, "Processing AI analysis...")
        prompt = self._construct_prompt(
            symbol, multi_tf_results, calendar_events,
            behavior_report, institutional_bias, retail_sentiment, risk_params, 
            confluence_score=confluence_score,
            price_action_score=price_action_score,
            position_context=position_context
        )
        
        # Get Validated Signal from AI Engine
        ai_data = await self.ai_engine.get_trading_signal(
            prompt, symbol, current_price, risk_params.get('atr_val', 0.001)
        )

        # [RISK MANAGER ADVISORY INTEGRATION]
        # In "Advisory Mode", we don't block. We just append warnings.
        if user_id:
             try:
                 from backend.services.risk_manager import risk_manager
                 # We need equity/exposure. For analysis, we might not have live account data handy unless we fetch it.
                 # Simplified check: Check Daily PnL limit only (internal state)
                 # Or fetch full report if we want to be thorough.
                 # Let's check Daily PnL state from Risk Manager's internal tracker
                 
                 current_pnl = risk_manager.daily_pnl.get(user_id, 0)
                 # We assume equity is tracked or we fetch it. 
                 # For speed, let's just check if Risk Manager has a "BLOCK" flag active for this user?
                 # Actually, let's just ask: "Is it safe?"
                 # We pass dummy values for exposure since we are pre-trade.
                 # We need account_id.
                 # For now, let's just inject the Risk Report Summary if available.
                 
                 risk_warnings = []
                 if abs(current_pnl) > 500: # Example Threshold or use percentage
                     risk_warnings.append(f"Daily Drawdown Notice: PnL is {current_pnl:.2f}")
                     
                 # Add to response
                 if risk_warnings:
                     ai_data['risk_advisory'] = {
                         "status": "WARNING",
                         "messages": risk_warnings
                     }
                 else:
                     ai_data['risk_advisory'] = {
                         "status": "SAFE",
                         "messages": []
                     }
                     
             except Exception as re:
                 logger.warning(f"Risk Advisory Failed: {re}")
                 ai_data['risk_advisory'] = {"status": "UNKNOWN", "error": str(re)}

        # Inject calculated technical data (Zero-Hallucination)
        if "status" not in ai_data or ai_data.get("status") != "error":
             ai_data['technical_data'] = multi_tf_results
             ai_data['behavior_dna'] = behavior_dna_data
             ai_data['upcoming_events'] = calendar_events # [FRONTEND] Raw event data for Economic Radar

             # [NEW] Daily Range Intelligence — for Trade Plan green box
             try:
                 # Pip multiplier for this symbol
                 sym_upper = symbol.upper()
                 if 'JPY' in sym_upper:
                     pip_mul = 100
                 elif 'XAU' in sym_upper or 'GOLD' in sym_upper:
                     pip_mul = 10
                 elif any(idx in sym_upper for idx in ['US30', 'SPX', 'NAS', 'US100', 'US500']):
                     pip_mul = 1
                 else:
                     pip_mul = 10000

                 # ATR (H1) in pips
                 h1_atr_raw = multi_tf_results.get('H1', {}).get('atr', 0)
                 atr_pips = round(h1_atr_raw * pip_mul, 1) if h1_atr_raw else 0

                 # ADR (D1) in pips
                 d1_adr_raw = multi_tf_results.get('D1', {}).get('adr', 0)
                 adr_pips = round(d1_adr_raw * pip_mul, 1) if d1_adr_raw else 0

                 # Today's range from last D1 candle (current day)
                 d1_candles = raw_candles_cache.get('D1', [])
                 today_high = 0
                 today_low = 0
                 today_range_pips = 0
                 if d1_candles and len(d1_candles) > 0:
                     last_d1 = d1_candles[-1]
                     today_high = float(last_d1.get('high', 0))
                     today_low = float(last_d1.get('low', 0))
                     today_range_raw = today_high - today_low
                     today_range_pips = round(today_range_raw * pip_mul, 1)

                 # Pips remaining = ADR - today's range used
                 pips_remaining = round(max(0, adr_pips - today_range_pips), 1)
                 pct_used = round((today_range_pips / adr_pips) * 100, 0) if adr_pips > 0 else 0

                 ai_data['daily_range_intel'] = {
                     'atr_h1_pips': atr_pips,
                     'adr_d1_pips': adr_pips,
                     'today_high': round(today_high, 5),
                     'today_low': round(today_low, 5),
                     'today_range_pips': today_range_pips,
                     'pips_remaining': pips_remaining,
                     'pct_used': min(pct_used, 100),
                     'pip_multiplier': pip_mul,
                     'entry_price': current_price
                 }
                 logger.info(f"📊 Daily Range Intel: ATR={atr_pips}p ADR={adr_pips}p Used={today_range_pips}p Left={pips_remaining}p ({pct_used}%)")
             except Exception as e:
                 logger.warning(f"Daily Range Intel calc failed: {e}")
                 ai_data['daily_range_intel'] = None
             
             # [FIX] Inject computed scores for frontend Signal Card (were computed but not stored)
             if confluence_score:
                 ai_data['confluence_score'] = confluence_score  # {bias, score, buy_score, sell_score, details}
             if price_action_score:
                 ai_data['price_action_score'] = price_action_score  # {score, bias, reason}
             
             # [FIX] Inject structured retail sentiment data (frontend needs dict, not string)
             sentiment_data = self._get_retail_sentiment_data(symbol)
             if sentiment_data:
                 ai_data['retail_sentiment_data'] = sentiment_data
             else:
                 ai_data['retail_sentiment_data'] = {
                     "long_pct": 0, "short_pct": 0, "signal": "No Data", "strength": "N/A", "bias": "N/A"
                 }
             
             # [FIX] Inject COT Data for Signal Card
             try:
                 cot_data = self.cot_engine.get_latest_sentiment(symbol)
                 if cot_data:
                     ai_data['cot_data'] = cot_data
                 else:
                     ai_data['cot_data'] = {
                        "symbol": symbol, "date": "No Data",
                        "smart_sentiment": 0, "smart_net": 0, "willco_index": 0, "oi": 0,
                        "hedge_net": 0, "hedge_sentiment": 0, "hedge_willco": 0
                     }
             except Exception as e:
                 logger.warning(f"COT fetch failed for {symbol}: {e}")

             # [FIX] Prioritize AI Engine's Analysis, use raw strings only as fallback
             base_ccy = symbol[:3] if len(symbol) >= 6 else "BASE"
             quote_ccy = symbol[3:6] if len(symbol) >= 6 else "QUOTE"

             # Only run this parsing if the AI failed to generate specific analysis
             if not ai_data.get('base_analysis') or len(str(ai_data.get('base_analysis'))) < 10:
                 if institutional_bias and isinstance(institutional_bias, str):
                     if f"--- {base_ccy} DRIVERS ---" in institutional_bias:
                         try:
                             parts = institutional_bias.split(f"--- {base_ccy} DRIVERS ---")
                             if len(parts) > 1:
                                 base_section = parts[1].split("---")[0].strip()
                                 ai_data['base_analysis'] = base_section[:500] if base_section else f"No specific {base_ccy} drivers found."
                         except: pass

             if not ai_data.get('quote_analysis') or len(str(ai_data.get('quote_analysis'))) < 10:
                 if institutional_bias and isinstance(institutional_bias, str):
                     if f"--- {quote_ccy} DRIVERS ---" in institutional_bias:
                         try:
                             parts = institutional_bias.split(f"--- {quote_ccy} DRIVERS ---")
                             if len(parts) > 1:
                                 quote_section = parts[1].split("---")[0].strip()
                                 ai_data['quote_analysis'] = quote_section[:500] if quote_section else f"No specific {quote_ccy} drivers found."
                         except: pass
             # If still empty, use generic fallback
             if not ai_data.get('base_analysis'):
                 ai_data['base_analysis'] = f"Limited {base_ccy} data available."
             if not ai_data.get('quote_analysis'):
                 ai_data['quote_analysis'] = f"Limited {quote_ccy} data available."
             
             # Economic Analysis Summary from calendar_events
             if calendar_events and len(calendar_events) > 0:
                 event_summaries = []
                 for ev in calendar_events[:3]: # Max 3 events
                     ev_name = ev.get('event_name', 'Unknown')
                     ev_impact = ev.get('impact_level', 'Unknown')
                     ev_time = ev.get('event_time', '')
                     event_summaries.append(f"• {ev_name} ({ev_impact}) at {ev_time}")
                 ai_data['economic_analysis'] = "Upcoming Events:\\n" + "\\n".join(event_summaries)
             else:
                 ai_data['economic_analysis'] = "No significant economic events in the next 12 hours."

             # [FIX] Summary Quality Gate — prevent generic/lazy summaries from reaching the frontend
             generic_summaries = ['ai analysis complete', 'analysis complete', 'completed', 'signal generated']
             current_summary = (ai_data.get('summary') or '').strip().lower()
             if not current_summary or current_summary in generic_summaries:
                 # Build a real summary from available data
                 if ai_data.get('reasons') and isinstance(ai_data['reasons'], list) and len(ai_data['reasons']) > 0:
                     ai_data['summary'] = ai_data['reasons'][0]
                 elif ai_data.get('macro_thesis'):
                     ai_data['summary'] = ai_data['macro_thesis']
                 elif ai_data.get('reason'):
                     ai_data['summary'] = ai_data['reason']
                 else:
                     direction = ai_data.get('direction', 'WAIT')
                     conf = ai_data.get('confidence', 0)
                     ai_data['summary'] = f"{direction} setup on {symbol} with {conf}% confluence across multi-timeframe analysis."
                 logger.info(f"Summary quality gate: replaced generic summary for {symbol}")
        
        # Final progress update
        await emit_progress(100, "Analysis complete")
        
        # Log total time
        total_time = time.time() - start_time
        logger.info(f"Analysis for {symbol} completed in {total_time:.2f}s")
            
            
        return ai_data


# =============================================================================
# AGENT VARIATIONS
# =============================================================================

class MarketScoutAgent(MacroLensAgentV2):
    """Standard 'Scout' agent. Quick, technical-focused analysis."""
    pass


class AnalystPrimeAgent(MacroLensAgentV2):
    """'Prime' agent. More detailed, risk-averse, strategic."""
    
    def _construct_prompt(self, *args, **kwargs) -> str:
        base_prompt = super()._construct_prompt(*args, **kwargs)
        
        base_prompt = base_prompt.replace(
            "You are a Senior Quantitative Analyst. You trade based on PRICE ACTION CONFLUENCE.", 
            "You are 'Analyst Prime', a Senior Global Macro Strategist. Your style is risk-averse and explanation-heavy."
        )
        
        base_prompt += """
# ANALYST PRIME INSTRUCTIONS
- Prioritize Capital Preservation.
- If the setup is not perfect, Signal WAIT.
- Provide deeper reasoning on 'Market Structure'.
- Mention 'Key Levels' explicitly in the reasoning.
"""
        base_prompt += """
# ANALYST PRIME INSTRUCTIONS
- Prioritize Capital Preservation.
- If the setup is not perfect, Signal WAIT.
- Provide deeper reasoning on 'Market Structure'.
- Mention 'Key Levels' explicitly in the reasoning.
"""
        return base_prompt


class QuantumTraderAgent(MacroLensAgentV2):
    """'Quantum' agent. Mathematical, pivot-focused, aggressive."""
    
    def _construct_prompt(self, *args, **kwargs) -> str:
        base_prompt = super()._construct_prompt(*args, **kwargs)
        
        base_prompt = base_prompt.replace(
            "You are a Senior Quantitative Analyst. You trade based on PRICE ACTION CONFLUENCE.", 
            "You are 'Quantum', an Algorithmic Trading Bot. Your style is precise and probability-based."
        )
        
        base_prompt += """
# QUANTUM INSTRUCTIONS
- Focus strictly on Math: Pivots, ATR stats, and Probabilities.
- Use succinct, bullet-point reasoning.
- You are willing to take higher risk if R:R is favorable (>1:2).
- Explicitly state the Expected Value (EV) in reasoning if possible.
"""
        return base_prompt


class CIOAgent(MacroLensAgentV2):
    """
    The Chief Investment Officer (CIO).
    Synthesizes tactical setups (Scout) with risk data (Guardian) to make final decisions.
    """
    
    def _construct_prompt(self, *args, **kwargs) -> str:
        # CIO inputs are custom (Trade Proposal + Risk Report), so we override the prompt construction completely
        # But to keep signature compatibility, we might receive them as args or handle differently.
        # Actually, ExecutiveService will likely call `ai_engine` directly or we repurpose `ask` or `process_single_request`.
        # For synergy, we'll implement a custom prompt builder here that ExecutiveService uses.
        return super()._construct_prompt(*args, **kwargs)

    async def review_proposal(self, proposal: Dict, risk_report: Dict) -> Dict:
        """
        Specialized method for CIO to review a trade proposal against a risk report.
        """
        system_prompt = """
        You are the Chief Investment Officer (CIO) of a top-tier hedge fund.
        Your goal is CAPITAL PRESERVATION first, and ASYMMETRIC RETURNS second.
        
        You have two subordinates:
        1. The SCOUT (Trade Manager): Monitors open positions and proposes modifications (Trail SL, Breakeven, Close Partial, Scale In).
        2. The GUARDIAN (Risk Manager): Reports on exposure, drawdown, and correlation.
        
        Your Job:
        - Review the Scout's proposed MODIFICATION to an existing position.
        - Cross-reference with the Guardian's risk report.
        - DECIDE: APPROVED, REJECTED, or MODIFIED.
        
        Rules:
        - If Daily Drawdown is > 2%, be extremely conservative with 'Scale In' or 'Widen SL'.
        - If Correlation is High, reject 'Scale In'. Support 'Close Partial' or 'Tighten SL'.
        - If the modification reduces risk (e.g. Move to Breakeven), almost always APPROVE.
        - Explain your decision in a "user_report" acting as a mentor to the user.
        
        Output JSON format:
        {
            "decision": "APPROVED" | "REJECTED" | "MODIFIED",
            "modified_lots": float (only if MODIFIED, otherwise null),
            "reasoning": "Internal thought process...",
            "user_report": "User-facing explanation (friendly, professional, concise)."
        }
        """
        
        user_prompt = f"""
        # THE SCOUT'S PROPOSAL
        Symbol: {proposal.get('symbol')}
        Action: {proposal.get('action')}
        Confidence: {proposal.get('confidence')}%
        Reasoning: {proposal.get('reasoning')}
        
        # THE GUARDIAN'S RISK REPORT
        Daily PnL: ${risk_report.get('daily_pnl', 0)} ({risk_report.get('daily_pnl_pct', 0)}%)
        Total Exposure: {risk_report.get('total_exposure', 0)} Lots
        Correlation Warning: {risk_report.get('correlation_warning', 'None')}
        Equity: ${risk_report.get('equity', 0)}
        
        # DECISION REQUIRED
        """
        
        try:
            # Use provider config for multi-model support
            config = self.ai_engine.get_provider_config(settings.LLM_MODEL)
            
            if not config['api_key']:
                return {
                    "decision": "REJECTED",
                    "reasoning": f"Missing API Key for {config['provider']}",
                    "user_report": "CIO Agent offline (Missing API Key)."
                }
            
            payload = {
                "model": config['model_id'],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 2048
            }
            
            # DeepSeek supports JSON mode
            if config['provider'] == "deepseek":
                payload["response_format"] = {"type": "json_object"}
            
            resp = await self.http_client.post(
                config['base_url'],
                headers={"Authorization": f"Bearer {config['api_key']}"},
                json=payload
            )
            content = resp.json()['choices'][0]['message']['content']
            return json.loads(content)
        except Exception as e:
            logger.error(f"CIO Review Error: {e}")
            # Fail safe: Block trade if CIO is offline
            return {
                "decision": "REJECTED",
                "reasoning": f"CIO Agent Error: {e}",
                "user_report": "The Chief Investment Officer is currently offline. Trade blocked for safety."
            }



class ChatAgent(MacroLensAgentV2):
    """
    'Support' agent. Conversational, helpful, and educational.
    """
    async def chat(self, user_message: str) -> str:
        """
        Direct chat method for Support requests.
        """
        system_prompt = """
        You are 'MacroLens Support', an AI trading assistant.
        Your goal is to help users understand the platform, trading concepts, and their account status.
        
        TONE:
        - Professional but friendly.
        - Concise (allow follow-up questions).
        - Educational (explain concepts simply).
        
        CAPABILITIES:
        - You can explain RSI, MACD, Pivot Points, etc.
        - You can explain how the MacroLens 'Scout' and 'Guardian' agents work.
        - You CANNOT give financial advice or predict the future in this chat.
        
        If asked about specific trade signals, refer them to the 'Trading Console'.
        """
        
        try:
            # Use provider config (defaulting to deepseek or configured model)
            config = self.ai_engine.get_provider_config(settings.LLM_MODEL)
            if not config['api_key']:
                return "I'm currently offline (Missing API Key)."
                
            payload = {
                "model": config['model_id'],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                "temperature": 0.5, # Slightly higher for conversation
                "max_tokens": 1024
            }
            
            # DeepSeek/Generic handling
            resp = await self.http_client.post(
                config['base_url'],
                headers={"Authorization": f"Bearer {config['api_key']}"},
                json=payload
            )
            
            if resp.status_code != 200:
                logger.error(f"ChatAgent Error {resp.status_code}: {resp.text}")
                return "I'm having trouble thinking right now. Please try again."
                
            return resp.json()['choices'][0]['message']['content']
            
        except Exception as e:
            logger.error(f"ChatAgent Exception: {e}")
            return "An internal error occurred."


class AgentFactory:
    @staticmethod
    def get_agent(agent_name: str) -> MacroLensAgentV2:
        if agent_name == "MLens-Analyst Prime":
            return AnalystPrimeAgent()
        elif agent_name == "MLens-Quantum Trader":
            return QuantumTraderAgent()
        elif agent_name == "MLens-CIO":
            return CIOAgent()
        elif agent_name == "chat":
            return ChatAgent()
        else:
            return MarketScoutAgent()



# =============================================================================
# TESTING
# =============================================================================

if __name__ == "__main__":
    async def main():
        agent = MacroLensAgentV2()
        
        # Mock fetch callback with realistic varying candles
        async def mock_fetch(symbol, tf):
            import random
            base = 1.1000
            candles = []
            for i in range(50):
                # Generate varying candles to test pattern detection
                open_p = base + (random.random() * 0.0010)
                close_p = open_p + (random.random() * 0.0005 - 0.00025)
                high_p = max(open_p, close_p) + random.random() * 0.0002
                low_p = min(open_p, close_p) - random.random() * 0.0002
                candles.append({
                    "open": round(open_p, 5),
                    "high": round(high_p, 5),
                    "low": round(low_p, 5),
                    "close": round(close_p, 5),
                    "volume": 100
                })
                base = close_p  # Trending movement
            return candles
        
        try:
            res = await agent.process_single_request("EURUSD", "H1", fetch_callback=mock_fetch)
            print(json.dumps(res, indent=2, default=str))
        finally:
            # IMPORTANT: Clean up all resources
            await agent.close()
            from backend.core.database import DatabasePool
            await DatabasePool.close()
            logger.info("Agent and Database connections closed.")
    
    asyncio.run(main())

