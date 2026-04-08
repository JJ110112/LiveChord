/**
 * AccordionInstrument — 手風琴左手 Stradella 低音系統教學
 *
 * 21-button layout: 3 rows × 7 columns
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
        this._drawBassWaterfall(this._b.getAudio().currentTime || 0);
      };
    }
    if (this._patternLabel && this._patternSelect) {
      this._patternLabel.textContent = this._patternSelect.options[this._patternSelect.selectedIndex].text;
    }

    this._activeIdx = -1;
    this._activeChordName = null;

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

  update(currentTime) {
    const chords = this._b.getDisplayChords();
    if (!chords || !chords.length) {
      // Still draw the empty waterfall frame (headers, lane dividers)
      this._drawBassWaterfall(currentTime);
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

    if (chordName !== this._activeChordName || ghostName !== this._ghostChordName) {
      this._activeChordName = chordName;
      this._activeIdx = idx;
      this._ghostChordName = ghostName;
      this._ghostAlpha = ghostAlpha;
      this.renderBassGrid(chordName, ghostName, ghostAlpha);
      if (this._chordNameEl) this._chordNameEl.textContent = chordName || "--";
      this._updateHints(chordName);
    } else if (ghostAlpha !== this._ghostAlpha) {
      this._ghostAlpha = ghostAlpha;
      this.renderBassGrid(chordName, ghostName, ghostAlpha);
    }

    this._drawBassWaterfall(currentTime);
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

  /* ---- Bass Grid Canvas Rendering ---- */
  /*
   * Physical layout (player's view, looking down at left hand):
   *   Vertical axis  = circle-of-fifths keys, E(top) → Bb(bottom)
   *   Horizontal axis = Minor(left) → Major(center) → Bass(right, near bellows)
   *   Alternating columns offset down by half a row (Stradella diagonal)
   *
   * Display column order (left→right): Minor(row2), Major(row1), Bass(row0)
   */

  // Map display column index (0=left .. 2=right) to data row index
  static DISP_TO_ROW = [2, 1, 0];           // Minor, Major, Bass
  static DISP_LABELS = ["Minor", "Major", "Bass"];
  static DISP_COLORS = ["#ff9800", "#ff9800", "#00bcd4"];

  renderBassGrid(chordName, ghostName, ghostAlpha) {
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

    const COLS = AccordionInstrument.COLS;   // ["Bb","F","C","G","D","A","E"]
    const D2R = AccordionInstrument.DISP_TO_ROW;
    const DLABELS = AccordionInstrument.DISP_LABELS;
    const DCOLORS = AccordionInstrument.DISP_COLORS;
    const nKeys = COLS.length;  // 7
    const nTypes = 3;

    // Keys top→bottom: E, A, D, G, C, F, Bb
    const keysTopDown = [...COLS].reverse();

    // Layout — reserve extra half-row for Stradella offset
    const padTop = 20, padBot = 16, padLeft = 8, padRight = 8;
    const headerH = 16;
    const areaW = W - padLeft - padRight;
    const areaH = H - padTop - padBot - headerH;
    const colW = areaW / nTypes;
    const rowH = areaH / (nKeys + 1.0); // +1.0 accounts for parallelogram offset
    const btnR = Math.min(colW, rowH) * 0.36;
    const offsetY = rowH * 0.5; // Stradella diagonal shift per column

    const active = this._resolveChord(chordName);
    const ghost = ghostName ? this._resolveChord(ghostName) : null;

    // Column headers
    ctx.font = "bold 11px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (let d = 0; d < nTypes; d++) {
      ctx.fillStyle = DCOLORS[d];
      ctx.fillText(DLABELS[d], padLeft + (d + 0.5) * colW, 2);
    }

    // Draw buttons
    for (let ki = 0; ki < nKeys; ki++) {
      const keyName = keysTopDown[ki];
      const col = COLS.indexOf(keyName);

      for (let d = 0; d < nTypes; d++) {
        const row = D2R[d]; // data row: 0=Bass, 1=Major, 2=Minor
        // Stradella offset: parallelogram — each column shifts down by d * half-row
        const dy = d * offsetY;
        const cx = padLeft + (d + 0.5) * colW;
        const cy = padTop + headerH + (ki + 0.5) * rowH + dy;

        // Match against data
        let isActive = false, isAltBass = false, isGhost = false;
        if (active && active.buttons) {
          for (const btn of active.buttons) {
            if (btn.col === col && btn.row === row) isActive = true;
          }
          if (active.altBass && active.altBass.col === col && row === 0) isAltBass = true;
        }
        if (ghost && ghost.buttons && ghostAlpha > 0.05) {
          for (const btn of ghost.buttons) {
            if (btn.col === col && btn.row === row) isGhost = true;
          }
        }

        // Button circle
        const isBass = (row === 0);
        ctx.beginPath();
        ctx.arc(cx, cy, btnR, 0, Math.PI * 2);
        if (isActive) {
          ctx.fillStyle = isBass ? "rgba(0,188,212,0.85)" : "rgba(255,152,0,0.85)";
          ctx.fill();
          ctx.shadowColor = isBass ? "#00bcd4" : "#ff9800";
          ctx.shadowBlur = 12;
          ctx.fill();
          ctx.shadowBlur = 0;
        } else if (isGhost) {
          ctx.fillStyle = isBass ? `rgba(0,188,212,${ghostAlpha})` : `rgba(255,152,0,${ghostAlpha})`;
          ctx.fill();
        } else if (isAltBass) {
          ctx.fillStyle = "rgba(0,188,212,0.25)";
          ctx.fill();
        } else {
          ctx.fillStyle = "rgba(60,60,70,0.7)";
          ctx.fill();
        }

        ctx.strokeStyle = isActive ? "#fff" : "rgba(255,255,255,0.2)";
        ctx.lineWidth = isActive ? 2 : 1;
        ctx.stroke();

        // Dimple on C bass
        if (col === AccordionInstrument.DIMPLE_COL && row === 0) {
          ctx.beginPath();
          ctx.arc(cx, cy, btnR * 0.3, 0, Math.PI * 2);
          ctx.strokeStyle = isActive ? "rgba(255,255,255,0.6)" : "rgba(255,255,255,0.25)";
          ctx.lineWidth = 1.5;
          ctx.stroke();
        }

        // Label
        ctx.fillStyle = isActive ? "#fff" : "#bbb";
        ctx.font = (isActive ? "bold " : "") + "11px sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        const label = row === 0 ? keyName : (row === 1 ? keyName : keyName + "m");
        ctx.fillText(label, cx, cy);
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

  /* ---- Bass Pattern Waterfall ---- */

  _drawBassWaterfall(currentTime) {
    const canvas = this._wfCanvas;
    if (!canvas || !canvas.parentElement) return;
    const rect = canvas.parentElement.getBoundingClientRect();
    if (rect.width < 10 || rect.height < 50) return; // not laid out yet
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

    const chords = this._b.getDisplayChords();
    if (!chords || !chords.length) return;

    // Pattern definition
    const patterns = {
      bass_chord:       { beats: 4, steps: ["B","C","B","C"] },
      alternating_bass: { beats: 4, steps: ["B","C","Ab","C"] },
      waltz:            { beats: 3, steps: ["B","C","C"] },
      march:            { beats: 2, steps: ["B","C"] },
    };
    const pat = patterns[this._bassPattern] || patterns.bass_chord;

    // Visible window: show 6 seconds centered on current time
    const windowBefore = 1.0; // 1s of past
    const windowAfter = 5.0;  // 5s of future
    const tStart = currentTime - windowBefore;
    const tEnd = currentTime + windowAfter;
    const totalT = tEnd - tStart;

    // Lanes
    const lanes = ["Bass", "Chord"];
    const laneW = W / lanes.length;
    const colors = { B: "#00bcd4", C: "#ff9800", Ab: "#26a69a" };
    const labels = { B: "Bass", C: "Chord", Ab: "Alt" };

    // Lane headers
    ctx.font = "bold 11px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (let i = 0; i < lanes.length; i++) {
      ctx.fillStyle = i === 0 ? "#00bcd4" : "#ff9800";
      ctx.fillText(lanes[i], (i + 0.5) * laneW, 2);
    }

    // Current time line
    const curY = ((currentTime - tStart) / totalT) * H;
    ctx.strokeStyle = "rgba(255,255,255,0.5)";
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(0, curY);
    ctx.lineTo(W, curY);
    ctx.stroke();
    ctx.setLineDash([]);

    // Generate bass events for visible chords
    const accData = this._b.getAccData ? this._b.getAccData() : null;
    const bpm = (accData && accData.bpm) ? accData.bpm : 120;
    const beatDur = 60.0 / bpm;

    for (let ci = 0; ci < chords.length; ci++) {
      const chord = chords[ci];
      const nextTime = ci + 1 < chords.length ? chords[ci + 1].time : (chord.end || chord.time + 4);
      if (nextTime < tStart || chord.time > tEnd) continue;

      const chordDur = nextTime - chord.time;
      const resolved = this._resolveChord(chord.chord);

      // Generate pattern beats within this chord (limit iterations for safety)
      let t = chord.time;
      let maxIter = 200;
      while (t < nextTime && --maxIter > 0) {
        for (let si = 0; si < pat.steps.length && t < nextTime; si++) {
          const step = pat.steps[si];
          const stepEnd = Math.min(t + beatDur, nextTime);
          if (t + beatDur * 0.1 > tEnd) { t = nextTime; break; }
          if (stepEnd < tStart) { t += beatDur; continue; }

          // Map step to lane
          const laneIdx = (step === "B" || step === "Ab") ? 0 : 1;
          const y1 = ((t - tStart) / totalT) * H;
          const y2 = ((stepEnd - tStart) / totalT) * H;
          const x = laneIdx * laneW + 4;
          const w = laneW - 8;

          // Is this the current beat?
          const isCurrent = currentTime >= t && currentTime < stepEnd;
          const color = colors[step] || "#888";

          ctx.globalAlpha = isCurrent ? 1.0 : (t < currentTime ? 0.3 : 0.7);
          ctx.fillStyle = color;
          const rr = 4;
          ctx.beginPath();
          ctx.moveTo(x + rr, y1);
          ctx.lineTo(x + w - rr, y1);
          ctx.quadraticCurveTo(x + w, y1, x + w, y1 + rr);
          ctx.lineTo(x + w, y2 - rr);
          ctx.quadraticCurveTo(x + w, y2, x + w - rr, y2);
          ctx.lineTo(x + rr, y2);
          ctx.quadraticCurveTo(x, y2, x, y2 - rr);
          ctx.lineTo(x, y1 + rr);
          ctx.quadraticCurveTo(x, y1, x + rr, y1);
          ctx.fill();

          // Label
          if (y2 - y1 > 16) {
            ctx.globalAlpha = isCurrent ? 1.0 : 0.6;
            ctx.fillStyle = "#fff";
            ctx.font = "10px sans-serif";
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            const btnLabel = step === "B"
              ? (resolved && resolved.buttons && resolved.buttons[0] ? resolved.buttons[0].label : "?")
              : step === "Ab"
                ? (resolved && resolved.altBass ? resolved.altBass.label : "?")
                : (resolved && resolved.buttons && resolved.buttons[1] ? resolved.buttons[1].label : "?");
            ctx.fillText(btnLabel, x + w / 2, (y1 + y2) / 2);
          }

          ctx.globalAlpha = 1.0;
          t += beatDur;
        }
      }
    }

    // Lane divider
    ctx.strokeStyle = "rgba(255,255,255,0.1)";
    ctx.lineWidth = 1;
    for (let i = 1; i < lanes.length; i++) {
      ctx.beginPath();
      ctx.moveTo(i * laneW, 16);
      ctx.lineTo(i * laneW, H);
      ctx.stroke();
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
