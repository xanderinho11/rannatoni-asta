@echo off
setlocal
cd /d "%~dp0"

REM Password locale del pannello Super Admin. Prima di pubblicare online cambiala.
if "%ADMIN_PASSWORD%"=="" set ADMIN_PASSWORD=asta2026

if not exist "venv\Scripts\activate.bat" (
    echo Primo avvio: preparo l'ambiente Python...
    python -m venv venv
    call venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)

echo.
echo ============================================================
echo   RANNATONI - ASTA DI RIPARAZIONE - SERVER LOCALE
echo.
echo   Sul PC:
echo     http://localhost:8000
echo     http://localhost:8000/admin

echo   Password Super Admin locale: %ADMIN_PASSWORD%
echo.
echo   Per i telefoni usa l'indirizzo IPv4 del PC seguito da :8000
echo   (PC e telefoni devono essere sulla stessa rete Wi-Fi).
echo.
echo   Indirizzi IPv4 rilevati:
powershell -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 ^| Where-Object {$_.IPAddress -notlike '127.*' -and $_.PrefixOrigin -ne 'WellKnown'} ^| ForEach-Object { Write-Host ('     http://' + $_.IPAddress + ':8000') }" 2>nul

echo.
echo   Lascia questa finestra aperta. Per fermare: Ctrl+C.
echo   I dati NON vengono azzerati al riavvio.
echo ============================================================
echo.

python -m uvicorn main:app --host 0.0.0.0 --port 8000
pause
