# Spotify Corpus Builder

A desktop tool for downloading audio previews from any Spotify playlist CSV and slicing them into short grains for use as a sample corpus in electroacoustic composition, granular synthesis, and corpus-based sound design.

---

## What It Does

<!-- Write 2–3 sentences here describing what you use this for and why you built it. Example: "I built this for my PhD work at the University of Oregon to quickly assemble large audio corpora from Spotify playlists without manually downloading and editing hundreds of files." -->

- Loads any Spotify playlist exported as a CSV
- Searches YouTube for each track, picks the result that best matches the song (length, title, official "Topic" uploads), and downloads N seconds of it
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

The window has your files and track list on the left (the Log tab next to Tracks opens automatically when you press Start), settings on the right, and a progress bar with the time remaining along the bottom. Everything you set, including the last CSV, is remembered for next time.

In the track list you can search, and select rows (Ctrl/Cmd-click, Shift-click). When you press Start, only the selected tracks are used; if nothing is selected, only the tracks matching the search.

### Command line

Everything the app does is also available from the command line:

```
python3 spotify_corpus_builder.py --csv my_songs.csv --sample 50 --seed 7 --randomize-cut 0.5 2.0
python3 spotify_corpus_builder.py --audio-folder ~/my_wavs --smart-grain --features --cluster
python3 spotify_corpus_builder.py --help
```

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
| Parallel downloads | How many tracks to download at the same time (default 3, max 8). Higher is faster, but YouTube may start refusing requests |
| Download from ⅓ in | Start the download about a third of the way into the song instead of at the beginning, to skip intros |
| Start cut at | Where in the preview to begin the grain (default 5s in) |
| Cut length | How long each grain is (default 1.5s) |
| Random sample | Pick N tracks at random from the CSV instead of all of them |
| Randomize cut per track | Each track gets a random grain length and start point within a range you set |
| Randomize button | Scrambles the main numeric settings, AI checkboxes and grain strategy at once |
| Audio folder | Point to a folder of existing WAVs — skips download and analyses/slices those files directly |

Each grain gets a 5 ms fade in and out so it doesn't click (`--fade-ms` on the command line; 0 turns it off). If a cut would run past the end of a file, it is moved back so the grain is always full length.

---

## Themes and Languages

Pick a theme and language in the top-right corner. Themes are the JSON files in `themes/`; add your own by copying one and changing the colours and `_name`. Languages are in `translations.json` (English is built in); any text a translation leaves out is shown in English.

---

## AI Analysis

Requires librosa (installed by setup script). The app shows a ✓ in the AI section header when it's ready.

| Feature | What it does |
|---|---|
| Smart grain selection | Scores every possible cut for loudness, onset density and timbral movement, and cuts at the best combined moment (`--grain-strategy` picks just one) |
| Best match / flag wrong versions | Compares the top 5 YouTube results to the song's length and title, prefers official "Topic" uploads, and flags likely live/cover/remix/sped-up versions. Doesn't need librosa |
| Extract audio features | Writes tempo, RMS energy, spectral centroid and estimated key (major/minor) per track to `metadata.json` |
| Cluster by similarity | Groups grains by timbre (MFCCs), picks the number of groups automatically, and copies each group into `grains_by_cluster/` |

Spotify's own data from the CSV (key, tempo, energy, danceability, valence, genres, album, label…) is also saved to `metadata.json` for each track. It was measured on the full studio recording, so trust it over the estimates from a short clip.
| CLAP embeddings | Optional — requires laion-clap (~2GB). Produces `coords.json` for spatial corpus browsers |

Analysis results are saved in `metadata.json` and reused on the next run, so only new files are analysed.

> **Note:** The first time smart grain selection runs, numba compiles in the background (30–60s). The log goes quiet briefly — this is normal.

---

## Output Structure

```
output/
  previews/       ← downloaded WAVs, one per track
  grains/         ← sliced grains, ready for corpus use
  grains_by_cluster/cluster_01/ …  ← grains grouped by similarity (if clustering is on)
  metadata.json   ← per track: Spotify data, which YouTube video was used, where the grain was cut, AI results
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
