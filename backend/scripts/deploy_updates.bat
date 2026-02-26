@echo off
echo ========================================================
echo   MACROLENS V3.2 DEPLOYMENT SCRIPT
echo ========================================================
echo.
echo Copying updated files from Workspace to C:\MacroLens...
echo.

copy /Y "backend\main.py" "C:\MacroLens\backend\main.py"
if %errorlevel% neq 0 echo [ERROR] Failed to copy main.py
if %errorlevel% equ 0 echo [SUCCESS] Updated main.py

copy /Y "backend\services\streaming_service.py" "C:\MacroLens\backend\services\streaming_service.py"
if %errorlevel% neq 0 echo [ERROR] Failed to copy streaming_service.py
if %errorlevel% equ 0 echo [SUCCESS] Updated streaming_service.py

copy /Y "backend\services\metaapi_service.py" "C:\MacroLens\backend\services\metaapi_service.py"
if %errorlevel% neq 0 echo [ERROR] Failed to copy metaapi_service.py
if %errorlevel% equ 0 echo [SUCCESS] Updated metaapi_service.py

echo.
echo ========================================================
echo   PLEASE RESTART YOUR PYTHON BACKEND NOW
echo   (Ctrl+C in the server window, then re-run)
echo ========================================================
pause
