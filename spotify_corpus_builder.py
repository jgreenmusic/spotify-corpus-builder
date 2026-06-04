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
    print("yt-dlp is not installed. Run setup.bat (Windows) or setup.sh (Mac) to fix this.")
    sys.exit(1)

_strategy_wins = {"energy": 0, "onsets": 0, "spectral": 0}


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
    print("ffmpeg is not installed or not found on your system.")
    print("  Windows: open PowerShell and run   winget install ffmpeg")
    print("  Mac:     open Terminal and run      brew install ffmpeg")
    print("Then restart the app.")
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
                 stop_event: threading.Event = None, ai_opts: dict = None,
                 metadata: dict = None, tracks: list = None):
    os.makedirs(previews_dir, exist_ok=True)
    if tracks is None:
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

    ai_opts = ai_opts or {}
    if metadata is None:
        metadata = {}

    ai_active = any(ai_opts.get(k) for k in ["smart_grain", "detect_versions", "extract_features"])
    librosa_ok = _check_librosa() if ai_active else False

    for i, track in enumerate(tracks, 1):
        if stop_event and stop_event.is_set():
            print("\nStopped by user.")
            break
        filename = sanitize(f"{track['artist']} - {track['name']}")
        wav_path = os.path.join(previews_dir, filename + ".wav")
        if os.path.exists(wav_path):
            print(f"  [{i}/{len(tracks)}] [exists]  {filename}.wav")
            skipped += 1
            if librosa_ok and filename not in metadata:
                _run_ai_on_track(wav_path, filename, ai_opts, metadata)
            continue
        print(f"  [{i}/{len(tracks)}] [fetch]   {track['artist']} - {track['name']}")
        if download_track(track["artist"], track["name"], wav_path, preview_length):
            print(f"  [{i}/{len(tracks)}] [done]    {filename}.wav")
            downloaded += 1
            if librosa_ok:
                _run_ai_on_track(wav_path, filename, ai_opts, metadata)
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
              stop_event: threading.Event = None, ai_opts: dict = None,
              metadata: dict = None, randomize_cut: bool = False,
              dur_min: float = 0.5, dur_max: float = 3.0, preview_length: int = 30):
    import random as _r
    os.makedirs(grains_dir, exist_ok=True)
    ffmpeg = ffmpeg_bin()
    wav_files = sorted(f for f in os.listdir(previews_dir) if f.lower().endswith(".wav"))
    total = len(wav_files)

    ai_opts = ai_opts or {}
    metadata = metadata or {}
    use_smart = ai_opts.get("smart_grain") and metadata

    if randomize_cut:
        print(f"\n=== SLICE ({total} files -> random cut, duration {dur_min}–{dur_max}s) ===")
    else:
        print(f"\n=== SLICE ({total} files -> offset {offset}s, grain {duration}s) ===")
    if use_smart:
        print("  Smart grain selection ON — using AI-suggested offsets where available.")
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

        track_key = fname[:-4] if fname.lower().endswith(".wav") else fname

        if randomize_cut:
            effective_duration = round(_r.uniform(dur_min, dur_max), 2)
            max_offset = max(0.0, preview_length - effective_duration - 1.0)
            effective_offset = round(_r.uniform(0.0, max_offset), 2)
            print(f"  [{i}/{total}] [cut]     offset={effective_offset}s dur={effective_duration}s  {fname}")
        else:
            effective_offset = offset
            effective_duration = duration
            if use_smart and track_key in metadata:
                suggested = metadata[track_key].get("suggested_offset")
                if suggested is not None:
                    effective_offset = suggested

        if slice_preview(src, dst, effective_offset, effective_duration, ffmpeg):
            print(f"  [{i}/{total}] [sliced]  {fname}")
            done += 1
        else:
            print(f"  [{i}/{total}] [failed]  {fname}")
            failed += 1

    print(f"\nSlice complete - sliced: {done}  skipped: {skipped}  failed: {failed}")


# ── AI Analysis ───────────────────────────────────────────────────────────────

def _check_librosa() -> bool:
    try:
        import librosa  # noqa: F401
        return True
    except ImportError:
        print("  [AI] librosa is not installed. Run setup.bat or setup.sh to enable AI analysis.")
        return False


def _librosa_available() -> bool:
    try:
        import librosa  # noqa: F401
        return True
    except ImportError:
        return False


