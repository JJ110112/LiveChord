/**
 * ArrangerInstrument — Yamaha PSR-SX900 編曲鍵盤教學
 *
 * Unified waterfall + keyboard view (like piano tab):
 *   - LH chord input notes shown as BLUE bars (held for chord duration)
 *   - RH melody notes shown as ORANGE bars
 *   - Split point indicator on keyboard + waterfall
 *   - Full 61-key range: C1 (MIDI 36) ~ C6 (MIDI 96)
 *
 * Split point default: F#2 (MIDI 54 in Yamaha octave where C3=middle C).
 */
class ArrangerInstrument {
  constructor(config, bridge) {
    this._config = config;
    this._b = bridge;
    this._activeChordName = null;
    this._activeIdx = -1;
    this._cache = {};          // chord name -> arranger voicing from API
    this._splitPoint = parseInt(localStorage.getItem("livechord_arranger_split") || "54");
    this._pianoCache = null;   // offscreen keyboard cache (full 61-key range)
    this._lastWidth = 0;
  }

  /* ---- Constants ---- */
  static DEFAULT_SPLIT = 54;   // F#2 (Yamaha: C3 = middle C = MIDI 60)
  static MIDI_LOW = 36;        // C1
  static MIDI_HIGH = 96;       // C6

  /* ---- Semantic colors (same as piano tab) ---- */
  static LH_COLOR = "rgba(33, 150, 243, 0.9)";
  static RH_COLOR = "rgba(255, 152, 0, 0.9)";
  static LH_GLOW  = "rgba(33, 150, 243, 0.4)";
  static RH_GLOW  = "rgba(255, 152, 0, 0.4)";

