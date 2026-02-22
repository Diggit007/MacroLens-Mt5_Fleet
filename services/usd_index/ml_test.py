
import os
import sys
import pandas as pd
import numpy as np
import yfinance as yf
from sklearn.linear_model import Ridge
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import r2_score
import matplotlib.pyplot as plt
from dotenv import load_dotenv
from pathlib import Path

# Ensure project root is in path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from backend.services.usd_index.index_engine import USDIndexEngine

def load_environment():
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
    
    # Handle MultiIndex columns
    if isinstance(dxy.columns, pd.MultiIndex):
        try:
            if 'Adj Close' in dxy.columns.get_level_values(0):
                return dxy.xs('Adj Close', level=0, axis=1).iloc[:, 0]
            elif 'Close' in dxy.columns.get_level_values(0):
                return dxy.xs('Close', level=0, axis=1).iloc[:, 0]
        except:
            pass
            
    if 'Adj Close' in dxy.columns: return dxy['Adj Close']
    if 'Close' in dxy.columns: return dxy['Close']
    return dxy.iloc[:, 0]

def run_ml_test():
    load_environment()
    engine = USDIndexEngine()
    engine.run_pipeline()
    
    features_df = engine._component_data.dropna()
    start_date = features_df.index.min().strftime('%Y-%m-%d')
    dxy_series = fetch_dxy(start_date)
    common_idx = features_df.index.intersection(dxy_series.index)
    X = features_df.loc[common_idx]
    y = dxy_series.loc[common_idx]

    # Linear
    linear = Ridge(alpha=1.0)
    linear.fit(X, y)
    y_lin = linear.predict(X)
    r2_lin = r2_score(y, y_lin)

    # Random Forest
    rf = RandomForestRegressor(n_estimators=100, max_depth=5, random_state=42)
    rf.fit(X, y)
    y_rf = rf.predict(X)
    r2_rf = r2_score(y, y_rf)

    print("-" * 30)
    print(f"Linear R2: {r2_lin:.4f}")
    print(f"Random Forest R2: {r2_rf:.4f}")
    print("-" * 30)
    
    # Feature Importance (RF)
    imps = pd.Series(rf.feature_importances_, index=X.columns).sort_values(ascending=False)
    print("RF Feature Importance:")
    print(imps)

    plt.figure(figsize=(12, 6))
    plt.plot(y.index, y, label='Actual DXY', color='black')
    plt.plot(y.index, y_rf, label=f'Random Forest (R2={r2_rf:.2f})', color='green', alpha=0.8)
    plt.plot(y.index, y_lin, label=f'Linear (R2={r2_lin:.2f})', color='red', linestyle='--', alpha=0.6)
    plt.title("Model Comparison: Linear vs Random Forest")
    plt.legend()
    plt.savefig("ml_comparison.png")
    print("Saved ml_comparison.png")

if __name__ == "__main__":
    run_ml_test()
