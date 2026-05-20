/**
 * ArrangerInstrument — Yamaha PSR-SX900 編曲鍵盤教學
 *
 * Unified waterfall + keyboard view (like piano tab):
 *   - LH chord input notes shown as BLUE bars (held for chord duration)
 *   - RH melody notes shown as ORANGE bars
 *   - Split point indicator on keyboard + waterfall
 *   - Full 61-key range: C1 (MIDI 36) ~ C6 (MIDI 96)
 *
 * Split point default: G#3 (MIDI 56 in Yamaha octave where C3=middle C).
 */
class ArrangerInstrument {
  constructor(config, bridge) {
    this._config = config;
    this._b = bridge;
    this._activeChordName = null;
    this._activeIdx = -1;
    this._cache = {};          // chord name -> arranger voicing from API
    // Migration: clear stale split value from old versions
    const storedSplit = localStorage.getItem("livechord_arranger_split");
    if (storedSplit && parseInt(storedSplit) < 48) {
      localStorage.removeItem("livechord_arranger_split");
    }
    this._splitPoint = parseInt(localStorage.getItem("livechord_arranger_split") || "56");
    this._pianoCache = null;   // offscreen keyboard cache (full 61-key range)
    this._lastWidth = 0;
  }

  /* ---- Constants ---- */
  static DEFAULT_SPLIT = 56;   // G#3 (MIDI 56)
  static MIDI_LOW = 21;        // A0 (same as 88-key piano for consistent sizing)
  static MIDI_HIGH = 108;      // C8

  /* ---- Semantic colors (same as piano tab) ---- */
  static LH_COLOR = "rgba(33, 150, 243, 0.9)";
  static RH_COLOR = "rgba(255, 152, 0, 0.9)";
  static LH_GLOW  = "rgba(33, 150, 243, 0.4)";
  static RH_GLOW  = "rgba(255, 152, 0, 0.4)";

