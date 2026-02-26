
import os
import sys

# Ensure project root is in path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from backend.services.usd_index.calibration import run_calibration

if __name__ == "__main__":
    run_calibration()
