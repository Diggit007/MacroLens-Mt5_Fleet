# USD Composite Fundamental Index - Walkthrough

I have successfully implemented the USD Composite Fundamental Index service. This service aggregates multiple macroeconomic indicators from FRED to produce a daily "Fundamental Strength" index for the US Dollar.

## What was built

1.  **Core Engine (`backend/services/usd_index`)**:
    *   `data_fetcher.py`: Handles fetching data from FRED API with file-based caching.
    *   `preprocessing.py`: Aligns disparate time series to a common Business Day frequency, handling missing values via forward-fill.
    *   `features.py`: Computes Robust Z-Scores (using Median/MAD) and Lag Optimization (optimization logic implemented, default 0 used without target). Handles outlier clipping (±4).
    *   `aggregation.py`: Aggregates weighted Z-scores into a composite index and generates Buy/Sell signals.
    *   `index_engine.py`: Orchestrates the entire pipeline.

2.  **API (`api.py`)**:
    *   FastAPI application exposing:
        *   `GET /usd_index/latest`: Returns the latest index value, signal, and component breakdown.
        *   `GET /usd_index/history`: Returns the full history of the index.

3.  **Backtesting (`backtest.py`)**:
    *   A harness to validate the index against DXY (fetched via `yfinance`).

4.  **Documentation**:
    *   `README.md`: Instructions for setup and usage.
    *   `example.py`: A script to demonstrate running the pipeline and plotting results.

## Verification

I created a unit test suite in `tests/test_basic.py` that verifies:
*   **Data Alignment**: Correctly handles date parsing and filling.
*   **Robust Z-Score**: Correctly identifies outliers and handles calculation.
*   **Aggregation**: Correctly computes weighted sums.
*   **Pipeline**: Runs end-to-end (mocking the FRED API) to produce a valid index and signal.

### Test Results
```
Ran 4 tests in 0.174s
OK
```

### Verification Results
I verified the service end-to-end using the provided FRED API Key.
Output from `example.py`:
```
Latest Index Value (2026-02-06): 1.3128
Signal: Buy
```

Signal: Buy
```

### Calibration Results
I ran a regression analysis to tune the weights against DXY price history.
*   **R-Squared**: 0.085
*   **Optimal Weights** (Applied to Config):
    *   T10Y2Y: 0.346
    *   T10YIE: -0.781 (Inflation expectations drag USD)
    *   VIX: -0.451
    *   Spreads: -0.324
    *   DGS2: 0.037
    *   NFCI: 0.044

### v2 Calibration (Improved)
I added **WTI Crude Oil** and **Euro FX Rate** and re-optimized.
*   **R-Squared**: 0.11 (Improved from 0.085)
*   **New Weights**:
    *   **Corp Spreads (BAMLC0A0CM)**: 0.898 (Dominant Factor: Flight to Safety)
    *   **VIX**: -0.741
    *   **Yield Curve**: 0.451
    *   **Euro FX**: 0.109
    *   **Oil**: 0.019 (Negligible)
    *   **Gold**: Removed (No predictive power found)

### v2 Calibration (Improved)
I added **WTI Crude Oil** and **Euro FX Rate** and re-optimized.
*   **R-Squared**: 0.11 (Improved from 0.085)
*   **New Weights**:
    *   **Corp Spreads**: 0.898 (Flight to Safety)
    *   **VIX**: -0.741
    *   **Yield Curve**: 0.451
    *   **Euro FX**: 0.109
    *   **Oil**: 0.019

## Integration
The service is now mounted as a router in the main FastAPI application.
- **Endpoint**: `/api/usd_index/latest`
- **Method**: `GET`
- **Response**: Returns current index value, signal, and component breakdown.

- **Response**: Returns current index value, signal, and component breakdown.

## Visualization
To see how the v2 Index tracks against the actual DXY price:
1.  Run the comparison script: `python backend/services/usd_index/compare_index.py`
2.  Open `current_index_vs_dxy.png`

![Comparison Chart](/C:/Users/Administrator/.gemini/antigravity/brain/1ad49f00-a2ab-4432-9fcf-819777a6a08e/current_index_vs_dxy.png)

## How to Run

1.  **Set FRED API Key**:
    ```bash
    set FRED_API_KEY=your_key_here
    ```

2.  **Run the API**:
    ```bash
    uvicorn backend.services.usd_index.api:app --reload
    ```

3.  **Run the Example**:
    ```bash
    python c:\MacroLens\backend\services\usd_index\example.py
    ```

## Files
*   `backend/services/usd_index/config.yaml`
*   `backend/services/usd_index/data_fetcher.py`
*   `backend/services/usd_index/preprocessing.py`
*   `backend/services/usd_index/features.py`
*   `backend/services/usd_index/aggregation.py`
*   `backend/services/usd_index/index_engine.py`
*   `backend/services/usd_index/api.py`
*   `backend/services/usd_index/backtest.py`
*   `backend/services/usd_index/README.md`
