/** LiveChord 和弦時間軸編輯器 */

(function () {
  const $ = (sel) => document.querySelector(sel);

  const params = new URLSearchParams(window.location.search);
  const trackPath = params.get("path");
  if (!trackPath) { window.location.href = "/"; return; }

  // ---- state ----
  let chords = [];          // [{time, end, chord}]
  let songKey = "";
  let selectedIdx = -1;
  let pixelsPerSec = parseInt(localStorage.getItem("livechord_editor_zoom")) || 15;
  let paletteChord = "";    // 面板選中的和弦
  let duration = 0;
  let isDragging = false;
  let isResizing = false;
  let dragStartX = 0;
  let dragOrigTime = 0;
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

    try {
      const info = await API.trackInfo(trackPath);
      $("#editorTitle").textContent = `${info.title || ""} — ${info.artist || ""}`;
    } catch {}

    // 載入現有和弦譜
    try {
      const data = await API.getChords(trackPath);
      if (data.exists) {
        chords = data.chords || [];
        songKey = data.key || "";
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

  function tickPlayhead() {
    if (!audio.paused) {
      const t = audio.currentTime;
      const d = audio.duration || 1;
      playhead.style.left = (t * pixelsPerSec) + "px";
      progress.style.width = (t / d * 100) + "%";
      $("#timeCurrent").textContent = formatTime(t);
      requestAnimationFrame(tickPlayhead);
    }
  }

  progressBar.addEventListener("click", (e) => {
    const rect = progressBar.getBoundingClientRect();
    audio.currentTime = (e.clientX - rect.left) / rect.width * (audio.duration || 0);
  });

  $("#volumeSlider").addEventListener("input", (e) => {
    audio.volume = parseFloat(e.target.value);
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
      block.className = "chord-block" + (i === selectedIdx ? " selected" : "");
      block.style.left = (c.time * pixelsPerSec) + "px";
      const w = ((c.end || c.time + 2) - c.time) * pixelsPerSec;
      block.style.width = Math.max(w, 20) + "px";
      block.textContent = c.chord;
      block.dataset.idx = i;

      // resize handle
      const handle = document.createElement("div");
      handle.className = "resize-handle";
      block.appendChild(handle);

      // click to select
      block.addEventListener("mousedown", (e) => {
        if (e.target === handle) {
          // resize
          e.stopPropagation();
          startResize(i, e);
          return;
        }
        e.stopPropagation();
        selectChord(i);
        startDrag(i, e);
      });

      timeline.appendChild(block);
    }
  }

  // ---- select ----

  function selectChord(idx) {
    selectedIdx = idx;
    const c = chords[idx];
    if (c) {
      $("#chordNameInput").value = c.chord;
    }
    render();
  }

  function deselectAll() {
    selectedIdx = -1;
    $("#chordNameInput").value = "";
    render();
  }

  // ---- drag (move) ----

  function startDrag(idx, e) {
    isDragging = true;
    dragStartX = e.clientX;
    dragOrigTime = chords[idx].time;
    dragOrigEnd = chords[idx].end || chords[idx].time + 2;

    function onMove(ev) {
      const dx = ev.clientX - dragStartX;
      const dt = dx / pixelsPerSec;
      const newTime = Math.max(0, dragOrigTime + dt);
      const dur = dragOrigEnd - dragOrigTime;
      chords[idx].time = round2(newTime);
      chords[idx].end = round2(newTime + dur);
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

  function startResize(idx, e) {
    isResizing = true;
    dragStartX = e.clientX;
    dragOrigEnd = chords[idx].end || chords[idx].time + 2;

    function onMove(ev) {
      const dx = ev.clientX - dragStartX;
      const dt = dx / pixelsPerSec;
      const newEnd = Math.max(chords[idx].time + 0.1, dragOrigEnd + dt);
      chords[idx].end = round2(newEnd);
      render();
    }

    function onUp() {
      isResizing = false;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
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
    const x = e.clientX - rect.left + timeline.parentElement.scrollLeft;
    const t = x / pixelsPerSec;

    const chord = paletteChord || prompt("輸入和弦名稱:", "C");
    if (!chord) return;

    chords.push({
      time: round2(t),
      end: round2(t + 4),
      chord: chord,
    });
    sortChords();
    selectedIdx = chords.findIndex((c) => Math.abs(c.time - round2(t)) < 0.01);
    render();
  });

  // ---- update / delete ----

  $("#btnUpdate").addEventListener("click", () => {
    if (selectedIdx < 0) { showToast("請先選取和弦"); return; }
    const name = $("#chordNameInput").value.trim();
    if (!name) { showToast("請輸入和弦名稱"); return; }
    chords[selectedIdx].chord = name;
    render();
    showToast("已更新");
  });

  $("#btnDelete").addEventListener("click", () => {
    if (selectedIdx < 0) { showToast("請先選取和弦"); return; }
    chords.splice(selectedIdx, 1);
    selectedIdx = -1;
    render();
    showToast("已刪除");
  });

  // keyboard shortcuts
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
    if (e.key === "Delete" || e.key === "Backspace") {
      if (selectedIdx >= 0) {
        chords.splice(selectedIdx, 1);
        selectedIdx = -1;
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
    const roots = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];
    const types = ["", "m", "7", "m7", "maj7", "aug", "dim", "sus2", "sus4", "m7b5", "maj9"];

    for (const r of roots) {
      for (const t of types) {
        const name = r + t;
        const btn = document.createElement("button");
        btn.className = "palette-btn";
        btn.textContent = name;
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
        container.appendChild(btn);
      }
    }
  }

  // ---- helpers ----

  function round2(v) { return Math.round(v * 100) / 100; }

  function sortChords() {
    chords.sort((a, b) => a.time - b.time);
  }

  // ---- start ----
  init();
})();
