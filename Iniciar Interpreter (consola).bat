@echo off
rem VERSIONES: app 0.5.1 | extension del navegador 0.3.3  (actualizar junto con __init__.py y manifest.json)
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
echo Interpreter 0.5.1 ^| extension del navegador 0.3.3 ^(recargala en edge://extensions si no coincide^)
set PYTHONUTF8=1
"%ENVDIR%\Scripts\python.exe" -m interpreter %*
echo.
echo Interpreter se cerro ^(codigo %errorlevel%^). Registro: logs\interpreter.log
pause
endlocal
