@echo off
REM Doppelklick -> richtet WorkTracker->clockodo ein. Nichts von Hand noetig.
cd /d "%~dp0"

echo Richte WorkTracker -^> clockodo ein ...
echo.

REM Python finden (py-Launcher bevorzugt, sonst python)
where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")
%PY% --version >nul 2>&1
if errorlevel 1 (
  echo Python 3 fehlt. Bitte einmalig von https://www.python.org/downloads/ installieren
  echo   ^(beim Setup "Add Python to PATH" anhaken^), dann erneut doppelklicken.
  echo.
  pause
  exit /b 1
)

REM Virtuelle Umgebung + Abhaengigkeiten (nur beim ersten Mal spuerbar)
if not exist ".venv" ( %PY% -m venv .venv )
call ".venv\Scripts\python.exe" -m pip install -q --upgrade pip >nul 2>&1
call ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt

REM Der eigentliche, gefuehrte Einrichter
call ".venv\Scripts\python.exe" clockodo_setup.py

echo.
pause
