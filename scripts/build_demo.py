"""Demo-songs builder — runs BTC chord detection, beat tracking, and melody
extraction directly against the curated MP3s under ``data/demo/`` and writes
out everything the VPS needs to serve them.

Calls the backend's analysis functions in-process — no running uvicorn needed.
Run from the repo root:

    python scripts/build_demo.py

Outputs (per track):
- ``data/demo/chords/<demo_hash>.json``  — chord + beat + downbeat data
- ``data/melodies/<demo_hash>.json``     — melody waterfall data
                                          (force-add to git: see ship-checklist)
- ``data/demo/manifest.json``            — list shipped to the homepage

Audio files (``data/demo/<id>.mp3``) and covers (``data/demo/covers/<id>.jpg``)
must already exist before running this — see the README in data/demo/ if/when
you add it. Also extracts the embedded cover from the MP3 if no JPG is present.
"""

import argparse
import hashlib
import json
import sys
import time
from io import BytesIO
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

# Internal backend imports — done after sys.path tweak so they resolve from the
# repo's backend dir, not whatever's installed system-wide.
from chord_cache import song_hash  # noqa: E402

DATA_DIR = REPO_ROOT / "data"
DEMO_DIR = DATA_DIR / "demo"
DEMO_AUDIO_DIR = DEMO_DIR
DEMO_CHORDS_DIR = DEMO_DIR / "chords"
DEMO_COVERS_DIR = DEMO_DIR / "covers"
MELODIES_DIR = DATA_DIR / "melodies"
MANIFEST_FILE = DEMO_DIR / "manifest.json"

# ---------------------------------------------------------------------------
# Curated 15-track set, organized into 4 categories. Order within a category
# is "easy / globally-recognized first" so each row's first card hooks the
# visitor (Canon in D opens Classical, Twinkle Twinkle opens Easy, etc).
# Frontend renders one .demo-category sub-row per `category` value.
# ---------------------------------------------------------------------------

