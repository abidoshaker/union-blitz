@echo off
setlocal EnableExtensions EnableDelayedExpansion
title CaseFile Studio - dependency installer

rem ===========================================================================
rem  CaseFile Studio dependency installer for Windows 10/11 - CPU only.
rem
rem  Installs, in order:
rem     Python 3.12, FFmpeg with libx264 + libass, Node.js LTS,
rem     a project virtualenv, the Python dependency tiers, and the
rem     Kokoro local-voice model files.
rem
rem  Usage:
rem     install-deps.bat                    normal install
rem     install-deps.bat --minimal          core backend only, no local TTS
rem     install-deps.bat --with-align-full  add CPU torch + WhisperX, ~2.5 GB
rem     install-deps.bat --with-optional    add provider SDKs
rem     install-deps.bat --no-node          skip Node.js, backend only
rem     install-deps.bat --all              everything
rem
rem  Safe to re-run. Nothing here needs Administrator.
rem ===========================================================================

rem A batch file that dies part-way - a PATH too long for cmd, a bad block, an
rem installer that takes the shell down with it - closes the console when it was
rem started by double-clicking, so you never get to read what went wrong. Run the
rem real work in a child cmd and hold this window open whatever happens to it.
if not defined CASEFILE_INSTALL_CHILD (
  set "CASEFILE_INSTALL_CHILD=1"
  cmd /d /s /c ""%~f0" %*"
  set "RC=!ERRORLEVEL!"
  echo.
  if not "!RC!"=="0" (
    echo   ------------------------------------------------------------------
    echo    The installer stopped early ^(exit code !RC!^).
    echo    What it managed to do is in install-log.txt next to this file.
    echo   ------------------------------------------------------------------
    echo.
  )
  pause
  exit /b !RC!
)

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

rem Called by full path: half the point of refresh_path is that PATH may be in
rem a bad way, and a fallback that cannot be found is not a fallback.
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell"
set "VENV=%ROOT%\.venv"
set "VPY=%VENV%\Scripts\python.exe"
set "TOOLDIR=%LOCALAPPDATA%\CaseFileStudio\tools"
set "LOG=%ROOT%\install-log.txt"
set "ENVFILE=%ROOT%\.env.local"

set "WANT_LOCAL_TTS=1"
set "WANT_ALIGN_FULL=0"
set "WANT_OPTIONAL=0"
set "WANT_NODE=1"
set "FAILED="

rem Each branch is wrapped in parentheses on purpose: after a bare
rem "if cond cmd1 & cmd2", cmd2 runs whether or not the condition held.
:parseargs
if "%~1"=="" goto argsdone
if /I "%~1"=="--minimal"         ( set "WANT_LOCAL_TTS=0"  & goto nextarg )
if /I "%~1"=="--with-align-full" ( set "WANT_ALIGN_FULL=1" & goto nextarg )
if /I "%~1"=="--with-optional"   ( set "WANT_OPTIONAL=1"   & goto nextarg )
if /I "%~1"=="--no-node"         ( set "WANT_NODE=0"       & goto nextarg )
if /I "%~1"=="--all"             ( set "WANT_ALIGN_FULL=1" & set "WANT_OPTIONAL=1" & goto nextarg )
if /I "%~1"=="--help" goto usage
if /I "%~1"=="-h"     goto usage
echo Unknown option: %~1
goto usage
:nextarg
shift
goto parseargs
:argsdone

echo. > "%LOG%"
call :banner
call :log "Install started %DATE% %TIME%"
call :log "Root: %ROOT%"

if not exist "%TOOLDIR%" mkdir "%TOOLDIR%" >nul 2>&1

call :step "1/9  Checking for winget"
call :check_winget

call :step "2/9  Python 3.12"
call :ensure_python
if not defined PYCMD goto fail_python

call :step "3/9  FFmpeg with libx264 and libass"
call :ensure_ffmpeg

call :step "4/9  Node.js LTS"
if "%WANT_NODE%"=="1" (call :ensure_node) else (echo      skipped, --no-node)

call :step "5/9  Virtual environment"
call :ensure_venv
if not exist "%VPY%" goto fail_venv

call :step "6/9  Python packages"
call :install_python_packages

call :step "7/9  Voice and transcription models"
call :fetch_models

