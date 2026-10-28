@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  start "YesMachine" ".venv\Scripts\python.exe" -m yesmachine.app
) else (
  py -3 -m yesmachine.app
)