  /* ---- MIDI helpers ---- */
  static NOTE_NAMES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];

  static midiToName(midi) {
    return ArrangerInstrument.NOTE_NAMES[midi % 12] + (Math.floor(midi / 12) - 1);
  }

  getSplitPoint() {
    return this._splitPoint;
  }

  setSplitPoint(midi) {
    const next = Math.max(48, Math.min(60, parseInt(midi, 10)));
    if (!Number.isFinite(next) || next === this._splitPoint) return this._splitPoint;
    this._splitPoint = next;
    localStorage.setItem("livechord_arranger_split", String(next));
    this._cache = {};
    this._pianoCache = null;
    this._kbCache = null;
    this._lastWidth = 0;
    this._lastKbWidth = 0;
    this._activeChordName = null;
    this.prefetchData();
    const t = this._b.getAudio().currentTime || 0;
    this.update(t);
    return this._splitPoint;
  }

  stepSplitPoint(delta) {
    return this.setSplitPoint(this._splitPoint + delta);
  }

  resetSplitPoint() {
    return this.setSplitPoint(ArrangerInstrument.DEFAULT_SPLIT);
  }

  /* ---- Interface methods ---- */

  init() {
    const chordData = this._b.getChordData();
    if (!chordData || !chordData.chords || chordData.chords.length === 0) {
      const self = this;
      setTimeout(() => {
        if (this._b.getActiveTab() === this._config.id) self.init();
      }, 1000);
      return;
    }

    const sel = this._config.selectors;
    this._wfCanvas = document.querySelector(sel.waterfallCanvas);
    this._kbCanvas = document.querySelector(sel.keyboardCanvas);

    this._activeIdx = -1;
    this._activeChordName = null;
    this._pianoCache = null;
    this._kbCache = null;
    this._lastWidth = 0;
    this._lastKbWidth = 0;
    if (this._kbCanvas) this._kbCanvas.style.cursor = "default";

    // ResizeObserver: re-render when flex layout changes canvas dimensions
    // (e.g. keyboard canvas sizing itself changes waterfall height)
    if (this._wfCanvas && !this._wfCanvas._arrResizeObs) {
      const ro = new ResizeObserver(() => {
        if (this._b.getActiveTab() !== this._config.id) return;
        const t = this._b.getAudio().currentTime || 0;
        this.update(t);
      });
      ro.observe(this._wfCanvas);
      this._wfCanvas._arrResizeObs = ro;
    }

    this.prefetchData();
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const t = this._b.getAudio().currentTime || 0;
        this.update(t);
      });
    });
  }

  async prefetchData() {
    const chords = this._b.getDisplayChords();
    if (!chords || !chords.length) return;
    const names = [...new Set(chords.map(c => c.chord))];
    const split = this._splitPoint;
    await Promise.all(names.map(async (name) => {
      const cacheKey = name + ":" + split;
      if (this._cache[cacheKey]) return;
      try {
        const url = `/api/chord/diagram/arranger/${encodeURIComponent(name)}?split=${split}`;
        const res = await fetch(url);
        if (!res.ok) throw new Error(res.status);
        this._cache[cacheKey] = await res.json();
      } catch { this._cache[cacheKey] = null; }
    }));
  }

  _resolveChord(chordName) {
    return this._cache[chordName + ":" + this._splitPoint] || null;
  }

  _isNearSplitArrow(clientX, clientY) {
    const cache = this._kbCache;
    if (!cache) return false;
    const rect = this._kbCanvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    const splitKey = cache.whiteXs[this._splitPoint] || cache.blackXs[this._splitPoint];
    if (!splitKey) return false;
    const sx = splitKey.x + splitKey.w;
    // Wide hit area: ±20px horizontal, top 30px of keyboard
    return Math.abs(x - sx) < 20 && y < 30;
  }

  /* ---- Main update (called every frame) ---- */

  update(currentTime) {
    // Track current chord for hint updates
    const chords = this._b.getDisplayChords();
    if (chords && chords.length > 0) {
      let idx = 0;
      for (let i = chords.length - 1; i >= 0; i--) {
        if (currentTime >= chords[i].time) { idx = i; break; }
      }
      const chordName = chords[idx].chord;
      if (chordName !== this._activeChordName) {
        this._activeChordName = chordName;
        this._activeIdx = idx;
      }
    }
    this._drawUnifiedWaterfall(currentTime);
    this._drawKeyboard(currentTime);
  }

  /* ======================================================================
   * UNIFIED WATERFALL — LH blue, RH orange, split line
   * ====================================================================== */

  _drawUnifiedWaterfall(currentTime) {
    const canvas = this._wfCanvas;
    if (!canvas || !canvas.parentElement) return;
    // Read size from parent to avoid canvas intrinsic-size feedback loop
    const W = canvas.parentElement.clientWidth;
    const H = canvas.clientHeight || canvas.parentElement.clientHeight;
    if (W < 10 || H < 30) return;

    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, W, H);

    // Ensure piano cache exists
    const ChordRender = this._b.ChordRender;
    if (!this._pianoCache || Math.abs(this._lastWidth - W) > 2) {
      this._pianoCache = ChordRender.initRangePianoCache(W, dpr,
        ArrangerInstrument.MIDI_LOW, ArrangerInstrument.MIDI_HIGH);
      this._lastWidth = W;
    }
    const cache = this._pianoCache;

    // Vertical piano key grid lines
    ctx.strokeStyle = "rgba(255, 255, 255, 0.05)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    let lastKey = null;
    for (const p in cache.whiteXs) {
      const wk = cache.whiteXs[p];
      ctx.moveTo(wk.x, 0);
      ctx.lineTo(wk.x, H);
      lastKey = wk;
    }
    if (lastKey) {
      ctx.moveTo(lastKey.x + lastKey.w, 0);
      ctx.lineTo(lastKey.x + lastKey.w, H);
    }
    ctx.stroke();

    const splitMidi = this._splitPoint;

    // Time grid
    const lookAhead = 4.0;
    const pxPerSec = H / lookAhead;
    const chords = this._b.getDisplayChords();
    if (chords && chords.length > 0) {
      ctx.textAlign = "left";
      ctx.textBaseline = "bottom";
      ctx.font = "11px sans-serif";
      for (let ci = 0; ci < chords.length; ci++) {
        const gc = chords[ci];
        const gridLines = this._b.getWaterfallBeatGrid
          ? this._b.getWaterfallBeatGrid(chords, ci)
          : [];
        for (const line of gridLines) {
          const bt = line.time;
          if (bt < currentTime - 0.1 || bt > currentTime + lookAhead) continue;
          const y = H - (bt - currentTime) * pxPerSec;
          if (y < 0 || y > H) continue;
          const isBarLine = !!line.isBarLine;
          ctx.strokeStyle = isBarLine ? "rgba(255,255,255,0.25)" : "rgba(255,255,255,0.08)";
          ctx.lineWidth = isBarLine ? 2 : 1;
          ctx.beginPath();
          ctx.moveTo(0, y);
          ctx.lineTo(W, y);
          ctx.stroke();
          if (isBarLine) {
            ctx.fillStyle = "rgba(255,255,255,0.5)";
            ctx.fillText(gc.chord, 8, y - 3);
          }
        }
      }
    }

    // Build event list: LH chord events (blue) + RH melody events (orange)
    const allEvents = [];

    // LH: generate chord-hold events from chord data + arranger voicing
    if (chords && chords.length > 0) {
      for (let ci = 0; ci < chords.length; ci++) {
        const gc = chords[ci];
        const gcEnd = (ci + 1 < chords.length) ? chords[ci + 1].time : gc.time + 4;
        const dur = gcEnd - gc.time;
        // Skip if entirely out of view
        if (gcEnd < currentTime || gc.time > currentTime + lookAhead) continue;
        const resolved = this._resolveChord(gc.chord);
        if (!resolved || !resolved.midi_notes) continue;
        for (let n = 0; n < resolved.midi_notes.length; n++) {
          allEvents.push({
            time: gc.time,
            duration: dur,
            pitch: resolved.midi_notes[n],
            velocity: 70,
            finger: resolved.fingering ? resolved.fingering[n] : null,
            _hand: "left",
          });
        }
      }
    }

    // RH: accompaniment right_hand or melody data. Cache the mapped melody so
    // `_sparked` persists across frames (fresh-object remap would flood particles).
    const accData = this._b.getAccData ? this._b.getAccData() : null;
    let rhEvents = (accData && accData.right_hand) ? accData.right_hand : [];
    if (rhEvents.length === 0) {
      const melodyData = this._b.getMelodyData ? this._b.getMelodyData() : null;
      if (melodyData && melodyData.length > 0) {
        if (!this._mappedMelody || this._mappedMelodySrc !== melodyData) {
          this._mappedMelody = melodyData.map(m => ({
            time: Number(m.time != null ? m.time : m.start) || 0,
            duration: Math.max(0.05, Number(m.duration) || ((Number(m.end) || 0) - (Number(m.start) || 0)) || 0.25),
            pitch: Math.round(Number(m.pitch != null ? m.pitch : m.midi) || 60),
            velocity: 80,
            finger: null,
          }));
          this._mappedMelodySrc = melodyData;
        }
        rhEvents = this._mappedMelody;
      }
    }
    // Filter RH to notes above split point
    for (const evt of rhEvents) {
      if (evt.pitch > splitMidi) {
        allEvents.push({ ...evt, _hand: "right" });
      }
    }

    // Draw note bars
    const activeLh = new Set();
    const activeRh = new Set();
    const fingeringMap = {};

    for (const evt of allEvents) {
      const noteStart = evt.time;
      const noteEnd = evt.time + evt.duration;
      if (noteEnd < currentTime || noteStart > currentTime + lookAhead) continue;

      // Re-arm spark flag on backward-seek
      if (evt._sparked && currentTime < noteStart) evt._sparked = false;

      const yBottom = H - (noteStart - currentTime) * pxPerSec;
      const yTop = H - (noteEnd - currentTime) * pxPerSec;
      const noteH = Math.max(yBottom - yTop, 3);

      const midi = evt.pitch;
      const keyInfo = cache.whiteXs[midi] || cache.blackXs[midi];
      if (!keyInfo) continue;
      const x = keyInfo.x;
      const kw = keyInfo.w;
      const isLeft = evt._hand === "left";

      // Piano-parity velocity → color ramp (identical formula across
      // piano / accordion / arranger so everything reads the same).
      const vel = evt.velocity || 80;
      const velT = Math.min(1.0, Math.max(0.0, (vel - 55) / 40));
      const velP = velT * velT;
      let cr, cg, cb, glowColor;
      if (isLeft) {
        cr = Math.round(40 + velP * 100);
        cg = Math.round(150 + velP * 85);
        cb = Math.round(230 + velP * 25);
        glowColor = `rgba(120, 200, 255, 1)`;
      } else {
        cr = Math.round(230 + velP * 25);
        cg = Math.round(130 + velP * 90);
        cb = Math.round(40 + velP * 40);
        glowColor = `rgba(255, 200, 120, 1)`;
      }
      const color = `rgba(${cr}, ${cg}, ${cb}, 1)`;

      // Landing-pad glow
      if (yBottom > H - 40 && yBottom < H) {
        ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, 0.35)`;
        ctx.fillRect(x, H - 5, kw, -20);
      }

      // Main note bar with always-on bloom
      const rr = Math.min(4, noteH / 2);
      ctx.save();
      ctx.shadowColor = glowColor;
      ctx.shadowBlur = Math.round(12 + velP * 36);
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.roundRect(x + 1, yTop, kw - 2, noteH, rr);
      ctx.fill();
      if (velP > 0.4) {
        ctx.shadowBlur = Math.round(velP * 55);
        ctx.fill();
      }
      ctx.restore();

      // Top highlight
      if (noteH > 4) {
        const hlH = Math.min(8, noteH * 0.35);
        const grd = ctx.createLinearGradient(0, yTop, 0, yTop + hlH);
        grd.addColorStop(0, "rgba(255,255,255,0.55)");
        grd.addColorStop(1, "rgba(255,255,255,0)");
        ctx.fillStyle = grd;
        ctx.beginPath();
        ctx.roundRect(x + 1, yTop, kw - 2, hlH, rr);
        ctx.fill();
      }

      // Hot leading edge
      if (yBottom < H && noteH > 3) {
        ctx.fillStyle = `rgba(255, 255, 255, ${0.25 + velP * 0.35})`;
        ctx.fillRect(x + 2, yBottom - 2, kw - 4, 2);
      }

      // Track active keys + fingering
      if (currentTime >= evt.time && currentTime < noteEnd) {
        if (isLeft) {
          activeLh.add(midi);
          if (evt.finger) fingeringMap[midi] = { finger: evt.finger, hand: "left" };
        } else {
          activeRh.add(midi);
          if (evt.finger) fingeringMap[midi] = { finger: evt.finger, hand: "right" };
        }
      }

      // Contact burst + spark particles
      if (yBottom >= H && yTop <= H) {
        ctx.save();
        ctx.fillStyle = color;
        ctx.shadowColor = glowColor;
        ctx.shadowBlur = 14 + velP * 30;
        ctx.fillRect(x + 1, H - 4, kw - 2, 8);
        ctx.fillStyle = `rgba(255,255,255,${0.5 + velP * 0.5})`;
        ctx.shadowBlur = 8 + velP * 20;
        ctx.shadowColor = "#fff";
        ctx.fillRect(x + 3, H - 2, kw - 6, 4);
        ctx.restore();

        if (!evt._sparked && this._b.spawnWaterfallParticles) {
          evt._sparked = true;
          this._b.spawnWaterfallParticles(x + kw / 2, H - 2, kw, cr, cg, cb, velP);
        }
      }
    }

    // Firework spark particles (shared via bridge)
    if (this._b.drawWaterfallParticles) this._b.drawWaterfallParticles(ctx);

    // Store active keys + fingering for keyboard highlighting
    this._activeLh = activeLh;
    this._activeRh = activeRh;
    this._fingeringMap = fingeringMap;

    // AI Teacher HUD (shared with piano tab) — skip if canvas too small
    if (this._b.drawAITeacherHUD && H > 100) {
      const lookAhead = 4.0;
      const pxPerSec = H / lookAhead;
      this._b.drawAITeacherHUD(ctx, W, H, currentTime, allEvents, pxPerSec);
    }
  }

  /* ======================================================================
   * KEYBOARD — full 61 keys with hand-colored highlights + split marker
   * ====================================================================== */

  _drawKeyboard(currentTime) {
    const canvas = this._kbCanvas;
    if (!canvas || !canvas.parentElement) return;
    const W = canvas.parentElement.clientWidth;
    if (W < 10) return;
    const dpr = window.devicePixelRatio || 1;
    const ChordRender = this._b.ChordRender;

    // Keyboard needs its own cache at its own width (may differ from waterfall)
    if (!this._kbCache || Math.abs(this._lastKbWidth - W) > 2) {
      this._kbCache = ChordRender.initRangePianoCache(W, dpr,
        ArrangerInstrument.MIDI_LOW, ArrangerInstrument.MIDI_HIGH);
      this._lastKbWidth = W;
    }
    const cache = this._kbCache;
    const pianoH = cache.totalH;

    canvas.style.width = W + "px";
    canvas.style.height = pianoH + "px";

    // Use draw88Piano for proper 3-pass rendering
    const activeLh = [...(this._activeLh || [])];
    const activeRh = [...(this._activeRh || [])];
    const fMap = this._fingeringMap || {};
    ChordRender.draw88Piano(canvas, cache, activeLh, activeRh, {
      fingeringMap: fMap,
      now: this._b.getAudio().currentTime || 0,
    });

    // Split point: red equilateral marker centered on the selected key.
    const ctx = canvas.getContext("2d");
    // Re-apply DPI scaling — draw88Piano resets ctx.setTransform to identity
    ctx.scale(dpr, dpr);
    const splitMidi = this._splitPoint;
    const splitKey = cache.whiteXs[splitMidi] || cache.blackXs[splitMidi];
    if (splitKey) {
      const sx = splitKey.x + splitKey.w / 2;
      const blackThird = (cache.bKeyW || splitKey.w * 0.58) / 3;
      const whiteFit = (cache.keyW || splitKey.w) * 0.2;
      const triW = Math.max(4, Math.min(blackThird, whiteFit));
      const triH = triW * Math.sqrt(3) / 2;
      ctx.fillStyle = "rgba(255, 80, 80, 1)";
      ctx.beginPath();
      ctx.moveTo(sx - triW / 2, 0.5);
      ctx.lineTo(sx + triW / 2, 0.5);
      ctx.lineTo(sx, 0.5 + triH);
      ctx.closePath();
      ctx.fill();
    }
  }
}
