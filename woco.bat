@echo off
rem Convenience wrapper: "woco.bat <args>" == "uv run woco <args>".
rem The actual entry point lives in woco_cli.py and [project.scripts] woco
rem in pyproject.toml. This shim avoids the "uv run" prefix from cmd.exe
rem and PowerShell while keeping the project root anchored to this file.
setlocal
set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"

call uv run --project "%HERE%" woco %*
exit /b %ERRORLEVEL%
