@echo off
REM Build script for Pickfair - Windows Executable
REM Requires Python 3.10+ and PyInstaller
REM v3.70.6 - Includes SSL DLLs for fast encryption

echo ============================================
echo   Pickfair Build Script v3.70.6
echo   (with OpenSSL support)
echo ============================================

cd /d "%~dp0"

REM Check Python
python --version
if %errorlevel% neq 0 (
    echo [ERROR] Python non trovato. Installa Python 3.10+
    pause
    exit /b 1
)

REM Install/Update dependencies
echo.
echo [1/4] Installazione dipendenze...
pip install --upgrade pyinstaller
pip install customtkinter numpy betfairlightweight telethon cryptography

REM Plugin libraries (pre-installed for plugins to use)
pip install pandas matplotlib

echo.
echo [2/4] Pulizia cartelle precedenti...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist __pycache__ rmdir /s /q __pycache__

echo.
echo [3/4] Compilazione eseguibile con SSL...
echo       (Cerca automaticamente libssl-3.dll e libcrypto-3.dll)
echo.
pyinstaller Pickfair.spec --noconfirm

if %errorlevel% neq 0 (
    echo [ERROR] Build fallita!
    pause
    exit /b 1
)

echo.
echo [4/4] Copia file aggiuntivi...
if not exist "dist\plugins" mkdir "dist\plugins"
if exist "plugins\plugin_template.py" copy "plugins\plugin_template.py" "dist\plugins\"
if exist "plugins\example_odds_alert.py" copy "plugins\example_odds_alert.py" "dist\plugins\"

echo.
echo ============================================
echo   Build completata!
echo   Eseguibile: dist\Pickfair.exe
echo ============================================
echo.
echo Se vedi "[SSL] Found:" durante la build,
echo le librerie SSL sono state incluse.
echo.
pause
