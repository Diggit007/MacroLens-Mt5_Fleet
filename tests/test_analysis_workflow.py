import asyncio
import sys
import pytest
from unittest.mock import MagicMock, AsyncMock

# MOCK EXPENSIVE DEPENDENCIES BEFORE IMPORT
sys.modules["backend.services.memory_store"] = MagicMock()
sys.modules["chromadb"] = MagicMock()
sys.modules["backend.services.debate_room"] = MagicMock()

# Import the worker function to test its internal logic
from backend.services.agent_service import MacroLensAgentV2

@pytest.mark.asyncio
async def test_agent_callback_signature_compliance():
    """
    Verifies that the Agent calls the fetch_callback with exactly 3 arguments:
    (account_id, symbol, timeframe)
    """
    agent = MacroLensAgentV2()
    
    # Mock the internal methods to avoid real API calls
    agent.ai_engine = MagicMock()
    agent.ai_engine.get_trading_signal = AsyncMock(return_value={"status": "success", "signal": "HOLD"})
    agent.get_cached_sentiment = MagicMock(return_value=0.5)
    agent.check_imminent_news = MagicMock(return_value=False)
    
    # Mock the callback
    # The callback MUST accept (account_id, symbol, timeframe)
    # If the agent calls it with different args, this mock will record it
    mock_callback = AsyncMock(return_value=[])
    
    user_id = "test_user"
    symbol = "EURUSD"
    timeframe = "H1"
    
    # Run the process
    try:
        await agent.process_single_request(
            symbol=symbol,
            timeframe=timeframe,
            fetch_callback=mock_callback,
            user_id=user_id
        )
    except Exception as e:
        # Ignore other errors, we just want to see how the callback was called
        pass
        
    # VERIFY THE CALL SIGNATURE
    # The Agent passes: (target_account_id, symbol, timeframe)
    # Since we didn't provide account_id and didn't mock settings.DEFAULT_ACCOUNT_ID, it likely passed None
    # We just want to verify it passed 3 arguments
    mock_callback.assert_called()
    call_args_list = mock_callback.call_args_list
    assert len(call_args_list) >= 1
    
    # VERIFY THE CALL SIGNATURE
    mock_callback.assert_called()
    call_args_list = mock_callback.call_args_list
    assert len(call_args_list) >= 1
    
    print(f"\nCaptured {len(call_args_list)} callback calls.")
    
    # We expect multiple calls (D1, H4, H1, etc.)
    # Check that at least one of them matches our requested timeframe structure
    matched_timeframe = False
    
    for call in call_args_list:
        args, kwargs = call
        assert len(args) == 3, f"Callback called with {len(args)} args: {args}"
        # args[0] is account_id (None)
        assert args[1] == symbol
        # args[2] is timeframe (D1, H4, etc.)
        if args[2] == timeframe:
            matched_timeframe = True
            
    assert matched_timeframe, f"Callback never called for requested timeframe {timeframe}. Calls: {[c[0][2] for c in call_args_list]}"
    print("\n✅ Agent Service correctly calls callback with (account_id, symbol, timeframe) structure")

from backend.services.metaapi_service import resolve_symbol
from unittest.mock import patch

@pytest.mark.asyncio
async def test_resolve_symbol_fallback():
    """
    Verifies that resolve_symbol returns the clean symbol if get_symbols returns empty/fails.
    """
    account_id = "test_acc"
    symbol = "EURUSD"
    
    # Mock connection
    mock_connection = MagicMock()
    # Case 1: get_symbols returns empty list
    mock_connection.get_symbols = AsyncMock(return_value=[])
    
    # Mock get_account to return our mock connection
    with patch("backend.services.metaapi_service.get_account", new=AsyncMock(return_value={'connection': mock_connection})):
        # We need to explicitly clear global cache for test
        with patch("backend.services.metaapi_service.SYMBOL_CACHE", {}):
            resolved = await resolve_symbol(mock_connection, account_id, symbol) # Note: function signature might ignore connection arg if it fetches it itself?
            
            # Wait, resolve_symbol signature is: async def resolve_symbol(connection, account_id: str, clean_symbol: str) -> str:
            # But line 35 calls get_account(account_id).
            # If I pass a connection, does it use it? 
            # Line 34: try: data = await get_account... connection = data['connection']
            # It seems it OVERWRITES the passed connection argument! 
            # So patching get_account is MANDATORY.
            
            # Should fallback to clean symbol
            assert resolved == symbol
            print("\n✅ resolve_symbol correctly falls back to input symbol on empty list")
            
        # Case 2: Exact Request
        mock_connection.get_symbols = AsyncMock(return_value=["EURUSD", "GBPUSD"])
        with patch("backend.services.metaapi_service.SYMBOL_CACHE", {}):
            resolved = await resolve_symbol(mock_connection, account_id, symbol)
            assert resolved == "EURUSD"
            print("\n✅ resolve_symbol correctly finds exact match")

if __name__ == "__main__":
    # Manually run if executed as script
    loop = asyncio.new_event_loop()
    loop.run_until_complete(test_agent_callback_signature_compliance())
    loop.run_until_complete(test_resolve_symbol_fallback())