def _run_ai_on_track(wav_path: str, filename: str, ai_opts: dict, metadata: dict):
    entry = metadata.setdefault(filename, {})
    if ai_opts.get("extract_features"):
        feats = analyze_audio(wav_path)
        if feats:
            entry["features"] = feats
            print(f"  [AI] {filename[:50]}: tempo={feats.get('tempo', 0):.0f} key={feats.get('estimated_key', '?')}")
    if ai_opts.get("smart_grain"):
        duration = ai_opts.get("duration", 1.5)
        offset = find_best_grain(wav_path, duration)
        entry["suggested_offset"] = offset
    if ai_opts.get("detect_versions"):
        result = detect_wrong_version(wav_path)
        entry["version_flag"] = result.get("flag", "ok")
        entry["version_confidence"] = result.get("confidence", 0.0)
        if result.get("flag", "ok") != "ok":
            print(f"  [AI] {filename[:50]}: [{result['flag']}] conf={result['confidence']:.2f}")


def analyze_audio(wav_path: str) -> dict:
    try:
        import librosa
        import numpy as np
        y, sr = librosa.load(wav_path, sr=None, mono=True)
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        rms = float(np.mean(librosa.feature.rms(y=y)))
        sc = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))
        zcr = float(np.mean(librosa.feature.zero_crossing_rate(y)))
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        key_idx = int(np.argmax(np.mean(chroma, axis=1)))
        keys = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        return {
            "tempo": float(tempo),
            "rms_energy": rms,
            "spectral_centroid": sc,
            "zero_crossing_rate": zcr,
            "estimated_key": keys[key_idx],
        }
    except Exception:
        return {}


def find_best_grain(wav_path: str, duration: float) -> float:
    try:
        import librosa
        import numpy as np
        y, sr = librosa.load(wav_path, sr=None, mono=True)
        total_dur = len(y) / sr
        margin = 2.0

        if total_dur < duration + 2 * margin:
            return max(0.0, total_dur / 4)

        hop = 512
        win_f = max(1, int(duration * sr) // hop)
        start_f = int(margin * sr) // hop
        end_f = int((total_dur - margin - duration) * sr) // hop

        if start_f >= end_f:
            return margin

        n = min(end_f, len(librosa.feature.rms(y=y, hop_length=hop)[0]) - win_f)

        # Strategy 1: max RMS energy
        rms = librosa.feature.rms(y=y, hop_length=hop)[0]
        energy_scores = [float(np.mean(rms[i:i + win_f])) for i in range(start_f, n)]
        best_e_i = int(np.argmax(energy_scores)) if energy_scores else 0
        mean_e = float(np.mean(energy_scores)) if energy_scores else 1e-9
        energy_conf = (energy_scores[best_e_i] - mean_e) / (mean_e + 1e-9) if energy_scores else 0.0

        # Strategy 2: max onset density
        onset_frames = librosa.onset.onset_detect(y=y, sr=sr, hop_length=hop)
        onset_counts = [int(np.sum((onset_frames >= i) & (onset_frames < i + win_f)))
                        for i in range(start_f, n)]
        best_o_i = int(np.argmax(onset_counts)) if onset_counts else 0
        mean_o = float(np.mean(onset_counts)) if onset_counts else 0.0
        onset_conf = (onset_counts[best_o_i] - mean_o) / (mean_o + 1.0) if onset_counts else 0.0

        # Strategy 3: max spectral centroid variance
        sc_frames = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=hop)[0]
        sc_n = min(n, len(sc_frames) - win_f)
        sc_scores = [float(np.var(sc_frames[i:i + win_f])) for i in range(start_f, sc_n)]
        best_sc_i = int(np.argmax(sc_scores)) if sc_scores else 0
        mean_sc = float(np.mean(sc_scores)) if sc_scores else 1e-9
        sc_conf = (sc_scores[best_sc_i] - mean_sc) / (mean_sc + 1e-9) if sc_scores else 0.0

        strategies = {
            "energy":   (best_e_i,  energy_conf),
            "onsets":   (best_o_i,  onset_conf),
            "spectral": (best_sc_i, sc_conf),
        }

        # Boost preferred strategy slightly to act as tiebreaker
        config = load_config()
        preferred = config.get("preferred_grain_strategy", "energy")
        boosted = {
            k: (idx, conf + (0.05 if k == preferred else 0.0))
            for k, (idx, conf) in strategies.items()
        }

        winner_name = max(boosted, key=lambda k: boosted[k][1])
        winner_f_i = strategies[winner_name][0]
        winner_offset = float((start_f + winner_f_i) * hop) / sr

        _strategy_wins[winner_name] = _strategy_wins.get(winner_name, 0) + 1
        print(f"  [AI] best grain at {winner_offset:.1f}s [{winner_name}]")
        return winner_offset
    except Exception:
        return 5.0