TRACKS = [
    # === 🎹 Classical (6) ===
    {
        "id": "canon_in_d",
        "title": "Canon in D",
        "artist": "Pachelbel",
        "license": "CC-BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "source_url": "https://commons.wikimedia.org/wiki/File:Pachelbel_-_Canon_in_D_major,_P._37_(Guitar).ogg",
        "vibe": "The killer demo — universal recognition, clearest progression",
        "category": "classical",
    },
    {
        "id": "fur_elise",
        "title": "Für Elise",
        "artist": "Beethoven (perf. Eileen Joyce, 1940)",
        "license": "Public Domain",
        "license_url": "https://creativecommons.org/publicdomain/mark/1.0/",
        "source_url": "https://archive.org/details/dx-974-beethoven-bagatelle-op-33-2",
        "vibe": "Romantic miniature; A minor",
        "category": "classical",
    },
    {
        "id": "moonlight_sonata_1st",
        "title": "Moonlight Sonata (1st mvt)",
        "artist": "Beethoven (perf. Solomon Cutner, ~1948)",
        "license": "Public Domain",
        "license_url": "https://creativecommons.org/publicdomain/mark/1.0/",
        "source_url": "https://archive.org/details/78_1st-movement-part-1-adagio-sostenuto_solomon-beethoven_gbia7003797a",
        "vibe": "Adagio sostenuto — slow triplet figuration",
        "category": "classical",
    },
    {
        "id": "air_on_g_string",
        "title": "Air on the G String",
        "artist": "J.S. Bach",
        "license": "Public Domain",
        "license_url": "https://creativecommons.org/publicdomain/mark/1.0/",
        "source_url": "https://commons.wikimedia.org/wiki/File:Air_(Bach).ogg",
        "vibe": "Baroque, super-stable beat — beat-alignment showcase",
        "category": "classical",
    },
    {
        "id": "clair_de_lune",
        "title": "Clair de Lune",
        "artist": "Debussy",
        "license": "CC-BY 3.0",
        "license_url": "https://creativecommons.org/licenses/by/3.0/",
        "source_url": "https://commons.wikimedia.org/wiki/File:Clair_de_lune_(Claude_Debussy)_Suite_bergamasque.ogg",
        "vibe": "Impressionist, modal — tests harmonic sophistication",
        "category": "classical",
    },
    {
        "id": "chopin_nocturne_op9_no2",
        "title": "Nocturne Op. 9 No. 2 in E♭ Major",
        "artist": "Frédéric Chopin",
        "license": "Public Domain",
        "license_url": "https://creativecommons.org/publicdomain/mark/1.0/",
        "source_url": "https://www.classicals.de/chopin-op9",
        "vibe": "Romantic classical solo piano — 12/8 compound triple",
        "category": "classical",
        # Manual override: beat_this detects this Nocturne as ~38 BPM in 4/4,
        # but it's actually 12/8 felt as 3-pulse at ~50 BPM.
        "bpm_override": 50.0,
        "beats_per_bar_override": 3,
    },

    # === 🌍 Folk (3) ===
    {
        "id": "greensleeves",
        "title": "Greensleeves",
        "artist": "English traditional (~1580)",
        "license": "CC-BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "source_url": "https://commons.wikimedia.org/wiki/File:Greensleeves_for_solo_piano.wav",
        "vibe": "A minor — clear minor-key detection target",
        "category": "folk",
    },
    {
        "id": "auld_lang_syne",
        "title": "Auld Lang Syne",
        "artist": "Robert Burns / Frank C. Stanley (1910)",
        "license": "Public Domain",
        "license_url": "https://creativecommons.org/publicdomain/mark/1.0/",
        "source_url": "https://commons.wikimedia.org/wiki/File:Auld_Lang_Syne.ogg",
        "vibe": "Universal New Year's tune; I-IV-V",
        "category": "folk",
    },
    {
        "id": "scarborough_fair",
        "title": "Scarborough Fair",
        "artist": "English traditional",
        "license": "Public Domain",
        "license_url": "https://creativecommons.org/publicdomain/mark/1.0/",
        "source_url": "https://commons.wikimedia.org/wiki/File:Anonimo_-_Scarborough_Fair.ogg",
        "vibe": "Dorian mode — proves we're not just major/minor",
        "category": "folk",
    },

    # === 🎷 Jazz (3) ===
    {
        "id": "when_the_saints",
        "title": "When the Saints Go Marching In",
        "artist": "American traditional",
        "license": "Public Domain",
        "license_url": "https://creativecommons.org/publicdomain/mark/1.0/",
        "source_url": "https://commons.wikimedia.org/wiki/File:When_the_Saints_Go_Marching_In.ogg",
        "vibe": "Dixieland I-IV-V — beginner-friendly",
        "category": "jazz",
    },
    {
        "id": "carefree",
        "title": "Carefree",
        "artist": "Kevin MacLeod",
        "license": "CC-BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "source_url": "https://incompetech.com/music/royalty-free/index.html?keywords=carefree",
        "vibe": "Upbeat ragtime piano",
        "category": "jazz",
    },
    {
        "id": "bossa_antigua",
        "title": "Bossa Antigua",
        "artist": "Kevin MacLeod",
        "license": "CC-BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "source_url": "https://incompetech.com/music/royalty-free/index.html?keywords=bossa+antigua",
        "vibe": "Jazz / bossa with ii-V-I and 7ths",
        "category": "jazz",
    },

    # === 🎵 Easy (3) ===
    {
        "id": "twinkle_mozart_k265",
        "title": "Twinkle, Twinkle (Mozart K.265 Variations)",
        "artist": "Mozart",
        "license": "CC-BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "source_url": "https://commons.wikimedia.org/wiki/File:KV.265_12_Variations_on_Ah_vous_dirai-je,_Maman_Mozart_JMC,_Han.ogg",
        "vibe": "Theme + 12 variations on the universal nursery tune",
        "category": "easy",
    },
    {
        "id": "frere_jacques",
        "title": "Frère Jacques (兩隻老虎)",
        "artist": "French traditional",
        "license": "CC-BY-SA 3.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/3.0/",
        "source_url": "https://commons.wikimedia.org/wiki/File:Frere_Jacques_-_canon.ogg",
        "vibe": "Round in C — 5 seconds to 'oh I know this'",
        "category": "easy",
    },
    {
        "id": "oh_susanna",
        "title": "Oh! Susanna",
        "artist": "Stephen Foster (1917 recording)",
        "license": "Public Domain",
        "license_url": "https://creativecommons.org/publicdomain/mark/1.0/",
        "source_url": "https://commons.wikimedia.org/wiki/File:Oh_Susanna1917.ogg",
        "vibe": "Foster (d. 1864) Americana classic",
        "category": "easy",
    },
]


