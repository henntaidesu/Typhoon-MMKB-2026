@echo off
echo ========================================
echo   Typhoon MMKB dev startup script
echo ========================================
echo.

set ROOT=%~dp0
set BACKEND=%ROOT%backend
set FRONTEND=%ROOT%frontend

echo [1/2] Activating conda env MMKB and starting backend (uvicorn main:app, no auto-reload)...
call conda activate MMKB
if errorlevel 1 (
  echo [ERROR] Failed to activate conda env MMKB. Is conda installed and the env created?
  pause
  exit /b 1
)

cd /d %BACKEND%
start "MMKB Backend" cmd /k python -m uvicorn main:app --port 8000

timeout /t 2 /nobreak >nul

echo [2/2] Preparing frontend dev server...
where node >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Node.js not found. Install it and add to PATH: https://nodejs.org/
  pause
  exit /b 1
)
where npm >nul 2>&1
if errorlevel 1 (
  echo [ERROR] npm not found. Check your Node.js installation.
  pause
  exit /b 1
)

cd /d %FRONTEND%
if not exist node_modules (
  echo Installing frontend dependencies...
  call npm install
  if errorlevel 1 (
    echo [ERROR] npm install failed. Check the network or package.json
    pause
    exit /b 1
  )
)

echo.
echo ========================================
echo   Frontend ^(Vite^):  http://localhost:5173
echo   Backend API:      http://localhost:8000
echo   API docs:         http://localhost:8000/docs
echo   Ctrl+C stops the frontend; close the "MMKB Backend" window to stop the backend
echo   Backend does NOT auto-reload - restart that window to pick up code changes
echo ========================================
echo.

npm run dev
