#!/usr/bin/env python3
"""
spotify_corpus_builder.py
Downloads preview clips for every track in a Spotify CSV export, then
slices each clip to a short grain for use as a corpus.

Prerequisites:
    pip install yt-dlp customtkinter
    Optional: pip install tkinterdnd2   (drag and drop onto the window)
    ffmpeg on PATH:
      Windows: winget install ffmpeg
      Mac:     brew install ffmpeg

AI analysis (optional):
    pip install librosa scikit-learn soundfile numpy
    (included in setup.bat / setup.sh)

GUI usage (default):
    Windows:   python  spotify_corpus_builder.py
    Mac/Linux: python3 spotify_corpus_builder.py

CLI usage:
    python spotify_corpus_builder.py --csv my_songs.csv [--offset 8] [--duration 2.0]
    python spotify_corpus_builder.py --skip-download
    python spotify_corpus_builder.py --skip-slice
"""

import argparse
import csv
import json
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import yt_dlp
except ImportError:
    print("yt-dlp is not installed. Run setup.bat (Windows) or setup.sh (Mac) to fix this.")
    sys.exit(1)

# ── Helpers ───────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# In a PyInstaller build, bundled data lives in a temp folder that is deleted on
# exit, so config and output must go next to the executable instead.
if getattr(sys, "frozen", False):
    APP_DIR  = os.path.dirname(os.path.abspath(sys.executable))
    DATA_DIR = getattr(sys, "_MEIPASS", APP_DIR)
else:
    APP_DIR = DATA_DIR = SCRIPT_DIR

_TRACK_COLS    = ["Track Name", "track_name", "Song Name", "song_name", "Title", "title"]
_ARTIST_COLS   = ["Artist Name(s)", "Artist Name", "artist_name", "artists", "Artist", "artist"]
_URI_COLS      = ["Track URI", "track_uri", "uri", "spotify_uri"]
_DURATION_COLS = ["Duration (ms)", "duration_ms", "Track Duration (ms)"]
_YOUTUBE_COLS  = ["YouTube URL", "youtube_url"]

# Extra Exportify columns kept in metadata.json. Spotify measured these on the
# full studio track, so they are more reliable than anything estimated from a clip.
_SPOTIFY_TEXT_COLS = {"Album Name": "album", "Release Date": "release_date",
                      "Record Label": "label", "Genres": "genres"}
_SPOTIFY_NUM_COLS  = {"Popularity": "popularity", "Danceability": "danceability",
                      "Energy": "energy", "Key": "key", "Mode": "mode", "Loudness": "loudness",
                      "Speechiness": "speechiness", "Acousticness": "acousticness",
                      "Instrumentalness": "instrumentalness", "Liveness": "liveness",
                      "Valence": "valence", "Tempo": "tempo", "Time Signature": "time_signature"}
_PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

AUDIO_EXTS = (".wav", ".aif", ".aiff", ".flac", ".mp3", ".m4a", ".ogg", ".opus")


def sanitize(name: str) -> str:
    return re.sub(r'[/\\:*?"<>|]', "_", name).strip()[:150]


def track_filename(track: dict) -> str:
    """File stem used for a track's preview, grain and metadata entry."""
    if not track.get("artist"):
        return sanitize(track["name"])
    return sanitize(f"{track['artist']} - {track['name']}")


@lru_cache(maxsize=1)
def ffmpeg_bin() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in [
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        os.path.join("C:\\", "ffmpeg", "bin", "ffmpeg.exe"),
    ]:
        if os.path.isfile(candidate):
            return candidate
    raise RuntimeError(
        "ffmpeg is not installed or not found on your system.\n"
        "  Windows: open PowerShell and run   winget install ffmpeg\n"
        "  Mac:     open Terminal and run      brew install ffmpeg\n"
        "Then restart the app.")


def audio_duration(path: str):
    """Length of an audio file in seconds, or None if it can't be read."""
    try:
        import soundfile as sf
        return float(sf.info(path).duration)
    except Exception:
        pass
    try:
        import wave
        with wave.open(path) as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        pass
    try:   # mp3/m4a and other formats soundfile can't read
        probe = shutil.which("ffprobe")
        out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", path],
                             capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip())
    except Exception:
        return None


def _find_col(row: dict, candidates: list) -> str:
    for col in candidates:
        if col in row and row[col] is not None:
            return row[col]
    return ""


def _spotify_fields(row: dict) -> dict:
    out = {}
    for col, key in _SPOTIFY_TEXT_COLS.items():
        val = (row.get(col) or "").strip()
        if val:
            out[key] = [g.strip() for g in val.split(",")] if key == "genres" else val
    for col, key in _SPOTIFY_NUM_COLS.items():
        try:
            num = float(row.get(col) or "")
        except ValueError:
            continue
        out[key] = int(num) if num.is_integer() else num
    if isinstance(out.get("key"), int) and 0 <= out["key"] < 12:
        mode = {1: " major", 0: " minor"}.get(out.get("mode"), "")
        out["key_name"] = _PITCH_CLASSES[out["key"]] + mode
    return out


def read_tracks(csv_path: str) -> list:
    """Parse a playlist CSV (or a plain-text track list) into track dicts.

    Rows without a track name are skipped. YouTube links in a text file are
    kept as {"youtube_url": ...} placeholders; see expand_youtube_links().
    """
    if not csv_path.lower().endswith(".csv"):
        with open(csv_path, encoding="utf-8-sig", errors="replace") as f:
            tracks, urls = parse_track_lines(f.read())
        return tracks + [{"artist": "", "name": u, "youtube_url": u, "_unexpanded": True} for u in urls]
    tracks = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name   = _find_col(row, _TRACK_COLS).strip()
            artist = _find_col(row, _ARTIST_COLS).strip()
            if not name:
                continue
            track = {"artist": artist, "name": name}
            youtube_url = _find_col(row, _YOUTUBE_COLS).strip()
            if youtube_url:
                track["youtube_url"] = youtube_url
            uri = _find_col(row, _URI_COLS).strip()
            if uri:
                track["uri"] = uri
            try:
                track["duration_ms"] = int(float(_find_col(row, _DURATION_COLS)))
            except ValueError:
                pass
            spotify = _spotify_fields(row)
            if spotify:
                track["spotify"] = spotify
            tracks.append(track)
    return tracks


_YOUTUBE_LINK = re.compile(r"^https?://(www\.|m\.|music\.)?(youtube\.com|youtu\.be)/", re.I)
_LINE_SEPARATORS = (" - ", " – ", " — ", "\t")


def parse_track_lines(text: str) -> tuple:
    """Parse pasted text: one "Artist - Title" per line (or tab-separated), or a
    YouTube link. Lines starting with # are ignored. Returns (tracks, urls)."""
    tracks, urls = [], []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if _YOUTUBE_LINK.match(line):
            urls.append(line)
            continue
        for sep in _LINE_SEPARATORS:
            if sep in line:
                artist, name = line.split(sep, 1)
                if name.strip():
                    tracks.append({"artist": artist.strip(), "name": name.strip()})
                break
        else:
            tracks.append({"artist": "", "name": line})
    return tracks, urls


_TITLE_JUNK = re.compile(
    r"\s*[\(\[](official\s*(music\s*)?(video|audio|visuali[sz]er|lyric video)|lyrics?( video)?|"
    r"audio|visuali[sz]er|hd|hq|4k|m/?v)[\)\]]\s*", re.I)


def _split_youtube_title(title: str, channel: str) -> tuple:
    """Guess (artist, track name) from a YouTube title and channel."""
    title = _TITLE_JUNK.sub(" ", title or "").strip()
    channel = (channel or "").strip()
    if channel.endswith(" - Topic"):          # auto-generated: title is the track name
        return channel[:-8], title
    for sep in _LINE_SEPARATORS[:3]:
        if sep in title:
            artist, name = title.split(sep, 1)
            return artist.strip(), name.strip()
    return channel, title


def expand_youtube_links(urls: list) -> list:
    """Turn YouTube video/playlist links into tracks that download from that exact video."""
    tracks = []
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
            "logger": _QuietLogger()}
    for url in urls:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False) or {}
        except Exception as e:
            print(f"  Could not read {url}: {_short_error(e)}")
            continue
        entries = [e for e in (info.get("entries") or [info]) if e]
        for e in entries:
            if not e.get("id") and not e.get("url"):
                continue
            artist, name = _split_youtube_title(e.get("title"), e.get("channel") or e.get("uploader"))
            if not name or name in ("[Private video]", "[Deleted video]"):
                continue
            track = {"artist": artist, "name": name,
                     "youtube_url": e.get("webpage_url") or e.get("url")
                                    or f"https://www.youtube.com/watch?v={e['id']}"}
            if e.get("duration"):
                track["duration_ms"] = int(float(e["duration"]) * 1000)
            tracks.append(track)
        print(f"  {url}: {len(entries)} video(s)")
    return tracks


def write_tracks_csv(path: str, tracks: list):
    """Save a track list in the same column layout as an Exportify CSV."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Track Name", "Artist Name(s)", "Duration (ms)", "YouTube URL"])
        for t in tracks:
            writer.writerow([t["name"], t.get("artist", ""), t.get("duration_ms", ""),
                             t.get("youtube_url", "")])


def _list_audio(folder: str) -> list:
    return sorted(f for f in os.listdir(folder) if f.lower().endswith(AUDIO_EXTS))


def _stem(fname: str) -> str:
    return os.path.splitext(fname)[0]


# ── Step 1: Download ──────────────────────────────────────────────────────────

class _QuietLogger:
    """Stops yt-dlp printing its own copy of errors; download_track reports them instead."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def _short_error(e: Exception) -> str:
    lines = str(e).strip().splitlines()
    msg = re.sub(r"^ERROR:\s*", "", lines[0]) if lines else type(e).__name__
    msg = re.split(r" \(caused by |; please report this issue", msg)[0]
    return msg if len(msg) <= 160 else msg[:157] + "..."


# Words in a YouTube title that suggest a different version than the studio track,
# unless the Spotify track name contains them too.
_VERSION_WORDS = ["live", "cover", "remix", "sped up", "slowed", "reverb", "nightcore",
                  "karaoke", "instrumental", "8d", "acoustic", "reaction", "tutorial",
                  "extended", "mashup", "bass boosted"]


