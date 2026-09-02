"""MIDI ingest — turn an uploaded .mid into a LiveChord song.

Runs as a *subprocess* (spawned by midi_api's worker thread) so the
pure-Python note loops never hold the server's GIL:

    python midi_ingest.py --midi <path> --job-id <id> --title <t> --out <json>

Outputs (all keyed by ``hash = song_hash("__midi/<job_id>")``):
  * data/chords/<h[:2]>/<h>.json   chords + beats/downbeats/bpm straight from
                                    the MIDI tempo map (exact, no tracker)
  * data/melodies/<h>.json          RH skyline melody, schema v2
  * data/accompaniments/<h>_midi_source.json
                                    the MIDI's own LH/RH notes with piano
                                    fingering (served by /api/ai/accompaniment
                                    instead of a generated style)
  * data/midi_audio/<h>.flac|wav    FluidSynth render (optional — skipped with a
                                    warning when fluidsynth / soundfont missing)

The result summary is written to ``--out`` as JSON so the parent can read
``{hash, chord_count, audio_url, warnings}`` without parsing stdout.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

BACKEND_DIR = Path(__file__).resolve().parent
REPO_DIR = BACKEND_DIR.parent
DATA_DIR = REPO_DIR / "data"
MIDI_AUDIO_DIR = DATA_DIR / "midi_audio"
ACC_DIR = DATA_DIR / "accompaniments"
MELODIES_DIR = DATA_DIR / "melodies"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

# Display spelling matches chord_detect._ROOT_NORMALIZE (flats, Gb for F#).
NOTE_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

# (intervals) -> suffix. Every suffix here exists in chord_table.CHORD_INTERVALS
# so the player's chord-info lookup resolves.
CHORD_TEMPLATES: List[Tuple[Tuple[int, ...], str]] = [
    ((0, 4, 7), ""),
    ((0, 3, 7), "m"),
    ((0, 4, 7, 10), "7"),
    ((0, 3, 7, 10), "m7"),
    ((0, 4, 7, 11), "maj7"),
    ((0, 3, 6), "dim"),
    ((0, 3, 6, 9), "dim7"),
    ((0, 3, 6, 10), "m7b5"),
    ((0, 4, 8), "aug"),
    ((0, 5, 7), "sus4"),
    ((0, 2, 7), "sus2"),
    ((0, 4, 7, 9), "6"),
    ((0, 3, 7, 9), "m6"),
]

# Krumhansl-Schmuckler key profiles.
_KS_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_KS_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]

SOURCE_TAG = "midi_upload"
LH_RH_SPLIT_PITCH = 60  # single-track fallback: below middle C → left hand


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_notes(midi) -> List[Tuple[float, float, int, int]]:
    """All non-drum notes as (start, end, pitch, velocity)."""
    out = []
    for inst in midi.instruments:
        if inst.is_drum:
            continue
        for n in inst.notes:
            if n.end > n.start:
                out.append((float(n.start), float(n.end), int(n.pitch), int(n.velocity)))
    out.sort()
    return out


def _grid(midi, duration: float) -> Tuple[List[float], List[float], float, str, List[dict]]:
    """beats, downbeats, bpm, time_signature, tempo_curve from the MIDI tempo map."""
    beats = [round(float(b), 3) for b in midi.get_beats()]
    downbeats = [round(float(d), 3) for d in midi.get_downbeats()]
    beats = [b for b in beats if b <= duration + 1e-3]
    downbeats = [d for d in downbeats if d <= duration + 1e-3]

    ts = "4/4"
    if midi.time_signature_changes:
        first = midi.time_signature_changes[0]
        ts = f"{first.numerator}/{first.denominator}"

    if len(downbeats) < 2 and len(beats) >= 2:
        try:
            per_bar = int(ts.split("/")[0]) or 4
        except ValueError:
            per_bar = 4
        downbeats = beats[::per_bar]

    tempo_curve: List[dict] = []
    times, tempi = midi.get_tempo_changes()
    for t, bpm in zip(times, tempi):
        if bpm > 0:
            tempo_curve.append({"t": round(float(t), 3), "bpm": round(float(bpm), 2)})

    # Duration-weighted median tempo — robust to a stray initial tempo event.
    bpm = 0.0
    if tempo_curve:
        spans = []
        for i, pt in enumerate(tempo_curve):
            nxt = tempo_curve[i + 1]["t"] if i + 1 < len(tempo_curve) else duration
            spans.append((max(0.0, nxt - pt["t"]), pt["bpm"]))
        spans.sort(key=lambda x: x[1])
        total = sum(s for s, _ in spans) or 1.0
        acc = 0.0
        for span, b in spans:
            acc += span
            if acc >= total / 2:
                bpm = b
                break
    if bpm <= 0 and len(beats) >= 2:
        bpm = 60.0 * (len(beats) - 1) / max(1e-6, beats[-1] - beats[0])
    if bpm <= 0:
        bpm = 120.0
    return beats, downbeats, round(bpm, 1), ts, tempo_curve


def _bars(downbeats: List[float], beats: List[float], duration: float) -> List[Tuple[float, float]]:
    anchors = list(downbeats) if len(downbeats) >= 2 else list(beats[::4])
    if not anchors:
        return [(0.0, duration)]
    if anchors[0] > 0.05:
        anchors.insert(0, 0.0)
    bars = []
    for i, s in enumerate(anchors):
        e = anchors[i + 1] if i + 1 < len(anchors) else duration
        if e - s > 0.05:
            bars.append((s, min(e, duration)))
    # Extend last bar so trailing notes are covered.
    if bars and bars[-1][1] < duration:
        bars[-1] = (bars[-1][0], duration)
    return bars


def _pc_profile(notes, s: float, e: float) -> Tuple[List[float], Optional[int], float]:
    """Duration×velocity-weighted pitch-class weights in [s, e), bass pc, total."""
    pcs = [0.0] * 12
    total = 0.0
    lowest: Optional[Tuple[int, float]] = None  # (pitch, weight)
    seg = e - s
    for start, end, pitch, vel in notes:
        if end <= s:
            continue
        if start >= e:
            break
        ov = min(end, e) - max(start, s)
        if ov <= 0:
            continue
        w = ov * (0.5 + 0.5 * vel / 127.0)
        pcs[pitch % 12] += w
        total += w
        # Bass candidate: must sound for a meaningful part of the segment.
        if ov >= 0.25 * seg or ov >= 0.25 * (end - start):
            if lowest is None or pitch < lowest[0]:
                lowest = (pitch, w)
    bass = lowest[0] % 12 if lowest else None
    return pcs, bass, total


def _best_chord(pcs: List[float], bass: Optional[int], total: float) -> Tuple[str, float]:
    """Template match. Returns (name, confidence 0..1)."""
    if total <= 1e-6:
        return "N", 0.0
    best_name, best_score = "N", -1e9
    for root in range(12):
        if pcs[root] < 0.04 * total and root != bass:
            continue
        for intervals, suffix in CHORD_TEMPLATES:
            tones = [(root + i) % 12 for i in intervals]
            inside = sum(pcs[t] for t in tones)
            outside = total - inside
            missing = sum(1 for t in tones if pcs[t] < 0.05 * total)
            score = inside - 0.7 * outside - 0.18 * total * missing
            if bass is not None and bass == root:
                score += 0.12 * total
            # Prefer triads over 7ths unless the 7th really sounds.
            if len(intervals) == 4:
                score -= 0.04 * total
            if score > best_score:
                best_score = score
                name = NOTE_NAMES[root] + suffix
                if bass is not None and bass != root and bass in tones:
                    name += "/" + NOTE_NAMES[bass]
                best_name = name
    conf = max(0.0, min(1.0, best_score / total)) if total > 0 else 0.0
    return best_name, conf


def _base_name(chord: str) -> str:
    return chord.split("/")[0]


def detect_chords(notes, bars: List[Tuple[float, float]]) -> List[dict]:
    """One chord per bar, split in halves when the two halves clearly differ."""
    entries: List[dict] = []
    for s, e in bars:
        pcs, bass, total = _pc_profile(notes, s, e)
        whole, conf_whole = _best_chord(pcs, bass, total)
        mid = (s + e) / 2.0
        pcs1, bass1, t1 = _pc_profile(notes, s, mid)
        pcs2, bass2, t2 = _pc_profile(notes, mid, e)
        c1, k1 = _best_chord(pcs1, bass1, t1)
        c2, k2 = _best_chord(pcs2, bass2, t2)
        split = (
            _base_name(c1) != _base_name(c2)
            and c1 != "N" and c2 != "N"
            and k1 >= 0.35 and k2 >= 0.35
            and t1 >= 0.15 * total and t2 >= 0.15 * total
        )
        if split:
            entries.append({"time": s, "end": mid, "chord": c1})
            entries.append({"time": mid, "end": e, "chord": c2})
        else:
            entries.append({"time": s, "end": e, "chord": whole})

    merged: List[dict] = []
    for ent in entries:
        if merged and merged[-1]["chord"] == ent["chord"] and abs(merged[-1]["end"] - ent["time"]) < 1e-3:
            merged[-1]["end"] = ent["end"]
        else:
            merged.append(dict(ent))
    # Drop leading/trailing silence markers; keep inner N so gaps stay honest.
    while merged and merged[0]["chord"] == "N":
        merged.pop(0)
    while merged and merged[-1]["chord"] == "N":
        merged.pop()
    for m in merged:
        m["time"] = round(m["time"], 3)
        m["end"] = round(m["end"], 3)
    return merged


def detect_key(midi, notes) -> str:
    """'C' / 'Am' style key. MIDI key signature wins; else Krumhansl-Schmuckler."""
    if midi.key_signature_changes:
        k = midi.key_signature_changes[0].key_number
        if 0 <= k < 24:
            return NOTE_NAMES[k % 12] + ("m" if k >= 12 else "")
    hist = [0.0] * 12
    for s, e, p, v in notes:
        hist[p % 12] += (e - s) * (0.5 + 0.5 * v / 127.0)
    if sum(hist) <= 0:
        return "C"

    def corr(a, b):
        ma, mb = sum(a) / 12, sum(b) / 12
        num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) or 1e-9
        return num / den

    best, best_r = "C", -2.0
    for root in range(12):
        rot = hist[root:] + hist[:root]
        r_maj = corr(rot, _KS_MAJOR)
        r_min = corr(rot, _KS_MINOR)
        if r_maj > best_r:
            best, best_r = NOTE_NAMES[root], r_maj
        if r_min > best_r:
            best, best_r = NOTE_NAMES[root] + "m", r_min
    return best


def split_hands(midi) -> Tuple[List[tuple], List[tuple]]:
    """(lh_notes, rh_notes) as (start, end, pitch, velocity).

    Piano-family tracks are preferred. Two or more tracks → lowest mean pitch
    is LH, highest is RH (extra tracks join by mean pitch). One track → split at
    middle C.
    """
    pool = [i for i in midi.instruments if not i.is_drum and len(i.notes) >= 4]
    piano = [i for i in pool if 0 <= i.program <= 7]
    if piano:
        pool = piano
    if not pool:
        return [], []

    def conv(inst):
        return [(float(n.start), float(n.end), int(n.pitch), int(n.velocity))
                for n in inst.notes if n.end > n.start]

    if len(pool) == 1:
        notes = conv(pool[0])
        lh = [n for n in notes if n[2] < LH_RH_SPLIT_PITCH]
        rh = [n for n in notes if n[2] >= LH_RH_SPLIT_PITCH]
        if not lh or not rh:
            # Narrow-range track: split around its own median instead.
            pitches = sorted(n[2] for n in notes)
            med = pitches[len(pitches) // 2]
            lh = [n for n in notes if n[2] < med]
            rh = [n for n in notes if n[2] >= med]
        return sorted(lh), sorted(rh)

    ranked = sorted(pool, key=lambda i: sum(n.pitch for n in i.notes) / len(i.notes))
    lh, rh = conv(ranked[0]), conv(ranked[-1])
    for inst in ranked[1:-1]:
        mean = sum(n.pitch for n in inst.notes) / len(inst.notes)
        (rh if mean >= LH_RH_SPLIT_PITCH else lh).extend(conv(inst))
    return sorted(lh), sorted(rh)


def skyline_melody(rh_notes) -> List[dict]:
    """Highest note per onset group, truncated at the next melody onset."""
    if not rh_notes:
        return []
    groups: List[List[tuple]] = []
    for n in rh_notes:
        if groups and n[0] - groups[-1][0][0] < 0.03:
            groups[-1].append(n)
        else:
            groups.append([n])
    tops = [max(g, key=lambda x: x[2]) for g in groups]
    events = []
    for i, (s, e, p, v) in enumerate(tops):
        end = e
        if i + 1 < len(tops):
            end = min(end, tops[i + 1][0])
        if end - s <= 0.02:
            continue
        events.append({
            "time": round(s, 4), "duration": round(end - s, 4),
            "start": round(s, 4), "end": round(end, 4),
            "pitch": p, "midi": p,
            "velocity": round(max(0.2, min(1.0, v / 127.0)), 3),
        })
    return events


def hand_events(notes, hand: str) -> List[dict]:
    lane = "lh_chord" if hand == "left" else "rh_accompaniment"
    out = []
    for s, e, p, v in notes:
        out.append({
            "schema_version": 2,
            "time": round(s, 4),
            "duration": round(e - s, 4),
            "pitch": int(p),
            "velocity": round(max(0.2, min(1.0, v / 127.0)), 3),
            "hand": hand,
            "voice_lane": lane,
            "gate_ratio": 1.0,
        })
    return out


def _quantize_group_times(events: List[dict], tol: float = 0.03) -> None:
    """Snap near-simultaneous onsets to one value so fingering sees chord blocks."""
    events.sort(key=lambda e: e["time"])
    anchor = None
    for e in events:
        if anchor is None or e["time"] - anchor > tol:
            anchor = e["time"]
        else:
            e["time"] = anchor


# ---------------------------------------------------------------------------
# FluidSynth render
# ---------------------------------------------------------------------------

def find_fluidsynth() -> Optional[str]:
    env = os.environ.get("LIVECHORD_FLUIDSYNTH")
    if env and os.path.isfile(env):
        return env
    exe = shutil.which("fluidsynth")
    if exe:
        return exe
    for cand in (REPO_DIR / "tools" / "fluidsynth" / "bin" / "fluidsynth.exe",
                 REPO_DIR / "tools" / "fluidsynth" / "fluidsynth.exe",
                 Path(r"C:\tools\fluidsynth\bin\fluidsynth.exe")):
        if cand.is_file():
            return str(cand)
    return None


def find_soundfont() -> Optional[str]:
    env = os.environ.get("LIVECHORD_SOUNDFONT")
    if env and os.path.isfile(env):
        return env
    sf_dir = REPO_DIR / "soundfonts"
    for name in ("FluidR3_GM_GS.sf2", "FluidR3_GM.sf2"):
        p = sf_dir / name
        if p.is_file():
            return str(p)
    if sf_dir.is_dir():
        for p in sorted(sf_dir.glob("*.sf2")) + sorted(sf_dir.glob("*.sf3")):
            return str(p)
    return None


def render_audio(midi_path: str, out_hash: str, warnings: List[str]) -> Optional[str]:
    """Render to data/midi_audio/<hash>.flac (wav fallback). Returns filename or None."""
    exe = find_fluidsynth()
    sf2 = find_soundfont()
    if not exe:
        warnings.append("fluidsynth not found — no synthesized audio")
        return None
    if not sf2:
        warnings.append("soundfont not found — no synthesized audio")
        return None
    MIDI_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    for fmt, ext in (("flac", ".flac"), ("wav", ".wav")):
        final = MIDI_AUDIO_DIR / f"{out_hash}{ext}"
        tmp = MIDI_AUDIO_DIR / f"{out_hash}{ext}.tmp{ext}"
        cmd = [exe, "-ni", "-r", "44100", "-g", "0.7", "-T", fmt, "-F", str(tmp), sf2, midi_path]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=300,
                                  encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            warnings.append("fluidsynth timed out")
            tmp.unlink(missing_ok=True)
            return None
        if proc.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 1024:
            # Remove a stale sibling in the other format so audio_url is unambiguous.
            for other in MIDI_AUDIO_DIR.glob(f"{out_hash}.*"):
                if other != tmp and not other.name.endswith(".tmp" + ext):
                    other.unlink(missing_ok=True)
            os.replace(tmp, final)
            return final.name
        tmp.unlink(missing_ok=True)
        warnings.append(f"fluidsynth {fmt} render failed: {(proc.stderr or '').strip()[-200:]}")
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def ingest(midi_path: str, job_id: str, title: str, progress_cb=None) -> dict:
    import pretty_midi
    from chord_cache import song_hash, chord_file_for, ensure_chord_bucket

    warnings: List[str] = []
    virtual_path = f"__midi/{job_id}"
    h = song_hash(virtual_path)

    midi = pretty_midi.PrettyMIDI(midi_path)
    notes = _load_notes(midi)
    if not notes:
        raise ValueError("MIDI contains no notes")
    duration = round(float(midi.get_end_time()), 3)

    beats, downbeats, bpm, ts, tempo_curve = _grid(midi, duration)
    bars = _bars(downbeats, beats, duration)
    chords = detect_chords(notes, bars)
    if not chords:
        raise ValueError("no chords could be derived from this MIDI")
    key = detect_key(midi, notes)

    # --- hands / melody / fingering -------------------------------------
    lh_raw, rh_raw = split_hands(midi)
    lh = hand_events(lh_raw, "left")
    rh = hand_events(rh_raw, "right")
    _quantize_group_times(lh)
    _quantize_group_times(rh)
    try:
        from ai.accompaniment_generator import _assign_fingering
        _assign_fingering(lh, "left")
        _assign_fingering(rh, "right")
    except Exception as e:  # fingering is best-effort
        warnings.append(f"fingering skipped: {e}")

    melody_events = skyline_melody(rh_raw)

    # --- audio -----------------------------------------------------------
    if progress_cb:
        progress_cb("render", 40)
    audio_name = render_audio(midi_path, h, warnings)
    audio_url = f"/api/midi/audio/{h}" if audio_name else ""

    if progress_cb:
        progress_cb("save", 85)

    # --- chord JSON ------------------------------------------------------
    sheet = {
        "path": virtual_path,
        "key": key,
        "capo": 0,
        "source": SOURCE_TAG,
        "title": title,
        "chords": chords,
        "duration": duration,
        "bpm": bpm,
        "beats": beats,
        "downbeats": downbeats,
        "tempo_curve": tempo_curve,
        "beats_source": "midi",
        "beat_version": 1,
        "time_signature": ts,
    }
    if audio_url:
        sheet["audio_url"] = audio_url
    ensure_chord_bucket(h)
    _atomic_write(chord_file_for(h), sheet)

    # --- melody JSON -----------------------------------------------------
    MELODIES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        from ai.melody_schema import finalize_melody_payload
        mel_payload = finalize_melody_payload(
            {"path": virtual_path, "melody": melody_events,
             "melody_source": {"engine": "midi_skyline", "kind": "midi"}},
            path=virtual_path, bpm=bpm, tempo_curve=tempo_curve, time_signature=ts,
        )
    except Exception as e:
        warnings.append(f"melody finalize fallback: {e}")
        mel_payload = {"path": virtual_path, "schema_version": 2, "melody": melody_events}
    _atomic_write(MELODIES_DIR / f"{h}.json", mel_payload)

    # --- accompaniment (the MIDI's own notes) ----------------------------
    ACC_DIR.mkdir(parents=True, exist_ok=True)
    acc = {
        "schema_version": 2,
        "path": virtual_path,
        "source": SOURCE_TAG,
        "style": "MIDI",
        "level": "L1",
        "section_type": "default",
        "instrument": "piano",
        "bpm": bpm,
        "source_beat_version": 1,
        "suggested_styles": [],
        "left_hand": lh,
        "right_hand": rh,
    }
    _atomic_write(ACC_DIR / f"{h}_midi_source.json", acc)

    return {
        "hash": h,
        "path": virtual_path,
        "chord_count": len(chords),
        "key": key,
        "bpm": bpm,
        "duration": duration,
        "audio_url": audio_url,
        "lh_notes": len(lh),
        "rh_notes": len(rh),
        "melody_notes": len(melody_events),
        "warnings": warnings,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="LiveChord MIDI ingest (subprocess)")
    ap.add_argument("--midi", required=True)
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--out", required=True, help="result JSON path")
    ap.add_argument("--progress", default="", help="optional progress JSON path")
    args = ap.parse_args(argv)

    def progress_cb(stage: str, pct: int):
        if not args.progress:
            return
        try:
            Path(args.progress).write_text(json.dumps({"stage": stage, "progress": pct}), encoding="utf-8")
        except OSError:
            pass

    try:
        result = ingest(args.midi, args.job_id, args.title or Path(args.midi).stem, progress_cb)
        result["ok"] = True
    except Exception as e:  # surfaced to the parent as a job error
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
