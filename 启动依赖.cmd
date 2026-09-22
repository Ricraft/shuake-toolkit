@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   Shuake Toolkit - first run dependency bootstrap
echo ============================================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0bootstrap.py" --install
) else (
    python "%~dp0bootstrap.py" --install
)

if errorlevel 1 (
    echo.
    echo Bootstrap failed. Please check logs\bootstrap_*.log
    pause
)

endlocal
