@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM   FALCON PDB - Actualizar
REM   Baja la ultima version desde GitHub, actualiza las
REM   dependencias y deja todo listo para iniciar.bat.
REM   No toca tu base de datos (falcon_pdb.db).
REM ============================================================
cd /d "%~dp0"
chcp 65001 >nul 2>&1
title Falcon PDB - Actualizar

if not exist .git (
    echo [ERROR] Esta carpeta no es un clon de git. Clona de nuevo el repositorio.
    pause
    exit /b 1
)

echo Cerrando el servidor si esta abierto...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8001 .*LISTENING"') do taskkill /PID %%P /F >nul 2>&1

echo.
echo Bajando la ultima version...
git fetch origin
if errorlevel 1 goto :FALLO
git checkout claude/relaxed-bardeen-1frf9o
if errorlevel 1 goto :FALLO
git pull --ff-only
if errorlevel 1 goto :FALLO

echo.
echo Actualizando dependencias...
call instalar.bat --no-pause
if errorlevel 1 goto :FALLO

echo.
git log --oneline -1
echo.
echo [OK] Listo. Ahora ejecuta iniciar.bat
pause
exit /b 0

:FALLO
echo.
echo [ERROR] No se pudo actualizar. Copia los mensajes de arriba y enviamelos.
pause
exit /b 1
