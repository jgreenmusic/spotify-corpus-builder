@echo off
echo ============================================
echo  Spotify Corpus Builder - Windows Setup
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo Download and install Python from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)
echo [OK] Python found
python --version

:: Install yt-dlp
echo.
echo Installing yt-dlp...
pip install --upgrade yt-dlp
if errorlevel 1 (
    echo ERROR: pip install failed. Try running this as Administrator.
    pause
    exit /b 1
)
echo [OK] yt-dlp installed

:: Check ffmpeg
echo.
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [!] ffmpeg not found on PATH.
    echo.
    echo Install it with one of these:
    echo   winget install ffmpeg
    echo   choco install ffmpeg
    echo   Or download from https://ffmpeg.org/download.html
    echo.
    echo After installing, restart this setup to confirm.
) else (
    echo [OK] ffmpeg found
)

echo.
echo ============================================
echo  Setup complete.
echo  Run:  python spotify_corpus_builder.py
echo ============================================
pause
