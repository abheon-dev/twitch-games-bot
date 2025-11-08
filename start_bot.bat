@echo off
chcp 65001 >nul
title Twitch Games Bot

echo.
echo ╔══════════════════════════════════════════════════════════════╗
echo ║              TWITCH GAMES BOT INDÍTÓ PROGRAM                 ║
echo ╚══════════════════════════════════════════════════════════════╝
echo.

echo 🔍 Ellenőrzés...
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python nincs telepítve vagy nincs a PATH-ban!
    echo Kérlek, telepítsd a Pythont: https://python.org
    pause
    exit /b 1
)

echo ✅ Python telepítve
echo.

echo 📦 Függőségek ellenőrzése...
python -m pip show twitchio >nul 2>&1
if errorlevel 1 (
    echo ⚠️ twitchio nincs telepítve, telepítés...
    python -m pip install twitchio
)

python -m pip show websockets >nul 2>&1
if errorlevel 1 (
    echo ⚠️ websockets nincs telepítve, telepítés...
    python -m pip install websockets
)

python -m pip show python-dotenv >nul 2>&1
if errorlevel 1 (
    echo ⚠️ python-dotenv nincs telepítve, telepítés...
    python -m pip install python-dotenv
)

echo ✅ Minden függőség telepítve
echo.

echo 🚀 Bot indítása...
echo ────────────────────────────────────────────────────────────────
echo.

python -u main_bot.py

echo.
echo ────────────────────────────────────────────────────────────────
echo.
echo 🛑 A bot leállt.
pause