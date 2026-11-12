"""MusicXML → LiveChord note-event JSON (+ readable MIDI export).

Pure-stdlib parser (ElementTree + zipfile) for the OMR pipeline. OMR output is
noisy — voices whose durations don't add up, junk measure numbers, missing
tempo — so the parser favours robustness over notation completeness:

- Measure length = the furthest position any voice reached in that measure,
  maxed across parts **by measure index** (OMR measure numbers are unreliable).
  Pickup bars stay short, an over-full voice stretches its bar instead of
  bleeding into the next one, and parts never drift against each other.
- Times are computed in quarter notes first, then converted to seconds through
  a piecewise tempo map, so a tempo change mid-piece shifts only later notes.
- Tied notes are merged into one event (one keypress on the waterfall).
- Grace and cue notes are skipped (no metric duration).
- Repeats / voltas / D.S. are NOT expanded — the score plays straight through.

Output follows Note Event Schema v2 (see CLAUDE.md): canonical ``duration`` in
seconds, ``gate_ratio=1.0``, legacy ``start``/``end`` kept in sync, plus
``left_hand`` / ``right_hand`` lists shaped like ``accData`` so the waterfall
can consume them, and ``beats`` / ``downbeats`` / ``bars`` like a chord JSON.
"""

from __future__ import annotations

import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Union

SCHEMA_VERSION = 2
DEFAULT_BPM = 120.0
DEFAULT_VELOCITY = 0.7

_STEP_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_PC_NAME = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
# beat-unit → length in quarter notes (for <metronome>)
_UNIT_QUARTERS = {"whole": 4.0, "half": 2.0, "quarter": 1.0, "eighth": 0.5,
                  "16th": 0.25, "32nd": 0.125}


class ScoreParseError(ValueError):
    """The file is not a usable MusicXML score."""


# ---------------------------------------------------------------------------
# Raw (quarter-note domain) representation
# ---------------------------------------------------------------------------

@dataclass
class _RawNote:
    q: float            # onset, quarter notes from score start
    qdur: float
    midi: int
    part: int
    staff: int
    voice: str
    measure: int        # global measure index
    hand: str           # "rh" | "lh"


@dataclass
class _RawScore:
    title: str = ""
    parts: list = field(default_factory=list)          # [{id, name}]
    notes: list = field(default_factory=list)          # [_RawNote]
    measure_q: list = field(default_factory=list)      # [(q_start, q_len)]
    measure_numbers: list = field(default_factory=list)
    timesig: list = field(default_factory=list)        # [(measure_idx, num, den)]
    tempos: list = field(default_factory=list)         # [(q, quarter_bpm)]
    key: Optional[dict] = None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        el.tag = _local(el.tag)
    return root


def _read_root(path: Path) -> ET.Element:
    data: bytes
    if path.suffix.lower() == ".mxl" or zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            target = None
            if "META-INF/container.xml" in names:
                c = _strip_ns(ET.fromstring(zf.read("META-INF/container.xml")))
                rf = c.find(".//rootfile")
                if rf is not None:
                    target = rf.get("full-path")
            if not target:
                target = next((n for n in names if n.lower().endswith((".xml", ".musicxml"))
                               and not n.startswith("META-INF")), None)
            if not target:
                raise ScoreParseError("no MusicXML inside .mxl archive")
            data = zf.read(target)
    else:
        data = path.read_bytes()
    try:
        root = _strip_ns(ET.fromstring(data))
    except ET.ParseError as e:
        raise ScoreParseError(f"invalid XML: {e}") from e
    if root.tag == "score-timewise":
        raise ScoreParseError("score-timewise MusicXML is not supported")
    if root.tag != "score-partwise":
        raise ScoreParseError(f"not a MusicXML score (root <{root.tag}>)")
    return root


def _num(el: Optional[ET.Element], default: float = 0.0) -> float:
    if el is None or el.text is None:
        return default
    try:
        return float(el.text.strip())
    except ValueError:
        return default


def _metronome_bpm(met: ET.Element) -> Optional[float]:
    unit = (met.findtext("beat-unit") or "quarter").strip()
    per_min = _num(met.find("per-minute"), 0.0)
    if per_min <= 0 or unit not in _UNIT_QUARTERS:
        return None
    q = _UNIT_QUARTERS[unit] * (1.5 if met.find("beat-unit-dot") is not None else 1.0)
    return per_min * q


