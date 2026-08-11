@echo off
if not exist .venv\Scripts\python.exe (
  echo Run setup_windows.bat first.
  exit /b 1
)
.venv\Scripts\python.exe -m src.cli.main --db-path data\Chinook_VN.sqlite
