/**
 * AccordionInstrument — 手風琴左手 Stradella 低音系統教學 + 右手鍵盤瀑布流
 *
 * LEFT PANEL:  3 rows × 7 columns Stradella bass grid with finger color indicators
 *              and ghost chord preview (guitar-style).
 * RIGHT PANEL: Piano-style keyboard waterfall for right-hand (F3–C6, MIDI 53–84).
 *
 * Columns (circle of fifths): Bb, F, C, G, D, A, E
 * Rows: 0=Bass (single notes), 1=Major chords, 2=Minor chords
 * C bass (col 2, row 0) has a concave dimple for tactile reference.
 */
class AccordionInstrument {
  constructor(config, bridge) {
    this._config = config;
    this._b = bridge;
    this._activeChordName = null;
    this._activeIdx = -1;
    this._ghostChordName = null;
    this._ghostAlpha = 0;
    this._cache = {};          // chord name -> bass mapping from API
    this._bassPattern = localStorage.getItem("livechord_acc_pattern") || "bass_chord";
    this._lastDrawn = null;
    this._pianoCache = null;
    this._lastWfWidth = 0;
  }

  /* ---- Stradella constants ---- */
  static COLS = ["Bb", "F", "C", "G", "D", "A", "E"];
  static ROW_LABELS = ["Bass", "Major", "Minor"];
  static DIMPLE_COL = 2; // C

  static ENHARMONIC = {
    "A#": "Bb", "Cb": "B", "B#": "C", "Fb": "E", "E#": "F",
    "Gb": "F#", "G#": "Ab", "D#": "Eb", "C#": "Db",
  };
  static COL_IDX = (() => {
    const m = {};
    AccordionInstrument.COLS.forEach((c, i) => m[c] = i);
    return m;
  })();

  /* ---- Finger color constants (guitar color scheme) ---- */
  static FINGER_CLR = { 1: "#ef5350", 2: "#ff9800", 3: "#ffeb3b", 4: "#66bb6a" };
  static ROW_FINGER = { 0: 2, 1: 3, 2: 4 }; // Bass=finger2(orange), Major=finger3(yellow), Minor=finger4(green)

  /* ---- Display mapping ---- */
  // Map display column index (0=left .. 2=right) to data row index
  static DISP_TO_ROW = [2, 1, 0];           // Minor, Major, Bass
  static DISP_LABELS = ["Minor", "Major", "Bass"];

  /* ---- Interface methods (same as StringInstrument) ---- */

  init() {
    // Retry if chord data not loaded yet (same pattern as StringInstrument)
    const chordData = this._b.getChordData();
    if (!chordData || !chordData.chords || chordData.chords.length === 0) {
      const self = this;
      setTimeout(() => {
        if (this._b.getActiveTab() === this._config.id) self.init();
      }, 1000);
      return;
    }

    const sel = this._config.selectors;
    this._gridCanvas = document.querySelector(sel.bassGridCanvas);
    this._wfCanvas = document.querySelector(sel.waterfallCanvas);
    this._chordNameEl = document.querySelector(sel.chordName);
    this._lhHintEl = document.querySelector(sel.lhHint);
    this._rhHintEl = document.querySelector(sel.rhHint);
    this._patternLabel = document.querySelector(sel.patternLabel);
    this._patternSelect = document.querySelector(sel.patternSelect);

    if (this._patternSelect) {
      this._patternSelect.value = this._bassPattern;
      this._patternSelect.onchange = () => {
        this._bassPattern = this._patternSelect.value;
        localStorage.setItem("livechord_acc_pattern", this._bassPattern);
        if (this._patternLabel) {
          this._patternLabel.textContent = this._patternSelect.options[this._patternSelect.selectedIndex].text;
        }
      };
    }
    if (this._patternLabel && this._patternSelect) {
      this._patternLabel.textContent = this._patternSelect.options[this._patternSelect.selectedIndex].text;
    }

    this._activeIdx = -1;
    this._activeChordName = null;
    this._pianoCache = null;
    this._lastWfWidth = 0;

    this.prefetchData();
    // Defer first render so container layout is computed
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
    const API = this._b.API;
    await Promise.all(names.map(async (name) => {
      if (this._cache[name]) return;
      try {
        const d = await API.chordDiagram("accordion", name);
        this._cache[name] = d;
      } catch { this._cache[name] = null; }
    }));
  }

