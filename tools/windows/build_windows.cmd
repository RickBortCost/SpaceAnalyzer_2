@echo off
setlocal
cd /d "%~dp0"
py -3 build_windows.py
if errorlevel 1 (
  echo Build non riuscita. Verificare .NET 8 SDK, Go e Python sul PC di compilazione.
  pause
  exit /b 1
)
echo EXE creato nella cartella source\dist.
pause
