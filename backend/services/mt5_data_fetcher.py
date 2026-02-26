"""
MT5 Data Fetcher
================
Thin wrapper around the MetaTrader5 Python package.
Provides candle data in the same format as MetaApi so TechnicalAnalyzer works unchanged.
"""

import logging
import time
import threading
from typing import List, Dict, Optional
from datetime import datetime, timezone


logger = logging.getLogger("MT5DataFetcher")

# Attempt to import MetaTrader5 — graceful fallback if not installed
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    logger.warning("MetaTrader5 package not installed. MT5DataFetcher will be unavailable.")


# Timeframe mapping: string → MT5 constant
TF_MAP = {}
if MT5_AVAILABLE:
    TF_MAP = {
        "M1":  mt5.TIMEFRAME_M1,
        "M5":  mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1":  mt5.TIMEFRAME_H1,
        "H4":  mt5.TIMEFRAME_H4,
        "D1":  mt5.TIMEFRAME_D1,
        "W1":  mt5.TIMEFRAME_W1,
        "MN1": mt5.TIMEFRAME_MN1,
    }

# Broker symbol name overrides (populated at runtime via auto-detect)
_SYMBOL_MAP: Dict[str, str] = {}

class MT5DataFetcher:
    """
    Fetches market data directly from an installed MT5 terminal.
    Thread-safe implementation using Reentrant Lock.
    """

    def __init__(self):
        self._initialized = False
        self._available_symbols: List[str] = []
        self._lock = threading.RLock()

    def initialize(self) -> bool:
        """Connect to MT5 terminal. Returns True on success."""
        with self._lock:
            if not MT5_AVAILABLE:
                logger.error("MetaTrader5 package not installed.")
                return False

            if self._initialized and self.health_check():
                return True

            if not mt5.initialize():
                logger.error(f"MT5 initialization failed: {mt5.last_error()}")
                return False

            terminal_info = mt5.terminal_info()
            if terminal_info:
                logger.info(
                    f"MT5 Connected: {terminal_info.name} | "
                    f"Company: {terminal_info.company} | "
                    f"Build: {terminal_info.build}"
                )
            
            # Cache available symbols
            self._refresh_symbols()
            self._initialized = True
            return True

    def shutdown(self):
        """Disconnect from MT5."""
        with self._lock:
            if MT5_AVAILABLE and self._initialized:
                mt5.shutdown()
                self._initialized = False
                logger.info("MT5 disconnected.")

    def health_check(self) -> bool:
        """Returns True if MT5 terminal is connected and responsive."""
        if not MT5_AVAILABLE:
            return False
        try:
            info = mt5.terminal_info()
            return info is not None and info.connected
        except Exception:
            return False

    def _refresh_symbols(self):
        """Cache available symbol names from the broker."""
        # Called from initialize (already locked)
        try:
            symbols = mt5.symbols_get()
            if symbols:
                self._available_symbols = [s.name for s in symbols]
                logger.info(f"MT5: {len(self._available_symbols)} symbols available.")
                # Build auto-mapping for common names
                self._build_symbol_map()
        except Exception as e:
            logger.error(f"Failed to refresh symbols: {e}")

    def get_all_symbols(self) -> List[str]:
        """Returns the list of all symbols available on the local MT5 terminal."""
        with self._lock:
            if not self._available_symbols:
                if self.initialize():
                    self._refresh_symbols()
            return self._available_symbols

    def _build_symbol_map(self):
        """
        Auto-detect broker symbol names.
        E.g., some brokers use 'EURUSD', others 'EURUSDm', 'EURUSD.a', etc.
        """
        global _SYMBOL_MAP
        standard_names = [
            "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
            "EURJPY", "GBPJPY", "EURGBP", "AUDNZD", "AUDCAD", "AUDCHF", "AUDJPY",
            "XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD", "US30", "USOIL",
        ]
        
        for std in standard_names:
            # Check exact match first
            if std in self._available_symbols:
                _SYMBOL_MAP[std] = std
                continue
            
            # Try common suffixes
            for suffix in [".a", "m", ".raw", "_SB", ".ecn", ".std"]:
                candidate = std + suffix
                if candidate in self._available_symbols:
                    _SYMBOL_MAP[std] = candidate
                    logger.debug(f"Symbol mapped: {std} → {candidate}")
                    break
            
            # Try case-insensitive partial match
            if std not in _SYMBOL_MAP:
                for available in self._available_symbols:
                    if std.lower() in available.lower() and len(available) <= len(std) + 4:
                        _SYMBOL_MAP[std] = available
                        logger.debug(f"Symbol fuzzy matched: {std} → {available}")
                        break

        logger.info(f"Symbol map built: {len(_SYMBOL_MAP)} of {len(standard_names)} mapped.")

    def _resolve_symbol(self, symbol: str) -> Optional[str]:
        """Resolve a standard symbol name to broker-specific name."""
        return _SYMBOL_MAP.get(symbol, symbol)

    def get_symbol_price(self, symbol: str) -> Optional[Dict]:
        """
        Get current bid/ask for a symbol.
        Returns: {"bid": 1.0850, "ask": 1.0852, "time": "..."}
        """
        with self._lock:
            if not self._ensure_connected():
                return None

            broker_sym = self._resolve_symbol(symbol)
            tick = mt5.symbol_info_tick(broker_sym)
            
            if tick is None:
                logger.warning(f"No tick data for {broker_sym}")
                return None

            return {
                "bid": tick.bid,
                "ask": tick.ask,
                "last": tick.last,
                "time": datetime.fromtimestamp(tick.time, tz=timezone.utc).isoformat(),
            }

    def fetch_candles(self, symbol: str, timeframe: str, count: int = 200) -> Optional[List[Dict]]:
        """
        Fetch candlestick data from MT5.
        
        Args:
            symbol: Standard symbol name (e.g., "EURUSD")
            timeframe: String timeframe (e.g., "H1", "D1", "M15")
            count: Number of candles to fetch
            
        Returns:
            List of candle dicts in MetaApi-compatible format, or None on failure.
        """
        with self._lock:
            if not self._ensure_connected():
                return None

            broker_sym = self._resolve_symbol(symbol)
            tf_const = TF_MAP.get(timeframe)
            
            if tf_const is None:
                logger.error(f"Unknown timeframe: {timeframe}")
                return None

            # Ensure the symbol is visible in Market Watch
            if not mt5.symbol_select(broker_sym, True):
                logger.warning(f"Symbol {broker_sym} not available / cannot be selected.")
                return None

            # Fetch rates
            rates = mt5.copy_rates_from_pos(broker_sym, tf_const, 0, count)
            
            if rates is None or len(rates) == 0:
                error = mt5.last_error()
                logger.warning(f"No candle data for {broker_sym} {timeframe}: {error}")
                return None

            # Convert NumPy structured array to list of dicts (MetaApi format)
            candles = []
            for rate in rates:
                candles.append({
                    "time": datetime.fromtimestamp(rate['time'], tz=timezone.utc).isoformat(),
                    "open": float(rate['open']),
                    "high": float(rate['high']),
                    "low": float(rate['low']),
                    "close": float(rate['close']),
                    "tickVolume": int(rate['tick_volume']),
                    "volume": int(rate['real_volume']) if 'real_volume' in rate.dtype.names else 0,
                })

            return candles

    def get_available_symbols(self) -> List[str]:
        """Returns list of symbols available in the broker's MT5 terminal."""
        # This method only reads _SYMBOL_MAP, which is populated during initialization
        # and then read-only. No lock needed for simple read access to a dict.
        return list(_SYMBOL_MAP.keys())

    def fetch_deals(self, start: datetime, end: datetime) -> List[Dict]:
        """
        Fetch history deals from MT5.
        
        Args:
            start: Start datetime (timezone aware or naive)
            end: End datetime (timezone aware or naive)
            
        Returns:
            List of deals in MetaApi-compatible format.
        """
        with self._lock:
            if not self._ensure_connected():
                return []
            
            try:
                # MT5 expects naive datetimes associated with the terminal's timezone? 
                # Or UTC? mt5.history_deals_get accepts datetime objects.
                # Documentation says "datetime object or timestamp".
                
                deals = mt5.history_deals_get(start, end)
                
                if deals is None:
                    error = mt5.last_error()
                    # Error code 1 (generic success?) sometimes returned if no deals?
                    if error[0] != 1:
                        logger.warning(f"Failed to fetch deals: {error}")
                    return []

                results = []
                for deal in deals:
                    # Map standard Deal fields to MetaApi format
                    # MetaApi Deal: { id, type, time, brokerTime, commission, swap, profit, symbol, magic, orderId, positionId, reason, entryType, volume, price }
                    
                    # Entry Type Mapping
                    # 0: IN, 1: OUT, 2: INOUT, 3: OUT_BY
                    entry_type_map = {0: 'DEAL_ENTRY_IN', 1: 'DEAL_ENTRY_OUT', 2: 'DEAL_ENTRY_INOUT', 3: 'DEAL_ENTRY_OUT_BY'}
                    entry_str = entry_type_map.get(deal.entry, str(deal.entry))
                    
                    # Deal Type Mapping
                    # 0: DEAL_TYPE_BUY, 1: DEAL_TYPE_SELL, 2: DEAL_TYPE_BALANCE...
                    type_map = {0: 'DEAL_TYPE_BUY', 1: 'DEAL_TYPE_SELL', 2: 'DEAL_TYPE_BALANCE'}
                    type_str = type_map.get(deal.type, str(deal.type))

                    d = {
                        "id": str(deal.ticket),
                        "platform": "mt5",
                        "type": type_str,
                        "time": datetime.fromtimestamp(deal.time, tz=timezone.utc).isoformat(),
                        "brokerTime": datetime.fromtimestamp(deal.time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f"), # Approximation
                        "commission": float(deal.commission),
                        "swap": float(deal.swap),
                        "profit": float(deal.profit),
                        "symbol": deal.symbol,
                        "magic": int(deal.magic),
                        "orderId": str(deal.order),
                        "positionId": str(deal.position_id),
                        "reason": str(deal.reason),
                        "entryType": entry_str,
                        "volume": float(deal.volume),
                        "price": float(deal.price),
                    }
                    results.append(d)
                
                return results

            except Exception as e:
                logger.error(f"Fetch Deals Exception: {e}")
                return []

    def _ensure_connected(self) -> bool:
        """Auto-reconnect if connection dropped."""
        # Called from locked methods, so don't lock here or use RLock
        if self._initialized:
             # health_check locks, so RLock is needed!
             if self.health_check():
                 return True
        
        logger.warning("MT5 connection lost. Attempting reconnect...")
        self._initialized = False
        
        for attempt in range(3):
            # initialize locks, so RLock is needed
            if self.initialize():
                logger.info(f"MT5 reconnected on attempt {attempt + 1}.")
                return True
            time.sleep(2 ** attempt)
        
        logger.warning("MT5 reconnection failed after 3 attempts.")
        return False


# ---------------------------------------------------------------------------
# Singleton instance
# ---------------------------------------------------------------------------
mt5_fetcher = MT5DataFetcher()


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")
    
    if mt5_fetcher.initialize():
        print(f"\nAvailable symbols: {mt5_fetcher.get_available_symbols()[:10]}...")
        
        # Test candle fetch
        candles = mt5_fetcher.fetch_candles("EURUSD", "H1", 10)
        if candles:
            print(f"\nEURUSD H1 — Last 3 candles:")
            for c in candles[-3:]:
                print(f"  {c['time']} | O:{c['open']:.5f} H:{c['high']:.5f} L:{c['low']:.5f} C:{c['close']:.5f}")
        
        # Test tick
        price = mt5_fetcher.get_symbol_price("EURUSD")
        print(f"\nEURUSD Tick: {price}")
        
        mt5_fetcher.shutdown()
    else:
        print("MT5 initialization failed.")
