"""
MIDI reverse-identifier: match unknown MIDIs against the audio chroma index
at V:/Database/Chroma_Index/ and reconstruct artist/title.

Pipeline:
  0. Artist gate: parse filename artist hint (e.g. '(aerosmith)…' or
     'ABBA - …'); if it clearly doesn't match any library artist,
     short-circuit to status=UNMATCHED_KNOWN_ARTIST before synthesis.
  1. Synthesize MIDI via pretty_midi.synthesize(fs=22050) (first 90s)
  2. Extract chroma with chroma_feat.extract_chroma — identical to the
     index builder so features live in the same space.
  3. Coarse filter: cosine sim of 24-dim chroma_sig across all 22k, trying
     all 12 transpositions — keep top N candidates with their best-fit
     pitch offset.
  4. Fine filter: subsequence DTW on chroma at the best transposition →
     normalized_cost + warp_mean_s + warp R² (linearity).
  5. Gates for a 'matched' verdict (all must pass):
       - normalized_cost < --cost-threshold (default 0.08)
       - warp_r2 >= --warp-r2-threshold (default 0.95)
       - Krumhansl major/minor mode of rotated query == mode of ref
       - If tempo_cache.json present: BPM ratio within --tempo-tolerance
         (accepting 1×, ½×, 2× to absorb transcription halving/doubling)
  6. On match, parse Artist/Title from the matched audio path's manifest
     entry and copy the MIDI to --dest under <Artist - Title>.mid.

Resume-safe via midi_catalog.jsonl.

Usage:
  python identify_midi.py --source U:/MIDI-Library/Uncategorized \
      --index V:/Database/Chroma_Index \
      --dest U:/MIDI-Library-Identified/Uncategorized \
      --catalog U:/MIDI-Library/midi_catalog.jsonl --resume
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import pickle
import re
import shutil
import sys
import time
from concurrent.futures import (
    FIRST_COMPLETED,
    ProcessPoolExecutor,
    wait,
)

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chroma_feat import DECIMATE, HOP, SR, TIME_TARGET_HZ, extract_chroma, file_hash, sanitize_chroma

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

try:
    from concurrent.futures.process import BrokenProcessPool
except ImportError:
    BrokenProcessPool = Exception

MIDI_EXTS = (".mid", ".midi")
MAX_MIDI_END_SEC = 600          # skip MIDIs whose declared length > 10 min
DEFAULT_QUERY_SECONDS = 90      # synth window

# ---------- worker-globals (populated by _init_worker) ----------
_SIGS_N: np.ndarray | None = None          # (N, 24) L2-normalized
_HASHES: list[str] | None = None            # list of str, row order of _SIGS_N
_HASH_TO_ROW: dict[str, int] | None = None
_CHROMA_ALL: dict[str, np.ndarray] | None = None   # hash → (12, F) float32
_MANIFEST: dict[str, str] | None = None            # hash → audio_path
_KNOWN_ARTISTS: set[str] | None = None             # normalized artist tokens present in library
_TEMPO_CACHE: dict[str, float] | None = None       # hash → BPM (may be empty)
_FLUIDSYNTH = None                                 # lazy-init fluidsynth.Synth (one per worker)
_FLUIDSYNTH_SFID: int = 0
_FLUIDSYNTH_SF2: str | None = None                 # remembers which SF2 was loaded


def _init_worker(sigs_path: str, hashes_path: str, chroma_all_path: str,
                 manifest_path: str, tempo_cache_path: str | None):
    global _SIGS_N, _HASHES, _HASH_TO_ROW, _CHROMA_ALL, _MANIFEST, _KNOWN_ARTISTS, _TEMPO_CACHE
    sigs = np.load(sigs_path)
    norms = np.linalg.norm(sigs, axis=1, keepdims=True)
    _SIGS_N = (sigs / np.clip(norms, 1e-6, None)).astype(np.float32)
    with open(hashes_path, "r", encoding="utf-8") as f:
        _HASHES = json.load(f)
    _HASH_TO_ROW = {h: i for i, h in enumerate(_HASHES)}
    with open(chroma_all_path, "rb") as f:
        raw = pickle.load(f)
    # Pre-sanitize refs: existing pickle was built before the sanitize pass was
    # added to extract_chroma, so frames-of-silence can still be all-zero here.
    _CHROMA_ALL = {h: sanitize_chroma(c) for h, c in raw.items()}
    del raw
    _MANIFEST = {}
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
                if "hash" in r and "path" in r:
                    _MANIFEST[r["hash"]] = r["path"]
            except Exception:
                pass
    _KNOWN_ARTISTS = build_known_artists(manifest_path)
    _TEMPO_CACHE = {}
    if tempo_cache_path and os.path.exists(tempo_cache_path):
        try:
            with open(tempo_cache_path, "r", encoding="utf-8") as f:
                _TEMPO_CACHE = {k: float(v) for k, v in json.load(f).items()}
        except Exception:
            _TEMPO_CACHE = {}


# ---------- cache builders (run once in main process) ----------

def _build_sigs_cache(index_dir: str) -> tuple[str, str]:
    sigs_path = os.path.join(index_dir, "chroma_sigs.npy")
    hashes_path = os.path.join(index_dir, "chroma_hashes.json")
    if os.path.exists(sigs_path) and os.path.exists(hashes_path):
        return sigs_path, hashes_path
    print(f"  [cache] building {sigs_path} from *.npz ...", flush=True)
    sigs = []
    hashes = []
    npz_files = sorted(f for f in os.listdir(index_dir) if f.endswith(".npz"))
    t0 = time.time()
    for i, fn in enumerate(npz_files):
        try:
            with np.load(os.path.join(index_dir, fn), allow_pickle=True) as z:
                sigs.append(z["chroma_sig"].astype(np.float32))
                hashes.append(os.path.splitext(fn)[0])
        except Exception as e:
            print(f"    skip {fn}: {e}", flush=True)
            continue
        if (i + 1) % 2000 == 0:
            print(f"    {i+1}/{len(npz_files)} ({(i+1)/(time.time()-t0):.0f}/s)", flush=True)
    sigs_arr = np.stack(sigs).astype(np.float32)
    np.save(sigs_path, sigs_arr)
    with open(hashes_path, "w", encoding="utf-8") as f:
        json.dump(hashes, f)
    print(f"  [cache] sigs {sigs_arr.shape} in {time.time()-t0:.0f}s", flush=True)
    return sigs_path, hashes_path


def _build_chroma_pickle(index_dir: str, hashes: list[str]) -> str:
    pkl_path = os.path.join(index_dir, "chroma_all.pkl")
    if os.path.exists(pkl_path):
        return pkl_path
    print(f"  [cache] building {pkl_path} (reference chromas) ...", flush=True)
    out: dict[str, np.ndarray] = {}
    t0 = time.time()
    for i, h in enumerate(hashes):
        p = os.path.join(index_dir, f"{h}.npz")
        try:
            with np.load(p, allow_pickle=True) as z:
                out[h] = z["chroma"].astype(np.float32)
        except Exception as e:
            print(f"    skip {h}: {e}", flush=True)
            continue
        if (i + 1) % 2000 == 0:
            print(f"    {i+1}/{len(hashes)} ({(i+1)/(time.time()-t0):.0f}/s)", flush=True)
    with open(pkl_path, "wb") as f:
        pickle.dump(out, f, protocol=pickle.HIGHEST_PROTOCOL)
    size_mb = os.path.getsize(pkl_path) / 1e6
    print(f"  [cache] chroma pickle {len(out)} entries, {size_mb:.0f} MB in {time.time()-t0:.0f}s", flush=True)
    return pkl_path


# ---------- matching ----------

def _rotate_sig(sig: np.ndarray, t: int) -> np.ndarray:
    """Rotate a (24,) signature (mean[12] + std[12]) by t semitones."""
    mean = np.roll(sig[:12], t)
    std = np.roll(sig[12:], t)
    return np.concatenate([mean, std]).astype(np.float32)


def _coarse_match(q_sig: np.ndarray, top_n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (top_row_idx, top_transposition, top_score) of shape (top_n,)."""
    assert _SIGS_N is not None
    # Stack q_sig for all 12 transpositions → (12, 24)
    q_all = np.stack([_rotate_sig(q_sig, t) for t in range(12)], axis=0)
    q_norms = np.linalg.norm(q_all, axis=1, keepdims=True)
    q_all = q_all / np.clip(q_norms, 1e-6, None)
    # (12, 24) @ (24, N) → (12, N)
    scores = q_all @ _SIGS_N.T
    best_trans = scores.argmax(axis=0)                  # (N,)
    best_scores = scores.max(axis=0)                    # (N,)
    top_n = min(top_n, best_scores.shape[0])
    top_idx = np.argpartition(-best_scores, top_n - 1)[:top_n]
    # Sort top by score descending for deterministic tie-breaking
    order = np.argsort(-best_scores[top_idx])
    top_idx = top_idx[order]
    return top_idx, best_trans[top_idx], best_scores[top_idx]


