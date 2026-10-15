@echo off
REM Trading Bot CLI Wrapper for Windows
REM Usage: bot.bat <command> [args...]
REM
REM Examples:
REM   bot.bat status
REM   bot.bat trading on
REM   bot.bat leverage 10
REM   bot.bat balance

setlocal

if "%BOT_CONTAINER%"=="" (
    set CONTAINER_NAME=trading-bot
) else (
    set CONTAINER_NAME=%BOT_CONTAINER%
)

REM Check if container is running
docker ps --format "{{.Names}}" | findstr /x "%CONTAINER_NAME%" >nul 2>&1
if errorlevel 1 (
    echo Error: Container '%CONTAINER_NAME%' is not running
    echo Start with: docker compose --profile prod up -d
    exit /b 1
)

REM Execute CLI in container
docker exec %CONTAINER_NAME% python -m scripts.cli %*