def score_candidate(entry: dict, track: dict) -> tuple:
    """Score a YouTube search result against a CSV track. Returns (score, flags)."""
    title = (entry.get("title") or "").lower()
    name  = track["name"].lower()
    flags = [w for w in _VERSION_WORDS
             if re.search(rf"\b{re.escape(w)}\b", title)
             and not re.search(rf"\b{re.escape(w)}\b", name)]
    score = -3.0 * len(flags)

    got, want = entry.get("duration"), track.get("duration_ms")
    if got and want:
        diff = abs(float(got) - want / 1000.0)
        score -= min(diff, 120.0) / 10.0          # 10 s off costs one point
        if diff > 15:
            flags.append(f"length off by {diff:.0f}s")

    channel = (entry.get("channel") or entry.get("uploader") or "").lower()
    if channel.endswith(" - topic"):              # YouTube's auto-generated studio uploads
        score += 2.0
    primary_artist = track["artist"].split(";")[0].strip().lower()
    if primary_artist and primary_artist in channel:
        score += 1.0
    return score, flags


def _search_youtube(query: str, n: int) -> list:
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
            "logger": _QuietLogger()}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
    return [e for e in (info or {}).get("entries") or [] if e]


def _download_start(track: dict, entry: dict, preview_length: int, from_middle: bool) -> float:
    """Where to start the download: 0, or about a third of the way into the song."""
    if not from_middle:
        return 0.0
    length = entry.get("duration") or (track.get("duration_ms") or 0) / 1000.0
    if not length or length <= preview_length:
        return 0.0
    return round(min(length * 0.33, length - preview_length), 1)


def download_track(track: dict, wav_path: str, preview_length: int,
                   match_versions: bool = True, from_middle: bool = False) -> tuple:
    """Search YouTube, pick the best result, download part of it as WAV.

    Returns (ok, error_message, info). info describes the chosen video.
    """
    primary_artist = track["artist"].split(";")[0].strip()
    query = f"{primary_artist} - {track['name']}" if primary_artist else track["name"]
    tmp_base = wav_path[:-4] + "_tmp"
    info = {}

    try:
        if track.get("youtube_url"):     # a specific video was given; don't search
            duration = (track.get("duration_ms") or 0) / 1000.0 or None
            best, flags = {"url": track["youtube_url"], "title": track["name"],
                           "duration": duration}, []
            match_versions = False
        else:
            candidates = _search_youtube(query, 5 if match_versions else 1)
            if not candidates:
                return False, "no YouTube results", info
            scored = [(score_candidate(e, track), i, e) for i, e in enumerate(candidates)]
            (_, flags), _, best = max(scored, key=lambda s: (s[0][0], -s[1]))
        url = best.get("url") or best.get("webpage_url") or f"https://www.youtube.com/watch?v={best['id']}"
        start = _download_start(track, best, preview_length, from_middle)
        info = {"youtube_url": url, "youtube_title": best.get("title"),
                "youtube_channel": best.get("channel") or best.get("uploader"),
                "download_start": start}
        if match_versions:
            info["version_flag"] = ", ".join(flags) if flags else "ok"
        info = {k: v for k, v in info.items() if v is not None}

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": tmp_base + ".%(ext)s",
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "logger": _QuietLogger(),
            "ffmpeg_location": ffmpeg_bin(),
            "download_ranges": yt_dlp.utils.download_range_func([], [[start, start + preview_length]]),
            "force_keyframes_at_cuts": True,
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
            "postprocessor_args": {"ffmpegextractaudio": ["-ar", "44100", "-ac", "2"]},
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        tmp_wav = tmp_base + ".wav"
        if os.path.exists(tmp_wav):
            shutil.move(tmp_wav, wav_path)
            return True, "", info
        err = "no audio was produced"
    except Exception as e:
        err = _short_error(e)

    for ext in [".wav", ".webm", ".m4a", ".mp3", ".opus", ".part"]:
        f = tmp_base + ext
        if os.path.exists(f):
            try:
                os.remove(f)
            except OSError:
                pass
    return False, err, info


def run_download(tracks: list, previews_dir: str, preview_length: int,
                 stop_event: threading.Event = None, metadata: dict = None,
                 match_versions: bool = True, from_middle: bool = False,
                 workers: int = 3, progress=None):
    os.makedirs(previews_dir, exist_ok=True)
    stop_event = stop_event or threading.Event()
    metadata = metadata if metadata is not None else {}
    workers = max(1, int(workers))

    todo, seen, skipped = [], set(), 0
    for track in tracks:
        filename = track_filename(track)
        if filename in seen:
            continue
        seen.add(filename)
        if os.path.exists(os.path.join(previews_dir, filename + ".wav")):
            skipped += 1
        else:
            todo.append(track)

    where = "from ~1/3 into each song" if from_middle else "from the start"
    print(f"\n=== DOWNLOAD ({len(todo)} to fetch, {skipped} already downloaded -> "
          f"{preview_length}s previews, {where}, {workers} at a time) ===")
    print(f"Output: {previews_dir}\n")
    downloaded = failed = flagged = 0
    fails_in_a_row = 0
    hinted = False

    def job(track):
        if stop_event.is_set():
            return track, None
        wav_path = os.path.join(previews_dir, track_filename(track) + ".wav")
        result = download_track(track, wav_path, preview_length,
                                match_versions=match_versions, from_middle=from_middle)
        stop_event.wait(0.5)  # small pause per worker so YouTube isn't hammered
        return track, result

    total = len(todo)
    if progress:
        progress("download", 0, total)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(job, t) for t in todo]
        for n, fut in enumerate(as_completed(futures), 1):
            track, result = fut.result()
            if progress:
                progress("download", n, total)
            if result is None:          # skipped because Stop was pressed
                continue
            ok, err, info = result
            filename = track_filename(track)
            if ok:
                metadata.setdefault(filename, {}).update(info)
                flag = info.get("version_flag", "ok")
                if flag != "ok":
                    flagged += 1
                    print(f"  [{n}/{total}] [check]   {filename}.wav  — {flag}  "
                          f"(got: {info.get('youtube_title')})")
                else:
                    print(f"  [{n}/{total}] [done]    {filename}.wav")
                downloaded += 1
                fails_in_a_row = 0
            else:
                print(f"  [{n}/{total}] [failed]  {filename}  ({err})")
                failed += 1
                fails_in_a_row += 1
                if fails_in_a_row >= 5 and not hinted:
                    print("  Several downloads in a row have failed. YouTube may have changed something;\n"
                          "  try updating yt-dlp:   python -m pip install -U yt-dlp")
                    hinted = True

    if stop_event.is_set():
        print("\nStopped by user.")
    print(f"\nDownload complete - downloaded: {downloaded}  skipped: {skipped}  failed: {failed}")
    if flagged:
        print(f"{flagged} downloads may be the wrong version — see [check] lines above, "
              f"or 'version_flag' in metadata.json.")


# ── Step 2: Slice ─────────────────────────────────────────────────────────────

def _slice_with_soundfile(src: str, dst: str, offset: float, duration: float,
                          fade_ms: float):
    """Cut a grain in-process. Returns None if the file needs ffmpeg instead
    (soundfile missing, or not 44.1 kHz mono/stereo, which ffmpeg converts)."""
    try:
        import numpy as np
        import soundfile as sf
        info = sf.info(src)
    except Exception:
        return None
    if info.samplerate != 44100 or info.channels not in (1, 2):
        return None
    sr = info.samplerate
    data, _ = sf.read(src, start=int(round(offset * sr)), frames=int(round(duration * sr)),
                      dtype="float32", always_2d=True)
    if len(data) == 0:
        return False
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    fade = min(int(fade_ms / 1000.0 * sr), len(data) // 4)
    if fade > 0:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)[:, None]
        data[:fade] *= ramp
        data[-fade:] *= ramp[::-1]
    sf.write(dst, data, sr, subtype="PCM_16")
    return True


def slice_preview(src: str, dst: str, offset: float, duration: float,
                  fade_ms: float = 5.0) -> bool:
    try:
        ok = _slice_with_soundfile(src, dst, offset, duration, fade_ms)
        if ok is not None:
            return ok
    except Exception:
        pass    # unreadable by soundfile; let ffmpeg try

    cmd = [ffmpeg_bin(), "-y", "-ss", f"{offset:.3f}", "-t", f"{duration:.3f}", "-i", src]
    fade = min(fade_ms / 1000.0, duration / 4)
    if fade > 0:
        cmd += ["-af", f"afade=t=in:st=0:d={fade:.4f},"
                       f"afade=t=out:st={duration - fade:.4f}:d={fade:.4f}"]
    cmd += ["-ar", "44100", "-ac", "2", dst]
    ok = subprocess.run(cmd, capture_output=True).returncode == 0
    # A WAV header alone is 44 bytes; anything that small means no audio was cut.
    if ok and os.path.getsize(dst) <= 44:
        os.remove(dst)
        ok = False
    return ok


