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

    this._activeIdx = -1;
    this._activeChordName = null;
    this._pianoCache = null;
    this._kbCache = null;
    this._lastWidth = 0;
    this._lastKbWidth = 0;
    this._draggingSplit = false;

    // Set up drag handlers on keyboard canvas (only once)
    if (this._kbCanvas && !this._kbCanvas._arrDragBound) {
      this._kbCanvas._arrDragBound = true;
      const kb = this._kbCanvas;
      kb.style.cursor = "default";
      kb.addEventListener("mousedown", (e) => this._onSplitDragStart(e));
      kb.addEventListener("mousemove", (e) => this._onSplitDragMove(e));
      kb.addEventListener("mouseup", () => this._onSplitDragEnd());
      kb.addEventListener("mouseleave", () => {
        if (!this._draggingSplit) {
          this._hoverSplit = false;
          this._kbCanvas.style.cursor = "default";
        }
      });
      // Touch support
      kb.addEventListener("touchstart", (e) => this._onSplitDragStart(e), { passive: false });
      kb.addEventListener("touchmove", (e) => this._onSplitDragMove(e), { passive: false });
      kb.addEventListener("touchend", () => this._onSplitDragEnd());
    }

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

  /* ---- Split point drag on keyboard ---- */

  _splitFromX(clientX) {
    const cache = this._kbCache;
    if (!cache) return this._splitPoint;
    const rect = this._kbCanvas.getBoundingClientRect();
    const x = clientX - rect.left;
    // Find the nearest key boundary
    let bestMidi = this._splitPoint;
    let bestDist = Infinity;
    const checkKey = (midi, ki) => {
      const rightEdge = ki.x + ki.w;
      const d = Math.abs(x - rightEdge);
      if (d < bestDist) { bestDist = d; bestMidi = parseInt(midi); }
    };
    for (const m in cache.whiteXs) checkKey(m, cache.whiteXs[m]);
    for (const m in cache.blackXs) checkKey(m, cache.blackXs[m]);
    // Clamp to valid range (C2=48 ~ C3=60 Yamaha)
    return Math.max(48, Math.min(60, bestMidi));
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

  _onSplitDragStart(e) {
    const pt = e.touches ? e.touches[0] : e;
    if (this._isNearSplitArrow(pt.clientX, pt.clientY)) {
      this._draggingSplit = true;
      if (e.preventDefault) e.preventDefault();
      // Attach to window so drag continues outside canvas
      this._windowMoveHandler = (ev) => this._onSplitDragMove(ev);
      this._windowUpHandler = () => this._onSplitDragEnd();
      window.addEventListener("mousemove", this._windowMoveHandler);
      window.addEventListener("mouseup", this._windowUpHandler);
    }
  }

  _onSplitDragMove(e) {
    const pt = e.touches ? e.touches[0] : e;
    if (this._draggingSplit) {
      if (e.preventDefault) e.preventDefault();
      const newSplit = this._splitFromX(pt.clientX);
      if (newSplit !== this._splitPoint) {
        this._splitPoint = newSplit;
        localStorage.setItem("livechord_arranger_split", String(newSplit));
        this._cache = {};
        this._pianoCache = null;
        this._kbCache = null;
        this._lastWidth = 0;
        this._lastKbWidth = 0;
        this._activeChordName = null;
        this.prefetchData();
      }
      this._kbCanvas.style.cursor = "grabbing";
    } else {
      // Show grab cursor when hovering near the arrow
      const near = this._isNearSplitArrow(pt.clientX, pt.clientY);
      this._hoverSplit = near;
      this._kbCanvas.style.cursor = near ? "grab" : "default";
    }
  }

  _onSplitDragEnd() {
    if (this._draggingSplit) {
      this._draggingSplit = false;
      this._kbCanvas.style.cursor = "default";
      // Remove window-level drag listeners
      if (this._windowMoveHandler) {
        window.removeEventListener("mousemove", this._windowMoveHandler);
        window.removeEventListener("mouseup", this._windowUpHandler);
        this._windowMoveHandler = null;
        this._windowUpHandler = null;
      }
    }
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
    const fingeringMap = {};

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

    }

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

    // Split point: red downward arrow at top of keyboard (draggable)
    const ctx = canvas.getContext("2d");
    // Re-apply DPI scaling — draw88Piano resets ctx.setTransform to identity
    ctx.scale(dpr, dpr);
    const splitMidi = this._splitPoint;
    const splitKey = cache.whiteXs[splitMidi] || cache.blackXs[splitMidi];
    if (splitKey) {
      const sx = splitKey.x + splitKey.w;
      const arrowW = 10;
      const arrowH = 14;
      // Draw filled red triangle pointing down (hover=brighter, drag=brightest)
      const arrowColor = this._draggingSplit ? "rgba(255, 50, 50, 1)"
        : this._hoverSplit ? "rgba(255, 70, 70, 0.95)"
        : "rgba(255, 80, 80, 0.65)";
      ctx.fillStyle = arrowColor;
      if (this._draggingSplit || this._hoverSplit) {
        ctx.shadowColor = "rgba(255, 50, 50, 0.6)";
        ctx.shadowBlur = 8;
      }
      ctx.beginPath();
      ctx.moveTo(sx - arrowW, 0);
      ctx.lineTo(sx + arrowW, 0);
      ctx.lineTo(sx, arrowH);
      ctx.closePath();
      ctx.fill();
      ctx.shadowBlur = 0;
    }
  }
}
