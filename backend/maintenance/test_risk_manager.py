from backend.services.risk_manager import RiskManager

def test_risk_manager():
    rm = RiskManager()
    
    # Defaults
    # High Confidence = 1.5% Risk
    # Med Confidence = 1.0% Risk
    # Low Confidence = 0.5% Risk
    # Pip Value ~ $10 for Standard
    
    # Case 1: $10,000 Equity, High Confidence, 20 Pip SL
    # Risk = $150
    # Cost per pip = $150 / 20 = $7.5
    # Lots = $7.5 / $10 = 0.75 Lots
    print("\n[Case 1] $10k, High Conf, 20 SL")
    lots = rm.calculate_lots(10000, "EURUSD", 20, "HIGH")
    print(f"Lots: {lots}")
    assert lots == 0.75
    
    # Case 2: $100,000 Equity, Med Conf, 50 Pip SL
    # Risk = $1,000 (1%)
    # Cost per pip = $1000 / 50 = $20
    # Lots = $20 / $10 = 2.0 Lots
    print("\n[Case 2] $100k, Med Conf, 50 SL")
    lots = rm.calculate_lots(100000, "EURUSD", 50, "MEDIUM")
    print(f"Lots: {lots}")
    assert lots == 2.0
    
    # Case 3: Small Account ($100), Low Conf, 20 SL
    # Risk = $0.50 (0.5%)
    # Cost per pip = $0.50 / 20 = $0.025
    # Lots = $0.025 / $10 = 0.0025 -> Min 0.01
    print("\n[Case 3] $100, Low Conf, 20 SL (Min Check)")
    lots = rm.calculate_lots(100, "EURUSD", 20, "LOW")
    print(f"Lots: {lots}")
    assert lots == 0.01
    
    print("\n✅ Risk Manager Verified!")

if __name__ == "__main__":
    test_risk_manager()
