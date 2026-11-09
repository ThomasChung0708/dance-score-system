@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv-ocr\Scripts\python.exe" goto missing
".venv-ocr\Scripts\python.exe" -X utf8 -m dance_results.ocr_static --labels "tests\fixtures\phase4a_ground_truth.json" %*
exit /b %errorlevel%
:missing
echo OCR environment missing. See docs\PHASE4A_OCR.md for setup.
exit /b 2
