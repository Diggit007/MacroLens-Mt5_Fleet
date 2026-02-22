
import os
import sys
import pandas as pd
import numpy as np
import yfinance as yf
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.metrics import r2_score
import matplotlib.pyplot as plt
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path

# Ensure project root is in path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from backend.services.usd_index.index_engine import USDIndexEngine

def load_environment():
    # Helper to load .env just like in data_fetcher
    try:
        env_path = Path(__file__).resolve().parents[2] / '.env'
        if env_path.exists():
            load_dotenv(env_path)
        else:
             alt_path = Path("c:/MacroLens/backend/.env")
             if alt_path.exists():
                 load_dotenv(alt_path)
    except Exception:
        pass

def fetch_dxy(start_date):
    print(f"Fetching DXY benchmark from {start_date}...")
    dxy = yf.download("DX-Y.NYB", start=start_date, progress=False)
    if dxy.empty:
        return pd.Series()
    
    # Handle MultiIndex columns (Ticker as level 1)
    if isinstance(dxy.columns, pd.MultiIndex):
        try:
            # Try to get Adj Close, else Close
            if 'Adj Close' in dxy.columns.get_level_values(0):
                return dxy.xs('Adj Close', level=0, axis=1).iloc[:, 0]
            elif 'Close' in dxy.columns.get_level_values(0):
                return dxy.xs('Close', level=0, axis=1).iloc[:, 0]
        except Exception as e:
            print(f"Error parsing MultiIndex: {e}")
            return pd.Series()
            
    # Handle Single Index
    if 'Adj Close' in dxy.columns:
        return dxy['Adj Close']
    elif 'Close' in dxy.columns:
        return dxy['Close']
        
    # Fallback: take first column
    return dxy.iloc[:, 0]

def run_calibration():
    # 1. Load Environment & Engine
    load_environment()
    engine = USDIndexEngine()
    
    print("Running Pipeline to get Feature Data...")
    # Force run to populate _component_data
    engine.run_pipeline()
    
    if not hasattr(engine, '_component_data') or engine._component_data is None:
        print("Error: No component data available from engine.")
        return

    # Get Features (X)
    features_df = engine._component_data.dropna()
    if features_df.empty:
        print("Error: Features DataFrame is empty.")
        return
        
    start_date = features_df.index.min().strftime('%Y-%m-%d')
    
    # 2. Get Target (y) -> DXY Price
    dxy_series = fetch_dxy(start_date)
    if dxy_series.empty:
        print("Error: Could not fetch DXY data.")
        return

    # 3. Align Data
    # Intersection of indices
    common_idx = features_df.index.intersection(dxy_series.index)
    
    X = features_df.loc[common_idx]
    y = dxy_series.loc[common_idx]
    
    if len(X) < 50:
        print("Error: Not enough overlapping data points for calibration.")
        return

    print(f"Calibrating on {len(X)} data points...")

    # 4. Regression Model
    # We want to find weights W such that Index ~ DXY.
    # Note: DXY is price level (approx 90-110).
    # Our Features are Z-scores (approx -3 to +3).
    # Model: DXY = Intercept + w1*F1 + w2*F2 ...
    
    # Using Ridge Regression to prevent extreme weights if features are correlated
    model = Ridge(alpha=1.0) 
    model.fit(X, y)
    
    y_pred = model.predict(X)
    r2 = r2_score(y, y_pred)
    
    print("-" * 30)
    print(f"Calibration Complete. R-Squared: {r2:.4f}")
    print("-" * 30)
    print("OPTIMIZED WEIGHTS:")
    
    # Normalize weights so they look like relative importance?
    # Or just output the raw coefficients which map Z-score to Price Level.
    # The 'weights' in our config are for a weighted SUM index.
    # Our engine calculates: Index = Sum(w * z) / Sum(w).
    # The regression calculates: Price = Intercept + Sum(coef * z).
    # So 'coef' is roughly proportional to 'weight'.
    
    coefs = pd.Series(model.coef_, index=X.columns)
    
    # Save weights to file for easy reading
    with open("weights.txt", "w") as f:
        f.write(coefs.sort_values(ascending=False).to_string())
        f.write(f"\nIntercept: {model.intercept_}")
        f.write(f"\nR2: {r2}")

    # Normalize coefs to sum to something (e.g. 10) or just show them relative to max
    # We'll just show the raw coefficients first
    print(coefs.sort_values(ascending=False))
    
    print("-" * 30)
    print(f"Intercept (Base DXY Level): {model.intercept_:.2f}")
    
    # 5. Visualization
    plt.figure(figsize=(14, 7))
    
    # Plot 1: Actual vs Fitted
    plt.subplot(2, 1, 1)
    plt.plot(y.index, y, label='Actual DXY', color='black', linewidth=1.5)
    plt.plot(y.index, y_pred, label='Calibrated Index (Fitted)', color='red', linestyle='--', linewidth=1)
    plt.title(f"DXY Calibration (R2: {r2:.3f})")
    plt.legend()
    plt.grid(True, alpha=0.3)
    
    # Plot 2: Residuals
    plt.subplot(2, 1, 2)
    residuals = y - y_pred
    plt.plot(y.index, residuals, label='Residuals (Actual - Fitted)', color='gray', alpha=0.7)
    plt.axhline(0, color='k', linestyle='-')
    plt.title("Residuals")
    plt.grid(True, alpha=0.3)
    
    plt.tight_layout()
    # Save to file instead of showing
    output_img = "calibration_result.png"
    plt.savefig(output_img)
    print(f"Calibration plot saved to {output_img}")
    # plt.show()

if __name__ == "__main__":
    run_calibration()
