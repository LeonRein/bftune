@echo off
rem bftune launcher for Windows shells without Git Bash (PowerShell, cmd). Same job as bin/bftune:
rem run the Python package bundled with the plugin through uv.
setlocal
set "HERE=%~dp0.."
where uv >nul 2>nul || if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%PATH%;%USERPROFILE%\.local\bin"
where uv >nul 2>nul || if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "PATH=%PATH%;%USERPROFILE%\.cargo\bin"
where uv >nul 2>nul && goto run
echo bftune: needs 'uv' (https://docs.astral.sh/uv/). Install it for the current user, then run bftune again: 1>&2
echo     powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" 1>&2
exit /b 127
:run
uv run --quiet --frozen --project "%HERE%" python -m bftune %*
exit /b %ERRORLEVEL%
