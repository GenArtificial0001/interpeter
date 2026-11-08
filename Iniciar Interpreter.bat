@echo off
rem VERSIONES: app 0.9.0 | extension del navegador 0.3.3  (actualizar junto con __init__.py y manifest.json)
rem Inicia Interpreter sin ventana de consola. Los registros quedan en logs\interpreter.log
rem Usa .venv-clone (tiene los modelos de voz clonada) y, si no existe, .venv.
setlocal
cd /d "%~dp0"
set "ENVDIR=.venv-clone"
if not exist "%ENVDIR%\Scripts\pythonw.exe" set "ENVDIR=.venv"
if not exist "%ENVDIR%\Scripts\pythonw.exe" (
  echo No se encontro ningun entorno ^(.venv-clone o .venv^) en esta carpeta.
  echo Crealo con:
  echo   python -m venv .venv
  echo   .venv\Scripts\python -m pip install -e ".[ml,ui,ov,dev]"
  pause
  exit /b 1
)
set PYTHONUTF8=1
start "" "%ENVDIR%\Scripts\pythonw.exe" -m interpreter %*
endlocal