def detect_wrong_version(wav_path: str) -> dict:
    try:
        import librosa
        import numpy as np
        y, sr = librosa.load(wav_path, sr=None, mono=True)

        rms = librosa.feature.rms(y=y)[0]
        energy_var = float(np.var(rms))
        zcr = librosa.feature.zero_crossing_rate(y)[0]
        zcr_var = float(np.var(zcr))
        sf = librosa.feature.spectral_flatness(y=y)[0]
        mean_flatness = float(np.mean(sf))

        live_score = 0.0
        if energy_var > 0.005:
            live_score += 0.4
        if zcr_var > 0.002:
            live_score += 0.3
        if mean_flatness < 0.01:
            live_score += 0.3

        cover_score = 0.0
        if mean_flatness > 0.1:
            cover_score += 0.4
        try:
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
            if float(tempo) < 40 or float(tempo) > 200:
                cover_score += 0.3
        except Exception:
            pass

        if live_score > cover_score and live_score >= 0.65:
            return {"flag": "live?", "confidence": round(live_score, 2),
                    "reason": "high energy variance and zcr variance"}
        if cover_score >= 0.65:
            return {"flag": "cover?", "confidence": round(cover_score, 2),
                    "reason": "unusual spectral profile"}
        return {"flag": "ok", "confidence": round(max(live_score, cover_score), 2), "reason": ""}
    except Exception:
        return {"flag": "ok", "confidence": 0.0, "reason": ""}