def _subseq_dtw(q: np.ndarray, ref: np.ndarray) -> dict:
    """Run subsequence DTW of q against ref.

    q, ref are (12, F_q), (12, F_r) float32, L2-normalized per frame.
    Returns a dict with cost, warp_mean_s, warp_r2, warp_max_dev_s,
    match_start_s. Bad/degenerate inputs produce a filler with cost=1.0
    and warp_r2=0.0 so the caller's ranking simply drops them.
    """
    import librosa
    Fq = q.shape[1]
    Fr = ref.shape[1]
    filler = {"cost": 1.0, "warp_mean_s": 999.0, "warp_r2": 0.0,
              "warp_max_dev_s": 999.0, "match_start_s": 0.0}
    if Fq < 4 or Fr < 4 or Fq >= Fr:
        return filler
    try:
        D, wp = librosa.sequence.dtw(
            X=q.astype(np.float32), Y=ref.astype(np.float32),
            metric="cosine", subseq=True,
        )
    except Exception:
        return filler
    end_col = int(wp[0, 1])
    start_col = int(wp[-1, 1])
    path_len = int(wp.shape[0])
    normalized_cost = float(D[-1, end_col] / max(path_len, 1))
    slope = (end_col - start_col) / max(Fq - 1, 1)
    expected = start_col + slope * wp[:, 0].astype(np.float32)
    dev_frames = np.abs(wp[:, 1].astype(np.float32) - expected)
    warp_mean_s = float(dev_frames.mean()) / TIME_TARGET_HZ
    r2, max_dev_frames = warp_linearity(wp)
    return {
        "cost": normalized_cost,
        "warp_mean_s": warp_mean_s,
        "warp_r2": r2,
        "warp_max_dev_s": float(max_dev_frames) / TIME_TARGET_HZ,
        "match_start_s": float(start_col) / TIME_TARGET_HZ,
    }