def run_slice(previews_dir: str, grains_dir: str, files: list, offset: float, duration: float,
              stop_event: threading.Event = None, metadata: dict = None,
              use_smart: bool = False, randomize_cut: bool = False,
              dur_min: float = 0.5, dur_max: float = 3.0, fade_ms: float = 5.0,
              progress=None):
    os.makedirs(grains_dir, exist_ok=True)
    stop_event = stop_event or threading.Event()
    metadata = metadata if metadata is not None else {}
    total = len(files)

    if randomize_cut:
        print(f"\n=== SLICE ({total} files -> random cut, duration {dur_min}–{dur_max}s) ===")
    else:
        print(f"\n=== SLICE ({total} files -> offset {offset}s, grain {duration}s) ===")
    if use_smart:
        print("  Smart grain selection ON — using AI-suggested offsets where available.")
    print(f"Output: {grains_dir}\n")
    done = skipped = failed = 0

    for i, fname in enumerate(files, 1):
        if stop_event.is_set():
            print("\nStopped by user.")
            break
        if progress:
            progress("slice", i - 1, total)
        src = os.path.join(previews_dir, fname)
        dst = os.path.join(grains_dir, _stem(fname) + ".wav")
        if os.path.exists(dst):
            print(f"  [{i}/{total}] [exists]  {fname}")
            skipped += 1
            continue

        key = _stem(fname)
        length = audio_duration(src)

        if randomize_cut:
            cut_dur = random.uniform(dur_min, dur_max)
            if length is not None:
                cut_dur = min(cut_dur, length)
            cut_off = random.uniform(0.0, max(0.0, (length or cut_dur) - cut_dur))
        else:
            cut_off, cut_dur = offset, duration
            if use_smart:
                suggested = metadata.get(key, {}).get("suggested_offset")
                if suggested is not None:
                    cut_off = suggested

        if length is not None and cut_off + cut_dur > length:
            cut_dur = min(cut_dur, length)
            cut_off = max(0.0, length - cut_dur)
            print(f"  [{i}/{total}] [note]    file is only {length:.1f}s — cut moved to {cut_off:.2f}s")

        if slice_preview(src, dst, cut_off, cut_dur, fade_ms):
            print(f"  [{i}/{total}] [sliced]  {fname}  @ {cut_off:.2f}s, {cut_dur:.2f}s")
            metadata.setdefault(key, {})["grain"] = {
                "offset": round(cut_off, 3), "duration": round(cut_dur, 3)}
            done += 1
        else:
            print(f"  [{i}/{total}] [failed]  {fname}")
            failed += 1

    if progress and not stop_event.is_set():
        progress("slice", total, total)
    print(f"\nSlice complete - sliced: {done}  skipped: {skipped}  failed: {failed}")


# ── AI Analysis ───────────────────────────────────────────────────────────────

GRAIN_STRATEGIES = ["auto", "energy", "onsets", "spectral"]
_ANALYSIS_SR = 22050   # plenty for these features and ~2x faster than 44.1k
_HOP = 512


def _librosa_available() -> bool:
    try:
        import librosa  # noqa: F401
        return True
    except ImportError:
        return False


def _smart_grain_tag(ai_opts: dict) -> str:
    return f"{ai_opts.get('duration', 1.5)}|{ai_opts.get('grain_strategy', 'auto')}"


def _needs_analysis(entry: dict, ai_opts: dict) -> bool:
    return bool(
        (ai_opts.get("extract_features") and "features" not in entry)
        or (ai_opts.get("smart_grain") and entry.get("suggested_offset_for") != _smart_grain_tag(ai_opts)))


def _window_means(x, win: int):
    """Mean of every length-`win` window of x (window i covers x[i:i+win])."""
    import numpy as np
    c = np.concatenate([[0.0], np.cumsum(x, dtype=float)])
    return (c[win:] - c[:-win]) / win


# Krumhansl-Schmuckler key profiles
_KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def estimate_key(chroma_mean) -> str:
    """Best-matching major/minor key for a 12-bin mean chroma vector."""
    import numpy as np
    best = (-2.0, "")
    for mode, profile in (("major", _MAJOR_PROFILE), ("minor", _MINOR_PROFILE)):
        for tonic in range(12):
            r = float(np.corrcoef(np.roll(profile, tonic), chroma_mean)[0, 1])
            if r > best[0]:
                best = (r, f"{_KEY_NAMES[tonic]} {mode}")
    return best[1]


def analyze_file(wav_path: str, features: bool = False, grain_duration: float = None,
                 strategy: str = "auto") -> dict:
    """Load a file once and compute everything requested from the same frames."""
    import librosa
    import numpy as np
    import warnings
    with warnings.catch_warnings():
        # mp3/m4a fall back from soundfile to audioread, which librosa warns about
        warnings.simplefilter("ignore")
        y, sr = librosa.load(wav_path, sr=_ANALYSIS_SR, mono=True)
    if len(y) == 0:
        return {}
    rms      = librosa.feature.rms(y=y, hop_length=_HOP)[0]
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=_HOP)[0]
    onset    = librosa.onset.onset_strength(y=y, sr=sr, hop_length=_HOP)
    n = min(len(rms), len(centroid), len(onset))
    rms, centroid, onset = rms[:n], centroid[:n], onset[:n]
    out = {}

    if features:
        tempo = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=_HOP)[0]
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=_HOP)
        out["features"] = {
            "tempo": round(float(np.atleast_1d(tempo)[0]), 1),
            "rms_energy": float(rms.mean()),
            "spectral_centroid": float(centroid.mean()),
            "zero_crossing_rate": float(librosa.feature.zero_crossing_rate(y, hop_length=_HOP)[0].mean()),
            "estimated_key": estimate_key(chroma.mean(axis=1)),
        }

    if grain_duration:
        out["suggested_offset"], out["grain_strategy"] = _best_grain(
            y, sr, rms, centroid, onset, grain_duration, strategy)
    return out


def _best_grain(y, sr, rms, centroid, onset_env, duration: float, strategy: str) -> tuple:
    """Pick the start time of the most interesting `duration`-second window.

    energy   = loudest window
    onsets   = most note/drum onsets
    spectral = most timbral movement (variance of spectral centroid)
    auto     = sum of all three, each standardised so none dominates
    """
    import librosa
    import numpy as np
    total = len(y) / sr
    if total <= duration:
        return 0.0, "whole file"
    win = max(1, int(round(duration * sr / _HOP)))
    n = len(rms)
    margin = 2.0 if total >= duration + 4.0 else 0.0   # avoid fade-ins/outs at the edges
    first = int(margin * sr / _HOP)
    last = min(int((total - margin - duration) * sr / _HOP), n - win)
    if last <= first:
        return round((total - duration) / 2, 3), "middle"

    onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, hop_length=_HOP)
    onset_mask = np.zeros(n)
    onset_mask[onset_frames[onset_frames < n]] = 1.0
    m1 = _window_means(centroid, win)
    curves = {
        "energy":   _window_means(rms, win),
        "onsets":   _window_means(onset_mask, win),
        "spectral": np.maximum(_window_means(centroid ** 2, win) - m1 ** 2, 0.0),
    }
    span = slice(first, last + 1)
    if strategy in curves:
        score = curves[strategy][span]
    else:
        strategy = "auto"
        score = np.zeros(last + 1 - first)
        for c in curves.values():
            c = c[span]
            sd = c.std()
            if sd > 0:
                score += (c - c.mean()) / sd
    best = first + int(np.argmax(score))
    return round(best * _HOP / sr, 3), strategy


def _run_ai_on_track(wav_path: str, key: str, ai_opts: dict, metadata: dict):
    """Fill in whichever requested analysis results this track's entry is missing."""
    entry = metadata.setdefault(key, {})
    want_feats = bool(ai_opts.get("extract_features")) and "features" not in entry
    want_grain = bool(ai_opts.get("smart_grain")) and \
        entry.get("suggested_offset_for") != _smart_grain_tag(ai_opts)
    try:
        result = analyze_file(wav_path, features=want_feats,
                              grain_duration=ai_opts.get("duration", 1.5) if want_grain else None,
                              strategy=ai_opts.get("grain_strategy", "auto"))
    except Exception as e:
        print(f"  [AI] could not analyse {key[:50]}: {_short_error(e)}")
        return
    if "features" in result:
        entry["features"] = feats = result["features"]
        print(f"  [AI] tempo={feats['tempo']:.0f}  key={feats['estimated_key']}")
    if "suggested_offset" in result:
        entry["suggested_offset"] = result["suggested_offset"]
        entry["suggested_offset_for"] = _smart_grain_tag(ai_opts)
        print(f"  [AI] best grain at {result['suggested_offset']:.1f}s [{result['grain_strategy']}]")


def run_analysis(previews_dir: str, files: list, ai_opts: dict, metadata: dict,
                 stop_event: threading.Event = None, progress=None):
    """Analyse every file whose metadata is missing a requested result.

    Runs as its own pass so it works the same whether the audio was just
    downloaded, downloaded in an earlier session, or came from an audio folder.
    """
    if not (ai_opts.get("smart_grain") or ai_opts.get("extract_features")):
        return
    if not _librosa_available():
        print("  [AI] librosa is not installed. Run setup.bat or setup.sh to enable AI analysis.")
        return
    stop_event = stop_event or threading.Event()
    todo = [f for f in files if _needs_analysis(metadata.get(_stem(f), {}), ai_opts)]
    print(f"\n=== ANALYSE ({len(todo)} of {len(files)} files need analysis) ===")
    if todo:
        print("  (The first file can take 30–60s while librosa warms up.)")
    if progress:
        progress("analyse", 0, len(todo))
    for i, fname in enumerate(todo, 1):
        if stop_event.is_set():
            print("\nStopped by user.")
            break
        print(f"  [{i}/{len(todo)}] {fname}")
        _run_ai_on_track(os.path.join(previews_dir, fname), _stem(fname), ai_opts, metadata)
        if progress:
            progress("analyse", i, len(todo))


def _grain_features(path: str):
    """Timbre summary of a grain: MFCC means/stds plus brightness, loudness, noisiness."""
    import librosa
    import numpy as np
    y, sr = librosa.load(path, sr=_ANALYSIS_SR, mono=True)   # grains are always WAV
    if len(y) < _HOP:
        return None
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13, hop_length=_HOP)
    return np.concatenate([
        mfcc.mean(axis=1), mfcc.std(axis=1),
        [librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=_HOP).mean(),
         librosa.feature.rms(y=y, hop_length=_HOP).mean(),
         librosa.feature.spectral_flatness(y=y, hop_length=_HOP).mean()],
    ])


