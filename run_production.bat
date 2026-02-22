@echo off
echo ========================================================
echo   MacroLens Production Launcher (Windows)
echo ========================================================
echo.

:: 1. Move to Project Root (C:\MacroLens)
pushd %~dp0..
echo [INFO] Working Directory: %CD%

:: 2. Set PYTHONPATH explicitly to Root
set PYTHONPATH=%CD%


:: 3. AUTO-CLEANUP: Kill any old instances to prevent "The Clone Attack"
echo [INFO] Health Check / Cleanup...
python backend/manage_processes.py

:: 3.5 SYSTEM HEALTH CHECK (Prevents "Whac-A-Mole" Regressions)
echo [INFO] Verifying Dependency Integrity...
python check_health.py
if %errorlevel% neq 0 (
    echo [ERROR] System Health Check Failed! 
    echo [ERROR] Please check the logs above.
    echo [ERROR] Please check the logs above.
    exit /b 1
    exit /b 1
)

:: 4. Launch Workers (Background)
echo [INFO] Starting Telegram Bot Worker...
start "Telegram Bot" /MIN python backend/workers/telegram_bot.py

echo [INFO] Starting AI Worker (Analysis Pipeline)...
start "AI Worker" /MIN python backend/worker_ai.py

echo [INFO] Starting Trade Worker (Execution Manager)...
start "Trade Worker" /MIN python backend/worker_trade.py

:loop

:: 5. Launch as Module (backend.main:app)
echo [INFO] Starting Uvicorn (backend.main:app)...
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 1

echo.
echo [WARN] Server crashed or stopped. Restarting in 5 seconds...
:: Use Python for reliable sleep (timeout can be skipped by keypress or fail in non-interactive shells)
python -c "import time; time.sleep(5)"
goto loop

popd
