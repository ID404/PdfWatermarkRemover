@echo off
REM PDF Watermark Remover - build single-file exe
REM Output: dist\PDFWatermarkRemover.exe
REM Requires: python -m pip install -r requirements.txt

python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
  echo PyInstaller not found. Installing...
  python -m pip install pyinstaller
  if errorlevel 1 goto fail
)

python -m PyInstaller --noconfirm --clean --onefile --windowed --name "PDFWatermarkRemover" --icon "app.ico" --distpath "dist" --workpath "build" --specpath "." --exclude-module numpy --exclude-module matplotlib --exclude-module PIL app.py

if errorlevel 1 goto fail
echo.
echo BUILD OK: dist\PDFWatermarkRemover.exe
pause
exit /b 0

:fail
echo.
echo BUILD FAILED - see output above.
pause
exit /b 1