def cluster_corpus(grains_dir: str, max_clusters: int = 8) -> dict:
    """Group grains by timbre. The number of groups is chosen by silhouette score."""
    try:
        import numpy as np
        from sklearn.cluster import KMeans
        from sklearn.metrics import silhouette_score
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        print("  [AI] librosa or scikit-learn not installed. Run setup.bat or setup.sh.")
        return {}
    try:
        wav_files = _list_audio(grains_dir)
        if not wav_files:
            return {}
        print(f"\n=== CLUSTER ({len(wav_files)} grains) ===")
        rows, names = [], []
        for fname in wav_files:
            try:
                feats = _grain_features(os.path.join(grains_dir, fname))
            except Exception:
                feats = None
            if feats is not None:
                rows.append(feats)
                names.append(fname)

        if len(names) < 4:
            return {f: 0 for f in names}

        X = StandardScaler().fit_transform(np.array(rows))
        best = None
        for k in range(2, min(max_clusters, len(names) - 1) + 1):
            labels = KMeans(n_clusters=k, n_init=10, random_state=42).fit_predict(X)
            score = silhouette_score(X, labels, sample_size=min(len(names), 2000), random_state=42)
            if best is None or score > best[0]:
                best = (score, k, labels)
        score, k, labels = best
        print(f"  [AI] Clustered into {k} groups (silhouette {score:.2f}; higher = better separated).")
        return {fname: int(label) for fname, label in zip(names, labels)}
    except Exception as e:
        print(f"  [AI] Clustering failed: {e}")
        return {}


def export_cluster_folders(grains_dir: str, output_root: str, clusters: dict):
    """Copy grains into grains_by_cluster/cluster_N/ so each group can be loaded on its own."""
    dest = os.path.join(output_root, "grains_by_cluster")
    shutil.rmtree(dest, ignore_errors=True)
    for fname, cid in clusters.items():
        folder = os.path.join(dest, f"cluster_{cid + 1:02d}")
        os.makedirs(folder, exist_ok=True)
        shutil.copy2(os.path.join(grains_dir, fname), os.path.join(folder, fname))
    print(f"  [AI] Grains copied into cluster folders: {dest}")


def run_clap_analysis(grains_dir: str, output_dir: str) -> None:
    try:
        import laion_clap
    except ImportError:
        print("  [CLAP] laion-clap is not installed. Install it separately and re-run.")
        print("         Note: CLAP requires a 2GB model download on first use.")
        return
    try:
        model = laion_clap.CLAP_Module(enable_fusion=False)
        model.load_ckpt()
        wav_files = sorted(f for f in os.listdir(grains_dir) if f.lower().endswith(".wav"))
        paths = [os.path.join(grains_dir, f) for f in wav_files]
        if not paths:
            print("  [CLAP] No grains found.")
            return
        print(f"  [CLAP] Embedding {len(paths)} grains...")
        embeddings = model.get_audio_embedding_from_filelist(paths, use_tensor=False)
        try:
            from umap import UMAP
            coords = UMAP(n_components=2, random_state=42).fit_transform(embeddings)
        except ImportError:
            from sklearn.decomposition import PCA
            coords = PCA(n_components=2).fit_transform(embeddings)
        result = {fname: {"x": float(coords[i, 0]), "y": float(coords[i, 1])}
                  for i, fname in enumerate(wav_files)}
        out_path = os.path.join(output_dir, "coords.json")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
        print(f"  [CLAP] coords.json saved to {out_path}")
    except Exception as e:
        print(f"  [CLAP] Error: {e}")


