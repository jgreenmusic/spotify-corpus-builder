"""Tests for the parts of the pipeline that don't need the network or a display.

Run with:  python -m pytest tests
"""
import json
import os
import shutil
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import spotify_corpus_builder as scb  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None
try:
    import librosa  # noqa: F401
    HAS_LIBROSA = True
except ImportError:
    HAS_LIBROSA = False


def write_wav(path, seconds, sr=44100, channels=2, freq=440.0):
    t = np.arange(int(seconds * sr)) / sr
    mono = 0.5 * np.sin(2 * np.pi * freq * t)
    data = np.repeat(mono[:, None], channels, axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((data * 32767).astype("<i2").tobytes())
    return str(path)


def read_wav(path):
    with wave.open(str(path)) as w:
        frames = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        return frames.reshape(-1, w.getnchannels()) / 32768.0, w.getframerate()


# ── Parsing ──────────────────────────────────────────────────────────────────

def test_sanitize_removes_path_characters():
    assert scb.sanitize('AC/DC: "Back" <In> Black?') == "AC_DC_ _Back_ _In_ Black_"


def test_track_filename_with_and_without_artist():
    assert scb.track_filename({"artist": "Burial", "name": "Archangel"}) == "Burial - Archangel"
    assert scb.track_filename({"artist": "", "name": "Archangel"}) == "Archangel"


def test_read_exportify_csv(tmp_path):
    p = tmp_path / "list.csv"
    p.write_text(
        "Track URI,Track Name,Artist Name(s),Duration (ms),Key,Mode,Tempo,Genres\n"
        'spotify:track:1,Xtal,Aphex Twin,290000,9,1,121.5,"idm,ambient"\n'
        ",,Nobody,1000,,,,\n",
        encoding="utf-8-sig")
    tracks = scb.read_tracks(str(p))
    assert len(tracks) == 1
    t = tracks[0]
    assert (t["artist"], t["name"], t["uri"], t["duration_ms"]) == ("Aphex Twin", "Xtal", "spotify:track:1", 290000)
    assert t["spotify"]["key_name"] == "A major"
    assert t["spotify"]["genres"] == ["idm", "ambient"]


def test_parse_track_lines():
    tracks, urls = scb.parse_track_lines(
        "# comment\nAphex Twin - Xtal\nBoards of Canada – Roygbiv\nBurial\tArchangel\n"
        "Just A Title\n\nhttps://youtu.be/abc\n")
    assert tracks == [
        {"artist": "Aphex Twin", "name": "Xtal"},
        {"artist": "Boards of Canada", "name": "Roygbiv"},
        {"artist": "Burial", "name": "Archangel"},
        {"artist": "", "name": "Just A Title"},
    ]
    assert urls == ["https://youtu.be/abc"]


def test_track_list_csv_round_trip(tmp_path):
    tracks = [{"artist": "A", "name": "B", "duration_ms": 1000, "youtube_url": "https://youtu.be/x"}]
    p = str(tmp_path / "saved.csv")
    scb.write_tracks_csv(p, tracks)
    assert scb.read_tracks(p) == tracks


@pytest.mark.parametrize("title,channel,expected", [
    ("Xtal", "Aphex Twin - Topic", ("Aphex Twin", "Xtal")),
    ("Burial - Archangel (Official Video)", "Hyperdub", ("Burial", "Archangel")),
    ("Windowlicker [HD]", "Aphex Twin", ("Aphex Twin", "Windowlicker")),
])
def test_split_youtube_title(title, channel, expected):
    assert scb._split_youtube_title(title, channel) == expected


# ── YouTube result matching ──────────────────────────────────────────────────

TRACK = {"artist": "Jungle", "name": "Busy Earnin'", "duration_ms": 181772}


def test_studio_upload_beats_live_and_cover():
    candidates = [
        {"title": "Jungle - Busy Earnin' (Live on KEXP)", "duration": 260, "channel": "KEXP"},
        {"title": "Busy Earnin'", "duration": 182, "channel": "Jungle - Topic"},
        {"title": "Busy Earnin' cover", "duration": 181, "channel": "someone"},
    ]
    scores = [scb.score_candidate(c, TRACK)[0] for c in candidates]
    assert scores.index(max(scores)) == 1


def test_flags_explain_the_problem():
    _, flags = scb.score_candidate({"title": "Busy Earnin' (Live)", "duration": 260}, TRACK)
    assert "live" in flags and any(f.startswith("length off") for f in flags)


def test_version_word_in_real_title_is_not_flagged():
    track = {"artist": "X", "name": "Song (Live)"}
    assert scb.score_candidate({"title": "X - Song (Live)"}, track)[1] == []


def test_download_start_from_middle():
    assert scb._download_start(TRACK, {"duration": 180}, 30, from_middle=False) == 0.0
    assert scb._download_start(TRACK, {"duration": 180}, 30, from_middle=True) == pytest.approx(59.4)
    assert scb._download_start(TRACK, {"duration": 20}, 30, from_middle=True) == 0.0


# ── Slicing ──────────────────────────────────────────────────────────────────

def test_slice_is_exact_length_with_silent_edges(tmp_path):
    src = write_wav(tmp_path / "in.wav", 10)
    dst = tmp_path / "out.wav"
    assert scb.slice_preview(src, str(dst), 2.0, 1.5, fade_ms=5)
    data, sr = read_wav(dst)
    assert sr == 44100 and data.shape == (66150, 2)
    assert abs(data[0]).max() < 0.01 and abs(data[-1]).max() < 0.01
    assert abs(data).max() > 0.4   # the middle keeps full volume


def test_cut_past_end_is_moved_back(tmp_path):
    previews, grains = tmp_path / "p", tmp_path / "g"
    previews.mkdir()
    write_wav(previews / "a.wav", 4)
    meta = {}
    scb.run_slice(str(previews), str(grains), ["a.wav"], offset=30, duration=1.5, metadata=meta)
    assert meta["a"]["grain"] == {"offset": 2.5, "duration": 1.5}
    assert read_wav(grains / "a.wav")[0].shape[0] == 66150


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_other_sample_rates_become_44k_stereo(tmp_path):
    src = write_wav(tmp_path / "in.wav", 3, sr=48000, channels=1)
    dst = tmp_path / "out.wav"
    assert scb.slice_preview(src, str(dst), 0.5, 1.0)
    data, sr = read_wav(dst)
    assert sr == 44100 and data.shape[1] == 2


# ── Pipeline ─────────────────────────────────────────────────────────────────

def test_pipeline_with_audio_folder_keeps_metadata(tmp_path):
    audio, out = tmp_path / "audio", tmp_path / "out"
    audio.mkdir()
    for i in range(3):
        write_wav(audio / f"t{i}.wav", 6, freq=220 * (i + 1))
    (audio / "notes.txt").write_text("not audio")
    scb.run_pipeline(str(out), audio_folder=str(audio), offset=1, duration=0.5)
    assert sorted(os.listdir(out / "grains")) == ["t0.wav", "t1.wav", "t2.wav"]

    meta_path = out / "metadata.json"
    meta = json.loads(meta_path.read_text())
    meta["t0"]["note"] = "kept"
    meta_path.write_text(json.dumps(meta))
    scb.run_pipeline(str(out), audio_folder=str(audio), offset=1, duration=0.5)
    assert json.loads(meta_path.read_text())["t0"]["note"] == "kept"


def test_pipeline_only_processes_given_tracks(tmp_path):
    out = tmp_path / "out"
    (out / "previews").mkdir(parents=True)
    write_wav(out / "previews" / "A - One.wav", 6)
    write_wav(out / "previews" / "B - Other.wav", 6)
    scb.run_pipeline(str(out), tracks=[{"artist": "A", "name": "One", "uri": "spotify:track:1"}],
                     do_download=False, offset=1, duration=0.5)
    assert os.listdir(out / "grains") == ["A - One.wav"]
    assert json.loads((out / "metadata.json").read_text())["A - One"]["uri"] == "spotify:track:1"


# ── Analysis (needs librosa) ─────────────────────────────────────────────────

@pytest.mark.skipif(not HAS_LIBROSA, reason="librosa not installed")
def test_best_grain_finds_the_loud_part(tmp_path):
    sr = 44100
    quiet = 0.01 * np.random.default_rng(0).standard_normal(sr * 10)
    loud = 0.5 * np.random.default_rng(1).standard_normal(sr * 2)
    signal = np.concatenate([quiet[:sr * 6], loud, quiet[sr * 6:]])
    path = tmp_path / "x.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(signal, -1, 1) * 32767).astype("<i2").tobytes())
    result = scb.analyze_file(str(path), grain_duration=1.5, strategy="energy")
    assert 5.5 <= result["suggested_offset"] <= 7.0


@pytest.mark.skipif(not HAS_LIBROSA, reason="librosa not installed")
def test_estimate_key_from_chroma():
    a_minor = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
    assert scb.estimate_key(np.roll(a_minor, 9)) == "A minor"