  /* ---- Bass pattern definitions ---- */
  static PATTERNS = {
    bass_chord:       { beats: 4, steps: ["B","C","B","C"] },
    alternating_bass: { beats: 4, steps: ["B","C","Ab","C"] },
    waltz:            { beats: 3, steps: ["B","C","C"] },
    march:            { beats: 2, steps: ["B","C"] },
  };

  /** Compute the current beat step (B/C/Ab) from the pattern */
  _currentStep(currentTime) {
    const pat = AccordionInstrument.PATTERNS[this._bassPattern]
             || AccordionInstrument.PATTERNS.bass_chord;
    const accData = this._b.getAccData ? this._b.getAccData() : null;
    const bpm = (accData && accData.bpm) ? accData.bpm : 120;
    const beatDur = 60.0 / bpm;

    const chords = this._b.getDisplayChords();
    if (!chords || !chords.length) return "B";

    // Find current chord start
    let chordStart = 0;
    for (let i = chords.length - 1; i >= 0; i--) {
      if (currentTime >= chords[i].time) { chordStart = chords[i].time; break; }
    }

    const elapsed = currentTime - chordStart;
    const beatIdx = Math.floor(elapsed / beatDur);
    const stepIdx = beatIdx % pat.steps.length;
    return pat.steps[Math.max(0, stepIdx)];
  }

