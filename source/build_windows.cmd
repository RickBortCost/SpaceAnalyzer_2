@echo off
setlocal
cd /d "%~dp0"
py -3 build_windows.py
if errorlevel 1 (
  echo Build non riuscita. Verificare Python, pip e Go sul PC di compilazione.
  pause
  exit /b 1
)
echo EXE creato nella cartella dist.
pause
