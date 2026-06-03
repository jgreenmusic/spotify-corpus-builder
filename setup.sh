#!/bin/bash
echo "============================================"
echo " Spotify Corpus Builder - Mac Setup"
echo "============================================"
echo
echo "This script will install the following software"
echo "on your computer. Please read before continuing."
echo
echo "------------------------------------------------"
echo " PYTHON PACKAGES  (installed via pip)"
echo "------------------------------------------------"
echo
echo " 1. yt-dlp"
echo "    What:    YouTube audio downloader"
echo "    By:      yt-dlp open source team"
echo "    Source:  github.com/yt-dlp/yt-dlp"
echo "    Used by: Searching YouTube and downloading"
echo "             audio previews for each track"
echo "    Size:    ~5 MB"
echo
echo " 2. customtkinter"
echo "    What:    Modern graphical UI library for Python"
echo "    By:      Tom Schimansky (open source)"
echo "    Source:  github.com/TomSchimansky/CustomTkinter"
echo "    Used by: The app window, buttons, and all"
echo "             visual elements"
echo "    Size:    ~2 MB"
echo
echo " These packages also install automatically"
echo " as sub-dependencies:"
echo
echo "    mutagen      - reads audio file metadata"
echo "    Pillow       - image handling (for UI icons)"
echo "    darkdetect   - detects system dark/light mode"
echo "    packaging    - version number handling"
echo "    requests     - standard web requests library"
echo
echo "------------------------------------------------"
echo " SYSTEM TOOL  (installed via Homebrew if missing)"
echo "------------------------------------------------"
echo
echo " 3. ffmpeg"
echo "    What:    Audio and video converter"
echo "    By:      FFmpeg open source project"
echo "    Source:  ffmpeg.org"
echo "    Used by: Converting downloaded audio to WAV"
echo "    Size:    ~80-100 MB"
echo
echo "------------------------------------------------"
echo " IMPORTANT NOTES"
echo "------------------------------------------------"
echo
echo " - All packages listed above are open source"
echo " - None of them collect your data or require"
echo "   an account"
echo " - yt-dlp searches YouTube publicly, the same"
echo "   way a browser would"
echo " - Nothing is installed silently beyond what"
echo "   is listed here"
echo
echo "============================================"
echo " Press Ctrl+C NOW to cancel."
echo " Otherwise, setup will begin in 10 seconds."
echo "============================================"
echo
sleep 10

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo
    echo "ERROR: Python 3 not found."
    echo "Download from https://www.python.org/downloads/"
    echo "Or run: brew install python"
    exit 1
fi
echo
echo "[OK] $(python3 --version)"

# Install Python packages
echo
echo "Installing yt-dlp and customtkinter..."
pip3 install --upgrade yt-dlp customtkinter
if [ $? -ne 0 ]; then
    echo
    echo "ERROR: Installation failed."
    echo "Try running with: sudo pip3 install --upgrade yt-dlp customtkinter"
    exit 1
fi
echo "[OK] Python packages installed"

# Check / install ffmpeg
echo
if command -v ffmpeg &>/dev/null; then
    echo "[OK] ffmpeg found"
else
    echo "[!] ffmpeg not found. Attempting to install via Homebrew..."
    if command -v brew &>/dev/null; then
        brew install ffmpeg
        if [ $? -eq 0 ]; then
            echo "[OK] ffmpeg installed"
        else
            echo "ERROR: Homebrew install failed. Try manually: brew install ffmpeg"
        fi
    else
        echo
        echo "    Homebrew not found. To install ffmpeg:"
        echo "    1. Install Homebrew: https://brew.sh"
        echo "    2. Then run: brew install ffmpeg"
    fi
fi

echo
echo "============================================"
echo " Setup complete."
echo " Run: python3 spotify_corpus_builder.py"
echo "============================================"
