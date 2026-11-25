@echo off
rem Lance WiFi Audit Pro depuis les sources (sans fen?tre de terminal persistante).
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Premiere utilisation : installation des dependances...
    python -m venv .venv || goto :err
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :err
)
start "" ".venv\Scripts\pythonw.exe" main.py %*
exit /b 0
:err
echo Echec de l'installation. Verifiez que Python 3.12+ est installe.
pause

