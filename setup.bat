@echo off
setlocal
cd /d "%~dp0"
title Ly AI Studio - Setup
color 0B
cls
echo ================================================================
echo                       Ly AI STUDIO - SETUP
echo ================================================================
echo.
echo This setup creates a private Python environment and installs
echo the required packages. Ollama is installed separately.
echo.

if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3 -m venv .venv
    ) else (
        where python >nul 2>&1
        if errorlevel 1 (
            echo ERROR: Python 3 was not found.
            echo Install Python from https://www.python.org/downloads/windows/
            echo During installation, enable the option to add Python to PATH.
            echo.
            pause
            exit /b 1
        )
        python -m venv .venv
    )
    if errorlevel 1 (
        echo ERROR: Could not create the virtual environment.
        echo Check that Python 3 is installed correctly.
        pause
        exit /b 1
    )
)

.venv\Scripts\python.exe initialize_config.py
if errorlevel 1 (
    echo ERROR: Could not create the configuration file.
    pause
    exit /b 1
)

echo Installing Python packages...
call .venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 (
    echo ERROR: pip could not be updated. Check your internet connection.
    pause
    exit /b 1
)
call .venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Package installation failed. Check your internet connection.
    pause
    exit /b 1
)

echo.
echo ================================================================
echo Setup finished.
echo 1. Install and run Ollama, then download a model (see README.md).
echo 2. Double-click Ly.bat to open the local assistant.
echo 3. Social login is optional and requires Supabase configuration.
echo 4. Never upload .env or OAuth client secrets to GitHub.
echo ================================================================
echo.
pause
