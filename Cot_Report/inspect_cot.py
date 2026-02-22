
import pandas as pd
import os

file_path = r"C:\MacroLens\backend\Cot_Report\Complete_COT_Report.xlsm"


    with open("inspection.log", "w") as f:
        f.write(f"Inspecting: {file_path}\n")
        xls = pd.ExcelFile(file_path)
        
        f.write("\nSHEET NAMES:\n")
        f.write(str(xls.sheet_names) + "\n")
        
        for sheet in xls.sheet_names:
            f.write(f"\n--- SHEET: {sheet} ---\n")
            try:
                # Read a bit more to find headers
                df = pd.read_excel(xls, sheet_name=sheet, nrows=20, header=None)
                f.write("FIRST 10 ROWS:\n")
                f.write(df.head(10).to_string() + "\n")
            except Exception as e:
                f.write(f"Error reading sheet {sheet}: {e}\n")
            
except Exception as e:
    with open("inspection.log", "w") as f:
        f.write(f"CRITICAL ERROR: {e}\n")
