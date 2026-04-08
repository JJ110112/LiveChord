/**
 * ArrangerInstrument — Yamaha PSR-SX900 編曲鍵盤教學
 *
 * LEFT PANEL:  Mini keyboard (C1 ~ split point) with static chord input highlighting.
 *              Shows which keys to press for Fingered chord input mode.
 * RIGHT PANEL: Piano-style keyboard waterfall for right-hand melody (split+1 ~ C6).
 *
 * Split point default: F#2 (MIDI 42), configurable C2–C3.
 */
class ArrangerInstrument {
  constructor(config, bridge) {
    this._config = config;
    this._b = bridge;
    this._activeChordName = null;
    this._activeIdx = -1;
    this._ghostChordName = null;
    this._ghostAlpha = 0;
    this._cache = {};          // chord name -> arranger voicing from API
    this._splitPoint = parseInt(localStorage.getItem("livechord_arranger_split") || "54");
    this._lhPianoCache = null; // offscreen canvas for LH mini keyboard
    this._rhPianoCache = null; // offscreen canvas for RH waterfall keyboard
    this._lastLhWidth = 0;
    this._lastRhWidth = 0;
    this._lastDrawn = null;
  }

  /* ---- Constants ---- */
  static DEFAULT_SPLIT = 54;   // F#2 (Yamaha octave: C3 = middle C = MIDI 60)
  static MIDI_LOW = 36;        // C1 (leftmost key on 61-key keyboard)
  static MIDI_HIGH = 96;       // C6

  /* ---- Finger color scheme (same as accordion/guitar) ---- */
  static FINGER_CLR = {
    1: "#ef5350",  // thumb  — red
    2: "#ff9800",  // index  — orange
    3: "#ffeb3b",  // middle — yellow
    4: "#66bb6a",  // ring   — green
    5: "#66bb6a",  // pinky  — green (same as ring for LH block chords)
  };

