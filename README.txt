Spotify Corpus Builder
======================

Downloads a short audio preview for every track in your Spotify liked songs,
then slices each one into a grain for use as a sample corpus.

What you get:
  output/previews/  --  one 30-second WAV per track (from YouTube)
  output/grains/    --  one short slice per track (default: 1.5 seconds, starting 5 seconds in)


WHAT YOU NEED BEFORE RUNNING
------------------------------

  Python 3          https://www.python.org/downloads/
                    Windows: check "Add Python to PATH" during install

  ffmpeg            handles audio conversion
                    Windows: run  winget install ffmpeg  in PowerShell
                    Mac:     run  brew install ffmpeg  in Terminal

  yt-dlp            downloads from YouTube (installed by the setup script below)


SETUP (one time only)
----------------------

  Windows  --  double-click setup.bat

  Mac      --  open Terminal, navigate to this folder, then run:
                 chmod +x setup.sh
                 ./setup.sh

  This installs yt-dlp and checks that ffmpeg is ready.


HOW TO RUN
-----------

  Just double-click or run the script -- a window will open.

  Windows:   python  spotify_corpus_builder.py
  Mac:       python3 spotify_corpus_builder.py

  Load your CSV using the Browse button, check the track list, adjust settings
  if needed, then click Start. Nothing downloads until you click Start.

  If it gets interrupted, just run it again -- it skips files that already exist.


OPTIONS (command line / advanced use)
---------------------------------------

  --offset          Seconds into the preview to start the grain  (default: 5.0)
  --duration        Length of each grain in seconds              (default: 1.5)
  --preview-length  How many seconds to download per track       (default: 30)
  --csv             Path to a Spotify CSV file                   (default: Liked_Songs.csv)
  --output          Where to save the files                      (default: ./output/)
  --skip-download   Skip downloading, just slice existing previews
  --skip-slice      Download only, skip slicing

  Examples:

    Longer grains starting later in the preview:
      python spotify_corpus_builder.py --offset 10 --duration 3.0

    Download only (no slicing):
      python spotify_corpus_builder.py --skip-slice

    Use a different CSV:
      python spotify_corpus_builder.py --csv my_playlist.csv --output ./my_corpus/


HOW TO EXPORT YOUR CSV FROM SPOTIFY
-------------------------------------

  The included Liked_Songs.csv is already set up. To export your own:

  1. Go to exportify.net
  2. Log in with Spotify
  3. Click Export next to any playlist or Liked Songs
  4. Save the CSV and load it in the app using the Browse button


OUTPUT STRUCTURE
-----------------

  output/
    previews/        <-- full 30-second WAVs
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
    yt-dlp could not find it on YouTube. Normal for obscure tracks.
    Everything else still downloads.

  Script stopped halfway
    Just run it again. It skips anything already downloaded.
