@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto try_py
python -m dance_results --library-only --open-report
goto finished
:try_py
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto missing_python
py -3 -m dance_results --library-only --open-report
goto finished
:missing_python
echo Python 3.10+ is required. See README.md.
:finished
echo.
pause
