@echo off
setlocal
cd /d "%~dp0"

where pythonw >nul 2>nul
if errorlevel 1 (
    echo Python wurde nicht gefunden. Bitte Python installieren und zum PATH hinzufuegen.
    pause
    exit /b 1
)

rem Ohne Konsolenfenster starten; "start.bat debug" zeigt die Konsole (Fehlersuche).
if /i "%~1"=="debug" (
    python debugV4.py
    pause
) else (
    start "" pythonw debugV4.py
)
