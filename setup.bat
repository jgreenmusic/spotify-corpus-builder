@echo off
echo ============================================
echo  Spotify Corpus Builder - Windows Setup
echo ============================================
echo.
echo This script will install the following software
echo on your computer. Please read before continuing.
echo.
echo ------------------------------------------------
echo  PYTHON PACKAGES  (installed via pip)
echo ------------------------------------------------
echo.
echo  1. yt-dlp
echo     What:    YouTube audio downloader
echo     By:      yt-dlp open source team
echo     Source:  github.com/yt-dlp/yt-dlp
echo     Used by: Searching YouTube and downloading
echo              audio previews for each track
echo     Size:    ~5 MB
echo.
echo  2. customtkinter
echo     What:    Modern graphical UI library for Python
echo     By:      Tom Schimansky (open source)
echo     Source:  github.com/TomSchimansky/CustomTkinter
echo     Used by: The app window, buttons, and all
echo              visual elements
echo     Size:    ~2 MB
echo.
echo  These packages also install automatically
echo  as sub-dependencies:
echo.
echo     mutagen      - reads audio file metadata
echo     Pillow       - image handling (for UI icons)
echo     darkdetect   - detects system dark/light mode
echo     packaging    - version number handling
echo     requests     - standard web requests library
echo.
echo ------------------------------------------------
echo  SYSTEM TOOL  (installed separately)
echo ------------------------------------------------
echo.
echo  3. ffmpeg
echo     What:    Audio and video converter
echo     By:      FFmpeg open source project
echo     Source:  ffmpeg.org
echo     Used by: Converting downloaded audio to WAV
echo     Size:    ~80-100 MB
echo.
echo ------------------------------------------------
echo  IMPORTANT NOTES
echo ------------------------------------------------
echo.
echo  - All packages listed above are open source
echo  - None of them collect your data or require
echo    an account
echo  - yt-dlp searches YouTube publicly, the same
echo    way a browser would
echo  - Nothing is installed silently beyond what
echo    is listed here
echo.
echo ============================================
echo  Press Ctrl+C NOW to cancel.
echo  Otherwise, setup will begin in 10 seconds.
echo ============================================
echo.
timeout /t 10 /nobreak >nul

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Python not found.
    echo Download and install Python from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)
echo.
echo [OK] Python found
python --version

:: Install Python packages
echo.
echo Installing yt-dlp and customtkinter...
pip install --upgrade yt-dlp customtkinter
if errorlevel 1 (
    echo.
    echo ERROR: Installation failed. Try running this script as Administrator.
    pause
    exit /b 1
)
echo [OK] Python packages installed

:: Check ffmpeg
echo.
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [!] ffmpeg not found.
    echo.
    echo     Install it with one of these commands:
    echo.
    echo       winget install ffmpeg
    echo       choco install ffmpeg
    echo.
    echo     Or download manually from: https://ffmpeg.org/download.html
    echo     After installing, run this setup again to confirm.
) else (
    echo [OK] ffmpeg found
)

echo.
echo ============================================
echo  Setup complete.
echo  Run: python spotify_corpus_builder.py
echo ============================================
pause
