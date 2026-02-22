# Setting up Redis on Windows (Phase 7 Requirement)

To enable **Multi-Worker Scaling (12+ Processes)**, you must install Redis. Windows does not support Redis natively, but there are two easy solutions.

## Option A: Memurai (Recommended for Native Windows)
Memurai is a Redis-compatible cache server for Windows. It is the easiest way to get "Redis" running without Docker or Linux.

1.  Download **Memurai Developer Edition** (Free) from: [https://www.memurai.com/get-memurai](https://www.memurai.com/get-memurai)
2.  Install the `.msi` file.
3.  It runs automatically as a Windows Service.
4.  **Done!** Your backend defaults to `redis://localhost:6379`, which Memurai listens on.

## Option B: WSL 2 (Windows Subsystem for Linux)
If you already use WSL (Ubuntu on Windows):

1.  Open your Ubuntu terminal.
2.  Run:
    ```bash
    sudo apt update
    sudo apt install redis-server
    sudo service redis-server start
    ```
3.  **Done!** Windows can access WSL's localhost ports automatically.

## Activating in Backend
Once a Redis/Memurai server is running:

1.  Open `backend/.env`
2.  Add/Update:
    ```ini
    USE_REDIS=True
    REDIS_URL=redis://localhost:6379/0
    ```
3.  Restart `run_production.bat`. 
    You will see logs confirming *Redis Cache Initialized*.
