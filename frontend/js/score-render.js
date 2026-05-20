/** 樂譜渲染模組 — VexFlow grand-staff + 播放光帶座標.
 *
 * Caller owns three sibling elements inside #scoreLayer:
 *   <div id="scoreStage"></div>   ← we render VexFlow SVG inside here
 *   #scoreChordRow                ← caller paints chord-symbol spans
 *   #scoreCursor                  ← caller moves the cursor via timeToX()
 *
 * The Stage container is exclusively ours: we clear+rebuild on every render().
 * Lazy init — init() is only called the first time both gates evaluate true:
 *   - body.score-eligible (waterfall wrapper ≥ ~540 px AND non-overview)
 *   - localStorage.livechord_show_score === "true"
 *
 * destroy() is called when either gate flips off, so portrait phones and
 * collapsed states don't keep eating VexFlow memory.
 */

/* global Vex */

(function (global) {
  "use strict";

  // VexFlow 4.x UMD bundle exposes Vex.Flow.* (same namespace as 3.x).
  function _VF() {
    return (global.Vex && global.Vex.Flow) ? global.Vex.Flow : null;
  }

  // ------- Pitch helpers -------

  const _FLAT_KEYS = new Set(["F", "Bb", "Eb", "Ab", "Db", "Gb", "Cb"]);
  const _PC_TO_NAME_SHARP = ["c", "c#", "d", "d#", "e", "f", "f#", "g", "g#", "a", "a#", "b"];
  const _PC_TO_NAME_FLAT  = ["c", "db", "d", "eb", "e", "f", "gb", "g", "ab", "a", "bb", "b"];

  function _midiToVexKey(midi, key) {
    const pc = ((midi % 12) + 12) % 12;
    const oct = Math.floor(midi / 12) - 1; // MIDI 60 = C4 → "c/4"
    const tonic = String(key || "C").replace(/m$/i, "");
    const table = _FLAT_KEYS.has(tonic) ? _PC_TO_NAME_FLAT : _PC_TO_NAME_SHARP;
    return table[pc] + "/" + oct;
  }

  // ------- Duration / meter helpers -------
  // Internal beat math uses quarter-note equivalents. VexFlow receives the
  // actual meter denominator (e.g. 6/8 => Voice({ num_beats: 6, beat_value: 8 }))
  // while durations are decomposed into readable plain/dotted notes.

  const _EPS = 1e-6;
  const _DUR_BEATS = { w: 4, h: 2, q: 1, "8": 0.5, "16": 0.25, "32": 0.125 };
  const _DUR_TIERS = [
    ["w", 4],
    ["hd", 3],
    ["h", 2],
    ["qd", 1.5],
    ["q", 1],
    ["8d", 0.75],
    ["8", 0.5],
    ["16d", 0.375],
    ["16", 0.25],
    ["32", 0.125],
  ];

  function _parseTimeSig(timeSig) {
    const m = String(timeSig || "4/4").replace(/\s+/g, "").match(/^(\d+)\/(\d+)$/);
    const numerator = m ? Math.max(1, parseInt(m[1], 10) || 4) : 4;
    const denominator = m ? Math.max(1, parseInt(m[2], 10) || 4) : 4;
    return {
      numerator,
      denominator,
      quarterBeats: numerator * (4 / denominator),
    };
  }

  function _durToBeats(d) {
    const s = String(d || "");
    const base = s.replace(/r/g, "").replace(/\./g, "").replace(/d/g, "");
    let b = _DUR_BEATS[base] || 0;
    if (s.indexOf(".") >= 0 || s.indexOf("d") >= 0) b *= 1.5;
    return b;
  }

  function _quantizeBeats(secs, quarterSecs) {
    return Math.max(0.125, Math.round((secs / Math.max(0.001, quarterSecs)) * 8) / 8);
  }

  function _beatsToDurations(beats) {
    const out = [];
    let remain = Math.max(0, Math.round(beats * 8) / 8);
    for (const [dur, b] of _DUR_TIERS) {
      while (remain >= b - _EPS) {
        out.push({ dur, beats: b });
        remain -= b;
        remain = Math.round(remain * 1000) / 1000;
      }
    }
    if (out.length === 0 && beats > _EPS) out.push({ dur: "32", beats: 0.125 });
    return out;
  }

  function _beatsToRests(beats, makeRest) {
    const out = [];
    for (const spec of _beatsToDurations(beats)) out.push(makeRest(spec));
    return out;
  }

  function _eventTime(e) {
    const t = Number(e && (e.time != null ? e.time : e.start));
    return Number.isFinite(t) ? t : 0;
  }

  function _eventDuration(e, fallback) {
    const d = Number(e && e.duration);
    if (Number.isFinite(d) && d > 0) return d;
    const start = Number(e && (e.time != null ? e.time : e.start));
    const end = Number(e && e.end);
    if (Number.isFinite(start) && Number.isFinite(end) && end > start) return end - start;
    return fallback;
  }

  // ------- ScoreRender public API -------

  const ScoreRender = {
    _host: null,        // the stage <div>; VexFlow renders <svg> inside this
    _renderer: null,
    _context: null,
    _barLayout: null,
    _rangeStart: 0,
    _rangeEnd: 0,
    _ready: false,

    /** Lazy init. `stageEl` is an empty <div> we own end-to-end. */
    init(stageEl, { width, height }) {
      const VF = _VF();
      if (!VF || !stageEl) return false;
      this.destroy();
      this._host = stageEl;
      this._host.innerHTML = "";
      try {
        this._renderer = new VF.Renderer(this._host, VF.Renderer.Backends.SVG);
        this._renderer.resize(Math.max(200, width | 0), Math.max(80, height | 0));
        this._context = this._renderer.getContext();
      } catch (e) {
        console.warn("[score-render] init failed", e);
        return false;
      }
      this._ready = true;
      return true;
    },

    /** Render a single window of measures. */
    render(opts) {
      const VF = _VF();
      if (!VF || !this._ready || !this._host) return;
      this._opts = opts || {};
      const { timeSig = "4/4", key = "C", bpm = 120, timeRange, barLines = [], lhNotes = [], rhNotes = [] } = this._opts;

      // Recreate renderer + svg from scratch each render — VexFlow doesn't
      // expose a "clear" primitive and our pages re-flow on every page turn.
      this._host.innerHTML = "";
      const width = this._host.clientWidth || 600;
      const height = this._host.clientHeight || 160;
      try {
        this._renderer = new VF.Renderer(this._host, VF.Renderer.Backends.SVG);
        this._renderer.resize(width, height);
        this._context = this._renderer.getContext();
      } catch (e) {
        console.warn("[score-render] renderer rebuild failed", e);
        return;
      }
      const ctx = this._context;

      const meter = _parseTimeSig(timeSig);
      const quarterSecs = 60 / Math.max(40, bpm);
      const fallbackBarSecs = meter.quarterBeats * quarterSecs;

      const range = timeRange || { start: 0, end: 4 * fallbackBarSecs };
      const barWindows = this._barWindows(range, barLines, fallbackBarSecs);
      const numBars = barWindows.length;

      // leftPad needs to clear the treble clef's flourish (it extends ~6 px
      // to the left of the staff line); rightPad keeps the last bar's notes
      // from butting up against the pedal-sustain indicator below the
      // waterfall edge — and absorbs VexFlow Formatter's natural-width
      // overshoot on dense eighth/sixteenth bars (stems + flags can stick
      // ~12 px past the last notehead).
      const leftPad = 18;
      const rightPad = 40;
      // First bar carries clef + key signature + time signature. Real width
      // depends on the key (D major = 2 sharps, A♭ = 4 flats etc), but
      // ~96 px covers typical pop keys without squeezing bar 1's music area
      // noticeably narrower than bar 2+ (which has only ~5 px left padding).
      const firstBarExtra = 96;
      const totalUsable = Math.max(200, width - leftPad - rightPad);
      const restBarW = Math.max(60, (totalUsable - firstBarExtra) / numBars);

      // CRITICAL: VexFlow's Stave(x, y, w) treats `y` as the TOP of the
      // stave's *visual box* (which includes 40 px above-staff headroom for
      // high ledger notes), NOT the top staff line itself. The bottom staff
      // line sits at y + 80, and ledger notes BELOW (D2, C2, B1, A1) extend
      // further. Measured y offsets from stave.y for the bass clef:
      //   G2 (bottom line)        → +80
      //   E2 (1st ledger below)   → +90
      //   D2                      → +95
      //   C2 (2nd ledger below)   → +100   ← + 12 px more for ♯/♭ glyph
      //   A1 (3rd ledger below)   → +110   ← + 12 px more for ♯/♭ glyph
      // Sharps/flats are centered vertically on the notehead but extend
      // ~12 px above AND below it (the glyph is ~25 px tall). So an
      // accidental on C2 reaches stave.y + 112; on A1, stave.y + 122.
      // Reserved bottom = 135 to fit C#2 / Cb2 / A#1 / Ab1 with margin.
      const topY = 4;
      const bottomY = Math.max(94, height - 135);

      const trebleStaves = [];
      const bassStaves = [];

      let x = leftPad;
      this._barLayout = [];
      for (let i = 0; i < numBars; i++) {
        const w = (i === 0 ? firstBarExtra : 0) + restBarW;
        const treble = new VF.Stave(x, topY, w);
        const bass = new VF.Stave(x, bottomY, w);
        if (i === 0) {
          try {
            treble.addClef("treble").addKeySignature(key).addTimeSignature(timeSig);
            bass.addClef("bass").addKeySignature(key).addTimeSignature(timeSig);
          } catch (_) { /* ignore bad key */ }
        }
        try {
          treble.setContext(ctx).draw();
          bass.setContext(ctx).draw();
        } catch (e) { console.warn("[score-render] stave draw", e); }
        trebleStaves.push(treble);
        bassStaves.push(bass);
        // xStart MUST be the start of the music area, NOT stave.getX(). For
        // bar 1 that's after clef + key + time (~64–100 px past getX()); for
        // bar 2+ it's ~5 px past getX(). Without this, cursor at t=tStart
        // sits BEFORE the first notehead — looks like score lags playback.
        const musicStartX = (typeof treble.getNoteStartX === "function")
          ? treble.getNoteStartX() : x;
        this._barLayout.push({
          xStart: musicStartX,
          xEnd: x + w,
          tStart: barWindows[i].start,
          tEnd: barWindows[i].end,
        });
        x += w;
      }

      // Brace + left line on the first bar — grand-staff visual cue.
      try {
        new VF.StaveConnector(trebleStaves[0], bassStaves[0])
          .setType(VF.StaveConnector.type.BRACE).setContext(ctx).draw();
        new VF.StaveConnector(trebleStaves[0], bassStaves[0])
          .setType(VF.StaveConnector.type.SINGLE_LEFT).setContext(ctx).draw();
      } catch (_) {}

      // Notes — per bar Voice with Padding Patch
      this._pendingTies = {};
      for (let i = 0; i < numBars; i++) {
        const barStart = barWindows[i].start;
        const barEnd = barWindows[i].end;
        const localQuarterSecs = Math.max(0.001, (barEnd - barStart) / meter.quarterBeats);
        const rhVexNotes = this._buildBarNotesV2(rhNotes, barStart, barEnd, localQuarterSecs, meter, key, "treble");
        const lhVexNotes = this._buildBarNotesV2(lhNotes, barStart, barEnd, localQuarterSecs, meter, key, "bass");
        this._drawVoice(rhVexNotes, trebleStaves[i], meter);
        this._drawVoice(lhVexNotes, bassStaves[i], meter);
      }
      this._pendingTies = {};

      this._rangeStart = range.start;
      this._rangeEnd = range.end;
    },

    _barWindows(range, barLines, fallbackBarSecs) {
      const start = Number(range && range.start) || 0;
      const end = Number(range && range.end) || (start + fallbackBarSecs);
      const points = [start, end];
      for (const line of barLines || []) {
        const t = Number(line);
        if (Number.isFinite(t) && t > start + 0.01 && t < end - 0.01) points.push(t);
      }
      points.sort((a, b) => a - b);
      const unique = [];
      for (const p of points) {
        if (!unique.length || Math.abs(p - unique[unique.length - 1]) > 0.01) unique.push(p);
      }
      // Use explicit downbeats only when at least one usable bar line sits
      // inside the current page. Empty downbeats must fall back to the
      // uniform meter grid instead of turning the whole page into one bar.
      if (unique.length > 2) {
        const windows = [];
        for (let i = 0; i < unique.length - 1; i++) {
          if (unique[i + 1] > unique[i] + 0.05) windows.push({ start: unique[i], end: unique[i + 1] });
        }
        if (windows.length) return windows;
      }

      const rangeSecs = Math.max(0.001, end - start);
      const numBars = Math.max(1, Math.round(rangeSecs / Math.max(0.001, fallbackBarSecs)));
      const barSecs = rangeSecs / numBars;
      const windows = [];
      for (let i = 0; i < numBars; i++) {
        windows.push({ start: start + i * barSecs, end: start + (i + 1) * barSecs });
      }
      return windows;
    },

    _buildBarNotesV2(events, barStart, barEnd, quarterSecs, meter, key, clef) {
      const VF = _VF();
      const pitchMin = clef === "bass" ? 33 : 53;
      const pitchMax = clef === "bass" ? 72 : 100;
      const fallbackDur = quarterSecs * 0.5;
      const inBar = [];
      for (const e of events || []) {
        const t = _eventTime(e);
        const p = Number(e && (e.pitch != null ? e.pitch : e.midi));
        if (!Number.isFinite(p) || p < pitchMin || p > pitchMax) continue;
        const dur = _eventDuration(e, fallbackDur);
        const end = t + dur;
        if (end <= barStart + 0.001 || t >= barEnd - 0.001) continue;
        inBar.push({
          time: Math.max(t, barStart),
          sourceTime: t,
          end,
          pitch: Math.round(p),
          crossesIn: t < barStart - 0.001,
          crossesOut: end > barEnd + 0.001,
        });
      }
      inBar.sort((a, b) => a.time - b.time || a.pitch - b.pitch);

      const makeRest = (spec) => {
        const sn = new VF.StaveNote({
          keys: clef === "treble" ? ["b/4"] : ["d/3"],
          duration: spec.dur + "r",
          clef,
        });
        sn._lcBeats = spec.beats;
        return sn;
      };

      const makeNote = (keys, spec, tieIn, tieOut) => {
        const sn = new VF.StaveNote({ keys, duration: spec.dur, clef });
        sn._lcBeats = spec.beats;
        sn._lcTieIn = tieIn || [];
        sn._lcTieOut = tieOut || [];
        keys.forEach((k, idx) => {
          const c = k.charAt(1);
          if (c === "#" || c === "b") {
            try { sn.addModifier(new VF.Accidental(c), idx); } catch (_) {}
          }
        });
        return sn;
      };

      const notes = [];
      let prevEnd = barStart;
      let cursor = 0;
      while (cursor < inBar.length) {
        const t0 = inBar[cursor].time;
        const group = [];
        while (cursor < inBar.length && Math.abs(inBar[cursor].time - t0) < 0.03) {
          group.push(inBar[cursor]);
          cursor++;
        }
        if (t0 > prevEnd + 0.01) {
          notes.push(..._beatsToRests(_quantizeBeats(t0 - prevEnd, quarterSecs), makeRest));
        }

        const noteEndT = Math.min(barEnd, Math.max(...group.map(e => e.end)));
        const noteBeats = Math.min(
          meter.quarterBeats,
          _quantizeBeats(Math.max(quarterSecs * 0.125, noteEndT - t0), quarterSecs)
        );
        const pitchSet = new Set();
        const items = [];
        for (const g of group) {
          if (!pitchSet.has(g.pitch)) {
            pitchSet.add(g.pitch);
            items.push(g);
          }
        }
        items.sort((a, b) => a.pitch - b.pitch);
        const keys = items.map(item => _midiToVexKey(item.pitch, key));
        const durSpecs = _beatsToDurations(noteBeats);
        for (let i = 0; i < durSpecs.length; i++) {
          const tieIn = [];
          const tieOut = [];
          items.forEach((item, idx) => {
            const id = `${clef}:${item.pitch}:${item.sourceTime.toFixed(4)}:${item.end.toFixed(4)}`;
            if ((i === 0 && item.crossesIn) || i > 0) tieIn.push({ id, index: idx });
            if ((i === durSpecs.length - 1 && item.crossesOut) || i < durSpecs.length - 1) tieOut.push({ id, index: idx });
          });
          notes.push(keys.length ? makeNote(keys, durSpecs[i], tieIn, tieOut) : makeRest(durSpecs[i]));
        }
        prevEnd = Math.max(prevEnd, t0 + noteBeats * quarterSecs);
      }
      if (prevEnd < barEnd - 0.01) {
        notes.push(..._beatsToRests(_quantizeBeats(barEnd - prevEnd, quarterSecs), makeRest));
      }

      const beatsOf = (n) => Number(n._lcBeats) || _durToBeats(n.getDuration ? n.getDuration() : n.duration);
      let filled = notes.reduce((s, n) => s + beatsOf(n), 0);
      let diff = Math.round((meter.quarterBeats - filled) * 1000) / 1000;
      if (diff > 0.001) {
        notes.push(..._beatsToRests(diff, makeRest));
        filled = notes.reduce((s, n) => s + beatsOf(n), 0);
        diff = Math.round((meter.quarterBeats - filled) * 1000) / 1000;
      }
      if (Math.abs(diff) > 0.001) {
        return _beatsToRests(meter.quarterBeats, makeRest);
      }
      return notes;
    },

    _drawVoice(stavenotes, stave, meter) {
      const VF = _VF();
      if (!stavenotes || !stavenotes.length) return;
      try {
        const voice = new VF.Voice({
          num_beats: meter.numerator,
          beat_value: meter.denominator,
        });
        voice.setStrict(false);
        voice.addTickables(stavenotes);
        // Format width target — pack notes into music area minus 44 px
        // right buffer. The 44 px absorbs:
        //   ~10 px notehead radius
        //   ~12 px sharp/flat accidental glyph next to the rightmost note
        //   ~10 px eighth-note flag
        //   ~12 px breathing space before the bar line
        // Without this, dense eighth/sixteenth bars overflow the bar line
        // (the rightmost notehead + its accidental visually exceed the
        // staff's right edge). VexFlow's Formatter will pack tighter than
        // its "minimum" width if forced, sacrificing visual breathing
        // between notes — preferable to overflow.
        const stEnd = stave.getX() + stave.getWidth();
        const stStart = (typeof stave.getNoteStartX === "function") ? stave.getNoteStartX() : stave.getX();
        const musicW = Math.max(40, stEnd - stStart - 44);
        new VF.Formatter().joinVoices([voice]).format([voice], musicW);
        voice.draw(this._context, stave);
        this._drawTiesForNotes(stavenotes);
      } catch (e) {
        console.warn("[score-render] voice draw failed", e);
      }
    },

    _drawTiesForNotes(stavenotes) {
      const VF = _VF();
      if (!VF || !this._context) return;
      this._pendingTies = this._pendingTies || {};
      for (const sn of stavenotes || []) {
        for (const tie of (sn._lcTieIn || [])) {
          const from = this._pendingTies[tie.id];
          if (!from) continue;
          try {
            new VF.StaveTie({
              first_note: from.note,
              last_note: sn,
              first_indices: [from.index],
              last_indices: [tie.index],
            }).setContext(this._context).draw();
          } catch (e) {
            console.warn("[score-render] tie draw failed", e);
          }
        }
        for (const tie of (sn._lcTieOut || [])) {
          this._pendingTies[tie.id] = { note: sn, index: tie.index };
        }
      }
    },

    /** Hot-path: derive cursor x for currentTime from cached bar layout. */
    timeToX(t) {
      const bl = this._barLayout;
      if (!bl || !bl.length) return 0;
      for (let i = 0; i < bl.length; i++) {
        const b = bl[i];
        if (t >= b.tStart && t < b.tEnd) {
          const frac = (t - b.tStart) / Math.max(0.001, b.tEnd - b.tStart);
          return b.xStart + frac * (b.xEnd - b.xStart);
        }
      }
      if (t < bl[0].tStart) return bl[0].xStart;
      return bl[bl.length - 1].xEnd;
    },

    currentRange() {
      return { start: this._rangeStart || 0, end: this._rangeEnd || 0 };
    },

    /** Drop SVG content + renderer refs so hidden state doesn't keep memory. */
    destroy() {
      if (this._host) {
        try { this._host.innerHTML = ""; } catch (_) {}
      }
      this._host = null;
      this._renderer = null;
      this._context = null;
      this._barLayout = null;
      this._ready = false;
    },
  };

  global.ScoreRender = ScoreRender;
})(window);
