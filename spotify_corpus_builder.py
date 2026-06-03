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
"""

import argparse
import csv
import json
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


# ── Translations ──────────────────────────────────────────────────────────────

TRANSLATIONS = {
    "English": {
        "window_title":   "Spotify Corpus Builder",
        "files_frame":    "Files",
        "language_label": "Language:",
        "csv_label":      "Load CSV File:",
        "save_label":     "Save To:",
        "browse_btn":     "Browse...",
        "tracks_frame":   "Tracks",
        "search_label":   "Search:",
        "no_csv_msg":     "No CSV loaded",
        "status_loaded":  "{n} tracks loaded",
        "status_filtered":"{n} of {m} tracks",
        "settings_frame": "Settings",
        "dl_length_label":"Download length (seconds):",
        "offset_label":   "Start cut at (seconds in):",
        "duration_label": "Cut length (seconds):",
        "explain_text":   "Slicing takes each downloaded preview and cuts a short section from it.\n"
                          "Offset = where in the file the cut starts.  "
                          "Cut length = how long each grain is.",
        "step1_check":    "Step 1 - Download previews from YouTube",
        "step2_check":    "Step 2 - Slice into grains",
        "log_frame":      "Log",
        "start_btn":      "Start",
        "stop_btn":       "Stop",
    },
    "Español": {
        "window_title":   "Constructor de Corpus de Spotify",
        "files_frame":    "Archivos",
        "language_label": "Idioma:",
        "csv_label":      "Cargar archivo CSV:",
        "save_label":     "Guardar en:",
        "browse_btn":     "Explorar...",
        "tracks_frame":   "Pistas",
        "search_label":   "Buscar:",
        "no_csv_msg":     "No hay CSV cargado",
        "status_loaded":  "{n} pistas cargadas",
        "status_filtered":"{n} de {m} pistas",
        "settings_frame": "Configuración",
        "dl_length_label":"Duración de descarga (segundos):",
        "offset_label":   "Iniciar corte en (segundos):",
        "duration_label": "Duración del corte (segundos):",
        "explain_text":   "El corte toma cada vista previa descargada y extrae una sección corta.\n"
                          "Desplazamiento = dónde comienza el corte.  "
                          "Duración = cuánto dura cada grano.",
        "step1_check":    "Paso 1 - Descargar vistas previas de YouTube",
        "step2_check":    "Paso 2 - Cortar en granos",
        "log_frame":      "Registro",
        "start_btn":      "Iniciar",
        "stop_btn":       "Detener",
    },
    "Deutsch": {
        "window_title":   "Spotify Corpus Builder",
        "files_frame":    "Dateien",
        "language_label": "Sprache:",
        "csv_label":      "CSV-Datei laden:",
        "save_label":     "Speichern unter:",
        "browse_btn":     "Durchsuchen...",
        "tracks_frame":   "Titel",
        "search_label":   "Suchen:",
        "no_csv_msg":     "Keine CSV geladen",
        "status_loaded":  "{n} Titel geladen",
        "status_filtered":"{n} von {m} Titeln",
        "settings_frame": "Einstellungen",
        "dl_length_label":"Download-Länge (Sekunden):",
        "offset_label":   "Schnitt starten bei (Sekunden):",
        "duration_label": "Schnittlänge (Sekunden):",
        "explain_text":   "Das Schneiden extrahiert einen kurzen Abschnitt aus jeder heruntergeladenen Vorschau.\n"
                          "Versatz = wo der Schnitt beginnt.  "
                          "Schnittlänge = wie lang jedes Korn ist.",
        "step1_check":    "Schritt 1 - Vorschauen von YouTube herunterladen",
        "step2_check":    "Schritt 2 - In Körner schneiden",
        "log_frame":      "Protokoll",
        "start_btn":      "Start",
        "stop_btn":       "Stopp",
    },
    "中文": {
        "window_title":   "Spotify 语料库构建器",
        "files_frame":    "文件",
        "language_label": "语言：",
        "csv_label":      "加载 CSV 文件：",
        "save_label":     "保存到：",
        "browse_btn":     "浏览...",
        "tracks_frame":   "曲目",
        "search_label":   "搜索：",
        "no_csv_msg":     "未加载 CSV",
        "status_loaded":  "已加载 {n} 首曲目",
        "status_filtered":"{n} / {m} 首曲目",
        "settings_frame": "设置",
        "dl_length_label":"下载时长（秒）：",
        "offset_label":   "裁剪起始位置（秒）：",
        "duration_label": "裁剪长度（秒）：",
        "explain_text":   "切片功能将每个已下载的预览音频裁剪出一段短片段。\n"
                          "偏移量 = 裁剪开始的时间点。  "
                          "裁剪长度 = 每个音粒的持续时间。",
        "step1_check":    "第一步 - 从 YouTube 下载预览",
        "step2_check":    "第二步 - 切片成音粒",
        "log_frame":      "日志",
        "start_btn":      "开始",
        "stop_btn":       "停止",
    },
    "日本語": {
        "window_title":   "Spotify コーパスビルダー",
        "files_frame":    "ファイル",
        "language_label": "言語：",
        "csv_label":      "CSV ファイルを読み込む：",
        "save_label":     "保存先：",
        "browse_btn":     "参照...",
        "tracks_frame":   "トラック",
        "search_label":   "検索：",
        "no_csv_msg":     "CSV が読み込まれていません",
        "status_loaded":  "{n} トラック読み込み済み",
        "status_filtered":"{m} 中 {n} トラック",
        "settings_frame": "設定",
        "dl_length_label":"ダウンロード長（秒）：",
        "offset_label":   "カット開始位置（秒）：",
        "duration_label": "カット長（秒）：",
        "explain_text":   "スライスは各ダウンロードされたプレビューから短いセクションを切り出します。\n"
                          "オフセット = カットが始まる位置。  "
                          "カット長 = 各グレインの長さ。",
        "step1_check":    "ステップ 1 - YouTube からプレビューをダウンロード",
        "step2_check":    "ステップ 2 - グレインにスライス",
        "log_frame":      "ログ",
        "start_btn":      "開始",
        "stop_btn":       "停止",
    },
}


def _load_translations():
    """Merge translations.json (if present) into TRANSLATIONS. Any language works."""
    path = os.path.join(SCRIPT_DIR, "translations.json")
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            extra = json.load(f)
        if isinstance(extra, dict):
            TRANSLATIONS.update(extra)
    except Exception:
        pass


_load_translations()


# ── GUI ───────────────────────────────────────────────────────────────────────

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


def load_tracks_from_csv(path: str) -> tuple:
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

        self.root = root
        self.root.resizable(True, True)
        self.root.minsize(700, 620)

        self._all_tracks = []
        self._log_queue  = queue.Queue()
        self._stop_event = threading.Event()
        self._running    = False
        self._lang       = "English"
        self._i18n       = {}  # key -> widget reference for live language updates

        self._build_ui()
        self._poll_log()

    def _T(self) -> dict:
        return TRANSLATIONS.get(self._lang, TRANSLATIONS["English"])

    def _cjk_font(self, lang: str):
        if sys.platform != "win32":
            return None
        if lang == "中文":
            return ("Microsoft YaHei", 9)
        if lang == "日本語":
            return ("Meiryo", 9)
        return ("Segoe UI", 9)

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        import tkinter as tk
        from tkinter import ttk

        PAD = {"padx": 8, "pady": 4}

        # ── Files + language ──────────────────────────────────────────────
        file_frame = ttk.LabelFrame(self.root, text="Files", padding=6)
        file_frame.pack(fill="x", padx=10, pady=(10, 4))
        file_frame.columnconfigure(1, weight=1)
        self._i18n["files_frame"] = file_frame

        # Language selector row
        lang_row = ttk.Frame(file_frame)
        lang_row.grid(row=0, column=0, columnspan=3, sticky="e", padx=8, pady=(0, 2))
        lang_lbl = ttk.Label(lang_row, text="Language:")
        lang_lbl.pack(side="left", padx=(0, 4))
        self._i18n["language_label"] = lang_lbl

        self._lang_var = tk.StringVar(value="English")
        lang_combo = ttk.Combobox(
            lang_row,
            textvariable=self._lang_var,
            values=list(TRANSLATIONS.keys()),
            state="readonly",
            width=12,
        )
        lang_combo.pack(side="left")
        lang_combo.bind("<<ComboboxSelected>>", self._on_lang_change)

        # CSV row
        csv_lbl = ttk.Label(file_frame, text="Load CSV File:")
        csv_lbl.grid(row=1, column=0, sticky="w", **PAD)
        self._i18n["csv_label"] = csv_lbl

        self._csv_var = tk.StringVar()
        ttk.Entry(file_frame, textvariable=self._csv_var, state="readonly").grid(
            row=1, column=1, sticky="ew", **PAD)

        csv_btn = ttk.Button(file_frame, text="Browse...", command=self._browse_csv)
        csv_btn.grid(row=1, column=2, **PAD)
        self._i18n["browse_btn_csv"] = csv_btn

        # Output row
        out_lbl = ttk.Label(file_frame, text="Save To:")
        out_lbl.grid(row=2, column=0, sticky="w", **PAD)
        self._i18n["save_label"] = out_lbl

        self._out_var = tk.StringVar(value=os.path.join(SCRIPT_DIR, "output"))
        ttk.Entry(file_frame, textvariable=self._out_var).grid(
            row=2, column=1, sticky="ew", **PAD)

        out_btn = ttk.Button(file_frame, text="Browse...", command=self._browse_output)
        out_btn.grid(row=2, column=2, **PAD)
        self._i18n["browse_btn_out"] = out_btn

        # ── Track list ────────────────────────────────────────────────────
        list_frame = ttk.LabelFrame(self.root, text="Tracks", padding=6)
        list_frame.pack(fill="both", expand=True, padx=10, pady=4)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(1, weight=1)
        self._i18n["tracks_frame"] = list_frame

        search_row = ttk.Frame(list_frame)
        search_row.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        search_row.columnconfigure(1, weight=1)

        search_lbl = ttk.Label(search_row, text="Search:")
        search_lbl.pack(side="left", padx=(0, 4))
        self._i18n["search_label"] = search_lbl

        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search)
        ttk.Entry(search_row, textvariable=self._search_var).pack(
            side="left", fill="x", expand=True)

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

        # ── Settings ──────────────────────────────────────────────────────
        settings_frame = ttk.LabelFrame(self.root, text="Settings", padding=6)
        settings_frame.pack(fill="x", padx=10, pady=4)
        self._i18n["settings_frame"] = settings_frame

        self._prev_len_var = tk.IntVar(value=30)
        self._offset_var   = tk.DoubleVar(value=5.0)
        self._duration_var = tk.DoubleVar(value=1.5)
        self._do_download  = tk.BooleanVar(value=True)
        self._do_slice     = tk.BooleanVar(value=True)

        row1 = ttk.Frame(settings_frame)
        row1.pack(fill="x")

        dl_lbl = ttk.Label(row1, text="Download length (seconds):")
        dl_lbl.pack(side="left", padx=(0, 4))
        self._i18n["dl_length_label"] = dl_lbl
        ttk.Spinbox(row1, from_=5, to=60, increment=5, textvariable=self._prev_len_var,
                    width=5).pack(side="left", padx=(0, 16))

        off_lbl = ttk.Label(row1, text="Start cut at (seconds in):")
        off_lbl.pack(side="left", padx=(0, 4))
        self._i18n["offset_label"] = off_lbl
        ttk.Spinbox(row1, from_=0, to=55, increment=0.5, textvariable=self._offset_var,
                    width=5, format="%.1f").pack(side="left", padx=(0, 16))

        dur_lbl = ttk.Label(row1, text="Cut length (seconds):")
        dur_lbl.pack(side="left", padx=(0, 4))
        self._i18n["duration_label"] = dur_lbl
        ttk.Spinbox(row1, from_=0.5, to=10, increment=0.5, textvariable=self._duration_var,
                    width=5, format="%.1f").pack(side="left")

        # Explanatory label
        explain_lbl = ttk.Label(
            settings_frame,
            text=(
                "Slicing takes each downloaded preview and cuts a short section from it.\n"
                "Offset = where in the file the cut starts.  "
                "Cut length = how long each grain is."
            ),
            foreground="#888888",
            font=("TkDefaultFont", 8),
            justify="left",
        )
        explain_lbl.pack(anchor="w", pady=(4, 2))
        self._i18n["explain_text"] = explain_lbl

        row2 = ttk.Frame(settings_frame)
        row2.pack(fill="x", pady=(4, 0))

        step1_chk = ttk.Checkbutton(
            row2, text="Step 1 - Download previews from YouTube",
            variable=self._do_download)
        step1_chk.pack(side="left", padx=(0, 20))
        self._i18n["step1_check"] = step1_chk

        step2_chk = ttk.Checkbutton(
            row2, text="Step 2 - Slice into grains",
            variable=self._do_slice)
        step2_chk.pack(side="left")
        self._i18n["step2_check"] = step2_chk

        # ── Controls ──────────────────────────────────────────────────────
        ctrl_frame = ttk.Frame(self.root, padding=(10, 4))
        ctrl_frame.pack(fill="x")
        ctrl_frame.columnconfigure(2, weight=1)

        self._start_btn = ttk.Button(ctrl_frame, text="Start", command=self._start,
                                     state="disabled")
        self._start_btn.grid(row=0, column=0, padx=(0, 8))
        self._i18n["start_btn"] = self._start_btn

        self._stop_btn = ttk.Button(ctrl_frame, text="Stop", command=self._stop,
                                    state="disabled")
        self._stop_btn.grid(row=0, column=1, sticky="w")
        self._i18n["stop_btn"] = self._stop_btn

        self._progress = ttk.Progressbar(ctrl_frame, mode="indeterminate")
        self._progress.grid(row=0, column=2, sticky="ew", padx=(8, 0))

        # ── Log ───────────────────────────────────────────────────────────
        import tkinter.scrolledtext as st
        log_frame = ttk.LabelFrame(self.root, text="Log", padding=6)
        log_frame.pack(fill="both", expand=False, padx=10, pady=(4, 10))
        self._i18n["log_frame"] = log_frame

        self._log_area = st.ScrolledText(
            log_frame, height=10, state="disabled",
            font=("Courier", 10), wrap="none")
        self._log_area.pack(fill="both", expand=True)

        # Apply initial language
        self._apply_language("English")

    # ── Language ──────────────────────────────────────────────────────────────

    def _on_lang_change(self, *_):
        self._apply_language(self._lang_var.get())

    def _apply_language(self, lang: str):
        self._lang = lang
        T = self._T()

        self.root.title(T["window_title"])

        # Update all labelled widgets
        label_map = {
            "files_frame":    "files_frame",
            "language_label": "language_label",
            "csv_label":      "csv_label",
            "save_label":     "save_label",
            "browse_btn_csv": "browse_btn",
            "browse_btn_out": "browse_btn",
            "tracks_frame":   "tracks_frame",
            "search_label":   "search_label",
            "settings_frame": "settings_frame",
            "dl_length_label":"dl_length_label",
            "offset_label":   "offset_label",
            "duration_label": "duration_label",
            "explain_text":   "explain_text",
            "step1_check":    "step1_check",
            "step2_check":    "step2_check",
            "log_frame":      "log_frame",
            "start_btn":      "start_btn",
            "stop_btn":       "stop_btn",
        }

        for widget_key, trans_key in label_map.items():
            widget = self._i18n.get(widget_key)
            if widget and trans_key in T:
                try:
                    widget.config(text=T[trans_key])
                except Exception:
                    pass

        # Update font for CJK rendering on Windows
        font = self._cjk_font(lang)
        if font:
            for widget in self._i18n.values():
                try:
                    widget.config(font=font)
                except Exception:
                    pass

        # Refresh count label
        n = len(self._all_tracks)
        if n > 0:
            self._count_label.config(text=T["status_loaded"].format(n=n))
        else:
            self._count_label.config(text=T["no_csv_msg"])

        # Keep language combobox updated with all available languages
        # (in case translations.json was loaded after build)
        pass

    # ── File pickers ──────────────────────────────────────────────────────────

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

    # ── CSV loading + search ──────────────────────────────────────────────────

    def _load_csv(self, path: str):
        tracks, err = load_tracks_from_csv(path)
        T = self._T()
        if err:
            self._count_label.config(text=f"Error: {err}")
            self._all_tracks = []
            self._refresh_tree([])
            self._start_btn.config(state="disabled")
            return

        self._all_tracks = tracks
        self._search_var.set("")
        self._refresh_tree(tracks)
        self._count_label.config(text=T["status_loaded"].format(n=len(tracks)))
        self._start_btn.config(state="normal")

    def _on_search(self, *_):
        T = self._T()
        query = self._search_var.get().lower()
        if not query:
            self._refresh_tree(self._all_tracks)
            self._count_label.config(
                text=T["status_loaded"].format(n=len(self._all_tracks)))
            return
        filtered = [
            t for t in self._all_tracks
            if query in t["artist"].lower() or query in t["name"].lower()
        ]
        self._refresh_tree(filtered)
        self._count_label.config(
            text=T["status_filtered"].format(n=len(filtered), m=len(self._all_tracks)))

    def _refresh_tree(self, tracks: list):
        self._tree.delete(*self._tree.get_children())
        for t in tracks:
            self._tree.insert("", "end", values=(t["artist"], t["name"]))

    # ── Run ───────────────────────────────────────────────────────────────────

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
        csv_path     = self._csv_var.get()
        output_root  = self._out_var.get()
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

    # ── Log ───────────────────────────────────────────────────────────────────

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
