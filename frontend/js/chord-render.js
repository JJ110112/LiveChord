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
  drawPiano(canvas, notes, scale = 1) {
    if (!canvas) return;
    notes = notes || [];

    // 音名→半音對照
    const NOTE_MAP = { C:0, "C#":1, Db:1, D:2, "D#":3, Eb:3, E:4, Fb:4, F:5, "F#":6, Gb:6, G:7, "G#":8, Ab:8, A:9, "A#":10, Bb:10, B:11, Cb:11 };
    const pressed = new Set();
    for (const n of notes) {
      const clean = n.replace(/bb|##/g, (m) => m === "bb" ? "♭♭" : "♯♯");
      if (n in NOTE_MAP) pressed.add(NOTE_MAP[n]);
      else {
        // handle double accidentals
        const base = n[0];
        let s = NOTE_MAP[base] || 0;
        for (let i = 1; i < n.length; i++) {
          if (n[i] === "#") s++;
          else if (n[i] === "b") s--;
        }
        pressed.add(((s % 12) + 12) % 12);
      }
    }

    // 繪製一個八度的鍵盤 (C ~ B)
    const ww = 14; // white key width
    const wh = 40; // white key height
    const bw = 9;  // black key width
    const bh = 24; // black key height
    const whites = [0, 2, 4, 5, 7, 9, 11]; // C D E F G A B
    const blacks = [1, 3, -1, 6, 8, 10];    // C# D# _ F# G# A#  (-1 = skip between E-F)

    const totalW = ww * 7 + 2;
    const totalH = wh + 12; // extra space for note labels
    const dpr = window.devicePixelRatio || 1;

    canvas.width = Math.round(totalW * scale * dpr);
    canvas.height = Math.round(totalH * scale * dpr);
    canvas.style.width = Math.round(totalW * scale) + "px";
    canvas.style.height = Math.round(totalH * scale) + "px";

    const ctx = canvas.getContext("2d");
    ctx.scale(scale * dpr, scale * dpr);
    ctx.clearRect(0, 0, totalW, totalH);

    const x0 = 1;

    // 白鍵
    for (let i = 0; i < 7; i++) {
      const x = x0 + i * ww;
      const semi = whites[i];
      const isPressed = pressed.has(semi);
      ctx.fillStyle = isPressed ? "#e94560" : "#f8f8f8";
      ctx.fillRect(x, 0, ww - 1, wh);
      ctx.strokeStyle = "#555";
      ctx.lineWidth = 0.5;
      ctx.strokeRect(x, 0, ww - 1, wh);

      // 按下時標記音名
      if (isPressed) {
        ctx.fillStyle = "#fff";
        ctx.font = `bold ${Math.max(8, 8)}px sans-serif`;
        ctx.textAlign = "center";
        ctx.fillText(["C","D","E","F","G","A","B"][i], x + (ww - 1) / 2, wh - 3);
      }
    }

    // 黑鍵
    const blackPositions = [0, 1, 3, 4, 5]; // 在第 0,1,3,4,5 個白鍵右邊
    const blackSemitones = [1, 3, 6, 8, 10];
    for (let i = 0; i < 5; i++) {
      const wp = blackPositions[i];
      const x = x0 + (wp + 1) * ww - bw / 2 - 0.5;
      const semi = blackSemitones[i];
      const isPressed = pressed.has(semi);
      ctx.fillStyle = isPressed ? "#e94560" : "#222";
      ctx.fillRect(x, 0, bw, bh);
      ctx.strokeStyle = "#000";
      ctx.lineWidth = 0.5;
      ctx.strokeRect(x, 0, bw, bh);

      if (isPressed) {
        ctx.fillStyle = "#fff";
        ctx.font = "bold 6px sans-serif";
        ctx.textAlign = "center";
        const labels = ["C#","D#","F#","G#","A#"];
        ctx.fillText(labels[i], x + bw / 2, bh - 2);
      }
    }
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
