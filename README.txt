Spotify Corpus Builder
======================

Downloads audio for every track in a Spotify CSV export, then slices each
one into a short grain for use as a sample corpus.

What you get:
  output/previews/  --  one WAV per track (default: 30 seconds each)
  output/grains/    --  one short slice per track (default: 1.5 seconds,
                        starting 5 seconds into the preview)


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


CHANGING HOW IT WORKS
-----------------------

  Everything below can be adjusted in the Settings section of the app.
  You do not need to use the command line for any of this.

  Download length
    How many seconds of each track to download. The default is 30 seconds.
    You might lower this if you want smaller files and a faster run, or raise
    it if you want more of each track to work with before slicing.

  Start cut at
    How far into the downloaded preview to start your grain. The default is
    5 seconds in. The first few seconds of a track are often an intro or
    fade-in, so starting a little further in usually gives you a more
    representative slice of the song.

  Cut length
    How long each grain should be. The default is 1.5 seconds. Shorter grains
    work well for dense concatenative synthesis. Longer grains preserve more
    musical context and are better if you want recognizable fragments.

  Step 1 and Step 2 checkboxes
    You can run just the download, just the slicing, or both. This is useful
    if you already have previews downloaded and only want to re-slice them
    with different settings, without downloading everything again.


OUTPUT STRUCTURE
-----------------

  output/
    previews/        <-- downloaded WAVs (one per track)
      Artist - Track Name.wav
      ...
    grains/          <-- sliced grains (ready for corpus use)
      Artist - Track Name.wav
      ...


IF SOMETHING GOES WRONG
-------------------------

  The log area at the bottom of the app will tell you what happened and
  what to do. Error messages are written to explain the fix, not just
  describe the problem.

  What if the app won't open at all?
    Run setup.bat (Windows) or setup.sh (Mac). Something was not installed
    correctly. The setup script will find and fix it.

  What if a track shows [failed] in the log?
    yt-dlp could not find that track on YouTube. This is normal for obscure
    tracks. Everything else still downloads. You can run the app again and
    it will skip tracks that already finished.

  What if a downloaded file sounds wrong (live version, cover, etc.)?
    This is a known limitation of using YouTube search. The app has no way
    to guarantee the studio version comes back. See the "HOW THE DOWNLOADS
    WORK" section above for a full explanation.

  What if the app stops halfway through?
    Just run it again. It skips any track that already has a file in the
    output folder.

  What if the track list is empty after loading a CSV?
    The CSV columns are not in the expected format. The app will explain
    what it needs in the log. Exporting your playlist fresh from exportify.net
    will always produce the correct format.
