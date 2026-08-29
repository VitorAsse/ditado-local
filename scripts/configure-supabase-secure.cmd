@echo off
setlocal
title Ditado Local - Supabase seguro
cd /d "%~dp0.."
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0configure-supabase-secure.ps1"
set "DITADO_EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%DITADO_EXIT_CODE%"=="0" (
  echo A configuracao nao terminou. O diagnostico seguro foi salvo pelo script.
)
pause
exit /b %DITADO_EXIT_CODE%
