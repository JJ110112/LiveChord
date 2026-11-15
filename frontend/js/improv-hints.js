/* Improvisation hints (player Tools → 即興提示).
 *
 * Pure functions, no DOM: player.js feeds them the displayed chords, the
 * melody and the key, then draws the result as hollow rings on the 88 keys
 * and a "/B" slash on the chord cards.
 *
 *  1. Phrase-end harmony — on a long melody note (where a singer would hold
 *     and a choir would join), the two nearest chord tones below the melody:
 *     a third / sixth / fourth / fifth under it, never a second (clashes).
 *  3. Bass line — for each chord, the chord tone (root / 3rd / 5th) closest
 *     to the previous bass, so the left hand walks by step instead of
 *     jumping (C → G/B → Am → F), plus a passing / approach note on the
 *     last beat before a bass move of a whole step or more.
 *
 * Fast passages get no harmony hint on purpose: only notes long enough to
 * plan a second voice qualify.
 */
(function (root) {
  "use strict";

  const BASS_LO = 36;     // C2
  const BASS_HI = 57;     // A3
  const BASS_HOME = 43;   // G2 — where the first bass note lands

  const MAJOR = [0, 2, 4, 5, 7, 9, 11];
  const MINOR = [0, 2, 3, 5, 7, 8, 10];

  const _NOTE = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };

  function pcOf(name) {
    if (!name) return null;
    const m = /^([A-Ga-g])([#b]*)/.exec(String(name).trim());
    if (!m) return null;
    let s = _NOTE[m[1].toUpperCase()];
    for (const ch of m[2]) s += ch === "#" ? 1 : -1;
    return ((s % 12) + 12) % 12;
  }

  /** Root pitch class of a chord symbol ("F#m7" → 6, "G/B" → 7). */
  function chordRootPc(chord) {
    return pcOf(chord);
  }

  /** Bass pitch class written after a slash ("G/B" → 11), else null. */
  function slashBassPc(chord) {
    const i = String(chord || "").lastIndexOf("/");
    return i > 0 ? pcOf(chord.slice(i + 1)) : null;
  }

  /** Scale pitch classes for a key like "C", "Am", "F#m" (mode overrides). */
  function scalePcs(key, mode) {
    const tonic = pcOf(key);
    if (tonic == null) return null;
    const minor = mode ? /^(minor|aeolian)$/i.test(mode) : /m(?!aj)/.test(String(key).slice(1));
    return (minor ? MINOR : MAJOR).map((i) => (tonic + i) % 12);
  }

  function _nearest(pc, ref, lo, hi) {
    let best = null;
    for (let m = lo; m <= hi; m++) {
      if (m % 12 !== pc) continue;
      if (best == null || Math.abs(m - ref) < Math.abs(best - ref)) best = m;
    }
    return best;
  }

  /**
   * Smoothest bass line through the chords.
   * @param chords  [{chord, time, end}]
   * @param tonesOf name → pitch classes, root first (root, 3rd, 5th, …)
   * @returns [{midi, pc, slash}] — slash=true when the bass is not the root
   */
  function bassLine(chords, tonesOf) {
    const out = [];
    let prev = null;
    let prevChord = null;
    const list = chords || [];
    for (let i = 0; i < list.length; i++) {
      const c = list[i];
      const name = c && c.chord;
      const root = chordRootPc(name);
      const tones = (tonesOf(name) || []).filter((p) => p != null);
      if (root == null || name === "N") {
        out.push(null);
        continue;
      }
      const fixed = slashBassPc(name);
      let pick;
      if (fixed != null) {
        pick = { pc: fixed, midi: _nearest(fixed, prev == null ? BASS_HOME : prev, BASS_LO, BASS_HI) };
      } else if (prev == null || name === prevChord) {
        pick = { pc: root, midi: _nearest(root, prev == null ? BASS_HOME : prev, BASS_LO, BASS_HI) };
      } else {
        // root costs nothing, 3rd a little, 5th (6/4 chord) more; holding the
        // same bass under a new chord loses the root motion, so it pays too.
        // A root moving up a 4th (V→I, ii→V) is a cadence: keep it on the root.
        const prevRoot = chordRootPc(prevChord);
        // The song's last chord ends on its root too.
        const cadence = (prevRoot != null && (root - prevRoot + 12) % 12 === 5) || i === list.length - 1 ? 3 : 0;
        const cands = [[root, 0]];
        if (tones[1] != null && tones[1] !== root) cands.push([tones[1], 2 + cadence]);
        if (tones[2] != null && tones[2] !== root) cands.push([tones[2], 6 + cadence]);
        for (const [pc, penalty] of cands) {
          const midi = _nearest(pc, prev, BASS_LO, BASS_HI);
          if (midi == null) continue;
          // + a gentle pull back toward the home register so the line does
          // not creep to the edge and then lose the root to the range cap.
          const cost = Math.abs(midi - prev) + penalty + (midi === prev ? 2 : 0)
            + 0.15 * Math.abs(midi - BASS_HOME);
          if (!pick || cost < pick.cost) pick = { pc, midi, cost };
        }
      }
      if (!pick || pick.midi == null) {
        out.push(null);
        continue;
      }
      out.push({ midi: pick.midi, pc: pick.pc, slash: pick.pc !== root });
      prev = pick.midi;
      prevChord = name;
    }
    return out;
  }

  /**
   * Passing / approach note between two bass notes, or null when the move
   * is already a step. Whole step → the chromatic note between. Wider →
   * the scale note one step before the target, on the side we come from
   * (chromatic half step when the key is unknown).
   */
  function approachNote(cur, next, scale) {
    if (cur == null || next == null) return null;
    const d = next - cur;
    const dir = Math.sign(d);
    if (Math.abs(d) <= 1) return null;
    if (Math.abs(d) === 2) return cur + dir;
    if (scale) {
      for (let k = 1; k <= 2; k++) {
        const m = next - dir * k;
        if (scale.includes(((m % 12) + 12) % 12)) return m;
      }
    }
    return next - dir;
  }

  /**
   * Melody notes long enough to hang a harmony on: at least 1.5 beats, or
   * at least 1 beat with a breath (≥ half a beat) before the next note.
   * @param notes [{start, end, midi}] (sorted or not)
   */
  function phraseEndNotes(notes, beatSec) {
    const beat = Math.max(0.3, beatSec || 0.5);
    const sorted = (notes || [])
      .filter((n) => n && Number.isFinite(n.start) && Number.isFinite(n.end) && Number.isFinite(n.midi))
      .slice()
      .sort((a, b) => a.start - b.start);
    const out = [];
    for (let i = 0; i < sorted.length; i++) {
      const n = sorted[i];
      const dur = n.end - n.start;
      const next = sorted[i + 1];
      const gap = next ? next.start - n.end : Infinity;
      if (dur >= Math.max(1.5 * beat, 0.6) || (dur >= Math.max(beat, 0.4) && gap >= 0.5 * beat)) {
        out.push(n);
      }
    }
    return out;
  }

  const _INTERVAL_LABEL = { 3: "3", 4: "3", 5: "4", 6: "4", 7: "5", 8: "6", 9: "6", 10: "7", 11: "7" };

  /**
   * Up to `max` chord tones under the melody note, nearest first, skipping
   * anything a second away (it would rub) and the melody's own pitch class.
   * @returns [{midi, label}] label = interval below the melody ("3", "6", …)
   */
  function harmonyBelow(melMidi, chordPcs, max) {
    const out = [];
    const limit = max || 2;
    const melPc = ((melMidi % 12) + 12) % 12;
    for (let m = melMidi - 3; m >= melMidi - 12 && out.length < limit; m--) {
      const pc = ((m % 12) + 12) % 12;
      if (pc === melPc || !chordPcs.includes(pc)) continue;
      out.push({ midi: m, label: _INTERVAL_LABEL[melMidi - m] || "" });
    }
    return out;
  }

  /** Index of the chord sounding at time t (chords sorted by time), or -1. */
  function chordIndexAt(chords, t) {
    for (let i = (chords || []).length - 1; i >= 0; i--) {
      if (t >= chords[i].time) return i;
    }
    return -1;
  }

  const api = {
    pcOf, chordRootPc, slashBassPc, scalePcs,
    bassLine, approachNote, phraseEndNotes, harmonyBelow, chordIndexAt,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.ImprovHints = api;
})(typeof window !== "undefined" ? window : globalThis);