def _rotate_chroma(c: np.ndarray, t: int) -> np.ndarray:
    """Rotate 12-row chroma by t semitones (up)."""
    return np.roll(c, t, axis=0)


# ---------- warp-path stability ----------

def warp_linearity(wp: np.ndarray) -> tuple[float, float]:
    """Linear-regression fit of the DTW warping path.

    Returns (r2, max_dev_frames). A true match has R² close to 1 and small
    max deviation; a harmonic-coincidence false positive produces a jumpy
    path with R² well below 1 — the key signal used to reject the ABBA-
    to-媽媽歌星 style false positives that still score low on cost alone.
    """
    if wp.shape[0] < 3:
        return (0.0, 0.0)
    x = wp[:, 0].astype(np.float32)
    y = wp[:, 1].astype(np.float32)
    x_mean = float(x.mean()); y_mean = float(y.mean())
    dx = x - x_mean; dy = y - y_mean
    denom = float((dx * dx).sum())
    if denom < 1e-6:
        return (0.0, 0.0)
    slope = float((dx * dy).sum() / denom)
    intercept = y_mean - slope * x_mean
    resid = y - (slope * x + intercept)
    ss_res = float((resid * resid).sum())
    ss_tot = float((dy * dy).sum())
    r2 = 1.0 - ss_res / max(ss_tot, 1e-9)
    r2 = max(0.0, min(1.0, r2))
    max_dev = float(np.abs(resid).max())
    return (r2, max_dev)


# ---------- key detection (Krumhansl-Schmuckler) ----------

_KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88], dtype=np.float32)
_KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17], dtype=np.float32)
_PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def detect_key(chroma_n: np.ndarray) -> tuple[int, str, float]:
    """Estimate (root, mode, correlation) from a (12, F) chroma matrix.

    mode ∈ {'major','minor'}. Correlation is the match strength of the
    winning template, useful for reporting confidence.
    """
    mean = chroma_n.mean(axis=1).astype(np.float32)
    total = float(mean.sum())
    if total < 1e-6:
        return (0, "major", 0.0)
    mean = mean / total
    mc = mean - mean.mean()
    mc_norm = float(np.linalg.norm(mc))
    best = (-2.0, 0, "major")
    for root in range(12):
        for mode, profile in (("major", _KS_MAJOR), ("minor", _KS_MINOR)):
            rp = np.roll(profile, root).astype(np.float32)
            rp = rp - rp.mean()
            denom = max(mc_norm * float(np.linalg.norm(rp)), 1e-9)
            corr = float(np.dot(mc, rp) / denom)
            if corr > best[0]:
                best = (corr, root, mode)
    return (best[1], best[2], best[0])


def key_label(root: int, mode: str) -> str:
    return f"{_PITCH_NAMES[root % 12]} {mode}"


# ---------- filename artist-hint parsing + known-artist gate ----------

_ARTIST_PAREN_RE = re.compile(r"^\s*\(([^)]{1,60})\)")


