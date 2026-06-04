Spotify Corpus Builder
======================

Downloads audio for every track in a Spotify CSV export, then slices each
one into a short grain for use as a sample corpus.

What you get:
  output/previews/  --  one WAV per track (default: 30 seconds each)
  output/grains/    --  one short slice per track



SETUP (one time only)
----------------------

  Windows  --  double-click setup.bat

  Mac      --  open Terminal, drag this folder into it, then run:
                 chmod +x setup.sh
                 ./setup.sh

  This installs yt-dlp, customtkinter, librosa, scikit-learn, soundfile,
  and numpy. It also checks that ffmpeg is available and installs it via
  Homebrew on Mac if missing.

  On macOS 14+ (Sonoma) or systems with Homebrew Python, setup.sh will
  automatically try fallback methods if the standard pip install is blocked.


HOW TO RUN
-----------

  Windows:   double-click spotify_corpus_builder.py
             or run:  python spotify_corpus_builder.py

  Mac:       run:  python3 spotify_corpus_builder.py

  Load your CSV using the Browse button, check the track list, adjust
  settings if needed, then click Start.

  If it gets interrupted, just run it again -- it skips files that
  already exist.



IMPORTANT: HOW THE DOWNLOADS WORK
-----------------------------------

  This app does NOT use the Spotify API and does NOT download official
  Spotify audio.

  Instead, it searches YouTube for each track using the artist name and
  track title (e.g. "Psychic Mirrors - Ricky Thai"), then downloads the
  first N seconds of whatever YouTube returns.

  What this means in practice:

    - Most popular tracks will match correctly and give you the studio version.

    - Some tracks may return a live recording, a cover version, a music
      video, or a fan upload instead of the original studio recording.

    - Very obscure tracks may not be found at all and will show [failed]
      in the log.

    - You are downloading publicly available audio from YouTube. Make sure
      this is appropriate for your use case.


WHAT YOU NEED BEFORE RUNNING
------------------------------

  Python 3          https://www.python.org/downloads/
                    Windows: check "Add Python to PATH" during install

  ffmpeg            handles audio conversion
                    Windows: run  winget install ffmpeg  in PowerShell
                    Mac:     run  brew install ffmpeg  in Terminal
                    (setup.sh will attempt this automatically on Mac)




HOW TO EXPORT YOUR CSV FROM SPOTIFY
-------------------------------------

  The included Liked_Songs.csv is already set up. To export your own:

  1. Go to exportify.net
  2. Log in with Spotify
  3. Click Export next to any playlist or Liked Songs
  4. Save the CSV and load it in the app using the Browse button

  Your CSV must have "Track Name" and "Artist Name(s)" columns.
  Exportify produces exactly this format.


SETTINGS
---------

  Download length
    How many seconds of each track to download from YouTube (default: 30s).

  Start cut at
    How far into the preview to begin the grain (default: 5s in).

  Cut length
    How long each grain should be (default: 1.5s).

  Step 1 -- Download previews from YouTube
    Uncheck if you already have previews downloaded and only want to re-slice.

  Step 2 -- Slice into grains
    Uncheck if you only want the raw previews without slicing.

  Random sample -- pick N tracks at random from the CSV
    Check this and set a count to draw a random subset from your CSV instead
    of processing every track. Useful for testing with a large CSV like
    Liked_Songs.csv without committing to the full run.

  Randomize cut per track -- duration min to max
    When checked, each track gets its own randomly chosen grain length
    (between your min and max) and a randomly chosen start point within
    the downloaded preview. Every run produces a different set of grains.

  Randomize button (in the action bar)
    Randomizes the Download length, Offset, Cut length, and AI checkboxes
    all at once. Good for quickly exploring different parameter combinations.

  Audio folder (optional)
    Browse to a folder of existing WAV files to feed directly into Step 2
    without downloading anything. The app will slice those files using your
    current settings. When an audio folder is set, Step 1 is skipped even
    if checked.


AI ANALYSIS (Note to self: I'm not sure if the AI Analysis feature is functioning properly. I am not a professional coder and used Claude Code to help me realize this tool in it's entirety)
------------

  The AI section requires librosa, which setup.bat / setup.sh installs.
  The section header shows a checkmark when librosa is ready, or disables
  the checkboxes if it is missing.

  Smart grain selection
    Analyzes each WAV with three strategies (peak energy, onset density,
    spectral centroid variance) and picks the best moment to cut. The
    winning strategy is remembered across runs and used as a tiebreaker.

  Flag suspected wrong versions
    Scores each download for live-recording and cover indicators. Tracks
    that score above threshold are flagged in the log as [live?] or [cover?].

  Extract audio features
    Writes tempo, RMS energy, spectral centroid, zero crossing rate, and
    estimated key for each track into output/metadata.json.

  Cluster corpus by similarity
    After slicing, groups your grains into similarity buckets using
    K-means clustering. Cluster IDs are written into metadata.json.

  CLAP embeddings (optional)
    Requires laion-clap (~2GB model download on first use). Produces a
    coords.json with 2D coordinates for each grain based on audio content,
    suitable for spatial corpus browsers.

  NOTE: The first time librosa's smart grain selection runs, numba (its
  JIT compiler) takes 30-60 seconds to compile. The log will go quiet
  briefly -- this is normal. Subsequent runs are fast.


OUTPUT STRUCTURE
-----------------

  output/
    previews/        <-- downloaded WAVs (one per track)
      Artist - Track Name.wav
      ...
    grains/          <-- sliced grains (ready for corpus use)
      Artist - Track Name.wav
      ...
    metadata.json    <-- AI analysis results (if AI features are enabled)
    coords.json      <-- CLAP embeddings (if CLAP is enabled)
