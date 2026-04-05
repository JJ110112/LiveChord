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
  let sectionData = null;
  // 88-key piano state
  let piano88Canvas = null;
  let piano88Cache = null;
  let piano88ChordMidis = [];
  let piano88SustainNotes = [];
  let piano88PrevMidi = null;
  let piano88LastIdx = -1;
  let piano88Hand = localStorage.getItem("livechord_88hand") || "both"; // "both"|"left"|"right"
  let hasChords = false;
  let keys88RibbonTrack = null;
  let keys88RibbonBuilt = false;
  // Waterfall / teaching mode state
  let waterfallCanvas = null;
  let activeHand = localStorage.getItem("livechord_active_hand") || "both";
  let waterfallCtx = null;
  let waterfallActive = true;
  let show88ChordTones = localStorage.getItem("livechord_show_chord_tones") === "true";
  let teachStyle = localStorage.getItem("livechord_teach_style") || "Arpeggio";
  let teachLevel = localStorage.getItem("livechord_teach_level") || "L1";
  if (!["L1", "L2", "L3"].includes(teachLevel)) teachLevel = "L1";
  let accData = null;  // {left_hand:[], right_hand:[]} from API
  let accLoading = false;
  let transpose = 0;
  let capo = 0;
  let favTracks = [];
  let activeLoadingTasks = 0;
  let loadingDelayTimer = null;

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
  const fsProgressBar = $("#fsProgressBar");
  const fsProgress = $("#fsProgress");
  const timeCurrent = $("#timeCurrent");
  const fsTimeCurrent = $("#fsTimeCurrent");
  const timeDuration = $("#timeDuration");
  const fsTimeDuration = $("#fsTimeDuration");
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
  const tabKeys = $("#tabKeys");
  const chordDisplay88 = $("#chordDisplay88");

  // ---- A-B Repeat state ----
  const btnABRepeat = $("#btnABRepeat");
  const abRange = $("#abRange");
  const fsAbRange = $("#fsAbRange");
  let abState = "idle";  // idle → a_set → active
  let abA = null;        // start time (seconds)
  let abB = null;        // end time (seconds)

  function _updateABRangeUI() {
    const d = audio.duration || 1;
    if (abState === "active" && abA != null && abB != null) {
      const left = (abA / d * 100) + "%";
      const width = ((abB - abA) / d * 100) + "%";
      [abRange, fsAbRange].forEach(el => {
        if (!el) return;
        el.style.display = "block";
        el.style.left = left;
        el.style.width = width;
      });
    } else if (abState === "a_set" && abA != null) {
      const left = (abA / d * 100) + "%";
      [abRange, fsAbRange].forEach(el => {
        if (!el) return;
        el.style.display = "block";
        el.style.left = left;
        el.style.width = "2px";
      });
    } else {
      [abRange, fsAbRange].forEach(el => {
        if (el) el.style.display = "none";
      });
    }
  }

  function _clearABRepeat() {
    abState = "idle";
    abA = null;
    abB = null;
    if (btnABRepeat) {
      btnABRepeat.classList.remove("a-set", "ab-active");
      btnABRepeat.textContent = "A-B";
    }
    _updateABRangeUI();
  }

  if (btnABRepeat) {
    btnABRepeat.addEventListener("click", () => {
      const t = audio.currentTime;
      if (abState === "idle") {
        abA = t;
        abState = "a_set";
        btnABRepeat.classList.add("a-set");
        btnABRepeat.textContent = "A-⏸";
        showToast("A 點: " + formatTime(t), 1500);
      } else if (abState === "a_set") {
        if (t <= abA) {
          showToast("B 點必須在 A 點之後", 1500);
          return;
        }
        abB = t;
        abState = "active";
        btnABRepeat.classList.remove("a-set");
        btnABRepeat.classList.add("ab-active");
        btnABRepeat.textContent = "A-B ✓";
        audio.currentTime = abA;
        showToast("A-B 循環: " + formatTime(abA) + " → " + formatTime(abB), 2000);
      } else {
        _clearABRepeat();
        showToast("A-B 循環已取消", 1500);
      }
      _updateABRangeUI();
    });
  }

  function _updateHandSwitchVisibility() {
    const hs = document.querySelector("#handSwitchContainer");
    const topHs = document.querySelector("#btnTopHandSwitch");
    const ms = document.querySelector("#modeSwitch");
    if (hs) hs.style.display = activeTab === "keys" ? "flex" : "none";
    if (topHs) topHs.style.display = activeTab === "keys" ? "flex" : "none";
    if (ms) ms.style.display = activeTab === "keys" ? "none" : "flex";
    // Hide zoom controls in keys tab (piano has fixed size)
    const hideZoom = activeTab === "keys";
    if (btnZoomIn) btnZoomIn.style.display = hideZoom ? "none" : "";
    if (btnZoomOut) btnZoomOut.style.display = hideZoom ? "none" : "";
    if (btnZoomReset) btnZoomReset.style.display = hideZoom ? "none" : "";
  }

  function _setAllTabsInactive() {
    if (tabOverview) tabOverview.classList.remove("active");
    if (tabDiagrams) tabDiagrams.classList.remove("active");
    if (tabKeys) tabKeys.classList.remove("active");
    chordDisplayOverview.style.display = "none";
    chordDisplayDiagrams.style.display = "none";
    if (chordDisplay88) chordDisplay88.style.display = "none";
  }

  const bigChordDiagram = $("#bigChordDiagram");

  // ---- 和弦區縮放 (must be before tab handlers that call _switchZoomToTab) ----
  const ZOOM_STEPS = [50, 67, 75, 80, 90, 100, 110, 125, 150, 175, 200, 250, 300];
  const ZOOM_FS_DEFAULTS = { overview: 200, diagrams: 200, keys: 100 };
  const _tabZoomFs = {};
  for (const tab of ["overview", "diagrams", "keys"]) {
    const saved = parseInt(localStorage.getItem(`livechord_zoom_${tab}`));
    _tabZoomFs[tab] = ZOOM_STEPS.indexOf(saved > 0 ? saved : ZOOM_FS_DEFAULTS[tab]);
    if (_tabZoomFs[tab] < 0) _tabZoomFs[tab] = ZOOM_STEPS.indexOf(ZOOM_FS_DEFAULTS[tab]);
  }
  let zoomIdx = ZOOM_STEPS.indexOf(100);
  const btnZoomIn = $("#btnZoomIn");
  const btnZoomOut = $("#btnZoomOut");
  const btnZoomReset = $("#btnZoomReset");
  const chordDisplayEl = $("#chordDisplay");

  if (tabOverview && tabDiagrams) {
    tabOverview.addEventListener("click", () => {
      activeTab = "overview";
      localStorage.setItem("livechord_tab", "overview");
      _setAllTabsInactive();
      tabOverview.classList.add("active");
      chordDisplayOverview.style.display = "";
      _updateHandSwitchVisibility();
      _switchZoomToTab("overview");
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
      _setAllTabsInactive();
      tabDiagrams.classList.add("active");
      chordDisplayDiagrams.style.display = "block";
      _updateHandSwitchVisibility();
      _switchZoomToTab("diagrams");
      // Diagrams 隱藏大和弦（避免重複）
      bigChordBox.style.display = "none";
      const t = audio.currentTime || 0;
      if (ribbonTrack) {
        ribbonTrack.style.transform = `translateX(${-t * pxPerSec}px)`;
      }
    });

    if (tabKeys) {
      tabKeys.addEventListener("click", () => {
        activeTab = "keys";
        localStorage.setItem("livechord_tab", "keys");
        _setAllTabsInactive();
        tabKeys.classList.add("active");
        chordDisplay88.style.display = "flex";
        _updateHandSwitchVisibility();
        _switchZoomToTab("keys");
        bigChordBox.style.display = "";
        _init88Piano();
        _initWaterfall();
        _setupTeachControls();
        _buildKeys88Ribbon();
        if (sectionData) _renderSectionMarkers();
        piano88LastIdx = -1;
        update88Piano(audio.currentTime || 0);
      });
    }

    // 還原上次 tab
    if (activeTab === "diagrams") tabDiagrams.click();
    else if (activeTab === "keys" && tabKeys) tabKeys.click();
  }

  function _isFullscreen() {
    return chordDisplayEl && chordDisplayEl.closest(".fullscreen") != null;
  }
  function _applyZoom() {
    let pct;
    if (_isFullscreen()) {
      pct = ZOOM_STEPS[zoomIdx];
      if (activeTab === "keys") pct = 100;
      _tabZoomFs[activeTab] = zoomIdx;
      localStorage.setItem(`livechord_zoom_${activeTab}`, pct);
    } else {
      pct = 100; // Normal mode is always 100%
    }
    const scaleTarget = document.getElementById("chordDisplayScaleTarget") || chordDisplayEl;
    if (scaleTarget) {
      scaleTarget.style.transformOrigin = "top left";
      scaleTarget.style.transform = `scale(${pct / 100})`;
      scaleTarget.style.width = (10000 / pct) + "%";
      // To properly handle flex inside, min-height needs to be 0
      scaleTarget.style.height = (10000 / pct) + "%";
      if(scaleTarget.id === "chordDisplayScaleTarget") {
         chordDisplayEl.style.transform = "none";
         chordDisplayEl.style.width = "100%";
         chordDisplayEl.style.height = "100%";
      }
    }
    if (btnZoomReset) btnZoomReset.textContent = pct + "%";
  }
  function _switchZoomToTab(tab) {
    if (_isFullscreen()) {
      zoomIdx = _tabZoomFs[tab] != null ? _tabZoomFs[tab] : ZOOM_STEPS.indexOf(ZOOM_FS_DEFAULTS[tab]);
    } else {
      zoomIdx = ZOOM_STEPS.indexOf(100);
    }
    _applyZoom();
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
    document.body.style.overflow = "hidden";
    btnFullscreen.innerHTML = "&#x2716;";
    if (btnPageFs) btnPageFs.innerHTML = "&#x2716;";
    // restore per-tab zoom (defaults: overview/diagrams=200%, keys=100%)
    _switchZoomToTab(activeTab);
    document.documentElement.requestFullscreen().catch(() => {});
  }
  function _exitFullscreen() {
    chordDisplay.classList.remove("fullscreen");
    document.body.style.overflow = "";
    btnFullscreen.innerHTML = "&#x26F6;";
    if (btnPageFs) btnPageFs.innerHTML = "&#x26F6;";
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
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
      document.body.style.overflow = "";
      if (btnFullscreen) btnFullscreen.innerHTML = "&#x26F6;";
      if (btnPageFs) btnPageFs.innerHTML = "&#x26F6;";
      zoomIdx = ZOOM_STEPS.indexOf(100);
      _applyZoom();
    }
  });

  // 相對簡譜：notes 陣列相對於當前 key 的簡譜
  function _notesToJianpu(notes, key) {
    if (!notes || notes.length === 0) return "";
    const JP = ["1","#1","2","#2","3","4","#4","5","#5","6","#6","7"];
    const JPF = ["1","b2","2","b3","3","4","b5","5","b6","6","b7","7"];
    const keySemi = 0; // always C-relative solfege
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
    _clearABRepeat();
    _setLoadingState(true, "載入樂曲中...", "正在讀取歌曲資訊與和弦編排...");
    try {
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
    } finally {
      _setLoadingState(false);
    }
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
  let _melodyPendingPlay = false;

  async function _loadMelody(path) {
    const interceptPlay = () => {
      audio.pause();
      _melodyPendingPlay = true;
      if (typeof btnPlay !== 'undefined' && btnPlay) btnPlay.innerHTML = "&#x25B6;";
    };
    audio.addEventListener("play", interceptPlay);

    try {
      _setLoadingState(true, "AI 旋律提取中...", "首次播放需要分離音軌...");
      if (!audio.paused) {
        audio.pause();
        _melodyPendingPlay = true;
      }

      const res = await fetch(`/api/ai/melody?path=${encodeURIComponent(path)}`);
      const data = await res.json();
      if (data.melody && data.melody.length > 0) {
        melodyData = data.melody;
      }
    } catch {} finally {
      audio.removeEventListener("play", interceptPlay);
      _setLoadingState(false);
      if (_melodyPendingPlay) {
        _melodyPendingPlay = false;
        audio.play().catch(() => {});
      }
    }
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

  // ---- 88-key piano ----

  function _get88PianoMaxWidth() {
    const w = chordDisplay88.clientWidth || 800;
    const containerH = chordDisplay88.clientHeight || 400;
    const maxKeyH = Math.max(80, containerH - 40);  // leave room for labels
    return Math.min(w, Math.round(maxKeyH / 6 * 52));  // keyH = keyW * 6
  }

  function _init88Piano() {
    if (!chordDisplay88) return;
    piano88Canvas = $("#piano88Canvas");
    if (!piano88Canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const maxW = _get88PianoMaxWidth();
    piano88Cache = ChordRender.init88PianoCache(maxW, dpr);
    const h = piano88Cache.totalH;
    piano88Canvas.width = Math.round(maxW * dpr);
    piano88Canvas.height = Math.round(h * dpr);
    piano88Canvas.style.width = maxW + "px";
    piano88Canvas.style.height = h + "px";
    // draw static keyboard immediately
    ChordRender.draw88Piano(piano88Canvas, piano88Cache, [], -1, {});
  }

  // Get melody notes (as jianpu) within a time range
  function _getMelodyJianpuInRange(start, end) {
    if (!melodyData) return "";
    const SEMI_TO_NAME = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];
    const JP = ["1","\u{266F}1","2","\u{266F}2","3","4","\u{266F}4","5","\u{266F}5","6","\u{266F}6","7"];
    const seen = new Set();
    const notes = [];
    for (const m of melodyData) {
      if (m.start >= end) break;
      if (m.end <= start) continue;
      const pc = m.midi % 12;
      if (!seen.has(pc)) {
        seen.add(pc);
        notes.push(JP[pc]);
      }
    }
    return notes.join(" ");
  }

  function _buildKeys88Ribbon() {
    keys88RibbonTrack = $("#keys88RibbonTrack");
    if (!keys88RibbonTrack) return;
    keys88RibbonTrack.innerHTML = "";
    keys88RibbonBuilt = false;

    const chords = _displayChords();
    if (!chords || chords.length === 0 || _ribbonPositions.length === 0) return;

    for (let i = 0; i < chords.length; i++) {
      const chord = chords[i];
      const cache = chordCache[chord.chord] || {};
      const pos = _ribbonPositions[i] || { left: chord.time * pxPerSec, width: 120 };
      const chordEnd = (i + 1 < chords.length) ? chords[i + 1].time : chord.time + 4;

      const div = document.createElement("div");
      div.className = "ribbon-item keys88-ribbon-item";
      div.style.left = `${pos.left}px`;
      div.style.width = `${pos.width}px`;

      // chord name
      const nameEl = document.createElement("div");
      nameEl.className = "chord-name";
      nameEl.textContent = chord.chord;
      div.appendChild(nameEl);

      // chord jianpu
      const jp = document.createElement("div");
      jp.className = "chord-jianpu";
      jp.innerHTML = ChordRender.jianpuToHtml(_notesToJianpu(cache.notes, _currentKey()));
      div.appendChild(jp);

      // melody jianpu
      const melJp = _getMelodyJianpuInRange(chord.time, chordEnd);
      if (melJp) {
        const melEl = document.createElement("div");
        melEl.className = "melody-jianpu";
        melEl.textContent = melJp;
        div.appendChild(melEl);
      }

      div.addEventListener("click", () => {
        audio.currentTime = chord.time;
      });
      keys88RibbonTrack.appendChild(div);
    }
    keys88RibbonBuilt = true;
  }

  function update88Piano(currentTime) {
    if (!piano88Canvas || !piano88Cache) return;
    if (!chordData || !chordData.chords) return;

    // re-init cache if canvas resized
    const maxW = _get88PianoMaxWidth();
    const dpr = window.devicePixelRatio || 1;
    if (Math.abs(piano88Cache.canvas.width / dpr - maxW) > 2) {
      piano88Cache = ChordRender.init88PianoCache(maxW, dpr);
      const h = piano88Cache.totalH;
      piano88Canvas.width = Math.round(maxW * dpr);
      piano88Canvas.height = Math.round(h * dpr);
      piano88Canvas.style.width = maxW + "px";
      piano88Canvas.style.height = h + "px";
    }

    const chords = _displayChords();
    if (!chords || chords.length === 0) return;

    // scroll the keys88 ribbon (fullscreen only)
    if (keys88RibbonTrack && keys88RibbonBuilt && _ribbonPositions.length > 0) {
      let scrollX = currentTime * pxPerSec;
      for (let i = _ribbonPositions.length - 1; i >= 0; i--) {
        const p = _ribbonPositions[i];
        if (currentTime >= p.time) {
          const frac = p.width > 0 ? (currentTime - p.time) * pxPerSec / ((i + 1 < _ribbonPositions.length ? _ribbonPositions[i+1].time - p.time : 4) * pxPerSec) : 0;
          scrollX = p.left + Math.min(frac, 1) * p.width;
          break;
        }
      }
      keys88RibbonTrack.style.transform = `translateX(${-scrollX}px)`;
    }

    // find current chord index
    let newIdx = -1;
    for (let i = chords.length - 1; i >= 0; i--) {
      if (currentTime >= chords[i].time) { newIdx = i; break; }
    }

    // on chord change, move old notes to sustain list
    if (newIdx !== piano88LastIdx) {
      if (piano88ChordMidis.length > 0) {
        for (const m of piano88ChordMidis) {
          piano88SustainNotes.push({ midi: m, release: currentTime });
        }
      }
      piano88LastIdx = newIdx;

      // highlight active ribbon item
      if (keys88RibbonTrack && keys88RibbonBuilt) {
        keys88RibbonTrack.querySelectorAll(".ribbon-item").forEach((el, i) => {
          el.classList.toggle("active", i === newIdx);
        });
      }

      if (newIdx >= 0) {
        const chord = chords[newIdx];
        const cache = chordCache[chord.chord] || {};
        const notes = cache.notes || [];
        piano88ChordMidis = ChordRender.voiceChordForLeftHand(notes, piano88PrevMidi);
        piano88PrevMidi = [...piano88ChordMidis];

        // update big chord box (show in normal mode, hide in fullscreen where ribbon has it)
        bigChordName.textContent = chord.chord;
        bigChordBox.style.display = _isFullscreen() ? "none" : "";
        bigChordJianpu.innerHTML = ChordRender.jianpuToHtml(_notesToJianpu(cache.notes, _currentKey()));
        bigChordDiagram.innerHTML = "";
        // show section color bar on big chord box
        if (sectionData && sectionData.sections) {
          const sec = sectionData.sections.find(s => chord.time >= s.start && chord.time < s.end);
          bigChordBox.style.borderTopColor = sec ? sec.color : "transparent";
          bigChordBox.title = sec ? `${sec.emoji} ${sec.label || sec.type}` : "";
        }
      } else {
        piano88ChordMidis = [];
      }
    }

    // prune old sustain notes
    piano88SustainNotes = piano88SustainNotes.filter(n => currentTime - n.release < 0.5);

    // determine actual played notes from accData if available
    let activeLh = [];
    let activeRh = [];
    if (waterfallActive && accData) {
       for (const e of (accData.left_hand||[])) {
           if (e.time <= currentTime && e.time + e.duration >= currentTime) activeLh.push(e.pitch);
       }
       for (const e of (accData.right_hand||[])) {
           if (e.time <= currentTime && e.time + e.duration >= currentTime) activeRh.push(e.pitch);
       }
    } else {
       activeLh = [...piano88ChordMidis];
    }
    
    const mel = _getMelodyMidi(currentTime);
    if (mel >= 0 && !activeRh.includes(mel)) activeRh.push(mel);

    if (activeHand === "left") activeRh = [];
    if (activeHand === "right") activeLh = [];

    let chordTones = [];
    const isChordTonesActive = show88ChordTones && piano88ChordMidis && piano88ChordMidis.length > 0;

    if (isChordTonesActive) {
      chordTones = [...new Set(piano88ChordMidis.map(m => m % 12))];
    }

    ChordRender.draw88Piano(piano88Canvas, piano88Cache, activeLh, activeRh, {
      chordTones: chordTones,
      sustainNotes: activeHand === "right" ? [] : piano88SustainNotes,
      now: currentTime,
    });
  }

  // ---- Waterfall / Teaching Mode ----

  function _setLoadingState(isLoading, msg, detail, type = 'spinner') {
    if (isLoading) {
      activeLoadingTasks++;
      if (msg) detectMsg.textContent = msg;
      if (detail) detectDetail.textContent = detail;
      
      const spinner = document.getElementById("loadingSpinner");
      const musicBars = document.getElementById("musicBarsAnim");
      if (spinner && musicBars) {
        if (type === 'music') {
          spinner.style.display = "none";
          musicBars.style.display = "flex";
        } else {
          spinner.style.display = "block";
          musicBars.style.display = "none";
        }
      }

      if (type === 'music') {
        clearTimeout(loadingDelayTimer);
        // Show instantly for streaming buffers
        if (activeLoadingTasks > 0) detectOverlay.style.display = "flex";
      } else if (activeLoadingTasks === 1) {
        clearTimeout(loadingDelayTimer);
        // Delay overlay to avoid quick flashes if cached
        loadingDelayTimer = setTimeout(() => {
          if (activeLoadingTasks > 0) detectOverlay.style.display = "flex";
        }, 500);
      }
    } else {
      activeLoadingTasks--;
      if (activeLoadingTasks <= 0) {
        activeLoadingTasks = 0;
        clearTimeout(loadingDelayTimer);
        detectOverlay.style.display = "none";
      }
    }
  }

  function _initWaterfall() {
    waterfallCanvas = $("#waterfallCanvas");
    if (!waterfallCanvas) return;
    waterfallCtx = waterfallCanvas.getContext("2d");
    
    waterfallActive = true;
    waterfallCanvas.classList.add("active");
    if (chordDisplay88) chordDisplay88.classList.add("waterfall-active");
    _loadAccompaniment();
    setTimeout(_resizeWaterfall, 50);
  }

  function _resizeWaterfall() {
    if (!waterfallCanvas || !waterfallActive) return;
    const w = chordDisplay88.clientWidth || 800;
    const h = waterfallCanvas.clientHeight || 200;
    const dpr = window.devicePixelRatio || 1;
    waterfallCanvas.width = Math.round(w * dpr);
    waterfallCanvas.height = Math.round(h * dpr);
    waterfallCtx.scale(dpr, dpr);
    if (audio.paused) drawWaterfall(audio.currentTime || 0);
  }

  function _loadAccompaniment() {
    if (!trackPath || accLoading) return;
    if (accData && accData._style === teachStyle && accData._level === teachLevel) return;
    accLoading = true;
    _setLoadingState(true, "AI 伴奏提取中...", "首次播放需要進行即時演算...");
    const url = `/api/ai/accompaniment?path=${encodeURIComponent(trackPath)}&style=${teachStyle}&level=${teachLevel}`;
    fetch(url).then(r => r.json()).then(data => {
      if (data.error) {
        console.warn("Accompaniment:", data.error);
        accData = null;
      } else {
        data._style = teachStyle;
        data._level = teachLevel;
        accData = data;
      }
      accLoading = false;
      _setLoadingState(false);
    }).catch(e => {
      console.error("Accompaniment fetch error:", e);
      accData = null;
      accLoading = false;
      _setLoadingState(false);
    });
  }

  function drawWaterfall(currentTime) {
    if (!waterfallCanvas || !waterfallCtx || !waterfallActive || !accData) return;
    if (!piano88Cache) return;

    const w = waterfallCanvas.clientWidth;
    const h = waterfallCanvas.clientHeight;
    if (w < 10 || h < 10) return;

    const ctx = waterfallCtx;
    ctx.clearRect(0, 0, w, h);

    // Draw vertical piano key grid
    ctx.strokeStyle = "rgba(255, 255, 255, 0.05)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    let lastKey = null;
    for (const p in piano88Cache.whiteXs) {
      const wk = piano88Cache.whiteXs[p];
      ctx.moveTo(wk.x, 0);
      ctx.lineTo(wk.x, h);
      lastKey = wk;
    }
    if (lastKey) {
      ctx.moveTo(lastKey.x + lastKey.w, 0);
      ctx.lineTo(lastKey.x + lastKey.w, h);
    }
    ctx.stroke();

    // Time window: show notes from currentTime to currentTime + lookAhead
    const lookAhead = 4.0;  // seconds visible above piano
    const pxPerSec = h / lookAhead;

    // Semantic Colors
    const LH_COLOR = "rgba(33, 150, 243, 0.9)";   // Blue (穩重/左手)
    const RH_COLOR = "rgba(255, 152, 0, 0.9)";    // Orange (主旋律/右手)
    const LH_GLOW  = "rgba(33, 150, 243, 0.4)";
    const RH_GLOW  = "rgba(255, 152, 0, 0.4)";

    // 畫拍線網格 (Beat Grid & Bar Lines)
    const bpm = (accData.bpm || 100); 
    const secPerBeat = 60 / bpm;
    const firstBeatTime = Math.floor(currentTime / secPerBeat) * secPerBeat;
    ctx.textAlign = "left";
    ctx.textBaseline = "bottom";
    ctx.font = "11px sans-serif";
    for (let bt = firstBeatTime; bt < currentTime + lookAhead; bt += secPerBeat) {
      const beatNum = Math.round(bt / secPerBeat);
      const beatInBar = (beatNum % 4) + 1; // 1, 2, 3, 4
      const isBarLine = (beatNum % 4 === 0);
      const y = h - (bt - currentTime) * pxPerSec;
      if (y < 0 || y > h) continue;
      
      ctx.strokeStyle = isBarLine ? "rgba(255,255,255,0.25)" : "rgba(255,255,255,0.08)";
      ctx.lineWidth = isBarLine ? 2 : 1;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();

      // Draw beat number at left edge
      ctx.fillStyle = isBarLine ? "rgba(255,255,255,0.6)" : "rgba(255,255,255,0.3)";
      ctx.fillText(beatInBar, 8, y - 2);
    }

    // A-B Repeat boundary lines on waterfall
    if (abState !== "idle" && abA != null) {
      const yA = h - (abA - currentTime) * pxPerSec;
      if (yA >= 0 && yA <= h) {
        ctx.strokeStyle = "#4fc3f7";
        ctx.lineWidth = 2;
        ctx.setLineDash([6, 3]);
        ctx.beginPath(); ctx.moveTo(0, yA); ctx.lineTo(w, yA); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = "#4fc3f7";
        ctx.font = "bold 11px sans-serif";
        ctx.textAlign = "right";
        ctx.fillText("A", w - 6, yA - 4);
      }
      if (abState === "active" && abB != null) {
        const yB = h - (abB - currentTime) * pxPerSec;
        if (yB >= 0 && yB <= h) {
          ctx.strokeStyle = "#4caf50";
          ctx.lineWidth = 2;
          ctx.setLineDash([6, 3]);
          ctx.beginPath(); ctx.moveTo(0, yB); ctx.lineTo(w, yB); ctx.stroke();
          ctx.setLineDash([]);
          ctx.fillStyle = "#4caf50";
          ctx.font = "bold 11px sans-serif";
          ctx.textAlign = "right";
          ctx.fillText("B", w - 6, yB - 4);
        }
        // Dim area outside A-B range
        const yAclamped = Math.max(0, Math.min(h, yA));
        const yBclamped = Math.max(0, Math.min(h, yB));
        if (yBclamped < yAclamped) {
          ctx.fillStyle = "rgba(0, 0, 0, 0.3)";
          if (yBclamped > 0) ctx.fillRect(0, 0, w, yBclamped);
          if (yAclamped < h) ctx.fillRect(0, yAclamped, w, h - yAclamped);
        }
      }
    }

    const allEvents = [];
    if (activeHand === "both" || activeHand === "left") {
      allEvents.push(...(accData.left_hand || []).map(e => ({...e, _hand: "left"})));
    }
    if (activeHand === "both" || activeHand === "right") {
      let rhEvents = accData.right_hand || [];
      if (rhEvents.length === 0 && typeof melodyData !== 'undefined' && melodyData) {
        rhEvents = melodyData.map(m => ({
          time: m.start,
          duration: m.end - m.start,
          pitch: m.midi,
          finger: null
        }));
      }
      allEvents.push(...rhEvents.map(e => ({...e, _hand: "right"})));
    }

    const cache = piano88Cache;
    ctx.font = "bold 11px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";

    // 左手手型背景區塊 (Position Hand Block)
    const lhEvents = allEvents.filter(e => e._hand === "left" && e.time >= currentTime && e.time <= currentTime + lookAhead);
    if (lhEvents.length > 0) {
      let minX = w, maxX = 0, minY = h, maxY = 0;
      for (const evt of lhEvents) {
        const ki = cache.whiteXs[evt.pitch] || cache.blackXs[evt.pitch];
        if (!ki) continue;
        const x = ki.x;
        const ex = x + ki.w;
        const yt = Math.max(0, h - (evt.time + evt.duration - currentTime) * pxPerSec);
        const yb = Math.min(h, h - (evt.time - currentTime) * pxPerSec);
        if (x < minX) minX = x;
        if (ex > maxX) maxX = ex;
        if (yt < minY) minY = yt;
        if (yb > maxY) maxY = yb;
      }
      if (maxX > minX && maxY > minY) {
        ctx.fillStyle = "rgba(33, 150, 243, 0.05)"; // very faint blue box
        ctx.fillRect(minX - 4, minY - 4, maxX - minX + 8, maxY - minY + 8);
        ctx.strokeStyle = "rgba(33, 150, 243, 0.15)";
        ctx.lineWidth = 1;
        ctx.strokeRect(minX - 4, minY - 4, maxX - minX + 8, maxY - minY + 8);
      }
    }

    // 將指法分群與優化顯示
    let lhLastF = 0, rhLastF = 0;
    
    for (const evt of allEvents) {
      const noteStart = evt.time;
      const noteEnd = evt.time + evt.duration;

      if (noteEnd < currentTime || noteStart > currentTime + lookAhead) continue;

      const yBottom = h - (noteStart - currentTime) * pxPerSec;
      const yTop = h - (noteEnd - currentTime) * pxPerSec;
      const noteH = Math.max(yBottom - yTop, 3);

      const midi = evt.pitch;
      const keyInfo = cache.whiteXs[midi] || cache.blackXs[midi];
      if (!keyInfo) continue;
      const x = keyInfo.x;
      const kw = keyInfo.w;

      const isLeft = evt._hand === "left";
      const color = isLeft ? LH_COLOR : RH_COLOR;
      const glow = isLeft ? LH_GLOW : RH_GLOW;

      // Drop prediction shadow on the keys if it's right about to hit
      if (yBottom > h - 40 && yBottom < h) {
        ctx.fillStyle = glow;
        ctx.fillRect(x, h - 5, kw, -20); // predictive glow standing on the landing bar
      }

      // Note bar
      ctx.fillStyle = color;
      const r = Math.min(4, noteH / 2);
      ctx.beginPath();
      ctx.roundRect(x + 1, yTop, kw - 2, noteH, r);
      ctx.fill();

      // Contact glow (目前發聲中，音塊壓中底線)
      if (yBottom >= h && yTop <= h) {
         ctx.save();
         ctx.fillStyle = color;
         ctx.shadowColor = color;
         ctx.shadowBlur = 10;
         ctx.fillRect(x + 1, h - 4, kw - 2, 8);
         ctx.fillStyle = "rgba(255,255,255,0.8)";
         ctx.shadowBlur = 5;
         ctx.shadowColor = "#fff";
         ctx.fillRect(x + 3, h - 2, kw - 6, 4);
         ctx.restore();
      }

      // Show finger numbers for all mapped notes
      if (evt.finger) {
        let showF = false; // 先擱置指法，不要顯示
        const lastF = isLeft ? lhLastF : rhLastF;
        
        if (showF) {
          // Display the number near the bottom of the falling block so it appears early
          const fy = Math.min(yBottom - 10, yTop + noteH / 2 + 5);
          ctx.font = "bold 13px sans-serif";
          
          let text = evt.finger;
          
          if (evt.finger === 1 && lastF > 1) {
             // Crossover hint
             text = "↻1";
             ctx.fillStyle = "rgba(255,255,255,0.2)";
             ctx.fillRect(x, Math.max(0, yTop), kw, noteH);
          }
          
          // High contrast text
          ctx.lineWidth = 2.5;
          ctx.strokeStyle = "rgba(0,0,0,0.8)";
          ctx.strokeText(text, x + kw / 2, Math.max(fy, 15));
          ctx.fillStyle = "#fff";
          ctx.fillText(text, x + kw / 2, Math.max(fy, 15));
        }
        
        if (isLeft) lhLastF = evt.finger; else rhLastF = evt.finger;
      }
    }

    // Landing line
    ctx.strokeStyle = "rgba(255,255,255,0.4)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(0, h - 2);
    ctx.lineTo(w, h - 2);
    ctx.stroke();

    // ---- HUD Overlay (AI Teach Hint & Current Chord) ----
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    
    if (teachStyle) {
      let hintText = "";
      if (teachStyle === "Arpeggio") hintText = "Flow 分解伴奏推進中... 留意樂句波動";
      else if (teachStyle === "Block") hintText = "Block 模式：雙手對齊抓穩柱狀和弦";
      else if (teachStyle === "Rhythm") hintText = "Rhythm 模式：強調第一拍與切分節奏";
      else hintText = "大師模式：" + teachStyle;

      ctx.font = "12px sans-serif";
      const txt = `🎵 AI Hint: ${hintText}`;
      const textMetrics = ctx.measureText(txt);
      const tw = textMetrics.width;

      ctx.fillStyle = "rgba(0,0,0,0.5)";
      ctx.beginPath();
      ctx.roundRect(22, 16, tw + 16, 24, 6);
      ctx.fill();

      ctx.fillStyle = "rgba(255,255,255,0.7)";
      ctx.textAlign = "left";
      ctx.textBaseline = "top";
      ctx.fillText(txt, 30, 21);
    }

  }

  // Teaching controls setup
  function _setupTeachControls() {
    const styleSelect = $("#teachStyle");
    const levelBtns = document.querySelectorAll("#levelSwitch .mode-btn");
    const aiBtn = $("#btnTeachAI");
    const toggle = $("#teachToggle");

    const handToggleBtns = document.querySelectorAll(".hand-toggle-btn");

    if (styleSelect) {
      styleSelect.value = teachStyle;
      styleSelect.addEventListener("change", () => {
        teachStyle = styleSelect.value;
        localStorage.setItem("livechord_teach_style", teachStyle);
        accData = null;
        if (waterfallActive) _loadAccompaniment();
      });
    }

    if (levelBtns.length) {
      levelBtns.forEach(btn => {
        if (btn.dataset.level === teachLevel) btn.classList.add("active");
        else btn.classList.remove("active");
        btn.addEventListener("click", () => {
          levelBtns.forEach(b => b.classList.remove("active"));
          btn.classList.add("active");
          teachLevel = btn.dataset.level;
          localStorage.setItem("livechord_teach_level", teachLevel);
          accData = null;
          if (waterfallActive) _loadAccompaniment();
        });
      });
    }
    
    if (handToggleBtns.length) {
      const updateHandToggleUI = () => {
        handToggleBtns.forEach(btn => {
          const lImg = btn.querySelector(".hand-left");
          const rImg = btn.querySelector(".hand-right");
          if (lImg && rImg) {
            lImg.classList.toggle("active", activeHand === "both" || activeHand === "left");
            rImg.classList.toggle("active", activeHand === "both" || activeHand === "right");
          }
          let title = "雙手";
          if (activeHand === "left") title = "僅左手";
          if (activeHand === "right") title = "僅右手";
          btn.title = `切換教學手勢 (${title})`;
        });
      };
      // Init UI state
      updateHandToggleUI();

      handToggleBtns.forEach(btn => {
        btn.addEventListener("click", () => {
          if (activeHand === "both") {
            activeHand = "left";
            showToast("手部切換: 顯示左手");
          } else if (activeHand === "left") {
            activeHand = "right";
            showToast("手部切換: 顯示右手");
          } else {
            activeHand = "both";
            showToast("手部切換: 顯示雙手");
          }
          localStorage.setItem("livechord_active_hand", activeHand);
          updateHandToggleUI();
        });
      });
    }

    if (aiBtn) {
      aiBtn.addEventListener("click", () => {
        if (!trackPath) return;
        fetch(`/api/ai/suggest-style?path=${encodeURIComponent(trackPath)}`)
          .then(r => r.json())
          .then(data => {
            if (data.suggested_styles && data.suggested_styles.length > 0) {
              const best = data.suggested_styles[0];
              if (styleSelect) styleSelect.value = best;
              teachStyle = best;
              localStorage.setItem("livechord_teach_style", best);
              accData = null;
              if (waterfallActive) _loadAccompaniment();
              showToast("AI: " + data.suggested_styles.join(", "));
            }
          }).catch(() => {});
      });
    }


    const btnShowChordTonesTB = $("#btnShowChordTonesTB");
    const btnBottomShowChordTones = $("#btnBottomShowChordTones");
    
    const updateChordTonesUI = () => {
      [btnShowChordTonesTB, btnBottomShowChordTones].forEach(btn => {
        if (!btn) return;
        if (show88ChordTones) {
          btn.style.background = "rgba(156, 39, 176, 0.2)";
          btn.style.textShadow = "0 0 10px rgba(156, 39, 176, 0.8)";
        } else {
          btn.style.background = "";
          btn.style.textShadow = "";
        }
      });
    };
    // Init state
    updateChordTonesUI();

    const handleChordTonesToggle = () => {
      show88ChordTones = !show88ChordTones;
      localStorage.setItem("livechord_show_chord_tones", show88ChordTones.toString());
      updateChordTonesUI();
      update88Piano(audio.currentTime || 0);
    };

    if (btnShowChordTonesTB) btnShowChordTonesTB.addEventListener("click", handleChordTonesToggle);
    if (btnBottomShowChordTones) btnBottomShowChordTones.addEventListener("click", handleChordTonesToggle);
  }

  // resize observer for 88-key piano
  if (chordDisplay88) {
    let _resizeTimer = null;
    new ResizeObserver(() => {
      clearTimeout(_resizeTimer);
      _resizeTimer = setTimeout(() => {
        if (activeTab === "keys") {
          _init88Piano();
          _resizeWaterfall();
          update88Piano(audio.currentTime || 0);
        }
      }, 100);
    }).observe(chordDisplay88);
  }

  // ---- section detection ----

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

          // 3. Piano88 ribbon — color bar + section label
          if (keys88RibbonTrack) {
            const k88items = keys88RibbonTrack.querySelectorAll(".keys88-ribbon-item");
            const kel = k88items[i];
            if (kel) {
              kel.style.setProperty("--sec-color", sec.color);
              kel.classList.add("has-section");
              if (i === 0 || displayed[i - 1].time < sec.start) {
                const kmarker = document.createElement("div");
                kmarker.className = "section-marker-ribbon";
                kmarker.textContent = `${sec.emoji} ${sec.label || sec.type}`;
                kmarker.style.color = sec.color;
                kel.appendChild(kmarker);
              }
            }
          }
        }
      }
    }
  }

  // ---- chord loading (自動偵測整合) ----

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
        if (activeTab === "diagrams") {
          bigChordBox.style.display = "none";
        } else {
          bigChordBox.style.display = "";
        }
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
    // rebuild 88-key ribbon (uses _ribbonPositions + chordCache)
    if (activeTab === "keys") _buildKeys88Ribbon();
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
      if (activeTab === "overview" && !audio.paused) {
        // Smart view: keep active chord in upper-center of chord area
        const container = chordDisplayOverview;
        const rect = el.getBoundingClientRect();
        const cRect = container.getBoundingClientRect();
        // getBoundingClientRect returns screen-space (zoomed) pixels;
        // scrollTop is in container (unzoomed) space — divide by scale
        const scale = _isFullscreen() ? (ZOOM_STEPS[zoomIdx] || 100) / 100 : 1;
        const targetY = cRect.top + 28 * scale; // offset for section markers above chord
        const diff = rect.top - targetY;
        if (Math.abs(diff) > 10 * scale) {
          container.scrollTop += diff / scale;
        }
      }

      if (ribbonElements[activeChordIdx]) {
        ribbonElements[activeChordIdx].classList.add("active");
      }

      // 更新大字顯示 — Diagrams 模式隱藏（避免重複）
      const chord = displayedChords[activeChordIdx];
      const cache = chordCache[chord.chord] || {};

      if (activeTab === "diagrams" || activeTab === "keys") {
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

  // Smart view: playing → chord area scrolls internally; paused → page scrolls freely
  function _setSmartView(playing) {
    if (chordDisplayOverview) {
      const isFs = chordDisplay && chordDisplay.classList.contains("fullscreen");
      if (playing) {
        chordDisplayOverview.style.overflowY = "auto";
        chordDisplayOverview.style.maxHeight = isFs ? "" : "calc(100vh - 320px)";
      } else {
        chordDisplayOverview.style.overflowY = "";
        chordDisplayOverview.style.maxHeight = "";
      }
    }
  }
  let _audioIsLoading = false;
  function _setAudioLoadingState(isLoading) {
    if (isLoading && !_audioIsLoading) {
      _audioIsLoading = true;
      _setLoadingState(true, "正努力串流中...", "請稍後，音訊緩衝中...", "music");
    } else if (!isLoading && _audioIsLoading) {
      _audioIsLoading = false;
      _setLoadingState(false);
    }
  }

  let _streamWatcherTimer = null;
  let _streamLastTime = -1;

  function _startStreamWatcher() {
    _stopStreamWatcher();
    _streamLastTime = audio.currentTime;
    _setAudioLoadingState(true);
    
    _streamWatcherTimer = setInterval(() => {
      if (audio.paused) {
        _stopStreamWatcher();
        return;
      }
      if (audio.currentTime === _streamLastTime) {
        _setAudioLoadingState(true);
      } else {
        _setAudioLoadingState(false);
        _streamLastTime = audio.currentTime;
      }
    }, 500);
  }

  function _stopStreamWatcher() {
    if (_streamWatcherTimer) clearInterval(_streamWatcherTimer);
    _streamWatcherTimer = null;
    _setAudioLoadingState(false);
  }

  audio.addEventListener("play", () => {
    btnPlay.innerHTML = "&#x23F8;"; 
    _setSmartView(true);
    _startStreamWatcher();
  });
  
  audio.addEventListener("pause", () => {
    btnPlay.innerHTML = "&#x25B6;"; 
    _setSmartView(false);
    _stopStreamWatcher();
  });

  audio.addEventListener("loadedmetadata", () => {
    timeDuration.textContent = formatTime(audio.duration);
    if(fsTimeDuration) fsTimeDuration.textContent = formatTime(audio.duration);
  });

  // requestAnimationFrame 同步
  let rafId = null;

  function tickSync() {
    if (!audio.paused) {
      let t = audio.currentTime;
      // A-B Repeat: loop back to A when reaching B
      if (abState === "active" && abA != null && abB != null && t >= abB) {
        audio.currentTime = abA;
        t = abA;
      }
      const d = audio.duration || 1;
      progress.style.width = (t / d * 100) + "%";
      if(fsProgress) fsProgress.style.width = progress.style.width;
      timeCurrent.textContent = formatTime(t);
      if(fsTimeCurrent) fsTimeCurrent.textContent = formatTime(t);
      updateActiveChord(t);
      if (activeTab === "keys") {
        update88Piano(t);
        drawWaterfall(t);
      }
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
    if(fsProgress) fsProgress.style.width = progress.style.width;
    timeCurrent.textContent = formatTime(t);
    if(fsTimeCurrent) fsTimeCurrent.textContent = formatTime(t);
    updateActiveChord(t);
    if (activeTab === "keys") {
      update88Piano(t);
      drawWaterfall(t);
    }
  });

  audio.addEventListener("seeked", () => {
    const t = audio.currentTime;
    progress.style.width = (t / (audio.duration || 1) * 100) + "%";
    if(fsProgress) fsProgress.style.width = progress.style.width;
    timeCurrent.textContent = formatTime(t);
    if(fsTimeCurrent) fsTimeCurrent.textContent = formatTime(t);
    updateActiveChord(t);
    if (activeTab === "keys") { piano88LastIdx = -1; update88Piano(t); }
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

  if (fsProgressBar) {
    let _draggingFsProgress = false;
    function _seekFromFsPointer(e) {
      const rect = fsProgressBar.getBoundingClientRect();
      const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      audio.currentTime = pct * (audio.duration || 0);
    }
    fsProgressBar.addEventListener("pointerdown", (e) => {
      _draggingFsProgress = true;
      fsProgressBar.setPointerCapture(e.pointerId);
      _seekFromFsPointer(e);
    });
    fsProgressBar.addEventListener("pointermove", (e) => {
      if (_draggingFsProgress) _seekFromFsPointer(e);
    });
    fsProgressBar.addEventListener("pointerup", () => { _draggingFsProgress = false; });
    fsProgressBar.addEventListener("pointercancel", () => { _draggingFsProgress = false; });
  }

  // 還原上次音量
  const savedVol = localStorage.getItem("livechord_volume");
  if (savedVol !== null) {
    const v = parseFloat(savedVol);
    audio.volume = v;
    volumeSlider.value = v;
  }

  volumeSlider.addEventListener("input", () => {
    audio.volume = parseFloat(volumeSlider.value);
    audio.muted = false;
    localStorage.setItem("livechord_volume", volumeSlider.value);
    btnMute.innerHTML = audio.volume === 0 ? "&#x1F507;" : "&#x1F509;";
  });

  // Mute toggle
  const btnMute = $("#btnMute");
  let _preMuteVol = 1;
  if (btnMute) {
    btnMute.addEventListener("click", () => {
      audio.muted = !audio.muted;
      btnMute.innerHTML = audio.muted ? "&#x1F507;" : "&#x1F509;";
    });
  }

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

          if (exactMatch) {
            detectMsg.textContent = "MIDI 匯入中…";
            detectDetail.textContent = exactMatch.name;
            const result = await API.midiImport(trackPath, exactMatch.path);
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
          // 無精確匹配 → 不自動匯入模糊結果，改用 BTC 偵測
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

  // ---- hand switch (88-key mode) ----
  const handSwitch = $("#handSwitch");
  const modeSwitch = $("#modeSwitch");

  if (handSwitch) {
    document.querySelectorAll("#handSwitch .mode-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll("#handSwitch .mode-btn").forEach((b) => {
          b.style.background = "transparent";
          b.style.color = "var(--text-dim)";
          b.classList.remove("active");
        });
        btn.style.background = "var(--accent)";
        btn.style.color = "#fff";
        btn.classList.add("active");
        piano88Hand = btn.dataset.hand;
        localStorage.setItem("livechord_88hand", piano88Hand);
        piano88LastIdx = -1; // force redraw
        update88Piano(audio.currentTime || 0);
      });
    });
    // restore saved hand selection
    const savedBtn = handSwitch.querySelector(`[data-hand="${piano88Hand}"]`);
    if (savedBtn) savedBtn.click();
  }

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