def _parse_raw(path: Path) -> _RawScore:
    root = _read_root(path)
    raw = _RawScore()
    raw.title = (root.findtext("work/work-title") or root.findtext("movement-title") or "").strip()

    part_names = {sp.get("id"): (sp.findtext("part-name") or "").strip()
                  for sp in root.findall("part-list/score-part")}
    parts = root.findall("part")
    if not parts:
        raise ScoreParseError("score has no <part>")

    # Pass 1: per part, collect notes with in-measure offsets + measure lengths.
    per_part = []   # list of list-of-measures; measure = dict
    for p_idx, part in enumerate(parts):
        raw.parts.append({"id": part.get("id") or f"P{p_idx + 1}",
                          "name": part_names.get(part.get("id"), "")})
        divisions = 1.0
        staves = 1
        clefs: dict[int, str] = {}
        measures = []
        open_ties: dict[tuple, dict] = {}
        for m_idx, meas in enumerate(part.findall("measure")):
            m = {"number": meas.get("number") or str(m_idx + 1), "notes": [],
                 "len": 0.0, "time": None, "tempos": [], "key": None}
            pos = 0.0          # quarters within the measure
            last_onset = 0.0
            for el in meas:
                tag = el.tag
                if tag == "attributes":
                    divisions = _num(el.find("divisions"), divisions) or divisions
                    staves = int(_num(el.find("staves"), staves)) or staves
                    for clef in el.findall("clef"):
                        clefs[int(clef.get("number") or 1)] = (clef.findtext("sign") or "G").strip()
                    t = el.find("time")
                    if t is not None and t.find("beats") is not None:
                        try:
                            num = int((t.findtext("beats") or "4").split("+")[0])
                            den = int(t.findtext("beat-type") or 4)
                            m["time"] = (num, den)
                        except ValueError:
                            pass
                    k = el.find("key")
                    if k is not None and k.find("fifths") is not None:
                        m["key"] = {"fifths": int(_num(k.find("fifths"))),
                                    "mode": (k.findtext("mode") or "major").strip()}
                elif tag == "backup":
                    pos = max(0.0, pos - _num(el.find("duration")) / divisions)
                elif tag == "forward":
                    pos += _num(el.find("duration")) / divisions
                    m["len"] = max(m["len"], pos)
                elif tag in ("direction", "sound"):
                    snd = el if tag == "sound" else el.find("sound")
                    bpm = None
                    if snd is not None and snd.get("tempo"):
                        try:
                            bpm = float(snd.get("tempo"))
                        except ValueError:
                            bpm = None
                    if bpm is None and tag == "direction":
                        met = el.find(".//metronome")
                        bpm = _metronome_bpm(met) if met is not None else None
                    if bpm and bpm > 0:
                        off = _num(el.find("offset")) / divisions if tag == "direction" else 0.0
                        m["tempos"].append((pos + off, bpm))
                elif tag == "note":
                    if el.find("grace") is not None or el.find("cue") is not None:
                        continue
                    dur = _num(el.find("duration")) / divisions
                    is_chord = el.find("chord") is not None
                    onset = last_onset if is_chord else pos
                    if not is_chord:
                        last_onset = pos
                        pos += dur
                        m["len"] = max(m["len"], pos)
                    pitch = el.find("pitch")
                    if pitch is None or el.find("rest") is not None or dur <= 0:
                        continue
                    step = (pitch.findtext("step") or "C").strip().upper()
                    midi = int(round((int(_num(pitch.find("octave"), 4)) + 1) * 12
                                     + _STEP_PC.get(step, 0) + _num(pitch.find("alter"))))
                    if not 0 <= midi <= 127:
                        continue
                    staff = int(_num(el.find("staff"), 1)) or 1
                    voice = (el.findtext("voice") or "1").strip()
                    ties = {t.get("type") for t in el.findall("tie")}
                    tkey = (staff, voice, midi)
                    if "stop" in ties and tkey in open_ties:
                        prev = open_ties[tkey]
                        prev["tie_extra"].append((m_idx, onset, dur))
                        if "start" not in ties:
                            open_ties.pop(tkey)
                        continue
                    if staves >= 2:
                        hand = "rh" if staff == 1 else "lh"
                    else:
                        hand = "lh" if clefs.get(staff, "G") == "F" else "rh"
                    note = {"onset": onset, "dur": dur, "midi": midi, "staff": staff,
                            "voice": voice, "hand": hand, "tie_extra": []}
                    m["notes"].append(note)
                    if "start" in ties:
                        open_ties[tkey] = note
            measures.append(m)
        per_part.append(measures)

    # Pass 2: canonical measure grid = max length across parts per index.
    n_meas = max(len(ms) for ms in per_part)
    cur_ts = (4, 4)
    q = 0.0
    for i in range(n_meas):
        cands = [ms[i] for ms in per_part if i < len(ms)]
        ts = next((c["time"] for c in cands if c["time"]), None)
        if ts:
            cur_ts = ts
            raw.timesig.append((i, ts[0], ts[1]))
        if raw.key is None:
            raw.key = next((c["key"] for c in cands if c["key"]), None)
        nominal = cur_ts[0] * 4.0 / cur_ts[1]
        length = max((c["len"] for c in cands), default=0.0) or nominal
        raw.measure_q.append((q, length))
        raw.measure_numbers.append(cands[0]["number"])
        for c in cands:
            for off, bpm in c["tempos"]:
                raw.tempos.append((q + off, bpm))
        q += length
    if not raw.timesig:
        raw.timesig.append((0, 4, 4))

    for p_idx, ms in enumerate(per_part):
        for i, m in enumerate(ms):
            mq = raw.measure_q[i][0]
            for nt in m["notes"]:
                qdur = nt["dur"] + sum(d for _, _, d in nt["tie_extra"])
                raw.notes.append(_RawNote(q=mq + nt["onset"], qdur=qdur, midi=nt["midi"],
                                          part=p_idx, staff=nt["staff"], voice=nt["voice"],
                                          measure=i, hand=nt["hand"]))

    # Dedupe tempo marks at the same position (multiple parts repeat them).
    seen = {}
    for tq, bpm in raw.tempos:
        seen.setdefault(round(tq, 6), bpm)
    raw.tempos = sorted(seen.items())
    return raw


