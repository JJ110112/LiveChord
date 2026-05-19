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

  // ------- Duration helpers -------
  // We assume denominator = 4 throughout (4/4, 3/4 etc). All beats expressed as
  // "quarter-note equivalents". 1/16 is the finest grid VexFlow can render
  // without dotted/tuplet complexity.

  const _DUR_BEATS = { w: 4, h: 2, q: 1, "8": 0.5, "16": 0.25, "32": 0.125 };

  function _durToBeats(d) {
    const s = String(d || "");
    const base = s.replace(/r/g, "").replace(/\./g, "");
    let b = _DUR_BEATS[base] || 0;
    if (s.indexOf(".") >= 0) b *= 1.5;
    return b;
  }

  // Snap raw seconds to nearest 1/16-beat, then choose the closest plain dur.
  function _quantizeDur(secs, beatSecs) {
    const beats = Math.max(0.0625, Math.round((secs / beatSecs) * 16) / 16);
    if (beats >= 4)    return "w";
    if (beats >= 2)    return "h";
    if (beats >= 1)    return "q";
    if (beats >= 0.5)  return "8";
    if (beats >= 0.25) return "16";
    return "32";
  }

  // Greedy decomposition of a residual beat count into rest StaveNotes.
  function _beatsToRests(beats, makeRest) {
    const out = [];
    let remain = beats;
    const tiers = [["w", 4], ["h", 2], ["q", 1], ["8", 0.5], ["16", 0.25], ["32", 0.125]];
    for (const [dur, b] of tiers) {
      while (remain >= b - 1e-6) {
        out.push(makeRest(dur));
        remain -= b;
      }
    }
    return out;
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
      const { timeSig = "4/4", key = "C", bpm = 120, timeRange, lhNotes = [], rhNotes = [] } = this._opts;

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

      const tsBeats = parseInt(String(timeSig).split("/")[0], 10) || 4;
      const beatSecs = 60 / Math.max(40, bpm);
      const barSecs = tsBeats * beatSecs;

      const range = timeRange || { start: 0, end: 4 * barSecs };
      const rangeSecs = Math.max(0.001, range.end - range.start);
      const numBars = Math.max(1, Math.round(rangeSecs / barSecs));

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
          tStart: range.start + i * barSecs,
          tEnd: range.start + (i + 1) * barSecs,
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
      for (let i = 0; i < numBars; i++) {
        const barStart = range.start + i * barSecs;
        const barEnd = barStart + barSecs;
        const rhVexNotes = this._buildBarNotes(rhNotes, barStart, barEnd, beatSecs, tsBeats, key, "treble");
        const lhVexNotes = this._buildBarNotes(lhNotes, barStart, barEnd, beatSecs, tsBeats, key, "bass");
        this._drawVoice(rhVexNotes, trebleStaves[i], tsBeats);
        this._drawVoice(lhVexNotes, bassStaves[i], tsBeats);
      }

      this._rangeStart = range.start;
      this._rangeEnd = range.end;
    },

    _buildBarNotes(events, barStart, barEnd, beatSecs, tsBeats, key, clef) {
      const VF = _VF();
      // Pitch-range filter per clef. Drop pitches well outside each
      // clef's reasonable display window (the waterfall + keyboard still
      // play them). Notes within the range are drawn at their actual
      // pitch — ledger lines ARE allowed and expected for low bass /
      // high treble.
      //   Bass:   MIDI 33-72  (A1 → C5)   covers stride / walking-bass
      //                                   + cross-staff middle voice
      //   Treble: MIDI 53-100 (F3 → E7)   covers melody + chord voicings
      const pitchMin = clef === "bass" ? 33 : 53;
      const pitchMax = clef === "bass" ? 72 : 100;
      const inBar = (events || []).filter(e => {
        const t = +e.time;
        const p = +e.pitch;
        return t >= barStart - 1e-3 && t < barEnd - 1e-3
               && Number.isFinite(p) && p >= pitchMin && p <= pitchMax;
      }).sort((a, b) => +a.time - +b.time);

      const makeRest = (dur) => new VF.StaveNote({
        keys: clef === "treble" ? ["b/4"] : ["d/3"],
        duration: dur + "r",
        clef,
      });

      const notes = [];
      let prevEnd = barStart;
      let cursor = 0;
      while (cursor < inBar.length) {
        const t0 = +inBar[cursor].time;
        const group = [];
        // Group same-onset (or near-same-onset within 30 ms) events into a chord StaveNote.
        while (cursor < inBar.length && Math.abs(+inBar[cursor].time - t0) < 0.03) {
          group.push(inBar[cursor]);
          cursor++;
        }
        // Leading rest if gap from prevEnd to t0.
        if (t0 > prevEnd + 0.01) {
          const gapBeats = (t0 - prevEnd) / beatSecs;
          notes.push(..._beatsToRests(gapBeats, makeRest));
        }
        const maxDur = Math.max(...group.map(e => +e.duration || beatSecs));
        const noteEndT = Math.min(barEnd, t0 + maxDur);
        const noteDurSecs = Math.max(beatSecs * 0.0625, noteEndT - t0);
        const durStr = _quantizeDur(noteDurSecs, beatSecs);
        // CRITICAL #1: dedup by pitch. AI accompaniment occasionally emits
        // two events at the same time with the same MIDI pitch (e.g. a
        // root reinforcement on top of a chord-tone). Visually we want one
        // notehead, not stacked duplicates.
        // CRITICAL #2: VexFlow requires keys[] sorted low→high — wrong
        // order flips stem direction and stacks noteheads incorrectly.
        const pitchSet = new Set();
        const pitches = [];
        for (const e of group) {
          const p = +e.pitch;
          if (Number.isFinite(p) && !pitchSet.has(p)) {
            pitchSet.add(p);
            pitches.push(p);
          }
        }
        pitches.sort((a, b) => a - b);
        const keys = pitches.map(p => _midiToVexKey(p, key));
        if (keys.length === 0) {
          notes.push(makeRest(durStr));
        } else {
          const sn = new VF.StaveNote({ keys, duration: durStr, clef });
          // CRITICAL #3: VexFlow does NOT auto-render sharp/flat glyphs
          // from the key string alone — keyProps.accidental gets set but
          // no Accidental modifier is added. Without an explicit
          // addModifier, "bb/3" draws at the B3 staff position WITHOUT the
          // flat glyph → reads visually as B3 (or A3 if the eye snaps to
          // the nearest ledger). Always emit explicit accidentals for any
          // key containing # or b in position 1.
          keys.forEach((k, idx) => {
            const c = k.charAt(1);
            if (c === "#" || c === "b") {
              try { sn.addModifier(new VF.Accidental(c), idx); } catch (_) {}
            }
          });
          notes.push(sn);
        }
        prevEnd = noteEndT;
      }
      if (prevEnd < barEnd - 0.01) {
        const trailBeats = (barEnd - prevEnd) / beatSecs;
        notes.push(..._beatsToRests(trailBeats, makeRest));
      }

      // ---- Padding Patch ----
      // VexFlow strict-validates: sum(note durations) must equal num_beats.
      // Off-by-1/16 → RhythmException → the whole layer crashes. Auto-balance
      // by either appending rests (positive diff) or truncating the last note
      // (negative diff). Last-resort fallback: replace bar with whole-bar rest.
      const beatsOf = (n) => _durToBeats(n.getDuration ? n.getDuration() : n.duration);
      let filled = notes.reduce((s, n) => s + beatsOf(n), 0);
      let diff = tsBeats - filled;
      if (diff > 0.001) {
        notes.push(..._beatsToRests(diff, makeRest));
        filled = notes.reduce((s, n) => s + beatsOf(n), 0);
        diff = tsBeats - filled;
      }
      if (Math.abs(diff) > 0.001) {
        return [makeRest("w")];
      }
      return notes;
    },

    _drawVoice(stavenotes, stave, tsBeats) {
      const VF = _VF();
      if (!stavenotes || !stavenotes.length) return;
      try {
        const voice = new VF.Voice({ num_beats: tsBeats, beat_value: 4 });
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
      } catch (e) {
        console.warn("[score-render] voice draw failed", e);
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
