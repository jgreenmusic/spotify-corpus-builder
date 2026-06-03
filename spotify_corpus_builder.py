#!/usr/bin/env python3
"""
spotify_corpus_builder.py
Downloads preview clips for every track in a Spotify CSV export, then
slices each clip to a short grain for use as a corpus.

Prerequisites:
    pip install yt-dlp customtkinter
    ffmpeg on PATH:
      Windows: winget install ffmpeg
      Mac:     brew install ffmpeg

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
    print("ERROR: ffmpeg not found.")
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
    cmd = [ffmpeg, "-y", "-ss", str(offset), "-t", str(duration),
           "-i", src, "-ar", "44100", "-ac", "2", dst]
    return subprocess.run(cmd, capture_output=True).returncode == 0


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
        "window_title": "Spotify Corpus Builder",
        "files_section": "FILES", "language_label": "Language",
        "theme_label": "Theme", "csv_label": "Load CSV File",
        "save_label": "Save To", "browse_btn": "Browse",
        "tracks_section": "TRACKS", "search_placeholder": "Search artist or track...",
        "no_csv_msg": "No CSV loaded", "status_loaded": "{n} tracks loaded",
        "status_filtered": "{n} of {m} tracks",
        "settings_section": "SETTINGS",
        "dl_length_label": "Download length (seconds)",
        "offset_label": "Start cut at (seconds in)",
        "duration_label": "Cut length (seconds)",
        "explain_text": (
            "Slicing takes each downloaded preview and cuts a short section from it.\n"
            "Offset = where in the file the cut starts.   Cut length = how long each grain is."
        ),
        "step1_check": "Step 1 — Download previews from YouTube",
        "step2_check": "Step 2 — Slice into grains",
        "start_btn": "Start", "stop_btn": "Stop",
        "log_section": "LOG",
    },
    "Espanol": {
        "window_title": "Constructor de Corpus de Spotify",
        "files_section": "ARCHIVOS", "language_label": "Idioma",
        "theme_label": "Tema", "csv_label": "Cargar archivo CSV",
        "save_label": "Guardar en", "browse_btn": "Explorar",
        "tracks_section": "PISTAS", "search_placeholder": "Buscar artista o pista...",
        "no_csv_msg": "No hay CSV cargado", "status_loaded": "{n} pistas cargadas",
        "status_filtered": "{n} de {m} pistas",
        "settings_section": "CONFIGURACION",
        "dl_length_label": "Duracion de descarga (segundos)",
        "offset_label": "Iniciar corte en (segundos)",
        "duration_label": "Duracion del corte (segundos)",
        "explain_text": (
            "El corte extrae una seccion corta de cada vista previa descargada.\n"
            "Desplazamiento = donde comienza el corte.   Duracion = cuanto dura cada grano."
        ),
        "step1_check": "Paso 1 - Descargar vistas previas de YouTube",
        "step2_check": "Paso 2 - Cortar en granos",
        "start_btn": "Iniciar", "stop_btn": "Detener",
        "log_section": "REGISTRO",
    },
    "Deutsch": {
        "window_title": "Spotify Corpus Builder",
        "files_section": "DATEIEN", "language_label": "Sprache",
        "theme_label": "Design", "csv_label": "CSV-Datei laden",
        "save_label": "Speichern unter", "browse_btn": "Durchsuchen",
        "tracks_section": "TITEL", "search_placeholder": "Kunstler oder Titel suchen...",
        "no_csv_msg": "Keine CSV geladen", "status_loaded": "{n} Titel geladen",
        "status_filtered": "{n} von {m} Titeln",
        "settings_section": "EINSTELLUNGEN",
        "dl_length_label": "Download-Lange (Sekunden)",
        "offset_label": "Schnitt starten bei (Sekunden)",
        "duration_label": "Schnittlange (Sekunden)",
        "explain_text": (
            "Das Schneiden extrahiert einen kurzen Abschnitt aus jeder Vorschau.\n"
            "Versatz = wo der Schnitt beginnt.   Schnittlange = wie lang jedes Korn ist."
        ),
        "step1_check": "Schritt 1 - Vorschauen von YouTube herunterladen",
        "step2_check": "Schritt 2 - In Korner schneiden",
        "start_btn": "Start", "stop_btn": "Stopp",
        "log_section": "PROTOKOLL",
    },
    "Chinese": {
        "window_title": "Spotify 语料库构建器",
        "files_section": "文件", "language_label": "语言",
        "theme_label": "主题", "csv_label": "加载 CSV 文件",
        "save_label": "保存到", "browse_btn": "浏览",
        "tracks_section": "曲目", "search_placeholder": "搜索艺术家或曲目...",
        "no_csv_msg": "未加载 CSV", "status_loaded": "已加载 {n} 首曲目",
        "status_filtered": "{n} / {m} 首曲目",
        "settings_section": "设置",
        "dl_length_label": "下载时长（秒）",
        "offset_label": "裁剪起始位置（秒）",
        "duration_label": "裁剪长度（秒）",
        "explain_text": (
            "切片功能将每个下载的预览音频裁剪成一段短片段。\n"
            "偏移量 = 裁剪开始的时间点。   裁剪长度 = 每个音粒的持续时间。"
        ),
        "step1_check": "第一步 — 从 YouTube 下载预览",
        "step2_check": "第二步 — 切片成音粒",
        "start_btn": "开始", "stop_btn": "停止",
        "log_section": "日志",
    },
    "Japanese": {
        "window_title": "Spotify コーパスビルダー",
        "files_section": "ファイル", "language_label": "言語",
        "theme_label": "テーマ", "csv_label": "CSV ファイルを読み込む",
        "save_label": "保存先", "browse_btn": "参照",
        "tracks_section": "トラック", "search_placeholder": "アーティストまたはトラックを検索...",
        "no_csv_msg": "CSV が読み込まれていません", "status_loaded": "{n} トラック読み込み済み",
        "status_filtered": "{m} 中 {n} トラック",
        "settings_section": "設定",
        "dl_length_label": "ダウンロード長（秒）",
        "offset_label": "カット開始位置（秒）",
        "duration_label": "カット長（秒）",
        "explain_text": (
            "スライスは各プレビューから短いセクションを切り出します。\n"
            "オフセット = カットが始まる位置。   カット長 = 各グレインの長さ。"
        ),
        "step1_check": "ステップ 1 — YouTube からプレビューをダウンロード",
        "step2_check": "ステップ 2 — グレインにスライス",
        "start_btn": "開始", "stop_btn": "停止",
        "log_section": "ログ",
    },
}


def _load_translations():
    path = os.path.join(SCRIPT_DIR, "translations.json")
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            extra = json.load(f)
        if isinstance(extra, dict):
            for k, v in extra.items():
                if not k.startswith("_"):
                    TRANSLATIONS[k] = v
    except Exception:
        pass


_load_translations()


# ── Theme system ──────────────────────────────────────────────────────────────

def discover_themes() -> dict:
    """Return {display_name: json_path} for all themes in themes/ folder."""
    themes_dir = os.path.join(SCRIPT_DIR, "themes")
    themes = {}
    if os.path.isdir(themes_dir):
        for fname in sorted(os.listdir(themes_dir)):
            if fname.endswith(".json") and not fname.startswith("_"):
                path = os.path.join(themes_dir, fname)
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                    display = data.get("_name", fname.replace(".json", "").replace("-", " ").title())
                    themes[display] = path
                except Exception:
                    pass
    return themes


def load_config() -> dict:
    path = os.path.join(SCRIPT_DIR, "config.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(updates: dict):
    path = os.path.join(SCRIPT_DIR, "config.json")
    config = load_config()
    config.update(updates)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except Exception:
        pass


def apply_startup_theme():
    """Call before any CTk widgets are created."""
    try:
        import customtkinter as ctk
    except ImportError:
        return

    config  = load_config()
    themes  = discover_themes()
    chosen  = config.get("theme")

    if chosen and chosen in themes:
        theme_path = themes[chosen]
        try:
            with open(theme_path, encoding="utf-8") as f:
                data = json.load(f)
            mode = data.get("_appearance_mode", "Dark")
            ctk.set_appearance_mode(mode)
            ctk.set_default_color_theme(theme_path)
            return
        except Exception:
            pass

    # Default fallback
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")


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
        tracks = []
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                name   = _find_col(row, _TRACK_COLS).strip()
                artist = _find_col(row, _ARTIST_COLS).strip()
                if name and artist:
                    tracks.append({"artist": artist, "name": name})
        if not tracks:
            return [], "No tracks found. Check that the CSV has Track Name and Artist columns."
        return tracks, ""
    except Exception as e:
        return [], str(e)


# ── Main UI class ─────────────────────────────────────────────────────────────

class CorpusBuilderUI:
    def __init__(self, root):
        import customtkinter as ctk
        from tkinter import ttk

        self.root  = root
        self.ctk   = ctk
        self._ttk  = ttk

        self._all_tracks = []
        self._log_queue  = queue.Queue()
        self._stop_event = threading.Event()
        self._running    = False

        config       = load_config()
        self._lang   = config.get("lang", "English")
        self._themes = discover_themes()

        self.root.title(self._T()["window_title"])
        self.root.minsize(760, 680)

        self._build_ui()
        self._poll_log()

    def _T(self) -> dict:
        return TRANSLATIONS.get(self._lang, TRANSLATIONS["English"])

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        ctk = self.ctk
        tk  = ctk  # alias for StringVar/BooleanVar

        import tkinter as _tk
        T = self._T()

        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(2, weight=1)  # track list expands

        # ── Header bar ────────────────────────────────────────────────────
        header = ctk.CTkFrame(self.root, corner_radius=0, height=48)
        header.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        header.grid_columnconfigure(1, weight=1)
        header.grid_propagate(False)

        ctk.CTkLabel(
            header,
            text="Spotify Corpus Builder",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=0, column=0, padx=16, pady=8, sticky="w")

        controls_right = ctk.CTkFrame(header, fg_color="transparent")
        controls_right.grid(row=0, column=2, padx=12, pady=4, sticky="e")

        # Theme selector
        config   = load_config()
        theme_names = list(self._themes.keys())
        cur_theme   = config.get("theme", theme_names[0] if theme_names else "")

        self._theme_label = ctk.CTkLabel(controls_right, text=T["theme_label"],
                                         font=ctk.CTkFont(size=11))
        self._theme_label.pack(side="left", padx=(0, 4))

        self._theme_var = _tk.StringVar(value=cur_theme)
        theme_combo = ctk.CTkComboBox(
            controls_right,
            variable=self._theme_var,
            values=theme_names if theme_names else ["Default"],
            width=130,
            command=self._on_theme_change,
        )
        theme_combo.pack(side="left", padx=(0, 16))

        # Language selector
        self._lang_label = ctk.CTkLabel(controls_right, text=T["language_label"],
                                        font=ctk.CTkFont(size=11))
        self._lang_label.pack(side="left", padx=(0, 4))

        self._lang_var = _tk.StringVar(value=self._lang)
        lang_combo = ctk.CTkComboBox(
            controls_right,
            variable=self._lang_var,
            values=list(TRANSLATIONS.keys()),
            width=120,
            command=self._on_lang_change,
        )
        lang_combo.pack(side="left")

        # ── Files section ─────────────────────────────────────────────────
        self._files_label = ctk.CTkLabel(
            self.root, text=T["files_section"],
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("gray50", "gray60"),
        )
        self._files_label.grid(row=1, column=0, sticky="w", padx=18, pady=(14, 2))

        files_frame = ctk.CTkFrame(self.root, corner_radius=8)
        files_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 4),
                         ipadx=4, ipady=4)
        files_frame.grid_columnconfigure(1, weight=1)
        files_frame.grid_rowconfigure(0, minsize=4)

        # CSV row
        self._csv_lbl = ctk.CTkLabel(files_frame, text=T["csv_label"],
                                     font=ctk.CTkFont(size=12))
        self._csv_lbl.grid(row=1, column=0, padx=(14, 8), pady=6, sticky="w")

        self._csv_var = _tk.StringVar()
        ctk.CTkEntry(files_frame, textvariable=self._csv_var, state="readonly",
                     height=32).grid(row=1, column=1, padx=4, pady=6, sticky="ew")

        self._csv_browse_btn = ctk.CTkButton(
            files_frame, text=T["browse_btn"], width=90, height=32,
            command=self._browse_csv)
        self._csv_browse_btn.grid(row=1, column=2, padx=(4, 14), pady=6)

        # Output row
        self._save_lbl = ctk.CTkLabel(files_frame, text=T["save_label"],
                                      font=ctk.CTkFont(size=12))
        self._save_lbl.grid(row=2, column=0, padx=(14, 8), pady=6, sticky="w")

        self._out_var = _tk.StringVar(value=os.path.join(SCRIPT_DIR, "output"))
        ctk.CTkEntry(files_frame, textvariable=self._out_var,
                     height=32).grid(row=2, column=1, padx=4, pady=6, sticky="ew")

        self._out_browse_btn = ctk.CTkButton(
            files_frame, text=T["browse_btn"], width=90, height=32,
            command=self._browse_output)
        self._out_browse_btn.grid(row=2, column=2, padx=(4, 14), pady=6)

        # ── Tracks section ────────────────────────────────────────────────
        self._tracks_label = ctk.CTkLabel(
            self.root, text=T["tracks_section"],
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("gray50", "gray60"),
        )
        self._tracks_label.grid(row=2, column=0, sticky="nw", padx=18, pady=(10, 2))

        tracks_outer = ctk.CTkFrame(self.root, corner_radius=8)
        tracks_outer.grid(row=2, column=0, sticky="nsew", padx=12, pady=(20, 4))
        tracks_outer.grid_columnconfigure(0, weight=1)
        tracks_outer.grid_rowconfigure(1, weight=1)

        # Search bar
        search_row = ctk.CTkFrame(tracks_outer, fg_color="transparent")
        search_row.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        search_row.grid_columnconfigure(0, weight=1)

        self._search_var = _tk.StringVar()
        self._search_var.trace_add("write", self._on_search)
        search_entry = ctk.CTkEntry(
            search_row,
            textvariable=self._search_var,
            placeholder_text=T["search_placeholder"],
            height=34,
        )
        search_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))

        self._count_label = ctk.CTkLabel(
            search_row, text=T["no_csv_msg"],
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray55"),
        )
        self._count_label.grid(row=0, column=1, sticky="e")

        # Treeview (ttk, styled to match CTk)
        tree_frame = ctk.CTkFrame(tracks_outer, fg_color="transparent")
        tree_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)

        self._style_treeview()

        self._tree = self._ttk.Treeview(
            tree_frame, columns=("artist", "track"),
            show="headings", height=10, style="Corpus.Treeview")
        self._tree.heading("artist", text="Artist")
        self._tree.heading("track",  text="Track")
        self._tree.column("artist", width=240, minwidth=100)
        self._tree.column("track",  width=340, minwidth=100)

        vsb = self._ttk.Scrollbar(tree_frame, orient="vertical",   command=self._tree.yview)
        hsb = self._ttk.Scrollbar(tree_frame, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        # ── Settings section ──────────────────────────────────────────────
        self._settings_label = ctk.CTkLabel(
            self.root, text=T["settings_section"],
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("gray50", "gray60"),
        )
        self._settings_label.grid(row=3, column=0, sticky="w", padx=18, pady=(10, 2))

        settings_frame = ctk.CTkFrame(self.root, corner_radius=8)
        settings_frame.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 4),
                            ipadx=4, ipady=4)

        import tkinter as _tk2
        self._prev_len_var = _tk2.StringVar(value="30")
        self._offset_var   = _tk2.StringVar(value="5.0")
        self._duration_var = _tk2.StringVar(value="1.5")
        self._do_download  = _tk2.BooleanVar(value=True)
        self._do_slice     = _tk2.BooleanVar(value=True)

        params_row = ctk.CTkFrame(settings_frame, fg_color="transparent")
        params_row.pack(fill="x", padx=12, pady=(10, 4))

        def _param(parent, label_key, var, width=70):
            f = ctk.CTkFrame(parent, fg_color="transparent")
            lbl = ctk.CTkLabel(f, text=T[label_key], font=ctk.CTkFont(size=11))
            lbl.pack(anchor="w")
            entry = ctk.CTkEntry(f, textvariable=var, width=width, height=30,
                                 justify="center")
            entry.pack()
            return f, lbl

        f1, self._dl_lbl    = _param(params_row, "dl_length_label", self._prev_len_var)
        f2, self._off_lbl   = _param(params_row, "offset_label",    self._offset_var)
        f3, self._dur_lbl   = _param(params_row, "duration_label",  self._duration_var)
        for f in (f1, f2, f3):
            f.pack(side="left", padx=(0, 24))

        self._explain_lbl = ctk.CTkLabel(
            settings_frame,
            text=T["explain_text"],
            font=ctk.CTkFont(size=10),
            text_color=("gray55", "gray55"),
            justify="left",
            anchor="w",
        )
        self._explain_lbl.pack(fill="x", padx=14, pady=(2, 6))

        steps_row = ctk.CTkFrame(settings_frame, fg_color="transparent")
        steps_row.pack(fill="x", padx=12, pady=(0, 10))

        self._step1_chk = ctk.CTkCheckBox(
            steps_row, text=T["step1_check"],
            variable=self._do_download, font=ctk.CTkFont(size=12))
        self._step1_chk.pack(side="left", padx=(0, 24))

        self._step2_chk = ctk.CTkCheckBox(
            steps_row, text=T["step2_check"],
            variable=self._do_slice, font=ctk.CTkFont(size=12))
        self._step2_chk.pack(side="left")

        # ── Action bar ────────────────────────────────────────────────────
        action_bar = ctk.CTkFrame(self.root, fg_color="transparent")
        action_bar.grid(row=4, column=0, sticky="ew", padx=12, pady=(4, 4))
        action_bar.grid_columnconfigure(2, weight=1)

        self._start_btn = ctk.CTkButton(
            action_bar, text=T["start_btn"], width=100, height=36,
            command=self._start, state="disabled",
            font=ctk.CTkFont(size=13, weight="bold"))
        self._start_btn.grid(row=0, column=0, padx=(0, 8))

        self._stop_btn = ctk.CTkButton(
            action_bar, text=T["stop_btn"], width=100, height=36,
            command=self._stop, state="disabled",
            fg_color=("gray70", "gray30"), hover_color=("gray60", "gray40"),
            font=ctk.CTkFont(size=13))
        self._stop_btn.grid(row=0, column=1)

        self._progress = ctk.CTkProgressBar(action_bar, mode="indeterminate", height=8)
        self._progress.grid(row=0, column=2, sticky="ew", padx=(16, 0))
        self._progress.set(0)

        # ── Log section ───────────────────────────────────────────────────
        self._log_label = ctk.CTkLabel(
            self.root, text=T["log_section"],
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("gray50", "gray60"),
        )
        self._log_label.grid(row=5, column=0, sticky="w", padx=18, pady=(6, 2))

        log_frame = ctk.CTkFrame(self.root, corner_radius=8)
        log_frame.grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 12))

        self._log_area = ctk.CTkTextbox(
            log_frame, height=160, state="disabled",
            font=ctk.CTkFont(family="Courier", size=11), wrap="none")
        self._log_area.pack(fill="both", expand=True, padx=4, pady=4)

    def _style_treeview(self):
        """Style the ttk Treeview to match the current CTk theme."""
        import customtkinter as ctk
        from tkinter import ttk

        try:
            from customtkinter.windows.widgets.theme import ThemeManager
            mode_idx = 1 if ctk.get_appearance_mode() == "Dark" else 0

            def _color(key, prop):
                val = ThemeManager.theme.get(key, {}).get(prop, "#333333")
                if isinstance(val, list):
                    return val[mode_idx]
                return val

            bg     = _color("CTkTextbox", "fg_color")
            fg     = _color("CTkLabel", "text_color")
            sel    = _color("CTkButton", "fg_color")
            head   = _color("CTkFrame", "top_fg_color")
        except Exception:
            mode = ctk.get_appearance_mode()
            bg   = "#1e1e1e" if mode == "Dark" else "#f5f5f5"
            fg   = "#e0e0e0" if mode == "Dark" else "#1a1a1a"
            sel  = "#1F6AA5" if mode == "Dark" else "#3B8ED0"
            head = "#2e2e2e" if mode == "Dark" else "#e8e8e8"

        style = self._ttk.Style()
        style.configure("Corpus.Treeview",
            background=bg, foreground=fg, fieldbackground=bg,
            borderwidth=0, rowheight=28)
        style.configure("Corpus.Treeview.Heading",
            background=head, foreground=fg, borderwidth=0, relief="flat")
        style.map("Corpus.Treeview",
            background=[("selected", sel)],
            foreground=[("selected", "#ffffff")])

    # ── Theme / language ──────────────────────────────────────────────────────

    def _on_theme_change(self, theme_name: str):
        save_config({"theme": theme_name, "lang": self._lang})
        # Relaunch with new theme applied before any widgets are created
        subprocess.Popen([sys.executable] + sys.argv)
        self.root.after(50, self.root.destroy)

    def _on_lang_change(self, lang: str):
        self._lang = lang
        save_config({"lang": lang})
        T = self._T()

        self.root.title(T["window_title"])
        self._files_label.configure(text=T["files_section"])
        self._tracks_label.configure(text=T["tracks_section"])
        self._settings_label.configure(text=T["settings_section"])
        self._log_label.configure(text=T["log_section"])
        self._lang_label.configure(text=T["language_label"])
        self._theme_label.configure(text=T["theme_label"])
        self._csv_lbl.configure(text=T["csv_label"])
        self._save_lbl.configure(text=T["save_label"])
        self._csv_browse_btn.configure(text=T["browse_btn"])
        self._out_browse_btn.configure(text=T["browse_btn"])
        self._dl_lbl.configure(text=T["dl_length_label"])
        self._off_lbl.configure(text=T["offset_label"])
        self._dur_lbl.configure(text=T["duration_label"])
        self._explain_lbl.configure(text=T["explain_text"])
        self._step1_chk.configure(text=T["step1_check"])
        self._step2_chk.configure(text=T["step2_check"])
        self._start_btn.configure(text=T["start_btn"])
        self._stop_btn.configure(text=T["stop_btn"])

        n = len(self._all_tracks)
        self._count_label.configure(
            text=T["status_loaded"].format(n=n) if n else T["no_csv_msg"])

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

    # ── CSV + search ──────────────────────────────────────────────────────────

    def _load_csv(self, path: str):
        tracks, err = load_tracks_from_csv(path)
        T = self._T()
        if err:
            self._count_label.configure(text=f"Error: {err}")
            self._all_tracks = []
            self._refresh_tree([])
            self._start_btn.configure(state="disabled")
            return
        self._all_tracks = tracks
        self._search_var.set("")
        self._refresh_tree(tracks)
        self._count_label.configure(text=T["status_loaded"].format(n=len(tracks)))
        self._start_btn.configure(state="normal")

    def _on_search(self, *_):
        T = self._T()
        q = self._search_var.get().lower()
        if not q:
            self._refresh_tree(self._all_tracks)
            self._count_label.configure(
                text=T["status_loaded"].format(n=len(self._all_tracks)))
            return
        filtered = [
            t for t in self._all_tracks
            if q in t["artist"].lower() or q in t["name"].lower()
        ]
        self._refresh_tree(filtered)
        self._count_label.configure(
            text=T["status_filtered"].format(n=len(filtered), m=len(self._all_tracks)))

    def _refresh_tree(self, tracks: list):
        self._tree.delete(*self._tree.get_children())
        for t in tracks:
            self._tree.insert("", "end", values=(t["artist"], t["name"]))

    # ── Run ───────────────────────────────────────────────────────────────────

    def _start(self):
        self._stop_event.clear()
        self._running = True
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._progress.start()
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

        try:
            prev_len = int(self._prev_len_var.get())
            offset   = float(self._offset_var.get())
            duration = float(self._duration_var.get())
        except ValueError:
            self._log_write("ERROR: Invalid number in settings — check Download length, Offset, and Duration.")
            self.root.after(0, self._on_done)
            return

        with _PrintRedirector(self._log_queue):
            try:
                if self._do_download.get():
                    run_download(csv_path, previews_dir, prev_len, self._stop_event)
                if self._do_slice.get() and not self._stop_event.is_set():
                    if os.path.isdir(previews_dir):
                        run_slice(previews_dir, grains_dir, offset, duration, self._stop_event)
                    else:
                        print("No previews folder found — run with Download enabled first.")
                print(f"\nAll done.  Previews: {previews_dir}  |  Grains: {grains_dir}")
            except Exception as e:
                print(f"ERROR: {e}")

        self.root.after(0, self._on_done)

    def _on_done(self):
        self._running = False
        self._progress.stop()
        self._progress.set(0)
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")

    # ── Log ───────────────────────────────────────────────────────────────────

    def _log_write(self, msg: str):
        self._log_queue.put(msg)

    def _poll_log(self):
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self._log_area.configure(state="normal")
                self._log_area.insert("end", msg + "\n")
                self._log_area.see("end")
                self._log_area.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) == 1:
        try:
            import customtkinter as ctk
        except ImportError:
            print("ERROR: customtkinter not installed. Run: pip install customtkinter")
            sys.exit(1)

        apply_startup_theme()
        root = ctk.CTk()
        CorpusBuilderUI(root)
        root.mainloop()
        return

    parser = argparse.ArgumentParser(
        description="Download Spotify preview clips and slice them into short grains.")
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

    print(f"\nAll done.  Previews: {previews_dir}  |  Grains: {grains_dir}")


if __name__ == "__main__":
    main()
