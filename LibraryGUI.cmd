@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv-ocr\Scripts\python.exe" goto with_ocr
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto try_py
python -m dance_results.gui
goto finished
:try_py
py -3 -m dance_results.gui
goto finished
:with_ocr
".venv-ocr\Scripts\python.exe" -X utf8 -m dance_results.gui
goto finished
:finished
if errorlevel 1 pause
