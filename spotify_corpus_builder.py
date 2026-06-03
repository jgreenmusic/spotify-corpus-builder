#!/usr/bin/env python3
"""
spotify_corpus_builder.py
Downloads preview clips for every track in a Spotify CSV export, then
slices each clip to a short grain for use as a corpus.

Prerequisites:
    pip install yt-dlp          (or: pip3 install yt-dlp on Mac)
    ffmpeg on PATH:
      Windows: winget install ffmpeg   (or download from https://ffmpeg.org)
      Mac:     brew install ffmpeg

Usage:
    Windows:  python  spotify_corpus_builder.py
    Mac/Linux: python3 spotify_corpus_builder.py

    python spotify_corpus_builder.py --csv my_songs.csv
    python spotify_corpus_builder.py --offset 8 --duration 2.0
    python spotify_corpus_builder.py --preview-length 15 --offset 3 --duration 1.0
    python spotify_corpus_builder.py --skip-download   # slice only (previews already downloaded)
    python spotify_corpus_builder.py --skip-slice      # download only

Options:
    --csv             Path to Spotify CSV export  (default: Liked_Songs.csv next to this script)
    --output          Output root folder           (default: ./output/ next to this script)
    --preview-length  Seconds to download per track (default: 30)
    --offset          Seconds into preview to start the grain (default: 5)
    --duration        Grain length in seconds      (default: 1.5)
    --skip-download   Skip download step (use existing previews)
    --skip-slice      Skip slice step
"""

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import yt_dlp
except ImportError:
    print("ERROR: yt-dlp not installed. Run: pip install yt-dlp")
    sys.exit(1)


# ── Helpers ───────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def sanitize(name: str) -> str:
    return re.sub(r'[/\\:*?"<>|]', "_", name).strip()[:150]


def ffmpeg_bin() -> str:
    """Find ffmpeg on PATH or common install locations."""
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    for candidate in [
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        os.path.join("C:\\", "ffmpeg", "bin", "ffmpeg.exe"),
    ]:
        if os.path.isfile(candidate):
            return candidate
    print("ERROR: ffmpeg not found. Install it and make sure it is on your PATH.")
    print("  macOS:   brew install ffmpeg")
    print("  Windows: winget install ffmpeg")
    print("  Linux:   sudo apt install ffmpeg")
    sys.exit(1)


# ── Step 1: Download ──────────────────────────────────────────────────────────

def download_track(artist: str, name: str, wav_path: str, preview_length: int) -> bool:
    primary_artist = artist.split(";")[0].strip()
    query = f"{primary_artist} - {name}"
    tmp_base = wav_path.replace(".wav", "_tmp")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": tmp_base + ".%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "download_ranges": yt_dlp.utils.download_range_func([], [[0, preview_length]]),
        "force_keyframes_at_cuts": True,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
        "postprocessor_args": {"ffmpegextractaudio": ["-ar", "44100", "-ac", "2"]},
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f"ytsearch1:{query}"])
        tmp_wav = tmp_base + ".wav"
        if os.path.exists(tmp_wav):
            shutil.move(tmp_wav, wav_path)
            return True
    except Exception:
        pass

    for ext in [".wav", ".webm", ".m4a", ".mp3", ".opus"]:
        f = tmp_base + ext
        if os.path.exists(f):
            try:
                os.remove(f)
            except OSError:
                pass

    return False


