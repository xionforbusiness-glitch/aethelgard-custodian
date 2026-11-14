@echo off
REM ============================================================================
REM Aethelgard Custodian — 1-Click Windows Account Pairing Launcher (R4.2)
REM ============================================================================
title Aethelgard Omnichannel Account Connector
cd /d "%~dp0"
echo ============================================================================
echo   AETHELGARD OMNICHANNEL 1-CLICK ACCOUNT CONNECTOR (WINDOWS)
echo ============================================================================
python connect_accounts_windows.py --extract %*
if %ERRORLEVEL% NEQ 0 (
    echo [!] Notice: Make sure Python 3 is installed and in your PATH.
)
echo.
echo Credentials safely stored in %USERPROFILE%\.aethelgard\credentials\
if "%1"=="" pause