def parse_artist_hint(filename: str) -> str | None:
    """Best-effort artist extraction from MIDI filename.

    Recognised patterns:
      (artist)title.mid                 → GeoCities Pop-Western convention
      artist - title.mid                → clean 'name - name' form
      artist-title.mid or artist_title  → dash/underscore with no spaces
    Returns None for cryptic names like '1000song.mid' or 'faye01.mid'.
    """
    name = os.path.splitext(os.path.basename(filename))[0]
    m = _ARTIST_PAREN_RE.match(name)
    if m:
        token = m.group(1).strip()
        if token:
            return token
    for sep in (" - ", " _ ", " - ".replace(" ", ""), "_"):
        if sep and sep in name:
            left = name.split(sep, 1)[0].strip()
            left = left.replace("_", " ").strip()
            if len(left) >= 2 and any(c.isalpha() for c in left):
                return left
            break
    return None


_ARTIST_NORM_STRIP = re.compile(r"[^a-z0-9一-鿿\s]")
_WS_RE = re.compile(r"\s+")


def _normalize_artist(s: str) -> str:
    s = s.lower().replace("_", " ").replace(".", " ")
    s = _ARTIST_NORM_STRIP.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def build_known_artists(manifest_path: str) -> set[str]:
    """Collect normalized artist tokens from manifest paths.

    Uses the same 3rd-to-last-folder / album-split heuristic as
    parse_artist_title, but extracted standalone so we can gate work
    BEFORE running synth+DTW.
    """
    tokens: set[str] = set()
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
                p = r.get("path", "").replace("\\", "/")
                parts = [x for x in p.split("/") if x]
                if len(parts) >= 3:
                    tokens.add(_normalize_artist(parts[-3]))
                if len(parts) >= 2:
                    album = parts[-2]
                    if " - " in album:
                        tokens.add(_normalize_artist(album.split(" - ", 1)[0]))
            except Exception:
                pass
    tokens.discard("")
    return tokens


def artist_hint_in_library(hint: str, known_artists: set[str]) -> bool:
    """Word-token subset match — either direction.

    Returns True for too-short hints so the gate errs on 'run DTW'. For
    multi-word hints, requires that all tokens (or all library tokens)
    appear as full words — not substrings — to avoid 'aerosmith' landing
    inside a library token 'ros' or similar noise.
    """
    n = _normalize_artist(hint)
    if len(n) < 2:
        return True
    if n in known_artists:
        return True
    hint_tokens = set(t for t in n.split() if t)
    if not hint_tokens:
        return True
    for ka in known_artists:
        if not ka:
            continue
        ka_tokens = set(t for t in ka.split() if t)
        if not ka_tokens:
            continue
        if hint_tokens.issubset(ka_tokens) or ka_tokens.issubset(hint_tokens):
            return True
    return False


# ---------- metadata parsing ----------

_SAFE_CHARS = re.compile(r'[\\/:*?"<>|]+')
_WS = re.compile(r"\s+")


def _safe_name(s: str) -> str:
    s = _SAFE_CHARS.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s[:120] or "unknown"


def parse_artist_title(audio_path: str) -> tuple[str, str]:
    """Best-effort (artist, title) from a manifest audio path.

    Typical shape:  Z:/POP/K-POP\\aespa\\aespa - Armageddon - The 1st Album\\Licorice.flac
      → artist='aespa', title='Licorice'
    Fallbacks try album-folder split on ' - ', parent folder, or 'Unknown'.
    """
    norm = audio_path.replace("\\", "/")
    parts = [p for p in norm.split("/") if p]
    if not parts:
        return ("Unknown", os.path.splitext(os.path.basename(audio_path))[0] or "Unknown")
    title = os.path.splitext(parts[-1])[0]
    artist = "Unknown"
    # parts[-2] = album folder, parts[-3] = artist folder (most common case)
    if len(parts) >= 3:
        candidate = parts[-3]
        if candidate and not candidate[:1].isdigit():
            artist = candidate
    if artist == "Unknown" and len(parts) >= 2:
        album = parts[-2]
        if " - " in album:
            artist = album.split(" - ", 1)[0]
        elif album and not album[:1].isdigit():
            artist = album
    return (_safe_name(artist), _safe_name(title))


# ---------- per-MIDI worker ----------

def _midi_bpm(pm) -> float:
    """Median tempo from a PrettyMIDI instance. 0.0 if unknown."""
    try:
        times, tempi = pm.get_tempo_changes()
        if len(tempi) == 0:
            return 0.0
        return float(np.median(tempi))
    except Exception:
        try:
            return float(pm.estimate_tempo())
        except Exception:
            return 0.0


