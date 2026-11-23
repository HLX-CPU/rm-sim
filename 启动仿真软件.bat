@echo off
rem Launch the rm_sim GUI. Python resolution order:
rem   1) .venv\Scripts\python.exe   2) venv\Scripts\python.exe
rem   3) RM_SIM_PYTHON environment variable   4) python on PATH
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
if "%~1"=="" set "QT_QPA_PLATFORM="

set "PY="
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "%~dp0venv\Scripts\python.exe" set "PY=%~dp0venv\Scripts\python.exe"
if not defined PY if defined RM_SIM_PYTHON set "PY=%RM_SIM_PYTHON%"
if not defined PY set "PY=python"

"%PY%" -B "%~dp0main.py" %*
set "result=%errorlevel%"
if not "%result%"=="0" pause
exit /b %result%
