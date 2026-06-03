# Spotify Corpus Builder

Downloads a short audio preview for every track in your Spotify liked songs, then slices each one into a grain for use as a sample corpus.

**What you get:**
- `output/previews/` — one 30-second WAV per track (from YouTube)
- `output/grains/` — one short slice per track (default: 1.5 seconds, starting 5 seconds in)

---

## What you need before running

- **Python 3** — [python.org/downloads](https://www.python.org/downloads/)
  - Windows: check "Add Python to PATH" during install
- **ffmpeg** — handles audio conversion
  - Windows: `winget install ffmpeg` in PowerShell
  - Mac: `brew install ffmpeg` in Terminal
- **yt-dlp** — downloads from YouTube (installed by the setup script below)

---

## Setup (one time only)

**Windows** — double-click `setup.bat`

**Mac** — open Terminal, drag the folder in, then run:
```
chmod +x setup.sh
./setup.sh
```

This installs yt-dlp and checks that ffmpeg is ready.

---

## How to run

Put `Liked_Songs.csv` in the same folder as the script (it's already included).

**Windows:**
```
python spotify_corpus_builder.py
```

**Mac:**
```
python3 spotify_corpus_builder.py
```

It will print progress as it downloads and slices each track. If it gets interrupted, just run it again — it skips files that already exist.

---

## Options

All options are optional. The defaults work fine for most uses.

| Option | Default | What it does |
|---|---|---|
| `--offset` | `5.0` | Seconds into the preview to start the grain |
| `--duration` | `1.5` | Length of each grain in seconds |
| `--preview-length` | `30` | How many seconds to download per track |
| `--csv` | `Liked_Songs.csv` | Path to a different Spotify CSV |
| `--output` | `./output/` | Where to save the files |
| `--skip-download` | off | Skip downloading, just slice existing previews |
| `--skip-slice` | off | Download only, skip slicing |

**Examples:**

Longer grains starting later in the preview:
```
python spotify_corpus_builder.py --offset 10 --duration 3.0
```

Download only (no slicing):
```
python spotify_corpus_builder.py --skip-slice
```

Use a different CSV and save to a custom folder:
```
python spotify_corpus_builder.py --csv my_playlist.csv --output ./my_corpus/
```

---

## How to export your CSV from Spotify

The included `Liked_Songs.csv` is already set up. To export your own:
1. Go to [exportify.net](https://exportify.net)
2. Log in with Spotify
3. Click Export next to "Liked Songs"
4. Save the CSV and place it next to this script

---

## Output structure

```
output/
├── previews/          <- full 30-second WAVs
│   ├── Artist - Track Name.wav
│   └── ...
└── grains/            <- sliced grains (ready for corpus use)
    ├── Artist - Track Name.wav
    └── ...
```

---

## Troubleshooting

**"yt-dlp not installed"** — run `pip install yt-dlp` (Windows) or `pip3 install yt-dlp` (Mac)

**"ffmpeg not found"** — install ffmpeg and make sure it's on your PATH (run `setup.bat` / `setup.sh` for instructions)

**A track shows [failed]** — yt-dlp couldn't find it on YouTube. This is normal for obscure tracks. Everything else still downloads.

**Script stopped halfway** — just run it again. It skips anything already downloaded.