def _synth_midi(path: str, max_seconds: float,
                backend: str = "sine", soundfont: str | None = None) -> tuple[np.ndarray, float, float]:
    """Synthesize a MIDI to mono float32 audio at SR.

    backend:
      'sine'       — pretty_midi.synthesize (default). Fast, pure fundamentals.
                     Chroma is 'thin' because real audio has harmonic spill;
                     true-positive DTW costs land higher than ideal.
      'fluidsynth' — pretty_midi.fluidsynth(sf2_path=...). Needs pyfluidsynth
                     + a General MIDI soundfont (e.g. FluidR3_GM.sf2). Produces
                     harmonically-rich audio whose chroma aligns much better
                     with real recordings. Slower (~3×) but typically halves
                     the true-positive cost.

    Returns (y, end_time_s, midi_bpm).  midi_bpm=0.0 if unknown.
    """
    import pretty_midi
    pm = pretty_midi.PrettyMIDI(path)
    end_time = float(pm.get_end_time())
    if end_time > MAX_MIDI_END_SEC:
        raise RuntimeError(f"midi_too_long:{end_time:.0f}s")
    if end_time < 5.0:
        raise RuntimeError(f"midi_too_short:{end_time:.1f}s")
    bpm = _midi_bpm(pm)
    if backend == "fluidsynth":
        if not soundfont or not os.path.isfile(soundfont):
            raise RuntimeError(f"soundfont_missing:{soundfont}")
        global _FLUIDSYNTH, _FLUIDSYNTH_SFID, _FLUIDSYNTH_SF2
        if _FLUIDSYNTH is None or _FLUIDSYNTH_SF2 != soundfont:
            # Lazy-init once per worker. pretty_midi.PrettyMIDI.fluidsynth
            # otherwise spins up a new Synth + reloads the 150 MB SF2 on
            # every call (~10 s wasted per MIDI).
            import fluidsynth as _fs
            if _FLUIDSYNTH is not None:
                try: _FLUIDSYNTH.delete()
                except Exception: pass
            _FLUIDSYNTH = _fs.Synth(samplerate=float(SR))
            _FLUIDSYNTH_SFID = _FLUIDSYNTH.sfload(soundfont)
            _FLUIDSYNTH_SF2 = soundfont
        y = pm.fluidsynth(synthesizer=_FLUIDSYNTH, sfid=_FLUIDSYNTH_SFID)
    else:
        y = pm.synthesize(fs=SR)
    if y.ndim > 1:
        y = y.mean(axis=1)
    y = y.astype(np.float32)
    n = int(max_seconds * SR)
    if len(y) > n:
        y = y[:n]
    return y, end_time, bpm


