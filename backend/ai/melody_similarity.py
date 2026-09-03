"""Melody similarity for phrase labelling (旋律輔助分段).

Humans group phrases by melody: the same tune sung again is the same kind of
phrase, whatever the chords do. This module turns the extracted melody into
per-bar fingerprints and compares segments:

  * fingerprint  — 12 pitch samples per bar, relative to the bar's median pitch
                   (so a verse sung a minor third higher still matches), None
                   where the melody rests; bass intrusions are octave-folded
                   around the song median first.
  * bar sim      — 0.7 × pitch closeness (mean |Δ| in semitones, 4 st → 0)
                   + 0.3 × rest/note rhythm agreement.
  * segment sim  — mean bar sim over the aligned overlap, best of offsets −1/0/+1.

Measured on Libera "The Lark's Last Song" (noisy full-mix pYIN melody):
verse↔verse 0.80–0.91, verse↔chorus ≈ 0.4, unrelated bars ≈ 0.38 baseline.
"""
import bisect
import statistics
from typing import Dict, List, Optional, Sequence, Tuple

SAMPLES_PER_BAR = 12
MIN_FP_BARS_SHARE = 0.5   # need fingerprints on at least half the bars to use the signal


def _clean(notes: List[Dict]) -> List[Tuple[float, float, int]]:
    out = []
    vals = [int(n.get("midi") or n.get("pitch") or 0) for n in notes if (n.get("midi") or n.get("pitch"))]
    if not vals:
        return out
    med = statistics.median(vals)
    for n in notes:
        m = n.get("midi") or n.get("pitch")
        if not m:
            continue
        m = int(m)
        if m < 48 and float(n.get("confidence", 1.0)) < 0.03:
            continue
        while m > med + 12:
            m -= 12
        while m < med - 12:
            m += 12
        s = float(n.get("start", n.get("time", 0)))
        e = float(n.get("end", s + float(n.get("duration", 0))))
        if e > s:
            out.append((s, e, m))
    out.sort()
    return out


class MelodyBars:
    def __init__(self, notes: List[Dict], bars: Sequence[float]):
        self.bars = [float(b) for b in bars]
        self.notes = _clean(notes or [])
        self._starts = [s for s, _, _ in self.notes]
        self.fps: List[Optional[List[Optional[float]]]] = [
            self._fp(self.bars[i], self.bars[i + 1]) for i in range(len(self.bars) - 1)
        ]
        have = sum(1 for f in self.fps if f is not None)
        self.usable = bool(self.fps) and have / len(self.fps) >= MIN_FP_BARS_SHARE

    def _pitch_at(self, t: float) -> Optional[int]:
        k = bisect.bisect_right(self._starts, t) - 1
        while k >= 0 and k >= len(self.notes) - 8:  # small backward scan for overlapping notes
            k -= 0
            break
        for j in range(k, max(-1, k - 6), -1):
            s, e, m = self.notes[j]
            if s <= t < e:
                return m
        return None

    def _fp(self, b0: float, b1: float):
        xs = [self._pitch_at(b0 + (b1 - b0) * (k + 0.5) / SAMPLES_PER_BAR) for k in range(SAMPLES_PER_BAR)]
        vals = [x for x in xs if x is not None]
        if len(vals) < 3:
            return None
        med = statistics.median(vals)
        return [None if x is None else x - med for x in xs]

    def bar_index(self, t: float) -> int:
        return max(0, min(len(self.fps) - 1, bisect.bisect_right(self.bars, t + 0.01) - 1))

    @staticmethod
    def bar_sim(a, b) -> float:
        if a is None or b is None:
            return 0.0
        pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
        if len(pairs) < 4:
            return 0.0
        d = sum(abs(x - y) for x, y in pairs) / len(pairs)
        rhythm = sum(1 for x, y in zip(a, b) if (x is None) == (y is None)) / SAMPLES_PER_BAR
        return max(0.0, 1 - d / 4) * 0.7 + rhythm * 0.3

    def segment_sim(self, seg: Tuple[float, float], ref: Tuple[float, float]) -> float:
        """Aligned similarity of seg against ref (ref may be longer: best window)."""
        if not self.fps:
            return 0.0
        a0, a1 = self.bar_index(seg[0]), self.bar_index(seg[1])
        r0, r1 = self.bar_index(ref[0]), self.bar_index(ref[1])
        n = max(1, a1 - a0)
        best = 0.0
        for start in range(r0 - 1, max(r0, r1 - n) + 2):
            vals = []
            for k in range(n):
                i, j = a0 + k, start + k
                if 0 <= i < len(self.fps) and 0 <= j < len(self.fps):
                    vals.append(self.bar_sim(self.fps[i], self.fps[j]))
            if vals:
                best = max(best, sum(vals) / len(vals))
        return best

    def best_sim(self, seg: Tuple[float, float], refs: List[Tuple[float, float]]) -> float:
        return max((self.segment_sim(seg, r) for r in refs), default=0.0)

    def bar_best_sim(self, i: int, refs: List[Tuple[float, float]]) -> float:
        """Best similarity of bar i against any bar inside the reference spans."""
        if not (0 <= i < len(self.fps)) or self.fps[i] is None:
            return 0.0
        best = 0.0
        for a, b in refs:
            for j in range(self.bar_index(a), self.bar_index(b) + 1):
                if j != i:
                    best = max(best, self.bar_sim(self.fps[i], self.fps[j]))
        return best
