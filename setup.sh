#!/bin/bash
echo "============================================"
echo " Spotify Corpus Builder - Mac Setup"
echo "============================================"
echo

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3 not found."
    echo "Download from https://www.python.org/downloads/ or run: brew install python"
    exit 1
fi
echo "[OK] $(python3 --version)"

# Install yt-dlp
echo
echo "Installing dependencies..."
pip3 install --upgrade yt-dlp customtkinter
if [ $? -ne 0 ]; then
    echo "ERROR: pip3 install failed."
    exit 1
fi
echo "[OK] yt-dlp installed"

# Check / install ffmpeg
echo
if command -v ffmpeg &>/dev/null; then
    echo "[OK] ffmpeg found"
else
    echo "[!] ffmpeg not found. Attempting to install via Homebrew..."
    if command -v brew &>/dev/null; then
        brew install ffmpeg
    else
        echo "Homebrew not found. Install ffmpeg manually:"
        echo "  1. Install Homebrew: https://brew.sh"
        echo "  2. Then run: brew install ffmpeg"
    fi
fi

echo
echo "============================================"
echo " Setup complete."
echo " Run:  python3 spotify_corpus_builder.py"
echo "============================================"
