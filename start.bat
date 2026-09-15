@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -X utf8 -B "%~dp0launcher.py" %*
) else (
    python -X utf8 -B "%~dp0launcher.py" %*
)
set "IELTS_EXIT=%ERRORLEVEL%"
if not "%IELTS_EXIT%"=="0" (
    echo.
    echo Please see docs deployment guide, or run start.bat --doctor.
    pause
)
exit /b %IELTS_EXIT%