call :step "8/9  Building the web interface"
if "%WANT_NODE%"=="1" (call :build_frontend) else (echo      skipped, --no-node)

call :step "9/9  Environment check"
call :write_envfile
"%VPY%" "%ROOT%\tools\doctor.py"

echo.
echo ===========================================================================
if defined FAILED (
  echo   Finished with warnings. These parts did not install:
  for %%F in (%FAILED%) do echo     - %%F
  echo.
  echo   Everything else is ready. See install-log.txt for the full output.
) else (
  echo   All dependencies installed.
)
echo.
echo   Next:  run.bat        starts the app on http://localhost:8760
echo          doctor.bat     re-checks the environment at any time
echo ===========================================================================
echo.
rem The wrapper at the top of this file holds the window open, so no pause here.
exit /b 0


rem ---------------------------------------------------------------------------
rem  Subroutines
rem ---------------------------------------------------------------------------

:banner
echo.
echo  ==========================================================================
echo    CaseFile Studio - dependency installer
echo    Windows, CPU-only, built for hour-long narration scripts
echo  ==========================================================================
echo.
exit /b 0

:step
echo.
echo  -- %~1
call :log "STEP %~1"
rem PATH length is the thing most likely to end this script without a word, so
rem record it at every step. If the install ever dies again, install-log.txt
rem says how close it was to cmd's 8,191-character limit when it happened.
call :log_pathlen
exit /b 0

:log_pathlen
set "PLEN=0"
if defined PATH call :strlen PATH PLEN
call :log "PATH is %PLEN% characters"
if %PLEN% GTR 7500 (
  echo      WARNING: PATH is %PLEN% characters, near the 8191 limit for cmd.
  echo      Tidy it in System Properties ^> Environment Variables if this fails.
)
exit /b 0

:strlen
rem %1 = name of the variable to measure, %2 = name of the variable to set.
rem Halving search: an 8,000-character PATH is not something to count one at a
rem time inside a batch file.
setlocal EnableDelayedExpansion
rem The leading sentinel is what makes the count come out as the length of the
rem value rather than one short. It is a rough figure - a path containing "!"
rem measures a little low - but this is a warning threshold, not a gate.
set "S=#!%~1!"
set "L=0"
for %%A in (4096 2048 1024 512 256 128 64 32 16 8 4 2 1) do (
  if "!S:~%%A!" NEQ "" (
    set /a L+=%%A
    set "S=!S:~%%A!"
  )
)
endlocal & set "%~2=%L%"
exit /b 0

:log
echo %~1 >> "%LOG%"
exit /b 0

:markfail
set "FAILED=%FAILED% %~1"
call :log "FAILED %~1"
exit /b 0

rem --- refresh PATH from the registry so tools installed by winget in this
rem     same session become visible without reopening the terminal ------------
:refresh_path
rem Pick up entries an installer has just added to the persistent PATH.
rem
rem This used to read the two registry values and prepend both onto %PATH% on
rem every call. Two things were wrong with that, and together they closed the
rem window part-way through the install:
rem
rem   * %PATH% already contains those entries, so each call very nearly doubled
rem     it. Three calls - Python, FFmpeg, Node - take a typical 2,000-character
rem     PATH past cmd's 8,191-character ceiling, at which point "set" fails and
rem     the batch dies. A double-clicked window dies with it.
rem   * Path is stored as REG_EXPAND_SZ, so "reg query" hands back the literal
rem     text %SystemRoot%\system32. cmd does not expand that a second time, so
rem     System32 quietly fell off the PATH and "where", "findstr" and the rest
rem     stopped resolving from that point on.
rem
rem PowerShell returns the values already expanded, so merge, de-duplicate and
rem length-check there, and only adopt the result if it is sane.
set "PATHTMP=%TEMP%\casefile-path-%RANDOM%%RANDOM%.txt"
"%PS%" -NoProfile -ExecutionPolicy Bypass -Command "$seen=New-Object System.Collections.Generic.HashSet[string]([StringComparer]::OrdinalIgnoreCase); $keep=New-Object System.Collections.Generic.List[string]; foreach($scope in @('Machine','User')){ foreach($entry in ([Environment]::GetEnvironmentVariable('Path',$scope) -split ';')){ $t=$entry.Trim().TrimEnd('\'); if($t -and $seen.Add($t)){ $keep.Add($t) } } }; $joined=($keep -join ';'); if($joined.Length -lt 7000){ [IO.File]::WriteAllText($env:PATHTMP,$joined) }" >> "%LOG%" 2>&1

