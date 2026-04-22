"""Phase 0 spike: librosa vs madmom on Y:\\ samples.

For each audio file:
  - librosa.beat.beat_track  -> static BPM + beat times
  - madmom RNN+DBN            -> dynamic beat times + downbeats
  - tempo_curve = local 4-beat sliding-window BPM (madmom only)

Outputs JSON per file under tmp/beat_spike/<basename>.json + a comparison
PNG showing (a) audio waveform and (b) both beat-time series + tempo_curve.

Usage:
    python backend/_beat_track_spike.py
    python backend/_beat_track_spike.py "Y:/some-other.flac"  # single file

Numbers to look at:
  - n_beats (more is fine; matters less than placement)
  - bpm_global (librosa) vs bpm_median (madmom)
  - tempo_range (max - min of madmom tempo_curve) -> rubato detector
  - n_downbeats / 4 should ~= bars
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np

OUT_DIR = Path(__file__).resolve().parents[1] / "tmp" / "beat_spike"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Default 10-sample roster covering 4 categories
DEFAULT_SAMPLES = [
    # modern quantized (baseline — must not regress)
    ("modern", "Y:/ABBA - Money, Money, Money (Official Music Video).flac"),
    ("modern", "Y:/Bee Gees - Stayin' Alive (Official Music Video).flac"),
    # live performances (rubato + crowd accel)
    ("live", "Y:/Andrea Bocelli - Besame Mucho (Live From Lake Las Vegas Resort, USA ⧸ 2006).flac"),
    ("live", "Y:/Bee Gees - Massachusetts (One For All Tour Live In Australia 1989).flac"),
    ("live", "Y:/Desperado (Live at The Forum, Los Angeles, CA, 10⧸21⧸1976) (2018 Remaster).flac"),
    # old recordings / natural human feel
    ("old_ballad", "Y:/All by Myself.flac"),
    ("old_ballad", "Y:/Air Supply - Here I Am (Just When I Thought I Was Over You).flac"),
    ("old_ballad", "Y:/Bee Gees - How Deep Is Your Love (Official Video).flac"),
    # solo piano / no drum kit (worst case for beat tracking)
    ("solo_piano", "Y:/Mariage D'amour - Richard Clayderman.flac"),
    ("solo_piano", "Y:/Richard Clayderman - Lettre à Ma Mère (Official Video) (HD Remaster).flac"),
]


def _safe_basename(p: str) -> str:
    name = Path(p).stem
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:80]


def _librosa_track(audio_path: str) -> dict:
    import librosa
    t0 = time.time()
    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    load_s = time.time() - t0
    t0 = time.time()
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    track_s = time.time() - t0
    bpm = float(tempo) if np.isscalar(tempo) else float(np.asarray(tempo).ravel()[0])
    return {
        "engine": "librosa.beat_track",
        "bpm_global": round(bpm, 2),
        "n_beats": len(beat_times),
        "beats": [round(t, 4) for t in beat_times],
        "load_s": round(load_s, 2),
        "compute_s": round(track_s, 2),
        "duration_s": round(len(y) / sr, 2),
    }


def _tempo_curve_from_beats(beats: list, window: int = 4) -> list:
    """Sliding-window local BPM. window=4 -> bpm = 60 * (window-1) / (b[i+w-1] - b[i])."""
    if len(beats) < window:
        return []
    out = []
    for i in range(len(beats) - window + 1):
        span = beats[i + window - 1] - beats[i]
        if span <= 0:
            continue
        bpm = 60.0 * (window - 1) / span
        # report at the window midpoint time
        t_mid = (beats[i] + beats[i + window - 1]) / 2.0
        out.append({"t": round(t_mid, 3), "bpm": round(bpm, 2)})
    return out


def _madmom_track(audio_path: str) -> dict:
    from madmom.audio.signal import Signal
    from madmom.features.beats import RNNBeatProcessor, DBNBeatTrackingProcessor
    from madmom.features.downbeats import (
        DBNDownBeatTrackingProcessor,
        RNNDownBeatProcessor,
    )

    t0 = time.time()
    sig = Signal(audio_path, sample_rate=44100, num_channels=1)
    load_s = time.time() - t0
    duration_s = len(sig) / sig.sample_rate

    t0 = time.time()
    rnn = RNNBeatProcessor()
    acts = rnn(sig)
    rnn_s = time.time() - t0

    t0 = time.time()
    dbn = DBNBeatTrackingProcessor(min_bpm=40, max_bpm=240, fps=100)
    beats = dbn(acts).tolist()
    dbn_s = time.time() - t0

    # Downbeats: separate RNN + DBN with 3/4 + 4/4 hypotheses
    t0 = time.time()
    db_rnn = RNNDownBeatProcessor()
    db_acts = db_rnn(sig)
    db_dbn = DBNDownBeatTrackingProcessor(beats_per_bar=[3, 4], fps=100)
    db_out = db_dbn(db_acts)
    # db_out is (N, 2): [time, beat_position]; downbeat where beat_position == 1
    downbeats = [round(float(t), 4) for t, pos in db_out if int(pos) == 1]
    db_s = time.time() - t0

    tempo_curve = _tempo_curve_from_beats(beats, window=4)
    bpms_only = [pt["bpm"] for pt in tempo_curve]

    return {
        "engine": "madmom RNN+DBN",
        "bpm_median": round(float(np.median(bpms_only)), 2) if bpms_only else None,
        "bpm_p10": round(float(np.percentile(bpms_only, 10)), 2) if bpms_only else None,
        "bpm_p90": round(float(np.percentile(bpms_only, 90)), 2) if bpms_only else None,
        "tempo_range_bpm": round(max(bpms_only) - min(bpms_only), 2) if bpms_only else 0,
        "n_beats": len(beats),
        "n_downbeats": len(downbeats),
        "beats": [round(t, 4) for t in beats],
        "downbeats": downbeats,
        "tempo_curve": tempo_curve,
        "load_s": round(load_s, 2),
        "rnn_s": round(rnn_s, 2),
        "dbn_s": round(dbn_s, 2),
        "downbeat_s": round(db_s, 2),
        "duration_s": round(duration_s, 2),
    }


def _plot_comparison(audio_path: str, lib: dict, mad: dict, out_png: Path) -> None:
    import librosa
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # Load mono at 11025 just for waveform display (cheap)
    y, sr = librosa.load(audio_path, sr=11025, mono=True)
    t_axis = np.arange(len(y)) / sr

    fig, axes = plt.subplots(3, 1, figsize=(16, 8), sharex=True)
    title = f"{Path(audio_path).name}  |  dur {lib['duration_s']:.1f}s"
    fig.suptitle(title, fontsize=10)

    # Pane 1: waveform + librosa beats
    axes[0].plot(t_axis, y, color="#888", linewidth=0.4)
    for b in lib["beats"]:
        axes[0].axvline(b, color="#1f77b4", alpha=0.45, linewidth=0.7)
    axes[0].set_title(
        f"librosa  |  bpm={lib['bpm_global']}  |  {lib['n_beats']} beats  |  {lib['compute_s']:.1f}s",
        fontsize=9,
    )
    axes[0].set_ylabel("amp", fontsize=8)

    # Pane 2: waveform + madmom beats (red) + downbeats (thick)
    axes[1].plot(t_axis, y, color="#888", linewidth=0.4)
    for b in mad["beats"]:
        axes[1].axvline(b, color="#d62728", alpha=0.4, linewidth=0.6)
    for d in mad["downbeats"]:
        axes[1].axvline(d, color="#d62728", alpha=0.95, linewidth=1.4)
    axes[1].set_title(
        f"madmom  |  bpm median={mad['bpm_median']}  range={mad['tempo_range_bpm']}  |  "
        f"{mad['n_beats']} beats  {mad['n_downbeats']} downbeats  |  RNN {mad['rnn_s']:.1f}s+DBN {mad['dbn_s']:.1f}s",
        fontsize=9,
    )
    axes[1].set_ylabel("amp", fontsize=8)

    # Pane 3: madmom tempo_curve (rubato visualisation)
    if mad["tempo_curve"]:
        ts = [pt["t"] for pt in mad["tempo_curve"]]
        bpms = [pt["bpm"] for pt in mad["tempo_curve"]]
        axes[2].plot(ts, bpms, color="#d62728", linewidth=1.2, label="madmom local bpm")
        axes[2].axhline(
            lib["bpm_global"], color="#1f77b4", linestyle="--",
            linewidth=1.0, label=f"librosa global ({lib['bpm_global']})",
        )
        axes[2].set_ylabel("BPM", fontsize=8)
        axes[2].legend(loc="upper right", fontsize=8)
        axes[2].grid(True, alpha=0.3)
    axes[2].set_xlabel("time (s)", fontsize=8)

    plt.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(out_png, dpi=110)
    plt.close(fig)


def _run_one(audio_path: str, category: str = "?") -> dict:
    name = _safe_basename(audio_path)
    print(f"\n=== [{category}] {Path(audio_path).name} ===", flush=True)

    if not os.path.exists(audio_path):
        print(f"  SKIP: file not found")
        return {"name": name, "category": category, "error": "file_not_found"}

    try:
        lib = _librosa_track(audio_path)
        print(
            f"  librosa: bpm={lib['bpm_global']:>5}  "
            f"{lib['n_beats']:>4} beats  ({lib['compute_s']:.1f}s)",
            flush=True,
        )
    except Exception as e:
        print(f"  librosa FAILED: {e}", flush=True)
        return {"name": name, "category": category, "error": f"librosa: {e}"}

    try:
        mad = _madmom_track(audio_path)
        print(
            f"  madmom:  bpm med={mad['bpm_median']:>5}  range={mad['tempo_range_bpm']:>5}  "
            f"{mad['n_beats']:>4} beats  {mad['n_downbeats']:>3} downbeats  "
            f"(RNN {mad['rnn_s']:.1f}+DBN {mad['dbn_s']:.1f}+DB {mad['downbeat_s']:.1f}s)",
            flush=True,
        )
    except Exception as e:
        print(f"  madmom FAILED: {e}", flush=True)
        return {
            "name": name,
            "category": category,
            "audio_path": audio_path,
            "librosa": lib,
            "error": f"madmom: {e}",
        }

    out_json = OUT_DIR / f"{name}.json"
    out_png = OUT_DIR / f"{name}.png"
    out_json.write_text(
        json.dumps(
            {
                "name": name,
                "category": category,
                "audio_path": audio_path,
                "librosa": lib,
                "madmom": mad,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    try:
        _plot_comparison(audio_path, lib, mad, out_png)
        print(f"  -> {out_json.name}  +  {out_png.name}", flush=True)
    except Exception as e:
        print(f"  PLOT FAILED: {e}", flush=True)

    return {"name": name, "category": category, "ok": True, "json": str(out_json)}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1:
        targets = [("custom", a) for a in sys.argv[1:]]
    else:
        targets = DEFAULT_SAMPLES

    print(f"Output dir: {OUT_DIR}")
    print(f"Samples: {len(targets)}")
    summary = []
    for category, path in targets:
        summary.append(_run_one(path, category))

    print("\n=== SUMMARY ===")
    for s in summary:
        if s.get("ok"):
            print(f"  OK   [{s['category']:>10}]  {s['name']}")
        else:
            print(f"  FAIL [{s['category']:>10}]  {s['name']}  ({s.get('error')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
