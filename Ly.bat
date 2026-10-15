@echo off
setlocal
cd /d "%~dp0"
title Ly AI - Local AI Launcher
color 0B
mode con: cols=78 lines=23 >nul 2>&1
cls
echo.
echo   ================================================================
echo.
echo          _      __   __      _    ___
echo         ^| ^|     \ \ / /     / \  ^|_ _^|
echo         ^| ^|      \ V /     / _ \  ^| ^|
echo         ^| ^|___   ^| ^|     / ___ \ ^| ^|
echo         ^|_____^|  ^|_^|    /_/   \_\___^|
echo.
echo                        L Y   A I
echo                 Welcome to your AI space
echo.
echo   ---------------------------------------------------------------
echo       Local AI  ^|  Private workspace  ^|  Ollama
echo   ---------------------------------------------------------------
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1"
exit /b %errorlevel%
