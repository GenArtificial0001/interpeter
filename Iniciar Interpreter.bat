@echo off
rem Inicia Interpreter sin ventana de consola. Los registros quedan en logs\interpreter.log
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo No se encontro el entorno .venv en esta carpeta.
  echo Crealo con:
  echo   python -m venv .venv
  echo   .venv\Scripts\python -m pip install -e ".[ml,ui,ov,dev]"
  pause
  exit /b 1
)
set PYTHONUTF8=1
start "" ".venv\Scripts\pythonw.exe" -m interpreter
endlocal
