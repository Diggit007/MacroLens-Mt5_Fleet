import os
import shutil
import socket
import psutil
import subprocess
import logging
import asyncio
import time

logger = logging.getLogger("Provisioner")

class FleetProvisioner:
    def __init__(self, base_mt5_path: str, instances_dir: str):
        self.base_mt5_path = base_mt5_path
        self.instances_dir = instances_dir
        os.makedirs(self.instances_dir, exist_ok=True)
        
    def find_free_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            return s.getsockname()[1]

    async def provision_terminal(self, account: str, password: str, server: str, user_id: str = "") -> dict:
        """
        1. Clones the MT5 directory securely for the specific account.
        2. Spawns the worker.py subprocess pointing to this installation.
        """
        instance_path = os.path.join(self.instances_dir, str(account))
        terminal_exe = os.path.join(instance_path, "terminal64.exe")
        
        # 1. Clone Directory if not exists
        if not os.path.exists(instance_path):
            logger.info(f"Cloning base MT5 to {instance_path}...")
            if not os.path.exists(self.base_mt5_path):
                raise Exception(f"Base MT5 path {self.base_mt5_path} does not exist!")
            
            # Use shutil to copytree, ignoring large log folders if necessary
            shutil.copytree(self.base_mt5_path, instance_path, ignore=shutil.ignore_patterns('logs*', 'tester*'))
            
            # Inject a blank config to ensure portable mode (though we pass portable flag anyway)
            with open(os.path.join(instance_path, "origin.txt"), "w") as f:
                 f.write(f"Provisioned for {account} via Fleet Manager")

        # 2. Find free port for the worker API
        worker_port = self.find_free_port()
        
        # 3. Spawn Python Worker Subprocess
        # Launch worker.py and detach it
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        cmd = [
            "python", os.path.join(BASE_DIR, "worker.py"),
            "--port", str(worker_port),
            "--account", str(account),
            "--password", str(password),
            "--server", str(server),
            "--path", terminal_exe
        ]
        if user_id:
            cmd.extend(["--user_id", str(user_id)])
        
        logger.info(f"Launching worker for account {account} on port {worker_port}")
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=BASE_DIR,
            creationflags=subprocess.CREATE_NO_WINDOW # Fully detached to avoid blocking
        )
        
        # Wait a few seconds for FastAPI to boot and MT5 to initialize
        await asyncio.sleep(8)
        
        # We assume it successfully started in the background. If it crashes, the healthcheck loop in main.py will catch it.
        return {
            "account": account,
            "port": worker_port,
            "pid": process.pid,
            "path": instance_path,
            "status": "running"
        }

    def kill_worker(self, pid: int):
        try:
            p = psutil.Process(pid)
            p.terminate()
            p.wait(timeout=3)
            return True
        except psutil.NoSuchProcess:
            return True
        except Exception as e:
            logger.error(f"Failed to kill worker PID {pid}: {e}")
            return False