if not exist "%PATHTMP%" (
  rem Either PowerShell is locked down, or the merged PATH came back too long
  rem to be safe. Keeping the PATH we already have is always survivable: a tool
  rem installed a moment ago is simply not visible until a new window is opened,
  rem and this script is safe to re-run.
  echo      could not refresh PATH - if the next step says a tool is missing,
  echo      close this window, open a new one, and run install-deps.bat again
  call :log "refresh_path: kept the existing PATH"
  exit /b 0
)

rem Read it with delayed expansion off, or a directory containing "!" is eaten,
rem then hand the value back out across the endlocal.
setlocal DisableDelayedExpansion
set "MERGED="
for /f "usebackq delims=" %%P in ("%PATHTMP%") do set "MERGED=%%P"
del "%PATHTMP%" >nul 2>&1
if not defined MERGED (
  endlocal
  exit /b 0
)
endlocal & set "PATH=%MERGED%"
call :log "refresh_path: PATH rebuilt from the registry"
exit /b 0

:check_winget
set "HAVE_WINGET=0"
where winget >nul 2>&1 && set "HAVE_WINGET=1"
if "%HAVE_WINGET%"=="1" (
  echo      winget found, it will be used for system installs
) else (
  echo      winget not found - falling back to direct downloads
  echo      If something fails, install "App Installer" from the Microsoft Store
)
exit /b 0

rem --- Python ----------------------------------------------------------------
:ensure_python
set "PYCMD="
for %%V in (3.12 3.11 3.13) do (
  if not defined PYCMD (
    py -%%V -c "import sys" >nul 2>&1 && set "PYCMD=py -%%V"
  )
)
if not defined PYCMD (
  python -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)" >nul 2>&1 && set "PYCMD=python"
)
if defined PYCMD (
  for /f "tokens=*" %%V in ('%PYCMD% -c "import sys;print(sys.version.split()[0])" 2^>nul') do echo      found Python %%V
  exit /b 0
)

echo      not found, installing Python 3.12
if "%HAVE_WINGET%"=="1" (
  winget install --id Python.Python.3.12 --source winget --scope user --accept-package-agreements --accept-source-agreements --silent >> "%LOG%" 2>&1
) else (
  call :download "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe" "%TOOLDIR%\python-3.12.8-amd64.exe"
  if exist "%TOOLDIR%\python-3.12.8-amd64.exe" (
    echo      running the Python installer, this takes a minute
    "%TOOLDIR%\python-3.12.8-amd64.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 >> "%LOG%" 2>&1
  )
)
call :refresh_path
for %%V in (3.12 3.11 3.13) do (
  if not defined PYCMD (
    py -%%V -c "import sys" >nul 2>&1 && set "PYCMD=py -%%V"
  )
)
if not defined PYCMD (
  python -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)" >nul 2>&1 && set "PYCMD=python"
)
exit /b 0

rem --- FFmpeg ----------------------------------------------------------------
:ensure_ffmpeg
set "FFMPEG="
call :probe_ffmpeg
if defined FFMPEG goto ff_verify

echo      not found, installing FFmpeg
if "%HAVE_WINGET%"=="1" (
  winget install --id Gyan.FFmpeg --source winget --scope user --accept-package-agreements --accept-source-agreements --silent >> "%LOG%" 2>&1
  call :refresh_path
  call :probe_ffmpeg
)
if not defined FFMPEG call :ffmpeg_portable
if not defined FFMPEG (
  echo      COULD NOT INSTALL FFMPEG - the app cannot render video without it
  call :markfail ffmpeg
  exit /b 0
)

:ff_verify
echo      using %FFMPEG%
set "FF_OK=1"
"%FFMPEG%" -hide_banner -buildconf 2>&1 | findstr /C:"--enable-libx264" >nul || set "FF_OK=0"
"%FFMPEG%" -hide_banner -buildconf 2>&1 | findstr /C:"--enable-libass" >nul || set "FF_OK=0"
if "%FF_OK%"=="1" (
  echo      libx264 and libass present
) else (
  echo      this FFmpeg build is missing libx264 or libass - fetching a full build
  call :ffmpeg_portable
  if defined FFMPEG (
    "%FFMPEG%" -hide_banner -buildconf 2>&1 | findstr /C:"--enable-libass" >nul && echo      libass present in the portable build || call :markfail ffmpeg-libass
  )
)
exit /b 0

