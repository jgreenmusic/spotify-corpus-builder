Spotify Corpus Builder
======================

Downloads audio for every track in a Spotify CSV export, then slices each
one into a short grain for use as a sample corpus.

What you get:
  output/previews/  --  one WAV per track (default: 30 seconds)
  output/grains/    --  one short slice per track (default: 1.5 seconds, starting 5 seconds in)


IMPORTANT: HOW THE DOWNLOADS WORK
-----------------------------------

  This app does NOT use the Spotify API and does NOT download official
  Spotify audio. Spotify's preview API is not publicly accessible.

  Instead, it searches YouTube for each track using the artist name and
  track title (e.g. "Radiohead - Paranoid Android"), then downloads the
  first N seconds of whatever YouTube returns.

  What this means in practice:

    - Most popular tracks will match correctly and give you the studio version.

    - Some tracks may return a live recording, a cover version, a music
      video, or a fan upload instead of the original studio recording.
      This is a limitation of relying on YouTube search results.

    - Very obscure tracks may not be found at all and will show [failed].

    - You are downloading publicly available audio from YouTube. Make sure
      this is appropriate for your use case.

  If a match looks wrong, you can check the filename -- it will be named
  after the artist and track from your CSV, regardless of what was actually
  downloaded.


WHAT YOU NEED BEFORE RUNNING
------------------------------

  Python 3          https://www.python.org/downloads/
                    Windows: check "Add Python to PATH" during install

  ffmpeg            handles audio conversion
                    Windows: run  winget install ffmpeg  in PowerShell
                    Mac:     run  brew install ffmpeg  in Terminal

  yt-dlp            downloads from YouTube (installed by the setup script)


SETUP (one time only)
----------------------

  Windows  --  double-click setup.bat

  Mac      --  open Terminal, navigate to this folder, then run:
                 chmod +x setup.sh
                 ./setup.sh

  This installs yt-dlp and customtkinter, and checks that ffmpeg is ready.
  It will show you exactly what is being installed before it starts.


HOW TO RUN
-----------

  Just double-click or run the script -- a window will open.

  Windows:   python  spotify_corpus_builder.py
  Mac:       python3 spotify_corpus_builder.py

  Load your CSV using the Browse button, check the track list, adjust
  settings if needed, then click Start. Nothing downloads until you
  click Start.

  If it gets interrupted, just run it again -- it skips files that
  already exist.


HOW TO EXPORT YOUR CSV FROM SPOTIFY
-------------------------------------

  The included Liked_Songs.csv is already set up. To export your own:

  1. Go to exportify.net
  2. Log in with Spotify
  3. Click Export next to any playlist or Liked Songs
  4. Save the CSV and load it in the app using the Browse button

  Your CSV must have "Track Name" and "Artist Name(s)" columns.
  Exportify produces exactly this format.


OPTIONS (command line / advanced use)
---------------------------------------

  --preview-length  How many seconds to download per track  (default: 30)
  --offset          Seconds into the preview to start the grain  (default: 5.0)
  --duration        Length of each grain in seconds  (default: 1.5)
  --csv             Path to a Spotify CSV file  (default: Liked_Songs.csv)
  --output          Where to save the files  (default: ./output/)
  --skip-download   Skip downloading, just slice existing previews
  --skip-slice      Download only, skip slicing

  Examples:

    Longer grains starting later in the preview:
      python spotify_corpus_builder.py --offset 10 --duration 3.0

    Download only (no slicing):
      python spotify_corpus_builder.py --skip-slice

    Use a different CSV:
      python spotify_corpus_builder.py --csv my_playlist.csv --output ./my_corpus/


OUTPUT STRUCTURE
-----------------

  output/
    previews/        <-- downloaded WAVs (one per track)
      Artist - Track Name.wav
      ...
    grains/          <-- sliced grains (ready for corpus use)
      Artist - Track Name.wav
      ...


TROUBLESHOOTING
----------------

  "yt-dlp not installed"
    Run: pip install yt-dlp  (Windows)  or  pip3 install yt-dlp  (Mac)

  "ffmpeg not found"
    Install ffmpeg and make sure it is on your PATH.
    Run setup.bat or setup.sh for step-by-step instructions.

  A track shows [failed]
    yt-dlp could not find it on YouTube. Normal for very obscure tracks.
    Everything else still downloads.

  A downloaded file sounds wrong (wrong version, live recording, etc.)
    This is a known limitation -- see "HOW THE DOWNLOADS WORK" above.
    YouTube search does not always return the studio version.

  Script stopped halfway
    Just run it again. It skips anything already downloaded.
