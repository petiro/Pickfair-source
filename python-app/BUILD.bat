@echo off
echo ================================================
echo   PICKFAIR - Build Windows Executable
echo ================================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERRORE: Python non trovato!
    echo Installa Python da https://www.python.org/downloads/
    pause
    exit /b 1
)

REM Install dependencies
echo Installazione dipendenze...
pip install betfairlightweight pyinstaller --quiet

REM Build
echo.
echo Creazione eseguibile...
python build.py

echo.
echo ================================================
echo   BUILD COMPLETATO!
echo ================================================
echo.
echo Trovi l'eseguibile in: dist\Pickfair.exe
echo.
pause
