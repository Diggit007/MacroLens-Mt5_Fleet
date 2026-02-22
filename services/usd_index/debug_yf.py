import yfinance as yf
print("Downloading DXY...")
df = yf.download("DX-Y.NYB", period="1mo", progress=False)
print("Columns:", df.columns)
print("Head:", df.head())
if 'Adj Close' in df.columns:
    print("Adj Close found.")
else:
    print("Adj Close NOT found.")
