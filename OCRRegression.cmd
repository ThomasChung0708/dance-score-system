@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv-ocr\Scripts\python.exe" goto missing
".venv-ocr\Scripts\python.exe" -X utf8 -m tools.ocr_regression %*
exit /b %errorlevel%
:missing
echo OCR environment missing. See docs\PHASE4A_OCR.md for setup.
exit /b 2