def process_one_midi(args: tuple) -> dict:
    midi_path, cfg = args
    try:
        try:
            h = file_hash(midi_path)
        except Exception as e:
            return {"midi_path": midi_path, "status": "hash_failed",
                    "error": f"{type(e).__name__}:{str(e)[:120]}"}

        # ----- Known-artist gate (runs BEFORE synth to save compute) -----
        artist_hint = parse_artist_hint(midi_path)
        if (cfg["gate_known_artist"] and artist_hint and _KNOWN_ARTISTS is not None
                and not artist_hint_in_library(artist_hint, _KNOWN_ARTISTS)):
            return {
                "midi_path": midi_path,
                "midi_hash": h,
                "status": "UNMATCHED_KNOWN_ARTIST",
                "artist_hint": artist_hint,
                "reason": "filename artist hint not present in reference library",
            }

        try:
            y, midi_end, midi_bpm = _synth_midi(
                midi_path, cfg["max_seconds"],
                backend=cfg.get("synth_backend", "sine"),
                soundfont=cfg.get("soundfont"),
            )
        except Exception as e:
            return {"midi_path": midi_path, "midi_hash": h, "status": "synth_failed",
                    "error": f"{type(e).__name__}:{str(e)[:120]}"}

        try:
            q_chroma, q_sig = extract_chroma(y, sr=SR)
        except Exception as e:
            return {"midi_path": midi_path, "midi_hash": h, "status": "extract_failed",
                    "error": f"{type(e).__name__}:{str(e)[:120]}"}
        finally:
            del y
            gc.collect()

        top_idx, top_trans, top_coarse = _coarse_match(q_sig, cfg["coarse_top"])

        assert _HASHES is not None and _CHROMA_ALL is not None
        results: list[dict] = []
        for row_i, t, coarse_score in zip(top_idx, top_trans, top_coarse):
            hh = _HASHES[int(row_i)]
            ref = _CHROMA_ALL.get(hh)
            if ref is None:
                continue
            q_rot = _rotate_chroma(q_chroma, int(t))
            d = _subseq_dtw(q_rot, ref)
            d["hash"] = hh
            d["trans"] = int(t)
            d["coarse"] = float(coarse_score)
            results.append(d)

        if not results:
            return {"midi_path": midi_path, "midi_hash": h, "status": "no_candidates"}

        # Rank: cost first (how well chroma aligns), then warp R² inverted
        # (higher R² = better path linearity, used as tiebreaker).
        results.sort(key=lambda r: (r["cost"], -r["warp_r2"]))
        best = results[0]
        runners = results[1:4]
        matched_audio = (_MANIFEST or {}).get(best["hash"], "")
        artist, title = (parse_artist_title(matched_audio) if matched_audio
                         else ("Unknown", "Unknown"))

        # ----- Key-mode check: rotated query key mode vs ref key mode -----
        q_rot_best = _rotate_chroma(q_chroma, int(best["trans"]))
        kq_root, kq_mode, kq_score = detect_key(q_rot_best)
        ref_chroma = _CHROMA_ALL[best["hash"]]
        kr_root, kr_mode, kr_score = detect_key(ref_chroma)
        mode_match = (kq_mode == kr_mode)
        root_match = (kq_root == kr_root)

        # ----- Tempo check (neutral if either BPM unknown) -----
        ref_bpm = (_TEMPO_CACHE or {}).get(best["hash"], 0.0)
        tempo_match: bool | None
        tempo_ratio = None
        if midi_bpm > 0 and ref_bpm > 0:
            tempo_ratio = float(midi_bpm / ref_bpm)
            # Accept 1x and 2x / 0.5x (MIDI transcribers often halve/double).
            def _within(r: float, tol: float) -> bool:
                return abs(r - 1.0) <= tol or abs(r - 2.0) <= tol * 2 or abs(r - 0.5) <= tol * 0.5
            tempo_match = _within(tempo_ratio, cfg["tempo_tolerance"])
        else:
            tempo_match = None  # neutral

        # ----- Status decision -----
        reasons: list[str] = []
        passes_cost = best["cost"] < cfg["cost_threshold"]
        passes_warp = best["warp_r2"] >= cfg["warp_r2_threshold"]
        passes_key = mode_match if cfg["check_key"] else True
        passes_tempo = (tempo_match is not False)  # True or None both accept

        if not passes_cost:
            reasons.append(f"cost {best['cost']:.4f} >= {cfg['cost_threshold']:.3f}")
        if not passes_warp:
            reasons.append(f"warp_r2 {best['warp_r2']:.3f} < {cfg['warp_r2_threshold']:.2f}")
        if not passes_key:
            reasons.append(f"mode_mismatch q={kq_mode} ref={kr_mode}")
        if tempo_match is False:
            reasons.append(f"tempo_mismatch midi={midi_bpm:.1f} ref={ref_bpm:.1f}")

        status = "matched" if (passes_cost and passes_warp and passes_key and passes_tempo) else "unmatched"
        confidence = float(max(0.0, 1.0 - best["cost"]) *
                           max(0.0, best["warp_r2"]) *
                           (1.0 if mode_match else 0.7) *
                           (1.0 if tempo_match is not False else 0.5))

        out = {
            "midi_path": midi_path,
            "midi_hash": h,
            "midi_end_s": midi_end,
            "midi_bpm": round(float(midi_bpm), 2),
            "artist_hint": artist_hint,
            "status": status,
            "matched_hash": best["hash"],
            "matched_audio": matched_audio,
            "transposition": best["trans"],
            "artist": artist,
            "title": title,
            "confidence": round(confidence, 3),
            "normalized_cost": round(best["cost"], 4),
            "warp_r2": round(best["warp_r2"], 3),
            "warp_max_dev_s": round(best["warp_max_dev_s"], 2),
            "warp_mean_s": round(best["warp_mean_s"], 2),
            "match_start_s": round(best["match_start_s"], 2),
            "coarse_score": round(best["coarse"], 3),
            "key_query": key_label(kq_root, kq_mode),
            "key_ref": key_label(kr_root, kr_mode),
            "mode_match": mode_match,
            "root_match": root_match,
            "ref_bpm": round(float(ref_bpm), 2) if ref_bpm > 0 else None,
            "tempo_ratio": round(tempo_ratio, 3) if tempo_ratio is not None else None,
            "tempo_match": tempo_match,
            "reject_reasons": reasons,
            "alt": [
                {
                    "hash": r["hash"],
                    "cost": round(r["cost"], 4),
                    "warp_r2": round(r["warp_r2"], 3),
                    "warp_mean_s": round(r["warp_mean_s"], 2),
                    **dict(zip(("artist", "title"),
                               parse_artist_title((_MANIFEST or {}).get(r["hash"], "")))),
                }
                for r in runners
            ],
        }
        return out
    except Exception as e:
        return {"midi_path": midi_path, "status": "worker_error",
                "error": f"{type(e).__name__}:{str(e)[:160]}"}


# ---------- main driver ----------

def _walk_midis(root: str):
    for dp, _, fns in os.walk(root):
        for fn in fns:
            if fn.lower().endswith(MIDI_EXTS):
                yield os.path.join(dp, fn)


def _norm_path(p: str) -> str:
    return os.path.normpath(p).replace("\\", "/")


