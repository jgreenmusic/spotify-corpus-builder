# Spotify Corpus Builder

A desktop tool for downloading audio previews from any Spotify playlist CSV and slicing them into short grains for use as a sample corpus in electroacoustic composition, granular synthesis, and corpus-based sound design.

---

## What It Does

<!-- Write 2–3 sentences here describing what you use this for and why you built it. Example: "I built this for my PhD work at the University of Oregon to quickly assemble large audio corpora from Spotify playlists without manually downloading and editing hundreds of files." -->

- Loads any Spotify playlist exported as a CSV
- Searches YouTube for each track and downloads the first N seconds
- Slices each download into a short audio grain at a configurable offset and length
- Optionally picks a random subset of tracks from a large CSV
- Optionally randomizes the cut position and grain length per track for varied corpora
- AI analysis layer: smart grain selection, wrong-version detection, feature extraction, and clustering

---

## Screenshots

<!-- Add screenshots here. Drag images into this editor on GitHub, or use: -->
<!-- ![App window](screenshots/app.png) -->

---

## Requirements

- **Python 3** — [python.org](https://www.python.org/downloads/)
  - Windows: check "Add Python to PATH" during install
- **ffmpeg** — audio conversion
  - Windows: `winget install ffmpeg`
  - Mac: `brew install ffmpeg` (setup.sh will attempt this automatically)

All Python dependencies (yt-dlp, customtkinter, librosa, scikit-learn, soundfile, numpy) are installed by the setup script.

---

## Installation

**Windows** — double-click `setup.bat`

**Mac** — open Terminal, navigate to the folder, then run:
```bash
chmod +x setup.sh
./setup.sh
```

The setup script installs all dependencies and checks for ffmpeg. On macOS 14+ (Sonoma) it handles the externally-managed-environment restriction automatically.

---

## Usage

**Windows:**
```
python spotify_corpus_builder.py
```

**Mac:**
```
python3 spotify_corpus_builder.py
```

Or double-click the `.py` file on Windows.

### Getting your Spotify CSV

1. Go to [exportify.net](https://exportify.net)
2. Log in with Spotify
3. Click Export next to any playlist or Liked Songs
4. Load the saved CSV in the app with the Browse button

---

## Settings

| Setting | What it does |
|---|---|
| Download length | How many seconds to download from YouTube per track (default 30s) |
| Start cut at | Where in the preview to begin the grain (default 5s in) |
| Cut length | How long each grain is (default 1.5s) |
| Random sample | Pick N tracks at random from the CSV instead of all of them |
| Randomize cut per track | Each track gets a random grain length and start point within a range you set |
| Randomize button | Scrambles all numeric settings and AI checkboxes at once |
| Audio folder | Point to a folder of existing WAVs — skips download and slices those files directly |

---

## AI Analysis

Requires librosa (installed by setup script). The app shows a ✓ in the AI section header when it's ready.

| Feature | What it does |
|---|---|
| Smart grain selection | Analyzes each WAV with three strategies (energy peak, onset density, spectral variance) and picks the best moment to cut |
| Flag wrong versions | Scores each download for live/cover indicators and logs suspicious results |
| Extract audio features | Writes tempo, RMS energy, spectral centroid, key per track to `metadata.json` |
| Cluster by similarity | Groups grains into similarity buckets using K-means after slicing |
| CLAP embeddings | Optional — requires laion-clap (~2GB). Produces `coords.json` for spatial corpus browsers |

> **Note:** The first time smart grain selection runs, numba compiles in the background (30–60s). The log goes quiet briefly — this is normal.

---

## Output Structure

```
output/
  previews/       ← downloaded WAVs, one per track
  grains/         ← sliced grains, ready for corpus use
  metadata.json   ← AI analysis results (if enabled)
  coords.json     ← CLAP embeddings (if enabled)
```

---

## About

**[jgreenmusic](https://github.com/jgreenmusic)**

<!-- Feel free to add more: what corpus-based tools you use this with (Max/MSP, CataRT, Kyma, etc.), links to pieces made with it, or your website. -->

---

## License

<!-- Choose one and delete the others, or remove this section: -->
<!-- MIT License — free to use, modify, and distribute -->
<!-- GPL-3.0 — open source, derivative works must stay open -->
<!-- No license stated — all rights reserved by default -->