  /* ---- MIDI helpers ---- */
  static NOTE_NAMES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];
  static WHITE_SEMI = new Set([0,2,4,5,7,9,11]);

  static midiToName(midi) {
    // Yamaha octave convention: C3 = middle C = MIDI 60
    return ArrangerInstrument.NOTE_NAMES[midi % 12] + (Math.floor(midi / 12) - 2);
  }

  /* ---- Interface methods (same as AccordionInstrument) ---- */

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
    this._lhCanvas = document.querySelector(sel.chordInputCanvas);
    this._wfCanvas = document.querySelector(sel.waterfallCanvas);
    this._chordNameEl = document.querySelector(sel.chordName);
    this._lhHintEl = document.querySelector(sel.lhHint);
    this._rhHintEl = document.querySelector(sel.rhHint);
    this._splitSelect = document.querySelector(sel.splitPointSelect);

    if (this._splitSelect) {
      this._splitSelect.value = String(this._splitPoint);
      this._splitSelect.onchange = () => this._onSplitChange();
    }

    this._activeIdx = -1;
    this._activeChordName = null;
    this._lhPianoCache = null;
    this._rhPianoCache = null;
    this._lastLhWidth = 0;
    this._lastRhWidth = 0;

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
    const cacheKey = chordName + ":" + this._splitPoint;
    return this._cache[cacheKey] || null;
  }

  _onSplitChange() {
    const val = parseInt(this._splitSelect.value);
    if (isNaN(val)) return;
    this._splitPoint = val;
    localStorage.setItem("livechord_arranger_split", String(val));
    // Invalidate caches
    this._cache = {};
    this._lhPianoCache = null;
    this._rhPianoCache = null;
    this._lastLhWidth = 0;
    this._lastRhWidth = 0;
    this._activeChordName = null;
    this.prefetchData();
    requestAnimationFrame(() => {
      const t = this._b.getAudio().currentTime || 0;
      this.update(t);
    });
  }

  /* ---- Main update (called every frame) ---- */

  update(currentTime) {
    const chords = this._b.getDisplayChords();
    if (!chords || !chords.length) {
      this._drawMelodyWaterfall(currentTime);
      return;
    }

    let idx = -1;
    for (let i = chords.length - 1; i >= 0; i--) {
      if (currentTime >= chords[i].time) { idx = i; break; }
    }
    if (idx < 0) idx = 0;

    const chordName = chords[idx].chord;

    // Ghost chord (2 seconds before next chord)
    let ghostName = null;
    let ghostAlpha = 0;
    if (idx + 1 < chords.length) {
      const nextTime = chords[idx + 1].time;
      const diff = nextTime - currentTime;
      if (diff > 0 && diff <= 2.0) {
        ghostName = chords[idx + 1].chord;
        ghostAlpha = Math.sin((1 - diff / 2.0) * Math.PI) * 0.5;
      }
    }

    // Update chord name / hints on chord change
    if (chordName !== this._activeChordName) {
      this._activeChordName = chordName;
      this._activeIdx = idx;
      if (this._chordNameEl) this._chordNameEl.textContent = chordName || "--";
      this._updateHints(chordName);
    }
    this._ghostChordName = ghostName;
    this._ghostAlpha = ghostAlpha;

    // Render both panels
    this.renderLhChordInput(chordName, ghostName, ghostAlpha);
    this._drawMelodyWaterfall(currentTime);
  }

  /* ======================================================================
   * LEFT PANEL: Mini keyboard with static chord input highlighting
   * ====================================================================== */

  renderLhChordInput(chordName, ghostName, ghostAlpha) {
    const canvas = this._lhCanvas;
    if (!canvas || !canvas.parentElement) return;
    const rect = canvas.parentElement.getBoundingClientRect();
    if (rect.width < 10 || rect.height < 50) return;
    const dpr = window.devicePixelRatio || 1;
    const W = rect.width;
    const H = Math.max(rect.height - 40, 60);
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    canvas.style.width = W + "px";
    canvas.style.height = H + "px";
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, W, H);

    const split = this._splitPoint;
    const midiLo = ArrangerInstrument.MIDI_LOW;

    // Build key layout
    const whites = [];
    for (let m = midiLo; m <= split; m++) {
      if (ArrangerInstrument.WHITE_SEMI.has(m % 12)) whites.push(m);
    }
    if (whites.length === 0) return;

    const nw = whites.length;
    const kw = W / nw;
    const kh = Math.min(H * 0.65, kw * 6);
    const bw = kw * 0.52;
    const bh = kh * 0.62;
    const keyTop = H - kh;

    // Draw background
    ctx.fillStyle = "#1a1a1a";
    ctx.fillRect(0, keyTop, W, kh);

    // White keys
    const gapW = Math.max(1, kw * 0.04);
    const whiteXs = {};
    for (let i = 0; i < nw; i++) {
      const x = i * kw + gapW / 2;
      const keyW = kw - gapW;
      const gradient = ctx.createLinearGradient(x, keyTop, x, keyTop + kh);
      gradient.addColorStop(0, "#e8e6e1"); gradient.addColorStop(0.15, "#fefefe");
      gradient.addColorStop(0.5, "#ffffff"); gradient.addColorStop(0.85, "#f8f7f3");
      gradient.addColorStop(1, "#e0ddd7");
      ctx.fillStyle = gradient;
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(x, keyTop, keyW, kh, [0, 0, 3, 3]);
      else ctx.rect(x, keyTop, keyW, kh);
      ctx.fill();
      // Side shadows
      ctx.fillStyle = "rgba(0,0,0,0.10)";
      ctx.fillRect(x, keyTop, kw * 0.06, kh);
      whiteXs[whites[i]] = { x, w: keyW, h: kh };
    }

    // Black keys
    const blackAfterSemi = new Set([0, 1, 3, 4, 5]); // C, C#, D#, E, F
    const whiteOrder = [0,2,4,5,7,9,11];
    const blackXs = {};
    for (let i = 0; i < nw - 1; i++) {
      const deg = whites[i] % 12;
      const wIdx = whiteOrder.indexOf(deg);
      if (!blackAfterSemi.has(wIdx)) continue;
      const bMidi = whites[i] + 1;
      if (bMidi > split) continue;
      const x = (i + 1) * kw - bw / 2;
      ctx.fillStyle = "#111";
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(x, keyTop, bw, bh, [0, 0, 3, 3]);
      else ctx.rect(x, keyTop, bw, bh);
      ctx.fill();
      // Subtle gradient
      const bgGrad = ctx.createLinearGradient(x, keyTop, x, keyTop + bh);
      bgGrad.addColorStop(0, "#0a0a0a"); bgGrad.addColorStop(0.7, "#1a1a1a");
      bgGrad.addColorStop(1, "#333");
      ctx.fillStyle = bgGrad;
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(x, keyTop, bw, bh, [0, 0, 3, 3]);
      else ctx.rect(x, keyTop, bw, bh);
      ctx.fill();
      blackXs[bMidi] = { x, w: bw, h: bh };
    }

    // --- Highlight chord keys ---
    const resolved = this._resolveChord(chordName);
    if (resolved && resolved.midi_notes) {
      this._highlightKeys(ctx, resolved.midi_notes, resolved.fingering, whiteXs, blackXs, keyTop, kh, bh, 1.0);
    }

    // --- Ghost chord preview ---
    if (ghostName && ghostAlpha > 0.02) {
      const ghostResolved = this._resolveChord(ghostName);
      if (ghostResolved && ghostResolved.midi_notes) {
        this._highlightKeys(ctx, ghostResolved.midi_notes, ghostResolved.fingering, whiteXs, blackXs, keyTop, kh, bh, ghostAlpha, true);
      }
    }

    // --- Note labels below keys ---
    ctx.fillStyle = "#888";
    ctx.font = `${Math.max(9, Math.min(12, kw * 0.7))}px sans-serif`;
    ctx.textAlign = "center";
    for (let i = 0; i < nw; i++) {
      const m = whites[i];
      if (m % 12 === 0) { // C notes — Yamaha octave (C3 = middle C)
        const oct = Math.floor(m / 12) - 2;
        const info = whiteXs[m];
        ctx.fillText("C" + oct, info.x + info.w / 2, keyTop + kh + 14);
      }
    }

    // Split point indicator
    ctx.strokeStyle = "rgba(255, 100, 100, 0.6)";
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(W - 1, keyTop);
    ctx.lineTo(W - 1, keyTop + kh);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(255, 100, 100, 0.7)";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "right";
    ctx.fillText("Split", W - 4, keyTop - 4);

    // "Fingered" mode label at top
    if (resolved && resolved.available === false && resolved.warning) {
      ctx.fillStyle = "rgba(255, 180, 0, 0.8)";
      ctx.font = "12px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(resolved.warning, W / 2, keyTop - 8);
    }
  }

  /**
   * Highlight chord keys on the mini keyboard with finger colors.
   */
  _highlightKeys(ctx, midiNotes, fingering, whiteXs, blackXs, keyTop, kh, bh, alpha, isGhost) {
    ctx.save();
    ctx.globalAlpha = alpha;

    for (let n = 0; n < midiNotes.length; n++) {
      const midi = midiNotes[n];
      const finger = fingering && fingering[n] ? fingering[n] : 1;
      const color = ArrangerInstrument.FINGER_CLR[finger] || "#ff9800";

      // Check if white or black key
      const wk = whiteXs[midi];
      const bk = blackXs[midi];

      if (wk) {
        // White key highlight
        ctx.fillStyle = color;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(wk.x + 1, keyTop + 1, wk.w - 2, kh - 2, [0, 0, 4, 4]);
        else ctx.rect(wk.x + 1, keyTop + 1, wk.w - 2, kh - 2);
        ctx.fill();
        // Top wash for 3D
        const topWash = ctx.createLinearGradient(0, keyTop, 0, keyTop + kh * 0.4);
        topWash.addColorStop(0, "rgba(255,255,255,0.3)");
        topWash.addColorStop(1, "rgba(255,255,255,0)");
        ctx.fillStyle = topWash;
        ctx.fillRect(wk.x + 1, keyTop + 1, wk.w - 2, kh * 0.4);

        if (isGhost) {
          // Dashed outline for ghost
          ctx.strokeStyle = color;
          ctx.lineWidth = 2;
          ctx.setLineDash([3, 3]);
          ctx.beginPath();
          if (ctx.roundRect) ctx.roundRect(wk.x + 1, keyTop + 1, wk.w - 2, kh - 2, [0, 0, 4, 4]);
          else ctx.rect(wk.x + 1, keyTop + 1, wk.w - 2, kh - 2);
          ctx.stroke();
          ctx.setLineDash([]);
        }

        // Finger number in circled digit
        ctx.fillStyle = "rgba(0,0,0,0.7)";
        ctx.font = `bold ${Math.max(14, Math.min(20, wk.w * 0.6))}px sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        const circled = String.fromCodePoint(0x2460 + finger - 1); // ①②③④⑤
        ctx.fillText(circled, wk.x + wk.w / 2, keyTop + kh * 0.65);
      }

      if (bk) {
        // Black key highlight
        ctx.fillStyle = color;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(bk.x + 0.5, keyTop + 0.5, bk.w - 1, bh - 1, [0, 0, 3, 3]);
        else ctx.rect(bk.x + 0.5, keyTop + 0.5, bk.w - 1, bh - 1);
        ctx.fill();

        if (isGhost) {
          ctx.strokeStyle = color;
          ctx.lineWidth = 2;
          ctx.setLineDash([3, 3]);
          ctx.beginPath();
          if (ctx.roundRect) ctx.roundRect(bk.x + 0.5, keyTop + 0.5, bk.w - 1, bh - 1, [0, 0, 3, 3]);
          else ctx.rect(bk.x + 0.5, keyTop + 0.5, bk.w - 1, bh - 1);
          ctx.stroke();
          ctx.setLineDash([]);
        }

        // Finger number
        ctx.fillStyle = "rgba(255,255,255,0.9)";
        ctx.font = `bold ${Math.max(11, Math.min(16, bk.w * 0.6))}px sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        const circled = String.fromCodePoint(0x2460 + finger - 1);
        ctx.fillText(circled, bk.x + bk.w / 2, keyTop + bh * 0.55);
      }
    }

    ctx.restore();
  }

  /* ======================================================================
   * RIGHT PANEL: Melody waterfall (reuses accordion's waterfall pattern)
   * ====================================================================== */

  _drawMelodyWaterfall(currentTime) {
    const canvas = this._wfCanvas;
    if (!canvas || !canvas.parentElement) return;
    const rect = canvas.parentElement.getBoundingClientRect();
    if (rect.width < 10 || rect.height < 50) return;
    const dpr = window.devicePixelRatio || 1;
    const W = rect.width;
    const H = rect.height;
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    canvas.style.width = W + "px";
    canvas.style.height = H + "px";
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, W, H);

    // Initialize or rebuild piano cache on width/split change
    const ChordRender = this._b.ChordRender;
    const rhLo = this._splitPoint + 1;
    const rhHi = ArrangerInstrument.MIDI_HIGH;
    if (!this._rhPianoCache || Math.abs(this._lastRhWidth - W) > 2) {
      this._rhPianoCache = ChordRender.initRangePianoCache(W, dpr, rhLo, rhHi);
      this._lastRhWidth = W;
    }
    const cache = this._rhPianoCache;

    // Cap keyboard to ~25% of panel height
    const maxPianoH = Math.round(H * 0.25);
    const pianoH = Math.min(cache.totalH, maxPianoH);
    const waterfallH = H - pianoH;

    if (waterfallH < 20) {
      ctx.drawImage(cache.canvas, 0, 0, cache.canvas.width, cache.canvas.height,
                    0, H - pianoH, W, pianoH);
      return;
    }

    // Draw vertical piano key grid lines in the waterfall area
    ctx.strokeStyle = "rgba(255, 255, 255, 0.05)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    let lastKey = null;
    for (const p in cache.whiteXs) {
      const wk = cache.whiteXs[p];
      ctx.moveTo(wk.x, 0);
      ctx.lineTo(wk.x, waterfallH);
      lastKey = wk;
    }
    if (lastKey) {
      ctx.moveTo(lastKey.x + lastKey.w, 0);
      ctx.lineTo(lastKey.x + lastKey.w, waterfallH);
    }
    ctx.stroke();

    // Beat grid (chord-based)
    const lookAhead = 4.0;
    const pxPerSec = waterfallH / lookAhead;
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
          const y = waterfallH - (bt - currentTime) * pxPerSec;
          if (y < 0 || y > waterfallH) continue;
          const isBarLine = (b === 0);
          ctx.strokeStyle = isBarLine ? "rgba(255,255,255,0.25)" : "rgba(255,255,255,0.08)";
          ctx.lineWidth = isBarLine ? 2 : 1;
          ctx.beginPath();
          ctx.moveTo(0, y);
          ctx.lineTo(W, y);
          ctx.stroke();
          ctx.fillStyle = isBarLine ? "rgba(255,255,255,0.6)" : "rgba(255,255,255,0.3)";
          ctx.fillText(b + 1, 8, y - 2);
        }
      }
    }

    // Right-hand note events (accompaniment right_hand or melody)
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

    // Filter to only notes above split point
    const splitPt = this._splitPoint;
    const activeKeys = new Set();

    if (rhEvents.length > 0) {
      ctx.font = "bold 11px sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";

      for (const evt of rhEvents) {
        if (evt.pitch <= splitPt) continue; // Skip LH notes
        const noteStart = evt.time;
        const noteEnd = evt.time + evt.duration;
        if (noteEnd < currentTime || noteStart > currentTime + lookAhead) continue;

        const yBottom = waterfallH - (noteStart - currentTime) * pxPerSec;
        const yTop = waterfallH - (noteEnd - currentTime) * pxPerSec;
        const noteH = Math.max(yBottom - yTop, 3);

        const midi = evt.pitch;
        const keyInfo = cache.whiteXs[midi] || cache.blackXs[midi];
        if (!keyInfo) continue;
        const x = keyInfo.x;
        const kw = keyInfo.w;
        const isOnBlackKey = !!cache.blackXs[midi];

        // Velocity-responsive orange color
        const vel = evt.velocity || 80;
        const velT = Math.min(1.0, Math.max(0.0, (vel - 55) / 40));
        const velP = velT * velT;
        const cr = Math.round(100 + velP * 155);
        const cg = Math.round(40 + velP * 170);
        const cb = Math.round(0 + velP * 50);
        const color = `rgba(${cr}, ${cg}, ${cb}, ${isOnBlackKey ? 0.95 : 0.9})`;
        const glowColor = `rgba(255, ${Math.min(255, cg + 60)}, ${Math.min(255, cb + 80)}, 1)`;

        // Prediction shadow
        if (yBottom > waterfallH - 40 && yBottom < waterfallH) {
          ctx.fillStyle = "rgba(255, 152, 0, 0.4)";
          ctx.fillRect(x, waterfallH - 5, kw, -20);
        }

        // Note bar
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
        if (currentTime >= evt.time && currentTime < evt.time + evt.duration) {
          activeKeys.add(midi);
        }

        // Contact flash
        if (yBottom >= waterfallH && yTop <= waterfallH) {
          ctx.save();
          ctx.fillStyle = color;
          ctx.shadowColor = glowColor;
          ctx.shadowBlur = 8 + velP * 22;
          ctx.fillRect(x + 1, waterfallH - 4, kw - 2, 8);
          ctx.fillStyle = `rgba(255,255,255,${0.3 + velP * 0.6})`;
          ctx.shadowBlur = velP * 15;
          ctx.shadowColor = "#fff";
          ctx.fillRect(x + 3, waterfallH - 2, kw - 6, 4);
          ctx.restore();
        }

        // Articulation markers
        if (evt.articulation === "staccato") {
          ctx.fillStyle = "#fff";
          ctx.beginPath();
          ctx.arc(x + kw / 2, yBottom - 4, 2.5, 0, Math.PI * 2);
          ctx.fill();
        } else if (evt.articulation === "legato" && noteH > 12) {
          ctx.strokeStyle = "rgba(255,255,255,0.3)";
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.arc(x + kw / 2, yTop, kw * 0.4, Math.PI, 0);
          ctx.stroke();
        }
      }
    }

    // Draw piano keyboard at the bottom
    ctx.drawImage(cache.canvas, 0, 0, cache.canvas.width, cache.canvas.height,
                  0, waterfallH, W, pianoH);

    // Highlight active keys
    if (activeKeys.size > 0) {
      const kh = cache.keyH;
      const bkh = cache.bKeyH;
      const RH_COLOR = "rgba(255, 152, 0, 0.9)";

      // Pass 1: White key highlights
      for (const midi of activeKeys) {
        const wk = cache.whiteXs[midi];
        if (!wk) continue;
        ctx.save();
        ctx.globalAlpha = 0.9;
        ctx.fillStyle = RH_COLOR;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(wk.x + 0.5, waterfallH + 0.5, wk.w - 1, kh - 1, [0, 0, 4, 4]);
        else ctx.rect(wk.x + 0.5, waterfallH + 0.5, wk.w - 1, kh - 1);
        ctx.fill();
        const topWash = ctx.createLinearGradient(0, waterfallH, 0, waterfallH + kh * 0.5);
        topWash.addColorStop(0, "rgba(255,255,255,0.25)");
        topWash.addColorStop(1, "rgba(255,255,255,0)");
        ctx.fillStyle = topWash;
        ctx.fillRect(wk.x + 0.5, waterfallH + 0.5, wk.w - 1, kh * 0.5);
        ctx.shadowColor = RH_COLOR;
        ctx.shadowBlur = 15;
        ctx.fillStyle = RH_COLOR;
        ctx.fillRect(wk.x + 2, waterfallH + kh - 6, wk.w - 4, 6);
        ctx.restore();
      }

      // Pass 2: Black key highlights
      for (const midi of activeKeys) {
        const bk = cache.blackXs[midi];
        if (!bk) continue;
        ctx.save();
        ctx.globalAlpha = 0.9;
        ctx.fillStyle = RH_COLOR;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(bk.x, waterfallH, bk.w, bkh, [0, 0, 3, 3]);
        else ctx.rect(bk.x, waterfallH, bk.w, bkh);
        ctx.fill();
        const hlGrad = ctx.createLinearGradient(bk.x, waterfallH, bk.x, waterfallH + bkh * 0.3);
        hlGrad.addColorStop(0, "rgba(255,255,255,0.3)");
        hlGrad.addColorStop(1, "rgba(255,255,255,0)");
        ctx.fillStyle = hlGrad;
        ctx.fillRect(bk.x + bk.w * 0.1, waterfallH, bk.w * 0.8, bkh * 0.3);
        ctx.shadowColor = RH_COLOR;
        ctx.shadowBlur = 12;
        ctx.fillStyle = RH_COLOR;
        ctx.fillRect(bk.x + 1, waterfallH + bkh - 4, bk.w - 2, 4);
        ctx.restore();
      }
    }
  }

  /* ---- Hints ---- */

  _updateHints(chordName) {
    const resolved = this._resolveChord(chordName);
    if (!resolved || !resolved.available) {
      if (this._lhHintEl) {
        this._lhHintEl.textContent = resolved && resolved.warning
          ? resolved.warning
          : "左手：和弦輸入 (Fingered)";
      }
      return;
    }
    const notes = resolved.midi_notes.map(m => ArrangerInstrument.midiToName(m));
    if (this._lhHintEl) {
      this._lhHintEl.textContent = `按鍵: ${notes.join(" + ")}`;
    }
  }
}