def _concat(raws: list[_RawScore]) -> _RawScore:
    """Join page-level scores end to end (multi-page PDF → one timeline).

    Parts are matched by index (OMR re-detects the same instruments per page).
    A page without its own tempo or time signature inherits the previous one.
    """
    out = _RawScore(title=next((r.title for r in raws if r.title), ""),
                    key=next((r.key for r in raws if r.key), None))
    out.parts = max((r.parts for r in raws), key=len, default=[])
    q_off, m_off = 0.0, 0
    for r in raws:
        out.measure_q += [(q + q_off, ln) for q, ln in r.measure_q]
        out.measure_numbers += r.measure_numbers
        for mi, num, den in r.timesig:
            prev = out.timesig[-1][1:] if out.timesig else None
            if prev == (num, den):
                continue
            # Continuation pages usually print no time signature, and the OMR
            # engine then guesses one (homr reads a 6/8 page 2 as 3/4). A guess
            # with the same bar length as the running meter is not a real
            # change — keep the running meter. A different bar length still
            # counts (and a genuine 6/8→3/4 hemiola at a page turn is rare).
            if (mi == 0 and prev is not None
                    and abs(num * 4.0 / den - prev[0] * 4.0 / prev[1]) < 1e-9):
                continue
            out.timesig.append((mi + m_off, num, den))
        out.tempos += [(q + q_off, b) for q, b in r.tempos]
        for n in r.notes:
            out.notes.append(_RawNote(q=n.q + q_off, qdur=n.qdur, midi=n.midi, part=n.part,
                                      staff=n.staff, voice=n.voice, measure=n.measure + m_off,
                                      hand=n.hand))
        if r.measure_q:
            q_off += r.measure_q[-1][0] + r.measure_q[-1][1]
        m_off += len(r.measure_q)
    return out


