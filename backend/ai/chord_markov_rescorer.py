"""Markov-based chord-progression rescorer (post-BTC, pre-write).

Looks at adjacent BTC chord pairs and asks the existing chord_predictor
(``ai.markov.get_predictor``): "given prior chord X in key K, how likely
is BTC's predicted Y?". When Y is below ``threshold`` AND the predictor
strongly suggests an alternative Z that shares Y's ROOT but differs in
QUALITY, swap Y → Z. Root never changes (BTC root accuracy is decent;
quality is the weak axis).

**Honest caveat about expected lift**: the live Markov corpus
(``data/models/markov.json``) is trained on existing chord JSONs, which
are themselves BTC-derived. So its quality biases REINFORCE BTC's biases
— don't expect this rescorer to "automatically fix" systematic errors
(e.g. Vm7-vs-V7 in minor keys won't flip just by Markov vote). What this
DOES catch:

  - Severely impossible transitions (P < 1-2%) where BTC mis-rooted
    or mis-classified a chord into a non-key area
  - Cases where the corpus already shows a strong alternative quality
    by margin (rare for systematic errors, common for one-offs)

Phase 2 plan: once chord_corrections (Plan A) accumulates ~200+ songs of
human edits, retrain Markov on the corrected versions only. THAT version
of the corpus will have the right quality biases and this same rescorer
will start catching Gm7→G7 patterns automatically.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# Quality stripping: split a chord name like "Gm7" into ("G", "m7"). We
# only treat root as the leading [A-G][#b]? — everything after is "quality"
# (including extensions like 7, m7, maj7, sus2, dim, etc).
_ROOT_RE = re.compile(r"^([A-G][#b]?)(.*)$")


def _split_chord(name: str) -> tuple[Optional[str], str]:
    if not name:
        return None, ""
    m = _ROOT_RE.match(name)
    if not m:
        return None, name
    return m.group(1), m.group(2)


def _normalize_root(root: str) -> str:
    """Map enharmonic spellings (e.g. predictor returns A# for Bb root) to a
    single canonical form. Predictor uses sharps; chord JSONs may use either.
    """
    flat_to_sharp = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}
    return flat_to_sharp.get(root, root)


def rescore_chords(
    chords: list,
    key: str,
    predictor,
    *,
    threshold: float = 0.02,
    prob_margin: float = 3.0,
    top_k: int = 12,
) -> tuple[list, dict]:
    """Quality-only rescoring pass.

    Args:
        chords: ``[{"time", "end", "chord"}, ...]`` from BTC + post-processing.
        key: tonic letter (e.g. ``"C"``, ``"Cm"`` — `m` suffix is stripped).
        predictor: ``ai.markov.ChordPredictor`` instance.
        threshold: BTC-predicted chord's Markov P below this triggers consideration.
        prob_margin: alternative must beat the original by this factor (e.g. 3×).
        top_k: how many predictor suggestions to scan for an alternative.

    Returns:
        ``(rescored_chords, meta_dict)``. ``meta_dict``:
          ``{"applied": bool, "swaps_count": int,
             "swaps": [{"time", "from", "to", "p_from", "p_to", "context"}],
             "reason": str (when applied=False)}``
    """
    meta = {"applied": False, "swaps_count": 0, "swaps": []}

    if not chords or len(chords) < 2:
        meta["reason"] = "too-few-chords"
        return list(chords), meta
    if predictor is None:
        meta["reason"] = "no-predictor"
        return list(chords), meta

    key_letter = (key or "C").rstrip("m")  # major-mode tonic letter

    out = [dict(chords[0])]
    for i in range(1, len(chords)):
        prev = out[-1]
        cur = dict(chords[i])
        prev_name = prev.get("chord")
        cur_name = cur.get("chord")
        if not prev_name or not cur_name:
            out.append(cur)
            continue

        cur_root, cur_qual = _split_chord(cur_name)
        if cur_root is None:
            out.append(cur)
            continue

        try:
            suggestions = predictor.suggest([prev_name], key=key_letter, top_k=top_k)
        except Exception as e:
            logger.debug("predictor.suggest failed at %s -> %s: %s",
                         prev_name, cur_name, e)
            out.append(cur)
            continue

        # P(BTC's chord)
        cur_root_norm = _normalize_root(cur_root)
        p_cur = 0.0
        for s in suggestions:
            s_root, s_qual = _split_chord(s.get("chord", ""))
            if s_root and _normalize_root(s_root) == cur_root_norm and s_qual == cur_qual:
                p_cur = float(s.get("probability", 0.0))
                break

        if p_cur >= threshold:
            out.append(cur)
            continue

        # Look for a same-root, different-quality alternative with
        # probability >= prob_margin * p_cur AND >= threshold.
        best_alt = None
        best_alt_p = 0.0
        for s in suggestions:
            s_root, s_qual = _split_chord(s.get("chord", ""))
            if not s_root or _normalize_root(s_root) != cur_root_norm:
                continue
            if s_qual == cur_qual:
                continue
            s_p = float(s.get("probability", 0.0))
            if s_p < threshold:
                continue
            if s_p < prob_margin * max(p_cur, 1e-9):
                continue
            if s_p > best_alt_p:
                best_alt = s
                best_alt_p = s_p

        if best_alt is None:
            out.append(cur)
            continue

        # Apply swap — only the chord NAME mutates; time/end preserved.
        new_name = best_alt["chord"]
        # Preserve original root spelling style (sharp vs flat) by reusing
        # the cur_root's surface form; predictor returns sharps. If the
        # alternative root happens to differ via enharmonic (shouldn't —
        # we filtered to same root), the predictor's spelling wins.
        alt_root, alt_qual = _split_chord(new_name)
        if alt_root and _normalize_root(alt_root) == cur_root_norm:
            new_name = cur_root + alt_qual

        meta["swaps"].append({
            "time": round(float(cur.get("time", 0.0)), 3),
            "from": cur_name,
            "to": new_name,
            "p_from": round(p_cur, 4),
            "p_to": round(best_alt_p, 4),
            "context": prev_name,
        })
        cur["chord"] = new_name
        out.append(cur)

    meta["swaps_count"] = len(meta["swaps"])
    meta["applied"] = meta["swaps_count"] > 0
    if meta["swaps_count"]:
        logger.info("[markov_rescore] swapped %d chord(s) in key=%s",
                    meta["swaps_count"], key)
    return out, meta
