@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv-ocr\Scripts\python.exe" goto missing
".venv-ocr\Scripts\python.exe" -X utf8 -m dance_results.gui %*
exit /b %errorlevel%
:missing
echo See docs\PHASE4B_MONITOR.md for setup.
pause
exit /b 2
