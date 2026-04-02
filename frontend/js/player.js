/** LiveChord 播放頁 */

(function () {
  const $ = (sel) => document.querySelector(sel);

  const params = new URLSearchParams(window.location.search);
  const trackPath = params.get("path");
  const autoplay = params.get("autoplay") === "1";
  const restoreFs = params.get("fs") === "1";
  if (!trackPath) { window.location.href = "/"; return; }

  // ---- state ----
  let isFavorite = false;
  let chordData = null;
  let displayMode = "piano";
  let chordCache = {};
  let siblingTracks = [];
  let currentIndex = -1;
  let activeChordIdx = -1;
  let chordElements = [];
  let _ribbonPositions = [];
  let transpose = 0;
  let capo = 0;
  let favTracks = [];

  // ---- DOM ----
  const audio = $("#audio");
  const cover = $("#cover");
  const songTitle = $("#songTitle");
  const songArtist = $("#songArtist");
  const songAlbum = $("#songAlbum");
  const songMeta = $("#songMeta");
  const btnPlay = $("#btnPlay");
  const btnFav = $("#btnFav");
  const progressBar = $("#progressBar");
  const progress = $("#progress");
  const timeCurrent = $("#timeCurrent");
  const timeDuration = $("#timeDuration");
  const volumeSlider = $("#volumeSlider");
  const chordDisplayOverview = $("#chordDisplayOverview");
  const chordDisplayDiagrams = $("#chordDisplayDiagrams");
  const ribbonTrack = $("#ribbonTrack");
  const tabOverview = $("#tabOverview");
  const tabDiagrams = $("#tabDiagrams");
  const toast = $("#toast");
  const detectOverlay = $("#detectOverlay");
  const detectMsg = $("#detectMsg");
  const detectDetail = $("#detectDetail");
  const bigChordBox = $("#currentChordBig");
  const bigChordName = $("#bigChordName");
  const bigChordJianpu = $("#bigChordJianpu");

  let activeTab = localStorage.getItem("livechord_tab") || "overview";
  let ribbonElements = [];
  const pxPerSec = 100;

  if (tabOverview && tabDiagrams) {
    tabOverview.addEventListener("click", () => {
      activeTab = "overview";
      localStorage.setItem("livechord_tab", "overview");
      tabOverview.classList.add("active");
      tabDiagrams.classList.remove("active");
      chordDisplayOverview.style.display = "";
      chordDisplayDiagrams.style.display = "none";
      // Overview 顯示大和弦
      if (hasChords) bigChordBox.style.display = "";
      if (activeChordIdx >= 0 && activeChordIdx < chordElements.length) {
        chordElements[activeChordIdx].scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
      }
      // 重新觸發高亮更新
      activeChordIdx = -1;
      updateActiveChord(audio.currentTime || -1);
    });

    tabDiagrams.addEventListener("click", () => {
      activeTab = "diagrams";
      localStorage.setItem("livechord_tab", "diagrams");
      tabDiagrams.classList.add("active");
      tabOverview.classList.remove("active");
      chordDisplayOverview.style.display = "none";
      chordDisplayDiagrams.style.display = "block";
      // Diagrams 隱藏大和弦（避免重複）
      bigChordBox.style.display = "none";
      const t = audio.currentTime || 0;
      if (ribbonTrack) {
        ribbonTrack.style.transform = `translateX(${-t * pxPerSec}px)`;
      }
    });

    // 還原上次 tab
    if (activeTab === "diagrams") tabDiagrams.click();
  }
  const bigChordDiagram = $("#bigChordDiagram");

  // ---- 和弦區縮放 ----
  const ZOOM_STEPS = [50, 67, 75, 80, 90, 100, 110, 125, 150, 175, 200, 250, 300];
  let zoomIdx = ZOOM_STEPS.indexOf(parseInt(localStorage.getItem("livechord_chord_zoom")) || 100);
  if (zoomIdx < 0) zoomIdx = ZOOM_STEPS.indexOf(100);
  const btnZoomIn = $("#btnZoomIn");
  const btnZoomOut = $("#btnZoomOut");
  const btnZoomReset = $("#btnZoomReset");
  const chordDisplayEl = $("#chordDisplay");

  function _applyZoom() {
    const pct = ZOOM_STEPS[zoomIdx];
    if (chordDisplayEl) {
      chordDisplayEl.style.transformOrigin = "top left";
      chordDisplayEl.style.transform = `scale(${pct / 100})`;
      // 補償 scale 造成的尺寸縮減
      chordDisplayEl.style.width = (10000 / pct) + "%";
      chordDisplayEl.style.height = (10000 / pct) + "%";
    }
    if (btnZoomReset) btnZoomReset.textContent = pct + "%";
    localStorage.setItem("livechord_chord_zoom", pct);
  }
  _applyZoom();

  if (btnZoomIn) btnZoomIn.addEventListener("click", () => {
    if (zoomIdx < ZOOM_STEPS.length - 1) { zoomIdx++; _applyZoom(); }
  });
  if (btnZoomOut) btnZoomOut.addEventListener("click", () => {
    if (zoomIdx > 0) { zoomIdx--; _applyZoom(); }
  });
  if (btnZoomReset) btnZoomReset.addEventListener("click", () => {
    zoomIdx = ZOOM_STEPS.indexOf(100);
    _applyZoom();
  });

  // ---- mini 播放控制（全螢幕用）----
  const btnMiniPlay = $("#btnMiniPlay");
  const btnMiniPrev = $("#btnMiniPrev");
  const btnMiniNext = $("#btnMiniNext");

  if (btnMiniPlay) {
    btnMiniPlay.addEventListener("click", () => {
      if (audio.paused) audio.play(); else audio.pause();
    });
    audio.addEventListener("play", () => { btnMiniPlay.innerHTML = "&#x23F8;"; });
    audio.addEventListener("pause", () => { btnMiniPlay.innerHTML = "&#x25B6;"; });
  }
  function _navUrl(path) {
    const fs = chordDisplay.classList.contains("fullscreen") || document.fullscreenElement ? "&fs=1" : "";
    return `/player?path=${encodeURIComponent(path)}&autoplay=1${fs}`;
  }
  function _navPrev() {
    if (loopMode === "favorites" && favTracks.length > 0) {
      const i = favTracks.indexOf(trackPath);
      const prev = (i <= 0) ? favTracks.length - 1 : i - 1;
      window.location.href = _navUrl(favTracks[prev]);
    } else if (siblingTracks.length > 0 && currentIndex > 0) {
      window.location.href = _navUrl(siblingTracks[currentIndex - 1].path);
    }
  }
  function _navNext() {
    if (loopMode === "favorites" && favTracks.length > 0) {
      const i = favTracks.indexOf(trackPath);
      const next = (i < 0 || i >= favTracks.length - 1) ? 0 : i + 1;
      window.location.href = _navUrl(favTracks[next]);
    } else if (siblingTracks.length > 0 && currentIndex < siblingTracks.length - 1) {
      window.location.href = _navUrl(siblingTracks[currentIndex + 1].path);
    }
  }
  if (btnMiniPrev) btnMiniPrev.addEventListener("click", _navPrev);
  if (btnMiniNext) btnMiniNext.addEventListener("click", _navNext);

  // ---- 全螢幕切換（一鍵：CSS fullscreen + 瀏覽器全螢幕）----
  const chordDisplay = $("#chordDisplay");
  const btnFullscreen = $("#btnFullscreen");
  const btnPageFs = $("#btnPageFs");

  function _enterFullscreen() {
    chordDisplay.classList.add("fullscreen");
    btnFullscreen.innerHTML = "&#x2716;";
    if (btnPageFs) btnPageFs.innerHTML = "&#x2716;";
    document.documentElement.requestFullscreen().catch(() => {});
  }
  function _exitFullscreen() {
    chordDisplay.classList.remove("fullscreen");
    btnFullscreen.innerHTML = "&#x26F6;";
    if (btnPageFs) btnPageFs.innerHTML = "&#x26F6;";
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    // 退出全螢幕時重置縮放到 100%
    zoomIdx = ZOOM_STEPS.indexOf(100);
    _applyZoom();
  }

  if (btnFullscreen && chordDisplay) {
    btnFullscreen.addEventListener("click", () => {
      if (chordDisplay.classList.contains("fullscreen")) _exitFullscreen();
      else _enterFullscreen();
    });
  }
  // header 的全螢幕按鈕也同步
  if (btnPageFs) {
    btnPageFs.onclick = () => {
      if (chordDisplay.classList.contains("fullscreen")) _exitFullscreen();
      else _enterFullscreen();
    };
  }
  // Esc 或瀏覽器退出全螢幕時同步狀態
  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement && chordDisplay.classList.contains("fullscreen")) {
      chordDisplay.classList.remove("fullscreen");
      if (btnFullscreen) btnFullscreen.innerHTML = "&#x26F6;";
      if (btnPageFs) btnPageFs.innerHTML = "&#x26F6;";
      // 重置縮放
      zoomIdx = ZOOM_STEPS.indexOf(100);
      _applyZoom();
    }
  });

  // 相對簡譜：notes 陣列相對於當前 key 的簡譜
  function _notesToJianpu(notes, key) {
    if (!notes || notes.length === 0) return "";
    const JP = ["1","#1","2","#2","3","4","#4","5","#5","6","#6","7"];
    const JPF = ["1","b2","2","b3","3","4","b5","5","b6","6","b7","7"];
    const keySemi = noteToSemitone(key || "C");
    return notes.map(n => {
      const semi = noteToSemitone(n);
      const interval = ((semi - keySemi) % 12 + 12) % 12;
      return n.includes("b") ? JPF[interval] : JP[interval];
    }).join(" ");
  }

  function _currentKey() {
    if (!chordData || !chordData.key) return "C";
    const shift = transpose - capo;
    return shift === 0 ? chordData.key : transposeChord(chordData.key, shift);
  }

  function showToast(msg, ms = 2000) {
    toast.textContent = msg;
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), ms);
  }

  // ===========================================================================
  // 一、載入 track：播放時若無和弦譜，自動偵測
  // ===========================================================================

  async function loadTrack(path) {
    audio.src = API.trackStreamUrl(path);
    cover.src = API.trackCoverUrl(path);
    cover.style.display = "";

    const miniTitle = $("#miniTitle");
    try {
      const info = await API.trackInfo(path);
      const title = info.title || path.split("/").pop().replace(/\.flac$/i, "");
      
      let diffHtml = "";
      const uc = info.unique_chords || 0;
      if (uc > 0) {
        let stars = 1;
        const labels = ["", "入門", "中階", "進階", "大師"];
        if (uc >= 15) stars = 4;
        else if (uc >= 9) stars = 3;
        else if (uc >= 5) stars = 2;
        const cl = (info.chord_list || []).join("  ");
        diffHtml = `<div style="font-size:11px;color:var(--text-dim);margin-top:2px">${"⭐".repeat(stars)} ${labels[stars]}（${uc}種）${cl ? " — " + cl : ""}</div>`;
      }
      
      const escTitle = title.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      songTitle.innerHTML = escTitle;
      songArtist.innerHTML = (info.artist || "") + diffHtml;
      songAlbum.textContent = info.album || "";
      const meta = [];
      if (info.sample_rate) meta.push(`${info.sample_rate / 1000}kHz`);
      if (info.bits_per_sample) meta.push(`${info.bits_per_sample}bit`);
      if (info.channels) meta.push(info.channels === 2 ? "Stereo" : `${info.channels}ch`);
      songMeta.textContent = meta.join(" / ");
      document.title = `${title} — LiveChord`;
      if (miniTitle) {
        miniTitle.textContent = title;
        requestAnimationFrame(() => {
          const wrap = miniTitle.parentElement;
          miniTitle.classList.toggle("marquee", miniTitle.scrollWidth > wrap.clientWidth);
        });
      }
    } catch {
      const title = path.split("/").pop().replace(/\.flac$/i, "");
      songTitle.textContent = title;
      if (miniTitle) miniTitle.textContent = title;
    }

    API.addRecent(path).catch(() => {});

    try {
      const favData = await API.getFavorites();
      favTracks = (favData.favorites || []).map(f => f.path);
      isFavorite = favTracks.includes(path);
      updateFavButton();
    } catch {}

    await loadChords(path);
    loadSiblings(path);
  }

  async function loadSiblings(path) {
    const parts = path.split("/");
    parts.pop();
    const dir = parts.join("/");
    try {
      const data = await API.browse(dir);
      siblingTracks = data.entries.filter((e) => !e.is_dir && e.name.toLowerCase().endsWith(".flac"));
      currentIndex = siblingTracks.findIndex((t) => t.path === path);
    } catch {}
  }

  // ---- melody data (for Dynamic Lead Sheet) ----
  let melodyData = null;

  async function _loadMelody(path) {
    try {
      const res = await fetch(`/api/ai/melody?path=${encodeURIComponent(path)}`);
      const data = await res.json();
      if (data.melody && data.melody.length > 0) {
        melodyData = data.melody;
      }
    } catch {}
  }

  function _getMelodyMidi(currentTime) {
    if (!melodyData) return -1;
    for (let i = melodyData.length - 1; i >= 0; i--) {
      if (currentTime >= melodyData[i].start && currentTime <= melodyData[i].end) {
        return melodyData[i].midi;
      }
    }
    return -1;
  }

  // ---- section detection ----
  let sectionData = null;

  async function _loadSections(path) {
    try {
      const res = await fetch(`/api/ai/sections?path=${encodeURIComponent(path)}`);
      sectionData = await res.json();
      if (sectionData.sections && sectionData.sections.length > 0) {
        _renderSectionMarkers();
      }
    } catch {}
  }

  function _renderSectionMarkers() {
    if (!sectionData || !sectionData.sections) return;
    // 移除舊的標記
    document.querySelectorAll(".section-marker, .section-marker-ribbon").forEach(el => el.remove());
    document.querySelectorAll(".has-section").forEach(el => {
      el.classList.remove("has-section");
      el.style.removeProperty("--sec-color");
    });

    if (chordElements.length === 0) return;
    const displayed = _displayChords();

    for (const sec of sectionData.sections) {
      for (let i = 0; i < displayed.length; i++) {
        if (displayed[i].time >= sec.start && displayed[i].time < sec.end) {
          const el = chordElements[i];
          const rel = ribbonElements[i];

          // 1. Overview 的和弦方塊 - 加上頂部顏色 Bar
          if (el) {
            el.style.setProperty("--sec-color", sec.color);
            el.classList.add("has-section");

            // 只有在段落的最開始一個和弦加上文字標籤
            if (i === 0 || displayed[i - 1].time < sec.start) {
              const marker = document.createElement("div");
              marker.className = "section-marker";
              marker.textContent = `${sec.emoji} ${sec.label || sec.type}`;
              marker.style.color = sec.color;
              el.appendChild(marker);
            }
          }

          // 2. Diagrams 的 Ribbon 片段 - 加上頂部顏色 Bar
          if (rel) {
            rel.style.setProperty("--sec-color", sec.color);
            rel.classList.add("has-section");

            if (i === 0 || displayed[i - 1].time < sec.start) {
              const rmarker = document.createElement("div");
              rmarker.className = "section-marker-ribbon";
              rmarker.textContent = `${sec.emoji} ${sec.label || sec.type}`;
              rmarker.style.color = sec.color;
              rel.appendChild(rmarker);
            }
          }
        }
      }
    }
  }

  // ---- chord loading (自動偵測整合) ----

  let hasChords = false;

  async function loadChords(path) {
    try {
      chordData = await API.getChords(path);
      if (chordData.exists && chordData.chords && chordData.chords.length > 0) {
        hasChords = true;
        // 和弦品質燈號
        const srcBadge = $("#chordSource");
        if (srcBadge) {
          const rawSrc = chordData.source || "btc";
          const src = rawSrc === "chordify" ? "midi" : rawSrc;
          const labels = { midi: "MIDI", btc: "BTC" };
          srcBadge.className = `chord-source-badge src-${src}`;
          srcBadge.textContent = labels[src] || src;
        }
        if (chordData.key) {
          const keyInfo = $("#chordKey");
          if (keyInfo) keyInfo.textContent = `Key: ${chordData.key}`;
        }
        if (chordData.capo) {
          capo = chordData.capo;
          const capoSel = $("#capoSelect");
          if (capoSel) capoSel.value = capo;
        }
        await preloadChordInfo(chordData.chords);
        buildChordDOM();
        bigChordBox.style.display = "";
        // 載入段落 + 旋律資訊
        _loadSections(path);
        _loadMelody(path);
        return;
      }
    } catch (err) {
      console.error("loadChords error:", err);
    }

    hasChords = false;
    chordDisplayOverview.innerHTML = `<div class="empty" style="padding:20px"><div class="msg" style="color:var(--text-dim)">尚無和弦譜 — 請按「偵測」按鈕手動偵測</div></div>`;
    if (ribbonTrack) ribbonTrack.innerHTML = "";
    bigChordBox.style.display = "none";
  }

  /** 播放時自動偵測（顯示 overlay） */
  async function autoDetectAndPlay() {
    detectOverlay.style.display = "";
    detectMsg.textContent = "正在偵測和弦…";
    detectDetail.textContent = "首次播放需要分析音訊，大約 30 秒~1 分鐘";

    try {
      const result = await API.detectChords(trackPath);
      detectMsg.textContent = `偵測完成！${result.chord_count} 個和弦`;
      detectDetail.textContent = `調性: ${result.key}`;

      chordCache = {};
      await loadChords(trackPath);
    } catch (err) {
      detectMsg.textContent = "偵測失敗";
      detectDetail.textContent = err.message;
      await new Promise((r) => setTimeout(r, 2000));
    } finally {
      detectOverlay.style.display = "none";
    }

    // 偵測完成後開始播放
    audio.play();
  }

  // ---- preload chord info ----

  async function preloadChordInfo(chords) {
    const names = [...new Set(chords.map((c) => c.chord))];
    await Promise.all(names.map(async (name) => {
      if (chordCache[name]) return;
      chordCache[name] = { jianpu: "", notes: [], diagram_guitar: null, diagram_ukulele: null };
      try {
        const info = await API.chordInfo(name);
        chordCache[name].jianpu = info.jianpu || "";
        chordCache[name].notes = info.notes || [];
      } catch {}
      try { chordCache[name].diagram_guitar = await API.chordDiagram("guitar", name); } catch {}
      try { chordCache[name].diagram_ukulele = await API.chordDiagram("ukulele", name); } catch {}
    }));
  }

  // ===========================================================================
  // 三、和弦高亮改進：當前和弦置中放大 + 時間軸連動
  // ===========================================================================

  function _displayChords() {
    if (!chordData || !chordData.chords) return [];
    const shift = transpose - capo;
    if (shift === 0) return chordData.chords;
    return chordData.chords.map((c) => ({ ...c, chord: transposeChord(c.chord, shift) }));
  }

  function buildChordDOM() {
    chordDisplayOverview.innerHTML = "";
    if (ribbonTrack) ribbonTrack.innerHTML = "";
    chordElements = [];
    ribbonElements = [];
    activeChordIdx = -1;

    const chords = _displayChords();
    if (!chords || chords.length === 0) return;

    const uncached = chords.filter((c) => !chordCache[c.chord]).map((c) => c.chord);
    if (uncached.length > 0) {
      _buildDOMFromChords(chords);
      _loadMissingCache(uncached).then(() => {
        _buildDOMFromChords(chords);
        updateActiveChord(audio.currentTime || -1);
      });
      return;
    }
    _buildDOMFromChords(chords);
  }

  function _buildDOMFromChords(chords) {
    chordDisplayOverview.innerHTML = "";
    if (ribbonTrack) ribbonTrack.innerHTML = "";
    chordElements = [];
    ribbonElements = [];
    _ribbonPositions = [];
    activeChordIdx = -1;

    // Pre-calculate ribbon positions: no overlap
    // Min width depends on mode — piano keyboard is wider than guitar diagram
    const minRibbonW = displayMode === "piano" ? 180 : 120;
    let curLeft = 0;
    for (let i = 0; i < chords.length; i++) {
      const timeLeft = chords[i].time * pxPerSec;
      const left = Math.max(timeLeft, curLeft);
      const nextStart = i + 1 < chords.length ? chords[i+1].time : chords[i].time + 4;
      const naturalW = (nextStart - chords[i].time) * pxPerSec;
      const w = Math.max(naturalW, minRibbonW);
      _ribbonPositions.push({ left, width: w, time: chords[i].time });
      curLeft = left + w;
    }

    let _prevMidi = null;
    for (let i = 0; i < chords.length; i++) {
      const el = _createChordEl(chords[i], i, _prevMidi);
      chordDisplayOverview.appendChild(el);
      chordElements.push(el);
      // 擷取 voice leading 的 MIDI 位置給下一個和弦
      const pianoCanvas = el.querySelector("canvas");
      if (pianoCanvas && pianoCanvas._lastMidi) _prevMidi = pianoCanvas._lastMidi;

      if (ribbonTrack) {
        const rEl = _createRibbonEl(chords[i], i, chords, _prevMidi);
        ribbonTrack.appendChild(rEl);
        ribbonElements.push(rEl);
        const rCanvas = rEl.querySelector("canvas");
        if (rCanvas && rCanvas._lastMidi) _prevMidi = rCanvas._lastMidi;
      }
    }
    // 重新渲染段落標記
    if (sectionData) _renderSectionMarkers();
  }

  async function _loadMissingCache(names) {
    const unique = [...new Set(names)];
    await Promise.all(unique.map(async (name) => {
      if (chordCache[name]) return;
      chordCache[name] = { jianpu: "", notes: [], diagram_guitar: null, diagram_ukulele: null };
      try {
        const info = await API.chordInfo(name);
        chordCache[name].jianpu = info.jianpu || "";
        chordCache[name].notes = info.notes || [];
      } catch {}
      try { chordCache[name].diagram_guitar = await API.chordDiagram("guitar", name); } catch {}
      try { chordCache[name].diagram_ukulele = await API.chordDiagram("ukulele", name); } catch {}
    }));
  }

  function _createChordEl(chord, idx, prevMidi) {
    const cache = chordCache[chord.chord] || {};
    const div = document.createElement("div");
    div.className = "chord-item";
    div.dataset.idx = idx;
    div.dataset.time = chord.time;
    if (chord.end != null) div.dataset.end = chord.end;

    div.addEventListener("click", () => {
      audio.currentTime = chord.time;
      // 立即更新高亮（不等 seeked 事件）
      updateActiveChord(chord.time);
    });

    _fillChordEl(div, chord, cache, prevMidi);
    return div;
  }

  function _createRibbonEl(chord, idx, allChords, prevMidi) {
    const cache = chordCache[chord.chord] || {};
    const div = document.createElement("div");
    div.className = "ribbon-item";
    div.dataset.idx = idx;
    
    // Use pre-calculated positions (no overlap)
    const pos = _ribbonPositions[idx] || { left: chord.time * pxPerSec, width: 120 };
    div.style.left = `${pos.left}px`;
    div.style.width = `${pos.width}px`;

    div.addEventListener("click", () => {
      audio.currentTime = chord.time;
      updateActiveChord(chord.time);
    });

    const nameEl = document.createElement("div");
    nameEl.className = "chord-name";
    nameEl.textContent = chord.chord;
    div.appendChild(nameEl);

    if (displayMode === "piano") {
      const jp = document.createElement("div");
      jp.className = "chord-jianpu";
      jp.innerHTML = ChordRender.jianpuToHtml(_notesToJianpu(cache.notes, _currentKey()));
      div.appendChild(jp);
      const pianoCanvas = document.createElement("canvas");
      ChordRender.drawPiano(pianoCanvas, cache.notes || [], 1, prevMidi);
      div.appendChild(pianoCanvas);
    } else {
      const key = displayMode === "guitar" ? "diagram_guitar" : "diagram_ukulele";
      const diag = cache[key];
      if (diag) {
        const canvas = document.createElement("canvas");
        ChordRender.drawDiagram(canvas, diag, 1);
        div.appendChild(canvas);
      } else {
        const span = document.createElement("div");
        span.style.cssText = "font-size:12px;color:#666;margin-top:8px";
        span.textContent = "無圖";
        div.appendChild(span);
      }
    }
    return div;
  }

  function _splitChordName(name) {
    const m = name.match(/^([A-G][b#]?)(.*)$/);
    return m ? { root: m[1], quality: m[2] } : { root: name, quality: "" };
  }

  function _fillChordEl(div, chord, cache, prevMidi) {
    div.innerHTML = "";

    const nameEl = document.createElement("div");
    nameEl.className = "chord-name";
    const parts = _splitChordName(chord.chord);
    nameEl.innerHTML = parts.root + (parts.quality ? `<span class="chord-quality">${parts.quality}</span>` : "");
    div.appendChild(nameEl);

    if (displayMode === "piano") {
      const jp = document.createElement("div");
      jp.className = "chord-jianpu";
      jp.innerHTML = ChordRender.jianpuToHtml(_notesToJianpu(cache.notes, _currentKey()));
      div.appendChild(jp);
      const pianoCanvas = document.createElement("canvas");
      pianoCanvas.style.marginTop = "4px";
      ChordRender.drawPiano(pianoCanvas, cache.notes || [], 1, prevMidi);
      div.appendChild(pianoCanvas);
    } else {
      const key = displayMode === "guitar" ? "diagram_guitar" : "diagram_ukulele";
      const diagramData = cache[key];
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
    }

    const timeEl = document.createElement("div");
    timeEl.className = "chord-time";
    timeEl.textContent = formatTime(chord.time);
    div.appendChild(timeEl);
  }

  /** 更新高亮：timeline 和弦 + 大字顯示 */
  function updateActiveChord(currentTime) {
    if (!chordData || !chordData.chords || chordElements.length === 0) return;

    if (activeTab === "diagrams" && ribbonTrack && _ribbonPositions.length > 0) {
      // Interpolate scroll from adjusted positions
      let scrollX = currentTime * pxPerSec;
      for (let i = _ribbonPositions.length - 1; i >= 0; i--) {
        const p = _ribbonPositions[i];
        if (currentTime >= p.time) {
          const frac = p.width > 0 ? (currentTime - p.time) * pxPerSec / ((i + 1 < _ribbonPositions.length ? _ribbonPositions[i+1].time - p.time : 4) * pxPerSec) : 0;
          scrollX = p.left + Math.min(frac, 1) * p.width;
          break;
        }
      }
      ribbonTrack.style.transform = `translateX(${-scrollX}px)`;
    }

    const displayedChords = _displayChords();

    // 找出當前和弦：持續顯示直到下一個和弦出現
    // 邏輯：找最後一個 time <= currentTime 的和弦
    // 如果在 end 之前 → 完全高亮；在 end 之後但下個和弦之前 → 仍顯示（持續）
    let newIdx = -1;
    for (let i = displayedChords.length - 1; i >= 0; i--) {
      if (currentTime >= displayedChords[i].time) {
        newIdx = i;
        break;
      }
    }

    if (newIdx === activeChordIdx) return;

    // 移除舊高亮
    if (activeChordIdx >= 0 && activeChordIdx < chordElements.length) {
      chordElements[activeChordIdx].classList.remove("active");
      if (ribbonElements[activeChordIdx]) {
        ribbonElements[activeChordIdx].classList.remove("active");
      }
    }

    activeChordIdx = newIdx;

    if (activeChordIdx >= 0 && activeChordIdx < chordElements.length) {
      const el = chordElements[activeChordIdx];
      el.classList.add("active");
      if (activeTab === "overview") {
        el.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
      }

      if (ribbonElements[activeChordIdx]) {
        ribbonElements[activeChordIdx].classList.add("active");
      }

      // 更新大字顯示 — Diagrams 模式隱藏（避免重複）
      const chord = displayedChords[activeChordIdx];
      const cache = chordCache[chord.chord] || {};

      if (activeTab === "diagrams") {
        bigChordBox.style.display = "none";
      } else {
        bigChordName.textContent = chord.chord;
        bigChordBox.style.display = "";

        bigChordJianpu.innerHTML = "";
        bigChordDiagram.innerHTML = "";

        if (displayMode === "piano") {
          bigChordJianpu.innerHTML = ChordRender.jianpuToHtml(_notesToJianpu(cache.notes, _currentKey()));
          const pianoCanvas = document.createElement("canvas");
          pianoCanvas.style.marginTop = "8px";
          ChordRender.drawPiano(pianoCanvas, cache.notes || [], 1.8, null, _getMelodyMidi(currentTime));
          bigChordDiagram.appendChild(pianoCanvas);
        } else {
          bigChordJianpu.innerHTML = ChordRender.jianpuToHtml(_notesToJianpu(cache.notes, _currentKey()));
          const key = displayMode === "guitar" ? "diagram_guitar" : "diagram_ukulele";
          const diag = cache[key];
          if (diag) {
            const canvas = document.createElement("canvas");
            canvas.style.marginTop = "8px";
            ChordRender.drawDiagram(canvas, diag, 2);
            bigChordDiagram.appendChild(canvas);
          }
        }
      }
    } else {
      bigChordName.textContent = "—";
      bigChordJianpu.innerHTML = "";
      bigChordDiagram.innerHTML = "";
    }
  }

  // ===========================================================================
  // 播放控制（攔截播放按鈕，無和弦時先偵測）
  // ===========================================================================

  btnPlay.addEventListener("click", () => {
    if (audio.paused) audio.play();
    else audio.pause();
  });

  audio.addEventListener("play", () => { btnPlay.innerHTML = "&#x23F8;"; });
  audio.addEventListener("pause", () => { btnPlay.innerHTML = "&#x25B6;"; });
  audio.addEventListener("loadedmetadata", () => {
    timeDuration.textContent = formatTime(audio.duration);
  });

  // requestAnimationFrame 同步
  let rafId = null;

  function tickSync() {
    if (!audio.paused) {
      const t = audio.currentTime;
      const d = audio.duration || 1;
      progress.style.width = (t / d * 100) + "%";
      timeCurrent.textContent = formatTime(t);
      updateActiveChord(t);
      rafId = requestAnimationFrame(tickSync);
    }
  }

  audio.addEventListener("play", () => {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = requestAnimationFrame(tickSync);
  });

  audio.addEventListener("pause", () => {
    if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
    const t = audio.currentTime;
    progress.style.width = (t / (audio.duration || 1) * 100) + "%";
    timeCurrent.textContent = formatTime(t);
    updateActiveChord(t);
  });

  audio.addEventListener("seeked", () => {
    const t = audio.currentTime;
    progress.style.width = (t / (audio.duration || 1) * 100) + "%";
    timeCurrent.textContent = formatTime(t);
    updateActiveChord(t);
  });

  // ---- 循環模式：off → single → favorites ----
  const btnLoop = $("#btnLoop");
  const LOOP_MODES = ["off", "single", "favorites"];
  const LOOP_LABELS = { off: "循環 OFF", single: "單曲循環", favorites: "最愛循環" };
  const LOOP_ICONS = { off: "\u{1F501}", single: "\u{1F502}", favorites: "fav" };
  let loopMode = localStorage.getItem("livechord_loop_mode") || "off";

  function _updateLoopUI() {
    audio.loop = (loopMode === "single");
    if (loopMode === "favorites") {
      btnLoop.innerHTML = '<span style="position:relative">\u{1F501}<span style="position:absolute;top:-5px;right:-6px;font-size:8px">\u2764\uFE0F</span></span>';
    } else {
      btnLoop.textContent = LOOP_ICONS[loopMode];
    }
    btnLoop.classList.toggle("modified", loopMode !== "off");
  }
  _updateLoopUI();

  // favTracks 在 loadTrack() 中載入，與 isFavorite 同步

  btnLoop.addEventListener("click", () => {
    const idx = (LOOP_MODES.indexOf(loopMode) + 1) % LOOP_MODES.length;
    loopMode = LOOP_MODES[idx];
    localStorage.setItem("livechord_loop_mode", loopMode);
    _updateLoopUI();
    showToast(LOOP_LABELS[loopMode], 1500);
  });

  audio.addEventListener("ended", () => {
    if (loopMode === "single") return; // audio.loop handles it

    if (loopMode === "favorites" && favTracks.length > 0) {
      _navNext();
      return;
    }

    // off — stop
    btnPlay.innerHTML = "&#x25B6;";
    progress.style.width = "0%";
    if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
    if (activeChordIdx >= 0 && activeChordIdx < chordElements.length) {
      chordElements[activeChordIdx].classList.remove("active");
      if (ribbonElements[activeChordIdx]) ribbonElements[activeChordIdx].classList.remove("active");
    }
    activeChordIdx = -1;
    bigChordName.textContent = "—";
    bigChordJianpu.innerHTML = "";
    bigChordDiagram.innerHTML = "";
  });

  // 平板 FLAC 串流可能不觸發 ended，用 timeupdate 偵測播放結束
  audio.addEventListener("timeupdate", () => {
    if (loopMode === "favorites" && favTracks.length > 0 && audio.duration > 0) {
      if (audio.currentTime >= audio.duration - 0.5 && !audio.paused) {
        audio.pause();
        _navNext();
      }
    }
  });

  // progress bar seek + drag (三、時間軸點擊/拖曳連動)
  function _seekFromPointer(e) {
    const rect = progressBar.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    audio.currentTime = pct * (audio.duration || 0);
  }
  let _draggingProgress = false;
  progressBar.addEventListener("pointerdown", (e) => {
    _draggingProgress = true;
    progressBar.setPointerCapture(e.pointerId);
    _seekFromPointer(e);
  });
  progressBar.addEventListener("pointermove", (e) => {
    if (_draggingProgress) _seekFromPointer(e);
  });
  progressBar.addEventListener("pointerup", () => { _draggingProgress = false; });
  progressBar.addEventListener("pointercancel", () => { _draggingProgress = false; });

  // 還原上次音量
  const savedVol = localStorage.getItem("livechord_volume");
  if (savedVol !== null) {
    const v = parseFloat(savedVol);
    audio.volume = v;
    volumeSlider.value = v;
  }

  volumeSlider.addEventListener("input", () => {
    audio.volume = parseFloat(volumeSlider.value);
    localStorage.setItem("livechord_volume", volumeSlider.value);
  });

  // ---- 播放速度 ----
  const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2];
  const btnSpeed = $("#btnSpeed");
  const btnMiniSpeed = $("#btnMiniSpeed");
  let speedIdx = SPEEDS.indexOf(1); // default 1x

  function _syncSpeedUI() {
    const s = SPEEDS[speedIdx];
    const label = s + "x";
    if (btnSpeed) { btnSpeed.textContent = label; btnSpeed.classList.toggle("modified", s !== 1); }
    if (btnMiniSpeed) { btnMiniSpeed.textContent = label; btnMiniSpeed.classList.toggle("modified", s !== 1); }
  }

  const savedSpeed = localStorage.getItem("livechord_speed");
  if (savedSpeed !== null) {
    const s = parseFloat(savedSpeed);
    const i = SPEEDS.indexOf(s);
    if (i >= 0) { speedIdx = i; audio.playbackRate = s; }
  }
  _syncSpeedUI();

  function _cycleSpeed() {
    speedIdx = (speedIdx + 1) % SPEEDS.length;
    const s = SPEEDS[speedIdx];
    audio.playbackRate = s;
    _syncSpeedUI();
    localStorage.setItem("livechord_speed", s);
  }
  if (btnSpeed) btnSpeed.addEventListener("click", _cycleSpeed);
  if (btnMiniSpeed) btnMiniSpeed.addEventListener("click", _cycleSpeed);

  // prev / next
  $("#btnPrev").addEventListener("click", _navPrev);
  $("#btnNext").addEventListener("click", _navNext);

  // ---- edit link ----
  const btnEdit = $("#btnEdit");
  if (btnEdit) btnEdit.href = `/editor?path=${encodeURIComponent(trackPath)}`;

  // ---- AI 建議按鈕 ----
  const btnAiSuggest = $("#btnAiSuggest");
  if (btnAiSuggest) {
    btnAiSuggest.addEventListener("click", async () => {
      if (!chordData || !chordData.chords || chordData.chords.length === 0) {
        showToast("尚無和弦資料", 2000);
        return;
      }

      // 取目前播放位置附近的和弦作為 context
      const t = audio.currentTime || 0;
      const displayed = _displayChords();
      const recent = [];
      for (let i = displayed.length - 1; i >= 0; i--) {
        if (displayed[i].time <= t) {
          for (let j = Math.max(0, i - 2); j <= i; j++) {
            recent.push(displayed[j].chord);
          }
          break;
        }
      }
      if (recent.length === 0 && displayed.length > 0) {
        recent.push(displayed[0].chord);
      }

      const key = chordData.key || "C";

      try {
        const res = await fetch(`/api/ai/suggest?chords=${encodeURIComponent(recent.join(","))}&key=${encodeURIComponent(key)}&top_k=5`);
        const data = await res.json();

        if (data.suggestions && data.suggestions.length > 0) {
          const msg = data.suggestions
            .map(s => `${s.chord}(${s.degree}) ${Math.round(s.probability * 100)}%`)
            .join("  ");
          showToast(`AI: ${recent.join("→")} → ${msg}`, 5000);
        } else {
          showToast("AI 無法預測", 2000);
        }
      } catch (err) {
        showToast("AI 預測失敗: " + err.message, 3000);
      }
    });
  }

  // ---- Jazzify 按鈕 ----
  const btnJazzify = $("#btnJazzify");
  let jazzifyActive = false;
  let jazzifyLevel = 0;  // 0=off, 1/2/3=level
  let originalChords = null;

  if (btnJazzify) {
    btnJazzify.addEventListener("click", async () => {
      if (!chordData || !chordData.chords || chordData.chords.length === 0) {
        showToast("尚無和弦資料", 2000);
        return;
      }

      // 循環：off → L1 → L2 → L3 → off
      jazzifyLevel = (jazzifyLevel + 1) % 4;

      if (jazzifyLevel === 0) {
        // 還原原始
        if (originalChords) {
          chordData.chords = originalChords;
          originalChords = null;
        }
        jazzifyActive = false;
        btnJazzify.textContent = "\u{1F3B7}";
        btnJazzify.style.background = "";
        chordCache = {};
        await preloadChordInfo(chordData.chords);
        buildChordDOM();
        updateActiveChord(audio.currentTime || -1);
        showToast("已還原原始和弦", 1500);
        return;
      }

      // 儲存原始（只在第一次）
      if (!originalChords) {
        originalChords = [...chordData.chords];
      }

      btnJazzify.textContent = `${jazzifyLevel}...`;

      try {
        const res = await API.jazzify(originalChords, chordData.key || "C", jazzifyLevel);
        chordData.chords = res.chords;
        jazzifyActive = true;
        btnJazzify.textContent = `${jazzifyLevel}`;
        btnJazzify.style.background = "rgba(255,152,0,.3)";
        chordCache = {};
        await preloadChordInfo(chordData.chords);
        buildChordDOM();
        updateActiveChord(audio.currentTime || -1);
        showToast(`Jazzify L${jazzifyLevel}: ${res.original_count}→${res.jazzified_count} 和弦, ${res.changes.length} 變更`, 3000);
      } catch (err) {
        showToast("Jazzify 失敗: " + err.message, 3000);
        jazzifyLevel = 0;
        btnJazzify.textContent = "\u{1F3B7}";
        btnJazzify.style.background = "";
      }
    });
  }

  // ---- manual detect button ----
  const btnDetect = $("#btnDetect");
  if (btnDetect) {
    btnDetect.addEventListener("click", async () => {
      detectOverlay.style.display = "";
      detectMsg.textContent = "搜尋 MIDI…";
      detectDetail.textContent = "";

      try {
        const midiResult = await API.midiSearch(trackPath);

        if (midiResult.results && midiResult.results.length > 0) {
          // 找同名 MIDI（檔名去副檔名 = FLAC 檔名去副檔名）
          const flacName = trackPath.split("/").pop().replace(/\.flac$/i, "");
          const exactMatch = midiResult.results.find(m =>
            m.name.replace(/\.(mid|midi)$/i, "") === flacName
          );

          const chosen = exactMatch || midiResult.results[0];
          detectMsg.textContent = "MIDI 匯入中…";
          detectDetail.textContent = chosen.name;
          const result = await API.midiImport(trackPath, chosen.path);
          if (result.warning) {
            showToast(`⚠️ ${result.warning}`, 6000);
          } else {
            showToast(`MIDI 匯入！${result.chord_count} 和弦，Key: ${result.key}`, 3000);
          }
          chordCache = {};
          await loadChords(trackPath);
          updateActiveChord(audio.currentTime || -1);
          detectOverlay.style.display = "none";
          return;
        }

        // 無 MIDI → BTC 偵測
        detectMsg.textContent = "AI 偵測和弦中…";
        detectDetail.textContent = "分析音訊中，請稍候";
        const result = await API.detectChords(trackPath);
        if (result.chord_count === 0) {
          showToast("⚠️ AI 無法辨識此音檔的和弦（可能是合成音色或非常規音源）", 5000);
        } else {
          showToast(`偵測完成！${result.chord_count} 和弦，Key: ${result.key}`, 3000);
        }
        chordCache = {};
        await loadChords(trackPath);
        updateActiveChord(audio.currentTime || -1);
      } catch (err) {
        showToast("偵測失敗: " + err.message, 4000);
      } finally {
        detectOverlay.style.display = "none";
      }
    });
  }

  // ---- favorite ----
  btnFav.addEventListener("click", async () => {
    try {
      if (isFavorite) {
        await API.removeFavorite(trackPath);
        isFavorite = false;
        favTracks = favTracks.filter(p => p !== trackPath);
        showToast("已取消收藏");
      } else {
        await API.addFavorite(trackPath);
        isFavorite = true;
        if (!favTracks.includes(trackPath)) favTracks.unshift(trackPath);
        showToast("已加入最愛");
      }
      updateFavButton();
    } catch (err) {
      showToast("操作失敗: " + err.message);
    }
  });

  function updateFavButton() {
    btnFav.innerHTML = isFavorite ? "&#x2764;" : "&#x2661;";
    btnFav.classList.toggle("active", isFavorite);
  }

  // ---- mode switch ----
  // 簡譜/吉他/烏克麗麗模式切換（只限 #modeSwitch 內的按鈕）
  const capoGroup = $("#capoGroup");

  function _updateCapoVisibility() {
    if (capoGroup) {
      capoGroup.style.display = (displayMode === "guitar" || displayMode === "ukulele") ? "" : "none";
    }
  }

  document.querySelectorAll("#modeSwitch .mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#modeSwitch .mode-btn").forEach((b) => {
        b.style.background = "transparent";
        b.style.color = "var(--text-dim)";
        b.classList.remove("active");
      });
      btn.style.background = "var(--accent)";
      btn.style.color = "#fff";
      btn.classList.add("active");
      displayMode = btn.dataset.mode;
      _updateCapoVisibility();
      buildChordDOM();
      updateActiveChord(audio.currentTime || -1);
    });
  });

  // ---- Transpose / Capo ----
  const transposeUpBtn = $("#btnTransposeUp");
  const transposeDnBtn = $("#btnTransposeDown");
  const transposeVal = $("#transposeValue");
  const capoSelect = $("#capoSelect");

  function _updateKeyDisplay() {
    const keyInfo = $("#chordKey");
    if (keyInfo && chordData && chordData.key) {
      const shift = transpose - capo;
      const newKey = shift === 0 ? chordData.key : transposeChord(chordData.key, shift);
      keyInfo.textContent = `Key: ${newKey}`;
    }
  }

  if (transposeUpBtn) transposeUpBtn.addEventListener("click", () => {
    transpose = Math.min(transpose + 1, 11);
    transposeVal.textContent = transpose > 0 ? `+${transpose}` : transpose;
    _updateKeyDisplay();
    buildChordDOM(); updateActiveChord(audio.currentTime || -1);
  });
  if (transposeDnBtn) transposeDnBtn.addEventListener("click", () => {
    transpose = Math.max(transpose - 1, -11);
    transposeVal.textContent = transpose > 0 ? `+${transpose}` : transpose;
    _updateKeyDisplay();
    buildChordDOM(); updateActiveChord(audio.currentTime || -1);
  });
  if (capoSelect) capoSelect.addEventListener("change", () => {
    capo = parseInt(capoSelect.value) || 0;
    _updateKeyDisplay();
    buildChordDOM(); updateActiveChord(audio.currentTime || -1);
  });

  // ---- player 搜尋 ----
  const searchInput = $("#searchInput");
  const searchResults = $("#searchResults");
  let _searchTimer = null;

  if (searchInput && searchResults) {
    searchInput.addEventListener("input", () => {
      clearTimeout(_searchTimer);
      const q = searchInput.value.trim();
      if (q.length < 1) { searchResults.classList.remove("show"); return; }
      
      _searchTimer = setTimeout(async () => {
        try {
          const data = await API.search(q);
          if (data.error) {
            searchResults.innerHTML = `<div style="padding:12px;color:var(--text-dim)">${escapeHtml(data.error)}</div>`;
            searchResults.classList.add("show");
            return;
          }
          
          const results = data.results || [];
          if (results.length === 0) {
            searchResults.innerHTML = '<div style="padding:12px;color:var(--text-dim);font-size:12px">找不到結果</div>';
          } else {
            let html = "";
            for (const r of results.slice(0, 50)) {
              const coverUrl = API.trackCoverUrl(r.path);
              
              let diffHtml = "";
              const uc = r.unique_chords || 0;
              if (uc > 0) {
                let stars = 1;
                if (uc >= 15) stars = 4;
                else if (uc >= 9) stars = 3;
                else if (uc >= 5) stars = 2;
                const key = r.chord_key || "";
                diffHtml = ` <span class="difficulty" style="font-size:0.8em;opacity:0.6;margin-left:6px">${"⭐".repeat(stars)}${key ? " " + key : ""}</span>`;
              }
              
              html += `
                <div class="result-item" data-path="${escapeHtml(r.path)}">
                  <img class="r-cover" src="${coverUrl}" onerror="this.style.display='none'" loading="lazy" alt="">
                  <div class="r-info">
                    <div class="r-title">${escapeHtml(r.title || r.path.split("/").pop())}${diffHtml}</div>
                    <div class="r-artist">${escapeHtml(r.artist || "")} ${r.album ? "— " + escapeHtml(r.album) : ""}</div>
                  </div>
                </div>`;
            }
            searchResults.innerHTML = html;
          }
          searchResults.classList.add("show");
          
          searchResults.querySelectorAll(".result-item").forEach((el) => {
            el.addEventListener("click", () => {
              searchResults.classList.remove("show");
              window.location.href = `/player?path=${encodeURIComponent(el.dataset.path)}`;
            });
          });
        } catch {}
      }, 300);
    });

    // 點擊外面關閉
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".search-box")) searchResults.classList.remove("show");
    });

    searchInput.addEventListener("focus", () => {
      if (searchInput.value.trim().length > 0 && searchResults.innerHTML) {
        searchResults.classList.add("show");
      }
    });
  }

  // ---- init ----
  // ---- 垂直拖曳捲動（Overview 和弦時間軸）----
  function _initDragScroll(el) {
    if (!el || el.dataset.dragscroll) return;
    el.dataset.dragscroll = "1";

    let isDragging = false;
    let startY = 0, scrollStart = 0;
    let lastY = 0, lastTime = 0, velocity = 0;
    let momentumId = null;

    function _stopMomentum() {
      if (momentumId) { cancelAnimationFrame(momentumId); momentumId = null; }
    }

    function _startDrag(y) {
      _stopMomentum();
      isDragging = true;
      startY = y;
      scrollStart = el.scrollTop;
      lastY = y;
      lastTime = Date.now();
      velocity = 0;
      el.style.cursor = "grabbing";
      el.style.userSelect = "none";
    }

    function _moveDrag(y) {
      if (!isDragging) return;
      const dy = y - startY;
      el.scrollTop = scrollStart - dy;

      const now = Date.now();
      const dt = now - lastTime;
      if (dt > 0) {
        velocity = (lastY - y) / dt * 16;
      }
      lastY = y;
      lastTime = now;
    }

    function _endDrag() {
      if (!isDragging) return;
      isDragging = false;
      el.style.cursor = "";
      el.style.userSelect = "";

      if (Math.abs(velocity) > 0.5) {
        function coast() {
          velocity *= 0.95;
          if (Math.abs(velocity) < 0.3) return;
          el.scrollTop += velocity;
          momentumId = requestAnimationFrame(coast);
        }
        momentumId = requestAnimationFrame(coast);
      }
    }

    el.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      _startDrag(e.clientY);
      e.preventDefault();
    });
    window.addEventListener("mousemove", (e) => {
      if (!isDragging) return;
      if (e.buttons === 0) { _endDrag(); return; }
      _moveDrag(e.clientY);
    });
    window.addEventListener("mouseup", _endDrag);
    document.addEventListener("mouseleave", _endDrag);

    el.addEventListener("touchstart", (e) => {
      _startDrag(e.touches[0].clientY);
    }, { passive: true });
    el.addEventListener("touchmove", (e) => {
      _moveDrag(e.touches[0].clientY);
    }, { passive: true });
    el.addEventListener("touchend", _endDrag);

    // prevent click after drag
    el.addEventListener("click", (e) => {
      if (Math.abs(velocity) > 1 || Math.abs(lastY - startY) > 5) {
        e.stopPropagation();
        e.preventDefault();
      }
    }, true);
  }

  _initDragScroll(chordDisplayOverview);

  loadTrack(trackPath).then(() => {
    if (autoplay) audio.play().catch(() => {});
    if (restoreFs && chordDisplay) {
      chordDisplay.classList.add("fullscreen");
      if (btnFullscreen) btnFullscreen.innerHTML = "&#x2716;";
      document.documentElement.requestFullscreen().catch(() => {});
    }
  });
})();

