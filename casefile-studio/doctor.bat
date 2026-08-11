@echo off
setlocal EnableExtensions
title CaseFile Studio - environment check

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "VPY=%ROOT%\.venv\Scripts\python.exe"

if not exist "%VPY%" (
  echo.
  echo  No virtual environment found. Run install-deps.bat first.
  echo.
  pause
  exit /b 1
)

if exist "%ROOT%\.env.local" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ROOT%\.env.local") do (
    if /I "%%A"=="CASEFILE_FFMPEG_BIN" set "CASEFILE_FFMPEG_BIN=%%B"
  )
)

"%VPY%" "%ROOT%\tools\doctor.py"
echo.
pause
exit /b 0
