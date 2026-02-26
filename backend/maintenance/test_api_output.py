import requests
import asyncio
import uvicorn
from contextlib import asynccontextmanager
import threading
import time

# We need to run the app briefly or just Mock request?
# Actually, the server relies on DB and Config. 
# It's better to bypass HTTP and import the endpoint function directly if possible, or run a minimal app.
# But main.py is complex.

# Let's just create a script that IMPORTS the EventPredictor and runs it, 
# mimicking the endpoint logic exactly.

from backend.services.event_predictor import EventPredictor

def test_analysis_output():
    print("Simulating Frontend Analysis Request for 'CPI'...")
    predictor = EventPredictor()
    
    # Simulate API Parameter
    event_name = "CPI"
    forecast = 3.2
    previous = 3.1
    currency = "USD"
    
    # Run Prediction
    prediction = predictor.predict_event(event_name, forecast, previous, currency)
    
    # Construct JSON Response (as API would)
    response = {
        "event": event_name,
        "prediction": prediction.predicted_outcome,
        "confidence": prediction.confidence,
        "forecast_pips": prediction.avg_pips,   # <--- The Feature User Wants
        "trend_direction": prediction.trend_forecast, # <--- The Feature User Wants
        "recommendation": prediction.recommendation,
        "details": f"Forecast: {forecast}, Previous: {previous}"
    }
    
    print("\n--- JSON RESPONSE TO FRONTEND ---")
    import json
    print(json.dumps(response, indent=2))

if __name__ == "__main__":
    test_analysis_output()
