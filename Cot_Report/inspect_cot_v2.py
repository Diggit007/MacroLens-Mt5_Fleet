
import pandas as pd
import os
import sys

file_path = r"C:\MacroLens\backend\Cot_Report\Complete_COT_Report.xlsm"
log_path = r"C:\MacroLens\backend\Cot_Report\inspection_v2.log"

try:
    with open(log_path, "w", encoding='utf-8') as f:
        f.write(f"Inspecting: {file_path}\n")
        
        if not os.path.exists(file_path):
            f.write("ERROR: File not found!\n")
            sys.exit(1)
            
        xls = pd.ExcelFile(file_path)
        
        f.write("\nSHEET NAMES:\n")
        f.write(str(xls.sheet_names) + "\n")
        
        for sheet in xls.sheet_names:
            f.write(f"\n{'='*30}\nSHEET: {sheet}\n{'='*30}\n")
            try:
                # Read header=None to see raw layout
                df = pd.read_excel(xls, sheet_name=sheet, nrows=20, header=None)
                f.write(df.to_string() + "\n")
            except Exception as e:
                f.write(f"Error reading sheet {sheet}: {e}\n")

    print("Inspection complete. Log written.")

except Exception as e:
    print(f"Global Error: {e}")
