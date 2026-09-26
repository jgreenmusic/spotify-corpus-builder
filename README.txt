Spotify Corpus Builder
======================

Downloads audio for every track in a Spotify CSV export (or a pasted list,
or YouTube links), then slices each one into a short grain for use as a
sample corpus.

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
  numpy and tkinterdnd2. It also checks that ffmpeg is available and installs it via
  Homebrew on Mac if missing.

  On macOS 14+ (Sonoma) or systems with Homebrew Python, setup.sh will
  automatically try fallback methods if the standard pip install is blocked.


HOW TO RUN
-----------

  Windows:   double-click spotify_corpus_builder.py
             or run:  python spotify_corpus_builder.py

  Mac:       run:  python3 spotify_corpus_builder.py

  Load your CSV using the Browse button, check the track list, adjust
  settings if needed, then click Start. The Log tab opens automatically
  and the bar at the bottom shows progress and the time remaining.

  To process only some tracks, search for them or select rows in the
  list (Ctrl/Cmd-click, Shift-click) before clicking Start.

  Your settings, the last CSV and your folders are remembered for next
  time. Theme and language are in the top-right corner.

  If it gets interrupted, just run it again -- it skips files that
  already exist.



IMPORTANT: HOW THE DOWNLOADS WORK
-----------------------------------

  This app does NOT use the Spotify API and does NOT download official
  Spotify audio.

  Instead, it searches YouTube for each track using the artist name and
  track title (e.g. "Psychic Mirrors - Ricky Thai"), looks at the top 5
  results, and downloads N seconds of the one that best matches the song:
  closest in length to the Spotify track, preferring official "Topic"
  uploads, and avoiding titles that say live, cover, remix, sped up etc.

  What this means in practice:

    - Most tracks will match correctly and give you the studio version.

    - Some tracks may still return a live recording, a cover version, a
      music video, or a fan upload. These show as [check] in the log with
      the reason, and are marked in metadata.json (version_flag).

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

  The app starts with example_playlist.csv, a 12-track example so you can
  try it without downloading hundreds of songs. To use your own music:

  1. Go to exportify.net
  2. Log in with Spotify
  3. Click Export next to any playlist or Liked Songs
  4. Save the CSV and load it in the app using the Browse button

  Your CSV must have "Track Name" and "Artist Name(s)" columns.
  Exportify produces exactly this format.


OTHER WAYS TO ADD TRACKS
--------------------------

  Paste...      Click Paste next to Browse and type or paste one
                "Artist - Title" per line. YouTube video or playlist
                links work too -- those exact videos are used.
                The list is saved as a CSV in "track lists" so it's
                remembered.

  Text file     Browse to (or drop) a .txt file in the same format.

  Drag & drop   Drop a CSV, a text file, or a folder of audio onto the
                window.


SETTINGS
---------

  Download length
    How many seconds of each track to download from YouTube (default: 30s).

  Parallel downloads
    How many tracks to download at the same time (default: 3, max 8).
    Higher is faster, but YouTube may start refusing requests.

  Start cut at
    How far into the preview to begin the grain (default: 5s in).

  Cut length
    How long each grain should be (default: 1.5s).

  Step 1 -- Download previews from YouTube
    Uncheck if you already have previews downloaded and only want to re-slice.

    Start ~1/3 into each song
      Downloads from about a third of the way into each song instead of the
      beginning, which skips quiet intros.

  Step 2 -- Slice into grains
    Uncheck if you only want the raw previews without slicing.

  Random sample -- pick N tracks at random from the CSV
    Check this and set a count to draw a random subset from your CSV instead
    of processing every track. Useful for testing with a large playlist
    without committing to the full run.

  Randomize cut per track -- duration min to max
    When checked, each track gets its own randomly chosen grain length
    (between your min and max) and a randomly chosen start point within
    the downloaded preview. Every run produces a different set of grains.

  Randomize button (in the action bar)
    Randomizes the Download length, Offset, Cut length, and AI checkboxes
    all at once. Good for quickly exploring different parameter combinations.

  Every grain gets a 5 ms fade in and out so it doesn't click. If a cut
  would run past the end of a file, it is moved back so the grain is
  always full length.

  Audio folder (optional)
    Browse to a folder of existing audio files (WAV, AIFF, FLAC, MP3, M4A,
    OGG, Opus) to feed directly into Step 2 without downloading anything. The app will slice those files using your
    current settings. When an audio folder is set, Step 1 is skipped even
    if checked.


AI ANALYSIS (Note to self: I'm not sure if the AI Analysis feature is functioning properly. I am not a professional coder and used Claude Code to help me realize this tool in it's entirety)
------------

  The AI section requires librosa, which setup.bat / setup.sh installs.
  The section header shows a checkmark when librosa is ready, or disables
  the checkboxes if it is missing.

  Smart grain selection
    Scores every possible cut for loudness (energy), number of note/drum
    onsets, and timbral movement (spectral), and cuts at the best moment.
    "auto" combines all three; the Strategy menu lets you pick just one.

  Pick the best YouTube match and flag likely wrong versions
    See "How the downloads work" above. Doesn't need librosa.

  Extract audio features
    Writes tempo, RMS energy, spectral centroid, zero crossing rate, and
    estimated key (major/minor) for each track into output/metadata.json.
    Spotify's own values from the CSV (key, tempo, energy, genres...) are
    saved there too and are more reliable, since Spotify measured the
    whole studio track.

  Cluster corpus by similarity
    After slicing, groups your grains by timbre, picks the number of
    groups automatically, and copies each group into
    output/grains_by_cluster/cluster_01, cluster_02, ...

  Analysis results are saved in metadata.json and reused next time, so
  only new files are analysed.

  CLAP embeddings (optional)
    Requires laion-clap (~2GB model download on first use). Produces a
    coords.json with 2D coordinates for each grain based on audio content,
    suitable for spatial corpus browsers.

  NOTE: The first time librosa's analysis runs, numba (its
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
    grains_by_cluster/  <-- grains grouped by similarity (if clustering is on)
    metadata.json    <-- per track: Spotify data, which YouTube video was
                         used, where the grain was cut, AI results
    coords.json      <-- CLAP embeddings (if CLAP is enabled)