def _load_resume(catalog_path: str) -> set[str]:
    seen = set()
    if not os.path.exists(catalog_path):
        return seen
    with open(catalog_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
                if "midi_path" in r:
                    seen.add(_norm_path(r["midi_path"]))
            except Exception:
                pass
    return seen


def _copy_match(src: str, dest_dir: str, artist: str, title: str) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    base = f"{artist} - {title}.mid"
    dest = os.path.join(dest_dir, base)
    if os.path.exists(dest):
        stem, ext = os.path.splitext(base)
        n = 2
        while True:
            dest = os.path.join(dest_dir, f"{stem} ({n}){ext}")
            if not os.path.exists(dest):
                break
            n += 1
    shutil.copy2(src, dest)
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="MIDI folder to scan (recursive)")
    ap.add_argument("--index", required=True, help="V:/Database/Chroma_Index")
    ap.add_argument("--dest", required=True, help="output folder for renamed matched MIDIs")
    ap.add_argument("--catalog", required=True, help="midi_catalog.jsonl path")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--max-seconds", type=float, default=DEFAULT_QUERY_SECONDS)
    ap.add_argument("--coarse-top", type=int, default=2000,
                    help="Number of fine-DTW candidates after the 24-dim sig coarse stage. "
                         "Bumped from 300 because the sig isn't discriminative enough — true "
                         "matches can land at coarse rank 10k+ even with R²=0.998.")
    ap.add_argument("--cost-threshold", type=float, default=0.08,
                    help="Max normalized DTW cost for a match (tight default after calibration)")
    ap.add_argument("--warp-r2-threshold", type=float, default=0.95,
                    help="Min warp-path R² (linearity) — rejects harmonic-coincidence false positives")
    ap.add_argument("--tempo-tolerance", type=float, default=0.15,
                    help="Fractional BPM tolerance (0.15 = ±15%). Applies if tempo_cache present.")
    ap.add_argument("--skip-key-check", action="store_true",
                    help="Disable Krumhansl major/minor mode-match gate")
    ap.add_argument("--skip-artist-gate", action="store_true",
                    help="Disable filename-artist pre-DTW gate (e.g. for --dry-run calibration)")
    ap.add_argument("--tempo-cache", default=None,
                    help="Path to tempo_cache.json (hash → BPM). Default: <index>/tempo_cache.json if present")
    ap.add_argument("--synth-backend", choices=("sine", "fluidsynth"), default="sine",
                    help="Audio synthesis backend. 'sine' uses pretty_midi.synthesize (default, no deps). "
                         "'fluidsynth' needs pyfluidsynth + --soundfont.")
    ap.add_argument("--soundfont", default=None,
                    help="Path to .sf2 file (required for --synth-backend fluidsynth)")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="First-N truncation after ordering")
    ap.add_argument("--sample", type=int, default=0,
                    help="Randomly sample N files after walking (for dry-run calibration)")
    ap.add_argument("--seed", type=int, default=42, help="Shuffle seed for --sample")
    args = ap.parse_args()

    manifest_path = os.path.join(args.index, "manifest.jsonl")
    if not os.path.exists(manifest_path):
        print(f"manifest not found at {manifest_path}", flush=True)
        sys.exit(2)

    sigs_path, hashes_path = _build_sigs_cache(args.index)
    with open(hashes_path, "r", encoding="utf-8") as f:
        hashes = json.load(f)
    chroma_all_path = _build_chroma_pickle(args.index, hashes)

    print(f"walking {args.source}...", flush=True)
    all_midis = list(_walk_midis(args.source))
    print(f"  found {len(all_midis)} midi files", flush=True)

    seen = _load_resume(args.catalog) if args.resume else set()
    if seen:
        print(f"  resume: {len(seen)} already in catalog, skipping", flush=True)
    pending = [p for p in all_midis if _norm_path(p) not in seen]
    if args.sample and args.sample < len(pending):
        import random
        rng = random.Random(args.seed)
        rng.shuffle(pending)
        pending = pending[: args.sample]
        print(f"  sampled {len(pending)} (seed={args.seed})", flush=True)
    elif args.limit and args.limit < len(pending):
        pending = pending[: args.limit]
    print(f"  to process: {len(pending)}", flush=True)
    if not pending:
        print("nothing to do."); return

    os.makedirs(os.path.dirname(args.catalog) or ".", exist_ok=True)
    os.makedirs(args.dest, exist_ok=True)

    tempo_cache_path = args.tempo_cache or os.path.join(args.index, "tempo_cache.json")
    if not os.path.exists(tempo_cache_path):
        tempo_cache_path = None

    if args.synth_backend == "fluidsynth":
        if not args.soundfont or not os.path.isfile(args.soundfont):
            print(f"[ERROR] --synth-backend fluidsynth requires --soundfont PATH "
                  f"(got {args.soundfont!r})", flush=True)
            sys.exit(2)
        try:
            import fluidsynth  # noqa: F401
        except ImportError:
            print("[ERROR] --synth-backend fluidsynth needs `pip install pyfluidsynth` "
                  "(and the fluidsynth native binary on PATH)", flush=True)
            sys.exit(2)

    cfg = {
        "max_seconds": float(args.max_seconds),
        "coarse_top": int(args.coarse_top),
        "cost_threshold": float(args.cost_threshold),
        "warp_r2_threshold": float(args.warp_r2_threshold),
        "tempo_tolerance": float(args.tempo_tolerance),
        "check_key": not args.skip_key_check,
        "gate_known_artist": not args.skip_artist_gate,
        "synth_backend": args.synth_backend,
        "soundfont": args.soundfont,
    }
    init_args = (sigs_path, hashes_path, chroma_all_path, manifest_path, tempo_cache_path)
    print(f"  cost<{cfg['cost_threshold']:.3f}  warp_r2>={cfg['warp_r2_threshold']:.2f}  "
          f"key_check={cfg['check_key']}  artist_gate={cfg['gate_known_artist']}  "
          f"tempo_cache={'yes' if tempo_cache_path else 'no'}  "
          f"synth={cfg['synth_backend']}", flush=True)
    jobs = [(p, cfg) for p in pending]

    ok = matched = unmatched = fail = 0
    t0 = time.time()

    from collections import defaultdict
    retry_counts: dict[str, int] = defaultdict(int)
    pending_jobs = list(jobs)
    total_jobs = len(jobs)

    pool_kwargs = {
        "max_workers": args.workers,
        "initializer": _init_worker,
        "initargs": init_args,
    }

    with open(args.catalog, "a", encoding="utf-8") as cat:
        BATCH = 100
        while pending_jobs:
            try:
                ex = ProcessPoolExecutor(**pool_kwargs)
            except Exception as e:
                print(f"  [ERROR] cannot start pool: {e}", flush=True)
                break
            with ex:
                while pending_jobs:
                    batch = pending_jobs[:BATCH]
                    fut_to_job = {ex.submit(process_one_midi, j): j for j in batch}
                    uncompleted = set(fut_to_job.keys())
                    pool_broken = False

                    while uncompleted:
                        done, uncompleted = wait(uncompleted, timeout=180.0,
                                                 return_when=FIRST_COMPLETED)
                        if not done and uncompleted:
                            print(f"  [WARNING] no jobs completed in 3 min. {len(uncompleted)} remain",
                                  flush=True)

                        for fut in done:
                            j = fut_to_job[fut]
                            try:
                                r = fut.result()
                            except BrokenProcessPool:
                                key = j[0]
                                print(f"  [ERROR] worker died near {key}", flush=True)
                                retry_counts[key] += 1
                                if retry_counts[key] > 2:
                                    print(f"  [ERROR] dropping {key} after 3 worker deaths", flush=True)
                                    pending_jobs.remove(j)
                                    fail += 1
                                pool_broken = True
                                break
                            except Exception as e:
                                r = {"midi_path": j[0], "status": "worker_error",
                                     "error": f"{type(e).__name__}:{str(e)[:160]}"}

                            if pool_broken:
                                break
                            pending_jobs.remove(j)

                            status = r.get("status", "")
                            if status == "matched":
                                matched += 1
                                if not args.dry_run:
                                    try:
                                        dest = _copy_match(r["midi_path"], args.dest,
                                                           r["artist"], r["title"])
                                        r["copied_to"] = dest
                                    except Exception as e:
                                        r["copy_error"] = f"{type(e).__name__}:{str(e)[:160]}"
                                else:
                                    r["copied_to"] = None
                            elif status in ("unmatched", "UNMATCHED_KNOWN_ARTIST", "no_candidates"):
                                unmatched += 1
                            else:
                                fail += 1

                            if "midi_path" in r:
                                r["midi_path"] = _norm_path(r["midi_path"])
                            cat.write(json.dumps(r, ensure_ascii=False) + "\n")
                            cat.flush()
                            ok += 1

                            if ok % 25 == 0 or not pending_jobs:
                                rate = ok / (time.time() - t0 + 1e-6)
                                eta = len(pending_jobs) / max(rate, 1e-6)
                                print(f"  [{ok}/{total_jobs}] matched={matched} "
                                      f"unmatched={unmatched} fail={fail} "
                                      f"{rate:.2f}/s ETA {eta:.0f}s", flush=True)

                        if pool_broken:
                            break
                    if pool_broken:
                        print("  [INFO] restarting ProcessPoolExecutor...", flush=True)
                        break

    print(f"\nDone: total={ok} matched={matched} unmatched={unmatched} fail={fail}")
    print(f"  catalog: {args.catalog}")
    if not args.dry_run:
        print(f"  copies : {args.dest}")


if __name__ == "__main__":
    main()
