import os
import subprocess
import time
import sys
import re

def get_backend_processes():
    """Finds PIDs of python processes running 'backend.main:app'"""
    pids = []
    try:
        # Use PowerShell (Gwmi - more compatible than CimInstance on some servers)
        ps_cmd = "Gwmi Win32_Process | Where-Object { $_.Name -eq 'python.exe' } | Select-Object ProcessId, CommandLine | ConvertTo-Csv -NoTypeInformation"
        
        # Run PS command
        cmd = f"powershell -NoProfile -Command \"{ps_cmd}\""
        output = subprocess.check_output(cmd, shell=True).decode('utf-8', errors='ignore')
        
        lines = output.strip().splitlines()
        for line in lines:
            if not line.strip(): continue
            # CSV Format: "CommandLine","ProcessId"
            
            if "backend.main:app" in line or "telegram_bot.py" in line or "worker_ai.py" in line or "worker_trade.py" in line:
                parts = line.split(',')
                pid_raw = parts[-1].replace('"', '').strip()
                
                if pid_raw.isdigit():
                    print(f"[FOUND] PID {pid_raw}")
                    pids.append(pid_raw)
                
    except Exception as e:
        print(f"Error checking processes: {e}")
        
    return pids

def kill_processes(pids):
    if not pids:
        print("No zombie backend processes found.")
        return

    print(f"Detected {len(pids)} active backend instances. Cleaning up...")
    for pid in pids:
        try:
            # Force kill
            subprocess.run(f"taskkill /F /PID {pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f" -> Killed PID {pid}")
        except Exception as e:
            print(f" -> Failed to kill PID {pid}: {e}")
    
    # Wait a moment for OS to release ports
    time.sleep(2)

if __name__ == "__main__":
    print("--------------------------------")
    print("   MacroLens Process Manager    ")
    print("--------------------------------")
    
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        zombies = get_backend_processes()
        print(f"\nTotal Instances Detected: {len(zombies)}")
        sys.exit(len(zombies))
    else:
        # Default mode: Kill
        zombies = get_backend_processes()
        kill_processes(zombies)
        
        # New: Force Kill Port 8000
        try:
            print("Checking Ports 80 and 8000...")
            # Check both 80 and 8000
            for port in ["80", "8000"]:
                output = subprocess.check_output(f"netstat -ano | findstr :{port}", shell=True).decode()
            for line in output.splitlines():
                if "LISTENING" in line:
                    parts = line.split()
                    pid = parts[-1]
                    if pid.isdigit() and pid != "0":
                        print(f"[PORT BLOCKER] Found PID {pid} on Port {port}. Killing...")
                        subprocess.run(f"taskkill /F /PID {pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        killed = True

            if 'killed' in locals() and killed:
                print("Waiting for port release...")
                time.sleep(2)
                
        except subprocess.CalledProcessError:
            pass # No process found (findstr returns error code 1)
        except Exception as e:
            print(f"Port kill error: {e}")

        print("Done.")
