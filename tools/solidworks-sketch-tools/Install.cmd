@echo off
setlocal
set "SETUP_POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if exist "%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe" set "SETUP_POWERSHELL=%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
"%SETUP_POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup.ps1"
set "SETUP_RESULT=%ERRORLEVEL%"
if not "%SETUP_RESULT%"=="0" echo Installation failed. Read Setup.log in this folder.
pause
exit /b %SETUP_RESULT%