:probe_ffmpeg
if exist "%TOOLDIR%\ffmpeg\bin\ffmpeg.exe" (
  set "FFMPEG=%TOOLDIR%\ffmpeg\bin\ffmpeg.exe"
  exit /b 0
)
for /f "delims=" %%P in ('where ffmpeg 2^>nul') do (
  if not defined FFMPEG set "FFMPEG=%%P"
)
exit /b 0

:ffmpeg_portable
rem BtbN GPL builds always carry libx264 and libass, and the URL is stable.
set "FFZIP=%TOOLDIR%\ffmpeg.zip"
set "FFEXTRACT=%TOOLDIR%\ffmpeg-extract"
call :download "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip" "%FFZIP%"
if not exist "%FFZIP%" exit /b 0
if exist "%FFEXTRACT%" rmdir /s /q "%FFEXTRACT%" >nul 2>&1
mkdir "%FFEXTRACT%" >nul 2>&1
echo      extracting FFmpeg
tar -xf "%FFZIP%" -C "%FFEXTRACT%" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo      extract failed - unzip "%FFZIP%" by hand into "%TOOLDIR%\ffmpeg"
  call :markfail ffmpeg-extract
  exit /b 0
)
if exist "%TOOLDIR%\ffmpeg" rmdir /s /q "%TOOLDIR%\ffmpeg" >nul 2>&1
for /d %%D in ("%FFEXTRACT%\*") do move "%%D" "%TOOLDIR%\ffmpeg" >nul 2>&1
if exist "%TOOLDIR%\ffmpeg\bin\ffmpeg.exe" (
  set "FFMPEG=%TOOLDIR%\ffmpeg\bin\ffmpeg.exe"
  del "%FFZIP%" >nul 2>&1
  rmdir /s /q "%FFEXTRACT%" >nul 2>&1
)
exit /b 0

rem --- Node ------------------------------------------------------------------
:ensure_node
where node >nul 2>&1
if not errorlevel 1 (
  for /f "tokens=*" %%V in ('node --version 2^>nul') do echo      found Node %%V
  exit /b 0
)
echo      not found, installing Node.js LTS
if "%HAVE_WINGET%"=="1" (
  winget install --id OpenJS.NodeJS.LTS --source winget --scope user --accept-package-agreements --accept-source-agreements --silent >> "%LOG%" 2>&1
  call :refresh_path
)
where node >nul 2>&1
if errorlevel 1 (
  echo      Node.js did not install - only the frontend dev server needs it
  echo      Get it from https://nodejs.org and re-run this script
  call :markfail nodejs
)
exit /b 0

rem --- venv ------------------------------------------------------------------
:ensure_venv
if exist "%VPY%" (
  echo      reusing %VENV%
  exit /b 0
)
echo      creating %VENV%
%PYCMD% -m venv "%VENV%" >> "%LOG%" 2>&1
if not exist "%VPY%" (
  echo      venv creation failed - see install-log.txt
  call :markfail venv
)
exit /b 0

rem --- Python packages -------------------------------------------------------
:install_python_packages
"%VPY%" -m pip install --upgrade pip setuptools wheel >> "%LOG%" 2>&1

call :pipreq core.txt "core backend" 1
if "%WANT_LOCAL_TTS%"=="1" call :pipreq tts-local.txt "local Kokoro voice" 0
if "%WANT_LOCAL_TTS%"=="1" call :pipreq vision.txt "face detection and blurring" 0
call :pipreq align-lite.txt "subtitle timing" 0
if "%WANT_ALIGN_FULL%"=="1" call :pipreq align-full.txt "high-accuracy alignment, large download" 0
if "%WANT_OPTIONAL%"=="1"   call :pipreq optional.txt "optional provider SDKs" 0
exit /b 0

