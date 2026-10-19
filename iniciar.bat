@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM   FALCON PDB - Iniciar
REM   Doble click: arranca el servidor en http://localhost:8001 y
REM   abre el navegador. Para detener: cierra esta ventana o Ctrl+C.
REM   (usa el puerto 8001 para no chocar con Falcon Contable, 8000)
REM ============================================================
cd /d "%~dp0"
chcp 65001 >nul 2>&1
title Falcon PDB - Servidor

REM --- Verificar que el entorno funciona; si no, repararlo ---
set "VENV_OK=1"
if not exist .venv\Scripts\python.exe set "VENV_OK=0"
if "!VENV_OK!"=="1" (
    .venv\Scripts\python.exe -c "import uvicorn, pdfplumber, openpyxl" >nul 2>&1
    if errorlevel 1 set "VENV_OK=0"
)
if "!VENV_OK!"=="0" (
    echo El entorno no esta listo. Instalando...
    call instalar.bat --no-pause
    if errorlevel 1 (
        pause
        exit /b 1
    )
)

REM --- Bajar actualizaciones si la carpeta es un clon de git ---
if exist .git (
    echo Buscando actualizaciones...
    git pull --ff-only
)

echo.
echo ============================================================
echo   Falcon PDB en http://localhost:8001   ^(admin / admin123^)
echo ============================================================
echo.
start "" /b cmd /c "timeout /t 3 >nul & start http://localhost:8001"
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
pause