  update(currentTime) {
    const chords = this._b.getDisplayChords();
    if (!chords || !chords.length) {
      this._drawKeyboardWaterfall(currentTime);
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

    // Current beat step for bass pattern visualization
    const step = this._currentStep(currentTime);

    // Always update chord name / hints on chord change
    if (chordName !== this._activeChordName) {
      this._activeChordName = chordName;
      this._activeIdx = idx;
      if (this._chordNameEl) this._chordNameEl.textContent = chordName || "--";
      this._updateHints(chordName);
    }
    this._ghostChordName = ghostName;
    this._ghostAlpha = ghostAlpha;

    // Re-render bass grid every frame (step changes continuously)
    this.renderBassGrid(chordName, ghostName, ghostAlpha, step);

    this._drawKeyboardWaterfall(currentTime);
  }

  /* ---- Chord -> button resolution (client-side mirror of backend) ---- */

  _resolveChord(chordName) {
    if (this._cache[chordName]) return this._cache[chordName];
    // Client-side fallback
    const m = chordName.match(/^([A-G][b#]?)(.*)/);
    if (!m) return null;
    let root = m[1], suffix = m[2];
    const isMinor = /^m($|[^a])/.test(suffix);
    const row = isMinor ? 2 : 1;
    const norm = AccordionInstrument.ENHARMONIC[root] || root;
    const col = AccordionInstrument.COL_IDX[norm];
    if (col === undefined) {
      // Fallback: use the fifth as bass (mirrors backend _fifth_map)
      const fifthMap = { B: "E", Eb: "Bb", Ab: "Bb", Db: "F", "F#": "D" };
      const fb = fifthMap[norm];
      const fbCol = fb ? AccordionInstrument.COL_IDX[fb] : undefined;
      if (fbCol !== undefined) {
        return {
          buttons: [
            { col: fbCol, row: 0, label: fb },
            { col: fbCol, row, label: chordName },
          ],
          altBass: null,
          available: false,
          warning: `21鍵無 ${root} 低音，以 ${fb} 代替`,
        };
      }
      return { buttons: [], available: false, warning: `21鍵無法演奏 ${root}` };
    }
    const altCol = col < 6 ? col + 1 : null;
    return {
      buttons: [
        { col, row: 0, label: norm },
        { col, row, label: chordName },
      ],
      altBass: altCol !== null ? { col: altCol, row: 0, label: AccordionInstrument.COLS[altCol] } : null,
      available: true,
    };
  }

  /* ---- Bass Grid Canvas Rendering (enhanced with finger colors & ghost) ---- */
  /*
   * Physical layout (player's view, looking down at left hand):
   *   Vertical axis  = circle-of-fifths keys, E(top) → Bb(bottom)
   *   Horizontal axis = Minor(left) → Major(center) → Bass(right, near bellows)
   *   Alternating columns offset down by half a row (Stradella diagonal)
   */

  renderBassGrid(chordName, ghostName, ghostAlpha, step) {
    const canvas = this._gridCanvas;
    if (!canvas || !canvas.parentElement) return;
    const rect = canvas.parentElement.getBoundingClientRect();
    if (rect.width < 10 || rect.height < 50) return;
    const dpr = window.devicePixelRatio || 1;
    const drawH = Math.max(rect.height - 40, 60);
    canvas.width = rect.width * dpr;
    canvas.height = drawH * dpr;
    canvas.style.width = rect.width + "px";
    canvas.style.height = drawH + "px";
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    const W = rect.width, H = drawH;
    ctx.clearRect(0, 0, W, H);

    const COLS = AccordionInstrument.COLS;
    const D2R = AccordionInstrument.DISP_TO_ROW;
    const DLABELS = AccordionInstrument.DISP_LABELS;
    const FCLR = AccordionInstrument.FINGER_CLR;
    const RFINGER = AccordionInstrument.ROW_FINGER;
    const nKeys = COLS.length;  // 7
    const nTypes = 3;

    const keysTopDown = [...COLS].reverse();

    // Layout — compact columns with reduced horizontal spacing
    const padTop = 20, padBot = 16;
    const headerH = 16;
    const maxColW = 80; // cap column width for tighter layout
    const naturalColW = (W - 16) / nTypes;
    const colW = Math.min(naturalColW, maxColW);
    const totalGridW = colW * nTypes;
    const gridLeft = (W - totalGridW) / 2; // center the grid
    const areaH = H - padTop - padBot - headerH;
    const rowH = areaH / (nKeys + 1.0);
    const btnR = Math.min(colW, rowH) * 0.36;
    const offsetY = rowH * 0.5;

    const active = this._resolveChord(chordName);
    const ghost = ghostName ? this._resolveChord(ghostName) : null;

    // Determine which rows are "playing now" based on bass pattern step
    // step: "B" = bass row only, "C" = chord row only, "Ab" = alt bass only
    const stepIsBass = (step === "B");
    const stepIsChord = (step === "C");
    const stepIsAlt = (step === "Ab");

    // Column headers with finger colors — positioned just above the first button row
    ctx.font = "bold 11px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    for (let d = 0; d < nTypes; d++) {
      const row = D2R[d];
      const finger = RFINGER[row];
      ctx.fillStyle = FCLR[finger];
      ctx.fillText(DLABELS[d], gridLeft + (d + 0.5) * colW, padTop + headerH - 2);
    }

    // Draw buttons
    for (let ki = 0; ki < nKeys; ki++) {
      const keyName = keysTopDown[ki];
      const col = COLS.indexOf(keyName);

      for (let d = 0; d < nTypes; d++) {
        const row = D2R[d]; // data row: 0=Bass, 1=Major, 2=Minor
        const finger = RFINGER[row];
        const fingerColor = FCLR[finger];
        const dy = d * offsetY;
        const cx = gridLeft + (d + 0.5) * colW;
        const cy = padTop + headerH + (ki + 0.5) * rowH + dy;

        // Match against chord resolution data
        let isChordBtn = false, isAltBass = false, isGhost = false;
        if (active && active.buttons) {
          for (const btn of active.buttons) {
            if (btn.col === col && btn.row === row) isChordBtn = true;
          }
          if (active.altBass && active.altBass.col === col && row === 0) isAltBass = true;
        }
        if (ghost && ghost.buttons && ghostAlpha > 0.05) {
          for (const btn of ghost.buttons) {
            if (btn.col === col && btn.row === row) isGhost = true;
          }
        }

        // Determine if this button is "playing now" based on beat step
        let isPlaying = false;
        if (isChordBtn) {
          if (row === 0 && stepIsBass) isPlaying = true;       // Bass button on B step
          if (row !== 0 && stepIsChord) isPlaying = true;      // Chord button on C step
        }
        if (isAltBass && stepIsAlt) isPlaying = true;          // Alt bass on Ab step
        // Also dim the chord buttons: show them as "belongs to chord" but not currently playing
        const belongsToChord = isChordBtn && !isPlaying;

        // Button circle
        ctx.beginPath();
        ctx.arc(cx, cy, btnR, 0, Math.PI * 2);

        if (isPlaying) {
          // Bright: currently playing this beat step
          ctx.fillStyle = fingerColor;
          ctx.fill();
          ctx.save();
          ctx.shadowColor = fingerColor;
          ctx.shadowBlur = 14;
          ctx.beginPath();
          ctx.arc(cx, cy, btnR, 0, Math.PI * 2);
          ctx.fill();
          ctx.restore();
          ctx.strokeStyle = "#fff";
          ctx.lineWidth = 2;
          ctx.stroke();
        } else if (belongsToChord) {
          // Dimmed: belongs to chord but not the active beat step
          ctx.fillStyle = fingerColor;
          ctx.globalAlpha = 0.3;
          ctx.fill();
          ctx.globalAlpha = 1;
          ctx.strokeStyle = fingerColor;
          ctx.lineWidth = 1.5;
          ctx.stroke();
        } else if (isGhost) {
          ctx.save();
          ctx.setLineDash(ghostAlpha > 0.6 ? [] : [4, 3]);
          ctx.globalAlpha = ghostAlpha * 0.5;
          ctx.fillStyle = fingerColor;
          ctx.fill();
          ctx.globalAlpha = ghostAlpha;
          ctx.strokeStyle = fingerColor;
          ctx.lineWidth = 2.5;
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.restore();
        } else if (isAltBass && !stepIsAlt) {
          // Alt bass dimmed when not on Ab step
          ctx.fillStyle = "rgba(255,152,0,0.15)";
          ctx.fill();
          ctx.strokeStyle = "rgba(255,255,255,0.15)";
          ctx.lineWidth = 1;
          ctx.stroke();
        } else {
          ctx.fillStyle = "rgba(60,60,70,0.7)";
          ctx.fill();
          ctx.strokeStyle = "rgba(255,255,255,0.2)";
          ctx.lineWidth = 1;
          ctx.stroke();
        }

        // Ghost "keep" indicator
        if (isGhost && active && active.buttons) {
          let activeAtSamePos = false;
          for (const btn of active.buttons) {
            if (btn.col === col && btn.row === row) { activeAtSamePos = true; break; }
          }
          if (activeAtSamePos) {
            ctx.save();
            ctx.globalAlpha = ghostAlpha;
            ctx.strokeStyle = "#fff";
            ctx.lineWidth = 2.5;
            ctx.beginPath();
            ctx.arc(cx, cy, btnR * 1.25, 0, Math.PI * 2);
            ctx.stroke();
            ctx.fillStyle = "#00e676";
            ctx.globalAlpha = Math.min(ghostAlpha + 0.2, 1);
            ctx.beginPath();
            ctx.arc(cx + btnR * 0.85, cy - btnR * 0.85, btnR * 0.3, 0, Math.PI * 2);
            ctx.fill();
            ctx.restore();
          }
        }

        // Dimple on C bass
        if (col === AccordionInstrument.DIMPLE_COL && row === 0) {
          ctx.beginPath();
          ctx.arc(cx, cy, btnR * 0.3, 0, Math.PI * 2);
          ctx.strokeStyle = isPlaying ? "rgba(255,255,255,0.6)" : "rgba(255,255,255,0.25)";
          ctx.lineWidth = 1.5;
          ctx.stroke();
        }

        // Label + finger number
        if (isPlaying) {
          ctx.fillStyle = finger === 3 ? "#333" : "#fff";
          ctx.font = `bold ${Math.round(btnR * 1.0)}px sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(finger, cx, cy);
        } else {
          ctx.fillStyle = isGhost ? fingerColor : (belongsToChord ? fingerColor : "#bbb");
          ctx.globalAlpha = isGhost ? ghostAlpha : (belongsToChord ? 0.6 : 1);
          ctx.font = "11px sans-serif";
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          const label = row === 0 ? keyName : (row === 1 ? keyName : keyName + "m");
          ctx.fillText(label, cx, cy);
          ctx.globalAlpha = 1;
        }
      }
    }

    // Warning
    if (active && !active.available && active.warning) {
      ctx.fillStyle = "rgba(255,80,80,0.9)";
      ctx.font = "bold 13px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(active.warning, W / 2, H - 4);
    }
  }

  /* ---- Keyboard Waterfall (Right Hand, Piano-style) ---- */

  _drawKeyboardWaterfall(currentTime) {
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

    // Initialize or rebuild piano cache on width change
    const ChordRender = this._b.ChordRender;
    if (!this._pianoCache || Math.abs(this._lastWfWidth - W) > 2) {
      this._pianoCache = ChordRender.initAccordionPianoCache(W, dpr);
      this._lastWfWidth = W;
    }
    const cache = this._pianoCache;
    const pianoH = cache.totalH;
    const waterfallH = H - pianoH; // area above the keyboard for falling notes

    if (waterfallH < 20) {
      // Not enough space for waterfall, just draw the keyboard
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

    // Right-hand note events (fallback to melody if no accompaniment right_hand)
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
    const activeKeys = new Set(); // collect currently-sounding MIDI notes for keyboard highlight
    if (rhEvents.length > 0) {
      ctx.font = "bold 11px sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";

      for (const evt of rhEvents) {
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

        // Prediction shadow on keys
        if (yBottom > waterfallH - 40 && yBottom < waterfallH) {
          ctx.fillStyle = "rgba(255, 152, 0, 0.4)";
          ctx.fillRect(x, waterfallH - 5, kw, -20);
        }

        // Note bar with velocity glow
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

        // Dim outline for very quiet notes
        if (velP < 0.15) {
          ctx.strokeStyle = "rgba(255,255,255,0.2)";
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.roundRect(x + 1, yTop, kw - 2, noteH, rr);
          ctx.stroke();
        }

        // Track active (currently sounding) keys
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

    // Draw piano keyboard at the bottom (static cached image)
    ctx.drawImage(cache.canvas, 0, 0, cache.canvas.width, cache.canvas.height,
                  0, waterfallH, W, pianoH);

    // Highlight active keys on the keyboard
    if (activeKeys.size > 0) {
      const kh = cache.keyH;
      const bh = cache.bKeyH;
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
        // Top wash for 3D
        const topWash = ctx.createLinearGradient(0, waterfallH, 0, waterfallH + kh * 0.5);
        topWash.addColorStop(0, "rgba(255,255,255,0.25)");
        topWash.addColorStop(1, "rgba(255,255,255,0)");
        ctx.fillStyle = topWash;
        ctx.fillRect(wk.x + 0.5, waterfallH + 0.5, wk.w - 1, kh * 0.5);
        // Bottom glow
        ctx.shadowColor = RH_COLOR;
        ctx.shadowBlur = 15;
        ctx.fillStyle = RH_COLOR;
        ctx.fillRect(wk.x + 2, waterfallH + kh - 6, wk.w - 4, 6);
        ctx.restore();
      }

      // Pass 2: Black key highlights (draw on top)
      for (const midi of activeKeys) {
        const bk = cache.blackXs[midi];
        if (!bk) continue;
        ctx.save();
        ctx.globalAlpha = 0.9;
        ctx.fillStyle = RH_COLOR;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(bk.x, waterfallH, bk.w, bh, [0, 0, 3, 3]);
        else ctx.rect(bk.x, waterfallH, bk.w, bh);
        ctx.fill();
        // Glossy highlight
        const hlGrad = ctx.createLinearGradient(bk.x, waterfallH, bk.x, waterfallH + bh * 0.3);
        hlGrad.addColorStop(0, "rgba(255,255,255,0.3)");
        hlGrad.addColorStop(1, "rgba(255,255,255,0)");
        ctx.fillStyle = hlGrad;
        ctx.fillRect(bk.x + bk.w * 0.1, waterfallH, bk.w * 0.8, bh * 0.3);
        // Bottom glow
        ctx.shadowColor = RH_COLOR;
        ctx.shadowBlur = 12;
        ctx.fillStyle = RH_COLOR;
        ctx.fillRect(bk.x + 1, waterfallH + bh - 4, bk.w - 2, 4);
        ctx.restore();
      }
    }
  }

  /* ---- Hints ---- */

  _updateHints(chordName) {
    const resolved = this._resolveChord(chordName);
    if (!resolved || !resolved.available) {
      if (this._lhHintEl) this._lhHintEl.textContent = resolved && resolved.warning ? resolved.warning : "左手：低音鈕";
      return;
    }
    const bass = resolved.buttons[0] ? resolved.buttons[0].label : "?";
    const chord = resolved.buttons[1] ? resolved.buttons[1].label : "?";
    const alt = resolved.altBass ? resolved.altBass.label : "";
    if (this._lhHintEl) {
      this._lhHintEl.textContent = `低音: ${bass}  和弦: ${chord}` + (alt ? `  交替: ${alt}` : "");
    }
  }
}
