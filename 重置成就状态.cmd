@echo off
chcp 65001 >nul
set "PROJECT_PYTHON=%~dp0..\python.exe"
if exist "%PROJECT_PYTHON%" (
    "%PROJECT_PYTHON%" "%~dp0scripts\reset_achievements.py"
) else (
    py -3 "%~dp0scripts\reset_achievements.py"
)
echo.
pause
