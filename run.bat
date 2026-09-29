@echo off
rem Bausastra one-click launcher: backend API + built Svelte frontend.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [bausastra] No virtualenv found at .venv. Create it first:
  echo   python -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)

if not exist "web\dist\index.html" (
  echo [bausastra] Frontend not built yet - building now...
  where npm >nul 2>nul
  if errorlevel 1 (
    echo [bausastra] Node.js/npm is required to build the frontend.
    pause
    exit /b 1
  )
  pushd web
  call npm install
  call npm run build
  popd
  if not exist "web\dist\index.html" (
    echo [bausastra] Frontend build failed.
    pause
    exit /b 1
  )
)

echo [bausastra] Starting API + website at http://127.0.0.1:5000 ...
start "Bausastra API" .venv\Scripts\python.exe -m bausastra.cli serve --port 5000
timeout /t 8 /nobreak >nul
start http://127.0.0.1:5000
echo [bausastra] Running. Close the "Bausastra API" window to stop the server.
