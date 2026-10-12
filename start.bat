@echo off
rem One-click launcher for Windows: sets up a virtual environment, installs dependencies and starts Self-AI.
setlocal
set "SELFAI_KEY_INPUT="
set "SELFAI_FIRST_RUN="
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
  set "SELFAI_FIRST_RUN=1"
  echo.
  echo Self-AI can run a free AI model on this computer, or use the DeepSeek API - paid, faster.
  set /p "SELFAI_KEY_INPUT=Paste a DeepSeek API key, or just press Enter to use a free local model: "
)
rem Variables set inside the block above are only visible from here on.
if defined SELFAI_FIRST_RUN (
  >>.env echo.
  if defined SELFAI_KEY_INPUT (
    >>.env echo SELFAI_PROVIDER=deepseek
    >>.env echo DEEPSEEK_API_KEY=%SELFAI_KEY_INPUT%
  ) else (
    >>.env echo SELFAI_PROVIDER=ollama
    where ollama >nul 2>nul || echo Install Ollama from https://ollama.com/download to run free local models.
  )
)

python -m selfai %*
pause
