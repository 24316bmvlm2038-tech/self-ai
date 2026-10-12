@echo off
rem One-click launcher for Windows: sets up a virtual environment, installs dependencies and starts Self-AI.
setlocal
set "SELFAI_KEY_INPUT="
cd /d "%~dp0"

if not exist .venv (
  echo Creating virtual environment...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 (
    echo Python 3.10+ is required. Install it from https://www.python.org/downloads/
    pause
    exit /b 1
  )
)
call .venv\Scripts\activate.bat

echo Checking dependencies...
python -m pip install --quiet -r requirements.txt

if not exist .env (
  copy .env.example .env >nul
  echo.
  echo Get a DeepSeek API key at https://platform.deepseek.com/api_keys
  set /p "SELFAI_KEY_INPUT=Paste your DeepSeek API key (or press Enter to add it to .env later): "
)
if defined SELFAI_KEY_INPUT (
  >>.env echo.
  >>.env echo DEEPSEEK_API_KEY=%SELFAI_KEY_INPUT%
)

python -m selfai %*
pause