// ---- 移調工具函式 (全域) ----
const NOTE_NAMES_SHARP = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];
const NOTE_NAMES_FLAT  = ["C","Db","D","Eb","E","F","Gb","G","Ab","A","Bb","B"];

function noteToSemitone(n) {
  const m = {C:0,D:2,E:4,F:5,G:7,A:9,B:11};
  let s = m[n[0].toUpperCase()] || 0;
  if (n.length > 1) { if (n[1]==="#") s++; else if (n[1]==="b") s--; }
  return ((s%12)+12)%12;
}
function semitoneToNote(s, flat) {
  s = ((s%12)+12)%12;
  return flat ? NOTE_NAMES_FLAT[s] : NOTE_NAMES_SHARP[s];
}
function transposeChord(chord, semi) {
  if (!chord || semi === 0) return chord;
  const m = chord.match(/^([A-G][b#]?)(.*?)(?:\/([A-G][b#]?))?$/);
  if (!m) return chord;
  const flat = m[1].includes("b") || chord.includes("b");
  let r = semitoneToNote(noteToSemitone(m[1])+semi, flat) + (m[2]||"");
  if (m[3]) r += "/" + semitoneToNote(noteToSemitone(m[3])+semi, flat);
  return r;
}

// 相對於 key 的簡譜標記
const JIANPU_NAMES = ["1","#1","2","#2","3","4","#4","5","#5","6","#6","7"];
const JIANPU_FLAT  = ["1","b2","2","b3","3","4","b5","5","b6","6","b7","7"];

function chordToJianpu(chord, key) {
  const m = chord.match(/^([A-G][b#]?)/);
  if (!m) return "";
  const notes = chord.match(/^([A-G][b#]?)(.*?)(?:\/([A-G][b#]?))?$/);
  if (!notes) return "";
  const root = notes[1];
  const keySemi = noteToSemitone(key || "C");
  const useFlat = root.includes("b") || (key && key.includes("b"));

  // 從 chord_table API 回傳的 notes 計算
  // 這裡用簡化方式：根音相對於 key 的簡譜
  const interval = ((noteToSemitone(root) - keySemi) % 12 + 12) % 12;
  return useFlat ? JIANPU_FLAT[interval] : JIANPU_NAMES[interval];
}
