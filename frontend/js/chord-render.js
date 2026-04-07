/** 和弦渲染模組 — 簡譜文字 & Canvas 和弦圖 */

const ChordRender = {
  /**
   * 將簡譜字串轉為 HTML（升降號上標）
   * 例: "b3 5 1" → "<sup>b</sup>3 5 1"
   */
  jianpuToHtml(str) {
    if (!str) return "";
    return str.replace(/([b#])([\d])/g, "<sup>$1</sup>$2");
  },

  /**
   * 在 Canvas 上繪製和弦圖
   * @param {HTMLCanvasElement} canvas
   * @param {Object} data - {name, numStrings, strings, baseFret, barres}
   * @param {number} scale
   */
  drawDiagram(canvas, data, scale = 1) {
    if (!data || !canvas) return;

    const numStrings = data.numStrings || 6;
    const strings = [...(data.strings || [])];
    const barres = [...(data.barres || [])];

    // Normalize relative frets to absolute
    const _dataBase = data.baseFret || 1;
    if (_dataBase > 1) {
      const _maxRel = Math.max(...strings.filter(f => f > 0), 0);
      if (_maxRel > 0 && _maxRel <= 5) {
        for (let i = 0; i < strings.length; i++) {
          if (strings[i] > 0) strings[i] = strings[i] + _dataBase - 1;
        }
        for (let i = 0; i < barres.length; i++) {
          if (barres[i] > 0 && barres[i] <= 5) barres[i] = barres[i] + _dataBase - 1;
        }
      }
    }

    // Auto-calculate baseFret
    const _fretted = strings.filter(f => f > 0);
    const _hasOpen = strings.some(f => f === 0);
    const _maxFret = _fretted.length > 0 ? Math.max(..._fretted) : 1;
    let baseFret = 1;
    if (!_hasOpen && _maxFret > 4) {
      baseFret = Math.min(..._fretted);
    }

    const sw = numStrings === 6 ? 60 : 44;
    const sh = 74;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(sw * scale * dpr);
    canvas.height = Math.round(sh * scale * dpr);
    canvas.style.width = Math.round(sw * scale) + "px";
    canvas.style.height = Math.round(sh * scale) + "px";

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(scale * dpr, scale * dpr);
    ctx.clearRect(0, 0, sw, sh);

    // 佈局
    const topY = 16;
    const gridH = 44;
    const frets = 4;
    const margin = 8;
    const stringSpacing = (sw - margin * 2) / (numStrings - 1);
    const fretSpacing = gridH / frets;

    // 畫格線
    ctx.strokeStyle = "#888";
    ctx.lineWidth = 1;

    // 橫線 (frets)
    for (let i = 0; i <= frets; i++) {
      const y = topY + i * fretSpacing;
      ctx.beginPath();
      ctx.moveTo(margin, y);
      ctx.lineTo(sw - margin, y);
      ctx.stroke();
    }

    // nut (如果是第一格)
    if (baseFret === 1) {
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(margin, topY);
      ctx.lineTo(sw - margin, topY);
      ctx.stroke();
      ctx.lineWidth = 1;
      ctx.strokeStyle = "#888";
    }

    // 縱線 (strings)
    for (let i = 0; i < numStrings; i++) {
      const x = margin + i * stringSpacing;
      ctx.beginPath();
      ctx.moveTo(x, topY);
      ctx.lineTo(x, topY + gridH);
      ctx.stroke();
    }

    // baseFret 標示
    if (baseFret > 1) {
      ctx.fillStyle = "#aaa";
      ctx.font = "10px sans-serif";
      ctx.textAlign = "right";
      ctx.fillText(baseFret.toString(), margin - 3, topY + fretSpacing / 2 + 3);
    }

    // barres
    ctx.fillStyle = "#fff";
    for (const barre of barres) {
      const fretPos = barre - baseFret + 1;
      if (fretPos < 1 || fretPos > frets) continue;
      const y = topY + (fretPos - 0.5) * fretSpacing;
      // 找 barre 範圍
      let minS = numStrings - 1, maxS = 0;
      strings.forEach((s, i) => {
        if (s >= barre) { minS = Math.min(minS, i); maxS = Math.max(maxS, i); }
      });
      const x1 = margin + minS * stringSpacing;
      const x2 = margin + maxS * stringSpacing;
      ctx.beginPath();
      ctx.lineWidth = 5;
      ctx.strokeStyle = "#fff";
      ctx.moveTo(x1, y);
      ctx.lineTo(x2, y);
      ctx.stroke();
      ctx.lineWidth = 1;
      ctx.strokeStyle = "#888";
    }

    // 按點
    for (let i = 0; i < numStrings; i++) {
      const fret = strings[i];
      const x = margin + i * stringSpacing;

      if (fret === -1) {
        // X (muted)
        ctx.fillStyle = "#888";
        ctx.font = "10px sans-serif";
        ctx.textAlign = "center";
        ctx.fillText("X", x, topY - 4);
      } else if (fret === 0) {
        // O (open)
        ctx.strokeStyle = "#fff";
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(x, topY - 6, 3, 0, Math.PI * 2);
        ctx.stroke();
        ctx.strokeStyle = "#888";
        ctx.lineWidth = 1;
      } else {
        // 按的位置
        const relFret = fret - baseFret + 1;
        if (relFret >= 1 && relFret <= frets) {
          const y = topY + (relFret - 0.5) * fretSpacing;
          ctx.fillStyle = "#fff";
          ctx.beginPath();
          ctx.arc(x, y, 3.5, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    }

    // 和弦名稱
    ctx.fillStyle = "#fff";
    ctx.font = "bold 10px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(data.name || "", sw / 2, sh - 2);
  },

  /**
   * 在 Canvas 上繪製鋼琴鍵盤（標示按鍵位置）
   * @param {HTMLCanvasElement} canvas
   * @param {string[]} notes - 組成音名列表 e.g. ["C", "E", "G"]
   * @param {number} scale
   */
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {string[]} notes - 組成音名 e.g. ["C","E","G","B"]
   * @param {number} scale
   * @param {number[]} prevMidi - 前一個和弦的 MIDI 音高（用於 voice leading）
   * @param {number} melodyMidi - 當前旋律 MIDI 音高（高亮顯示, -1=無）
   */
  drawPiano(canvas, notes, scale = 1, prevMidi = null, melodyMidi = -1) {
    if (!canvas) return;
    notes = notes || [];

    // 音名→半音
    const NOTE_MAP = { C:0,"C#":1,Db:1,D:2,"D#":3,Eb:3,E:4,Fb:4,F:5,"F#":6,Gb:6,G:7,"G#":8,Ab:8,A:9,"A#":10,Bb:10,B:11,Cb:11 };
    const SEMI_TO_NAME = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];

    function noteToSemi(n) {
      if (n in NOTE_MAP) return NOTE_MAP[n];
      let s = NOTE_MAP[n[0]] || 0;
      for (let i = 1; i < n.length; i++) { if (n[i]==="#") s++; else if (n[i]==="b") s--; }
      return ((s % 12) + 12) % 12;
    }

    // 計算絕對 MIDI 音高：從根音開始堆疊，每個音 >= 前一個音
    const semis = notes.map(n => noteToSemi(n));
    const absNotes = []; // absolute MIDI note numbers
    if (semis.length > 0) {
      const rootOctave = 4; // start from octave 4 (middle C area)
      let prev = semis[0] + rootOctave * 12;
      absNotes.push(prev);
      for (let i = 1; i < semis.length; i++) {
        let n = semis[i] + rootOctave * 12;
        while (n < prev) n += 12;
        absNotes.push(n);
        prev = n;
      }
    }

    // Voice Leading：如果有前一個和弦，找最近的轉位
    if (prevMidi && prevMidi.length > 0 && absNotes.length > 0) {
      const prevCenter = prevMidi.reduce((a, b) => a + b, 0) / prevMidi.length;
      // 嘗試所有轉位：每個音可以 ±12
      let bestVoicing = [...absNotes];
      let bestDist = Infinity;
      // 嘗試整體移高/低八度 + 個別音微調
      for (let shift = -12; shift <= 12; shift += 12) {
        const candidate = absNotes.map(n => n + shift);
        // 個別音找最近 prevMidi 的位置
        const voiced = candidate.map(n => {
          let best = n;
          let minD = Math.abs(n - prevCenter);
          for (const oct of [-12, 0, 12]) {
            const d = Math.abs((n + oct) - prevCenter);
            if (d < minD) { minD = d; best = n + oct; }
          }
          return best;
        });
        // 確保排序且在合理範圍
        voiced.sort((a, b) => a - b);
        const totalDist = voiced.reduce((sum, n, i) => {
          const closest = prevMidi.reduce((min, p) => Math.min(min, Math.abs(n - p)), 99);
          return sum + closest;
        }, 0);
        if (totalDist < bestDist) {
          bestDist = totalDist;
          bestVoicing = voiced;
        }
      }
      // 替換 absNotes
      absNotes.length = 0;
      bestVoicing.forEach(n => absNotes.push(n));
    }

    // 區分核心音 (前4個: root,3rd,5th,7th) vs 延伸音 (9th,11th,13th)
    const coreSet = new Set(absNotes.slice(0, 4));
    const extSet = new Set(absNotes.slice(4));

    // 計算鍵盤範圍：顯示根音前 2 個白鍵到最高音後 2 個白鍵
    const whiteNotes = [0,2,4,5,7,9,11]; // semitones that are white keys
    const isWhite = (midi) => whiteNotes.includes(midi % 12);

    let loMidi = absNotes.length ? absNotes[0] : 48;
    let hiMidi = absNotes.length ? absNotes[absNotes.length - 1] : 59;
    // expand to white key boundaries + padding
    while (!isWhite(loMidi)) loMidi--;
    for (let p = 0; p < 2; p++) { loMidi--; while (!isWhite(loMidi)) loMidi--; }
    while (!isWhite(hiMidi)) hiMidi++;
    for (let p = 0; p < 2; p++) { hiMidi++; while (!isWhite(hiMidi)) hiMidi++; }

    // Build white key list
    const whiteKeys = [];
    for (let m = loMidi; m <= hiMidi; m++) { if (isWhite(m)) whiteKeys.push(m); }
    const numWhites = whiteKeys.length;

    // Pressed set (absolute MIDI)
    const pressedSet = new Set(absNotes);

    // Drawing params
    const ww = 12, wh = 44, bw = 8, bh = 26, dotR = 3;
    const totalW = numWhites * ww + 2;
    const totalH = wh + 2;
    const dpr = window.devicePixelRatio || 1;

    canvas.width = Math.round(totalW * scale * dpr);
    canvas.height = Math.round(totalH * scale * dpr);
    canvas.style.width = Math.round(totalW * scale) + "px";
    canvas.style.height = Math.round(totalH * scale) + "px";

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(scale * dpr, scale * dpr);
    ctx.clearRect(0, 0, totalW, totalH);

    const x0 = 1;

    // 顏色：核心音=紅色, 延伸音=藍色
    const CORE_COLOR = "#e94560";   // 紅 — 骨架 (1,3,5,7)
    const EXT_COLOR  = "#4ca1ff";   // 藍 — 點綴 (9,11,13)

    // Draw white keys
    for (let i = 0; i < numWhites; i++) {
      const midi = whiteKeys[i];
      const x = x0 + i * ww;
      const isCore = coreSet.has(midi);
      const isExt = extSet.has(midi);
      const hit = isCore || isExt;
      ctx.fillStyle = hit ? "#fff" : "#f0f0f0";
      ctx.fillRect(x, 0, ww - 1, wh);
      ctx.strokeStyle = "#999";
      ctx.lineWidth = 0.5;
      ctx.strokeRect(x, 0, ww - 1, wh);
      if (hit) {
        ctx.fillStyle = isExt ? EXT_COLOR : CORE_COLOR;
        ctx.beginPath();
        ctx.arc(x + (ww - 1) / 2, wh - dotR - 4, dotR, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // Draw black keys
    const blackAfter = new Set([0, 1, 3, 4, 5]);
    for (let i = 0; i < numWhites - 1; i++) {
      const midi = whiteKeys[i];
      const deg = midi % 12;
      if (!blackAfter.has(whiteNotes.indexOf(deg))) continue;
      const blackMidi = midi + 1;
      const x = x0 + (i + 1) * ww - bw / 2 - 0.5;
      const isCore = coreSet.has(blackMidi);
      const isExt = extSet.has(blackMidi);
      const hit = isCore || isExt;
      ctx.fillStyle = hit ? "#444" : "#222";
      ctx.fillRect(x, 0, bw, bh);
      ctx.strokeStyle = "#000";
      ctx.lineWidth = 0.5;
      ctx.strokeRect(x, 0, bw, bh);
      if (hit) {
        ctx.fillStyle = isExt ? EXT_COLOR : "#fff";
        ctx.beginPath();
        ctx.arc(x + bw / 2, bh - dotR - 3, dotR, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // 旋律音高亮：綠色菱形標記
    if (melodyMidi >= 0) {
      const MELODY_COLOR = "#00e676";  // 亮綠
      const mpc = melodyMidi % 12;
      // 找到鍵盤上的位置
      for (let i = 0; i < numWhites; i++) {
        if (whiteKeys[i] % 12 === mpc) {
          const x = x0 + i * ww + (ww - 1) / 2;
          const y = 6;
          ctx.fillStyle = MELODY_COLOR;
          // 菱形
          ctx.beginPath();
          ctx.moveTo(x, y - 4);
          ctx.lineTo(x + 4, y);
          ctx.lineTo(x, y + 4);
          ctx.lineTo(x - 4, y);
          ctx.closePath();
          ctx.fill();
          break;
        }
      }
      // 黑鍵
      for (let i = 0; i < numWhites - 1; i++) {
        const deg = whiteKeys[i] % 12;
        if (!blackAfter.has(whiteNotes.indexOf(deg))) continue;
        if ((whiteKeys[i] + 1) % 12 === mpc) {
          const x = x0 + (i + 1) * ww - 0.5;
          ctx.fillStyle = MELODY_COLOR;
          ctx.beginPath();
          ctx.moveTo(x, 3);
          ctx.lineTo(x + 3, 6);
          ctx.lineTo(x, 9);
          ctx.lineTo(x - 3, 6);
          ctx.closePath();
          ctx.fill();
          break;
        }
      }
    }

    // 回傳 MIDI 位置供下一個和弦做 voice leading
    canvas._lastMidi = [...absNotes];
  },

  /**
   * 建立和弦項目 HTML（簡譜模式）
   */
  createJianpuItem(chord, jianpu, isActive) {
    const div = document.createElement("div");
    div.className = "chord-item" + (isActive ? " active" : "");
    div.innerHTML = `
      <div class="chord-name">${escapeHtml(chord.chord)}</div>
      <div class="chord-jianpu">${ChordRender.jianpuToHtml(jianpu)}</div>
      <div class="chord-time">${formatTime(chord.time)}</div>`;
    return div;
  },

  /**
   * 建立和弦項目（和弦圖模式）
   */
  createDiagramItem(chord, diagramData, isActive) {
    const div = document.createElement("div");
    div.className = "chord-item" + (isActive ? " active" : "");
    div.style.minWidth = "80px";

    const nameEl = document.createElement("div");
    nameEl.className = "chord-name";
    nameEl.textContent = chord.chord;
    div.appendChild(nameEl);

    if (diagramData) {
      const canvas = document.createElement("canvas");
      canvas.style.marginTop = "4px";
      ChordRender.drawDiagram(canvas, diagramData, 1);
      div.appendChild(canvas);
    } else {
      const span = document.createElement("div");
      span.style.cssText = "font-size:11px;color:#666;margin-top:4px";
      span.textContent = "無圖";
      div.appendChild(span);
    }

    const timeEl = document.createElement("div");
    timeEl.className = "chord-time";
    timeEl.textContent = formatTime(chord.time);
    div.appendChild(timeEl);

    return div;
  },

  // =========================================================================
  // 88 Piano Keys — full keyboard visualisation
  // =========================================================================

  /** Convert chord note-names to absolute MIDI in left-hand range (C2-C3) */
  voiceChordForLeftHand(noteNames, prevMidi) {
    if (!noteNames || noteNames.length === 0) return [];
    const NOTE_MAP = { C:0,"C#":1,Db:1,D:2,"D#":3,Eb:3,E:4,Fb:4,F:5,"F#":6,Gb:6,G:7,"G#":8,Ab:8,A:9,"A#":10,Bb:10,B:11,Cb:11 };
    function nts(n) {
      if (n in NOTE_MAP) return NOTE_MAP[n];
      let s = NOTE_MAP[n[0]] || 0;
      for (let i = 1; i < n.length; i++) { if (n[i]==="#") s++; else if (n[i]==="b") s--; }
      return ((s % 12) + 12) % 12;
    }
    const semis = noteNames.map(n => nts(n));
    const abs = [];
    const rootOct = 3; // left-hand: root around C3 (MIDI 48)
    let prev = semis[0] + rootOct * 12;
    abs.push(prev);
    for (let i = 1; i < semis.length; i++) {
      let n = semis[i] + rootOct * 12;
      while (n < prev) n += 12;
      abs.push(n);
      prev = n;
    }
    // voice leading towards previous chord voicing
    if (prevMidi && prevMidi.length > 0) {
      const pc = prevMidi.reduce((a,b) => a+b, 0) / prevMidi.length;
      let best = [...abs], bestD = Infinity;
      for (let shift = -12; shift <= 12; shift += 12) {
        const cand = abs.map(n => {
          let v = n + shift, minD = Math.abs(v - pc);
          for (const o of [-12, 0, 12]) {
            const t = n + shift + o;
            if (t >= 36 && t <= 59 && Math.abs(t - pc) < minD) { minD = Math.abs(t - pc); v = t; }
          }
          return v;
        });
        cand.sort((a,b) => a-b);
        const d = cand.reduce((s,n) => s + prevMidi.reduce((m,p) => Math.min(m, Math.abs(n-p)), 99), 0);
        if (d < bestD) { bestD = d; best = cand; }
      }
      return best;
    }
    return abs;
  },

  /**
   * Create offscreen canvas with static 88-key keyboard.
   * Returns { canvas, whiteXs, blackXs, keyW, keyH, bKeyW, bKeyH }
   */
  init88PianoCache(width, dpr) {
    // 88 keys: A0 (21) to C8 (108) — 52 white keys
    const whites = [];
    for (let m = 21; m <= 108; m++) { if ([0,2,4,5,7,9,11].includes(m % 12)) whites.push(m); }
    const nw = whites.length; // 52
    const kw = width / nw;
    const kh = kw * 6;       // real piano ratio 1:6
    const bw = kw * 0.48;    // black key ~48% of white width
    const bh = kh * 0.62;    // black key ~62% of white height
    const bevelH = Math.round(kh * 0.06); // front face depth for 3D effect
    const labelSpace = 16;
    const totalH = kh + bevelH + labelSpace;

    const c = document.createElement("canvas");
    c.width = Math.round(width * dpr);
    c.height = Math.round(totalH * dpr);
    const ctx = c.getContext("2d");
    ctx.scale(dpr, dpr);

    // Helper: roundRect support (Fallback if very old browser)
    const drawRoundRect = (x, y, w, h, radii) => {
      if (ctx.roundRect) {
        ctx.beginPath();
        ctx.roundRect(x, y, w, h, radii);
      } else {
        ctx.beginPath();
        ctx.rect(x, y, w, h);
      }
    };

    // Dark background for key gaps (deep shadow between keys)
    ctx.fillStyle = "#1a1a1a";
    ctx.fillRect(0, 0, width, kh + bevelH);

    // Draw White Keys
    const gapW = Math.max(1, kw * 0.04); // gap between keys
    for (let i = 0; i < nw; i++) {
        const x = i * kw + gapW / 2;
        const keyW = kw - gapW;

        // Main ivory gradient — warm tone with center highlight
        const gradient = ctx.createLinearGradient(x, 0, x, kh);
        gradient.addColorStop(0,    "#e8e6e1");  // top: warm shadow (overhang)
        gradient.addColorStop(0.05, "#f5f4f0");  // transition
        gradient.addColorStop(0.15, "#fefefe");  // bright ivory
        gradient.addColorStop(0.5,  "#ffffff");  // center highlight — light reflection
        gradient.addColorStop(0.85, "#f8f7f3");  // warmth returning
        gradient.addColorStop(0.95, "#ece9e3");  // pre-bevel darkening
        gradient.addColorStop(1,    "#e0ddd7");  // bottom edge

        ctx.fillStyle = gradient;
        drawRoundRect(x, 0, keyW, kh, [0, 0, 3, 3]);
        ctx.fill();

        // Left edge shadow (inter-key depth)
        const leftShadow = ctx.createLinearGradient(x, 0, x + kw * 0.08, 0);
        leftShadow.addColorStop(0, "rgba(0,0,0,0.12)");
        leftShadow.addColorStop(1, "rgba(0,0,0,0)");
        ctx.fillStyle = leftShadow;
        ctx.fillRect(x, 0, kw * 0.08, kh);

        // Right edge shadow (inter-key depth)
        const rightShadow = ctx.createLinearGradient(x + keyW, 0, x + keyW - kw * 0.08, 0);
        rightShadow.addColorStop(0, "rgba(0,0,0,0.10)");
        rightShadow.addColorStop(1, "rgba(0,0,0,0)");
        ctx.fillStyle = rightShadow;
        ctx.fillRect(x + keyW - kw * 0.08, 0, kw * 0.08, kh);

        // Top inner shadow (soft, from overhang)
        const innerGlow = ctx.createLinearGradient(x, 0, x, kh * 0.06);
        innerGlow.addColorStop(0, "rgba(0,0,0,0.10)");
        innerGlow.addColorStop(1, "rgba(0,0,0,0)");
        ctx.fillStyle = innerGlow;
        ctx.fillRect(x, 0, keyW, kh * 0.06);

        // 3D front face (bevel) — realistic depth
        const bevelGrad = ctx.createLinearGradient(x, kh, x, kh + bevelH);
        bevelGrad.addColorStop(0,   "#d0cec8");  // top lip (lightest)
        bevelGrad.addColorStop(0.2, "#b8b5ae");
        bevelGrad.addColorStop(0.6, "#a8a5a0");
        bevelGrad.addColorStop(1,   "#807d78");  // bottom (darkest)
        ctx.fillStyle = bevelGrad;
        drawRoundRect(x, kh, keyW, bevelH, [0, 0, 3, 3]);
        ctx.fill();
    }

    // Draw Black Keys with enhanced 3D
    const blackAfterSemi = new Set([0, 1, 3, 4, 5]); // white key semitone index where black follows (C,D, F,G,A)
    const whiteOrder = [0,2,4,5,7,9,11];
    const blackXs = {};
    const bFaceH = Math.max(2, bh * 0.04); // black key front face height

    // Pass 1: Render shadows for black keys so they look "raised"
    ctx.shadowColor = "rgba(0, 0, 0, 0.7)";
    ctx.shadowBlur = 8;
    ctx.shadowOffsetX = 1;
    ctx.shadowOffsetY = 3;
    for (let i = 0; i < nw - 1; i++) {
        const deg = whites[i] % 12;
        const wIdx = whiteOrder.indexOf(deg);
        if (!blackAfterSemi.has(wIdx)) continue;
        const x = (i + 1) * kw - bw / 2;
        drawRoundRect(x, 0, bw, bh + bFaceH, [0, 0, 3, 3]);
        ctx.fillStyle = "#000";
        ctx.fill();
    }
    ctx.shadowBlur = 0;
    ctx.shadowOffsetX = 0;
    ctx.shadowOffsetY = 0;

    // Pass 2: Draw glossy black key surfaces
    for (let i = 0; i < nw - 1; i++) {
        const deg = whites[i] % 12;
        const wIdx = whiteOrder.indexOf(deg);
        if (!blackAfterSemi.has(wIdx)) continue;
        const bMidi = whites[i] + 1;
        const x = (i + 1) * kw - bw / 2;

        // Main body gradient
        const bgGrad = ctx.createLinearGradient(x, 0, x, bh);
        bgGrad.addColorStop(0,    "#0a0a0a");  // very dark top
        bgGrad.addColorStop(0.05, "#181818");  // slight lighten
        bgGrad.addColorStop(0.7,  "#1a1a1a");  // body
        bgGrad.addColorStop(0.95, "#282828");  // pre-edge lighten
        bgGrad.addColorStop(1,    "#333");     // bottom edge reflection

        drawRoundRect(x, 0, bw, bh, [0, 0, 3, 3]);
        ctx.fillStyle = bgGrad;
        ctx.fill();

        // Top surface reflection — bright narrow band
        const hlX = x + bw * 0.12;
        const hlW = bw * 0.76;
        const hlH = bh * 0.12;
        const hlGrad = ctx.createLinearGradient(hlX, 0, hlX, hlH);
        hlGrad.addColorStop(0, "rgba(255,255,255,0.35)");
        hlGrad.addColorStop(1, "rgba(255,255,255,0.0)");
        ctx.fillStyle = hlGrad;
        ctx.fillRect(hlX, 0, hlW, hlH);

        // Longer subtle gloss (top to ~60%)
        const longGloss = ctx.createLinearGradient(x, 0, x, bh * 0.6);
        longGloss.addColorStop(0, "rgba(255,255,255,0.12)");
        longGloss.addColorStop(1, "rgba(255,255,255,0.0)");
        drawRoundRect(x + bw * 0.1, 0, bw * 0.8, bh * 0.6, [0, 0, 2, 2]);
        ctx.fillStyle = longGloss;
        ctx.fill();

        // Left edge highlight (light catch)
        ctx.fillStyle = "rgba(255,255,255,0.06)";
        ctx.fillRect(x, 0, 1, bh);

        // Front face of black key
        const bfGrad = ctx.createLinearGradient(x, bh, x, bh + bFaceH);
        bfGrad.addColorStop(0, "#383838");
        bfGrad.addColorStop(1, "#1a1a1a");
        ctx.fillStyle = bfGrad;
        drawRoundRect(x, bh, bw, bFaceH, [0, 0, 2, 2]);
        ctx.fill();

        // Outline
        ctx.strokeStyle = "#0a0a0a";
        ctx.lineWidth = 0.8;
        drawRoundRect(x, 0, bw, bh + bFaceH, [0, 0, 3, 3]);
        ctx.stroke();

        blackXs[bMidi] = { x, w: bw, h: bh };
    }

    // white key x-positions lookup
    const whiteXs = {};
    for (let i = 0; i < nw; i++) whiteXs[whites[i]] = { x: i * kw, w: kw - 1, h: kh };

    // octave labels + middle-C marker
    ctx.fillStyle = "#666";
    ctx.font = `${Math.max(9, Math.min(11, kw * 0.9))}px sans-serif`;
    ctx.textAlign = "center";
    for (let oct = 0; oct <= 8; oct++) {
      const midi = oct * 12 + 12; // C of this octave (C0=12, C1=24, ..., C8=108)
      if (midi < 21 || midi > 108) continue;
      const info = whiteXs[midi];
      if (!info) continue;
      ctx.fillText("C" + oct, info.x + (info.w / 2), kh + bevelH + 13);
    }
    // middle C (C4=60) subtle marker line
    const c4 = whiteXs[60];
    if (c4) {
      ctx.strokeStyle = "rgba(41, 182, 246, 0.6)"; // Cyan marker
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(c4.x + c4.w / 2, kh + bevelH - 3);
      ctx.lineTo(c4.x + c4.w / 2, kh + bevelH + 2);
      ctx.stroke();
    }

    return { canvas: c, whiteXs, blackXs, keyW: kw, keyH: kh, bKeyW: bw, bKeyH: bh, bevelH, totalH };
  },

  /**
   * Per-frame render of the 88-key piano.
   * @param {HTMLCanvasElement} canvas  — visible canvas
   * @param {Object} cache             — from init88PianoCache
   * @param {number[]} chordMidis      — absolute MIDI for current chord
   * @param {number} melodyMidi        — current melody MIDI (-1 = none)
   * @param {Object} opts              — { coreCount, sustainNotes:[{midi,release}], now }
   */
  draw88Piano(canvas, cache, leftMidis, rightMidis, opts) {
    if (!canvas || !cache) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || canvas.parentElement.clientWidth || 800;
    const h = canvas.clientHeight || canvas.parentElement.clientHeight || 180;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(1,0,0,1,0,0);
    // blit static keyboard
    ctx.drawImage(cache.canvas, 0, 0, canvas.width, canvas.height);
    ctx.scale(dpr, dpr);

    const { whiteXs, blackXs } = cache;
    // Updated Hand Colors matching the waterfall + reference imagery
    const LH_COLOR = "rgba(41, 182, 246, 0.9)";   // Bright Cyan (Left)
    const RH_COLOR = "rgba(255, 152, 0, 0.9)";    // Deep Orange (Right)
    const CHORD_OUTLINE = "rgba(182, 79, 255, 0.85)"; // Purple pill aura

    // Convert rightMidis if it was still passed as single number temporarily
    const rMidis = Array.isArray(rightMidis) ? rightMidis : (rightMidis >= 0 ? [rightMidis] : []);
    const lMidis = Array.isArray(leftMidis) ? leftMidis : [];
    
    const sustainNotes = (opts && opts.sustainNotes) || [];
    const chordHints = (opts && opts.chordHints) || Array.isArray(opts && opts.coreCount === undefined ? leftMidis : []); 
    
    const now = (opts && opts.now) || 0;

    const highlights = [];

    // sustaining (fading) notes
    for (const sn of sustainNotes) {
      const age = now - sn.release;
      if (age > 0.6) continue;
      highlights.push({ midi: sn.midi, color: "rgba(100,100,100,0.5)", alpha: 0.35 * (1 - age / 0.6) });
    }

    for (const m of lMidis) highlights.push({ midi: m, color: LH_COLOR, alpha: 0.95 });
    for (const m of rMidis) highlights.push({ midi: m, color: RH_COLOR, alpha: 0.95 });
    
    // Chord hints (outline only)
    if (opts && opts.chordHints) {
      for (const m of opts.chordHints) {
        if (!lMidis.includes(m) && !rMidis.includes(m)) {
          let isFallingLh = opts.fallingLh && opts.fallingLh.includes(m);
          let isFallingRh = opts.fallingRh && opts.fallingRh.includes(m);
          if (isFallingLh || isFallingRh) {
            highlights.push({ 
              midi: m, 
              color: isFallingLh ? LH_COLOR : RH_COLOR, 
              alpha: 0.85, 
              outlineOnly: true,
              glow: true
            });
          } else {
            highlights.push({ midi: m, color: CHORD_OUTLINE, alpha: 1, outlineOnly: true });
          }
        }
      }
    }

    // Global Chord Tones (faint background highlight across keyboard)
    if (opts && opts.chordTones && opts.chordTones.length > 0) {
      const FAINT_BLUE = "#4fc3f7"; // Soft cyan highlighting
      for (let m = 21; m <= 108; m++) {
        const pc = m % 12;
        if (opts.chordTones.includes(pc)) {
           if (!lMidis.includes(m) && !rMidis.includes(m) && !(opts.chordHints && opts.chordHints.includes(m))) {
             highlights.push({ midi: m, color: FAINT_BLUE, alpha: 0.9, outlineOnly: true, fillFaint: true });
           }
        }
      }
    }

    const drawRoundRect = (x, y, w, h, radii) => {
      if (ctx.roundRect) {
        ctx.beginPath();
        ctx.roundRect(x, y, w, h, radii);
      } else {
        ctx.beginPath();
        ctx.rect(x, y, w, h);
      }
    };

    // Pass 1: draw WHITE key highlights only
    for (const hl of highlights) {
      const wk = whiteXs[hl.midi];
      if (!wk) continue;
      ctx.globalAlpha = hl.alpha;
      if (hl.outlineOnly) {
         ctx.fillStyle = hl.color;
         ctx.globalAlpha = hl.fillFaint ? 0.2 : 0.3;
         drawRoundRect(wk.x + 1.5, 1.5, wk.w - 3, wk.h - 3, [0, 0, 4, 4]);
         ctx.fill();
         ctx.globalAlpha = hl.alpha;
         ctx.strokeStyle = hl.color;
         ctx.lineWidth = hl.fillFaint ? 1.5 : 2.5;
         if (hl.glow) {
           ctx.shadowBlur = 12;
           ctx.shadowColor = hl.color;
         }
         ctx.stroke();
         ctx.shadowBlur = 0; 
      } else {
         // Solid color fill for full key coverage
         ctx.fillStyle = hl.color;
         drawRoundRect(wk.x + 0.5, 0.5, wk.w - 1, wk.h - 1, [0, 0, 4, 4]);
         ctx.fill();
         // Subtle lighter wash in top half for slight 3D look
         const topWash = ctx.createLinearGradient(0, 0, 0, wk.h * 0.5);
         topWash.addColorStop(0, "rgba(255,255,255,0.25)");
         topWash.addColorStop(1, "rgba(255,255,255,0)");
         ctx.fillStyle = topWash;
         ctx.fillRect(wk.x + 0.5, 0.5, wk.w - 1, wk.h * 0.5);
         // Bottom-edge illumination glow
         ctx.save();
         ctx.beginPath();
         ctx.rect(wk.x, 0, wk.w, wk.h);
         ctx.clip();
         const wGlowH = 16;
         const wGlowY = wk.h - wGlowH;
         const wGlowGrad = ctx.createLinearGradient(0, wGlowY, 0, wk.h);
         wGlowGrad.addColorStop(0, "rgba(255,255,255,0)");
         wGlowGrad.addColorStop(1, "rgba(255,255,255,0.5)");
         ctx.fillStyle = wGlowGrad;
         ctx.fillRect(wk.x + 2, wGlowY, wk.w - 4, wGlowH);
         ctx.restore();
         // Highlight the front face bevel too
         if (cache.bevelH > 0) {
           ctx.fillStyle = hl.color;
           ctx.globalAlpha = 0.7;
           drawRoundRect(wk.x + 0.5, wk.h, wk.w - 1, cache.bevelH, [0, 0, 3, 3]);
           ctx.fill();
           ctx.globalAlpha = hl.alpha;
         }
      }
    }

    // Pass 2: Re-render black keys over white highlights
    // First erase any white highlight bleed under black key areas
    ctx.globalAlpha = 1;
    ctx.shadowBlur = 0;
    ctx.shadowOffsetY = 0;
    for (const midi in blackXs) {
      const bk = blackXs[midi];
      ctx.fillStyle = "#1a1a1a";
      ctx.fillRect(bk.x - 1, 0, bk.w + 2, bk.h + 4);
    }
    // Then draw black keys with shadow
    ctx.shadowColor = "rgba(0, 0, 0, 0.7)";
    ctx.shadowBlur = 6;
    ctx.shadowOffsetY = 2;
    for (const midi in blackXs) {
      ctx.globalAlpha = 1;
      const bk = blackXs[midi];
      drawRoundRect(bk.x, 0, bk.w, bk.h, [0, 0, 3, 3]);

      const bgGrad = ctx.createLinearGradient(bk.x, 0, bk.x, bk.h);
      bgGrad.addColorStop(0, "#111");
      bgGrad.addColorStop(1, "#2a2a2a");
      ctx.fillStyle = bgGrad;
      ctx.fill();
    }
    ctx.shadowBlur = 0;

    // Pass 3: draw BLACK key highlights on top
    for (const hl of highlights) {
      const bk = blackXs[hl.midi];
      if (!bk) continue;
      ctx.globalAlpha = Math.min(1.0, hl.alpha + 0.15);
      if (hl.outlineOnly) {
         ctx.fillStyle = hl.color;
         ctx.globalAlpha = hl.fillFaint ? 0.3 : 0.4;
         drawRoundRect(bk.x + 1.5, 1.5, bk.w - 3, bk.h - 3, [0, 0, 3, 3]);
         ctx.fill();
         ctx.globalAlpha = Math.min(1.0, hl.alpha + 0.15);
         ctx.strokeStyle = hl.color;
         ctx.lineWidth = hl.fillFaint ? 1.5 : 2.5;
         if (hl.glow) {
           ctx.shadowBlur = 12;
           ctx.shadowColor = hl.color;
         }
         ctx.stroke();
         ctx.shadowBlur = 0; 
      } else {
         // Solid color fill for full black key
         ctx.fillStyle = hl.color;
         drawRoundRect(bk.x + 0.5, 0.5, bk.w - 1, bk.h - 1, [0, 0, 3, 3]);
         ctx.fill();
         // Lighter wash for top half
         const bTopWash = ctx.createLinearGradient(0, 0, 0, bk.h * 0.4);
         bTopWash.addColorStop(0, "rgba(255,255,255,0.2)");
         bTopWash.addColorStop(1, "rgba(255,255,255,0)");
         ctx.fillStyle = bTopWash;
         ctx.fillRect(bk.x + 0.5, 0.5, bk.w - 1, bk.h * 0.4);
         // Bottom-edge illumination glow for black key
         ctx.save();
         ctx.beginPath();
         ctx.rect(bk.x, 0, bk.w, bk.h);
         ctx.clip();
         const bGlowH = 10;
         const bGlowY = bk.h - bGlowH;
         const bGlowGrad = ctx.createLinearGradient(0, bGlowY, 0, bk.h);
         bGlowGrad.addColorStop(0, "rgba(255,255,255,0)");
         bGlowGrad.addColorStop(1, "rgba(255,255,255,0.45)");
         ctx.fillStyle = bGlowGrad;
         ctx.fillRect(bk.x + 1, bGlowY, bk.w - 2, bGlowH);
         ctx.restore();
      }
    }
    ctx.globalAlpha = 1;

    // Overlap indicator: chord+melody on same key
    for (const m of rMidis) {
      if (lMidis.includes(m)) {
        const pos = whiteXs[m] || blackXs[m];
        if (pos) {
          ctx.fillStyle = LH_COLOR;
          ctx.globalAlpha = 0.9;
          ctx.beginPath();
          ctx.arc(pos.x + pos.w / 2, (pos.h || cache.bKeyH) - 8, 3, 0, Math.PI * 2);
          ctx.fill();
          ctx.globalAlpha = 1;
        }
      }
    }

    // ---- Phase 11 P3: Fingering numbers on piano keys ----
    // Shows current + upcoming (1s lookahead) fingerings
    // Upcoming: dimmer + dashed border (prepare your hand!)
    const fMap = opts && opts.fingeringMap;
    const fNow = (opts && opts.now) || 0;
    if (fMap && Object.keys(fMap).length > 0) {
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";

      for (const midiStr in fMap) {
        const midi = parseInt(midiStr);
        const { finger, hand, upcoming, crossFromPitch } = fMap[midiStr];
        if (!finger) continue;

        const isBlack = !!blackXs[midi];
        const ki = isBlack ? blackXs[midi] : whiteXs[midi];
        if (!ki) continue;

        const circleR = isBlack ? Math.min(ki.w * 0.35, 8) : Math.min(ki.w * 0.35, 10);
        const cx = ki.x + ki.w / 2;
        const cy = isBlack ? ki.h - circleR - 4 : ki.h - circleR - 8;

        // ---- Draw Crossing Arc ----
        if (crossFromPitch !== undefined && crossFromPitch !== null) {
          const fromIsBlack = !!blackXs[crossFromPitch];
          const fromKi = fromIsBlack ? blackXs[crossFromPitch] : whiteXs[crossFromPitch];
          
          if (fromKi) {
            const startX = fromKi.x + fromKi.w / 2;
            const startY = fromIsBlack ? fromKi.h - 12 : fromKi.h - 18;
            const endX = cx;
            const endY = cy;
            
            const midX = (startX + endX) / 2;
            // Arc goes upwards (smaller Y) based on distance
            const arcH = 20 + Math.min(Math.abs(endX - startX) * 0.15, 60);
            const ctrlY = Math.min(startY, endY) - arcH;
            
            ctx.save();
            ctx.beginPath();
            ctx.moveTo(startX, startY);
            ctx.quadraticCurveTo(midX, ctrlY, endX, endY);
            
            // Flowing energy line effect
            const crossColor = hand === "left" ? "#81d4fa" : "#ffcc80";
            
            ctx.strokeStyle = crossColor;
            ctx.lineWidth = 2.5;
            
            // Flowing animation over time
            ctx.shadowBlur = 8;
            ctx.shadowColor = crossColor;
            ctx.globalAlpha = upcoming ? (0.4 + 0.6 * Math.sin(fNow * 8)) : 0.8;
            
            ctx.setLineDash([8, 6]);
            ctx.lineDashOffset = -fNow * 40; 
            
            ctx.stroke();
            ctx.restore();
          }
        }

        if (upcoming) {
          // Upcoming: subtle pulsing circle (prepare hand position)
          const pulse = 0.5 + 0.3 * Math.sin(fNow * 5);
          const circleColor = hand === "left"
            ? `rgba(33, 150, 243, ${pulse})`
            : `rgba(255, 152, 0, ${pulse})`;

          ctx.globalAlpha = 1;
          ctx.fillStyle = circleColor;
          ctx.beginPath();
          ctx.arc(cx, cy, circleR, 0, Math.PI * 2);
          ctx.fill();

          // Dashed border
          ctx.strokeStyle = "rgba(255,255,255,0.6)";
          ctx.lineWidth = 1;
          ctx.setLineDash([2, 2]);
          ctx.stroke();
          ctx.setLineDash([]);

          // Number (slightly transparent)
          ctx.fillStyle = `rgba(255,255,255,${0.5 + pulse * 0.3})`;
          ctx.font = `bold ${Math.round(circleR * 1.3)}px sans-serif`;
          ctx.fillText(String(finger), cx, cy + 0.5);
        } else {
          // Currently playing: solid circle
          const circleColor = hand === "left"
            ? (isBlack ? "rgba(33, 150, 243, 0.95)" : "rgba(33, 150, 243, 0.9)")
            : (isBlack ? "rgba(255, 152, 0, 0.95)" : "rgba(255, 152, 0, 0.9)");

          ctx.globalAlpha = 1;
          ctx.fillStyle = circleColor;
          ctx.beginPath();
          ctx.arc(cx, cy, circleR, 0, Math.PI * 2);
          ctx.fill();

          ctx.strokeStyle = "rgba(255,255,255,0.9)";
          ctx.lineWidth = 1.5;
          ctx.stroke();

          ctx.fillStyle = "#fff";
          ctx.font = `bold ${Math.round(circleR * 1.3)}px sans-serif`;
          ctx.fillText(String(finger), cx, cy + 0.5);
        }
      }
    }

    // ---- Phase 11 P3: Pedal indicator bar ----
    const pedalOn = opts && opts.pedalActive;
    if (cache.bevelH > 0) {
      const bevelY = cache.wKeyH;
      const barH = cache.bevelH;
      if (pedalOn) {
        const depth = (opts && opts.pedalDepth) || 1.0;
        // Glowing green bar when pedal is pressed
        ctx.fillStyle = depth >= 1.0
          ? "rgba(76, 175, 80, 0.6)"
          : "rgba(76, 175, 80, 0.3)";  // half-pedal dimmer
        ctx.fillRect(0, bevelY, w, barH);
        // Label
        ctx.fillStyle = "rgba(255,255,255,0.8)";
        ctx.font = "bold 9px sans-serif";
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        ctx.fillText(depth >= 1.0 ? "PEDAL" : "½ PED", 6, bevelY + barH / 2);
      }
    }

    ctx.globalAlpha = 1;
    ctx.setTransform(1,0,0,1,0,0);
  },

  /**
   * 繪製大型水平吉他指板（Guitar Teaching Mode）
   * @param {HTMLCanvasElement} canvas
   * @param {Object} data - {strings, baseFret, barres, fingers, numStrings, name}
   * @param {Object} opts - {scale, rootNote, highlightRoot}
   */
  drawGuitarFretboard(canvas, data, opts = {}) {
    if (!canvas || !data) return;

    const numStrings = data.numStrings || 6;
    const strings = [...(data.strings || [])];
    const barres = [...(data.barres || [])];

    // Normalize fret data: if baseFret > 1 and frets look relative (small values),
    // convert to absolute frets for consistent rendering.
    const _dataBase = data.baseFret || 1;
    if (_dataBase > 1) {
      const maxRel = Math.max(...strings.filter(f => f > 0), 0);
      if (maxRel > 0 && maxRel <= 5) {
        // Relative frets: convert to absolute
        for (let i = 0; i < strings.length; i++) {
          if (strings[i] > 0) strings[i] = strings[i] + _dataBase - 1;
        }
        // Also convert barres to absolute
        for (let i = 0; i < barres.length; i++) {
          if (barres[i] > 0 && barres[i] <= 5) barres[i] = barres[i] + _dataBase - 1;
        }
      }
    }

    // Auto-calculate baseFret from absolute fret positions
    const fretted = strings.filter(f => f > 0);
    const hasOpen = strings.some(f => f === 0);
    const maxFret = fretted.length > 0 ? Math.max(...fretted) : 1;
    let baseFret = 1;
    if (!hasOpen && maxFret > 4) {
      baseFret = Math.min(...fretted);
    }
    const fingers = data.fingers || [];
    const scale = opts.scale || 1;
    const rootNote = opts.rootNote || null;
    const highlightRoot = opts.highlightRoot !== false;

    // Open string note names — use custom labels/MIDI for ukulele etc.
    const OPEN_MIDI = data._openMidi || [40, 45, 50, 55, 59, 64]; // default: guitar E2 A2 D3 G3 B3 E4
    const STRING_LABELS = data._stringLabels || ["E", "A", "D", "G", "B", "e"];
    const NOTE_NAMES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];

    // Layout
    const numFrets = 4;
    const stringSpacing = 36 * scale;
    const fretSpacing = 100 * scale;
    const leftPad = 56 * scale;  // for string names + X/O markers
    const topPad = 20 * scale;
    const dotR = 14 * scale;
    const fontSize = Math.round(13 * scale);

    const W = leftPad + numFrets * fretSpacing + 30 * scale;
    const H = topPad + (numStrings - 1) * stringSpacing + 50 * scale;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    canvas.style.width = W + "px";
    canvas.style.height = H + "px";
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, W, H);

    // String Y positions (string 0 = low E at bottom, string 5 = high e at top)
    function stringY(i) {
      return topPad + (numStrings - 1 - i) * stringSpacing;
    }
    // Fret X positions
    function fretX(f) {
      return leftPad + f * fretSpacing;
    }

    // Draw fret lines (vertical)
    ctx.strokeStyle = "rgba(255,255,255,0.2)";
    ctx.lineWidth = 1;
    for (let f = 0; f <= numFrets; f++) {
      const x = fretX(f);
      ctx.beginPath();
      ctx.moveTo(x, stringY(numStrings - 1));
      ctx.lineTo(x, stringY(0));
      ctx.stroke();
    }

    // Nut (thick line at fret 0 if baseFret === 1)
    if (baseFret === 1) {
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 4 * scale;
      const x = fretX(0);
      ctx.beginPath();
      ctx.moveTo(x, stringY(numStrings - 1) - 2);
      ctx.lineTo(x, stringY(0) + 2);
      ctx.stroke();
    } else {
      // Show baseFret number
      ctx.fillStyle = "#888";
      ctx.font = `${Math.round(11 * scale)}px sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(baseFret + "", fretX(0) - 12 * scale, stringY(numStrings - 1) - 16 * scale);
    }

    // Draw string lines (horizontal) and labels
    for (let i = 0; i < numStrings; i++) {
      const y = stringY(i);
      const isMuted = strings[i] === -1;

      // String line
      ctx.strokeStyle = isMuted ? "rgba(255,80,80,0.15)" : "rgba(255,255,255,0.35)";
      ctx.lineWidth = isMuted ? 1 : (1.5 + (numStrings - 1 - i) * 0.2) * scale;
      ctx.beginPath();
      ctx.moveTo(fretX(0), y);
      ctx.lineTo(fretX(numFrets), y);
      ctx.stroke();

      // String name (left side)
      ctx.fillStyle = isMuted ? "#e74c3c" : "rgba(255,255,255,0.6)";
      ctx.font = `bold ${fontSize}px sans-serif`;
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      ctx.fillText(STRING_LABELS[i], leftPad - 24 * scale, y);

      // X or O marker
      if (isMuted) {
        ctx.fillStyle = "#e74c3c";
        ctx.font = `bold ${Math.round(16 * scale)}px sans-serif`;
        ctx.textAlign = "center";
        ctx.fillText("✕", leftPad - 10 * scale, y);
      } else if (strings[i] === 0) {
        ctx.strokeStyle = "rgba(255,255,255,0.5)";
        ctx.lineWidth = 1.5 * scale;
        ctx.beginPath();
        ctx.arc(leftPad - 10 * scale, y, 5 * scale, 0, Math.PI * 2);
        ctx.stroke();
      }
    }

    // Fret numbers (bottom)
    ctx.fillStyle = "#666";
    ctx.font = `${Math.round(10 * scale)}px sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (let f = 1; f <= numFrets; f++) {
      const x = fretX(f - 0.5);
      ctx.fillText((baseFret - 1 + f) + "", x, stringY(0) + 14 * scale);
    }

    // Determine root note semitone for highlighting
    let rootSemi = -1;
    if (rootNote) {
      const idx = NOTE_NAMES.indexOf(rootNote);
      if (idx >= 0) rootSemi = idx;
    }

    // Draw barres
    for (const b of barres) {
      const fretPos = b - baseFret + 1;
      if (fretPos < 1 || fretPos > numFrets) continue;
      // Find the range of strings the barre covers
      let lo = numStrings, hi = -1;
      for (let s = 0; s < numStrings; s++) {
        if (strings[s] === b) {
          lo = Math.min(lo, s);
          hi = Math.max(hi, s);
        }
      }
      if (lo <= hi) {
        const x = fretX(fretPos - 0.5);
        const y1 = stringY(hi);
        const y2 = stringY(lo);
        ctx.strokeStyle = "rgba(255,255,255,0.5)";
        ctx.lineWidth = 6 * scale;
        ctx.lineCap = "round";
        ctx.beginPath();
        ctx.moveTo(x, y1);
        ctx.lineTo(x, y2);
        ctx.stroke();
        ctx.lineCap = "butt";
      }
    }

    // Draw finger dots
    for (let i = 0; i < numStrings; i++) {
      const fret = strings[i];
      if (fret <= 0) continue; // skip muted and open

      const fretPos = fret - baseFret + 1;
      if (fretPos < 1 || fretPos > numFrets) continue;

      const x = fretX(fretPos - 0.5);
      const y = stringY(i);

      // Check if this is a root note
      const noteMidi = OPEN_MIDI[i] + fret;
      const noteSemi = noteMidi % 12;
      const isRoot = highlightRoot && rootSemi >= 0 && noteSemi === rootSemi;

      // Draw dot
      ctx.beginPath();
      ctx.arc(x, y, dotR, 0, Math.PI * 2);
      ctx.fillStyle = isRoot ? "#ff9800" : "#9c27b0";
      ctx.fill();
      ctx.strokeStyle = "rgba(255,255,255,0.3)";
      ctx.lineWidth = 1;
      ctx.stroke();

      // Draw finger number
      const finger = fingers[i] || 0;
      if (finger > 0) {
        ctx.fillStyle = "#fff";
        ctx.font = `bold ${Math.round(12 * scale)}px sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(finger + "", x, y);
      }
    }
  },

  /**
   * Draw a VERTICAL fretboard (strings vertical, frets horizontal).
   * Standard guitar teaching orientation.
   * Supports colored finger dots: 1=red, 2=orange, 3=yellow, 4=green
   * @param {HTMLCanvasElement} canvas
   * @param {Object} data - {strings, fingers, baseFret, barres, numStrings, name}
   * @param {Object} opts - {nextData, canvasW, canvasH}
   *   nextData: diagram for the next chord (drawn as ghost)
   */
  drawVerticalFretboard(canvas, data, opts = {}) {
    if (!canvas || !data) return;

    const numStrings = data.numStrings || 6;
    const strings = [...(data.strings || [])];
    const barres = [...(data.barres || [])];
    const fingers = data.fingers || [];

    // Normalize frets (same logic as drawGuitarFretboard)
    const _dataBase = data.baseFret || 1;
    if (_dataBase > 1) {
      const maxRel = Math.max(...strings.filter(f => f > 0), 0);
      if (maxRel > 0 && maxRel <= 5) {
        for (let i = 0; i < strings.length; i++) {
          if (strings[i] > 0) strings[i] = strings[i] + _dataBase - 1;
        }
        for (let i = 0; i < barres.length; i++) {
          if (barres[i] > 0 && barres[i] <= 5) barres[i] = barres[i] + _dataBase - 1;
        }
      }
    }

    const fretted = strings.filter(f => f > 0);
    const hasOpen = strings.some(f => f === 0);
    const maxFret = fretted.length > 0 ? Math.max(...fretted) : 1;
    let baseFret = 1;
    if (!hasOpen && maxFret > 4) baseFret = Math.min(...fretted);
    const numFrets = 5;

    // Use canvas element size
    const dpr = window.devicePixelRatio || 1;
    const W = opts.canvasW || canvas.clientWidth || 240;
    const H = opts.canvasH || canvas.clientHeight || 400;
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    canvas.style.width = W + "px";
    canvas.style.height = H + "px";
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, W, H);

    // Layout: strings vertical, frets horizontal — scale to fit
    const padTop = Math.round(H * 0.06);
    const padBot = Math.round(H * 0.03);
    const padLeft = Math.round(W * 0.1);
    const padRight = Math.round(W * 0.05);
    const stringSpacing = (W - padLeft - padRight) / Math.max(numStrings - 1, 1);
    const fretSpacing = (H - padTop - padBot) / numFrets;
    const dotR = Math.min(stringSpacing * 0.35, fretSpacing * 0.3, 18);

    function strX(s) { return padLeft + s * stringSpacing; }
    function fretY(f) { return padTop + f * fretSpacing; }

    // Finger colors
    const FINGER_CLR = { 1: "#ef5350", 2: "#ff9800", 3: "#ffeb3b", 4: "#66bb6a" };

    // Draw fret lines (horizontal)
    for (let f = 0; f <= numFrets; f++) {
      const y = fretY(f);
      ctx.strokeStyle = f === 0 && baseFret === 1 ? "rgba(255,255,255,0.7)" : "rgba(255,255,255,0.15)";
      ctx.lineWidth = f === 0 && baseFret === 1 ? 3 : 1;
      ctx.beginPath();
      ctx.moveTo(strX(0) - 4, y);
      ctx.lineTo(strX(numStrings - 1) + 4, y);
      ctx.stroke();
    }

    // Draw string lines (vertical)
    for (let s = 0; s < numStrings; s++) {
      const x = strX(s);
      ctx.strokeStyle = "rgba(255,255,255,0.2)";
      ctx.lineWidth = s === 0 ? 1.5 : 1;
      ctx.beginPath();
      ctx.moveTo(x, fretY(0));
      ctx.lineTo(x, fretY(numFrets));
      ctx.stroke();
    }

    // Fret numbers on left
    ctx.fillStyle = "rgba(255,255,255,0.35)";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (let f = 1; f <= numFrets; f++) {
      const fretNum = baseFret + f - 1;
      ctx.fillText(fretNum, padLeft - 8, fretY(f - 1) + fretSpacing / 2);
    }

    // Open/muted markers above nut
    const STRING_LABELS = data._stringLabels || (numStrings === 4 ? ["G","C","E","A"] : ["E","A","D","G","B","e"]);
    ctx.font = "10px sans-serif";
    ctx.textAlign = "center";
    for (let s = 0; s < numStrings; s++) {
      const x = strX(s);
      const fret = strings[s];
      if (fret === 0) {
        ctx.strokeStyle = "rgba(255,255,255,0.5)";
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(x, padTop - 12, 5, 0, Math.PI * 2);
        ctx.stroke();
      } else if (fret === -1) {
        ctx.fillStyle = "rgba(255,255,255,0.35)";
        ctx.font = "bold 12px sans-serif";
        ctx.textBaseline = "middle";
        ctx.fillText("×", x, padTop - 12);
      }
    }

    // Draw barres
    for (const b of barres) {
      const relF = b - baseFret + 1;
      if (relF < 1 || relF > numFrets) continue;
      const y = fretY(relF - 1) + fretSpacing / 2;
      // Find leftmost and rightmost strings in this barre
      let minS = numStrings, maxS = -1;
      for (let s = 0; s < numStrings; s++) {
        if (strings[s] === b) { minS = Math.min(minS, s); maxS = Math.max(maxS, s); }
      }
      if (minS <= maxS) {
        ctx.fillStyle = FINGER_CLR[1] || "rgba(255,255,255,0.5)";
        ctx.globalAlpha = 0.6;
        ctx.beginPath();
        ctx.roundRect(strX(minS) - dotR, y - dotR * 0.7, strX(maxS) - strX(minS) + dotR * 2, dotR * 1.4, dotR * 0.7);
        ctx.fill();
        ctx.globalAlpha = 1;
      }
    }

    // Draw finger dots — current chord (with pressed-down glow)
    for (let s = 0; s < strings.length; s++) {
      const fret = strings[s];
      if (fret <= 0) continue;
      const relF = fret - baseFret + 1;
      if (relF < 1 || relF > numFrets) continue;
      const x = strX(s);
      const y = fretY(relF - 1) + fretSpacing / 2;
      const finger = fingers[s] || 0;
      const color = FINGER_CLR[finger] || "#9c27b0";

      // Glow effect (pressed-down feel)
      ctx.save();
      ctx.shadowColor = color;
      ctx.shadowBlur = dotR * 1.5;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(x, y, dotR, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

      // Inner highlight for 3D pressed effect
      const grad = ctx.createRadialGradient(x - dotR * 0.25, y - dotR * 0.25, 0, x, y, dotR);
      grad.addColorStop(0, "rgba(255,255,255,0.4)");
      grad.addColorStop(0.5, "rgba(255,255,255,0.05)");
      grad.addColorStop(1, "transparent");
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(x, y, dotR, 0, Math.PI * 2);
      ctx.fill();

      // Finger number
      if (finger > 0) {
        ctx.fillStyle = finger === 3 ? "#333" : "#fff";
        ctx.font = `bold ${Math.round(dotR * 1.1)}px sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(finger, x, y);
      }
    }

    // Draw next chord ghost (if provided) — dashed outlines, higher visibility
    const nextData = opts.nextData;
    if (nextData && nextData.strings) {
      const nStrings = [...nextData.strings];
      const nBase = nextData.baseFret || 1;
      if (nBase > 1) {
        const maxR = Math.max(...nStrings.filter(f => f > 0), 0);
        if (maxR > 0 && maxR <= 5) {
          for (let i = 0; i < nStrings.length; i++) {
            if (nStrings[i] > 0) nStrings[i] = nStrings[i] + nBase - 1;
          }
        }
      }

      // Use current chord's baseFret for coordinate mapping
      // If next chord doesn't fit, show a "position shift" label instead
      let allVisible = true;
      for (let s = 0; s < nStrings.length; s++) {
        const fret = nStrings[s];
        if (fret <= 0) continue;
        const relFCur = fret - baseFret + 1;
        if (relFCur < 1 || relFCur > numFrets) { allVisible = false; break; }
      }

      ctx.save();
      const ghostAlpha = opts.nextAlpha != null ? opts.nextAlpha : 0.55;
      ctx.setLineDash(ghostAlpha > 0.6 ? [] : [4, 3]);
      ctx.globalAlpha = ghostAlpha;

      if (allVisible) {
        for (let s = 0; s < nStrings.length; s++) {
          const fret = nStrings[s];
          if (fret <= 0) continue;
          const relFCur = fret - baseFret + 1;
          const x = strX(s);
          const y = fretY(relFCur - 1) + fretSpacing / 2;
          const nFinger = (nextData.fingers || [])[s] || 0;
          const color = FINGER_CLR[nFinger] || "#ff9800";

          ctx.strokeStyle = color;
          ctx.lineWidth = 2.5;
          ctx.beginPath();
          ctx.arc(x, y, dotR * 0.9, 0, Math.PI * 2);
          ctx.stroke();

          if (nFinger > 0) {
            ctx.fillStyle = color;
            ctx.globalAlpha = ghostAlpha * 0.5;
            ctx.beginPath();
            ctx.arc(x, y, dotR * 0.9, 0, Math.PI * 2);
            ctx.fill();
            ctx.globalAlpha = ghostAlpha;
          }
        }
      } else {
        // Next chord is in a different fret region — show label
        ctx.globalAlpha = 0.5;
        ctx.fillStyle = "rgba(255,152,0,0.6)";
        ctx.font = `bold ${Math.round(dotR * 0.9)}px sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        const nextName = nextData.name || "next";
        ctx.fillText("→ " + nextName, W / 2, padTop + fretSpacing * 0.3);
      }

      ctx.restore();
    }
  },
};

// escapeHtml, formatTime moved to utils.js
