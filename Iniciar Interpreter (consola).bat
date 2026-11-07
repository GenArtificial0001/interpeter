@echo off
rem Inicia Interpreter CON consola, para ver errores y mensajes de diagnostico.
setlocal
cd /d "%~dp0"
set "ENVDIR=.venv-clone"
if not exist "%ENVDIR%\Scripts\python.exe" set "ENVDIR=.venv"
if not exist "%ENVDIR%\Scripts\python.exe" (
  echo No se encontro ningun entorno ^(.venv-clone o .venv^) en esta carpeta.
  pause
  exit /b 1
)
echo Usando el entorno: %ENVDIR%
set PYTHONUTF8=1
"%ENVDIR%\Scripts\python.exe" -m interpreter
echo.
echo Interpreter se cerro ^(codigo %errorlevel%^). Registro: logs\interpreter.log
pause
endlocal
