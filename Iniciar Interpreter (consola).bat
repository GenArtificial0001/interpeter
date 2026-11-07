@echo off
rem Inicia Interpreter CON consola, para ver errores y mensajes de diagnostico.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo No se encontro el entorno .venv en esta carpeta.
  pause
  exit /b 1
)
set PYTHONUTF8=1
".venv\Scripts\python.exe" -m interpreter
echo.
echo Interpreter se cerro (codigo %errorlevel%). Registro: logs\interpreter.log
pause
endlocal