def _drop_contradicted_meters(raw: _RawScore) -> None:
    """Drop time-signature changes that the bars themselves contradict.

    OMR engines invent time signatures (homr labelled a 6/8 page 2 as 2/4
    while every bar on it held 3 quarters). Bar lengths come from the notes,
    so they are the better witness: a change after the first is kept only if
    the median bar length of the span it governs equals its nominal length.
    """
    kept = raw.timesig[:1]
    for k, (mi, num, den) in enumerate(raw.timesig[1:], 1):
        end = raw.timesig[k + 1][0] if k + 1 < len(raw.timesig) else len(raw.measure_q)
        lens = sorted(ln for _, ln in raw.measure_q[mi:end])
        if not lens:
            continue
        median = lens[len(lens) // 2]
        if abs(median - num * 4.0 / den) < 1e-6 and kept[-1][1:] != (num, den):
            kept.append((mi, num, den))
    raw.timesig = kept


# ---------------------------------------------------------------------------
# Quarter → seconds and JSON build
# ---------------------------------------------------------------------------

class _TempoMap:
    def __init__(self, tempos: list[tuple[float, float]]):
        pts = [(q, b) for q, b in tempos if b > 0]
        if not pts or pts[0][0] > 1e-9:
            pts.insert(0, (0.0, pts[0][1] if pts else DEFAULT_BPM))
        self.points = pts
        self.sec_at = [0.0]
        for (q0, b0), (q1, _) in zip(pts, pts[1:]):
            self.sec_at.append(self.sec_at[-1] + (q1 - q0) * 60.0 / b0)

    def seconds(self, q: float) -> float:
        i = len(self.points) - 1
        while i > 0 and self.points[i][0] > q:
            i -= 1
        q0, bpm = self.points[i]
        return self.sec_at[i] + (q - q0) * 60.0 / bpm


def _r(x: float) -> float:
    return round(x, 4)


def _note_name(midi: int) -> str:
    return f"{_PC_NAME[midi % 12]}{midi // 12 - 1}"


def _build(raw: _RawScore, explicit_tempo: bool) -> dict:
    tm = _TempoMap(raw.tempos)
    ts_by_measure = {mi: (n, d) for mi, n, d in raw.timesig}

    measures, beats, cur = [], [], (4, 4)
    for i, (mq, ln) in enumerate(raw.measure_q):
        cur = ts_by_measure.get(i, cur)
        step = 4.0 / cur[1]
        k = 0
        while k * step < ln - 1e-6:
            beats.append(_r(tm.seconds(mq + k * step)))
            k += 1
        measures.append({"index": i, "number": raw.measure_numbers[i],
                         "time": _r(tm.seconds(mq)), "end": _r(tm.seconds(mq + ln)),
                         "quarter_start": _r(mq), "quarter_length": _r(ln),
                         "time_signature": f"{cur[0]}/{cur[1]}"})

    notes = []
    for n in sorted(raw.notes, key=lambda n: (n.q, n.midi)):
        t0, t1 = tm.seconds(n.q), tm.seconds(n.q + n.qdur)
        mq = raw.measure_q[n.measure][0]
        notes.append({
            "schema_version": SCHEMA_VERSION,
            "time": _r(t0), "duration": _r(t1 - t0),
            "start": _r(t0), "end": _r(t1),
            "pitch": n.midi, "midi": n.midi, "name": _note_name(n.midi),
            "velocity": DEFAULT_VELOCITY, "gate_ratio": 1.0,
            "voice_lane": f"{n.hand}_score",
            "hand": n.hand, "part": n.part, "staff": n.staff, "voice": n.voice,
            "measure": n.measure, "beat_in_measure": _r(n.q - mq),
            "quarter_time": _r(n.q), "quarter_duration": _r(n.qdur),
        })

    first_ts = raw.timesig[0]
    end_q = raw.measure_q[-1][0] + raw.measure_q[-1][1] if raw.measure_q else 0.0
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "musicxml",
        "title": raw.title,
        "parts": raw.parts,
        "key": raw.key,
        "bpm": _r(tm.points[0][1]),
        "tempo_source": "score" if explicit_tempo else "default",
        "tempo_map": [{"time": _r(tm.seconds(q)), "quarter": _r(q), "bpm": _r(b)}
                      for q, b in tm.points],
        "time_signature": f"{first_ts[1]}/{first_ts[2]}",
        "time_signatures": [{"measure": mi, "time": measures[mi]["time"] if mi < len(measures) else 0.0,
                             "time_signature": f"{n}/{d}"} for mi, n, d in raw.timesig],
        "duration": _r(tm.seconds(end_q)),
        "measures": measures,
        "bars": [m["time"] for m in measures],
        "downbeats": [m["time"] for m in measures],
        "beats": beats,
        "notes": notes,
        "right_hand": [n for n in notes if n["hand"] == "rh"],
        "left_hand": [n for n in notes if n["hand"] == "lh"],
        "_raw_tempos": tm.points,   # consumed by to_midi, stripped before serving
    }


