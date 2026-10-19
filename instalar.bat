@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM   FALCON PDB - Instalacion
REM   Crea el entorno virtual (.venv), instala las dependencias y
REM   deja lista la base de datos (usuario admin / admin123).
REM   Uso:  instalar.bat            (con pausa al final)
REM         instalar.bat --no-pause (cuando lo llama iniciar.bat)
REM ============================================================
set "NOPAUSE=0"
if /I "%~1"=="--no-pause" set "NOPAUSE=1"

cd /d "%~dp0"
chcp 65001 >nul 2>&1
title Falcon PDB - Instalacion

echo.
echo ============================================================
echo   FALCON PDB - Instalacion
echo ============================================================
echo.

REM --- Buscar Python 3.11 o superior ---
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo [ERROR] No se encontro Python 3.11 o superior.
    echo         Instalalo desde https://www.python.org/downloads/ marcando
    echo         "Add python.exe to PATH" y vuelve a ejecutar este archivo.
    goto :FALLO
)
echo Python encontrado: %PY%

REM --- Recrear el entorno virtual si no funciona ---
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe -c "import sys" >nul 2>&1
    if errorlevel 1 (
        echo El entorno virtual esta roto. Se recrea...
        rmdir /s /q .venv
    )
)
if not exist .venv\Scripts\python.exe (
    echo Creando entorno virtual...
    %PY% -m venv .venv
    if errorlevel 1 goto :FALLO
)

echo Instalando dependencias ^(puede tardar unos minutos^)...
.venv\Scripts\python.exe -m pip install --upgrade pip >nul 2>&1
.venv\Scripts\python.exe -m pip install --prefer-binary -r requirements.txt
if errorlevel 1 goto :FALLO

echo Preparando la base de datos...
.venv\Scripts\python.exe seed.py
if errorlevel 1 goto :FALLO

echo.
echo [OK] Instalacion completa. Ejecuta iniciar.bat para abrir Falcon PDB.
echo.
if "%NOPAUSE%"=="0" pause
exit /b 0

:FALLO
echo.
echo [ERROR] La instalacion no se completo. Revisa los mensajes de arriba.
echo.
if "%NOPAUSE%"=="0" pause
exit /b 1
