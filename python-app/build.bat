@echo off
REM Build script for Pickfair - Windows Executable
REM Requires Python 3.8+ and PyInstaller
REM v3.70.10 - OpenSSL 1.1.1 support for Windows 7

echo ============================================
echo   Pickfair Build Script v3.70.10
echo   (with OpenSSL 1.1.1 for Windows 7)
echo ============================================

cd /d "%~dp0"

REM Check Python
python --version
if %errorlevel% neq 0 (
    echo [ERROR] Python non trovato. Installa Python 3.8+
    pause
    exit /b 1
)

REM Add OpenSSL to PATH (needed for cryptography during build)
echo.
echo [0/4] Configurazione OpenSSL...
set "OPENSSL_FOUND=0"

REM Check common OpenSSL installation paths
if exist "C:\Programmi\OpenSSL-Win64" (
    set "PATH=C:\Programmi\OpenSSL-Win64;%PATH%"
    set "OPENSSL_FOUND=1"
    echo       Trovato: C:\Programmi\OpenSSL-Win64
)
if exist "C:\Program Files\OpenSSL-Win64" (
    set "PATH=C:\Program Files\OpenSSL-Win64;%PATH%"
    set "OPENSSL_FOUND=1"
    echo       Trovato: C:\Program Files\OpenSSL-Win64
)
if exist "C:\OpenSSL-Win64" (
    set "PATH=C:\OpenSSL-Win64;%PATH%"
    set "OPENSSL_FOUND=1"
    echo       Trovato: C:\OpenSSL-Win64
)
if exist "C:\OpenSSL-Win64\bin" (
    set "PATH=C:\OpenSSL-Win64\bin;%PATH%"
)
if exist "C:\Programmi\OpenSSL-Win64\bin" (
    set "PATH=C:\Programmi\OpenSSL-Win64\bin;%PATH%"
)
if exist "C:\Program Files\OpenSSL-Win64\bin" (
    set "PATH=C:\Program Files\OpenSSL-Win64\bin;%PATH%"
)

REM Also check for DLLs in current directory
if exist "libssl-1_1-x64.dll" (
    set "PATH=%CD%;%PATH%"
    set "OPENSSL_FOUND=1"
    echo       Trovato: DLL nella cartella corrente
)
if exist "third_party\libssl-1_1-x64.dll" (
    set "PATH=%CD%\third_party;%PATH%"
    set "OPENSSL_FOUND=1"
    echo       Trovato: DLL in third_party
)

if "%OPENSSL_FOUND%"=="0" (
    echo [AVVISO] OpenSSL 1.1.1 non trovato.
    echo          Scarica da: https://slproweb.com/download/Win64OpenSSL_Light-1_1_1w.exe
    echo          Oppure copia libssl-1_1-x64.dll e libcrypto-1_1-x64.dll qui.
    echo.
)

REM Install/Update dependencies
echo.
echo [1/4] Installazione dipendenze...
pip install --upgrade pyinstaller
pip install customtkinter numpy betfairlightweight telethon
pip install cryptography==41.0.7

REM Plugin libraries (pre-installed for plugins to use)
pip install pandas matplotlib

echo.
echo [2/4] Pulizia cartelle precedenti...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist __pycache__ rmdir /s /q __pycache__

echo.
echo [3/4] Compilazione eseguibile con SSL...
echo       (Cerca OpenSSL 1.1.1 per compatibilita Windows 7)
echo.
pyinstaller Pickfair.spec --noconfirm

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Build fallita!
    echo.
    echo SOLUZIONE: Copia questi file nella cartella corrente:
    echo   - libssl-1_1-x64.dll
    echo   - libcrypto-1_1-x64.dll
    echo.
    echo Li trovi in: C:\Programmi\OpenSSL-Win64\
    echo.
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
echo Se vedi "[SSL] Found 1.1.1:" durante la build,
echo le librerie SSL sono state incluse correttamente.
echo.
pause
