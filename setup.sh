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
echo " 1. yt-dlp          YouTube audio downloader"
echo " 2. customtkinter   Modern GUI library"
echo " 3. librosa         Audio analysis (AI features)"
echo " 4. scikit-learn    Machine learning (clustering)"
echo " 5. soundfile       WAV file reader"
echo " 6. numpy           Numerical processing"
echo
echo "------------------------------------------------"
echo " SYSTEM TOOL"
echo "------------------------------------------------"
echo
echo " ffmpeg  --  audio converter (via Homebrew if missing)"
echo
echo "------------------------------------------------"
echo " IMPORTANT NOTES"
echo "------------------------------------------------"
echo
echo " - All packages are open source"
echo " - None collect your data or require an account"
echo " - yt-dlp searches YouTube the same way a browser would"
echo " - Nothing is installed silently beyond the list above"
echo
echo "============================================"
echo " Press Ctrl+C NOW to cancel."
echo " Otherwise, setup will begin in 10 seconds."
echo "============================================"
echo
sleep 10

# ── Check Python 3 ────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo
    echo "ERROR: Python 3 not found."
    echo "Download from https://www.python.org/downloads/"
    echo "Or run: brew install python"
    exit 1
fi
echo
echo "[OK] $(python3 --version)"

# ── Install packages ─────────────────────────────────────────────────────────
PACKAGES="yt-dlp customtkinter librosa scikit-learn soundfile numpy"

echo
echo "Installing Python packages..."

# Try normal install first (works on most systems)
python3 -m pip install --upgrade $PACKAGES 2>/dev/null
STATUS=$?

# macOS 14+ / Homebrew Python blocks pip without a flag or venv
if [ $STATUS -ne 0 ]; then
    echo
    echo "[!] Standard pip install blocked (common on macOS 14+ / Homebrew Python)."
    echo "    Trying --break-system-packages..."
    python3 -m pip install --upgrade --break-system-packages $PACKAGES 2>/dev/null
    STATUS=$?
fi

# Fall back to a local virtual environment if both attempts failed
if [ $STATUS -ne 0 ]; then
    echo
    echo "[!] pip install still failed. Creating a local virtual environment..."
    python3 -m venv .venv
    source .venv/bin/activate
    python3 -m pip install --upgrade $PACKAGES
    STATUS=$?
    deactivate

    if [ $STATUS -eq 0 ]; then
        echo
        echo "[OK] Packages installed into .venv"
        echo
        echo "  IMPORTANT: Because a virtual environment was used, run the app with:"
        echo "    source .venv/bin/activate && python3 spotify_corpus_builder.py"
        echo "  Or double-click run.sh if you have one."
    else
        echo
        echo "ERROR: Installation failed. Try manually:"
        echo "  python3 -m venv .venv"
        echo "  source .venv/bin/activate"
        echo "  pip install yt-dlp customtkinter librosa scikit-learn soundfile numpy"
        exit 1
    fi
else
    echo "[OK] Python packages installed"
fi

# ── Check / install ffmpeg ────────────────────────────────────────────────────
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