def demo_hash(track_id: str) -> str:
    """Match backend chord_cache.song_hash() for path "__demo/<id>"."""
    return song_hash(f"__demo/{track_id}")


def _infer_beats_per_bar(chord_data: dict, sec_per_beat: float) -> int:
    """Mirror frontend chord-correction.js:inferBeatsPerBar — median of inter-
    downbeat gaps in beats, snapped to 3 or 4. Defaults to 4 if no usable
    downbeats[]."""
    db = chord_data.get("downbeats") or []
    if len(db) < 3 or sec_per_beat <= 0:
        return 4
    diffs = [db[i] - db[i-1] for i in range(1, len(db))]
    if not diffs:
        return 4
    diffs.sort()
    med = diffs[len(diffs) // 2]
    beats = round(med / sec_per_beat)
    return beats if beats in (3, 4) else 4


def _apply_bar_split(chord_data: dict) -> int:
    """Pre-apply the player's "依小節切分" (bar-aware split) so demo chord
    cards are bar-sized when the player first loads — no need for the user
    to open the 自動切分 panel.

    Direct port of player.js _showAutoSplitPanel mode='bar'. Mutates
    ``chord_data['chords']`` in place; returns the number of original chords
    that were split.
    """
    bpm = chord_data.get("bpm")
    chords = chord_data.get("chords") or []
    if not bpm or bpm <= 0 or not chords:
        return 0
    sec_per_beat = 60.0 / float(bpm)
    beats_per_bar = _infer_beats_per_bar(chord_data, sec_per_beat)
    bar_dur = beats_per_bar * sec_per_beat

    # Phase: real downbeat[0] preferred, else beat[0], else first chord time.
    dbs = chord_data.get("downbeats") or []
    beats_arr = chord_data.get("beats") or []
    phase = (dbs[0] if dbs else
             (beats_arr[0] if beats_arr else
              (chords[0].get("time", 0) if chords else 0)))

    def next_bar_anchor(t: float) -> float:
        for d in dbs:
            if d > t + 1e-3:
                return d
        # Fallback synthetic grid
        import math
        k = math.ceil((t - phase - 1e-6) / bar_dur)
        return phase + k * bar_dur

    def split_at(idx: int, first_beats: int, second_beats: int) -> None:
        c = chords[idx]
        cut = c["time"] + first_beats * sec_per_beat
        new = {
            "time": round(cut, 3),
            "end": c.get("end"),
            "chord": c["chord"],
        }
        c["end"] = round(cut, 3)
        chords.insert(idx + 1, new)

    count = 0
    # Reverse iteration so inserts don't disturb the walk.
    for i in range(len(chords) - 1, -1, -1):
        c = chords[i]
        end = c.get("end")
        dur = (end - c["time"]) if end is not None else (
            (chords[i + 1]["time"] - c["time"]) if i < len(chords) - 1 else 2.0
        )
        beats = round(dur / sec_per_beat)
        if beats <= beats_per_bar:
            continue

        anchor = next_bar_anchor(c["time"])
        if anchor > c["time"] + 1e-3 and anchor < c["time"] + dur - 1e-3:
            first_split_beats = round((anchor - c["time"]) / sec_per_beat)
            if first_split_beats < 1 or first_split_beats >= beats:
                first_split_beats = beats_per_bar
        else:
            first_split_beats = beats_per_bar

        cursor = i
        remaining = beats
        did_split = False

        if first_split_beats < beats and first_split_beats != beats_per_bar:
            split_at(cursor, first_split_beats, remaining - first_split_beats)
            remaining -= first_split_beats
            cursor += 1
            did_split = True

        while remaining > beats_per_bar:
            split_at(cursor, beats_per_bar, remaining - beats_per_bar)
            remaining -= beats_per_bar
            cursor += 1
            did_split = True

        if did_split:
            count += 1

    return count


_BEAT_THIS_PREDICTOR = None  # cached File2Beats; ~5s to load fresh


def _beat_this_local(audio_path: str, chords: list) -> dict:
    """Direct beat_this inference, mirrors backend/modal_beat_this.py output
    so the chord JSON has the same shape demos would get from the VPS."""
    global _BEAT_THIS_PREDICTOR
    import numpy as np
    from beat_this.inference import File2Beats
    if _BEAT_THIS_PREDICTOR is None:
        # Use cuda if available, else cpu (will be ~5x slower but still works)
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _BEAT_THIS_PREDICTOR = File2Beats(checkpoint_path="final0", device=device, float16=(device == "cuda"))

    beats_arr, downbeats_arr = _BEAT_THIS_PREDICTOR(audio_path)
    beats_raw = [float(x) for x in beats_arr]
    downbeats = [float(x) for x in downbeats_arr]

    # Drop adjacent <50ms duplicates (same cleanup as Modal path)
    if len(beats_raw) >= 2:
        arr = np.asarray(beats_raw, dtype=float)
        diffs = np.diff(arr)
        valid_idx = np.concatenate(([True], diffs > 0.05))
        beats = arr[valid_idx].tolist()
    else:
        beats = beats_raw

    if not beats:
        return {"bpm": 0.0, "beats": [], "downbeats": downbeats,
                "tempo_curve": [], "beats_source": "beat_this", "beat_version": 1}

    arr = np.asarray(beats, dtype=float)
    diffs = np.diff(arr)
    diffs = diffs[diffs > 0.05]
    bpm = float(60.0 / float(np.median(diffs))) if len(diffs) > 0 else 0.0

    tempo_curve = []
    if len(diffs) > 0:
        half = max(1, 4 // 2)
        n = len(diffs)
        for i in range(n):
            lo = max(0, i - half)
            hi = min(n, i + half + 1)
            med = float(np.median(diffs[lo:hi]))
            if med > 0.05:
                tempo_curve.append({"t": float(arr[i]), "bpm": round(60.0 / med, 2)})

    # Ballad halving (matches VPS pipeline)
    bpm_correction_record = None
    try:
        from bpm_sanity import ballad_halving_check, apply_halving_to_beat_info
        bpm_corrected, bpm_meta = ballad_halving_check(bpm, audio_path, bpm_range=(130.0, 220.0))
        if bpm_meta.get("applied"):
            beat_info = {"tempo_curve": tempo_curve, "downbeats": downbeats}
            apply_halving_to_beat_info(beat_info)
            tempo_curve = beat_info["tempo_curve"]
            downbeats = beat_info["downbeats"]
            bpm = bpm_corrected
            bpm_correction_record = {
                "applied": True,
                "reason": bpm_meta["reason"],
                "onset_density": bpm_meta["onset_density"],
                "rms_cov": bpm_meta["rms_cov"],
                "original": bpm_meta["original"],
            }
    except Exception as e:
        print(f"  [warn] ballad_halving_check skipped: {e}")

    # Snap chord boundaries to the beat grid (matches analyze_and_snap_dynamic behavior)
    n_snapped = 0
    try:
        from beat_snap import _snap_to_grid
        beat_arr = np.sort(np.asarray(beats, dtype=np.float64))
        n_snapped = _snap_to_grid(chords, beat_arr)
    except Exception as e:
        print(f"  [warn] _snap_to_grid skipped: {e}")

    return {
        "bpm": round(bpm, 2),
        "n_snapped": n_snapped,
        "beats": [round(t, 4) for t in beats],
        "downbeats": downbeats,
        "tempo_curve": tempo_curve,
        "beats_source": "beat_this",
        "beat_version": 1,
        "bpm_correction": bpm_correction_record,
    }


def _probe_audio_duration(audio_path: str) -> float:
    """Mirror process_queue._probe_audio_duration — mutagen first, librosa fallback."""
    try:
        from mutagen import File
        f = File(audio_path)
        if f and f.info and f.info.length:
            return float(f.info.length)
    except Exception:
        pass
    try:
        import librosa
        y, sr = librosa.load(audio_path, sr=None, mono=True)
        return float(len(y) / sr)
    except Exception:
        return 0.0


# Per-category 3-stop gradient palettes for the programmatic cover generator.
# Each entry is (top, mid, bottom). Picked so each category reads as visually
# distinct in a horizontal row of 3-6 cards.
_CATEGORY_PALETTES = {
    "classical": ((15, 25, 60),   (35, 45, 95),   (80, 50, 70)),    # midnight blue -> indigo -> sepia (Chopin palette)
    "folk":      ((25, 60, 30),   (60, 110, 55),  (140, 100, 50)),  # forest green -> moss -> warm brown
    "jazz":      ((210, 90, 50),  (230, 140, 80), (180, 80, 95)),   # amber -> sunset -> wine (existing Carefree palette)
    "easy":      ((180, 220, 200),(200, 200, 230),(245, 235, 215)), # mint -> lavender -> cream (light, friendly)
}


def _generate_category_cover(track: dict, cover_path: Path) -> None:
    """Paint a 600×600 cover for a track that has no embedded ID3 art.
    Gradient + soft glow + work title + artist line, with per-category color
    palette so cards in the same row look like a coherent set."""
    from PIL import Image, ImageDraw, ImageFont
    cat = track.get("category", "easy")
    top_c, mid_c, bot_c = _CATEGORY_PALETTES.get(cat, _CATEGORY_PALETTES["easy"])

    img = Image.new("RGB", (600, 600))
    px = img.load()
    def lerp(a, b, t):
        return tuple(int(a[i] + (b[i]-a[i]) * t) for i in range(3))
    for y in range(600):
        c = lerp(top_c, mid_c, y / 300) if y < 300 else lerp(mid_c, bot_c, (y - 300) / 300)
        for x in range(600):
            px[x, y] = c

    # Soft glow vignette — color depends on palette warmth.
    glow_color = (255, 240, 200, 16) if cat != "easy" else (255, 255, 255, 30)
    for r, alpha in [(450, 16), (320, 14), (220, 12)]:
        overlay = Image.new("RGBA", (600, 600), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.ellipse((300-r, 300-r, 300+r, 300+r),
                   fill=(glow_color[0], glow_color[1], glow_color[2], alpha))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    def find_font(size):
        for p in ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
                  "/Library/Fonts/Arial Bold.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
            try: return ImageFont.truetype(p, size)
            except Exception: continue
        return ImageFont.load_default()

    draw = ImageDraw.Draw(img)
    title = track.get("title", "")
    artist = track.get("artist", "")

    # Light category gets dark text for contrast; everything else gets white.
    text_color = (40, 40, 60) if cat == "easy" else (255, 255, 255)
    stroke_color = (255, 255, 255) if cat == "easy" else (20, 20, 40)
    sub_color = (90, 90, 110) if cat == "easy" else (220, 220, 240)

    # Title — auto-shrink to fit 540px wide, max 2 lines.
    title_size = 64
    while title_size > 28:
        title_font = find_font(title_size)
        tb = draw.textbbox((0, 0), title, font=title_font)
        if tb[2] - tb[0] <= 540:
            break
        title_size -= 4
    title_font = find_font(title_size)
    tb = draw.textbbox((0, 0), title, font=title_font)
    tw = tb[2] - tb[0]
    draw.text(((600 - tw) // 2, 230), title, font=title_font, fill=text_color,
              stroke_width=2, stroke_fill=stroke_color)

    artist_font = find_font(26)
    ab = draw.textbbox((0, 0), artist, font=artist_font)
    aw = ab[2] - ab[0]
    if aw > 540:  # truncate
        while aw > 540 and len(artist) > 10:
            artist = artist[:-2]
            ab = draw.textbbox((0, 0), artist + "…", font=artist_font)
            aw = ab[2] - ab[0]
        artist = artist + "…"
    draw.text(((600 - aw) // 2, 330), artist, font=artist_font, fill=sub_color)

    DEMO_COVERS_DIR.mkdir(parents=True, exist_ok=True)
    img.save(cover_path, "JPEG", quality=88, optimize=True)
    print(f"  [cover] generated {cat} cover -> {cover_path.name}")


def _ensure_cover(track: dict, audio_path: Path) -> Path | None:
    """Make sure data/demo/covers/<id>.jpg exists; extract from ID3 APIC if
    embedded, else generate a category-themed gradient cover."""
    cover_path = DEMO_COVERS_DIR / f"{track['id']}.jpg"
    if cover_path.is_file():
        return cover_path

    # Try ID3 embedded
    try:
        from mutagen import File
        from PIL import Image
        f = File(str(audio_path))
        apic = next((f.tags[k] for k in f.tags.keys() if k.startswith("APIC")), None) if f and f.tags else None
        if apic:
            DEMO_COVERS_DIR.mkdir(parents=True, exist_ok=True)
            im = Image.open(BytesIO(apic.data)).convert("RGB")
            w, h = im.size
            side = min(w, h)
            im = im.crop(((w-side)//2, (h-side)//2, (w-side)//2 + side, (h-side)//2 + side))
            im = im.resize((600, 600), Image.LANCZOS)
            im.save(cover_path, "JPEG", quality=88, optimize=True)
            print(f"  [cover] extracted from ID3 APIC -> {cover_path.name}")
            return cover_path
    except Exception as e:
        print(f"  [warn] cover extraction failed: {e}")

    # Fallback: programmatic category-themed cover
    try:
        _generate_category_cover(track, cover_path)
        return cover_path
    except Exception as e:
        print(f"  [warn] cover generation failed: {e}")

    return None


def analyze_track(track: dict) -> dict | None:
    """Run BTC + beat tracking on one demo MP3, return the chord-JSON dict
    (does not write — caller decides path)."""
    audio_path = DEMO_AUDIO_DIR / f"{track['id']}.mp3"
    if not audio_path.is_file():
        print(f"  [SKIP] audio missing: {audio_path}")
        return None

    print(f"  [btc ] running chord detection…")
    t0 = time.time()
    from chord_detect import detect_chords_and_key_isolated
    chords, key = detect_chords_and_key_isolated(str(audio_path))
    print(f"  [btc ] done in {time.time()-t0:.1f}s — {len(chords)} chords, key={key}")

    print(f"  [beat] running beat_this (matches VPS Modal output)…")
    t0 = time.time()
    beats_info = _beat_this_local(str(audio_path), chords)
    print(f"  [beat] done in {time.time()-t0:.1f}s — bpm={beats_info.get('bpm')}, "
          f"source={beats_info.get('beats_source')}, "
          f"beats={len(beats_info.get('beats', []))}, "
          f"downbeats={len(beats_info.get('downbeats', []))}")

    duration = _probe_audio_duration(str(audio_path))
    new_hash = demo_hash(track["id"])

    # Match the chord JSON shape produced by process_queue._save_chord_json,
    # plus demo-only fields the player.js hash-mode branch reads.
    cdata = {
        "path": f"__demo/{track['id']}",
        "key": key,
        "capo": 0,
        "source": "demo",
        "title": track["title"],
        "artist": track["artist"],
        "license": track["license"],
        "license_url": track["license_url"],
        "source_url": track["source_url"],
        "category": track.get("category", "easy"),
        "demo_audio_url": f"/static/demo/{track['id']}.mp3",
        "chords": chords,
        "duration": duration,
    }
    # beat fields
    for k in ("bpm", "beats", "downbeats", "tempo_curve", "beats_source",
              "beat_version", "n_snapped", "bpm_correction"):
        if k in beats_info:
            cdata[k] = beats_info[k]

    # Per-track override: lets us hand-correct cases where beat_this got the
    # meter / tempo wrong (e.g. compound-triple Nocturne pulse-tracked as
    # straight 4/4). Rebuilds downbeats[] from beats[] taking every Nth and
    # overrides bpm so the player displays the user-correct value.
    bpm_ovr = track.get("bpm_override")
    bpb_ovr = track.get("beats_per_bar_override")
    if bpm_ovr or bpb_ovr:
        if bpm_ovr:
            cdata["bpm_original_detected"] = cdata.get("bpm")
            cdata["bpm"] = float(bpm_ovr)
        if bpb_ovr and cdata.get("beats") and cdata.get("downbeats"):
            beats_arr = cdata["beats"]
            first_db = cdata["downbeats"][0]
            anchor = min(range(len(beats_arr)), key=lambda i: abs(beats_arr[i] - first_db))
            cdata["downbeats"] = beats_arr[anchor::int(bpb_ovr)]
        cdata["bar_arbitrator_override"] = {
            "applied": True,
            "beats_per_bar": bpb_ovr,
            "bpm_set": bpm_ovr,
            "reason": "manual override in scripts/build_demo.py TRACKS",
        }
        print(f"  [ovr ] bpm={cdata['bpm']} beats_per_bar={bpb_ovr} "
              f"(downbeats now {len(cdata.get('downbeats',[]))})")

    # Pre-apply bar-aware split so demo cards are bar-sized on first load
    # (no need for the user to open the 自動切分 panel). Mirrors the
    # frontend "依小節切分" mode in player.js _showAutoSplitPanel.
    n_split = _apply_bar_split(cdata)
    print(f"  [bar ] split {n_split} long chords into bar-sized cards "
          f"(now {len(cdata['chords'])} total)")

    DEMO_CHORDS_DIR.mkdir(parents=True, exist_ok=True)
    out = DEMO_CHORDS_DIR / f"{new_hash}.json"
    out.write_text(json.dumps(cdata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [json] {out.relative_to(REPO_ROOT)}  (hash={new_hash})")

    return {"hash": new_hash, "chord_json": out}


def extract_melody(track: dict, demo_hash_str: str) -> Path | None:
    audio_path = DEMO_AUDIO_DIR / f"{track['id']}.mp3"
    if not audio_path.is_file():
        return None
    print(f"  [mel ] running melody extraction (~30-60s)…")
    t0 = time.time()
    try:
        from ai.melody_extractor import MelodyExtractor
        melody = MelodyExtractor().extract_melody(str(audio_path))
    except Exception as e:
        print(f"  [warn] melody failed: {e}")
        return None
    if not melody:
        print(f"  [warn] melody empty")
        return None
    MELODIES_DIR.mkdir(parents=True, exist_ok=True)
    out = MELODIES_DIR / f"{demo_hash_str}.json"
    out.write_text(
        json.dumps({"path": f"__demo/{track['id']}", "melody": melody}, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"  [mel ] done in {time.time()-t0:.1f}s — {len(melody)} notes -> {out.relative_to(REPO_ROOT)}")
    return out


def build_manifest():
    entries = []
    for track in TRACKS:
        h = demo_hash(track["id"])
        chord_path = DEMO_CHORDS_DIR / f"{h}.json"
        cover_disk = DEMO_COVERS_DIR / f"{track['id']}.jpg"
        if not chord_path.is_file():
            print(f"  [warn] chord JSON missing for {track['id']}, skipping manifest entry")
            continue
        entries.append({
            "id": track["id"],
            "title": track["title"],
            "artist": track["artist"],
            "license": track["license"],
            "license_url": track["license_url"],
            "source_url": track["source_url"],
            "vibe": track["vibe"],
            "category": track.get("category", "easy"),
            "hash": h,
            "audio_url": f"/static/demo/{track['id']}.mp3",
            "cover_url": f"/static/demo/covers/{track['id']}.jpg" if cover_disk.is_file() else "",
        })
    MANIFEST_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[manifest] {MANIFEST_FILE.relative_to(REPO_ROOT)} written ({len(entries)} entries)")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--only", help="Only process this track id (debug)")
    p.add_argument("--skip-melody", action="store_true",
                   help="Skip melody extraction (faster, no waterfall RH on demos)")
    p.add_argument("--manifest-only", action="store_true",
                   help="Skip analysis, just rebuild manifest.json from existing chord JSONs")
    args = p.parse_args()

    if args.manifest_only:
        build_manifest()
        return

    DEMO_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    DEMO_CHORDS_DIR.mkdir(parents=True, exist_ok=True)
    DEMO_COVERS_DIR.mkdir(parents=True, exist_ok=True)

    for track in TRACKS:
        if args.only and track["id"] != args.only:
            continue
        print(f"\n=== {track['title']} ({track['id']}) — {track['artist']} ===")

        audio_path = DEMO_AUDIO_DIR / f"{track['id']}.mp3"
        if not audio_path.is_file():
            print(f"  [SKIP] audio missing — drop {audio_path.name} into data/demo/ first")
            continue

        _ensure_cover(track, audio_path)

        result = analyze_track(track)
        if not result:
            continue

        if not args.skip_melody:
            extract_melody(track, result["hash"])

    build_manifest()
    print("\nDone. Next:")
    print("  git add -f data/demo/ data/melodies/<3 demo hashes>.json")
    print("  Verify locally, then commit + push + ssh livechord-vps git pull.")


if __name__ == "__main__":
    main()
