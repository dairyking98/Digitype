@echo off
rem Type Seed labelling tool. Double-click, or: run.bat "E:\path\to\samples" [more files or folders]
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Setting up Python environment, first run only...
  python -m venv .venv || py -3 -m venv .venv
  .venv\Scripts\python -m pip install -q -r requirements.txt
)
.venv\Scripts\python -m typeseed label %*
pause
