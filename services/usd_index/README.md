# USD Composite Fundamental Index

A Python service that computes a daily "Fundamental Strength" index for the US Dollar based on macro-economic drivers from FRED (Federal Reserve Economic Data).

## Overview

The index aggregates multiple fundamental drivers:
- **Yield Curve (10Y-2Y)**: Growth/Inflation expectations.
- **2-Year Yields**: Short-term rate differentials.
- **Corporate Spreads**: Risk sentiment / Credit stress.
- **Breakeven Inflation**: Inflation expectations.
- **Financial Conditions**: Liquidity tightness.
- **VIX**: Safe-haven demand.

It produces:
- `composite_index`: A robust Z-score indicating deviation from the mean (Positive = USD Strength).
- `signal`: "Buy" / "Sell" / "Neutral" based on index thresholds.

## Setup

1. **Install Dependencies**:
   ```bash
   pip install fredapi pandas numpy scipy pyyaml fastapi uvicorn yfinance
   ```
2. **Set API Key**:
   Get a free API key from [FRED](https://fred.stlouisfed.org/docs/api/api_key.html).
   ```bash
   export FRED_API_KEY="your_api_key_here"
   # Or set in Windows
   set FRED_API_KEY=your_api_key_here
   ```

## Usage

### Run API
```bash
uvicorn backend.services.usd_index.api:app --reload
```
Endpoints:
- `GET /usd_index/latest`: Current index value.
- `GET /usd_index/history`: Historical series.

### Run as Library
```python
from backend.services.usd_index.index_engine import USDIndexEngine

engine = USDIndexEngine()
# Run pipeline (fetch, process, aggregate)
df = engine.run_pipeline()
print(df.tail())
```

### Configuration
Edit `backend/services/usd_index/config.yaml` to add/remove series or change weights.
