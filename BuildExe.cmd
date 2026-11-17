@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
rem Builds dist\DanceScore.exe: game folder mode only, no OCR packages, no Python needed to run it.
if not exist ".venv-build\Scripts\python.exe" (
  py -3.11 -m venv .venv-build || goto failed
)
".venv-build\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements-build.txt || goto failed
".venv-build\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --name DanceScore ^
  --distpath dist --workpath build --specpath build ^
  --paths "%~dp0." ^
  --add-data "%~dp0dance_results\gui_assets;dance_results/gui_assets" ^
  --exclude-module numpy --exclude-module cv2 --exclude-module PIL --exclude-module onnxruntime ^
  --exclude-module rapidocr --exclude-module windows_capture --exclude-module psutil --exclude-module tkinter ^
  packaging\DanceScore.py || goto failed
echo.
echo Built dist\DanceScore.exe
exit /b 0
:failed
echo Build failed.
exit /b 1
