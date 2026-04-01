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
    const strings = data.strings || [];
    const baseFret = data.baseFret || 1;
    const barres = data.barres || [];

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
   */
  drawPiano(canvas, notes, scale = 1, prevMidi = null) {
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
};

function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str || "";
  return d.innerHTML;
}

function formatTime(sec) {
  if (sec == null) return "";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
