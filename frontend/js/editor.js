/** LiveChord 和弦時間軸編輯器 */

(function () {
  const $ = (sel) => document.querySelector(sel);

  const params = new URLSearchParams(window.location.search);
  const trackPath = params.get("path");
  if (!trackPath) { window.location.href = "/"; return; }

  // ---- state ----
  let chords = [];          // [{time, end, chord}]
  let songKey = "";
  let songBPM = 120;
  let selectedChords = new Set();
  let lastSelectedChord = null;
  let pixelsPerSec = parseInt(localStorage.getItem("livechord_editor_zoom")) || 15;
  let paletteChord = "";    // 面板選中的和弦
  let duration = 0;
  let isDragging = false;
  let isResizing = false;
  let dragStartX = 0;
  let dragOrigEnd = 0;

  // ---- DOM ----
  const audio = $("#audio");
  const timeline = $("#timeline");
  const ruler = $("#ruler");
  const playhead = $("#playhead");
  const progressBar = $("#progressBar");
  const progress = $("#progress");
  const btnPlay = $("#btnPlay");
  // showToast, formatTime moved to utils.js

  // ---- init ----

  async function init() {
    audio.src = API.trackStreamUrl(trackPath);
    $("#btnBack").href = `/player?path=${encodeURIComponent(trackPath)}`;
    
    // 載入儲存的音量
    const savedVol = localStorage.getItem("livechord_volume");
    if (savedVol !== null) {
        audio.volume = parseFloat(savedVol);
        $("#volumeSlider").value = savedVol;
    }

    try {
      const info = await API.trackInfo(trackPath);
      $("#editorTitle").textContent = `${info.title || ""} — ${info.artist || ""}`;
    } catch {}

    // 載入現有和弦譜
    const version = params.get("version");
    try {
      const data = await API.getChords(trackPath, version);
      if (data.exists) {
        chords = data.chords || [];
        songKey = data.key || "";
        if (data.bpm) songBPM = data.bpm;
        $("#keyInput").value = songKey;
      }
    } catch {}

    $("#zoomSlider").value = pixelsPerSec;

    buildPalette();
    render();
  }

  // ---- audio ----

  audio.addEventListener("loadedmetadata", () => {
    duration = audio.duration;
    $("#timeDuration").textContent = formatTime(duration);
    timeline.style.width = Math.max(duration * pixelsPerSec, 800) + "px";
    buildRuler();
    render();
  });

  btnPlay.addEventListener("click", () => {
    if (audio.paused) audio.play(); else audio.pause();
  });
  audio.addEventListener("play", () => { btnPlay.innerHTML = "&#x23F8;"; tickPlayhead(); });
  audio.addEventListener("pause", () => { btnPlay.innerHTML = "&#x25B6;"; });

  function updatePlayheadUI() {
    const t = audio.currentTime;
    const d = audio.duration || 1;
    const px = t * pixelsPerSec;
    playhead.style.left = px + "px";
    progress.style.width = (t / d * 100) + "%";
    $("#timeCurrent").textContent = formatTime(t);

    if (!audio.paused && !isDragging && !isResizing) {
        const container = timeline.parentElement;
        const viewLeft = container.scrollLeft;
        const viewRight = viewLeft + container.clientWidth;
        
        // 分頁式跟隨：超出右邊界或小於左邊界時，自動往後翻一頁 (播放頭置於左側 10% 處)
        if (px > viewRight - 50 || px < viewLeft) {
            container.scrollLeft = Math.max(0, px - (container.clientWidth * 0.1));
        }
    }
  }

  function tickPlayhead() {
    if (!audio.paused) {
      updatePlayheadUI();
      requestAnimationFrame(tickPlayhead);
    }
  }

  audio.addEventListener("timeupdate", () => {
    if (audio.paused) updatePlayheadUI();
  });
  audio.addEventListener("seeked", updatePlayheadUI);

  progressBar.addEventListener("click", (e) => {
    const rect = progressBar.getBoundingClientRect();
    audio.currentTime = (e.clientX - rect.left) / rect.width * (audio.duration || 0);
  });

  $("#volumeSlider").addEventListener("input", (e) => {
    const vol = parseFloat(e.target.value);
    audio.volume = vol;
    localStorage.setItem("livechord_volume", vol);
  });

  // ---- zoom ----

  $("#zoomSlider").addEventListener("input", (e) => {
    pixelsPerSec = parseInt(e.target.value);
    localStorage.setItem("livechord_editor_zoom", pixelsPerSec);
    if (duration) timeline.style.width = Math.max(duration * pixelsPerSec, 800) + "px";
    buildRuler();
    render();
  });

  // ---- ruler ----

  function buildRuler() {
    ruler.innerHTML = "";
    if (!duration) return;
    const step = pixelsPerSec >= 20 ? 5 : pixelsPerSec >= 10 ? 10 : 30;
    for (let t = 0; t <= duration; t += step) {
      const tick = document.createElement("div");
      tick.className = "tick";
      tick.style.left = (t * pixelsPerSec) + "px";
      tick.textContent = formatTime(t);
      ruler.appendChild(tick);
    }
  }

  // ---- render chord blocks ----

  function render() {
    // 清除舊的 chord-block
    timeline.querySelectorAll(".chord-block").forEach((el) => el.remove());

    for (let i = 0; i < chords.length; i++) {
      const c = chords[i];
      const block = document.createElement("div");
      block.className = "chord-block" + (selectedChords.has(c) ? " selected" : "");
      block.style.left = (c.time * pixelsPerSec) + "px";
      const w = ((c.end || c.time + 2) - c.time) * pixelsPerSec;
      block.style.width = Math.max(w, 20) + "px";
      block.dataset.idx = i;
      
      const beatSec = 60 / songBPM;
      const durSec = (c.end || c.time + 2) - c.time;
      const beats = Math.round((durSec / beatSec) * 10) / 10;
      
      // 始終在方塊內顯示拍時長度，位於右下角
      block.innerHTML = `
          <div style="pointer-events:none;">${c.chord}</div>
          <div style="position:absolute; bottom:2px; right:8px; font-size:10px; opacity:0.5; pointer-events:none;">${beats}</div>
      `;

      // resize handle
      const handle = document.createElement("div");
      handle.className = "resize-handle";
      block.appendChild(handle);

      // click to select
      block.addEventListener("mousedown", (e) => {
        if (e.target === handle) {
          // resize
          e.stopPropagation();
          startResize(c, e);
          return;
        }
        e.stopPropagation();

        let shouldSelectRange = false;
        if (e.shiftKey && lastSelectedChord != null) {
            const startI = chords.indexOf(lastSelectedChord);
            if (startI >= 0) {
                shouldSelectRange = true;
                const min = Math.min(startI, i);
                const max = Math.max(startI, i);
                if (!e.ctrlKey && !e.metaKey) {
                    selectedChords.clear();
                }
                for(let j=min; j<=max; j++) {
                    if (chords[j]) selectedChords.add(chords[j]);
                }
            }
        }
        
        if (!shouldSelectRange) {
            if (e.ctrlKey || e.metaKey) {
                if (selectedChords.has(c)) selectedChords.delete(c);
                else selectedChords.add(c);
            } else {
                selectedChords.clear();
                selectedChords.add(c);
            }
            lastSelectedChord = c;
        }

        selectChord(c);
        startDrag(c, e);
      });

      timeline.appendChild(block);
    }
  }

  // ---- select ----

  function selectChord(c) {
    if (c) {
      $("#chordNameInput").value = c.chord;
    }
    render();
  }

  function deselectAll() {
    selectedChords.clear();
    lastSelectedChord = null;
    $("#chordNameInput").value = "";
    render();
  }

  // ---- drag (move) ----

  function startDrag(c, e) {
    if (!selectedChords.has(c)) {
        selectedChords.clear();
        selectedChords.add(c);
        lastSelectedChord = c;
        selectChord(c);
    }
    
    isDragging = true;
    dragStartX = e.clientX;
    
    let activeChords = Array.from(selectedChords);
    
    // Alt-Drag to clone
    if (e.altKey) {
        let newSelection = new Set();
        let clonedArr = [];
        activeChords.forEach(item => {
            const clone = { time: item.time, end: item.end, chord: item.chord };
            chords.push(clone);
            newSelection.add(clone);
            clonedArr.push(clone);
        });
        selectedChords = newSelection;
        lastSelectedChord = clonedArr[clonedArr.length - 1];
        activeChords = clonedArr;
    }

    const origCoords = activeChords.map(item => ({ item: item, origTime: item.time, origEnd: item.end || item.time + 2 }));

    function onMove(ev) {
      const dx = ev.clientX - dragStartX;
      const dt = dx / pixelsPerSec;
      
      origCoords.forEach(obj => {
         let newTime = Math.max(0, obj.origTime + dt);
         newTime = snapTime(newTime);
         const dur = obj.origEnd - obj.origTime;
         obj.item.time = newTime;
         obj.item.end = newTime + dur;
      });
      render();
    }

    function onUp() {
      isDragging = false;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      sortChords();
      render();
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  // ---- resize ----

  function startResize(c, e) {
    isResizing = true;
    dragStartX = e.clientX;
    dragOrigEnd = c.end || c.time + 2;
    const origTime = c.time;

    function onMove(ev) {
      const dx = ev.clientX - dragStartX;
      const dt = dx / pixelsPerSec;
      let newEnd = Math.max(origTime + 0.1, dragOrigEnd + dt);
      newEnd = snapTime(newEnd);
      if (newEnd <= origTime) newEnd = origTime + 0.1;
      c.end = newEnd;
      render();
    }

    function onUp() {
      isResizing = false;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      sortChords();
      render();
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  // ---- timeline click = add chord ----

  timeline.addEventListener("click", (e) => {
    if (isDragging || isResizing) return;
    if (e.target.closest(".chord-block")) return;

    const rect = timeline.getBoundingClientRect();
    const x = e.clientX - rect.left;
    let t = x / pixelsPerSec;
    
    // 如果點擊的是時間尺規區，移動播放頭
    if (e.target.closest(".ruler") || e.clientY - rect.top <= 24) {
        audio.currentTime = Math.max(0, Math.min(audio.duration || 0, t));
        return;
    }

    t = snapTime(t);

    const chord = paletteChord || prompt("輸入和弦名稱:", "C");
    if (!chord) return;

    const newC = { time: t, end: t + 4, chord: chord };
    chords.push(newC);
    sortChords();
    selectedChords.clear();
    selectedChords.add(newC);
    lastSelectedChord = newC;
    render();
  });

  // ---- update / delete ----

  $("#btnUpdate").addEventListener("click", () => {
    if (selectedChords.size === 0) { showToast("請先選取和弦"); return; }
    const name = $("#chordNameInput").value.trim();
    if (!name) { showToast("請輸入和弦名稱"); return; }
    selectedChords.forEach(c => c.chord = name);
    render();
    showToast("批次更新" + selectedChords.size + "個和弦");
  });

  $("#btnDelete").addEventListener("click", () => {
    if (selectedChords.size === 0) { showToast("請先選取和弦"); return; }
    chords = chords.filter(c => !selectedChords.has(c));
    selectedChords.clear();
    lastSelectedChord = null;
    render();
    showToast("已刪除");
  });

  // ---- nudge ----
  
  if ($("#btnNudgeLeft")) {
      $("#btnNudgeLeft").addEventListener("click", () => {
          if(selectedChords.size === 0) return;
          selectedChords.forEach(c => {
              c.time = Math.max(0, c.time - 0.25);
              if (c.end) c.end = Math.max(0.1, c.end - 0.25);
          });
          sortChords();
          render();
          showToast("選取的和弦向左移 0.25s");
      });
  }

  if ($("#btnNudgeRight")) {
      $("#btnNudgeRight").addEventListener("click", () => {
          if(selectedChords.size === 0) return;
          selectedChords.forEach(c => {
              c.time = c.time + 0.25;
              if (c.end) c.end = c.end + 0.25;
          });
          sortChords();
          render();
          showToast("選取的和弦向右移 0.25s");
      });
  }

  if ($("#btnQuantize")) {
      $("#btnQuantize").addEventListener("click", () => {
          if (selectedChords.size < 2) {
              showToast("請至少選取 2 個連續的和弦以進行拍數正規化");
              return;
          }
          const selArr = Array.from(selectedChords).sort((a,b) => a.time - b.time);
          const beatSec = 60 / songBPM;
          const resolution = beatSec / 2; // snap to nearest 0.5 beat (eighth note)

          // We use the first chord's time as the unmovable anchor
          let currAnchorTime = selArr[0].time;
          
          for (let i = 0; i < selArr.length - 1; i++) {
              let cCurr = selArr[i];
              let cNext = selArr[i + 1];
              
              // Calculate ideal quantized duration between current and next
              let diff = cNext.time - cCurr.time;
              let quantizedDiff = Math.abs(diff) < 0.02 ? 0 : Math.round(diff / resolution) * resolution;
              
              // Prevent duration from collapsing to 0
              if (quantizedDiff <= 0) quantizedDiff = resolution;
              
              // The next chord's time is set explicitly based on the idealized duration
              cNext.time = currAnchorTime + quantizedDiff;
              
              // Ensure the current chord's visual end matches the next chord
              cCurr.end = cNext.time;
              
              currAnchorTime = cNext.time;
          }
          
          // Optionally fix the visual end of the absolute last selected chord
          let cLast = selArr[selArr.length - 1];
          let defaultDur = cLast.end ? (cLast.end - cLast.time) : (4 * beatSec);
          let quantizedDur = Math.round(defaultDur / resolution) * resolution;
          if (quantizedDur <= 0) quantizedDur = resolution;
          cLast.end = cLast.time + quantizedDur;

          sortChords();
          render();
          showToast(`已將 ${selArr.length} 個和弦的節拍長度正規化`);
      });
  }

  let clipboardChords = [];

  // keyboard shortcuts
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;

    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c") {
        if (selectedChords.size > 0) {
            clipboardChords = Array.from(selectedChords).map(c => ({...c})).sort((a, b) => a.time - b.time);
            showToast(`已複製 ${clipboardChords.length} 個和弦`);
        }
        return;
    }

    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "v") {
        if (clipboardChords.length > 0) {
            selectedChords.clear();
            const baseTime = clipboardChords[0].time;
            const pasteTime = snapTime(audio.currentTime);
            const timeOffset = pasteTime - baseTime;
            
            clipboardChords.forEach(c => {
                const dur = (c.end || c.time + 2) - c.time;
                const newC = { time: snapTime(c.time + timeOffset), end: snapTime(c.time + timeOffset + dur), chord: c.chord };
                chords.push(newC);
                selectedChords.add(newC);
                lastSelectedChord = newC;
            });
            sortChords();
            render();
            showToast(`貼上 ${clipboardChords.length} 個和弦`);
        }
        return;
    }

    if (e.key === "Delete" || e.key === "Backspace") {
      if (selectedChords.size > 0) {
        chords = chords.filter(c => !selectedChords.has(c));
        selectedChords.clear();
        lastSelectedChord = null;
        render();
      }
    }
    if (e.key === " ") {
      e.preventDefault();
      if (audio.paused) audio.play(); else audio.pause();
    }
    if (e.key === "ArrowLeft") {
        e.preventDefault();
        audio.currentTime = Math.max(0, audio.currentTime - 5);
    }
    if (e.key === "ArrowRight") {
        e.preventDefault();
        audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + 5);
    }
    if (e.key === "Escape") deselectAll();
  });

  // ---- save ----

  $("#btnSave").addEventListener("click", async () => {
    songKey = $("#keyInput").value.trim();
    try {
      await API.saveChords({
        path: trackPath,
        key: songKey,
        capo: 0,
        chords: chords,
      });
      showToast("已儲存！", 2000);
    } catch (err) {
      showToast("儲存失敗: " + err.message, 3000);
    }
  });

  // ---- import ChordPro ----

  $("#btnImport").addEventListener("click", () => {
    const input = prompt(
      "貼上 ChordPro 格式的和弦（每行一個），例如:\n" +
      "[C]  [Am]  [F]  [G]\n" +
      "或帶時間: 0:00 C  0:04 Am  0:08 F");
    if (!input) return;

    const lines = input.trim().split("\n");
    let t = audio.currentTime || 0;

    for (const line of lines) {
      // 嘗試解析 "時間 和弦" 格式
      const m = line.match(/^(\d+):(\d+)\s+(.+)/);
      if (m) {
        t = parseInt(m[1]) * 60 + parseInt(m[2]);
        const names = m[3].trim().split(/\s+/);
        for (const name of names) {
          const clean = name.replace(/[\[\]]/g, "");
          if (clean) {
            chords.push({ time: round2(t), end: round2(t + 4), chord: clean });
            t += 4;
          }
        }
      } else {
        // 解析 [C] [Am] 格式
        const matches = line.match(/\[([A-G][^\]]*)\]/g);
        if (matches) {
          for (const m of matches) {
            const name = m.replace(/[\[\]]/g, "");
            chords.push({ time: round2(t), end: round2(t + 4), chord: name });
            t += 4;
          }
        } else {
          // 純和弦名稱，空格分隔
          const names = line.trim().split(/\s+/);
          for (const name of names) {
            if (name.match(/^[A-G]/)) {
              chords.push({ time: round2(t), end: round2(t + 4), chord: name });
              t += 4;
            }
          }
        }
      }
    }

    sortChords();
    render();
    showToast(`匯入 ${chords.length} 個和弦`);
  });

  // ---- chord palette ----

  function buildPalette() {
    const container = $("#chordPalette");
    container.innerHTML = "";
    container.style.display = "block";
    container.style.overflowX = "auto";
    
    // 以各個根音為一列
    const rootGroups = [
        ["C", "Db", "C#"],
        ["D", "Eb", "D#"],
        ["E"],
        ["F", "Gb", "F#"],
        ["G", "Ab", "G#"],
        ["A", "Bb", "A#"],
        ["B"]
    ];
    
    const types = ["", "m", "7", "m7", "maj7", "aug", "dim", "sus2", "sus4", "m7b5", "maj9"];

    for (const group of rootGroups) {
      // 每個群組 (如 C 及其升降記號) 放成一區塊，或是每列放一個根音
      for (const r of group) {
          const row = document.createElement("div");
          row.style.display = "flex";
          row.style.gap = "6px";
          row.style.marginBottom = "6px";
          
          const label = document.createElement("div");
          label.textContent = r;
          label.style.width = "30px";
          label.style.fontWeight = "bold";
          label.style.display = "flex";
          label.style.alignItems = "center";
          label.style.justifyContent = "center";
          label.style.color = "var(--text-dim)";
          label.style.background = "rgba(255,255,255,0.05)";
          label.style.borderRadius = "4px";
          row.appendChild(label);

          for (const t of types) {
            const name = r + t;
            const btn = document.createElement("button");
            btn.className = "palette-btn";
            btn.textContent = name;
            btn.style.flex = "1";
            btn.addEventListener("click", () => {
              container.querySelectorAll(".palette-btn").forEach((b) =>
                b.classList.remove("selected")
              );
              if (paletteChord === name) {
                paletteChord = "";
              } else {
                paletteChord = name;
                btn.classList.add("selected");
              }
            });
            row.appendChild(btn);
          }
          container.appendChild(row);
      }
      // 加入分隔線
      const sep = document.createElement("div");
      sep.style.height = "1px";
      sep.style.background = "rgba(255,255,255,0.1)";
      sep.style.margin = "8px 0";
      container.appendChild(sep);
    }
  }

  // ---- helpers ----

  function round2(v) { return Math.round(v * 100) / 100; }
  
  function snapTime(v) {
      const chk = $("#chkSnap");
      // 自適應 BPM 貼齊 (預設為 8 分音符 = 0.5 拍)
      if (chk && chk.checked) {
          const beatSec = 60 / songBPM;
          const snapResolution = beatSec / 2;
          return Math.round(v / snapResolution) * snapResolution;
      }
      return round2(v);
  }

  function sortChords() {
    chords.sort((a, b) => a.time - b.time);
  }

  // ---- start ----
  init();
})();
