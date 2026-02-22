import os

log_file = r"C:\tmp\openclaw\openclaw-2026-01-31.log"

try:
    with open(log_file, 'rb') as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(size - 4096, 0))  # Read last 4KB
        lines = f.readlines()
        for line in lines[-20:]: # Print last 20 lines
            print(line.decode('utf-8', errors='ignore').strip())
except Exception as e:
    print(f"Error reading log: {e}")
