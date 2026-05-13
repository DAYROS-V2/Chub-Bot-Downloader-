@echo off
setlocal
title Chub Down Release Installer
color 0A
cd /d "%~dp0"

echo.
echo  ================================================
echo    Chub Down Release Installer
echo  ================================================
echo.

where py >nul 2>&1
if errorlevel 1 (
    echo   Python launcher was not found.
    echo   Install Python 3.10 or newer from:
    echo   https://www.python.org/downloads/windows/
    echo.
    echo   During install, check "Add python.exe to PATH".
    pause
    exit /b 1
)

py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo   Python 3.10 or newer is required.
    py -3 --version
    pause
    exit /b 1
)

echo   Updating pip...
py -3 -m ensurepip --upgrade >nul 2>&1
py -3 -m pip install --upgrade pip
if errorlevel 1 (
    echo.
    echo   Pip update failed.
    pause
    exit /b 1
)

echo.
echo   Installing Python requirements...
py -3 -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo   Requirement install failed.
    pause
    exit /b 1
)

set "CHROME_FOUND=0"
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"

if "%CHROME_FOUND%"=="0" (
    echo.
    echo   Google Chrome was not found in the usual install locations.
    echo   This downloader uses real Chrome for login/session access.
    where winget >nul 2>&1
    if not errorlevel 1 (
        echo.
        choice /M "Install Google Chrome with winget now"
        if not errorlevel 2 (
            winget install --id Google.Chrome -e --source winget
        )
    ) else (
        echo   Install Chrome from:
        echo   https://www.google.com/chrome/
    )
)

echo.
echo  ================================================
echo   Install complete.
echo   Run run_downloader.bat to start.
echo  ================================================
echo.
pause
