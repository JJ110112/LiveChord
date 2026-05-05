/**
 * beat-sync.js — Frontend helpers for the dynamic-beat fields written by
 * backend/beat_snap.analyze_and_snap_dynamic.
 *
 * Mirrors backend/ai/beat_helpers.py — same lookup semantics so backend
 * and frontend agree on local BPM at any time t.
 *
 * Exposes window.BeatSync = {
 *   localBpmAt(tempoCurve, t, fallbackBpm)  → number
 *   beatDurationAt(tempoCurve, t, fallbackBpm) → number (seconds)
 *   nearestBeatIndex(beats, t)              → integer | -1
 *   tempoRange(tempoCurve)                   → {min, max, range, isRubato}
 *   createPLL({getNow, beats, alphaPerSec})  → {tick, lockedPhase, currentBpm, isLocked}
 * }
 *
 * The PLL is intentionally minimal — its only job is to map "audio time t"
 * to "phase within the nearest beat" so beat-dot illumination snaps onto
 * the actual detected beat moment rather than an interpolated grid.
 * Compensation is rate-limited (default 20ms/sec) so display stays smooth;
 * deviations > 200ms are treated as seek and re-locked instantly.
 */
(function () {
  "use strict";

  /* ---------- Tempo curve lookup ---------- */

  function localBpmAt(tempoCurve, t, fallbackBpm) {
    fallbackBpm = (typeof fallbackBpm === "number" && fallbackBpm > 0) ? fallbackBpm : 120;
    if (!tempoCurve || !tempoCurve.length) return fallbackBpm;

    const last = tempoCurve.length - 1;
    if (t <= tempoCurve[0].t) return Number(tempoCurve[0].bpm) || fallbackBpm;
    if (t >= tempoCurve[last].t) return Number(tempoCurve[last].bpm) || fallbackBpm;

    // Binary search for first index where tempoCurve[i].t >= t
    let lo = 0, hi = last;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (tempoCurve[mid].t < t) lo = mid + 1;
      else hi = mid;
    }
    const idx = lo;
    const t0 = tempoCurve[idx - 1].t, b0 = Number(tempoCurve[idx - 1].bpm);
    const t1 = tempoCurve[idx].t, b1 = Number(tempoCurve[idx].bpm);
    const span = t1 - t0;
    if (!(span > 0)) return b0 || fallbackBpm;
    const frac = (t - t0) / span;
    return b0 + (b1 - b0) * frac;
  }

  function beatDurationAt(tempoCurve, t, fallbackBpm) {
    const bpm = localBpmAt(tempoCurve, t, fallbackBpm);
    return bpm > 0 ? 60.0 / bpm : 60.0 / (fallbackBpm || 120);
  }

  /* ---------- Beat array lookup ---------- */

  function nearestBeatIndex(beats, t) {
    if (!beats || !beats.length) return -1;
    if (t <= beats[0]) return 0;
    if (t >= beats[beats.length - 1]) return beats.length - 1;
    let lo = 0, hi = beats.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (beats[mid] < t) lo = mid + 1;
      else hi = mid;
    }
    // lo is first index >= t; pick whichever of (lo-1, lo) is closer.
    if (lo > 0 && Math.abs(beats[lo - 1] - t) < Math.abs(beats[lo] - t)) return lo - 1;
    return lo;
  }

  /* ---------- Rubato detection (UI hint) ---------- */

  function tempoRange(tempoCurve) {
    if (!tempoCurve || !tempoCurve.length) {
      return { min: 0, max: 0, range: 0, isRubato: false };
    }
    let min = Infinity, max = -Infinity;
    for (const pt of tempoCurve) {
      const b = Number(pt.bpm);
      if (b < min) min = b;
      if (b > max) max = b;
    }
    const range = max - min;
    return { min, max, range, isRubato: range >= 10 };
  }

  /* ---------- Phase-locked loop ---------- */

  // Compensation rate cap: at most this much phase shift per second of audio
  // time. Keeps beat-dot animation smooth; user shouldn't perceive the lock.
  const _MAX_COMP_PER_SEC = 0.02;
  // Above this absolute deviation, treat as seek (snap rather than chase).
  const _SEEK_THRESHOLD = 0.20;

  /**
   * @param {object} opts
   * @param {Array<number>} opts.beats   sorted beat times in seconds
   * @param {number} [opts.fallbackBpm]  used when beats is empty
   * @returns {{
   *   tick: (t: number) => {phase: number, bpm: number, lockedTo: number, locked: boolean},
   *   reset: () => void,
   * }}
   *
   * `tick(t)` returns:
   *   phase  – seconds since the locked beat (0 = on the beat). Uses the
   *            chase-rate cap so it varies smoothly across frames.
   *   bpm    – inferred from the gap between the lock beat and its
   *            neighbour (so display number reacts to rubato).
   *   lockedTo – absolute time of the beat we're locked to.
   *   locked – false if `beats` was empty (no PLL behaviour, just falls
   *            back to t for downstream code to detect and use raw time).
   */
  function createPLL(opts) {
    const beats = (opts && opts.beats) || [];
    const fallbackBpm = (opts && opts.fallbackBpm) || 120;

    let _appliedShift = 0;   // accumulated correction (audio-time seconds)
    let _lastT = null;       // last input time, for delta-time math

    function reset() {
      _appliedShift = 0;
      _lastT = null;
    }

    function tick(t) {
      if (!beats.length) {
        return { phase: 0, bpm: fallbackBpm, lockedTo: t, locked: false };
      }
      const idx = nearestBeatIndex(beats, t);
      const lockedTo = beats[idx];
      const rawDelta = t - lockedTo;  // signed; ahead is positive

      // Seek detection — re-lock instantly without rate-cap chase.
      if (Math.abs(rawDelta) > _SEEK_THRESHOLD) {
        _appliedShift = 0;
        _lastT = t;
        const localBpm = _bpmFromBeats(beats, idx, fallbackBpm);
        return { phase: rawDelta, bpm: localBpm, lockedTo, locked: true };
      }

      // Rate-limited chase. Move _appliedShift toward rawDelta at a
      // capped speed proportional to the wall-clock advance since last tick.
      const dt = (_lastT == null) ? 0 : Math.max(0, t - _lastT);
      const target = rawDelta;
      const maxStep = _MAX_COMP_PER_SEC * dt;
      let shift = _appliedShift;
      if (Math.abs(target - shift) <= maxStep) shift = target;
      else shift += (target > shift ? maxStep : -maxStep);
      _appliedShift = shift;
      _lastT = t;

      const localBpm = _bpmFromBeats(beats, idx, fallbackBpm);
      return { phase: shift, bpm: localBpm, lockedTo, locked: true };
    }

    return { tick, reset };
  }

  function _bpmFromBeats(beats, idx, fallbackBpm) {
    // Use the gap between the locked beat and its neighbour. Pick the
    // forward-looking gap when possible (matches "speed I'm about to play")
    // and fall back to the backward gap at the end of the song.
    if (idx + 1 < beats.length) {
      const gap = beats[idx + 1] - beats[idx];
      if (gap > 0) return 60.0 / gap;
    }
    if (idx > 0) {
      const gap = beats[idx] - beats[idx - 1];
      if (gap > 0) return 60.0 / gap;
    }
    return fallbackBpm;
  }

  /* ---------- Public surface ---------- */

  window.BeatSync = {
    localBpmAt,
    beatDurationAt,
    nearestBeatIndex,
    tempoRange,
    createPLL,
  };
})();
