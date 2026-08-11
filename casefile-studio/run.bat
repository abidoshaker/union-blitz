@echo off
setlocal EnableExtensions EnableDelayedExpansion
title CaseFile Studio

rem  Starts the backend on http://localhost:8760 and opens a browser.
rem  Run install-deps.bat first.

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "VPY=%ROOT%\.venv\Scripts\python.exe"
set "PORT=8760"

if not exist "%VPY%" (
  echo.
  echo  No virtual environment found.
  echo  Run install-deps.bat first.
  echo.
  pause
  exit /b 1
)

rem Pick up the FFmpeg location install-deps.bat recorded, without touching
rem the system PATH.
if exist "%ROOT%\.env.local" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ROOT%\.env.local") do (
    if /I "%%A"=="CASEFILE_FFMPEG_BIN" set "PATH=%%B;!PATH!"
  )
)

if not exist "%ROOT%\backend\app\main.py" (
  echo.
  echo  The backend is not built yet - only the installer and spec are in
  echo  this folder so far. Once backend\app\main.py exists this script
  echo  will start it.
  echo.
  pause
  exit /b 1
)

echo.
echo  Starting CaseFile Studio on http://localhost:%PORT%
echo  Leave this window open. Ctrl+C stops the server.
echo.

start "" "http://localhost:%PORT%"
"%VPY%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT% --app-dir "%ROOT%\backend"

echo.
echo  Server stopped.
pause
exit /b 0
