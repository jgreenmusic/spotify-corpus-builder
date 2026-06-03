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

GUI usage (default):
    Windows:   python  spotify_corpus_builder.py
    Mac/Linux: python3 spotify_corpus_builder.py

CLI usage:
    python spotify_corpus_builder.py --csv my_songs.csv
    python spotify_corpus_builder.py --offset 8 --duration 2.0
    python spotify_corpus_builder.py --preview-length 15 --offset 3 --duration 1.0
    python spotify_corpus_builder.py --skip-download
    python spotify_corpus_builder.py --skip-slice

Options:
    --csv             Path to Spotify CSV export  (default: opens GUI)
    --output          Output root folder           (default: ./output/)
    --preview-length  Seconds to download per track (default: 30)
    --offset          Seconds into preview to start the grain (default: 5)
    --duration        Grain length in seconds      (default: 1.5)
    --skip-download   Skip download step
    --skip-slice      Skip slice step
"""

import argparse
import csv
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
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

# Column name variants across different Spotify export tools
_TRACK_COLS  = ["Track Name", "track_name", "Song Name", "song_name", "Title", "title"]
_ARTIST_COLS = ["Artist Name(s)", "Artist Name", "artist_name", "artists", "Artist", "artist"]


def sanitize(name: str) -> str:
    return re.sub(r'[/\\:*?"<>|]', "_", name).strip()[:150]


def ffmpeg_bin() -> str:
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
    sys.exit(1)


def _find_col(row: dict, candidates: list) -> str:
    """Return the first candidate column name that exists in row."""
    for col in candidates:
        if col in row:
            return row[col]
    return ""


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


def run_download(csv_path: str, previews_dir: str, preview_length: int,
                 stop_event: threading.Event = None):
    os.makedirs(previews_dir, exist_ok=True)

    tracks = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name   = _find_col(row, _TRACK_COLS).strip()
            artist = _find_col(row, _ARTIST_COLS).strip()
            if name and artist:
                tracks.append({"name": name, "artist": artist})

    print(f"\n=== DOWNLOAD ({len(tracks)} tracks -> {preview_length}s previews) ===")
    print(f"Output: {previews_dir}\n")

    downloaded = skipped = failed = 0

    for i, track in enumerate(tracks, 1):
        if stop_event and stop_event.is_set():
            print("\nStopped by user.")
            break

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


def run_slice(previews_dir: str, grains_dir: str, offset: float, duration: float,
              stop_event: threading.Event = None):
    os.makedirs(grains_dir, exist_ok=True)
    ffmpeg = ffmpeg_bin()

    wav_files = sorted(f for f in os.listdir(previews_dir) if f.lower().endswith(".wav"))
    total = len(wav_files)

    print(f"\n=== SLICE ({total} files -> offset {offset}s, grain {duration}s) ===")
    print(f"Output: {grains_dir}\n")

    done = skipped = failed = 0

    for i, fname in enumerate(wav_files, 1):
        if stop_event and stop_event.is_set():
            print("\nStopped by user.")
            break

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


# ── GUI ───────────────────────────────────────────────────────────────────────

class _PrintRedirector:
    """Captures print() calls and puts them in a queue for the UI to display."""
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


def load_tracks_from_csv(path: str) -> tuple[list[dict], str]:
    """
    Read a CSV and return (tracks, error_message).
    tracks is a list of {"artist": ..., "name": ...}.
    Handles multiple Spotify export column name variants.
    """
    try:
        tracks = []
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name   = _find_col(row, _TRACK_COLS).strip()
                artist = _find_col(row, _ARTIST_COLS).strip()
                if name and artist:
                    tracks.append({"artist": artist, "name": name})
        if not tracks:
            return [], "No tracks found. Make sure the CSV has Track Name and Artist columns."
        return tracks, ""
    except Exception as e:
        return [], str(e)


class CorpusBuilderUI:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk

        self.root = root
        self.root.title("Spotify Corpus Builder")
        self.root.resizable(True, True)
        self.root.minsize(700, 600)

        self._all_tracks = []
        self._log_queue  = queue.Queue()
        self._stop_event = threading.Event()
        self._running    = False

        self._build_ui()
        self._poll_log()

    def _build_ui(self):
        import tkinter as tk
        from tkinter import ttk

        PAD = {"padx": 8, "pady": 4}

        # ── Top: file pickers ──────────────────────────────────────────────
        file_frame = ttk.LabelFrame(self.root, text="Files", padding=6)
        file_frame.pack(fill="x", padx=10, pady=(10, 4))
        file_frame.columnconfigure(1, weight=1)

        ttk.Label(file_frame, text="CSV file:").grid(row=0, column=0, sticky="w", **PAD)
        self._csv_var = tk.StringVar()
        ttk.Entry(file_frame, textvariable=self._csv_var, state="readonly").grid(
            row=0, column=1, sticky="ew", **PAD)
        ttk.Button(file_frame, text="Browse...", command=self._browse_csv).grid(
            row=0, column=2, **PAD)

        ttk.Label(file_frame, text="Output folder:").grid(row=1, column=0, sticky="w", **PAD)
        self._out_var = tk.StringVar(value=os.path.join(SCRIPT_DIR, "output"))
        ttk.Entry(file_frame, textvariable=self._out_var).grid(
            row=1, column=1, sticky="ew", **PAD)
        ttk.Button(file_frame, text="Browse...", command=self._browse_output).grid(
            row=1, column=2, **PAD)

        # ── Middle: track list ─────────────────────────────────────────────
        list_frame = ttk.LabelFrame(self.root, text="Tracks", padding=6)
        list_frame.pack(fill="both", expand=True, padx=10, pady=4)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(1, weight=1)

        search_row = ttk.Frame(list_frame)
        search_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        search_row.columnconfigure(1, weight=1)
        ttk.Label(search_row, text="Search:").pack(side="left", padx=(0, 4))
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search)
        ttk.Entry(search_row, textvariable=self._search_var).pack(side="left", fill="x", expand=True)
        self._count_label = ttk.Label(search_row, text="No CSV loaded")
        self._count_label.pack(side="right", padx=8)

        cols = ("artist", "track")
        self._tree = ttk.Treeview(list_frame, columns=cols, show="headings", height=12)
        self._tree.heading("artist", text="Artist")
        self._tree.heading("track",  text="Track")
        self._tree.column("artist", width=260, minwidth=120)
        self._tree.column("track",  width=340, minwidth=120)

        vsb = ttk.Scrollbar(list_frame, orient="vertical",   command=self._tree.yview)
        hsb = ttk.Scrollbar(list_frame, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=1, column=0, sticky="nsew")
        vsb.grid(row=1, column=1, sticky="ns")
        hsb.grid(row=2, column=0, sticky="ew")

        # ── Settings ───────────────────────────────────────────────────────
        settings_frame = ttk.LabelFrame(self.root, text="Settings", padding=6)
        settings_frame.pack(fill="x", padx=10, pady=4)

        import tkinter as tk
        self._prev_len_var = tk.IntVar(value=30)
        self._offset_var   = tk.DoubleVar(value=5.0)
        self._duration_var = tk.DoubleVar(value=1.5)
        self._do_download  = tk.BooleanVar(value=True)
        self._do_slice     = tk.BooleanVar(value=True)

        row1 = ttk.Frame(settings_frame)
        row1.pack(fill="x")
        ttk.Label(row1, text="Preview length (s):").pack(side="left", padx=(0, 4))
        ttk.Spinbox(row1, from_=5, to=60, increment=5, textvariable=self._prev_len_var,
                    width=5).pack(side="left", padx=(0, 20))
        ttk.Label(row1, text="Grain offset (s):").pack(side="left", padx=(0, 4))
        ttk.Spinbox(row1, from_=0, to=55, increment=0.5, textvariable=self._offset_var,
                    width=5, format="%.1f").pack(side="left", padx=(0, 20))
        ttk.Label(row1, text="Grain duration (s):").pack(side="left", padx=(0, 4))
        ttk.Spinbox(row1, from_=0.5, to=10, increment=0.5, textvariable=self._duration_var,
                    width=5, format="%.1f").pack(side="left")

        row2 = ttk.Frame(settings_frame)
        row2.pack(fill="x", pady=(4, 0))
        ttk.Checkbutton(row2, text="Download previews", variable=self._do_download).pack(
            side="left", padx=(0, 20))
        ttk.Checkbutton(row2, text="Slice grains", variable=self._do_slice).pack(side="left")

        # ── Controls + progress ────────────────────────────────────────────
        ctrl_frame = ttk.Frame(self.root, padding=(10, 4))
        ctrl_frame.pack(fill="x")
        ctrl_frame.columnconfigure(1, weight=1)

        self._start_btn = ttk.Button(ctrl_frame, text="Start", command=self._start,
                                     state="disabled")
        self._start_btn.grid(row=0, column=0, padx=(0, 8))
        self._stop_btn = ttk.Button(ctrl_frame, text="Stop", command=self._stop,
                                    state="disabled")
        self._stop_btn.grid(row=0, column=1, sticky="w")

        self._progress = ttk.Progressbar(ctrl_frame, mode="indeterminate")
        self._progress.grid(row=0, column=2, sticky="ew", padx=(8, 0))

        # ── Log ────────────────────────────────────────────────────────────
        import tkinter.scrolledtext as st
        log_frame = ttk.LabelFrame(self.root, text="Log", padding=6)
        log_frame.pack(fill="both", expand=False, padx=10, pady=(4, 10))

        self._log_area = st.ScrolledText(log_frame, height=10, state="disabled",
                                         font=("Courier", 10), wrap="none")
        self._log_area.pack(fill="both", expand=True)

    # ── File pickers ───────────────────────────────────────────────────────────

    def _browse_csv(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Select Spotify CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if path:
            self._csv_var.set(path)
            self._load_csv(path)

    def _browse_output(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self._out_var.set(path)

    # ── CSV loading + search ───────────────────────────────────────────────────

    def _load_csv(self, path: str):
        tracks, err = load_tracks_from_csv(path)
        if err:
            self._count_label.config(text=f"Error: {err}")
            self._all_tracks = []
            self._refresh_tree([])
            self._start_btn.config(state="disabled")
            return

        self._all_tracks = tracks
        self._search_var.set("")
        self._refresh_tree(tracks)
        self._count_label.config(text=f"{len(tracks)} tracks loaded")
        self._start_btn.config(state="normal")

    def _on_search(self, *_):
        query = self._search_var.get().lower()
        if not query:
            self._refresh_tree(self._all_tracks)
            self._count_label.config(text=f"{len(self._all_tracks)} tracks loaded")
            return
        filtered = [
            t for t in self._all_tracks
            if query in t["artist"].lower() or query in t["name"].lower()
        ]
        self._refresh_tree(filtered)
        self._count_label.config(
            text=f"{len(filtered)} of {len(self._all_tracks)} tracks")

    def _refresh_tree(self, tracks: list):
        self._tree.delete(*self._tree.get_children())
        for t in tracks:
            self._tree.insert("", "end", values=(t["artist"], t["name"]))

    # ── Run ────────────────────────────────────────────────────────────────────

    def _start(self):
        if not self._csv_var.get():
            return
        self._stop_event.clear()
        self._running = True
        self._start_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._progress.start(12)
        self._log_write("--- Starting ---")
        threading.Thread(target=self._run_thread, daemon=True).start()

    def _stop(self):
        self._stop_event.set()
        self._log_write("--- Stop requested ---")

    def _run_thread(self):
        csv_path    = self._csv_var.get()
        output_root = self._out_var.get()
        previews_dir = os.path.join(output_root, "previews")
        grains_dir   = os.path.join(output_root, "grains")

        with _PrintRedirector(self._log_queue):
            try:
                if self._do_download.get():
                    run_download(csv_path, previews_dir,
                                 self._prev_len_var.get(), self._stop_event)

                if self._do_slice.get() and not self._stop_event.is_set():
                    if os.path.isdir(previews_dir):
                        run_slice(previews_dir, grains_dir,
                                  self._offset_var.get(), self._duration_var.get(),
                                  self._stop_event)
                    else:
                        print("No previews folder found - run with Download enabled first.")

                print(f"\nAll done.  Previews: {previews_dir}  Grains: {grains_dir}")
            except Exception as e:
                print(f"ERROR: {e}")

        self.root.after(0, self._on_done)

    def _on_done(self):
        self._running = False
        self._progress.stop()
        self._start_btn.config(state="normal")
        self._stop_btn.config(state="disabled")

    # ── Log polling ────────────────────────────────────────────────────────────

    def _log_write(self, msg: str):
        self._log_queue.put(msg)

    def _poll_log(self):
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self._log_area.config(state="normal")
                self._log_area.insert("end", msg + "\n")
                self._log_area.see("end")
                self._log_area.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    # If no CLI flags are passed, launch the GUI
    if len(sys.argv) == 1:
        import tkinter as tk
        root = tk.Tk()
        CorpusBuilderUI(root)
        root.mainloop()
        return

    parser = argparse.ArgumentParser(
        description="Download Spotify preview clips and slice them into short grains.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--csv",            default=os.path.join(SCRIPT_DIR, "Liked_Songs.csv"))
    parser.add_argument("--output",         default=os.path.join(SCRIPT_DIR, "output"))
    parser.add_argument("--preview-length", type=int,   default=30)
    parser.add_argument("--offset",         type=float, default=5.0)
    parser.add_argument("--duration",       type=float, default=1.5)
    parser.add_argument("--skip-download",  action="store_true")
    parser.add_argument("--skip-slice",     action="store_true")
    args = parser.parse_args()

    previews_dir = os.path.join(args.output, "previews")
    grains_dir   = os.path.join(args.output, "grains")

    if not args.skip_download:
        if not os.path.exists(args.csv):
            print(f"ERROR: CSV not found at {args.csv}")
            sys.exit(1)
        run_download(args.csv, previews_dir, args.preview_length)

    if not args.skip_slice:
        if not os.path.isdir(previews_dir):
            print(f"ERROR: previews folder not found at {previews_dir}")
            sys.exit(1)
        run_slice(previews_dir, grains_dir, args.offset, args.duration)

    print(f"\nAll done.  Previews: {previews_dir}  Grains: {grains_dir}")


if __name__ == "__main__":
    main()