:pipreq
rem %1 = filename, %2 = description, %3 = 1 if required
echo      installing %~2
"%VPY%" -m pip install -r "%ROOT%\requirements\%~1" >> "%LOG%" 2>&1
if errorlevel 1 (
  if "%~3"=="1" (
    echo      REQUIRED install failed: %~1 - see install-log.txt
    call :markfail %~1
  ) else (
    echo      optional install had problems: %~1 - retrying package by package
    call :pip_one_by_one "%ROOT%\requirements\%~1"
  )
) else (
  echo      done
)
exit /b 0

:pip_one_by_one
rem A single unavailable wheel should not sink a whole tier.
rem The first character is tested directly rather than piping the line into
rem findstr: requirement lines contain ">=", which cmd would read as a redirect.
for /f "usebackq eol=# tokens=* delims=" %%L in ("%~1") do (
  set "PKG=%%L"
  if defined PKG (
    if not "!PKG:~0,1!"=="-" (
      "%VPY%" -m pip install "!PKG!" >> "%LOG%" 2>&1
      if errorlevel 1 (
        echo        skipped !PKG!
        call :log "SKIPPED PACKAGE !PKG!"
      )
    )
    set "PKG="
  )
)
exit /b 0

rem --- models ----------------------------------------------------------------
:fetch_models
if "%WANT_LOCAL_TTS%"=="0" (
  echo      skipped, --minimal
  exit /b 0
)
"%VPY%" "%ROOT%\tools\fetch_models.py"
if errorlevel 1 (
  echo      model download incomplete - run doctor.bat later to retry
  call :markfail models
)
exit /b 0

rem --- frontend --------------------------------------------------------------
:build_frontend
where npm >nul 2>&1
if errorlevel 1 (
  echo      npm not found - the app will run without its web interface
  call :markfail frontend
  exit /b 0
)
if not exist "%ROOT%\frontend\package.json" (
  echo      no frontend sources, skipping
  exit /b 0
)
echo      installing web dependencies, this takes a few minutes
pushd "%ROOT%\frontend"
call npm install --no-audit --no-fund >> "%LOG%" 2>&1
if errorlevel 1 (
  echo      npm install failed - see install-log.txt
  call :markfail frontend-deps
  popd
  exit /b 0
)
echo      building the interface
call npm run build >> "%LOG%" 2>&1
if errorlevel 1 (
  echo      frontend build failed - see install-log.txt
  call :markfail frontend-build
) else (
  echo      done
)
popd
exit /b 0

rem --- helpers ---------------------------------------------------------------
:download
rem %1 = url, %2 = destination
if exist "%~2" exit /b 0
echo      downloading %~nx2
where curl >nul 2>&1
if not errorlevel 1 (
  curl -L --fail --retry 3 --retry-delay 2 -o "%~2" "%~1" >> "%LOG%" 2>&1
) else (
  "%PS%" -NoProfile -ExecutionPolicy Bypass -Command "try{[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;Invoke-WebRequest -Uri '%~1' -OutFile '%~2' -UseBasicParsing}catch{exit 1}" >> "%LOG%" 2>&1
)
if not exist "%~2" (
  echo      download failed: %~1
  call :log "DOWNLOAD FAILED %~1"
)
exit /b 0

:write_envfile
rem Recorded here rather than pushed into the system PATH, which is easy to
rem corrupt with setx. run.bat reads this file.
> "%ENVFILE%" echo # Written by install-deps.bat. Safe to edit.
if defined FFMPEG (
  for %%F in ("%FFMPEG%") do >> "%ENVFILE%" echo CASEFILE_FFMPEG_BIN=%%~dpF
)
>> "%ENVFILE%" echo CASEFILE_VENV=%VENV%
exit /b 0

:fail_python
echo.
echo  Python 3.12 could not be installed automatically.
echo  Install it from https://www.python.org/downloads/ and tick
echo  "Add python.exe to PATH", then re-run this script.
echo.
exit /b 1

:fail_venv
echo.
echo  The virtual environment could not be created. See install-log.txt.
echo.
exit /b 1

:usage
echo.
echo  install-deps.bat [options]
echo.
echo    --minimal            core backend only, no local TTS or models
echo    --with-align-full    add CPU torch + WhisperX for tighter caption timing
echo    --with-optional      add Fish/Anthropic/OpenAI/Piper SDKs
echo    --no-node            skip Node.js
echo    --all                --with-align-full plus --with-optional
echo    --help               this message
echo.
exit /b 0
