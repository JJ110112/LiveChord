"use strict";
// i18n helper — fall back to the dict key if i18n.js hasn't booted yet.
function _t(k, v) { return (window.LiveChordI18n && window.LiveChordI18n.t) ? window.LiveChordI18n.t(k, v) : k; }

/** LiveChord 和弦時間軸編輯器 */

(function () {
  const $ = (sel) => document.querySelector(sel);

  const params = new URLSearchParams(window.location.search);
  const trackPath = params.get("path");
  // `encodeURIComponent(null)` from the player used to land users on
  // `/editor?path=null` with an empty timeline. Treat the literal "null" /
  // "undefined" strings as missing too.
  if (!trackPath || trackPath === "null" || trackPath === "undefined") {
    window.location.href = "/";
    return;
  }

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

    // Mirror the player's BPM-multiplier so what the user sees in the editor
    // matches what they were looking at in the player. Without this, a user
    // who tapped the player's BPM badge down to 68 lands on an editor still
    // using the raw 136 from the JSON — every chord's dot count doubles and
    // "the BPM seems wrong" is the first thing they hit.
    try {
      const mult = parseFloat(localStorage.getItem(`bpm_mult_${trackPath}`));
      if (mult > 0 && mult !== 1) songBPM = songBPM * mult;
    } catch {}

    // Section bands (phrase overlay above chord blocks). Non-blocking —
    // bands render when the fetch resolves; editor stays usable meanwhile.
    _loadSections().catch(() => {});

    $("#zoomSlider").value = pixelsPerSec;
    _updateBpmDisplay();

    buildPalette();
    render();
  }

  // Seek to the playhead time the user was at in the player (URL param `t`).
  // Timeline auto-scrolls to follow the playhead via `updatePlayheadUI`.
  const seekParam = parseFloat(params.get("t") || "");
  if (seekParam > 0 && isFinite(seekParam)) {
    const applySeek = () => {
      try {
        audio.currentTime = Math.min(audio.duration || seekParam, seekParam);
        // updatePlayheadUI's scroll branch only fires while playing; force
        // an initial centered scroll so the relevant chords are on-screen.
        requestAnimationFrame(() => {
          const container = timeline.parentElement;
          const px = seekParam * pixelsPerSec;
          container.scrollLeft = Math.max(0, px - container.clientWidth * 0.3);
        });
      } catch {}
    };
    audio.addEventListener("loadedmetadata", applySeek, { once: true });
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
    // Sections reflow on zoom because px/sec changes
    _renderSectionBands();
  }

  // ---- section (phrase) bands ----

  let _sectionData = null;

  async function _loadSections() {
    try {
      const r = await fetch(`/api/ai/sections?path=${encodeURIComponent(trackPath)}`);
      if (!r.ok) return;
      const data = await r.json();
      _sectionData = (data && Array.isArray(data.sections)) ? data.sections : [];
      _renderSectionBands();
    } catch {}
  }

  function _renderSectionBands() {
    const bands = $("#sectionBands");
    if (!bands) return;
    bands.innerHTML = "";
    if (!_sectionData || _sectionData.length === 0) return;
    // Type counts for numbering (e.g., Verse 1 / Verse 2)
    const typeCounts = {};
    _sectionData.forEach(s => { typeCounts[s.type] = (typeCounts[s.type] || 0) + 1; });
    const noNumberTypes = ["intro", "outro", "dialogue"];
    const typeSeen = {};
    for (const s of _sectionData) {
      const left = s.start * pixelsPerSec;
      const width = Math.max(2, (s.end - s.start) * pixelsPerSec);
      typeSeen[s.type] = (typeSeen[s.type] || 0) + 1;
      const total = typeCounts[s.type] || 1;
      const numStr = (total > 1 && !noNumberTypes.includes(s.type)) ? ` ${typeSeen[s.type]}` : "";
      const label = (s.label || s.type || "") + numStr;
      const color = s.color || "#888";
      const band = document.createElement("div");
      band.className = "section-band";
      band.style.left = left + "px";
      band.style.width = width + "px";
      band.style.background = color;
      band.textContent = label;
      band.title = `${label} — ${formatTime(s.start)} ~ ${formatTime(s.end)}`;
      bands.appendChild(band);
    }
  }

  function _updateBpmDisplay() {
    const el = $("#bpmDisplay");
    if (el) el.textContent = Math.round(songBPM);
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
      const dotCount = Math.max(1, Math.min(16, Math.round(durSec / beatSec)));
      let dotsHtml = "";
      for (let d = 0; d < dotCount; d++) dotsHtml += '<span class="beat-dot"></span>';

      // 方塊內：和弦名（置中）、節拍點（底部中央）、拍數 0.1 精度（右下角）
      block.innerHTML = `
          <div style="pointer-events:none;">${c.chord}</div>
          <div class="chord-block-beats">${dotsHtml}</div>
          <div style="position:absolute; bottom:2px; right:8px; font-size:10px; opacity:0.5; pointer-events:none;">${beats}</div>
      `;

      // resize handle
      const handle = document.createElement("div");
      handle.className = "resize-handle";
      block.appendChild(handle);

      // Right-click → quick beat-count adjust (cascades subsequent chords
      // so the downstream timing stays coherent with the music).
      block.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        e.stopPropagation();
        _showBeatAdjustPopup(c, block, e);
      });

      // click to select
      block.addEventListener("mousedown", (e) => {
        if (e.target === handle) {
          // resize
          e.stopPropagation();
          startResize(c, e);
          return;
        }
        e.stopPropagation();

        // Palette-armed replace: if a palette chord is selected, tapping an
        // existing block rewrites its name in place. Skips drag/multi-select
        // so the user can chain-tap multiple blocks with the same palette pick.
        if (paletteChord && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
          c.chord = paletteChord;
          selectedChords.clear();
          selectedChords.add(c);
          lastSelectedChord = c;
          selectChord(c);
          showToast(_t("editor.toast.replaced_with", { chord: paletteChord }));
          return;
        }

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

    const chord = paletteChord || prompt(_t("editor.prompt.chord_name"), "C");
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
    if (selectedChords.size === 0) { showToast(_t("editor.toast.select_first")); return; }
    const name = $("#chordNameInput").value.trim();
    if (!name) { showToast(_t("editor.toast.need_chord_name")); return; }
    selectedChords.forEach(c => c.chord = name);
    render();
    showToast(_t("editor.toast.batch_updated", { n: selectedChords.size }));
  });

  $("#btnDelete").addEventListener("click", () => {
    if (selectedChords.size === 0) { showToast(_t("editor.toast.select_first")); return; }
    chords = chords.filter(c => !selectedChords.has(c));
    selectedChords.clear();
    lastSelectedChord = null;
    render();
    showToast(_t("editor.toast.deleted"));
  });

  // ---- replace all ----

  $("#btnReplaceAll").addEventListener("click", () => {
    const from = prompt(_t("editor.prompt.replace_from"));
    if (!from) return;
    const to = prompt(_t("editor.prompt.replace_to", { from }));
    if (!to) return;
    let count = 0;
    chords.forEach(c => { if (c.chord === from) { c.chord = to; count++; } });
    if (count === 0) {
      showToast(_t("editor.toast.no_match", { from }));
    } else {
      render();
      showToast(_t("editor.toast.replaced_n", { count, from, to }));
    }
  });

  // ---- split ----

  let splitTarget = null;

  function showSplitPopup(chord, anchorEl) {
    splitTarget = chord;
    const beatSec = 60 / songBPM;
    const durSec = (chord.end || chord.time + 2) - chord.time;
    const totalBeats = Math.round((durSec / beatSec) * 2) / 2; // round to 0.5

    const optionsEl = $("#splitOptions");
    optionsEl.innerHTML = "";
    const step = 0.5;
    for (let left = step; left < totalBeats; left += step) {
      const right = Math.round((totalBeats - left) * 10) / 10;
      if (right < step) continue;
      // only show "round" ratios to keep UI clean (integer or .5)
      if (left % 0.5 !== 0 || right % 0.5 !== 0) continue;
      const btn = document.createElement("button");
      btn.className = "toolbar-btn";
      btn.style.cssText = "padding:4px 10px;font-size:12px;";
      btn.textContent = `${left} / ${right}`;
      btn.addEventListener("click", () => { doSplit(left, right); });
      optionsEl.appendChild(btn);
    }

    // pre-fill custom with even split
    const half = Math.round(totalBeats / 2 * 2) / 2;
    $("#splitCustomLeft").value = half;
    $("#splitCustomRight").value = Math.round((totalBeats - half) * 10) / 10;

    // position popup near the chord block
    const popup = $("#splitPopup");
    popup.style.display = "block";
    if (anchorEl) {
      const rect = anchorEl.getBoundingClientRect();
      popup.style.left = Math.min(rect.left, window.innerWidth - 280) + "px";
      popup.style.top = (rect.bottom + 4) + "px";
    }
  }

  function hideSplitPopup() {
    $("#splitPopup").style.display = "none";
    splitTarget = null;
  }

  // ---- right-click: adjust beat count of a single chord ----

  let _beatAdjustTarget = null;

  function _showBeatAdjustPopup(chord, anchorEl, ev) {
    _beatAdjustTarget = chord;
    const popup = $("#beatAdjustPopup");
    if (!popup) return;
    const beatSec = 60 / songBPM;
    const currentDur = (chord.end || chord.time + 2) - chord.time;
    const currentBeats = Math.round((currentDur / beatSec) * 10) / 10;
    $("#beatAdjustCurrent").textContent = _t("editor.beat_adjust.current", { n: currentBeats });
    const opts = $("#beatAdjustOptions");
    opts.innerHTML = "";
    // Common beat counts + .5 options for syncopation
    const choices = [0.5, 1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 12, 16];
    for (const beats of choices) {
      const btn = document.createElement("button");
      btn.className = "toolbar-btn";
      btn.style.cssText = "padding:3px 10px;font-size:12px;min-width:36px";
      btn.textContent = String(beats);
      if (Math.abs(beats - currentBeats) < 0.05) {
        btn.style.outline = "2px solid var(--accent)";
      }
      btn.addEventListener("click", () => _applyBeatAdjust(beats));
      opts.appendChild(btn);
    }
    popup.style.display = "block";
    // Position near the right-click
    const x = ev && typeof ev.clientX === "number" ? ev.clientX : (anchorEl?.getBoundingClientRect().left || 0);
    const y = ev && typeof ev.clientY === "number" ? ev.clientY : (anchorEl?.getBoundingClientRect().bottom || 0);
    popup.style.left = Math.min(x, window.innerWidth - 240) + "px";
    popup.style.top = Math.min(y + 4, window.innerHeight - 150) + "px";
  }

  function _hideBeatAdjustPopup() {
    $("#beatAdjustPopup").style.display = "none";
    _beatAdjustTarget = null;
  }

  function _applyBeatAdjust(newBeats) {
    if (!_beatAdjustTarget) return;
    const chord = _beatAdjustTarget;
    const beatSec = 60 / songBPM;
    const oldEnd = chord.end || chord.time + 2;
    const newEnd = chord.time + newBeats * beatSec;
    const delta = newEnd - oldEnd;
    const idx = chords.indexOf(chord);
    chord.end = newEnd;
    // Cascade: shift every subsequent chord by `delta` so their relative
    // placement is preserved. Without this, shortening this chord leaves
    // a gap and growing it overlaps the next one.
    if (idx >= 0 && Math.abs(delta) > 0.001) {
      for (let j = idx + 1; j < chords.length; j++) {
        chords[j].time += delta;
        if (chords[j].end) chords[j].end += delta;
      }
    }
    sortChords();
    render();
    _hideBeatAdjustPopup();
    showToast(_t("editor.toast.beats_changed", { n: newBeats, after: chords.length - idx - 1 }));
  }

  document.addEventListener("click", (e) => {
    if ($("#beatAdjustPopup").style.display !== "none" && !e.target.closest("#beatAdjustPopup")) {
      _hideBeatAdjustPopup();
    }
  });

  function doSplit(leftBeats, rightBeats) {
    if (!splitTarget) return;
    const chord = splitTarget;
    const beatSec = 60 / songBPM;
    const splitTime = chord.time + leftBeats * beatSec;
    const origEnd = chord.end || chord.time + 2;

    chord.end = splitTime;
    const newChord = { time: splitTime, end: origEnd, chord: chord.chord };
    const idx = chords.indexOf(chord);
    chords.splice(idx + 1, 0, newChord);
    sortChords();

    selectedChords.clear();
    selectedChords.add(newChord);
    lastSelectedChord = newChord;
    render();
    hideSplitPopup();
    showToast(_t("editor.toast.split_into", { l: leftBeats, r: rightBeats }));
  }

  $("#btnSplit").addEventListener("click", () => {
    if (selectedChords.size !== 1) {
      showToast(_t("editor.toast.select_one_to_split"));
      return;
    }
    const chord = selectedChords.values().next().value;
    const blockEl = timeline.querySelector(`.chord-block[data-idx="${chords.indexOf(chord)}"]`);
    showSplitPopup(chord, blockEl);
  });

  $("#splitCustomBtn").addEventListener("click", () => {
    const left = parseFloat($("#splitCustomLeft").value);
    const right = parseFloat($("#splitCustomRight").value);
    if (!left || !right || left <= 0 || right <= 0) {
      showToast(_t("editor.toast.invalid_beats"));
      return;
    }
    doSplit(left, right);
  });

  document.addEventListener("click", (e) => {
    if ($("#splitPopup").style.display !== "none" &&
        !e.target.closest("#splitPopup") &&
        !e.target.closest("#btnSplit")) {
      hideSplitPopup();
    }
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
          showToast(_t("editor.toast.nudged_left"));
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
          showToast(_t("editor.toast.nudged_right"));
      });
  }

  if ($("#btnQuantize")) {
      $("#btnQuantize").addEventListener("click", () => {
          if (selectedChords.size < 2) {
              showToast(_t("editor.toast.need_2_for_quantize"));
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
          showToast(_t("editor.toast.quantized_n", { n: selArr.length }));
      });
  }

  if ($("#btnQuantizeAll")) {
      $("#btnQuantizeAll").addEventListener("click", () => {
          if (chords.length < 2) { showToast(_t("editor.toast.need_2_for_quantize_all")); return; }
          if (!confirm(_t("editor.confirm.quantize_all", { n: chords.length }))) return;
          const beatSec = 60 / songBPM;
          const resolution = beatSec / 2;
          let currAnchorTime = chords[0].time;
          for (let i = 0; i < chords.length - 1; i++) {
              const cCurr = chords[i], cNext = chords[i + 1];
              const diff = cNext.time - cCurr.time;
              let quantizedDiff = Math.abs(diff) < 0.02 ? 0 : Math.round(diff / resolution) * resolution;
              if (quantizedDiff <= 0) quantizedDiff = resolution;
              cNext.time = currAnchorTime + quantizedDiff;
              cCurr.end = cNext.time;
              currAnchorTime = cNext.time;
          }
          const cLast = chords[chords.length - 1];
          const lastDur = (cLast.end || cLast.time + 2) - cLast.time;
          let lastQ = Math.round(lastDur / resolution) * resolution;
          if (lastQ <= 0) lastQ = resolution;
          cLast.end = cLast.time + lastQ;
          sortChords();
          render();
          showToast(_t("editor.toast.quantized_all", { n: chords.length }));
      });
  }

  let clipboardChords = [];

  // keyboard shortcuts
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;

    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "h") {
        e.preventDefault();
        $("#btnReplaceAll").click();
        return;
    }

    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c") {
        if (selectedChords.size > 0) {
            clipboardChords = Array.from(selectedChords).map(c => ({...c})).sort((a, b) => a.time - b.time);
            showToast(_t("editor.toast.copied_n", { n: clipboardChords.length }));
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
            showToast(_t("editor.toast.pasted_n", { n: clipboardChords.length }));
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
    if (e.key === "?") {
      const overlay = $("#helpOverlay");
      const backdrop = $("#helpBackdrop");
      const show = overlay.style.display === "none" || !overlay.style.display;
      overlay.style.display = show ? "block" : "none";
      backdrop.style.display = show ? "block" : "none";
      return;
    }
    if (e.key === "Escape") {
      if ($("#helpOverlay").style.display === "block") {
        $("#helpOverlay").style.display = "none";
        $("#helpBackdrop").style.display = "none";
        return;
      }
      if ($("#splitPopup").style.display !== "none") { hideSplitPopup(); return; }
      if ($("#beatAdjustPopup").style.display !== "none") { _hideBeatAdjustPopup(); return; }
      deselectAll();
    }
    if (e.key.toLowerCase() === "r" && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      toggleRecordMode();
      return;
    }
    if (e.key.toLowerCase() === "t" && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      handleTap();
      return;
    }
    if (e.key.toLowerCase() === "s" && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      $("#btnSplit").click();
      return;
    }
  });

  // ---- record mode (live chord input) ----

  let isRecordMode = false;

  function placeLiveChord(name) {
    const t = snapTime(audio.currentTime);
    const beatSec = 60 / songBPM;
    const defaultDur = beatSec * 4;
    const existing = chords.find(c => Math.abs(c.time - t) < beatSec * 0.25);
    if (existing) {
      existing.chord = name;
    } else {
      chords.push({ time: t, end: t + defaultDur, chord: name });
      sortChords();
    }
    render();
    showToast(`${name} @ ${formatTime(t)}`);
  }

  function toggleRecordMode() {
    isRecordMode = !isRecordMode;
    $("#recordModeIndicator").style.display = isRecordMode ? "" : "none";
    $("#liveChordInput").style.display = isRecordMode ? "" : "none";
    $("#btnRecordMode").style.background = isRecordMode ? "rgba(233,30,99,0.3)" : "";
    if (isRecordMode) {
      $("#liveChordText").value = "";
      $("#liveChordText").focus();
      if (audio.paused) audio.play();
    }
  }

  $("#btnRecordMode").addEventListener("click", toggleRecordMode);

  $("#liveChordText").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const name = e.target.value.trim();
      if (!name) return;
      placeLiveChord(name);
      e.target.value = "";
    }
    if (e.key === "Escape") {
      toggleRecordMode();
    }
    e.stopPropagation();
  });

  // ---- MIDI input ----

  let midiAccess = null;
  let midiHeldNotes = new Set();
  let midiChordTimeout = null;

  // 固定顯示拼法（全降記號，唯 F# 升記號）— 與 utils.js NOTE_NAMES_DISPLAY 一致。
  const NOTE_NAMES = ["C","Db","D","Eb","E","F","F#","G","Ab","A","Bb","B"];
  const CHORD_MAP = {
    "0,4,7": "", "0,3,7": "m", "0,4,7,10": "7", "0,3,7,10": "m7",
    "0,4,7,11": "maj7", "0,3,6": "dim", "0,4,8": "aug",
    "0,2,7": "sus2", "0,5,7": "sus4", "0,3,6,10": "m7b5",
    "0,4,7,11,14": "maj9", "0,4,7,10,14": "9", "0,3,7,11": "mMaj7",
    "0,4,7,10,17": "11", "0,3,7,10,14": "m9",
  };

  function detectChordFromMIDI(midiNotes) {
    const pitchClasses = [...new Set(midiNotes.map(n => n % 12))].sort((a, b) => a - b);
    if (pitchClasses.length < 2) return null;
    // try each pitch class as root (to handle inversions)
    for (const root of pitchClasses) {
      const intervals = pitchClasses.map(pc => (pc - root + 12) % 12).sort((a, b) => a - b);
      const key = intervals.join(",");
      if (CHORD_MAP[key] !== undefined) {
        return NOTE_NAMES[root] + CHORD_MAP[key];
      }
    }
    return null;
  }

  function handleMIDIMessage(msg) {
    const [status, note, velocity] = msg.data;
    const command = status & 0xf0;
    if (command === 0x90 && velocity > 0) {
      midiHeldNotes.add(note);
      if (midiChordTimeout) clearTimeout(midiChordTimeout);
      midiChordTimeout = setTimeout(() => {
        if (midiHeldNotes.size >= 2 && isRecordMode) {
          const notes = Array.from(midiHeldNotes).sort((a, b) => a - b);
          const chordName = detectChordFromMIDI(notes);
          if (chordName) placeLiveChord(chordName);
        }
      }, 50);
    } else if (command === 0x80 || (command === 0x90 && velocity === 0)) {
      midiHeldNotes.delete(note);
    }
  }

  async function initMIDI() {
    if (!navigator.requestMIDIAccess) {
      showToast(_t("editor.toast.midi_unsupported"));
      return;
    }
    try {
      midiAccess = await navigator.requestMIDIAccess();
      for (const input of midiAccess.inputs.values()) {
        input.onmidimessage = handleMIDIMessage;
      }
      midiAccess.onstatechange = (e) => {
        if (e.port.type === "input" && e.port.state === "connected") {
          e.port.onmidimessage = handleMIDIMessage;
          showToast(_t("editor.toast.midi_connected", { name: e.port.name }));
        }
      };
      const count = midiAccess.inputs.size;
      showToast(count > 0 ? _t("editor.toast.midi_n", { n: count }) : _t("editor.toast.midi_none"));
    } catch (err) {
      showToast(_t("editor.toast.midi_failed", { err: err.message }));
    }
  }

  $("#btnMIDI").addEventListener("click", initMIDI);

  $("#helpBackdrop").addEventListener("click", () => {
    $("#helpOverlay").style.display = "none";
    $("#helpBackdrop").style.display = "none";
  });

  // ---- tap tempo ----

  let tapTimestamps = [];
  const TAP_RESET_MS = 3000;

  function handleTap() {
    const now = performance.now();
    if (tapTimestamps.length > 0 && now - tapTimestamps[tapTimestamps.length - 1] > TAP_RESET_MS) {
      tapTimestamps = [];
    }
    tapTimestamps.push(now);
    // flash button
    const btn = $("#btnTapTempo");
    btn.style.background = "rgba(255,87,34,0.4)";
    setTimeout(() => { btn.style.background = ""; }, 120);

    if (tapTimestamps.length < 2) {
      $("#tapBpmDisplay").textContent = _t("editor.tap.keep_tapping");
      return;
    }
    if (tapTimestamps.length > 16) tapTimestamps.shift();

    let total = 0;
    for (let i = 1; i < tapTimestamps.length; i++) {
      total += tapTimestamps[i] - tapTimestamps[i - 1];
    }
    const avgMs = total / (tapTimestamps.length - 1);
    const bpm = Math.round(60000 / avgMs);
    $("#tapBpmDisplay").textContent = _t("editor.tap.bpm_n_taps", { bpm, n: tapTimestamps.length });
    $("#btnApplyTapBpm").style.display = "";
    $("#btnApplyTapBpm").dataset.bpm = bpm;
    $("#btnResetTap").style.display = "";
  }

  $("#btnTapTempo").addEventListener("click", handleTap);

  $("#btnApplyTapBpm").addEventListener("click", () => {
    const bpm = parseInt($("#btnApplyTapBpm").dataset.bpm);
    if (bpm > 0) {
      songBPM = bpm;
      // User explicitly re-derived BPM here — clear any player-side
      // multiplier so the next /player load sees this raw value (otherwise
      // the mult is silently applied on top, doubling/halving again).
      try { localStorage.removeItem(`bpm_mult_${trackPath}`); } catch {}
      _updateBpmDisplay();
      render();
      showToast(_t("editor.toast.bpm_updated", { bpm }));
    }
  });

  $("#btnResetTap").addEventListener("click", () => {
    tapTimestamps = [];
    $("#tapBpmDisplay").textContent = "";
    $("#btnApplyTapBpm").style.display = "none";
    $("#btnResetTap").style.display = "none";
  });

  // ---- save ----

  $("#btnSave").addEventListener("click", async () => {
    songKey = $("#keyInput").value.trim();
    try {
      const result = await API.saveChords({
        path: trackPath,
        key: songKey,
        capo: 0,
        bpm: songBPM,
        chords: chords,
      });
      if (result && result.version) {
        const url = new URL(window.location);
        url.searchParams.set("version", result.version);
        window.history.replaceState({}, "", url);
      }
      showToast(_t("editor.toast.saved"), 2000);
    } catch (err) {
      showToast(_t("editor.toast.save_failed", { err: err.message }), 3000);
    }
  });

  // ---- import ChordPro ----

  $("#btnImport").addEventListener("click", () => {
    const input = prompt(_t("editor.prompt.import_chordpro"));
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
    showToast(_t("editor.toast.imported_n", { n: chords.length }));
  });

  // ---- chord palette ----

  function buildPalette() {
    const container = $("#chordPalette");
    container.innerHTML = "";
    container.style.display = "block";
    container.style.overflowX = "auto";
    
    // 以各個根音為一列
    // 固定顯示拼法：每個音高僅提供單一拼法（全降記號，唯 F# 升記號）。
    const rootGroups = [
        ["C", "Db"],
        ["D", "Eb"],
        ["E"],
        ["F", "F#"],
        ["G", "Ab"],
        ["A", "Bb"],
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