def load_metadata(output_dir: str) -> dict:
    try:
        with open(os.path.join(output_dir, "metadata.json"), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def run_pipeline(output_root: str, tracks: list = None, audio_folder: str = "",
                 preview_length: int = 30, offset: float = 5.0, duration: float = 1.5,
                 do_download: bool = True, do_slice: bool = True,
                 randomize_cut: bool = False, dur_min: float = 0.5, dur_max: float = 3.0,
                 fade_ms: float = 5.0, seed=None, match_versions: bool = True,
                 from_middle: bool = False, workers: int = 3, ai_opts: dict = None,
                 stop_event: threading.Event = None, progress=None):
    """Download -> analyse -> slice -> cluster. Shared by the GUI and the CLI.

    With an audio_folder, every audio file in it is used and nothing is downloaded.
    Otherwise only the given tracks are downloaded, analysed and sliced.
    progress, if given, is called as progress(stage, done, total).
    """
    stop_event = stop_event or threading.Event()
    ai_opts = dict(ai_opts or {})
    ai_opts["duration"] = duration
    tracks = tracks or []
    grains_dir = os.path.join(output_root, "grains")

    if seed is not None:
        random.seed(seed)
        print(f"Random seed: {seed}")
    if randomize_cut and ai_opts.get("smart_grain"):
        print("Randomize cut is on, so smart grain selection is skipped for this run.")
        ai_opts["smart_grain"] = False

    links = [t["youtube_url"] for t in tracks if t.get("_unexpanded")]
    if links and not audio_folder:
        print(f"Reading {len(links)} YouTube link(s)…")
        tracks = [t for t in tracks if not t.get("_unexpanded")] + expand_youtube_links(links)

    metadata = load_metadata(output_root)

    if audio_folder:
        previews_dir = audio_folder
        print(f"Audio folder set — skipping download, using files in: {audio_folder}")
        files = _list_audio(audio_folder) if os.path.isdir(audio_folder) else []
    else:
        previews_dir = os.path.join(output_root, "previews")
        if do_download:
            ffmpeg_bin()   # fail once with a clear message rather than on every track
            run_download(tracks, previews_dir, preview_length, stop_event, metadata=metadata,
                         match_versions=match_versions, from_middle=from_middle,
                         workers=workers, progress=progress)
        files, seen = [], set()
        for t in tracks:
            key = track_filename(t)
            if key in seen or not os.path.exists(os.path.join(previews_dir, key + ".wav")):
                continue
            seen.add(key)
            files.append(key + ".wav")
            entry = metadata.setdefault(key, {})
            entry.update({k: t[k] for k in ("artist", "name", "uri", "duration_ms", "spotify") if k in t})

    if not files:
        print("No audio files to process." +
              ("" if audio_folder or do_download else "  Enable Download, or set an audio folder."))
        return

    if not stop_event.is_set():
        run_analysis(previews_dir, files, ai_opts, metadata, stop_event, progress)

    if do_slice and not stop_event.is_set():
        run_slice(previews_dir, grains_dir, files, offset, duration, stop_event,
                  metadata=metadata, use_smart=bool(ai_opts.get("smart_grain")),
                  randomize_cut=randomize_cut, dur_min=dur_min, dur_max=dur_max,
                  fade_ms=fade_ms, progress=progress)

    if not stop_event.is_set() and ai_opts.get("cluster") and os.path.isdir(grains_dir):
        clusters = cluster_corpus(grains_dir)
        for fname, cluster_id in clusters.items():
            metadata.setdefault(_stem(fname), {})["cluster"] = cluster_id
        if clusters:
            export_cluster_folders(grains_dir, output_root, clusters)

    if not stop_event.is_set() and ai_opts.get("clap") and os.path.isdir(grains_dir):
        run_clap_analysis(grains_dir, output_root)

    if metadata:
        save_metadata(output_root, metadata)

    print(f"\nAll done.  Previews: {previews_dir}  |  Grains: {grains_dir}")


def save_metadata(output_dir: str, metadata: dict) -> None:
    try:
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, "metadata.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
        print(f"  [AI] metadata.json saved to {path}")
    except Exception as e:
        print(f"  [AI] Could not save metadata: {e}")


# ── Translations ──────────────────────────────────────────────────────────────
# English is the source text and the fallback for any key a translation lacks.
# Other languages live in translations.json.

TRANSLATIONS = {
    "English": {
        "window_title": "Spotify Corpus Builder",
        "app_description": "Load a Spotify CSV, download audio previews from YouTube, and slice them into short grains for use as a sample corpus.",
        "files_section": "FILES",
        "language_label": "Language",
        "theme_label": "Theme",
        "csv_label": "Track list",
        "csv_hint": "A Spotify playlist exported from exportify.net (free), or a text file with one “Artist - Title” per line.",
        "csv_error_cols": "No tracks found. Make sure your CSV has 'Track Name' and 'Artist Name(s)' columns.\nExport from Spotify using exportify.net (free, no install needed).",
        "save_label": "Save to",
        "browse_btn": "Browse",
        "audio_label": "Audio folder",
        "audio_hint": "Optional: use existing audio files (WAV, AIFF, FLAC, MP3…) instead of downloading. Leave empty to download from the track list.",
        "paste_btn": "Paste…",
        "paste_title": "Paste a track list",
        "paste_hint": "One track per line as “Artist - Title”. YouTube video and playlist links work too; their videos are used directly.",
        "load_btn": "Load tracks",
        "cancel_btn": "Cancel",
        "drop_hint": "Tip: you can also drag a CSV, a text file or a folder of audio onto this window.",
        "youtube_reading": "Reading {n} YouTube link(s)…",
        "import_empty": "No tracks found in that list.",
        "tracks_section": "TRACKS",
        "search_placeholder": "Search artist or track…",
        "no_csv_msg": "No CSV loaded",
        "status_loaded": "{n} tracks loaded",
        "status_filtered": "{n} of {m} tracks",
        "selection_hint": "Select rows to process only those; otherwise every track matching the search is used.",
        "artist_col": "Artist",
        "track_col": "Track",
        "settings_section": "SETTINGS",
        "dl_length_label": "Download length (s)",
        "offset_label": "Start cut at (s)",
        "duration_label": "Cut length (s)",
        "workers_label": "Parallel downloads",
        "explain_text": "Slicing cuts a short section from each downloaded preview.\nStart = where in the file the cut begins.   Cut length = how long each grain is.",
        "step1_check": "Step 1 — Download previews from YouTube",
        "from_middle_check": "Start ~⅓ into each song  (skips intros)",
        "step2_check": "Step 2 — Slice into grains",
        "sample_pre": "Random sample — pick",
        "sample_post": "tracks at random",
        "randcut_pre": "Randomize cut per track — length",
        "youtube_note": (
            "Note: This app does not use the Spotify API or download official Spotify audio. "
            "It searches YouTube by artist and track name, picks the result that best matches the song, "
            "and downloads N seconds of it. Most tracks match correctly, but some may still be a live "
            "recording, cover or alternate version; these are flagged in the log."
        ),
        "ai_section": "AI ANALYSIS",
        "librosa_ready": "librosa ready",
        "librosa_missing": "librosa not installed — analysis disabled",
        "smart_grain_check": "Smart grain selection  (find the best moment automatically)",
        "strategy_label": "Strategy",
        "detect_versions_check": "Pick the best YouTube match and flag likely wrong versions  (checks titles and song length)",
        "extract_features_check": "Extract audio features  (tempo, energy, key per track)",
        "cluster_check": "Cluster corpus by similarity  (one folder per group)",
        "clap_check": "CLAP embeddings  (optional — requires laion-clap, ~2GB model)",
        "ai_requires_note": "Analysis requires librosa. Run setup.bat or setup.sh to install it.",
        "start_btn": "Start",
        "stop_btn": "Stop",
        "randomize_btn": "Randomize",
        "log_section": "Log",
        "status_ready": "Ready",
        "status_done": "Done",
        "status_stopped": "Stopped",
        "stage_download": "Downloading",
        "stage_analyse": "Analysing",
        "stage_slice": "Slicing",
        "time_left": "~{t} left",
        "error_see_log": "Error — see log",
        "busy_note": "Finish or stop the current run first.",
    },
}

# Language names used by older versions, so a saved choice still works.
_LANG_ALIASES = {"Espanol": "Español", "Chinese": "中文", "Japanese": "日本語"}


def _load_translations():
    for folder in dict.fromkeys([DATA_DIR, APP_DIR]):
        path = os.path.join(folder, "translations.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                extra = json.load(f)
        except Exception as e:
            print(f"Could not read {path}: {e}")
            continue
        if isinstance(extra, dict):
            for k, v in extra.items():
                if not k.startswith("_") and isinstance(v, dict):
                    TRANSLATIONS[k] = v


_load_translations()


# ── Config and themes ─────────────────────────────────────────────────────────

def load_config() -> dict:
    path = os.path.join(APP_DIR, "config.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(updates: dict):
    path = os.path.join(APP_DIR, "config.json")
    config = load_config()
    config.update(updates)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


DEFAULT_THEME = "Default"


def list_themes() -> dict:
    """Theme name -> path, from the themes/ folder (bundled and next to the app)."""
    themes = {}
    for folder in dict.fromkeys([os.path.join(DATA_DIR, "themes"), os.path.join(APP_DIR, "themes")]):
        if not os.path.isdir(folder):
            continue
        for fname in sorted(os.listdir(folder)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(folder, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    name = json.load(f).get("_name") or fname[:-5]
            except Exception:
                continue
            themes[name] = path
    return themes


def apply_theme(name: str) -> str:
    """Load a theme on top of customtkinter's built-in blue theme, so widgets a
    theme file doesn't mention still get sensible colours. Call before building
    widgets. Returns the name of the theme actually applied."""
    import customtkinter as ctk
    from customtkinter import ThemeManager
    ctk.set_default_color_theme("blue")
    mode = "Dark"
    path = list_themes().get(name)
    if not path:
        name = DEFAULT_THEME
    else:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for widget, props in data.items():
                # CTkFont entries are per-platform in theme files; keep the default fonts.
                if widget.startswith("_") or widget == "CTkFont" or not isinstance(props, dict):
                    continue
                ThemeManager.theme.setdefault(widget, {}).update(props)
            mode = data.get("_appearance_mode", "Dark")
        except Exception:
            name = DEFAULT_THEME
    ctk.set_appearance_mode(mode)
    return name


def apply_startup_theme():
    """Call before any CTk widgets are created."""
    try:
        import customtkinter  # noqa: F401
    except ImportError:
        return
    apply_theme(load_config().get("theme", DEFAULT_THEME))


# ── GUI support ───────────────────────────────────────────────────────────────

class _PrintRedirector:
    def __init__(self, log_queue: queue.Queue):
        self._queue = log_queue
        self._orig  = sys.stdout

    def write(self, text: str):
        text = text.strip("\n")
        if text:
            self._queue.put(text)

    def flush(self):
        pass

    def __enter__(self):
        sys.stdout = self
        return self

    def __exit__(self, *_):
        sys.stdout = self._orig


def load_tracks_from_csv(path: str):
    try:
        tracks = read_tracks(path)
        if not tracks:
            return [], "No tracks found — check Track Name and Artist Name(s) columns."
        return tracks, ""
    except Exception as e:
        return [], str(e)


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{round(seconds / 60)} min"
    return f"{seconds // 3600} h {round(seconds % 3600 / 60)} min"


# Every user setting the window remembers between sessions, with its default.
_SETTING_DEFAULTS = {
    "csv_path": "", "output": "", "audio_folder": "",
    "preview_len": "30", "offset": "5.0", "duration": "1.5", "workers": "3",
    "do_download": True, "from_middle": False, "do_slice": True,
    "sample_on": False, "sample_n": "25",
    "randcut_on": False, "dur_min": "0.5", "dur_max": "3.0",
    "ai_smart": True, "ai_strategy": "auto", "ai_versions": True,
    "ai_feats": True, "ai_cluster": True, "ai_clap": False,
}


# ── Main UI class ─────────────────────────────────────────────────────────────

class CorpusBuilderUI:
    _LOG_MAX_LINES = 5000
    _MUTED = ("gray40", "gray62")
    _OK    = ("#1a7f37", "#3fb950")
    _BAD   = ("#cf222e", "#f85149")

    def __init__(self, root):
        import customtkinter as ctk
        import tkinter as tk
        from tkinter import ttk

        self.root = root
        self.ctk  = ctk
        self._ttk = ttk

        self._all_tracks  = []
        self._visible_idx = []   # indices into _all_tracks currently shown in the tree
        self._search_text = ""
        self._log_queue   = queue.Queue()
        self._stop_event  = threading.Event()
        self._running     = False
        self._progress_state = None   # (stage, done, total, started_at), set by the worker
        self._librosa_ok  = _librosa_available()

        config = load_config()
        lang = _LANG_ALIASES.get(config.get("lang"), config.get("lang"))
        self._lang  = lang if lang in TRANSLATIONS else "English"
        self._theme = config.get("theme", DEFAULT_THEME)

        # Tk variables outlive the widgets, so settings survive a rebuild.
        saved = config.get("settings", {})
        self._vars = {}
        for key, default in _SETTING_DEFAULTS.items():
            value = saved.get(key, default)
            if isinstance(default, bool):
                self._vars[key] = tk.BooleanVar(value=bool(value))
            else:
                self._vars[key] = tk.StringVar(value=str(value))
        if not self._vars["output"].get():
            self._vars["output"].set(os.path.join(APP_DIR, "output"))
        self._vars["audio_folder"].trace_add("write", lambda *_: self._update_start_state())

        self._dnd_ok = bool(getattr(root, "TkdndVersion", None))
        if self._dnd_ok:
            root.drop_target_register("DND_Files")
            root.dnd_bind("<<Drop>>", self._on_drop)

        root.minsize(980, 640)
        root.geometry("1180x800")
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._poll()

        csv_path = self._vars["csv_path"].get()
        if csv_path and os.path.isfile(csv_path):
            self._load_csv(csv_path)

    def _T(self) -> dict:
        # Fall back to English for any key a translation is missing.
        return {**TRANSLATIONS["English"], **TRANSLATIONS.get(self._lang, {})}

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        ctk = self.ctk
        T = self._T()
        from customtkinter import ThemeManager

        for child in self.root.winfo_children():
            child.destroy()
        self.root.configure(fg_color=ThemeManager.theme["CTk"]["fg_color"])
        self.root.title(T["window_title"])
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)

        self._fonts = {
            "title":   ctk.CTkFont(size=22, weight="bold"),
            "section": ctk.CTkFont(size=12, weight="bold"),
            "body":    ctk.CTkFont(size=13),
            "hint":    ctk.CTkFont(size=12),
            "button":  ctk.CTkFont(size=14, weight="bold"),
            "mono":    ctk.CTkFont(family="Courier", size=12),
        }
        self._style_treeview()

        self._build_header(T)

        body = ctk.CTkFrame(self.root, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=12, pady=(4, 0))
        body.grid_columnconfigure(0, weight=3, uniform="cols")
        body.grid_columnconfigure(1, weight=2, uniform="cols")
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(2, weight=1)
        self._build_files(left, T)
        self._build_tracks_and_log(left, T)

        right = ctk.CTkScrollableFrame(body, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right.grid_columnconfigure(0, weight=1)
        self._build_settings(right, T)
        self._build_ai(right, T)

        self._build_action_bar(T)

    def _section(self, parent, text, row):
        self.ctk.CTkLabel(parent, text=text, font=self._fonts["section"],
                          text_color=self._MUTED, anchor="w"
                          ).grid(row=row, column=0, sticky="w", padx=6, pady=(10, 4))

    def _card(self, parent, row, **grid):
        card = self.ctk.CTkFrame(parent, corner_radius=10)
        card.grid(row=row, column=0, sticky=grid.pop("sticky", "ew"), **grid)
        return card

    def _hint(self, parent, text):
        """Muted help text that re-wraps to whatever width it is given."""
        label = self.ctk.CTkLabel(parent, text=text, font=self._fonts["hint"],
                                  text_color=self._MUTED, justify="left", anchor="w",
                                  wraplength=400)
        label.bind("<Configure>", lambda e, lb=label: lb.configure(
            wraplength=max(200, lb.winfo_width() - 8)))
        return label

    def _build_header(self, T):
        ctk = self.ctk
        header = ctk.CTkFrame(self.root, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        title_block = ctk.CTkFrame(header, fg_color="transparent")
        title_block.grid(row=0, column=0, padx=20, pady=12, sticky="w")
        ctk.CTkLabel(title_block, text=T["window_title"], font=self._fonts["title"]
                     ).pack(anchor="w")
        ctk.CTkLabel(title_block, text=T["app_description"], font=self._fonts["body"],
                     text_color=self._MUTED, wraplength=620, justify="left"
                     ).pack(anchor="w", pady=(2, 0))

        pickers = ctk.CTkFrame(header, fg_color="transparent")
        pickers.grid(row=0, column=1, padx=20, pady=12, sticky="e")
        themes = [DEFAULT_THEME] + sorted(list_themes())
        for col, (label, values, current, command) in enumerate([
            (T["theme_label"], themes, self._theme, self._on_theme_change),
            (T["language_label"], list(TRANSLATIONS), self._lang, self._on_lang_change),
        ]):
            ctk.CTkLabel(pickers, text=label, font=self._fonts["hint"], text_color=self._MUTED
                         ).grid(row=0, column=col, sticky="w", padx=(0 if col == 0 else 12, 0))
            box = ctk.CTkComboBox(pickers, values=values, width=140, height=30,
                                  state="readonly", command=command)
            box.set(current)
            box.grid(row=1, column=col, padx=(0 if col == 0 else 12, 0))

    def _build_files(self, parent, T):
        ctk = self.ctk
        self._section(parent, T["files_section"], 0)
        card = self._card(parent, 1)
        card.grid_columnconfigure(1, weight=1)

        csv_hint = T["csv_hint"] + ("\n" + T["drop_hint"] if self._dnd_ok else "")
        rows = [
            (T["csv_label"],   self._vars["csv_path"],     self._browse_csv,          csv_hint,        "paste"),
            (T["save_label"],  self._vars["output"],       self._browse_output,       None,            None),
            (T["audio_label"], self._vars["audio_folder"], self._browse_audio_folder, T["audio_hint"], "clear"),
        ]
        r = 0
        for label, var, browse, hint, extra in rows:
            top = 14 if r == 0 else 6
            ctk.CTkLabel(card, text=label, font=self._fonts["body"], anchor="w"
                         ).grid(row=r, column=0, padx=(16, 10), pady=(top, 0), sticky="w")
            ctk.CTkEntry(card, textvariable=var, height=32, font=self._fonts["body"],
                         state="readonly" if var is self._vars["csv_path"] else "normal"
                         ).grid(row=r, column=1, pady=(top, 0), sticky="ew")
            ctk.CTkButton(card, text=T["browse_btn"], width=92, height=32,
                          font=self._fonts["body"], command=browse
                          ).grid(row=r, column=2, padx=(8, 0), pady=(top, 0))
            if extra == "paste":
                ctk.CTkButton(card, text=T["paste_btn"], width=80, height=32,
                              font=self._fonts["body"], command=self._open_paste_dialog,
                              fg_color="transparent", border_width=1,
                              text_color=("gray10", "gray90")
                              ).grid(row=r, column=3, padx=(4, 0), pady=(top, 0), sticky="w")
            elif extra == "clear":
                ctk.CTkButton(card, text="✕", width=32, height=32,
                              fg_color=("gray75", "gray30"), hover_color=("gray65", "gray40"),
                              text_color=("gray10", "gray90"),
                              command=lambda v=var: v.set("")
                              ).grid(row=r, column=3, padx=(4, 0), pady=(top, 0), sticky="w")
            r += 1
            if hint:
                self._hint(card, hint).grid(row=r, column=1, columnspan=3, sticky="ew",
                                            pady=(2, 0))
                r += 1
        ctk.CTkFrame(card, height=10, width=12, fg_color="transparent").grid(row=r, column=4)

    def _build_tracks_and_log(self, parent, T):
        ctk = self.ctk
        self._tabs = ctk.CTkTabview(parent, corner_radius=10, height=260)
        self._tabs.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        self._tab_tracks = T["tracks_section"].capitalize() if T["tracks_section"].isupper() else T["tracks_section"]
        self._tab_log = T["log_section"]
        tracks_tab = self._tabs.add(self._tab_tracks)
        log_tab = self._tabs.add(self._tab_log)

        # Tracks tab
        tracks_tab.grid_columnconfigure(0, weight=1)
        tracks_tab.grid_rowconfigure(1, weight=1)
        search_row = ctk.CTkFrame(tracks_tab, fg_color="transparent")
        search_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        search_row.grid_columnconfigure(0, weight=1)
        # No textvariable: customtkinter hides the placeholder when one is set.
        self._search_entry = ctk.CTkEntry(search_row, placeholder_text=T["search_placeholder"],
                                          height=32, font=self._fonts["body"])
        self._search_entry.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        if self._search_text:
            self._search_entry.insert(0, self._search_text)
        self._search_entry.bind("<KeyRelease>", self._on_search)
        self._count_label = ctk.CTkLabel(search_row, text=T["no_csv_msg"],
                                         font=self._fonts["hint"], text_color=self._MUTED)
        self._count_label.grid(row=0, column=1, sticky="e")

        tree_frame = ctk.CTkFrame(tracks_tab, fg_color="transparent")
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)
        self._tree = self._ttk.Treeview(tree_frame, columns=("artist", "track"),
                                        show="headings", style="Corpus.Treeview")
        self._tree.heading("artist", text=T["artist_col"], anchor="w")
        self._tree.heading("track",  text=T["track_col"], anchor="w")
        self._tree.column("artist", width=240, minwidth=100)
        self._tree.column("track",  width=320, minwidth=100)
        vsb = ctk.CTkScrollbar(tree_frame, command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self._hint(tracks_tab, T["selection_hint"]).grid(row=2, column=0, sticky="ew", pady=(6, 0))

        # Log tab
        log_tab.grid_columnconfigure(0, weight=1)
        log_tab.grid_rowconfigure(0, weight=1)
        self._log_area = ctk.CTkTextbox(log_tab, state="disabled", wrap="none",
                                        font=self._fonts["mono"])
        self._log_area.grid(row=0, column=0, sticky="nsew")

    def _build_settings(self, parent, T):
        ctk = self.ctk
        V = self._vars
        self._section(parent, T["settings_section"], 0)
        card = self._card(parent, 1)
        card.grid_columnconfigure((0, 1), weight=1)

        params = [("dl_length_label", "preview_len"), ("workers_label", "workers"),
                  ("offset_label", "offset"), ("duration_label", "duration")]
        for i, (label_key, var_key) in enumerate(params):
            cell = ctk.CTkFrame(card, fg_color="transparent")
            cell.grid(row=i // 2, column=i % 2, sticky="w", padx=16, pady=(12 if i < 2 else 6, 0))
            ctk.CTkLabel(cell, text=T[label_key], font=self._fonts["body"], anchor="w"
                         ).pack(anchor="w")
            ctk.CTkEntry(cell, textvariable=V[var_key], width=90, height=32,
                         font=self._fonts["body"], justify="center").pack(anchor="w", pady=(4, 0))

        self._hint(card, T["explain_text"]).grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(10, 4))

        steps = ctk.CTkFrame(card, fg_color="transparent")
        steps.grid(row=3, column=0, columnspan=2, sticky="ew", padx=16, pady=(6, 0))

        def check(parent, key, text, indent=0, pady=(0, 8)):
            box = ctk.CTkCheckBox(parent, text=text, variable=V[key], font=self._fonts["body"])
            box.pack(anchor="w", padx=(indent, 0), pady=pady)
            return box

        check(steps, "do_download", T["step1_check"])
        check(steps, "from_middle", T["from_middle_check"], indent=28)
        check(steps, "do_slice", T["step2_check"])

        def inline_row(key, pre, entries, post):
            row = ctk.CTkFrame(steps, fg_color="transparent")
            row.pack(anchor="w", pady=(0, 8))
            ctk.CTkCheckBox(row, text=pre, variable=V[key], font=self._fonts["body"]).pack(side="left")
            for j, var_key in enumerate(entries):
                if j:
                    ctk.CTkLabel(row, text="–", font=self._fonts["body"]).pack(side="left", padx=2)
                ctk.CTkEntry(row, textvariable=V[var_key], width=52, height=28,
                             font=self._fonts["body"], justify="center").pack(side="left", padx=(6, 4))
            ctk.CTkLabel(row, text=post, font=self._fonts["body"]).pack(side="left")

        inline_row("sample_on", T["sample_pre"], ["sample_n"], T["sample_post"])
        inline_row("randcut_on", T["randcut_pre"], ["dur_min", "dur_max"], "s")

        ctk.CTkFrame(card, height=1, fg_color=("gray78", "gray30")).grid(
            row=4, column=0, columnspan=2, sticky="ew", padx=16, pady=(6, 0))
        self._hint(card, T["youtube_note"]).grid(
            row=5, column=0, columnspan=2, sticky="ew", padx=16, pady=(8, 14))

    def _build_ai(self, parent, T):
        ctk = self.ctk
        V = self._vars
        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.grid(row=2, column=0, sticky="ew", pady=(10, 4))
        ctk.CTkLabel(head, text=T["ai_section"], font=self._fonts["section"],
                     text_color=self._MUTED).pack(side="left", padx=6)
        ok = self._librosa_ok
        ctk.CTkLabel(head, text=("●  " + T["librosa_ready"]) if ok else ("●  " + T["librosa_missing"]),
                     font=self._fonts["hint"], text_color=self._OK if ok else self._BAD
                     ).pack(side="left", padx=(8, 0))

        card = self._card(parent, 3, pady=(0, 12))

        smart_row = ctk.CTkFrame(card, fg_color="transparent")
        smart_row.pack(anchor="w", fill="x", padx=16, pady=(14, 8))
        smart = ctk.CTkCheckBox(smart_row, text=T["smart_grain_check"], variable=V["ai_smart"],
                                font=self._fonts["body"])
        smart.pack(anchor="w")
        strat = ctk.CTkFrame(smart_row, fg_color="transparent")
        strat.pack(anchor="w", padx=(28, 0), pady=(6, 0))
        ctk.CTkLabel(strat, text=T["strategy_label"], font=self._fonts["hint"],
                     text_color=self._MUTED).pack(side="left", padx=(0, 8))
        strategy = ctk.CTkComboBox(strat, values=GRAIN_STRATEGIES, variable=V["ai_strategy"],
                                   width=120, height=28, state="readonly")
        strategy.pack(side="left")

        boxes = [smart]
        for key, text_key in [("ai_versions", "detect_versions_check"),
                              ("ai_feats", "extract_features_check"),
                              ("ai_cluster", "cluster_check"),
                              ("ai_clap", "clap_check")]:
            box = ctk.CTkCheckBox(card, text=T[text_key], variable=V[key], font=self._fonts["body"])
            box.pack(anchor="w", padx=16, pady=(0, 8))
            if key != "ai_versions":   # version matching doesn't need librosa
                boxes.append(box)
        if not ok:
            for box in boxes + [strategy]:
                box.configure(state="disabled")
            self._hint(card, T["ai_requires_note"]).pack(anchor="w", fill="x", padx=16, pady=(0, 4))
        ctk.CTkFrame(card, height=6, fg_color="transparent").pack()

    def _build_action_bar(self, T):
        ctk = self.ctk
        bar = ctk.CTkFrame(self.root, corner_radius=0)
        bar.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        bar.grid_columnconfigure(3, weight=1)

        self._start_btn = ctk.CTkButton(bar, text=T["start_btn"], width=120, height=40,
                                        font=self._fonts["button"], command=self._start)
        self._start_btn.grid(row=0, column=0, padx=(20, 8), pady=12)
        self._stop_btn = ctk.CTkButton(bar, text=T["stop_btn"], width=100, height=40,
                                       font=self._fonts["body"], command=self._stop,
                                       fg_color=("gray75", "gray30"), hover_color=("gray65", "gray40"),
                                       text_color=("gray10", "gray90"))
        self._stop_btn.grid(row=0, column=1, padx=(0, 8))
        self._randomize_btn = ctk.CTkButton(bar, text=T["randomize_btn"], width=110, height=40,
                                            font=self._fonts["body"], command=self._randomize,
                                            fg_color="transparent", border_width=1,
                                            text_color=("gray10", "gray90"))
        self._randomize_btn.grid(row=0, column=2)

        status = ctk.CTkFrame(bar, fg_color="transparent")
        status.grid(row=0, column=3, sticky="ew", padx=(24, 20))
        status.grid_columnconfigure(0, weight=1)
        self._status_label = ctk.CTkLabel(status, text=T["status_ready"], font=self._fonts["hint"],
                                          text_color=self._MUTED, anchor="w")
        self._status_label.grid(row=0, column=0, sticky="w")
        self._progress = ctk.CTkProgressBar(status, height=8)
        self._progress.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self._progress.set(0)

        self._stop_btn.configure(state="normal" if self._running else "disabled")
        self._update_start_state()

    def _style_treeview(self):
        """Style the ttk Treeview to match the current CTk theme."""
        from customtkinter import ThemeManager
        mode_idx = 1 if self.ctk.get_appearance_mode() == "Dark" else 0

        def color(widget, prop, fallback):
            val = ThemeManager.theme.get(widget, {}).get(prop, fallback)
            return val[mode_idx] if isinstance(val, (list, tuple)) else val

        bg   = color("CTkTextbox", "fg_color", "#1d1e1e")
        fg   = color("CTkLabel", "text_color", "#dce4ee")
        sel  = color("CTkButton", "fg_color", "#1f6aa5")
        head = color("CTkFrame", "top_fg_color", "#333333")
        if bg == "transparent":
            bg = "#1d1e1e" if mode_idx else "#f9f9fa"

        style = self._ttk.Style()
        style.theme_use("clam")   # the default themes ignore most colour options
        style.configure("Corpus.Treeview", background=bg, foreground=fg, fieldbackground=bg,
                        borderwidth=0, rowheight=26, font=("", -13))
        style.configure("Corpus.Treeview.Heading", background=head, foreground=fg,
                        borderwidth=0, relief="flat", font=("", -13, "bold"), padding=(6, 4))
        style.map("Corpus.Treeview.Heading", background=[("active", head)])
        style.map("Corpus.Treeview", background=[("selected", sel)],
                  foreground=[("selected", "#ffffff")])
        style.layout("Corpus.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    # ── Theme / language ──────────────────────────────────────────────────────

    def _rebuild(self):
        """Recreate every widget (after a theme or language change), keeping state."""
        log_text = self._log_area.get("1.0", "end-1c")
        tab = self._tabs.get()
        on_log = tab == self._tab_log
        self._build_ui()
        if log_text:
            self._log_area.configure(state="normal")
            self._log_area.insert("end", log_text + "\n")
            self._log_area.configure(state="disabled")
        if on_log:
            self._tabs.set(self._tab_log)
        self._on_search()
        self._save_settings()

    def _on_lang_change(self, lang: str):
        if self._running:
            self._log_write(self._T()["busy_note"])
            self.root.after(0, self._build_header_only)
            return
        self._lang = lang
        self._rebuild()

    def _on_theme_change(self, name: str):
        if self._running:
            self._log_write(self._T()["busy_note"])
            self.root.after(0, self._build_header_only)
            return
        self._theme = apply_theme(name)
        self._rebuild()

    def _build_header_only(self):
        # Put the pickers back to the current values after a refused change.
        for child in self.root.grid_slaves(row=0, column=0):
            child.destroy()
        self._build_header(self._T())

    # ── Settings persistence ──────────────────────────────────────────────────

    def _save_settings(self):
        save_config({"lang": self._lang, "theme": self._theme,
                     "settings": {k: v.get() for k, v in self._vars.items()}})

    def _on_close(self):
        self._stop_event.set()
        self._save_settings()
        self.root.after_cancel(self._poll_id)
        self.root.destroy()

    # ── File pickers ──────────────────────────────────────────────────────────

    def _browse_csv(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Select a track list",
            filetypes=[("Track lists", "*.csv *.txt"), ("All files", "*.*")],
        )
        if path:
            self._open_track_file(path)

    def _open_track_file(self, path: str):
        if path.lower().endswith(".csv"):
            self._vars["csv_path"].set(path)
            self._load_csv(path)
            return
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                text = f.read()
        except OSError as e:
            self._log_write(str(e))
            return
        self._import_text(text, os.path.splitext(os.path.basename(path))[0])

    def _on_drop(self, event):
        for path in self.root.tk.splitlist(event.data):
            if os.path.isdir(path):
                self._vars["audio_folder"].set(path)
            elif path.lower().endswith((".csv", ".txt")):
                self._open_track_file(path)
            elif path.lower().endswith(AUDIO_EXTS):
                self._vars["audio_folder"].set(os.path.dirname(path))
        return event.action

    def _open_paste_dialog(self):
        ctk = self.ctk
        T = self._T()
        win = ctk.CTkToplevel(self.root)
        win.title(T["paste_title"])
        win.geometry("560x440")
        win.transient(self.root)
        win.grid_columnconfigure(0, weight=1)
        win.grid_rowconfigure(1, weight=1)
        self._hint(win, T["paste_hint"]).grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        box = ctk.CTkTextbox(win, font=self._fonts["body"])
        box.grid(row=1, column=0, sticky="nsew", padx=16)
        box.insert("1.0", "Aphex Twin - Xtal\nBoards of Canada - Roygbiv\n")
        buttons = ctk.CTkFrame(win, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="e", padx=16, pady=14)

        def load():
            text = box.get("1.0", "end")
            win.destroy()
            self._import_text(text, "pasted")

        ctk.CTkButton(buttons, text=T["cancel_btn"], width=90, command=win.destroy,
                      fg_color="transparent", border_width=1,
                      text_color=("gray10", "gray90")).pack(side="left", padx=(0, 8))
        ctk.CTkButton(buttons, text=T["load_btn"], width=110, command=load).pack(side="left")
        win.after(100, lambda: (win.lift(), box.focus_set(), box.tag_add("sel", "1.0", "end")))

    def _import_text(self, text: str, name: str):
        """Load pasted text or a .txt list. YouTube links are read in the
        background; the result is saved as a CSV so it is remembered like any other."""
        T = self._T()
        tracks, urls = parse_track_lines(text)

        def finish(all_tracks):
            if not all_tracks:
                self._log_write(T["import_empty"])
                self._tabs.set(self._tab_log)
                return
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = os.path.join(APP_DIR, "track lists", f"{sanitize(name)}-{stamp}.csv")
            write_tracks_csv(path, all_tracks)
            self._log_write(f"Saved {len(all_tracks)} tracks to {path}")
            self._vars["csv_path"].set(path)
            self._load_csv(path)

        if not urls:
            finish(tracks)
            return
        self._log_write(T["youtube_reading"].format(n=len(urls)))
        self._tabs.set(self._tab_log)

        def work():
            with _PrintRedirector(self._log_queue):
                found = expand_youtube_links(urls)
            self.root.after(0, lambda: finish(tracks + found))
        threading.Thread(target=work, daemon=True).start()

    def _browse_output(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self._vars["output"].set(path)

    def _browse_audio_folder(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(title="Select folder containing WAV files")
        if path:
            self._vars["audio_folder"].set(path)

    # ── CSV + search ──────────────────────────────────────────────────────────

    def _load_csv(self, path: str):
        tracks, err = load_tracks_from_csv(path)
        T = self._T()
        if err:
            msg = T["csv_error_cols"] if ("Track Name" in err or "no tracks" in err.lower()) else err
            self._count_label.configure(text=T["error_see_log"])
            self._log_write(msg)
            self._tabs.set(self._tab_log)
            self._all_tracks = []
            self._refresh_tree([])
            self._update_start_state()
            return
        self._all_tracks = tracks
        self._search_text = ""
        if self._search_entry.get():   # deleting an empty entry would wipe its placeholder
            self._search_entry.delete(0, "end")
        self._on_search()
        self._update_start_state()
        self._save_settings()

    def _update_start_state(self):
        if self._running:
            self._start_btn.configure(state="disabled")
            return
        ready = bool(self._all_tracks) or bool(self._vars["audio_folder"].get().strip())
        self._start_btn.configure(state="normal" if ready else "disabled")

    def _on_search(self, *_):
        T = self._T()
        self._search_text = self._search_entry.get()
        q = self._search_text.strip().lower()
        if not self._all_tracks:
            self._refresh_tree([])
            self._count_label.configure(text=T["no_csv_msg"])
            return
        if not q:
            self._refresh_tree(range(len(self._all_tracks)))
            self._count_label.configure(text=T["status_loaded"].format(n=len(self._all_tracks)))
            return
        matches = [i for i, t in enumerate(self._all_tracks)
                   if q in t["artist"].lower() or q in t["name"].lower()]
        self._refresh_tree(matches)
        self._count_label.configure(
            text=T["status_filtered"].format(n=len(matches), m=len(self._all_tracks)))

    def _refresh_tree(self, indices):
        self._visible_idx = list(indices)
        self._tree.delete(*self._tree.get_children())
        for i in self._visible_idx:
            t = self._all_tracks[i]
            artists = ", ".join(a.strip() for a in t["artist"].split(";"))
            self._tree.insert("", "end", iid=str(i), values=(artists, t["name"]))

    def _tracks_to_process(self) -> list:
        """Selected rows if any, otherwise every row the search filter shows."""
        selected = self._tree.selection()
        if selected:
            self._log_write(f"Using {len(selected)} selected tracks.")
            return [self._all_tracks[int(iid)] for iid in selected]
        if len(self._visible_idx) < len(self._all_tracks):
            self._log_write(f"Using the {len(self._visible_idx)} tracks matching the search.")
        return [self._all_tracks[i] for i in self._visible_idx]

    # ── Run ───────────────────────────────────────────────────────────────────

    def _start(self):
        self._stop_event.clear()
        self._running = True
        self._progress_state = None
        self._save_settings()
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._progress.set(0)
        self._tabs.set(self._tab_log)
        self._log_write("--- Starting ---")
        threading.Thread(target=self._run_thread, daemon=True).start()

    def _stop(self):
        self._stop_event.set()
        self._log_write("--- Stop requested ---")

    def _on_progress(self, stage: str, done: int, total: int):
        """Called from the worker thread; _poll reads it on the UI thread."""
        prev = self._progress_state
        started = prev[3] if prev and prev[0] == stage else time.time()
        self._progress_state = (stage, done, total, started)

    def _run_thread(self):
        V = self._vars
        output_root  = V["output"].get()
        audio_folder = V["audio_folder"].get().strip()

        try:
            prev_len = int(V["preview_len"].get())
            offset   = float(V["offset"].get())
            duration = float(V["duration"].get())
            dur_min  = float(V["dur_min"].get())
            dur_max  = float(V["dur_max"].get())
            workers  = max(1, min(8, int(V["workers"].get())))
            if dur_min > dur_max:
                dur_min, dur_max = dur_max, dur_min
        except ValueError:
            self._log_write("One of the number fields has an invalid value. Check that Download length, "
                            "Parallel downloads, Start, Cut length and the length range are all plain "
                            "numbers (for example: 30, 3, 5.0, 1.5).")
            self.root.after(0, self._on_done)
            return

        ai_opts = {
            "smart_grain":      V["ai_smart"].get(),
            "grain_strategy":   V["ai_strategy"].get(),
            "extract_features": V["ai_feats"].get(),
            "cluster":          V["ai_cluster"].get(),
            "clap":             V["ai_clap"].get(),
        }

        tracks = [] if audio_folder else self._tracks_to_process()
        if tracks and V["sample_on"].get():
            try:
                n = max(1, int(V["sample_n"].get()))
                pool = len(tracks)
                tracks = random.sample(tracks, min(n, pool))
                self._log_write(f"Random sample: {len(tracks)} of {pool} tracks selected.")
            except ValueError:
                self._log_write("Invalid sample count — using all tracks.")

        with _PrintRedirector(self._log_queue):
            try:
                run_pipeline(
                    output_root, tracks=tracks, audio_folder=audio_folder,
                    preview_length=prev_len, offset=offset, duration=duration,
                    do_download=V["do_download"].get(), do_slice=V["do_slice"].get(),
                    randomize_cut=V["randcut_on"].get(), dur_min=dur_min, dur_max=dur_max,
                    match_versions=V["ai_versions"].get(), from_middle=V["from_middle"].get(),
                    workers=workers, ai_opts=ai_opts, stop_event=self._stop_event,
                    progress=self._on_progress)
            except Exception as e:
                print(f"ERROR: {e}")

        self.root.after(0, self._on_done)

    def _on_done(self):
        T = self._T()
        self._running = False
        stopped = self._stop_event.is_set()
        self._progress_state = None
        self._progress.set(0 if stopped else 1)
        self._status_label.configure(text=T["status_stopped"] if stopped else T["status_done"])
        self._stop_btn.configure(state="disabled")
        self._update_start_state()

    def _randomize(self):
        V = self._vars
        V["preview_len"].set(str(random.randint(15, 60)))
        V["offset"].set(f"{random.uniform(0.0, 25.0):.1f}")
        V["duration"].set(f"{random.uniform(0.5, 5.0):.1f}")
        for key in ("ai_smart", "ai_versions", "ai_feats", "ai_cluster"):
            V[key].set(random.choice([True, False]))
        V["ai_strategy"].set(random.choice(GRAIN_STRATEGIES))

    # ── Log + progress polling ────────────────────────────────────────────────

    def _log_write(self, msg: str):
        self._log_queue.put(msg)

    def _poll(self):
        # Drain everything queued since the last poll and insert it in one go.
        lines = []
        try:
            while len(lines) < 2000:
                lines.append(self._log_queue.get_nowait())
        except queue.Empty:
            pass
        if lines:
            box = self._log_area
            box.configure(state="normal")
            box.insert("end", "\n".join(lines) + "\n")
            excess = int(box.index("end-1c").split(".")[0]) - self._LOG_MAX_LINES
            if excess > 0:
                box.delete("1.0", f"{excess + 1}.0")
            box.see("end")
            box.configure(state="disabled")

        state = self._progress_state
        if self._running and state:
            stage, done, total, started = state
            T = self._T()
            text = f"{T.get('stage_' + stage, stage)}  {done} / {total}"
            if 0 < done < total:
                elapsed = time.time() - started
                if elapsed > 3:
                    text += "   ·   " + T["time_left"].format(
                        t=_fmt_duration(elapsed / done * (total - done)))
            self._progress.set(done / total if total else 0)
            self._status_label.configure(text=text)
        self._poll_id = self.root.after(100, self._poll)


# ── Entry point ───────────────────────────────────────────────────────────────

def _make_root():
    """A CTk window with drag and drop if the optional tkinterdnd2 package is installed."""
    import customtkinter as ctk
    try:
        from tkinterdnd2 import TkinterDnD
    except ImportError:
        return ctk.CTk()

    class DnDRoot(ctk.CTk, TkinterDnD.DnDWrapper):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            try:
                self.TkdndVersion = TkinterDnD._require(self)
            except Exception:
                self.TkdndVersion = None

    return DnDRoot()


def main():
    if len(sys.argv) == 1:
        try:
            import customtkinter as ctk
        except ImportError:
            print("customtkinter is not installed. Run setup.bat (Windows) or setup.sh (Mac) to fix this.")
            sys.exit(1)

        apply_startup_theme()
        root = _make_root()
        CorpusBuilderUI(root)
        root.mainloop()
        return

    parser = argparse.ArgumentParser(
        description="Download Spotify preview clips and slice them into short grains.")
    parser.add_argument("--csv",            default=os.path.join(APP_DIR, "Liked_Songs.csv"),
                        help="Exportify CSV, or a .txt file with one 'Artist - Title' or YouTube link per line")
    parser.add_argument("--youtube",        action="append", default=[], metavar="URL",
                        help="YouTube video or playlist link (can be repeated; used instead of --csv)")
    parser.add_argument("--output",         default=os.path.join(APP_DIR, "output"))
    parser.add_argument("--audio-folder",   default="",
                        help="Use existing audio files from this folder instead of downloading")
    parser.add_argument("--preview-length", type=int,   default=30)
    parser.add_argument("--offset",         type=float, default=5.0)
    parser.add_argument("--duration",       type=float, default=1.5)
    parser.add_argument("--fade-ms",        type=float, default=5.0,
                        help="Fade in/out on each grain in milliseconds (0 = off)")
    parser.add_argument("--sample",         type=int,   default=0, metavar="N",
                        help="Pick N tracks at random from the CSV")
    parser.add_argument("--randomize-cut",  type=float, nargs=2, metavar=("MIN", "MAX"),
                        help="Random grain length between MIN and MAX seconds, at a random position")
    parser.add_argument("--seed",           type=int,
                        help="Random seed, so a random sample or random cuts can be reproduced")
    parser.add_argument("--download-from",  choices=["start", "middle"], default="start",
                        help="Download from the start of each song, or from about a third of the way in")
    parser.add_argument("--workers",        type=int, default=3,
                        help="How many tracks to download at the same time (default 3)")
    parser.add_argument("--first-result",   action="store_true",
                        help="Use the first YouTube result instead of picking the best match")
    parser.add_argument("--skip-download",  action="store_true")
    parser.add_argument("--skip-slice",     action="store_true")
    ai = parser.add_argument_group("AI analysis (requires librosa)")
    ai.add_argument("--smart-grain",     action="store_true")
    ai.add_argument("--grain-strategy",  choices=GRAIN_STRATEGIES, default="auto")
    ai.add_argument("--features",        action="store_true")
    ai.add_argument("--cluster",         action="store_true")
    ai.add_argument("--clap",            action="store_true")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    tracks = []
    if args.youtube:
        tracks = [{"artist": "", "name": u, "youtube_url": u, "_unexpanded": True} for u in args.youtube]
    elif not args.audio_folder:
        if not os.path.exists(args.csv):
            print(f"ERROR: CSV not found at {args.csv}")
            sys.exit(1)
        tracks = read_tracks(args.csv)
        if args.sample > 0:
            tracks = random.sample(tracks, min(args.sample, len(tracks)))
            print(f"Random sample: {len(tracks)} tracks selected.")

    dur_min, dur_max = sorted(args.randomize_cut) if args.randomize_cut else (0.5, 3.0)
    try:
        run_pipeline(
            args.output, tracks=tracks, audio_folder=args.audio_folder,
            preview_length=args.preview_length, offset=args.offset, duration=args.duration,
            do_download=not args.skip_download, do_slice=not args.skip_slice,
            randomize_cut=bool(args.randomize_cut), dur_min=dur_min, dur_max=dur_max,
            fade_ms=args.fade_ms, seed=args.seed,
            match_versions=not args.first_result, from_middle=args.download_from == "middle",
            workers=args.workers,
            ai_opts={"smart_grain": args.smart_grain, "grain_strategy": args.grain_strategy,
                     "extract_features": args.features, "cluster": args.cluster,
                     "clap": args.clap})
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(130)


if __name__ == "__main__":
    main()
