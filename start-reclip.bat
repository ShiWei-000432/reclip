@echo off
REM ============================================================
REM  ReClip Windows launcher (self-bootstrapping)
REM
REM  What it does:
REM    0. Creates the venv and installs dependencies on first run
REM    1. Adds venv\Scripts to PATH  -> yt-dlp.exe becomes visible
REM    2. Locates a WORKING ffmpeg   -> MP4 merge / MP3 extraction
REM    3. Starts the Flask app
REM
REM  Usage: double-click, or run from a terminal.
REM         set PORT=9000                  to change the port
REM         set HOST=0.0.0.0               to expose on the LAN
REM         set RECLIP_TOKEN=your-secret   to require an access token
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "VENV=%~dp0venv"

REM --- 0) Bootstrap the virtual environment on first run ---------------
if not exist "%VENV%\Scripts\python.exe" (
    echo [INFO] First run - creating virtual environment...
    where python >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Python was not found on PATH.
        echo         Install Python 3.8+ and make sure "Add to PATH" is ticked.
        exit /b 1
    )
    python -m venv "%VENV%"
    if not exist "%VENV%\Scripts\python.exe" (
        echo [ERROR] Could not create the virtual environment.
        exit /b 1
    )
    echo [INFO] Installing dependencies...
    "%VENV%\Scripts\python.exe" -m pip install --upgrade pip
    "%VENV%\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed. Check your network / proxy.
        exit /b 1
    )
)

REM --- 1) venv Scripts first, so yt-dlp.exe resolves to our venv copy --
set "PATH=%VENV%\Scripts;%PATH%"

REM --- 2) Make sure ffmpeg actually RUNS, not just resolves.
REM        NOTE: the shims in %LOCALAPPDATA%\Microsoft\WinGet\Links are often
REM        0-byte reparse points that yt-dlp cannot execute, so we point
REM        PATH at the real package bin directory instead.
ffmpeg -version >nul 2>nul
if errorlevel 1 (
    set "FFMPEG_FOUND="
    for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*") do (
        for /d %%E in ("%%~D\ffmpeg-*") do (
            if exist "%%~E\bin\ffmpeg.exe" set "FFMPEG_FOUND=%%~E\bin"
        )
    )
    if not defined FFMPEG_FOUND (
        for %%P in ("C:\ffmpeg\bin" "%ProgramFiles%\ffmpeg\bin" "%LOCALAPPDATA%\ffmpeg\bin") do (
            if exist "%%~P\ffmpeg.exe" set "FFMPEG_FOUND=%%~P"
        )
    )
    if defined FFMPEG_FOUND (
        set "PATH=!FFMPEG_FOUND!;!PATH!"
        echo [INFO] ffmpeg added to PATH from: !FFMPEG_FOUND!
    ) else (
        echo [WARN] No working ffmpeg found.
        echo        MP4 merging and MP3 extraction will FAIL.
        echo        Fix with:  winget install Gyan.FFmpeg
    )
)

where yt-dlp >nul 2>nul
if errorlevel 1 (
    echo [WARN] yt-dlp not found. Run:
    echo        venv\Scripts\python.exe -m pip install -r requirements.txt
)

if "%PORT%"=="" set "PORT=8899"
if "%HOST%"=="" set "HOST=127.0.0.1"

echo.
echo   ReClip is running at http://localhost:%PORT%
if "%RECLIP_TOKEN%"=="" (
    echo   Auth: OFF  ^(set RECLIP_TOKEN to require a token^)
) else (
    echo   Auth: ON   ^(token required^)
)
echo   Press Ctrl+C to stop.
echo.

"%VENV%\Scripts\python.exe" app.py
endlocal