def run_download(csv_path: str, previews_dir: str, preview_length: int):
    os.makedirs(previews_dir, exist_ok=True)

    tracks = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name   = row.get("Track Name", "").strip()
            artist = row.get("Artist Name(s)", "").strip()
            if name and artist:
                tracks.append({"name": name, "artist": artist})

    print(f"\n=== DOWNLOAD ({len(tracks)} tracks -> {preview_length}s previews) ===")
    print(f"Output: {previews_dir}\n")

    downloaded = skipped = failed = 0

    for i, track in enumerate(tracks, 1):
        filename = sanitize(f"{track['artist']} - {track['name']}")
        wav_path = os.path.join(previews_dir, filename + ".wav")

        if os.path.exists(wav_path):
            print(f"  [{i}/{len(tracks)}] [exists]  {filename}.wav")
            skipped += 1
            continue

        print(f"  [{i}/{len(tracks)}] [fetch]   {track['artist']} - {track['name']}")
        if download_track(track["artist"], track["name"], wav_path, preview_length):
            print(f"  [{i}/{len(tracks)}] [done]    {filename}.wav")
            downloaded += 1
        else:
            print(f"  [{i}/{len(tracks)}] [failed]  {track['artist']} - {track['name']}")
            failed += 1

        time.sleep(1)

    print(f"\nDownload complete - downloaded: {downloaded}  skipped: {skipped}  failed: {failed}")


# ── Step 2: Slice ─────────────────────────────────────────────────────────────

def slice_preview(src: str, dst: str, offset: float, duration: float, ffmpeg: str) -> bool:
    cmd = [
        ffmpeg, "-y",
        "-ss", str(offset),
        "-t",  str(duration),
        "-i",  src,
        "-ar", "44100",
        "-ac", "2",
        dst,
    ]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0


def run_slice(previews_dir: str, grains_dir: str, offset: float, duration: float):
    os.makedirs(grains_dir, exist_ok=True)
    ffmpeg = ffmpeg_bin()

    wav_files = sorted(f for f in os.listdir(previews_dir) if f.lower().endswith(".wav"))
    total = len(wav_files)

    print(f"\n=== SLICE ({total} files -> offset {offset}s, grain {duration}s) ===")
    print(f"Output: {grains_dir}\n")

    done = skipped = failed = 0

    for i, fname in enumerate(wav_files, 1):
        src = os.path.join(previews_dir, fname)
        dst = os.path.join(grains_dir, fname)

        if os.path.exists(dst):
            print(f"  [{i}/{total}] [exists]  {fname}")
            skipped += 1
            continue

        if slice_preview(src, dst, offset, duration, ffmpeg):
            print(f"  [{i}/{total}] [sliced]  {fname}")
            done += 1
        else:
            print(f"  [{i}/{total}] [failed]  {fname}")
            failed += 1

    print(f"\nSlice complete - sliced: {done}  skipped: {skipped}  failed: {failed}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Download Spotify preview clips and slice them into short grains.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--csv",            default=os.path.join(SCRIPT_DIR, "Liked_Songs.csv"),
                        help="Path to Spotify CSV export (default: Liked_Songs.csv next to this script)")
    parser.add_argument("--output",         default=os.path.join(SCRIPT_DIR, "output"),
                        help="Root output folder (default: ./output/)")
    parser.add_argument("--preview-length", type=int,   default=30,
                        help="Seconds to download per track (default: 30)")
    parser.add_argument("--offset",         type=float, default=5.0,
                        help="Seconds into preview to start the grain (default: 5.0)")
    parser.add_argument("--duration",       type=float, default=1.5,
                        help="Grain length in seconds (default: 1.5)")
    parser.add_argument("--skip-download",  action="store_true",
                        help="Skip download, use existing previews")
    parser.add_argument("--skip-slice",     action="store_true",
                        help="Skip slicing step")
    args = parser.parse_args()

    previews_dir = os.path.join(args.output, "previews")
    grains_dir   = os.path.join(args.output, "grains")

    if not args.skip_download:
        if not os.path.exists(args.csv):
            print(f"ERROR: CSV not found at {args.csv}")
            print("Pass --csv <path> or place Liked_Songs.csv next to this script.")
            sys.exit(1)
        run_download(args.csv, previews_dir, args.preview_length)

    if not args.skip_slice:
        if not os.path.isdir(previews_dir):
            print(f"ERROR: previews folder not found at {previews_dir}")
            print("Run without --skip-download first.")
            sys.exit(1)
        run_slice(previews_dir, grains_dir, args.offset, args.duration)

    print(f"\nAll done.")
    print(f"  Previews: {previews_dir}")
    if not args.skip_slice:
        print(f"  Grains:   {grains_dir}")


if __name__ == "__main__":
    main()