def cluster_corpus(grains_dir: str, n_clusters: int = 5) -> dict:
    try:
        import librosa
        import numpy as np
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        print("  [AI] librosa or scikit-learn not installed. Run setup.bat or setup.sh.")
        return {}
    try:
        wav_files = sorted(f for f in os.listdir(grains_dir) if f.lower().endswith(".wav"))
        if not wav_files:
            return {}

        print(f"  [AI] Clustering {len(wav_files)} grains...")
        features = []
        valid_files = []
        for fname in wav_files:
            path = os.path.join(grains_dir, fname)
            try:
                y, sr = librosa.load(path, sr=None, mono=True)
                rms = float(np.mean(librosa.feature.rms(y=y)))
                sc = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))
                zcr = float(np.mean(librosa.feature.zero_crossing_rate(y)))
                tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
                features.append([rms, sc, zcr, float(tempo)])
                valid_files.append(fname)
            except Exception:
                pass

        if len(valid_files) < 2:
            return {f: 0 for f in valid_files}

        k = max(2, min(n_clusters, len(valid_files) // 20))
        X = StandardScaler().fit_transform(features)
        labels = KMeans(n_clusters=k, n_init=10, random_state=42).fit_predict(X)
        print(f"  [AI] Clustered into {k} groups.")
        return {fname: int(label) for fname, label in zip(valid_files, labels)}
    except Exception as e:
        print(f"  [AI] Clustering failed: {e}")
        return {}


def run_clap_analysis(grains_dir: str, output_dir: str) -> None:
    try:
        import laion_clap  # noqa: F401
    except ImportError:
        print("  [CLAP] laion-clap is not installed. Install it separately and re-run.")
        print("         Note: CLAP requires a 2GB model download on first use.")
        return
    try:
        import json as _json
        import numpy as np
        import laion_clap
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
            _json.dump(result, fh, indent=2)
        print(f"  [CLAP] coords.json saved to {out_path}")
    except Exception as e:
        print(f"  [CLAP] Error: {e}")


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

TRANSLATIONS = {
    "English": {
        "window_title": "Spotify Corpus Builder",
        "app_description": "Load a Spotify CSV, download audio previews from YouTube, and slice them into short grains for use as a sample corpus.",
        "files_section": "FILES", "language_label": "Language",
        "csv_label": "Load CSV File",
        "csv_hint": "CSV must have 'Track Name' and 'Artist Name(s)' columns.  Export any Spotify playlist free at exportify.net",
        "csv_error_cols": "No tracks found. Make sure your CSV has 'Track Name' and 'Artist Name(s)' columns.\nExport from Spotify using exportify.net (free, no install needed).",
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
        "ai_section":            "AI ANALYSIS",
        "smart_grain_check":     "Smart grain selection  (find the best moment automatically)",
        "detect_versions_check": "Flag suspected wrong versions  (live recordings, covers)",
        "extract_features_check":"Extract audio features  (tempo, energy, key per track)",
        "cluster_check":         "Cluster corpus by similarity  (groups grains after slicing)",
        "clap_check":            "CLAP embeddings  (optional — requires laion-clap, ~2GB model)",
        "ai_requires_note":      "Smart analysis requires librosa. Run setup.bat or setup.sh to install it.",
        "start_btn": "Start", "stop_btn": "Stop",
        "log_section": "LOG",
    },
    "Espanol": {
        "window_title": "Constructor de Corpus de Spotify",
        "app_description": "Carga un CSV de Spotify, descarga vistas previas de audio de YouTube y cortalas en granos cortos para usar como corpus de muestras.",
        "files_section": "ARCHIVOS", "language_label": "Idioma",
        "csv_label": "Cargar archivo CSV",
        "csv_hint": "El CSV debe tener columnas 'Track Name' y 'Artist Name(s)'.  Exporta cualquier lista de Spotify en exportify.net",
        "csv_error_cols": "No se encontraron pistas. Verifica que el CSV tenga columnas 'Track Name' y 'Artist Name(s)'.\nExporta desde Spotify usando exportify.net (gratis, sin instalacion).",
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
        "ai_section":            "ANALISIS IA",
        "smart_grain_check":     "Seleccion inteligente de grano  (encuentra el mejor momento automaticamente)",
        "detect_versions_check": "Marcar versiones incorrectas  (grabaciones en vivo, covers)",
        "extract_features_check":"Extraer caracteristicas de audio  (tempo, energia, tono por pista)",
        "cluster_check":         "Agrupar corpus por similitud  (agrupa granos despues del corte)",
        "clap_check":            "Embeddings CLAP  (opcional — requiere laion-clap, 2GB modelo)",
        "ai_requires_note":      "El analisis inteligente requiere librosa. Ejecuta setup.bat o setup.sh para instalarlo.",
        "start_btn": "Iniciar", "stop_btn": "Detener",
        "log_section": "REGISTRO",
    },
    "Deutsch": {
        "window_title": "Spotify Corpus Builder",
        "app_description": "Lade eine Spotify-CSV, lade Audio-Vorschauen von YouTube herunter und schneide sie in kurze Korner fur einen Sample-Corpus.",
        "files_section": "DATEIEN", "language_label": "Sprache",
        "csv_label": "CSV-Datei laden",
        "csv_hint": "CSV muss Spalten 'Track Name' und 'Artist Name(s)' enthalten.  Exportiere Spotify-Playlists kostenlos auf exportify.net",
        "csv_error_cols": "Keine Titel gefunden. Stelle sicher, dass die CSV Spalten 'Track Name' und 'Artist Name(s)' hat.\nExportieren mit exportify.net (kostenlos, keine Installation).",
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
        "ai_section":            "KI-ANALYSE",
        "smart_grain_check":     "Intelligente Kornauswahl  (besten Moment automatisch finden)",
        "detect_versions_check": "Falsche Versionen markieren  (Live-Aufnahmen, Cover)",
        "extract_features_check":"Audio-Merkmale extrahieren  (Tempo, Energie, Tonart pro Titel)",
        "cluster_check":         "Corpus nach Ahnlichkeit clustern  (gruppiert Korner nach dem Schneiden)",
        "clap_check":            "CLAP-Einbettungen  (optional — erfordert laion-clap, ca. 2GB Modell)",
        "ai_requires_note":      "Intelligente Analyse erfordert librosa. Fuhre setup.bat oder setup.sh aus.",
        "start_btn": "Start", "stop_btn": "Stopp",
        "log_section": "PROTOKOLL",
    },
    "Chinese": {
        "window_title": "Spotify 语料库构建器",
        "app_description": "加载 Spotify CSV，从 YouTube 下载音频预览，并将其切割成短片段，用作采样语料库。",
        "files_section": "文件", "language_label": "语言",
        "csv_label": "加载 CSV 文件",
        "csv_hint": "CSV 必须包含 'Track Name' 和 'Artist Name(s)' 列。  在 exportify.net 免费导出任意 Spotify 播放列表",
        "csv_error_cols": "未找到曲目。请确认 CSV 包含 'Track Name' 和 'Artist Name(s)' 列。\n可在 exportify.net 从 Spotify 导出（免费，无需安装）。",
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
        "ai_section":            "AI 分析",
        "smart_grain_check":     "智能音粒选择  （自动找到最佳时刻）",
        "detect_versions_check": "标记疑似错误版本  （现场录音、翻唱）",
        "extract_features_check":"提取音频特征  （每首曲目的节奏、能量、调性）",
        "cluster_check":         "按相似度聚类语料库  （切片后对音粒进行分组）",
        "clap_check":            "CLAP 嵌入  （可选 — 需要 laion-clap，约 2GB 模型）",
        "ai_requires_note":      "智能分析需要 librosa。请运行 setup.bat 或 setup.sh 进行安装。",
        "start_btn": "开始", "stop_btn": "停止",
        "log_section": "日志",
    },
    "Japanese": {
        "window_title": "Spotify コーパスビルダー",
        "app_description": "Spotify の CSV を読み込み、YouTube から音声プレビューをダウンロードし、サンプルコーパス用の短いグレインにスライスします。",
        "files_section": "ファイル", "language_label": "言語",
        "csv_label": "CSV ファイルを読み込む",
        "csv_hint": "CSV には 'Track Name' と 'Artist Name(s)' 列が必要です。  exportify.net で Spotify プレイリストを無料エクスポート",
        "csv_error_cols": "トラックが見つかりません。CSV に 'Track Name' と 'Artist Name(s)' 列があるか確認してください。\nexportify.net で Spotify からエクスポートできます（無料・インストール不要）。",
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
        "ai_section":            "AI 分析",
        "smart_grain_check":     "スマートグレイン選択  （最適な瞬間を自動検出）",
        "detect_versions_check": "不正バージョンにフラグ  （ライブ録音、カバー）",
        "extract_features_check":"音声特徴を抽出  （トラックごとのテンポ、エネルギー、キー）",
        "cluster_check":         "コーパスを類似度でクラスタリング  （スライス後にグレインをグループ化）",
        "clap_check":            "CLAP エンベディング  （オプション — laion-clap 必要、約 2GB）",
        "ai_requires_note":      "スマート分析には librosa が必要です。setup.bat または setup.sh を実行してください。",
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

        config     = load_config()
        self._lang = config.get("lang", "English")
        self._librosa_ok = _librosa_available()

        self.root.title(self._T()["window_title"])
        self.root.minsize(860, 500)
        self.root.geometry("900x700")

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
        self.root.grid_rowconfigure(0, weight=1)

        self._scroll = ctk.CTkScrollableFrame(self.root)
        self._scroll.grid(row=0, column=0, sticky="nsew")
        self._scroll.grid_columnconfigure(0, weight=1)

        # ── Header — row 0 ────────────────────────────────────────────────
        header = ctk.CTkFrame(self._scroll, corner_radius=0, height=86)
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
            self._scroll, text=T["files_section"],
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("gray40", "gray55"),
        )
        self._files_label.grid(row=1, column=0, sticky="w", padx=20, pady=(16, 4))

        # ── FILES frame — row 2 ───────────────────────────────────────────
        files_frame = ctk.CTkFrame(self._scroll, corner_radius=10)
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
        self._out_browse_btn.grid(row=2, column=2, padx=(4, 16), pady=(4, 4))

        self._audio_lbl = ctk.CTkLabel(
            files_frame,
            text="Audio folder  (optional — load existing WAVs, skips download)",
            font=ctk.CTkFont(size=14), anchor="w")
        self._audio_lbl.grid(row=3, column=0, padx=(16, 12), pady=(4, 16), sticky="w")

        self._audio_folder_var = _tk.StringVar()
        ctk.CTkEntry(files_frame, textvariable=self._audio_folder_var,
                     height=36, font=ctk.CTkFont(size=13)
                     ).grid(row=3, column=1, padx=4, pady=(4, 16), sticky="ew")

        audio_btn_frame = ctk.CTkFrame(files_frame, fg_color="transparent")
        audio_btn_frame.grid(row=3, column=2, padx=(4, 16), pady=(4, 16))
        ctk.CTkButton(
            audio_btn_frame, text=T["browse_btn"], width=68, height=36,
            font=ctk.CTkFont(size=13), command=self._browse_audio_folder
        ).pack(side="left", padx=(0, 4))
        ctk.CTkButton(
            audio_btn_frame, text="✕", width=28, height=36,
            font=ctk.CTkFont(size=13),
            fg_color=("gray70", "gray30"), hover_color=("gray60", "gray40"),
            command=lambda: self._audio_folder_var.set("")
        ).pack(side="left")

        # ── TRACKS label — row 3 ──────────────────────────────────────────
        self._tracks_label = ctk.CTkLabel(
            self._scroll, text=T["tracks_section"],
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("gray40", "gray55"),
        )
        self._tracks_label.grid(row=3, column=0, sticky="w", padx=20, pady=(6, 4))

        # ── TRACKS frame — row 4 (expands) ────────────────────────────────
        tracks_outer = ctk.CTkFrame(self._scroll, corner_radius=10)
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
            self._scroll, text=T["settings_section"],
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("gray40", "gray55"),
        )
        self._settings_label.grid(row=5, column=0, sticky="w", padx=20, pady=(6, 4))

        # ── SETTINGS frame — row 6 ────────────────────────────────────────
        settings_frame = ctk.CTkFrame(self._scroll, corner_radius=10)
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

        sample_row = ctk.CTkFrame(steps_frame, fg_color="transparent")
        sample_row.pack(anchor="w", pady=(10, 0))

        self._random_sample_enabled = _tk2.BooleanVar(value=False)
        ctk.CTkCheckBox(
            sample_row, text="Random sample — pick",
            variable=self._random_sample_enabled,
            font=ctk.CTkFont(size=14),
        ).pack(side="left")

        self._sample_count_var = _tk2.StringVar(value="25")
        ctk.CTkEntry(
            sample_row, textvariable=self._sample_count_var,
            width=64, height=32, font=ctk.CTkFont(size=14), justify="center",
        ).pack(side="left", padx=(10, 10))

        ctk.CTkLabel(
            sample_row, text="tracks at random from the CSV",
            font=ctk.CTkFont(size=14),
        ).pack(side="left")

        cut_row = ctk.CTkFrame(steps_frame, fg_color="transparent")
        cut_row.pack(anchor="w", pady=(8, 0))

        self._randomize_cut_enabled = _tk2.BooleanVar(value=False)
        ctk.CTkCheckBox(
            cut_row, text="Randomize cut per track — duration",
            variable=self._randomize_cut_enabled,
            font=ctk.CTkFont(size=14),
        ).pack(side="left")

        self._dur_min_var = _tk2.StringVar(value="0.5")
        ctk.CTkEntry(
            cut_row, textvariable=self._dur_min_var,
            width=56, height=32, font=ctk.CTkFont(size=14), justify="center",
        ).pack(side="left", padx=(10, 4))

        ctk.CTkLabel(cut_row, text="–", font=ctk.CTkFont(size=14)).pack(side="left", padx=4)

        self._dur_max_var = _tk2.StringVar(value="3.0")
        ctk.CTkEntry(
            cut_row, textvariable=self._dur_max_var,
            width=56, height=32, font=ctk.CTkFont(size=14), justify="center",
        ).pack(side="left", padx=(4, 8))

        ctk.CTkLabel(cut_row, text="s", font=ctk.CTkFont(size=14)).pack(side="left")

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

        # ── AI ANALYSIS label — row 7 ─────────────────────────────────────
        ai_label_text = (
            T["ai_section"] + "  ✓ librosa ready"
            if self._librosa_ok else
            T["ai_section"] + "  ✗ librosa not installed — features disabled"
        )
        self._ai_label = ctk.CTkLabel(
            self._scroll, text=ai_label_text,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("gray40", "gray55"),
        )
        self._ai_label.grid(row=7, column=0, sticky="w", padx=20, pady=(6, 4))

        # ── AI ANALYSIS frame — row 8 ─────────────────────────────────────
        self._ai_smart_grain     = _tk2.BooleanVar(value=True)
        self._ai_detect_versions = _tk2.BooleanVar(value=True)
        self._ai_extract_feats   = _tk2.BooleanVar(value=True)
        self._ai_cluster         = _tk2.BooleanVar(value=True)
        self._ai_clap            = _tk2.BooleanVar(value=False)

        ai_frame = ctk.CTkFrame(self._scroll, corner_radius=10)
        ai_frame.grid(row=8, column=0, sticky="ew", padx=14, pady=(0, 6))

        self._ai_smart_grain_chk = ctk.CTkCheckBox(
            ai_frame, text=T["smart_grain_check"],
            variable=self._ai_smart_grain, font=ctk.CTkFont(size=14))
        self._ai_smart_grain_chk.pack(anchor="w", padx=16, pady=(14, 6))

        self._ai_detect_versions_chk = ctk.CTkCheckBox(
            ai_frame, text=T["detect_versions_check"],
            variable=self._ai_detect_versions, font=ctk.CTkFont(size=14))
        self._ai_detect_versions_chk.pack(anchor="w", padx=16, pady=(0, 6))

        self._ai_extract_feats_chk = ctk.CTkCheckBox(
            ai_frame, text=T["extract_features_check"],
            variable=self._ai_extract_feats, font=ctk.CTkFont(size=14))
        self._ai_extract_feats_chk.pack(anchor="w", padx=16, pady=(0, 6))

        self._ai_cluster_chk = ctk.CTkCheckBox(
            ai_frame, text=T["cluster_check"],
            variable=self._ai_cluster, font=ctk.CTkFont(size=14))
        self._ai_cluster_chk.pack(anchor="w", padx=16, pady=(0, 6))

        self._ai_clap_chk = ctk.CTkCheckBox(
            ai_frame, text=T["clap_check"],
            variable=self._ai_clap, font=ctk.CTkFont(size=14))
        self._ai_clap_chk.pack(anchor="w", padx=16, pady=(0, 8))

        self._ai_requires_note = ctk.CTkLabel(
            ai_frame,
            text=T["ai_requires_note"],
            font=ctk.CTkFont(size=12),
            text_color=("gray45", "gray50"),
            justify="left",
            anchor="w",
        )
        self._ai_requires_note.pack(anchor="w", padx=16, pady=(0, 12))

        if not self._librosa_ok:
            for chk in (self._ai_smart_grain_chk, self._ai_detect_versions_chk,
                        self._ai_extract_feats_chk, self._ai_cluster_chk, self._ai_clap_chk):
                chk.configure(state="disabled")

        # ── Action bar — row 9 ────────────────────────────────────────────
        action_bar = ctk.CTkFrame(self._scroll, fg_color="transparent")
        action_bar.grid(row=9, column=0, sticky="ew", padx=14, pady=(4, 6))
        action_bar.grid_columnconfigure(3, weight=1)

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

        self._randomize_btn = ctk.CTkButton(
            action_bar, text="Randomize", width=120, height=42,
            command=self._randomize,
            fg_color=("gray65", "gray35"), hover_color=("gray55", "gray45"),
            font=ctk.CTkFont(size=14))
        self._randomize_btn.grid(row=0, column=2, padx=(10, 0))

        self._progress = ctk.CTkProgressBar(action_bar, mode="indeterminate", height=10)
        self._progress.grid(row=0, column=3, sticky="ew", padx=(18, 0))
        self._progress.set(0)

        # ── LOG label — row 10 ────────────────────────────────────────────
        self._log_label = ctk.CTkLabel(
            self._scroll, text=T["log_section"],
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("gray40", "gray55"),
        )
        self._log_label.grid(row=10, column=0, sticky="w", padx=20, pady=(4, 4))

        # ── LOG frame — row 11 ────────────────────────────────────────────
        log_frame = ctk.CTkFrame(self._scroll, corner_radius=10)
        log_frame.grid(row=11, column=0, sticky="ew", padx=14, pady=(0, 14))

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

            bg   = _color("CTkTextbox", "fg_color")
            fg   = _color("CTkLabel", "text_color")
            sel  = _color("CTkButton", "fg_color")
            head = _color("CTkFrame", "top_fg_color")
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
        self._ai_label.configure(text=T.get("ai_section", "AI ANALYSIS"))
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
        self._ai_smart_grain_chk.configure(text=T.get("smart_grain_check", "Smart grain selection"))
        self._ai_detect_versions_chk.configure(text=T.get("detect_versions_check", "Flag suspected wrong versions"))
        self._ai_extract_feats_chk.configure(text=T.get("extract_features_check", "Extract audio features"))
        self._ai_cluster_chk.configure(text=T.get("cluster_check", "Cluster corpus by similarity"))
        self._ai_clap_chk.configure(text=T.get("clap_check", "CLAP embeddings"))
        self._ai_requires_note.configure(text=T.get("ai_requires_note", "Requires librosa."))
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

    def _browse_audio_folder(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(title="Select folder containing WAV files")
        if path:
            self._audio_folder_var.set(path)

    # ── CSV + search ──────────────────────────────────────────────────────────

    def _load_csv(self, path: str):
        tracks, err = load_tracks_from_csv(path)
        T = self._T()
        if err:
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
        audio_folder = self._audio_folder_var.get().strip()
        previews_dir = audio_folder if audio_folder else os.path.join(output_root, "previews")
        grains_dir   = os.path.join(output_root, "grains")

        try:
            prev_len = int(self._prev_len_var.get())
            offset   = float(self._offset_var.get())
            duration = float(self._duration_var.get())
            dur_min  = float(self._dur_min_var.get())
            dur_max  = float(self._dur_max_var.get())
            if dur_min > dur_max:
                dur_min, dur_max = dur_max, dur_min
        except ValueError:
            self._log_write("One of the number fields has an invalid value. Check that Download length, Offset, Duration, and Duration range are all plain numbers (for example: 30, 5.0, 1.5).")
            self.root.after(0, self._on_done)
            return

        ai_opts = {
            "smart_grain":      self._ai_smart_grain.get(),
            "detect_versions":  self._ai_detect_versions.get(),
            "extract_features": self._ai_extract_feats.get(),
            "cluster":          self._ai_cluster.get(),
            "clap":             self._ai_clap.get(),
            "duration":         duration,
        }

        metadata = {}
        _strategy_wins.update({"energy": 0, "onsets": 0, "spectral": 0})

        tracks_override = None
        if self._random_sample_enabled.get():
            import random as _r
            try:
                n = max(1, int(self._sample_count_var.get()))
                pool = list(self._all_tracks)
                tracks_override = _r.sample(pool, min(n, len(pool)))
            except ValueError:
                self._log_write("Invalid sample count — using all tracks.")

        with _PrintRedirector(self._log_queue):
            try:
                if tracks_override is not None:
                    print(f"Random sample: {len(tracks_override)} of {len(self._all_tracks)} tracks selected.")
                if audio_folder:
                    print(f"Audio folder set — skipping download, slicing from: {audio_folder}")
                if self._do_download.get() and not audio_folder:
                    run_download(csv_path, previews_dir, prev_len, self._stop_event,
                                 ai_opts=ai_opts, metadata=metadata, tracks=tracks_override)
                if self._do_slice.get() and not self._stop_event.is_set():
                    if os.path.isdir(previews_dir):
                        run_slice(previews_dir, grains_dir, offset, duration,
                                  self._stop_event, ai_opts=ai_opts, metadata=metadata,
                                  randomize_cut=self._randomize_cut_enabled.get(),
                                  dur_min=dur_min, dur_max=dur_max, preview_length=prev_len)
                    else:
                        print("No previews folder found — run with Download enabled first.")

                if not self._stop_event.is_set() and ai_opts.get("cluster"):
                    if os.path.isdir(grains_dir):
                        clusters = cluster_corpus(grains_dir)
                        for fname, cluster_id in clusters.items():
                            key = fname[:-4] if fname.lower().endswith(".wav") else fname
                            metadata.setdefault(key, {})["cluster"] = cluster_id

                if not self._stop_event.is_set() and ai_opts.get("clap"):
                    if os.path.isdir(grains_dir):
                        run_clap_analysis(grains_dir, output_root)

                if metadata:
                    save_metadata(output_root, metadata)

                if any(_strategy_wins.values()):
                    preferred = max(_strategy_wins, key=_strategy_wins.get)
                    save_config({"preferred_grain_strategy": preferred})

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

    def _randomize(self):
        import random as _r
        self._prev_len_var.set(str(_r.randint(15, 60)))
        self._offset_var.set(f"{_r.uniform(0.0, 25.0):.1f}")
        self._duration_var.set(f"{_r.uniform(0.5, 5.0):.1f}")
        self._ai_smart_grain.set(_r.choice([True, False]))
        self._ai_detect_versions.set(_r.choice([True, False]))
        self._ai_extract_feats.set(_r.choice([True, False]))
        self._ai_cluster.set(_r.choice([True, False]))

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
            print("customtkinter is not installed. Run setup.bat (Windows) or setup.sh (Mac) to fix this.")
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
