@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   Shuake Toolkit - first run dependency bootstrap
echo ============================================================
echo.

rem Only fall back when the interpreter probe itself fails. Once bootstrap.py
rem starts, its nonzero exit is final: rerunning --install could launch the
rem business app twice when dependency setup succeeded but app startup failed.
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto use_python
py -3 "%~dp0bootstrap.py" --install
set "BOOTSTRAP_EXIT_CODE=%errorlevel%"
goto report_result

:use_python
where python >nul 2>nul
if errorlevel 1 goto no_python
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto no_python
python "%~dp0bootstrap.py" --install
set "BOOTSTRAP_EXIT_CODE=%errorlevel%"
goto report_result

:no_python
echo No working Python 3.11+ interpreter was found.
set "BOOTSTRAP_EXIT_CODE=1"

:report_result
if "%BOOTSTRAP_EXIT_CODE%"=="0" goto done
echo.
echo Bootstrap failed. Please check logs\bootstrap_*.log
pause

:done
endlocal & exit /b %BOOTSTRAP_EXIT_CODE%
