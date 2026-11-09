@echo off
rem VERSIONES: instalador de prueba de la app 0.10.2 (misma ventana que el .exe, pero sin compilarlo)
rem Abre el instalador de Interpreter para PROBARLO desde esta carpeta, sin generar el .exe.
rem Empaqueta el codigo actual y abre la ventana. Se ve esta consola por si hay errores.
rem
rem IMPORTANTE: en la ventana elige una carpeta de instalacion DISTINTA de esta (por defecto:
rem %USERPROFILE%\Interpreter) o una de prueba como C:\Temp\InterpreterPrueba.
setlocal
cd /d "%~dp0"
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (
  where py >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
  where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo No se encontro Python. Instala Python 3.11 a 3.13 o crea el entorno .venv de esta carpeta.
  pause
  exit /b 1
)
echo Interpreter - instalador de prueba 0.10.2
echo Usando: %PY%
set PYTHONUTF8=1
set "PYTHONPATH=%~dp0src"
%PY% scripts\build_installer.py --solo-payload
if errorlevel 1 (
  echo No se pudo empaquetar la aplicacion.
  pause
  exit /b 1
)
%PY% -m interpreter_setup
echo.
echo El instalador se cerro ^(codigo %errorlevel%^).
pause
endlocal
