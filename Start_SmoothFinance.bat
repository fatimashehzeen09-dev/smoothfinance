@echo off
setlocal
cd /d "%~dp0"

py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 (
    py -3 app.py --open-browser
    goto finished
)

python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 (
    python app.py --open-browser
    goto finished
)

echo Python 3.10 or newer is required.
echo Install Python from https://www.python.org/downloads/windows/
echo Then double-click this launcher again.
pause
exit /b 1

:finished
if errorlevel 1 pause