def parse_musicxml(paths: Union[str, Path, Iterable[Union[str, Path]]]) -> dict:
    """Parse one MusicXML (or .mxl) file, or several pages joined in order."""
    if isinstance(paths, (str, Path)):
        paths = [paths]
    raws = [_parse_raw(Path(p)) for p in paths]
    if not raws:
        raise ScoreParseError("no input files")
    raw = raws[0] if len(raws) == 1 else _concat(raws)
    _drop_contradicted_meters(raw)
    if not raw.notes:
        raise ScoreParseError("score contains no pitched notes")
    score = _build(raw, explicit_tempo=bool(raw.tempos))
    return score


def public_view(score: dict) -> dict:
    """Score dict without internal fields, safe to JSON-serialize."""
    return {k: v for k, v in score.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Readable MIDI export (canonical durations; gate_ratio ignored by design)
# ---------------------------------------------------------------------------

def to_midi(score: dict, out_path: Union[str, Path], ticks_per_beat: int = 480) -> Path:
    """Write a type-1 MIDI file: track 0 = tempo/meter, then RH and LH tracks.

    Uses the quarter-note positions (exact) rather than seconds, so tempo
    changes are carried by set_tempo meta events like a notation export.
    """
    import mido

    mf = mido.MidiFile(type=1, ticks_per_beat=ticks_per_beat)
    meta = mido.MidiTrack()
    mf.tracks.append(meta)
    abs_meta = []
    for q, bpm in score.get("_raw_tempos") or [(0.0, score.get("bpm") or DEFAULT_BPM)]:
        abs_meta.append((round(q * ticks_per_beat), mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm))))
    for ts in score.get("time_signatures", []):
        m = score["measures"][ts["measure"]] if ts["measure"] < len(score["measures"]) else None
        num, den = (int(x) for x in ts["time_signature"].split("/"))
        tick = round((m["quarter_start"] if m else 0.0) * ticks_per_beat)
        abs_meta.append((tick, mido.MetaMessage("time_signature", numerator=num, denominator=den)))
    _append_abs(meta, abs_meta)

    for hand, channel, name in (("rh", 0, "Right hand"), ("lh", 1, "Left hand")):
        evs = [n for n in score["notes"] if n["hand"] == hand]
        if not evs:
            continue
        tr = mido.MidiTrack()
        tr.append(mido.MetaMessage("track_name", name=name))
        abs_ev = []
        for n in evs:
            on = round(n["quarter_time"] * ticks_per_beat)
            off = max(on + 1, round((n["quarter_time"] + n["quarter_duration"]) * ticks_per_beat))
            vel = max(1, min(127, round(n["velocity"] * 127)))
            abs_ev.append((on, mido.Message("note_on", note=n["midi"], velocity=vel, channel=channel)))
            abs_ev.append((off, mido.Message("note_off", note=n["midi"], velocity=0, channel=channel)))
        # note_off before note_on at the same tick so repeated pitches retrigger
        abs_ev.sort(key=lambda e: (e[0], e[1].type == "note_on"))
        _append_abs(tr, abs_ev, presorted=True)
        mf.tracks.append(tr)

    out_path = Path(out_path)
    mf.save(str(out_path))
    return out_path


def _append_abs(track, abs_events, presorted: bool = False):
    if not presorted:
        abs_events = sorted(abs_events, key=lambda e: e[0])
    last = 0
    for tick, msg in abs_events:
        track.append(msg.copy(time=tick - last))
        last = tick
