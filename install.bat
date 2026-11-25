@echo off
setlocal
set "TARGET=%USERPROFILE%\.gemini\config\plugins\agy-video-reader"
set "SRC=%~dp0"
if "%SRC:~-1%"=="\" set "SRC=%SRC:~0,-1%"

if /I NOT "%SRC%"=="%TARGET%" (
  echo [agy-video-reader] Installing plugin to "%TARGET%"...
  if not exist "%TARGET%" mkdir "%TARGET%"
  robocopy "%SRC%" "%TARGET%" /E /XD .git .venv cache __pycache__ /XF cookies.txt settings.json >nul
)

echo [agy-video-reader] Syncing isolated uv environment...
cd /d "%TARGET%"
uv sync
if errorlevel 1 (
  echo [agy-video-reader] uv sync failed. Please check Python 3.12+ and uv installation.
  exit /b 1
)

echo [agy-video-reader] Installed successfully! Open Antigravity Customizations to manage it.
endlocal
