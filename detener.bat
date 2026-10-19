@echo off
REM Detiene el servidor de Falcon PDB (el que escucha en el puerto 8003).
set "ENCONTRADO=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8003 .*LISTENING"') do (
    taskkill /PID %%P /F >nul 2>&1
    set "ENCONTRADO=1"
)
echo Servidor de Falcon PDB detenido ^(si estaba en ejecucion^).
pause
