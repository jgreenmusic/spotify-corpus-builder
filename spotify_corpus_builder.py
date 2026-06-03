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
        "app_description": "Load a Spotify CSV, download audio previews from YouTube, and slice them into short grains for use as a sample corpus.",
        "files_section": "FILES", "language_label": "Language",
        "csv_label": "Load CSV File",
        "csv_hint": "CSV must have 'Track Name' and 'Artist Name(s)' columns.  Export any Spotify playlist free at exportify.net",
        "csv_error_cols": "No tracks found — make sure your CSV has 'Track Name' and 'Artist Name(s)' columns.\nExport from Spotify using exportify.net (free, no install needed).",
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
        "youtube_note": (
            "Note: This app does not use the Spotify API or download official Spotify audio. "
            "It searches YouTube by artist and track name and downloads the first N seconds of the result. "
            "Most tracks will match correctly, but some may return a live recording, cover, or alternate version instead of the studio track."
        ),
        "start_btn": "Start", "stop_btn": "Stop",
        "log_section": "LOG",
    },
    "Espanol": {
        "window_title": "Constructor de Corpus de Spotify",
        "app_description": "Carga un CSV de Spotify, descarga vistas previas de audio de YouTube y cortalas en granos cortos para usar como corpus de muestras.",
        "files_section": "ARCHIVOS", "language_label": "Idioma",
        "csv_label": "Cargar archivo CSV",
        "csv_hint": "El CSV debe tener columnas 'Track Name' y 'Artist Name(s)'.  Exporta cualquier lista de Spotify en exportify.net",
        "csv_error_cols": "No se encontraron pistas — verifica que el CSV tenga columnas 'Track Name' y 'Artist Name(s)'.\nExporta desde Spotify usando exportify.net (gratis, sin instalacion).",
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
        "youtube_note": (
            "Nota: Esta app no usa la API de Spotify ni descarga audio oficial de Spotify. "
            "Busca en YouTube por artista y titulo, y descarga los primeros N segundos del resultado. "
            "La mayoria de pistas coinciden correctamente, pero algunas pueden devolver una version en vivo, cover o alternativa en lugar del estudio."
        ),
        "start_btn": "Iniciar", "stop_btn": "Detener",
        "log_section": "REGISTRO",
    },
    "Deutsch": {
        "window_title": "Spotify Corpus Builder",
        "app_description": "Lade eine Spotify-CSV, lade Audio-Vorschauen von YouTube herunter und schneide sie in kurze Korner fur einen Sample-Corpus.",
        "files_section": "DATEIEN", "language_label": "Sprache",
        "csv_label": "CSV-Datei laden",
        "csv_hint": "CSV muss Spalten 'Track Name' und 'Artist Name(s)' enthalten.  Exportiere Spotify-Playlists kostenlos auf exportify.net",
        "csv_error_cols": "Keine Titel gefunden — stelle sicher, dass die CSV Spalten 'Track Name' und 'Artist Name(s)' hat.\nExportieren mit exportify.net (kostenlos, keine Installation).",
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
        "youtube_note": (
            "Hinweis: Diese App verwendet nicht die Spotify-API und ladt kein offizielles Spotify-Audio herunter. "
            "Sie sucht auf YouTube nach Kunstler und Titel und ladt die ersten N Sekunden herunter. "
            "Die meisten Titel werden korrekt gefunden, aber einige konnen eine Live-Version, ein Cover oder eine alternative Version ergeben."
        ),
        "start_btn": "Start", "stop_btn": "Stopp",
        "log_section": "PROTOKOLL",
    },
    "Chinese": {
        "window_title": "Spotify 语料库构建器",
        "app_description": "加载 Spotify CSV，从 YouTube 下载音频预览，并将其切割成短片段，用作采样语料库。",
        "files_section": "文件", "language_label": "语言",
        "csv_label": "加载 CSV 文件",
        "csv_hint": "CSV 必须包含 'Track Name' 和 'Artist Name(s)' 列。  在 exportify.net 免费导出任意 Spotify 播放列表",
        "csv_error_cols": "未找到曲目 — 请确认 CSV 包含 'Track Name' 和 'Artist Name(s)' 列。\n可在 exportify.net 从 Spotify 导出（免费，无需安装）。",
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
        "youtube_note": (
            "注意：本应用不使用 Spotify API，也不下载官方 Spotify 音频。"
            "它通过艺术家名和曲目名在 YouTube 上搜索，并下载结果的前 N 秒。"
            "大多数曲目可以正确匹配，但部分可能返回现场录音、翻唱版或其他版本，而非录音室原版。"
        ),
        "start_btn": "开始", "stop_btn": "停止",
        "log_section": "日志",
    },
    "Japanese": {
        "window_title": "Spotify コーパスビルダー",
        "app_description": "Spotify の CSV を読み込み、YouTube から音声プレビューをダウンロードし、サンプルコーパス用の短いグレインにスライスします。",
        "files_section": "ファイル", "language_label": "言語",
        "csv_label": "CSV ファイルを読み込む",
        "csv_hint": "CSV には 'Track Name' と 'Artist Name(s)' 列が必要です。  exportify.net で Spotify プレイリストを無料エクスポート",
        "csv_error_cols": "トラックが見つかりません — CSV に 'Track Name' と 'Artist Name(s)' 列があるか確認してください。\nexportify.net で Spotify からエクスポートできます（無料・インストール不要）。",
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
        "youtube_note": (
            "注意：このアプリは Spotify API を使用せず、Spotify の公式音声もダウンロードしません。"
            "アーティスト名とトラック名で YouTube を検索し、結果の最初の N 秒をダウンロードします。"
            "ほとんどのトラックは正しくマッチしますが、ライブ録音、カバー、別バージョンが返される場合があります。"
        ),
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
            return [], "No tracks found — check Track Name and Artist Name(s) columns."
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

        self.root.title(self._T()["window_title"])
        self.root.minsize(860, 720)

        self._build_ui()
        self._poll_log()

    def _T(self) -> dict:
        return TRANSLATIONS.get(self._lang, TRANSLATIONS["English"])

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        ctk = self.ctk
        import tkinter as _tk
        T = self._T()

        self.root.grid_columnconfigure(0, weight=1)
        # row 4 = tracks frame — the only row that expands vertically
        self.root.grid_rowconfigure(4, weight=1)

        # ── Header ────────────────────────────────────────────────────────
        # row 0
        header = ctk.CTkFrame(self.root, corner_radius=0, height=86)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        header.grid_propagate(False)

        title_block = ctk.CTkFrame(header, fg_color="transparent")
        title_block.grid(row=0, column=0, padx=20, pady=10, sticky="w")

        ctk.CTkLabel(
            title_block,
            text="Spotify Corpus Builder",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w")

        self._desc_label = ctk.CTkLabel(
            title_block,
            text=T["app_description"],
            font=ctk.CTkFont(size=13),
            text_color=("gray45", "gray50"),
            wraplength=440,
            justify="left",
        )
        self._desc_label.pack(anchor="w", pady=(3, 0))

        lang_block = ctk.CTkFrame(header, fg_color="transparent")
        lang_block.grid(row=0, column=2, padx=20, pady=10, sticky="e")

        self._lang_label = ctk.CTkLabel(lang_block, text=T["language_label"],
                                        font=ctk.CTkFont(size=13))
        self._lang_label.pack(side="left", padx=(0, 6))

        self._lang_var = _tk.StringVar(value=self._lang)
        ctk.CTkComboBox(
            lang_block,
            variable=self._lang_var,
            values=list(TRANSLATIONS.keys()),
            width=130,
            height=32,
            command=self._on_lang_change,
        ).pack(side="left")

        # ── FILES label — row 1 ───────────────────────────────────────────
        self._files_label = ctk.CTkLabel(
            self.root, text=T["files_section"],
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("gray40", "gray55"),
        )
        self._files_label.grid(row=1, column=0, sticky="w", padx=20, pady=(16, 4))

        # ── FILES frame — row 2 ───────────────────────────────────────────
        files_frame = ctk.CTkFrame(self.root, corner_radius=10)
        files_frame.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 6))
        files_frame.grid_columnconfigure(1, weight=1)

        self._csv_lbl = ctk.CTkLabel(files_frame, text=T["csv_label"],
                                     font=ctk.CTkFont(size=14), anchor="w")
        self._csv_lbl.grid(row=0, column=0, padx=(16, 12), pady=(16, 4), sticky="w")

        self._csv_var = _tk.StringVar()
        ctk.CTkEntry(files_frame, textvariable=self._csv_var, state="readonly",
                     height=36, font=ctk.CTkFont(size=13)
                     ).grid(row=0, column=1, padx=4, pady=(16, 4), sticky="ew")

        self._csv_browse_btn = ctk.CTkButton(
            files_frame, text=T["browse_btn"], width=100, height=36,
            font=ctk.CTkFont(size=13), command=self._browse_csv)
        self._csv_browse_btn.grid(row=0, column=2, padx=(4, 16), pady=(16, 4))

        self._csv_hint_lbl = ctk.CTkLabel(
            files_frame, text=T["csv_hint"],
            font=ctk.CTkFont(size=12),
            text_color=("gray45", "gray50"),
            justify="left", anchor="w",
            wraplength=780,
        )
        self._csv_hint_lbl.grid(row=1, column=0, columnspan=3,
                                padx=16, pady=(0, 12), sticky="w")

        self._save_lbl = ctk.CTkLabel(files_frame, text=T["save_label"],
                                      font=ctk.CTkFont(size=14), anchor="w")
        self._save_lbl.grid(row=2, column=0, padx=(16, 12), pady=(4, 16), sticky="w")

        self._out_var = _tk.StringVar(value=os.path.join(SCRIPT_DIR, "output"))
        ctk.CTkEntry(files_frame, textvariable=self._out_var,
                     height=36, font=ctk.CTkFont(size=13)
                     ).grid(row=2, column=1, padx=4, pady=(4, 16), sticky="ew")

        self._out_browse_btn = ctk.CTkButton(
            files_frame, text=T["browse_btn"], width=100, height=36,
            font=ctk.CTkFont(size=13), command=self._browse_output)
        self._out_browse_btn.grid(row=2, column=2, padx=(4, 16), pady=(4, 16))

        # ── TRACKS label — row 3 ──────────────────────────────────────────
        self._tracks_label = ctk.CTkLabel(
            self.root, text=T["tracks_section"],
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("gray40", "gray55"),
        )
        self._tracks_label.grid(row=3, column=0, sticky="w", padx=20, pady=(6, 4))

        # ── TRACKS frame — row 4 (expands) ────────────────────────────────
        tracks_outer = ctk.CTkFrame(self.root, corner_radius=10)
        tracks_outer.grid(row=4, column=0, sticky="nsew", padx=14, pady=(0, 6))
        tracks_outer.grid_columnconfigure(0, weight=1)
        tracks_outer.grid_rowconfigure(1, weight=1)

        search_row = ctk.CTkFrame(tracks_outer, fg_color="transparent")
        search_row.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        search_row.grid_columnconfigure(0, weight=1)

        self._search_var = _tk.StringVar()
        self._search_var.trace_add("write", self._on_search)
        ctk.CTkEntry(
            search_row,
            textvariable=self._search_var,
            placeholder_text=T["search_placeholder"],
            height=38,
            font=ctk.CTkFont(size=14),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 12))

        self._count_label = ctk.CTkLabel(
            search_row, text=T["no_csv_msg"],
            font=ctk.CTkFont(size=13),
            text_color=("gray45", "gray50"),
        )
        self._count_label.grid(row=0, column=1, sticky="e")

        tree_frame = ctk.CTkFrame(tracks_outer, fg_color="transparent")
        tree_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)

        self._style_treeview()

        self._tree = self._ttk.Treeview(
            tree_frame, columns=("artist", "track"),
            show="headings", height=10, style="Corpus.Treeview")
        self._tree.heading("artist", text="Artist")
        self._tree.heading("track",  text="Track")
        self._tree.column("artist", width=250, minwidth=100)
        self._tree.column("track",  width=350, minwidth=100)

        vsb = self._ttk.Scrollbar(tree_frame, orient="vertical",   command=self._tree.yview)
        hsb = self._ttk.Scrollbar(tree_frame, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        # ── SETTINGS label — row 5 ────────────────────────────────────────
        self._settings_label = ctk.CTkLabel(
            self.root, text=T["settings_section"],
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("gray40", "gray55"),
        )
        self._settings_label.grid(row=5, column=0, sticky="w", padx=20, pady=(6, 4))

        # ── SETTINGS frame — row 6 ────────────────────────────────────────
        settings_frame = ctk.CTkFrame(self.root, corner_radius=10)
        settings_frame.grid(row=6, column=0, sticky="ew", padx=14, pady=(0, 6))

        import tkinter as _tk2
        self._prev_len_var = _tk2.StringVar(value="30")
        self._offset_var   = _tk2.StringVar(value="5.0")
        self._duration_var = _tk2.StringVar(value="1.5")
        self._do_download  = _tk2.BooleanVar(value=True)
        self._do_slice     = _tk2.BooleanVar(value=True)

        params_row = ctk.CTkFrame(settings_frame, fg_color="transparent")
        params_row.pack(fill="x", padx=16, pady=(14, 6))

        def _param(parent, label_key, var, width=84):
            f = ctk.CTkFrame(parent, fg_color="transparent")
            lbl = ctk.CTkLabel(f, text=T[label_key], font=ctk.CTkFont(size=13),
                               wraplength=200, justify="left", anchor="w")
            lbl.pack(anchor="w", fill="x")
            entry = ctk.CTkEntry(f, textvariable=var, width=width, height=36,
                                 font=ctk.CTkFont(size=14), justify="center")
            entry.pack(pady=(6, 0))
            return f, lbl

        f1, self._dl_lbl  = _param(params_row, "dl_length_label", self._prev_len_var)
        f2, self._off_lbl = _param(params_row, "offset_label",    self._offset_var)
        f3, self._dur_lbl = _param(params_row, "duration_label",  self._duration_var)
        for f in (f1, f2, f3):
            f.pack(side="left", padx=(0, 32))

        self._explain_lbl = ctk.CTkLabel(
            settings_frame,
            text=T["explain_text"],
            font=ctk.CTkFont(size=12),
            text_color=("gray45", "gray50"),
            justify="left",
            anchor="w",
            wraplength=800,
        )
        self._explain_lbl.pack(fill="x", padx=16, pady=(6, 10))

        # Checkboxes stacked vertically so long translated text never overflows
        steps_frame = ctk.CTkFrame(settings_frame, fg_color="transparent")
        steps_frame.pack(fill="x", padx=16, pady=(0, 16))

        self._step1_chk = ctk.CTkCheckBox(
            steps_frame, text=T["step1_check"],
            variable=self._do_download, font=ctk.CTkFont(size=14))
        self._step1_chk.pack(anchor="w", pady=(0, 8))

        self._step2_chk = ctk.CTkCheckBox(
            steps_frame, text=T["step2_check"],
            variable=self._do_slice, font=ctk.CTkFont(size=14))
        self._step2_chk.pack(anchor="w")

        # YouTube transparency note
        divider = ctk.CTkFrame(settings_frame, height=1,
                               fg_color=("gray80", "gray30"))
        divider.pack(fill="x", padx=16, pady=(12, 0))

        self._youtube_note_lbl = ctk.CTkLabel(
            settings_frame,
            text=T["youtube_note"],
            font=ctk.CTkFont(size=12),
            text_color=("gray45", "gray50"),
            justify="left",
            anchor="w",
            wraplength=800,
        )
        self._youtube_note_lbl.pack(fill="x", padx=16, pady=(8, 16))

        # ── Action bar — row 7 ────────────────────────────────────────────
        action_bar = ctk.CTkFrame(self.root, fg_color="transparent")
        action_bar.grid(row=7, column=0, sticky="ew", padx=14, pady=(4, 6))
        action_bar.grid_columnconfigure(2, weight=1)

        self._start_btn = ctk.CTkButton(
            action_bar, text=T["start_btn"], width=120, height=42,
            command=self._start, state="disabled",
            font=ctk.CTkFont(size=15, weight="bold"))
        self._start_btn.grid(row=0, column=0, padx=(0, 10))

        self._stop_btn = ctk.CTkButton(
            action_bar, text=T["stop_btn"], width=120, height=42,
            command=self._stop, state="disabled",
            fg_color=("gray70", "gray30"), hover_color=("gray60", "gray40"),
            font=ctk.CTkFont(size=15))
        self._stop_btn.grid(row=0, column=1)

        self._progress = ctk.CTkProgressBar(action_bar, mode="indeterminate", height=10)
        self._progress.grid(row=0, column=2, sticky="ew", padx=(18, 0))
        self._progress.set(0)

        # ── LOG label — row 8 ─────────────────────────────────────────────
        self._log_label = ctk.CTkLabel(
            self.root, text=T["log_section"],
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("gray40", "gray55"),
        )
        self._log_label.grid(row=8, column=0, sticky="w", padx=20, pady=(4, 4))

        # ── LOG frame — row 9 ─────────────────────────────────────────────
        log_frame = ctk.CTkFrame(self.root, corner_radius=10)
        log_frame.grid(row=9, column=0, sticky="ew", padx=14, pady=(0, 14))

        self._log_area = ctk.CTkTextbox(
            log_frame, height=170, state="disabled",
            font=ctk.CTkFont(family="Courier", size=13), wrap="none")
        self._log_area.pack(fill="both", expand=True, padx=6, pady=6)

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
        self._csv_lbl.configure(text=T["csv_label"])
        self._csv_hint_lbl.configure(text=T["csv_hint"])
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
        self._desc_label.configure(text=T.get("app_description", ""))
        self._youtube_note_lbl.configure(text=T.get("youtube_note", ""))

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
            # Use specific column-format error if no tracks were found
            if "Track Name" in err or "no tracks" in err.lower():
                msg = T.get("csv_error_cols", err)
            else:
                msg = err
            self._count_label.configure(text="Error — see log")
            self._log_write(msg)
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