  /* ---- MIDI helpers ---- */
  static NOTE_NAMES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];

  static midiToName(midi) {
    return ArrangerInstrument.NOTE_NAMES[midi % 12] + (Math.floor(midi / 12) - 2);
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
    this._lhHintEl = document.querySelector(sel.lhHint);
    this._splitSelect = document.querySelector(sel.splitPointSelect);

    if (this._splitSelect) {
      this._splitSelect.value = String(this._splitPoint);
      this._splitSelect.onchange = () => this._onSplitChange();
    }

    this._activeIdx = -1;
    this._activeChordName = null;
    this._pianoCache = null;
    this._lastWidth = 0;

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

  _onSplitChange() {
    const val = parseInt(this._splitSelect.value);
    if (isNaN(val)) return;
    this._splitPoint = val;
    localStorage.setItem("livechord_arranger_split", String(val));
    this._cache = {};
    this._pianoCache = null;
    this._lastWidth = 0;
    this._activeChordName = null;
    this.prefetchData();
    requestAnimationFrame(() => this.update(this._b.getAudio().currentTime || 0));
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
        this._updateHints(chordName);
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
    const container = canvas.parentElement;
    const cw = container.clientWidth;
    // Reserve space for keyboard below
    const kbCanvas = this._kbCanvas;
    const kbH = kbCanvas ? (kbCanvas.clientHeight || 0) : 0;
    const ch = container.clientHeight - kbH;
    if (cw < 10 || ch < 30) return;

    const dpr = window.devicePixelRatio || 1;
    const W = cw;
    const H = ch;
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    canvas.style.width = W + "px";
    canvas.style.height = H + "px";
    const ctx = canvas.getContext("2d");
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

    // Split point vertical line in waterfall
    const splitMidi = this._splitPoint;
    const splitKeyInfo = cache.whiteXs[splitMidi] || cache.blackXs[splitMidi];
    if (splitKeyInfo) {
      const sx = splitKeyInfo.x + splitKeyInfo.w;
      ctx.strokeStyle = "rgba(255, 80, 80, 0.35)";
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 4]);
      ctx.beginPath();
      ctx.moveTo(sx, 0);
      ctx.lineTo(sx, H);
      ctx.stroke();
      ctx.setLineDash([]);
    }

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
        const gcEnd = (ci + 1 < chords.length) ? chords[ci + 1].time : gc.time + 4;
        const gcDur = gcEnd - gc.time;
        for (let b = 0; b < 4; b++) {
          const bt = gc.time + (b / 4) * gcDur;
          if (bt < currentTime - 0.1 || bt > currentTime + lookAhead) continue;
          const y = H - (bt - currentTime) * pxPerSec;
          if (y < 0 || y > H) continue;
          const isBarLine = (b === 0);
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

    // RH: accompaniment right_hand or melody data
    const accData = this._b.getAccData ? this._b.getAccData() : null;
    let rhEvents = (accData && accData.right_hand) ? accData.right_hand : [];
    if (rhEvents.length === 0) {
      const melodyData = this._b.getMelodyData ? this._b.getMelodyData() : null;
      if (melodyData && melodyData.length > 0) {
        rhEvents = melodyData.map(m => ({
          time: m.start,
          duration: m.end - m.start,
          pitch: m.midi,
          velocity: 80,
          finger: null,
        }));
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

    for (const evt of allEvents) {
      const noteStart = evt.time;
      const noteEnd = evt.time + evt.duration;
      if (noteEnd < currentTime || noteStart > currentTime + lookAhead) continue;

      const yBottom = H - (noteStart - currentTime) * pxPerSec;
      const yTop = H - (noteEnd - currentTime) * pxPerSec;
      const noteH = Math.max(yBottom - yTop, 3);

      const midi = evt.pitch;
      const keyInfo = cache.whiteXs[midi] || cache.blackXs[midi];
      if (!keyInfo) continue;
      const x = keyInfo.x;
      const kw = keyInfo.w;
      const isLeft = evt._hand === "left";
      const isOnBlackKey = !!cache.blackXs[midi];

      // Velocity-responsive coloring (same as piano waterfall)
      const vel = evt.velocity || 80;
      const velT = Math.min(1.0, Math.max(0.0, (vel - 55) / 40));
      const velP = velT * velT;
      let color, glowColor;
      if (isLeft) {
        const cr = Math.round(10 + velP * 100);
        const cg = Math.round(40 + velP * 160);
        const cb = Math.round(100 + velP * 155);
        color = `rgba(${cr}, ${cg}, ${cb}, ${isOnBlackKey ? 0.95 : 0.9})`;
        glowColor = `rgba(${Math.min(255, cr+80)}, ${Math.min(255, cg+60)}, 255, 1)`;
      } else {
        const cr = Math.round(100 + velP * 155);
        const cg = Math.round(40 + velP * 170);
        const cb = Math.round(0 + velP * 50);
        color = `rgba(${cr}, ${cg}, ${cb}, ${isOnBlackKey ? 0.95 : 0.9})`;
        glowColor = `rgba(255, ${Math.min(255, cg+60)}, ${Math.min(255, cb+80)}, 1)`;
      }

      // Prediction shadow
      if (yBottom > H - 40 && yBottom < H) {
        ctx.fillStyle = isLeft ? ArrangerInstrument.LH_GLOW : ArrangerInstrument.RH_GLOW;
        ctx.fillRect(x, H - 5, kw, -20);
      }

      // Note bar with glow
      const rr = Math.min(4, noteH / 2);
      ctx.save();
      if (velP > 0.15) {
        ctx.shadowColor = glowColor;
        ctx.shadowBlur = Math.round(3 + velP * 25);
      }
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.roundRect(x + 1, yTop, kw - 2, noteH, rr);
      ctx.fill();
      if (velP > 0.5) {
        ctx.shadowBlur = Math.round(velP * 35);
        ctx.fill();
      }
      ctx.restore();

      // Dim outline for quiet notes
      if (velP < 0.15) {
        ctx.strokeStyle = "rgba(255,255,255,0.2)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.roundRect(x + 1, yTop, kw - 2, noteH, rr);
        ctx.stroke();
      }

      // Track active keys
      if (currentTime >= evt.time && currentTime < noteEnd) {
        if (isLeft) activeLh.add(midi);
        else activeRh.add(midi);
      }

      // Contact flash
      if (yBottom >= H && yTop <= H) {
        ctx.save();
        ctx.fillStyle = color;
        ctx.shadowColor = glowColor;
        ctx.shadowBlur = 8 + velP * 22;
        ctx.fillRect(x + 1, H - 4, kw - 2, 8);
        ctx.fillStyle = `rgba(255,255,255,${0.3 + velP * 0.6})`;
        ctx.shadowBlur = velP * 15;
        ctx.shadowColor = "#fff";
        ctx.fillRect(x + 3, H - 2, kw - 6, 4);
        ctx.restore();
      }

      // Finger number for LH chord notes
      if (isLeft && evt.finger && yBottom > 0 && yTop < H && noteH > 16) {
        const circled = String.fromCodePoint(0x2460 + evt.finger - 1);
        ctx.fillStyle = "rgba(255,255,255,0.85)";
        ctx.font = "bold 12px sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(circled, x + kw / 2, (yTop + yBottom) / 2);
      }
    }

    // Store active keys for keyboard highlighting
    this._activeLh = activeLh;
    this._activeRh = activeRh;
  }

  /* ======================================================================
   * KEYBOARD — full 61 keys with hand-colored highlights + split marker
   * ====================================================================== */

  _drawKeyboard(currentTime) {
    const canvas = this._kbCanvas;
    if (!canvas || !canvas.parentElement) return;
    const container = canvas.parentElement;
    const W = container.clientWidth;
    if (W < 10) return;
    const dpr = window.devicePixelRatio || 1;

    // Ensure cache
    const ChordRender = this._b.ChordRender;
    if (!this._pianoCache || Math.abs(this._lastWidth - W) > 2) {
      this._pianoCache = ChordRender.initRangePianoCache(W, dpr,
        ArrangerInstrument.MIDI_LOW, ArrangerInstrument.MIDI_HIGH);
      this._lastWidth = W;
    }
    const cache = this._pianoCache;
    const pianoH = cache.totalH;

    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(pianoH * dpr);
    canvas.style.width = W + "px";
    canvas.style.height = pianoH + "px";
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);

    // Draw cached static keyboard
    ctx.drawImage(cache.canvas, 0, 0, cache.canvas.width, cache.canvas.height,
                  0, 0, W, pianoH);

    const kh = cache.keyH;
    const bh = cache.bKeyH;
    const activeLh = this._activeLh || new Set();
    const activeRh = this._activeRh || new Set();
    const LH_CLR = ArrangerInstrument.LH_COLOR;
    const RH_CLR = ArrangerInstrument.RH_COLOR;

    // Pass 1: White key highlights
    const allActive = new Set([...activeLh, ...activeRh]);
    for (const midi of allActive) {
      const wk = cache.whiteXs[midi];
      if (!wk) continue;
      const clr = activeLh.has(midi) ? LH_CLR : RH_CLR;
      ctx.save();
      ctx.globalAlpha = 0.9;
      ctx.fillStyle = clr;
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(wk.x + 0.5, 0.5, wk.w - 1, kh - 1, [0, 0, 4, 4]);
      else ctx.rect(wk.x + 0.5, 0.5, wk.w - 1, kh - 1);
      ctx.fill();
      // Top wash
      const topWash = ctx.createLinearGradient(0, 0, 0, kh * 0.5);
      topWash.addColorStop(0, "rgba(255,255,255,0.25)");
      topWash.addColorStop(1, "rgba(255,255,255,0)");
      ctx.fillStyle = topWash;
      ctx.fillRect(wk.x + 0.5, 0.5, wk.w - 1, kh * 0.5);
      // Bottom glow
      ctx.shadowColor = clr;
      ctx.shadowBlur = 15;
      ctx.fillStyle = clr;
      ctx.fillRect(wk.x + 2, kh - 6, wk.w - 4, 6);
      ctx.restore();
    }

    // Pass 2: Black key highlights
    for (const midi of allActive) {
      const bk = cache.blackXs[midi];
      if (!bk) continue;
      const clr = activeLh.has(midi) ? LH_CLR : RH_CLR;
      ctx.save();
      ctx.globalAlpha = 0.9;
      ctx.fillStyle = clr;
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(bk.x, 0, bk.w, bh, [0, 0, 3, 3]);
      else ctx.rect(bk.x, 0, bk.w, bh);
      ctx.fill();
      const hlGrad = ctx.createLinearGradient(bk.x, 0, bk.x, bh * 0.3);
      hlGrad.addColorStop(0, "rgba(255,255,255,0.3)");
      hlGrad.addColorStop(1, "rgba(255,255,255,0)");
      ctx.fillStyle = hlGrad;
      ctx.fillRect(bk.x + bk.w * 0.1, 0, bk.w * 0.8, bh * 0.3);
      ctx.shadowColor = clr;
      ctx.shadowBlur = 12;
      ctx.fillStyle = clr;
      ctx.fillRect(bk.x + 1, bh - 4, bk.w - 2, 4);
      ctx.restore();
    }

    // Split point marker on keyboard
    const splitMidi = this._splitPoint;
    const splitKey = cache.whiteXs[splitMidi] || cache.blackXs[splitMidi];
    if (splitKey) {
      const sx = splitKey.x + splitKey.w;
      ctx.strokeStyle = "rgba(255, 80, 80, 0.7)";
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.moveTo(sx, 0);
      ctx.lineTo(sx, kh + cache.bevelH);
      ctx.stroke();
      // Small label
      ctx.fillStyle = "rgba(255, 80, 80, 0.8)";
      ctx.font = "bold 9px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("SPLIT", sx, kh + cache.bevelH + 11);
    }
  }

  /* ---- Hints ---- */

  _updateHints(chordName) {
    const resolved = this._resolveChord(chordName);
    if (!resolved || !resolved.available) {
      if (this._lhHintEl) {
        this._lhHintEl.textContent = resolved && resolved.warning
          ? resolved.warning
          : "左手：和弦輸入區";
      }
      return;
    }
    const notes = resolved.midi_notes.map(m => ArrangerInstrument.midiToName(m));
    if (this._lhHintEl) {
      this._lhHintEl.textContent = `${chordName}: ${notes.join(" + ")}`;
    }
  }
}
