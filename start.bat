@echo off
setlocal
echo ========================================
echo   Typhoon MMKB dev startup script
echo ========================================
echo.

set ROOT=%~dp0
set BACKEND=%ROOT%backend
set FRONTEND=%ROOT%frontend

echo [1/3] Activating conda env MMKB...
call conda activate MMKB
if errorlevel 1 (
  echo [ERROR] Failed to activate conda env MMKB. Is conda installed and the env created?
  pause
  exit /b 1
)

echo [2/3] Checking Node.js toolchain...
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

echo [3/3] Starting backend in this console (background) and frontend in foreground...
cd /d %BACKEND%
start /b "" python -m uvicorn main:app --port 8000

timeout /t 2 /nobreak >nul

echo.
echo ========================================
echo   Frontend ^(Vite^):  http://localhost:5173
echo   Backend API:      http://localhost:8000
echo   API docs:         http://localhost:8000/docs
echo   Both run in THIS window - Ctrl+C once stops everything
echo   Backend does NOT auto-reload - restart this script to pick up code changes
echo ========================================
echo.

cd /d %FRONTEND%
call npm run dev

echo.
echo Shutting down backend on port 8000...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:"LISTENING" ^| findstr ":8000 "') do (
  taskkill /f /pid %%p >nul 2>&1
)
echo Done.
endlocal
