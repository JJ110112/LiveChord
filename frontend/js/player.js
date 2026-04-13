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
  let showFingering = localStorage.getItem("livechord_show_fingering") !== "false"; // 預設開啟
  let teachStyle = localStorage.getItem("livechord_teach_style") || "Auto";
  let teachLevel = localStorage.getItem("livechord_teach_level") || "L1";
  if (!["L1", "L2", "L3"].includes(teachLevel)) teachLevel = "L1";
  let accData = null;  // {left_hand:[], right_hand:[]} from API
  let _beatPhase = 0;  // beat grid phase offset (seconds)
  let accLoading = false;
  let transpose = 0;
  let capo = 0;
  let favTracks = [];
  let activeLoadingTasks = 0;
  let loadingDelayTimer = null;

  // ---- DOM ----
  const audio = $("#audio");
  const songTitle = $("#songTitle");
  const btnPlay = $("#btnPlay");
  const btnFav = $("#btnFav");
  const topProgressBar = $("#topProgress");
  const topProgressFill = $("#topProgressFill");
  const topAbRange = $("#topAbRange");
  const timeCurrent = $("#timeCurrent");
  const timeDuration = $("#timeDuration");
  const volumeSlider = $("#volumeSlider");
  const chordDisplayPiano = $("#chordDisplayPiano");
  const pianoWaterfallView = $("#pianoWaterfallView");
  const chordDisplayGuitar = $("#chordDisplayGuitar");
  const chordDisplayUkulele = $("#chordDisplayUkulele");
  const detectOverlay = $("#detectOverlay");
  const detectMsg = $("#detectMsg");
  const detectDetail = $("#detectDetail");
  const capoGroup = $("#capoGroup");
  const chordRibbonPanel = $("#chordRibbonPanel");
  const unifiedRibbonTrack = $("#unifiedRibbonTrack");
  const resizeHandle = $("#resizeHandle");
  const instrumentPanel = $("#instrumentPanel");

  // Marquee scrolling for overflowing song title
  function _checkMarquee(el) {
    if (!el) return;
    el.classList.remove("marquee");
    // Unwrap existing marquee-inner
    const existing = el.querySelector(".marquee-inner");
    if (existing) el.textContent = existing.firstChild.textContent;
    // Check if text overflows (double RAF ensures layout is settled)
    requestAnimationFrame(() => { requestAnimationFrame(() => {
      // Temporarily set overflow:auto — some browsers report
      // scrollWidth === clientWidth when overflow:hidden is active
      el.style.overflow = "auto";
      const overflows = el.scrollWidth > el.clientWidth;
      el.style.overflow = "";
      if (overflows) {
        const text = el.textContent;
        el.textContent = "";
        // Two copies for seamless loop: [text   text   ] scrolls -50%
        const span = document.createElement("span");
        span.className = "marquee-inner";
        span.textContent = text;
        const sep = document.createTextNode("   \u00A0\u00A0\u00A0   ");
        span.appendChild(sep);
        const copy = document.createTextNode(text);
        span.appendChild(copy);
        el.appendChild(span);
        el.classList.add("marquee");
        // Duration proportional to text length (~ 50px/sec)
        const dur = Math.max(8, span.scrollWidth / 50);
        el.style.setProperty("--marquee-dur", dur + "s");
      }
    }); });
  }

  function _updateCapoVisibility() {
    if (capoGroup) {
      capoGroup.style.display = InstrumentRegistry.isStringInstrument(activeTab) ? "" : "none";
    }
  }

  let activeTab = localStorage.getItem("livechord_tab") || "piano";
  if (activeTab === "overview") activeTab = "piano"; // overview removed
  let pianoSubmode = "waterfall"; // always waterfall
  let ribbonElements = []; // unified vertical ribbon elements
  const pxPerSec = 100;
  const chordDisplay88 = pianoWaterfallView;

  // ---- A-B Repeat state ----
  const btnABRepeat = $("#btnABRepeat");
  let abState = "idle";  // idle → a_set → active
  let abA = null;        // start time (seconds)
  let abB = null;        // end time (seconds)

  function _updateABRangeUI() {
    const d = audio.duration || 1;
    if (abState === "active" && abA != null && abB != null) {
      const left = (abA / d * 100) + "%";
      const width = ((abB - abA) / d * 100) + "%";
      if (topAbRange) {
        topAbRange.style.display = "block";
        topAbRange.style.left = left;
        topAbRange.style.width = width;
      }
    } else if (abState === "a_set" && abA != null) {
      const left = (abA / d * 100) + "%";
      if (topAbRange) {
        topAbRange.style.display = "block";
        topAbRange.style.left = left;
        topAbRange.style.width = "2px";
      }
    } else {
      if (topAbRange) topAbRange.style.display = "none";
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
    const topHs = document.querySelector("#btnTopHandSwitch");
    const isPiano = activeTab === "piano";
    if (topHs) topHs.style.display = isPiano ? "flex" : "none";
  }

  function _setAllTabsInactive() {
    if (chordDisplayPiano) chordDisplayPiano.style.display = "none";
    if (chordDisplayGuitar) chordDisplayGuitar.style.display = "none";
    if (chordDisplayUkulele) chordDisplayUkulele.style.display = "none";
    const chordDisplayAccordion = $("#chordDisplayAccordion");
    if (chordDisplayAccordion) chordDisplayAccordion.style.display = "none";
    const chordDisplayArranger = $("#chordDisplayArranger");
    if (chordDisplayArranger) chordDisplayArranger.style.display = "none";
    // Update instrument button icon
    document.querySelectorAll("#tbInstrument .tb-popup-btn").forEach(b => b.classList.remove("active"));
  }

  const chordDisplayEl = $("#chordDisplay");

  // --- Main tab switching ---
  function _switchTab(tab) {
    activeTab = tab;
    localStorage.setItem("livechord_tab", tab);
    _setAllTabsInactive();

    // Set displayMode based on tab
    if (tab === "guitar") displayMode = "guitar";
    else if (tab === "ukulele") displayMode = "ukulele";
    else if (tab === "accordion") displayMode = "accordion";
    else if (tab === "arranger") displayMode = "arranger";
    else displayMode = "piano";

    // Update instrument trigger icon
    const iconMap = { piano: "\u{1F3B9}", guitar: "\u{1F3B8}", ukulele: "\u{1FA95}", accordion: "\u{1FA97}", arranger: "\u{1F3B9}" };
    const btnInstrument = $("#btnInstrument");
    if (btnInstrument) btnInstrument.textContent = iconMap[tab] || "\u2328";
    // Highlight active in popup
    const activeBtn = document.querySelector(`#tbInstrument .tb-popup-btn[data-tab="${tab}"]`);
    if (activeBtn) activeBtn.classList.add("active");

    if (tab === "piano") {
      if (chordDisplayPiano) chordDisplayPiano.style.display = "flex";
      _init88Piano();
      _initWaterfall();
      piano88LastIdx = -1;
      update88Piano(audio.currentTime || 0);
    } else {
      // String instruments via registry (guitar, ukulele, ...)
      const inst = InstrumentRegistry.get(tab);
      if (inst) {
        const container = $(inst._config.selectors.container);
        if (container) container.style.display = "flex";
        inst.init();
      }
    }
    _setupTeachControls();
    _updateCapoVisibility();
    _updateHandSwitchVisibility();
    if (typeof window._syncGuitarArpUI === "function") window._syncGuitarArpUI();

    // Load per-instrument scale and rebuild ribbon
    _loadRibbonScale();
    _buildUnifiedRibbon();
  }

  // Instrument tab switching from bottom toolbar popup
  document.querySelectorAll("#tbInstrument .tb-popup-btn").forEach(btn => {
    btn.addEventListener("click", () => _switchTab(btn.dataset.tab));
  });

  // Tab restore deferred until after scale declarations (see below)

  // ---- rewind to start (or A-B loop start) ----
  function _rewindToStart() {
    audio.currentTime = (abState === "active" && abA != null) ? abA : 0;
  }

  // ---- Unified Ribbon Builder (vertical, piano-style, replaces overview) ----
  function _buildUnifiedRibbon() {
    if (!unifiedRibbonTrack) return;
    unifiedRibbonTrack.innerHTML = "";
    ribbonElements = [];

    const chords = _displayChords();
    if (!chords || chords.length === 0) return;

    // Build in reverse order: last chord at top, first chord at bottom
    // This matches the waterfall direction (time flows top→bottom)
    let lastSection = null;
    let _prevMidi = null;

    // First pass: collect items in normal order for _prevMidi voice leading
    const items = [];
    for (let i = 0; i < chords.length; i++) {
      const c = chords[i];
      const cache = chordCache[c.chord] || {};

      // Section header & Phrase Colors
      let sectionHdr = null;
      let phraseColor = 'var(--accent)';
      let isPhraseStart = false;
      let phraseLabelZh = '';
      let phraseLabelEn = '';

      if (sectionData && sectionData.sections) {
        // Robust matching: find the section this chord belongs to (with 0.5s tolerance to catch chords playing slightly early)
        const activeSec = sectionData.sections.find(s => c.time >= s.start - 0.5 && c.time <= s.end);
        if (activeSec) {
          phraseColor = activeSec.color || '#888';
          // Check if this is the first chord we see for this specific section block timestamp
          if (activeSec.start !== lastSection) {
            lastSection = activeSec.start;
            const modeTag = activeSec.mode && activeSec.mode !== "Major" && activeSec.mode !== "Minor" ? ` ${activeSec.mode}` : "";
            
            const baseType = activeSec.type.replace(/\d+|'/g, ''); 
            const enType = baseType.charAt(0).toUpperCase() + baseType.slice(1).replace('_', ' ');
            
            phraseLabelZh = activeSec.label + modeTag;
            phraseLabelEn = enType + modeTag;
            
            sectionHdr = { labelZh: phraseLabelZh, labelEn: phraseLabelEn, color: phraseColor };
            isPhraseStart = true;
          }
        }
      }

      const item = document.createElement("div");
      item.className = "rv-item";
      item.dataset.idx = i;
      item.dataset.time = c.time;
      item.style.setProperty('--chord-idx', i); // for flex order
      item.style.setProperty('--phrase-color', phraseColor);
      
      if (isPhraseStart) {
        item.dataset.phraseStart = "true";
        const gridPhraseEl = document.createElement("div");
        gridPhraseEl.className = "rv-grid-phrase";
        gridPhraseEl.dataset.zh = phraseLabelZh;
        gridPhraseEl.dataset.en = phraseLabelEn;
        gridPhraseEl.style.color = phraseColor;
        
        // Initial setup
        const curLang = window.liveChordPhraseLang || 'zh';
        gridPhraseEl.textContent = curLang === 'zh' ? phraseLabelZh : phraseLabelEn;
        
        // Toggle language function
        gridPhraseEl.addEventListener("click", (e) => {
          e.stopPropagation();
          const nextLang = (window.liveChordPhraseLang || 'zh') === 'zh' ? 'en' : 'zh';
          window.liveChordPhraseLang = nextLang;
          document.querySelectorAll(".rv-grid-phrase, .rv-section-text").forEach(el => {
            el.textContent = el.dataset[nextLang];
          });
        });
        item.appendChild(gridPhraseEl);
      }
      
      item.addEventListener("click", () => {
        audio.currentTime = c.time;
        updateActiveChord(c.time);
      });

      const nameEl = document.createElement("div");
      nameEl.className = "rv-chord-name";
      nameEl.textContent = c.chord;
      item.appendChild(nameEl);

      const jp = document.createElement("div");
      jp.className = "rv-jianpu";
      jp.innerHTML = ChordRender.jianpuToHtml(_notesToJianpu(cache.notes, _currentKey()));
      item.appendChild(jp);

      const _diagKey = `diagram_${activeTab}`;
      if (InstrumentRegistry.isStringInstrument(activeTab) && cache[_diagKey]) {
        const canvas = document.createElement("canvas");
        ChordRender.drawDiagram(canvas, cache[_diagKey], ribbonScale);
        item.appendChild(canvas);
      } else {
        const pianoCanvas = document.createElement("canvas");
        ChordRender.drawPiano(pianoCanvas, cache.notes || [], ribbonScale, _prevMidi);
        item.appendChild(pianoCanvas);
        if (pianoCanvas._lastMidi) _prevMidi = pianoCanvas._lastMidi;
      }

      const timeEl = document.createElement("div");
      timeEl.className = "rv-time";
      timeEl.textContent = formatTime(c.time);
      item.appendChild(timeEl);

      items.push({ item, sectionHdr, idx: i });
    }

    // Append in reverse: last chord first (top), first chord last (bottom)
    for (let j = items.length - 1; j >= 0; j--) {
      const { item, sectionHdr } = items[j];
      if (sectionHdr) {
        const hdr = document.createElement("div");
        hdr.className = "rv-section-header";
        
        const curLang = window.liveChordPhraseLang || 'zh';
        const txt = curLang === 'zh' ? sectionHdr.labelZh : sectionHdr.labelEn;
        hdr.innerHTML = `<span class="rv-section-dot" style="background:${sectionHdr.color}"></span><span class="rv-section-text" data-zh="${sectionHdr.labelZh}" data-en="${sectionHdr.labelEn}">${txt}</span>`;
        
        hdr.style.cursor = "pointer";
        hdr.addEventListener("click", (e) => {
          const nextLang = (window.liveChordPhraseLang || 'zh') === 'zh' ? 'en' : 'zh';
          window.liveChordPhraseLang = nextLang;
          document.querySelectorAll(".rv-grid-phrase, .rv-section-text").forEach(el => {
            el.textContent = el.dataset[nextLang];
          });
        });
        
        unifiedRibbonTrack.appendChild(hdr);
      }
      unifiedRibbonTrack.appendChild(item);
    }

    // ribbonElements keeps normal index order for updateActiveChord
    ribbonElements = items.map(it => it.item);

    // Scroll to bottom only on fresh load (first chord = song start).
    // When rebuilding mid-song (e.g. AI Transformer toggle, transpose change),
    // leave positioning to the caller via updateActiveChord(..., forceScroll=true).
    if (chordRibbonPanel) {
      const isFreshLoad = activeChordIdx < 0 && (!audio || !audio.currentTime || audio.currentTime < 0.1);
      if (isFreshLoad) {
        requestAnimationFrame(() => {
          chordRibbonPanel.scrollTop = chordRibbonPanel.scrollHeight;
        });
      }
    }
  }

  // ---- Resize Handle (drag to resize chord ribbon panel) ----
  if (resizeHandle && chordRibbonPanel) {
    let _resizing = false;
    let _startX = 0;
    let _startW = 0;

    resizeHandle.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".ribbon-toggle-btn")) return; // let button handle it
      _resizing = true;
      _startX = e.clientX;
      _startW = chordRibbonPanel.offsetWidth;
      resizeHandle.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    resizeHandle.addEventListener("pointermove", (e) => {
      if (!_resizing) return;
      const delta = e.clientX - _startX;
      const newW = Math.max(180, Math.min(window.innerWidth * 0.4, _startW + delta));
      chordRibbonPanel.style.width = newW + "px";
    });
    resizeHandle.addEventListener("pointerup", () => {
      _resizing = false;
      localStorage.setItem("livechord_ribbon_width", chordRibbonPanel.offsetWidth);
    });
    resizeHandle.addEventListener("pointercancel", () => { _resizing = false; });

    // Restore saved width
    const savedRibbonW = parseInt(localStorage.getItem("livechord_ribbon_width"));
    if (savedRibbonW > 0) chordRibbonPanel.style.width = savedRibbonW + "px";
  }

  // ---- Ribbon diagram scale (+/−) ----
  const _ribbonScales = {};
  for (const t of ["piano", "guitar", "ukulele"]) {
    const v = parseFloat(localStorage.getItem(`livechord_ribbon_scale_${t}`));
    _ribbonScales[t] = (v >= 1) ? v : 1.0;
  }
  let ribbonScale = _ribbonScales[activeTab] || 1.0;
  const scaleLabel = $("#scaleLabel");

  function _loadRibbonScale() {
    ribbonScale = _ribbonScales[activeTab] || 1.0;
    _updateScaleLabel();
  }
  function _updateScaleLabel() {
    if (scaleLabel) scaleLabel.textContent = ribbonScale.toFixed(1);
  }
  function _changeRibbonScale(delta) {
    ribbonScale = Math.round(Math.max(1, Math.min(3, ribbonScale + delta)) * 10) / 10;
    _ribbonScales[activeTab] = ribbonScale;
    localStorage.setItem(`livechord_ribbon_scale_${activeTab}`, ribbonScale);
    _updateScaleLabel();
    _buildUnifiedRibbon();
    updateActiveChord(audio.currentTime || 0);
  }
  const btnScaleUp = $("#btnScaleUp");
  const btnScaleDown = $("#btnScaleDown");
  if (btnScaleUp) btnScaleUp.addEventListener("click", () => _changeRibbonScale(0.1));
  if (btnScaleDown) btnScaleDown.addEventListener("click", () => _changeRibbonScale(-0.1));
  _updateScaleLabel();

  // ---- Overview Mode Toggle ----
  const btnToggleOverview = $("#btnToggleOverview");
  let isOverviewMode = false;
  if (btnToggleOverview) {
    btnToggleOverview.addEventListener("click", (e) => {
      e.stopPropagation();
      isOverviewMode = !isOverviewMode;
      btnToggleOverview.classList.toggle("active", isOverviewMode);
      if (chordRibbonPanel) {
        if (isOverviewMode) {
          chordRibbonPanel.classList.add("overview-mode");
        } else {
          chordRibbonPanel.classList.remove("overview-mode");
        }
      }
      // Scroll to current chord immediately on toggle
      updateActiveChord(audio.currentTime || 0);
    });
  }

  // Restore last tab (deferred here so _ribbonScales is initialized)
  _switchTab(activeTab);

  // ---- Toggle chord ribbon visibility ----
  const btnToggleRibbon = $("#btnToggleRibbon");
  let ribbonVisible = localStorage.getItem("livechord_ribbon_visible") !== "false";

  function _applyRibbonVisibility() {
    if (chordRibbonPanel) chordRibbonPanel.style.display = ribbonVisible ? "" : "none";
    if (resizeHandle) resizeHandle.style.width = ribbonVisible ? "" : "22px";
    if (btnToggleRibbon) btnToggleRibbon.innerHTML = ribbonVisible ? "&#x276E;" : "&#x276F;";
  }
  _applyRibbonVisibility();

  if (btnToggleRibbon) {
    btnToggleRibbon.addEventListener("click", (e) => {
      e.stopPropagation();
      ribbonVisible = !ribbonVisible;
      localStorage.setItem("livechord_ribbon_visible", ribbonVisible);
      _applyRibbonVisibility();
    });
  }

  // ---- Top progress bar seek ----
  if (topProgressBar) {
    let _draggingTop = false;
    function _seekFromTopProgress(e) {
      const rect = topProgressBar.getBoundingClientRect();
      const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      audio.currentTime = pct * (audio.duration || 0);
    }
    topProgressBar.addEventListener("pointerdown", (e) => {
      _draggingTop = true;
      topProgressBar.setPointerCapture(e.pointerId);
      _seekFromTopProgress(e);
    });
    topProgressBar.addEventListener("pointermove", (e) => {
      if (_draggingTop) _seekFromTopProgress(e);
    });
    topProgressBar.addEventListener("pointerup", () => { _draggingTop = false; });
    topProgressBar.addEventListener("pointercancel", () => { _draggingTop = false; });
  }

  // ---- Title click-to-search ----
  const topbarSearch = $("#topbarSearch");
  if (songTitle && topbarSearch) {
    songTitle.addEventListener("click", () => {
      songTitle.style.display = "none";
      topbarSearch.style.display = "";
      const input = topbarSearch.querySelector("input");
      if (input) { input.value = ""; input.focus(); }
    });
    // Close search on blur or Escape
    const searchInput = topbarSearch.querySelector("input");
    if (searchInput) {
      searchInput.addEventListener("blur", () => {
        setTimeout(() => {
          topbarSearch.style.display = "none";
          songTitle.style.display = "";
        }, 200);
      });
      searchInput.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
          topbarSearch.style.display = "none";
          songTitle.style.display = "";
        }
      });
    }
  }

  // ---- Touch fallback for hover popups ----
  if ('ontouchstart' in window) {
    document.querySelectorAll(".tb-item").forEach(item => {
      if (!item.querySelector(".tb-popup")) return;
      const trigger = item.querySelector(".tb-trigger");
      if (trigger) {
        trigger.addEventListener("click", (e) => {
          // Close other open popups
          document.querySelectorAll(".tb-item.open").forEach(other => {
            if (other !== item) other.classList.remove("open");
          });
          item.classList.toggle("open");
          e.stopPropagation();
        });
      }
    });
    document.addEventListener("click", () => {
      document.querySelectorAll(".tb-item.open").forEach(i => i.classList.remove("open"));
    });
  }
  function _navUrl(path) {
    const fs = document.fullscreenElement ? "&fs=1" : "";
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
  // ---- 全螢幕（預設沉浸式，btnPageFs 控制瀏覽器原生全螢幕）----
  const chordDisplay = $("#chordDisplay");
  const btnPageFs = $("#btnPageFs");

  // Always immersive - hide body overflow
  document.body.style.overflow = "hidden";

  if (btnPageFs) {
    btnPageFs.onclick = () => {
      if (document.fullscreenElement) {
        document.exitFullscreen().catch(() => {});
        btnPageFs.innerHTML = "&#x26F6;";
      } else {
        document.documentElement.requestFullscreen().catch(() => {});
        btnPageFs.innerHTML = "&#x2716;";
      }
    };
  }
  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement) {
      if (btnPageFs) btnPageFs.innerHTML = "&#x26F6;";
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

  // showToast moved to utils.js

  // ===========================================================================
  // 一、載入 track：播放時若無和弦譜，自動偵測
  // ===========================================================================

  async function loadTrack(path) {
    _clearABRepeat();
    _setLoadingState(true, "載入樂曲中...", "正在讀取歌曲資訊與和弦編排...");
    try {
      audio.src = API.trackStreamUrl(path);

      let _trackArtist = "";
      try {
        const info = await API.trackInfo(path);
        const title = info.title || path.split("/").pop().replace(/\.flac$/i, "");
        _trackArtist = info.artist || "";
        const escTitle = title.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        songTitle.innerHTML = escTitle;
        songTitle.title = _trackArtist ? `${title} — ${_trackArtist}` : title;
        document.title = `${title} — LiveChord`;
        _checkMarquee(songTitle);
      } catch {
        const title = path.split("/").pop().replace(/\.flac$/i, "");
        songTitle.textContent = title;
        _checkMarquee(songTitle);
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
    const container = pianoWaterfallView || chordDisplay88;
    const w = (container && container.clientWidth) || 800;
    const containerH = (container && container.clientHeight) || 400;
    const maxKeyH = Math.max(80, containerH - 40);
    return Math.min(w, Math.round(maxKeyH / 5.1 * 52));  // 15% shorter keys
  }

  function _init88Piano() {
    if (!pianoWaterfallView) return;
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

        // big chord box removed — info is in unified ribbon
      } else {
        piano88ChordMidis = [];
      }
    }

    // prune old sustain notes
    piano88SustainNotes = piano88SustainNotes.filter(n => currentTime - n.release < 0.5);

    // determine actual played notes + fingering from accData
    // fingeringMap includes lookahead: current + next 1s of notes
    let activeLh = [];
    let activeRh = [];
    let fingeringMap = {};  // midi -> {finger, hand, upcoming}
    const FINGER_LOOKAHEAD = 1.0; // show fingering 1s ahead
    if (waterfallActive && accData) {
       for (const e of (accData.left_hand||[])) {
           const playing = e.time <= currentTime && e.time + e.duration >= currentTime;
           const upcoming = !playing && e.time > currentTime && e.time <= currentTime + FINGER_LOOKAHEAD;
           if (playing) activeLh.push(e.pitch);
           if ((playing || upcoming) && e.finger) {
               // Don't overwrite a currently-playing finger with an upcoming one
               if (!fingeringMap[e.pitch] || playing) {
                   fingeringMap[e.pitch] = { 
                       finger: e.finger, 
                       hand: "left", 
                       upcoming: !playing,
                       crossFromPitch: e.crossFromPitch
                   };
               }
           }
       }
       for (const e of (accData.right_hand||[])) {
           const playing = e.time <= currentTime && e.time + e.duration >= currentTime;
           const upcoming = !playing && e.time > currentTime && e.time <= currentTime + FINGER_LOOKAHEAD;
           if (playing) activeRh.push(e.pitch);
           if ((playing || upcoming) && e.finger) {
               if (!fingeringMap[e.pitch] || playing) {
                   fingeringMap[e.pitch] = { 
                       finger: e.finger, 
                       hand: "right", 
                       upcoming: !playing,
                       crossFromPitch: e.crossFromPitch
                   };
               }
           }
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

    // Phase 11: pedal state
    let pedalActive = false;
    let pedalDepth = 0;
    if (accData && accData.pedal) {
      for (const p of accData.pedal) {
        if (p.start <= currentTime && p.end > currentTime) {
          pedalActive = true;
          pedalDepth = p.depth || 1.0;
          break;
        }
      }
    }

    ChordRender.draw88Piano(piano88Canvas, piano88Cache, activeLh, activeRh, {
      chordTones: chordTones,
      sustainNotes: activeHand === "right" ? [] : piano88SustainNotes,
      now: currentTime,
      fingeringMap: showFingering ? fingeringMap : null,
      pedalActive: pedalActive,
      pedalDepth: pedalDepth,
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

  function _detectCrossings(events, hand) {
    if (!events || events.length === 0) return;
    const sortedEvents = [...events].sort((a, b) => a.time - b.time);
    let lastTime = -1;
    let prevNotes = [];
    let currNotes = [];
    for (const evt of sortedEvents) {
        if (!evt.finger) continue;
        // 使用 0.05 秒的容差將同時間撥出的和弦音群組在一起，避免自己跟自己 cross
        if (evt.time > lastTime + 0.05) {
            // 如果與上一個音的間隔超過 0.8 秒，代表樂句已經結束，手已經抬起，故清空 prevNotes
            if (lastTime !== -1 && evt.time - lastTime > 0.8) {
                prevNotes = [];
            } else {
                prevNotes = currNotes;
            }
            currNotes = [evt];
            lastTime = evt.time;
        } else {
            currNotes.push(evt);
        }
        
        if (prevNotes.length > 0) {
            for (const pEvt of prevNotes) {
                const pDiff = evt.pitch - pEvt.pitch;
                const fDiff = evt.finger - pEvt.finger;
                if (pDiff !== 0 && fDiff !== 0) {
                    const isCross = pDiff * fDiff * (hand === "right" ? 1 : -1) < 0;
                    // Detect thumb tuck or finger crossing over thumb
                    if (isCross) {
                        evt.crossFromPitch = pEvt.pitch;
                        break;
                    }
                }
            }
        }
    }
  }

  function _loadAccompaniment(forceRefresh) {
    if (!trackPath || accLoading) return;
    if (!forceRefresh && accData && accData._style === teachStyle && accData._level === teachLevel) return;
    accLoading = true;
    _setLoadingState(true, forceRefresh ? "AI 伴奏重新生成中..." : "AI 伴奏提取中...",
                     forceRefresh ? "清除快取並重新演算（含踏板/力度）..." : "首次播放需要進行即時演算...");
    let url = `/api/ai/accompaniment?path=${encodeURIComponent(trackPath)}&style=${teachStyle}&level=${teachLevel}`;
    if (forceRefresh) url += "&nocache=1";
    fetch(url).then(r => r.json()).then(data => {
      if (data.error) {
        console.warn("Accompaniment:", data.error);
        accData = null;
      } else {
        data._style = teachStyle;
        data._level = teachLevel;
        _detectCrossings(data.left_hand, "left");
        _detectCrossings(data.right_hand, "right");
        accData = data;
        // Compute beat phase offset from first note time
        const _allNotes = [...(data.left_hand || []), ...(data.right_hand || [])];
        if (_allNotes.length > 0) {
          const firstTime = Math.min(..._allNotes.map(n => n.time));
          const spb = 60 / (data.bpm || 100);
          _beatPhase = firstTime % spb;
        } else {
          _beatPhase = 0;
        }
        if (forceRefresh) {
          const pedalCount = (data.pedal || []).length;
          const hasVel = (data.left_hand || []).some(e => e.velocity);
          console.log(`[Refresh] pedal=${pedalCount}, velocity=${hasVel}`);
        }
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

  // AI Teacher HUD state (must be before drawWaterfall)
  let _teacherMsgCache = "";
  var _teacherMsgTime = 0;

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
    // Per-key-type shades for white/black key visibility
    const LH_WHITE = "rgba(100, 181, 246, 0.9)";  // lighter blue for white-key notes
    const LH_BLACK = "rgba(30, 136, 229, 0.9)";   // deeper blue for black-key notes
    const RH_WHITE = "rgba(255, 183, 77, 0.9)";   // lighter orange for white-key notes
    const RH_BLACK = "rgba(245, 124, 0, 0.9)";    // deeper orange for black-key notes

    // 畫拍線網格 — chord-based grid (aligned to actual note positions)
    const _gridChords = _displayChords();
    ctx.textAlign = "left";
    ctx.textBaseline = "bottom";
    ctx.font = "11px sans-serif";
    if (_gridChords && _gridChords.length > 0) {
      for (let ci = 0; ci < _gridChords.length; ci++) {
        const gc = _gridChords[ci];
        const gcEnd = (ci + 1 < _gridChords.length) ? _gridChords[ci + 1].time : gc.time + 4;
        const gcDur = gcEnd - gc.time;
        for (let b = 0; b < 4; b++) {
          const bt = gc.time + (b / 4) * gcDur;
          if (bt < currentTime - 0.1 || bt > currentTime + lookAhead) continue;
          const y = h - (bt - currentTime) * pxPerSec;
          if (y < 0 || y > h) continue;
          const isBarLine = (b === 0);
          ctx.strokeStyle = isBarLine ? "rgba(255,255,255,0.25)" : "rgba(255,255,255,0.08)";
          ctx.lineWidth = isBarLine ? 2 : 1;
          ctx.beginPath();
          ctx.moveTo(0, y);
          ctx.lineTo(w, y);
          ctx.stroke();
          // Beat number at left edge
          ctx.fillStyle = isBarLine ? "rgba(255,255,255,0.6)" : "rgba(255,255,255,0.3)";
          ctx.fillText(b + 1, 8, y - 2);
        }
      }
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
      const isOnBlackKey = !!cache.blackXs[midi];
      // Phase 11: velocity → 0~1 using actual data range (typ. 55~95)
      // Remap so min velocity in data → 0, max → 1 (aggressive contrast)
      const vel = evt.velocity || 80;
      const velT = Math.min(1.0, Math.max(0.0, (vel - 55) / 40)); // 55→0, 95→1
      // Apply power curve for more dramatic contrast (dim stays dim, bright pops)
      const velP = velT * velT; // quadratic: 0.5→0.25, 0.8→0.64, 1.0→1.0
      let color, glowColor;
      if (isLeft) {
        // Blue: pp=very dark navy → ff=electric blue
        const cr = Math.round(10 + velP * 100);
        const cg = Math.round(40 + velP * 160);
        const cb = Math.round(100 + velP * 155);
        color = `rgba(${cr}, ${cg}, ${cb}, ${isOnBlackKey ? 0.95 : 0.9})`;
        glowColor = `rgba(${Math.min(255, cr+80)}, ${Math.min(255, cg+60)}, 255, 1)`;
      } else {
        // Orange: pp=dark brown → ff=blazing orange-yellow
        const cr = Math.round(100 + velP * 155);
        const cg = Math.round(40 + velP * 170);
        const cb = Math.round(0 + velP * 50);
        color = `rgba(${cr}, ${cg}, ${cb}, ${isOnBlackKey ? 0.95 : 0.9})`;
        glowColor = `rgba(255, ${Math.min(255, cg+60)}, ${Math.min(255, cb+80)}, 1)`;
      }
      const glow = isLeft ? LH_GLOW : RH_GLOW;

      // Drop prediction shadow on the keys if it's right about to hit
      if (yBottom > h - 40 && yBottom < h) {
        ctx.fillStyle = glow;
        ctx.fillRect(x, h - 5, kw, -20);
      }

      // Note bar + velocity glow
      const rr = Math.min(4, noteH / 2);
      ctx.save();
      if (velP > 0.15) {
        // 任何中等以上力度都有光暈
        ctx.shadowColor = glowColor;
        ctx.shadowBlur = Math.round(3 + velP * 25); // 3~28px
      }
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.roundRect(x + 1, yTop, kw - 2, noteH, rr);
      ctx.fill();
      // 強音 (velP > 0.5): 再疊一層加強發光
      if (velP > 0.5) {
        ctx.shadowBlur = Math.round(velP * 35);
        ctx.fill();
      }
      ctx.restore();

      // 弱音: 細邊框讓暗色方塊仍可辨識
      if (velP < 0.15) {
        ctx.strokeStyle = "rgba(255,255,255,0.2)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.roundRect(x + 1, yTop, kw - 2, noteH, rr);
        ctx.stroke();
      }

      // Contact glow (觸鍵瞬間) — 強音爆發光
      if (yBottom >= h && yTop <= h) {
         ctx.save();
         ctx.fillStyle = color;
         ctx.shadowColor = glowColor;
         ctx.shadowBlur = 8 + velP * 22;
         ctx.fillRect(x + 1, h - 4, kw - 2, 8);
         // 白光芯
         ctx.fillStyle = `rgba(255,255,255,${0.3 + velP * 0.6})`;
         ctx.shadowBlur = velP * 15;
         ctx.shadowColor = "#fff";
         ctx.fillRect(x + 3, h - 2, kw - 6, 4);
         ctx.restore();
      }

      // Phase 11: Articulation markers
      if (evt.articulation === "staccato") {
        // Staccato dot at bottom of note block
        ctx.fillStyle = "#fff";
        ctx.beginPath();
        ctx.arc(x + kw / 2, yBottom - 4, 2.5, 0, Math.PI * 2);
        ctx.fill();
      } else if (evt.articulation === "legato" && noteH > 12) {
        // Legato curve connecting to next note (subtle arc at top)
        ctx.strokeStyle = "rgba(255,255,255,0.3)";
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(x + kw / 2, yTop, kw * 0.4, Math.PI, 0);
        ctx.stroke();
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

    // ---- Phase 11: Pedal visualization ----
    if (accData.pedal && accData.pedal.length > 0) {
      for (const ped of accData.pedal) {
        const pedStart = ped.start;
        const pedEnd = ped.end;
        if (pedEnd < currentTime || pedStart > currentTime + lookAhead) continue;

        const yPedBottom = h - (pedStart - currentTime) * pxPerSec;
        const yPedTop = h - (pedEnd - currentTime) * pxPerSec;
        const depth = ped.depth || 1.0;
        const alpha = depth * 0.15;

        // Pedal sustain region (subtle green tint across full width)
        ctx.fillStyle = `rgba(76, 175, 80, ${alpha})`;
        ctx.fillRect(0, Math.max(0, yPedTop), w, Math.min(h, yPedBottom) - Math.max(0, yPedTop));

        // Pedal change marker (horizontal dashed line at pedal start)
        if (yPedBottom > 0 && yPedBottom < h) {
          ctx.strokeStyle = depth >= 1.0 ? "rgba(76, 175, 80, 0.5)" : "rgba(76, 175, 80, 0.3)";
          ctx.lineWidth = 1;
          ctx.setLineDash(depth >= 1.0 ? [] : [3, 3]);
          ctx.beginPath();
          ctx.moveTo(0, yPedBottom);
          ctx.lineTo(w, yPedBottom);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      }
    }

    // ---- Phase 11: Velocity opacity on note blocks ----
    // (Applied above via evt.velocity — opacity modulation already in color)

    // Landing line
    ctx.strokeStyle = "rgba(255,255,255,0.4)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(0, h - 2);
    ctx.lineTo(w, h - 2);
    ctx.stroke();

    // ---- AI Teacher HUD (bottom-left, context-aware) ----
    _drawAITeacherHUD(ctx, w, h, currentTime, allEvents, pxPerSec);

  }

  // ===========================================================================
  // AI Teacher HUD — 即時教學提示 (Phase 11)
  // ===========================================================================

  function _drawAITeacherHUD(ctx, w, h, currentTime, allEvents, pxPerSec) {
    // 收集當前正在演奏的音符
    const nowPlaying = allEvents.filter(e =>
      e.time <= currentTime && e.time + e.duration > currentTime
    );
    // 即將到來的音符 (0.5s 內)
    const upcoming = allEvents.filter(e =>
      e.time > currentTime && e.time <= currentTime + 0.5
    );

    // 每 1.5 秒更新一次訊息，避免閃爍
    if (currentTime - _teacherMsgTime > 1.5 || !_teacherMsgCache) {
      _teacherMsgCache = _generateTeacherMessage(currentTime, nowPlaying, upcoming);
      _teacherMsgTime = currentTime;
    }

    if (!_teacherMsgCache) return;

    // 繪製底部右側浮動提示框 (靠近右手區域)
    const padding = 10;
    ctx.font = "12px 'Segoe UI', sans-serif";
    const metrics = ctx.measureText(_teacherMsgCache);
    const boxW = Math.min(metrics.width + padding * 2 + 20, w * 0.6);
    const boxH = 28;
    const boxX = w - boxW - 12;
    const boxY = h - 42;

    // 背景 — 半透明深色 pill
    ctx.fillStyle = "rgba(10, 10, 10, 0.65)";
    ctx.beginPath();
    ctx.roundRect(boxX, boxY, boxW, boxH, 14);
    ctx.fill();

    // 右側彩色小圓點 (呼吸動畫)
    const pulse = 0.6 + 0.4 * Math.sin(currentTime * 3);
    ctx.fillStyle = `rgba(76, 175, 80, ${pulse})`;
    ctx.beginPath();
    ctx.arc(boxX + boxW - 16, boxY + boxH / 2, 4, 0, Math.PI * 2);
    ctx.fill();

    // 文字 (右對齊，圓點左邊)
    ctx.fillStyle = "rgba(255, 255, 255, 0.85)";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.fillText(_teacherMsgCache, boxX + boxW - 26, boxY + boxH / 2);
  }

  function _generateTeacherMessage(t, nowPlaying, upcoming) {
    // 找當前和弦
    let currentChordName = "";
    let nextChordName = "";
    if (chordData && chordData.chords) {
      for (let i = chordData.chords.length - 1; i >= 0; i--) {
        if (t >= chordData.chords[i].time) {
          currentChordName = chordData.chords[i].chord || "";
          if (i + 1 < chordData.chords.length) {
            nextChordName = chordData.chords[i + 1].chord || "";
          }
          break;
        }
      }
    }

    // 分析即將到來的音符
    const upLH = upcoming.filter(e => e._hand === "left");
    const upRH = upcoming.filter(e => e._hand === "right");
    const hasThumbCross = upcoming.some(e => e.finger === 1 && e._hand === "right");
    const hasBlackKey = upcoming.some(e => {
      const pc = (e.pitch || 0) % 12;
      return [1, 3, 6, 8, 10].includes(pc);
    });
    const hasLargeJump = upcoming.length >= 2 &&
      Math.abs((upcoming[upcoming.length - 1]?.pitch || 0) - (upcoming[0]?.pitch || 0)) > 7;

    // 踏板狀態
    let pedalActive = false;
    if (accData && accData.pedal) {
      pedalActive = accData.pedal.some(p => p.start <= t && p.end > t);
    }

    // 即將到來的力度變化
    const upVelocities = upcoming.map(e => e.velocity || 80);
    const avgVel = upVelocities.length > 0
      ? upVelocities.reduce((a, b) => a + b, 0) / upVelocities.length : 80;

    // 動態表情提示
    let dynHint = "";
    if (avgVel > 95) dynHint = "ff 全力推進！";
    else if (avgVel > 80) dynHint = "f 有力地彈奏";
    else if (avgVel < 50) dynHint = "pp 輕柔觸鍵...";
    else if (avgVel < 65) dynHint = "p 溫柔地";

    // Articulation 提示
    const artTypes = upcoming.map(e => e.articulation).filter(Boolean);
    let artHint = "";
    if (artTypes.includes("staccato")) artHint = "斷奏 · ";
    else if (artTypes.includes("legato")) artHint = "連奏 ~ ";

    // 組合多樣化的教學訊息 — 根據情境優先級
    const msgs = [];

    // 高優先: 技術警告
    if (hasThumbCross) {
      msgs.push("注意拇指穿越 ↻ 保持手腕放鬆");
    }
    if (hasLargeJump) {
      msgs.push("大跳躍即將到來 — 提前移動手位");
    }
    if (hasBlackKey && upRH.length > 2) {
      msgs.push("黑鍵群 — 手指靠近琴蓋，指尖觸鍵");
    }

    // 中優先: 和弦/換和弦提示
    if (nextChordName && upcoming.length > 0) {
      const timeToNext = upcoming[0].time - t;
      if (timeToNext < 0.3 && nextChordName !== currentChordName) {
        msgs.push(`準備換和弦 → ${nextChordName}`);
      }
    }

    // 表情提示
    if (dynHint) msgs.push(dynHint);

    // 踏板提示
    if (pedalActive) {
      msgs.push("踏板延音中 🎹");
    }

    // Articulation + 風格
    if (artHint) msgs.push(artHint.trim());

    // 低優先: 風格基礎提示
    if (msgs.length === 0) {
      if (teachStyle === "Auto") msgs.push("段落自適 — 主歌分解、副歌附點、前奏柱狀");
      else if (teachStyle === "Arpeggio") msgs.push("流動的分解和弦 — 保持均勻觸鍵");
      else if (teachStyle === "Block") msgs.push("柱狀和弦 — 雙手同步，力度均衡");
      else if (teachStyle === "Rhythm") msgs.push("Ballad 附點節奏 — 長-短-長，抒情搖擺");
      else if (teachStyle === "Walking") msgs.push("Walking Bass — 低音線條行走中");
      else if (teachStyle === "Stride") msgs.push("Stride — 低音與和弦交替跳躍");
      else if (teachStyle === "Shell") msgs.push("Shell Voicing — 3rd + 7th 骨架和聲");
      else msgs.push("跟著音塊節奏，享受音樂 ♪");
    }

    // 取最高優先的一條
    return msgs[0] || "";
  }

  // Teaching controls setup
  var _teachControlsReady = false;
  function _setupTeachControls() {
    if (_teachControlsReady) {
      // Already initialized — just sync guitar UI visibility
      if (typeof window._syncGuitarArpUI === "function") window._syncGuitarArpUI();
      return;
    }
    _teachControlsReady = true;
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

    // Phase 11: Fingering toggle
    const btnFingering = $("#btnFingering");
    if (btnFingering) {
      const updateFingeringUI = () => {
        btnFingering.style.background = showFingering ? "rgba(76, 175, 80, 0.2)" : "";
        btnFingering.style.textShadow = showFingering ? "0 0 10px rgba(76, 175, 80, 0.8)" : "";
      };
      updateFingeringUI();
      btnFingering.addEventListener("click", () => {
        showFingering = !showFingering;
        localStorage.setItem("livechord_show_fingering", showFingering.toString());
        updateFingeringUI();
        showToast(showFingering ? "指法顯示: ON" : "指法顯示: OFF", 1500);
      });
    }

    // Phase 11: Force refresh accompaniment (clear cache)
    const btnRefreshAcc = $("#btnRefreshAcc");
    if (btnRefreshAcc) {
      btnRefreshAcc.addEventListener("click", () => {
        accData = null;
        _loadAccompaniment(true);
        showToast("強制重新生成伴奏 (含踏板/力度)...", 3000);
      });
    }

    // Teach-controls toggle
    const btnToggleTeach = $("#btnToggleTeach");
    const teachPanel = $("#teachControls");
    if (btnToggleTeach && teachPanel) {
      let teachOpen = localStorage.getItem("livechord_teach_open") === "true";
      function _syncTeachToggle() {
        teachPanel.style.display = teachOpen ? "flex" : "none";
        btnToggleTeach.style.color = teachOpen ? "var(--accent)" : "var(--text-dim)";
      }
      _syncTeachToggle();
      btnToggleTeach.addEventListener("click", () => {
        teachOpen = !teachOpen;
        localStorage.setItem("livechord_teach_open", teachOpen.toString());
        _syncTeachToggle();
      });
    }

    // Guitar style & arpeggio pattern selectors
    const guitarStyleSel = $("#guitarStyleSelect");
    const arpPatternSel = $("#gtArpPattern");
    const arpSelectorDiv = $("#gtArpSelector");

    function _syncGuitarArpUI() {
      const isStringTab = InstrumentRegistry.isStringInstrument(activeTab);
      const isArp = guitarStrumStyle === "arpeggio";
      // Top bar: arp selector or style label
      if (arpSelectorDiv) {
        arpSelectorDiv.style.display = (isArp && isStringTab) ? "" : "none";
      }
      const styleLabel = $("#gtRhStyleLabel");
      if (styleLabel) {
        if (isStringTab && !isArp) {
          styleLabel.style.display = "";
          const nameEl = styleLabel.querySelector(".gt-rh-style-name");
          if (nameEl) nameEl.textContent = guitarStrumStyle === "block" ? "Block ▼" : "D DU UDU";
        } else {
          styleLabel.style.display = "none";
        }
      }
      if (guitarStyleSel) {
        guitarStyleSel.style.display = isStringTab ? "" : "none";
      }
      // Bottom legend: p/i/m/a for arpeggio, hide for others
      const rhLegend = $("#gtRhFingerLegend");
      if (rhLegend) {
        rhLegend.style.display = (isArp && isStringTab) ? "" : "none";
      }
      // Hide piano teachStyle on guitar/ukulele, show on piano
      const pianoStyleSel = $("#teachStyle");
      if (pianoStyleSel) {
        pianoStyleSel.style.display = isStringTab ? "none" : "";
      }
    }

    // Ensure values are initialized (var hoisting leaves them undefined until assignment line)
    if (!guitarStrumStyle) guitarStrumStyle = localStorage.getItem("livechord_guitar_strum_style") || "arpeggio";
    if (!guitarArpPattern) guitarArpPattern = localStorage.getItem("livechord_guitar_arp_pattern") || "pima";

    if (guitarStyleSel) {
      guitarStyleSel.value = guitarStrumStyle;
      guitarStyleSel.addEventListener("change", () => {
        guitarStrumStyle = guitarStyleSel.value;
        localStorage.setItem("livechord_guitar_strum_style", guitarStrumStyle);
        _syncGuitarArpUI();
      });
    }

    if (arpPatternSel) {
      arpPatternSel.value = guitarArpPattern;
      arpPatternSel.addEventListener("change", () => {
        guitarArpPattern = arpPatternSel.value;
        localStorage.setItem("livechord_guitar_arp_pattern", guitarArpPattern);
      });
    }

    // Expose sync function for tab switching
    window._syncGuitarArpUI = _syncGuitarArpUI;
  }

  // resize observer for 88-key piano
  if (chordDisplay88) {
    let _resizeTimer = null;
    new ResizeObserver(() => {
      clearTimeout(_resizeTimer);
      _resizeTimer = setTimeout(() => {
        if (activeTab === "piano" && pianoSubmode === "waterfall") {
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
    // Rebuild ribbon to include section markers now that sectionData is available
    _buildUnifiedRibbon();
    updateActiveChord(audio.currentTime || 0);
    _updateKeyDisplay(audio.currentTime || 0);
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
          const _ma = { Mixolydian:"Mix", Dorian:"Dor", Lydian:"Lyd", Aeolian:"Aeo", Blues:"Blues" };
          const _ml = chordData.mode && _ma[chordData.mode] ? ` ${_ma[chordData.mode]}` : "";
          if (keyInfo) keyInfo.textContent = `Key: ${chordData.key}${_ml}`;
        }
        if (chordData.capo) {
          capo = chordData.capo;
          const capoSel = $("#capoSelect");
          if (capoSel) capoSel.value = capo;
        }
        await preloadChordInfo(chordData.chords);
        buildChordDOM();
        // Re-init active instrument so it picks up new chord data
        if (activeTab !== "piano") {
          const inst = InstrumentRegistry.get(activeTab);
          if (inst) {
            const container = $(inst._config.selectors.container);
            if (container) container.style.display = "flex";
            inst.init();
          }
        }
        // Chords loaded
        // 載入段落 + 旋律資訊
        _loadSections(path);
        _loadMelody(path);
        return;
      }
    } catch (err) {
      console.error("loadChords error:", err);
    }

    hasChords = false;
    if (unifiedRibbonTrack) unifiedRibbonTrack.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-dim);font-size:13px">尚無和弦譜 — 請按「偵測」按鈕</div>';
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
      const entry = { jianpu: "", notes: [] };
      // Dynamically create diagram slots for all registered string instruments
      for (const id of InstrumentRegistry.list()) {
        if (InstrumentRegistry.needsDiagram(id)) entry[`diagram_${id}`] = null;
      }
      chordCache[name] = entry;
      try {
        const info = await API.chordInfo(name);
        chordCache[name].jianpu = info.jianpu || "";
        chordCache[name].notes = info.notes || [];
      } catch {}
      for (const id of InstrumentRegistry.list()) {
        if (InstrumentRegistry.needsDiagram(id)) {
          try { chordCache[name][`diagram_${id}`] = await API.chordDiagram(id, name); } catch {}
        }
      }
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
        requestAnimationFrame(() => updateActiveChord(audio.currentTime || -1, true));
      });
      return;
    }
    _buildDOMFromChords(chords);
  }

  function _buildDOMFromChords(chords) {
    chordElements = [];
    activeChordIdx = -1;

    // Build unified ribbon (vertical, piano-style)
    _buildUnifiedRibbon();

    // 重新渲染段落標記
    if (sectionData) _renderSectionMarkers();
  }

  async function _loadMissingCache(names) {
    const unique = [...new Set(names)];
    await Promise.all(unique.map(async (name) => {
      if (chordCache[name]) return;
      const entry = { jianpu: "", notes: [] };
      for (const id of InstrumentRegistry.list()) {
        if (InstrumentRegistry.needsDiagram(id)) entry[`diagram_${id}`] = null;
      }
      chordCache[name] = entry;
      try {
        const info = await API.chordInfo(name);
        chordCache[name].jianpu = info.jianpu || "";
        chordCache[name].notes = info.notes || [];
      } catch {}
      for (const id of InstrumentRegistry.list()) {
        if (InstrumentRegistry.needsDiagram(id)) {
          try { chordCache[name][`diagram_${id}`] = await API.chordDiagram(id, name); } catch {}
        }
      }
    }));
  }

  // Overview chord items and horizontal ribbon removed — unified vertical ribbon replaces them

  /** 更新高亮：unified ribbon + instrument panels */
  function updateActiveChord(currentTime, forceScroll = false) {
    if (!chordData || !chordData.chords || ribbonElements.length === 0) return;

    const displayedChords = _displayChords();

    let newIdx = -1;
    for (let i = displayedChords.length - 1; i >= 0; i--) {
      if (currentTime >= displayedChords[i].time) {
        newIdx = i;
        break;
      }
    }

    if (newIdx === activeChordIdx) return;

    // Remove old highlight
    if (activeChordIdx >= 0 && activeChordIdx < ribbonElements.length) {
      ribbonElements[activeChordIdx].classList.remove("active");
      ribbonElements[activeChordIdx].classList.add("played");
    }

    activeChordIdx = newIdx;

    if (activeChordIdx >= 0 && activeChordIdx < ribbonElements.length) {
      const el = ribbonElements[activeChordIdx];
      el.classList.remove("played");
      el.classList.add("active");

      // Auto-scroll ribbon to keep active chord visible
      if (chordRibbonPanel && (!audio.paused || forceScroll)) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }
  }

  // ===========================================================================
  // 播放控制（攔截播放按鈕，無和弦時先偵測）
  // ===========================================================================

  // Context-sensitive playback controls
  // Playing: ◀ = rewind to start, ▶ = pause
  // Stopped: ◀ = prev song, ▶ = play
  btnPlay.addEventListener("click", () => {
    if (audio.paused) audio.play();
    else audio.pause();
  });

  const btnPrev = $("#btnPrev");
  if (btnPrev) {
    btnPrev.addEventListener("click", () => {
      _rewindToStart();
    });
  }

  function _setSmartView(playing) {
    // No-op: overview removed, ribbon always scrolls
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
  });

  // requestAnimationFrame sync
  let rafId = null;

  function _updateProgress(t) {
    const d = audio.duration || 1;
    const pct = (t / d * 100) + "%";
    if (topProgressFill) topProgressFill.style.width = pct;
    timeCurrent.textContent = formatTime(t);
  }

  function tickSync() {
    if (!audio.paused) {
      let t = audio.currentTime;
      if (abState === "active" && abA != null && abB != null && t >= abB) {
        audio.currentTime = abA;
        t = abA;
      }
      _updateProgress(t);
      updateActiveChord(t);
      _updateKeyDisplay(t);
      if (activeTab === "piano") {
        update88Piano(t);
        drawWaterfall(t);
      } else {
        const _inst = InstrumentRegistry.get(activeTab);
        if (_inst) _inst.update(t);
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
    _updateProgress(t);
    updateActiveChord(t);
    if (activeTab === "piano") { update88Piano(t); drawWaterfall(t); }
  });

  audio.addEventListener("seeked", () => {
    const t = audio.currentTime;
    _updateProgress(t);
    updateActiveChord(t);
    if (activeTab === "piano") { piano88LastIdx = -1; update88Piano(t); }
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
    if (topProgressFill) topProgressFill.style.width = "0%";
    if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
    if (activeChordIdx >= 0 && activeChordIdx < ribbonElements.length) {
      ribbonElements[activeChordIdx].classList.remove("active");
    }
    activeChordIdx = -1;
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

  // Restore saved volume
  const savedVol = localStorage.getItem("livechord_volume");
  if (savedVol !== null) {
    const v = parseFloat(savedVol);
    audio.volume = v;
    if (volumeSlider) volumeSlider.value = v;
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
  let speedIdx = SPEEDS.indexOf(1);

  function _syncSpeedUI() {
    const s = SPEEDS[speedIdx];
    const label = s + "x";
    if (btnSpeed) { btnSpeed.textContent = label; btnSpeed.classList.toggle("modified", s !== 1); }
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

      if (jazzifyAIActive) {
        jazzifyAIActive = false;
        if (btnJazzifyAI) {
           btnJazzifyAI.textContent = "AI";
           btnJazzifyAI.style.background = "";
        }
      }

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

  const btnJazzifyAI = $("#btnJazzifyAI");
  let jazzifyAIActive = false;

  if (btnJazzifyAI) {
    btnJazzifyAI.addEventListener("click", async () => {
      if (!chordData || !chordData.chords || chordData.chords.length === 0) {
        showToast("尚無和弦資料", 2000);
        return;
      }

      if (jazzifyAIActive) {
        // 還原原始
        if (originalChords) {
          chordData.chords = originalChords;
          originalChords = null;
        }
        jazzifyAIActive = false;
        jazzifyActive = false;
        jazzifyLevel = 0;
        btnJazzifyAI.style.background = "";
        if (btnJazzify) {
          btnJazzify.textContent = "\u{1F3B7}";
          btnJazzify.style.background = "";
        }
        chordCache = {};
        await preloadChordInfo(chordData.chords);
        buildChordDOM();
        activeChordIdx = -1; // 強制重新觸發
        requestAnimationFrame(() => updateActiveChord(audio.currentTime || -1, true));
        showToast("已還原原始和弦", 1500);
        return;
      }

      // 儲存原始
      if (!originalChords) {
        originalChords = [...chordData.chords];
      }

      btnJazzifyAI.textContent = "⌛";

      try {
        const res = await API.jazzify(originalChords, chordData.key || "C", 3, "transformer");
        if (res.error) {
          throw new Error(res.error);
        }
        chordData.chords = res.chords;
        jazzifyAIActive = true;
        jazzifyActive = true;
        jazzifyLevel = 3; // Sync UI state conceptually
        btnJazzifyAI.textContent = "AI";
        btnJazzifyAI.style.background = "rgba(156,39,176,.3)";
        if (btnJazzify) btnJazzify.style.background = "";

        chordCache = {};
        await preloadChordInfo(chordData.chords);
        buildChordDOM();
        activeChordIdx = -1; // 強制重新觸發
        requestAnimationFrame(() => updateActiveChord(audio.currentTime || -1, true));
        showToast(`AI Transformer 重配: ${res.original_count}→${res.jazzified_count} 和弦`, 3000);
      } catch (err) {
        showToast("AI Jazzify 失敗: " + err.message, 3000);
        jazzifyAIActive = false;
        btnJazzifyAI.textContent = "AI";
        btnJazzifyAI.style.background = "";
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

  // ---- instrument / capo (driven by tab selection) ----

  // ---- hand switch (88-key mode) ----
  const handSwitch = $("#handSwitch");

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

  function _updateKeyDisplay(currentTime) {
    const keyInfo = $("#chordKey");
    if (!keyInfo || !chordData || !chordData.key) return;
    const shift = transpose - capo;
    const baseKey = shift === 0 ? chordData.key : transposeChord(chordData.key, shift);

    // Detect per-section key changes & mode
    if (sectionData && sectionData.sections) {
      const globalIsMajor = !baseKey.endsWith("m");
      const globalRoot = baseKey.replace(/m$/, "");
      const rawRoot = k => k.replace(/m.*$/, "");
      const normalize = k => {
        if (!globalIsMajor || !/^[A-G][b#]?m$/.test(k)) return k;
        const mRoot = k.replace(/m$/, "");
        if (mRoot === globalRoot) return baseKey;
        return transposeChord(mRoot, 3);
      };

      // Build per-section normalized key sequence
      const secKeys = sectionData.sections
        .filter(s => s.key)
        .map(s => normalize(shift === 0 ? s.key : transposeChord(s.key, shift)));

      // Filter: only keep sustained modulations (key must persist 2+ consecutive sections)
      // Single-section deviations = modal borrowing, not modulation
      const stable = [];
      for (let i = 0; i < secKeys.length; i++) {
        const r = rawRoot(secKeys[i]);
        const prevR = i > 0 ? rawRoot(secKeys[i - 1]) : null;
        const nextR = i < secKeys.length - 1 ? rawRoot(secKeys[i + 1]) : null;
        // Keep if: same as previous OR same as next (sustained)
        if (r === prevR || r === nextR) {
          if (stable.length === 0 || rawRoot(stable[stable.length - 1]) !== r) {
            stable.push(secKeys[i]);
          }
        }
      }
      // Fallback: if nothing survived, use global key
      if (stable.length === 0) stable.push(baseKey);

      const t = currentTime != null ? currentTime : (audio.currentTime || 0);
      const curSec = [...sectionData.sections].reverse().find(s => t >= s.start);
      const curMode = curSec && curSec.mode ? curSec.mode : "";
      const modeAbbr = { Mixolydian: "Mix", Dorian: "Dor", Lydian: "Lyd", Aeolian: "Aeo", Phrygian: "Phr", Blues: "Blues" };
      const curModeLabel = modeAbbr[curMode] || "";

      const uniqueRoots = new Set(stable.map(rawRoot));
      if (uniqueRoots.size > 1) {
        const curRaw = curSec && curSec.key ? normalize(shift === 0 ? curSec.key : transposeChord(curSec.key, shift)) : baseKey;
        const display = stable.map(k => {
          if (rawRoot(k) === rawRoot(curRaw)) {
            return `<span style="color:#00e5ff;text-shadow:0 0 8px rgba(0,229,255,0.5)">${k}</span>`;
          }
          return `<span style="opacity:0.35">${k}</span>`;
        }).join(' <span style="opacity:0.3">→</span> ');
        const modeSuffix = curModeLabel ? ` <span style="color:#00e5ff;opacity:0.7;font-size:0.85em">${curModeLabel}</span>` : "";
        keyInfo.innerHTML = `Key: ${display}${modeSuffix}`;
        return;
      }
      // No modulation — show single key with mode if non-standard
      if (curModeLabel) {
        keyInfo.innerHTML = `Key: ${baseKey} <span style="color:#00e5ff;opacity:0.7;font-size:0.85em">${curModeLabel}</span>`;
        return;
      }
    }
    keyInfo.textContent = `Key: ${baseKey}`;
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

  // ---- player search (in topbar) ----
  const searchInput2 = $("#searchInput");
  const searchResults = $("#searchResults");
  let _searchTimer = null;

  if (searchInput2 && searchResults) {
    searchInput2.addEventListener("input", () => {
      clearTimeout(_searchTimer);
      const q = searchInput2.value.trim();
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

    // Click outside to close
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".topbar-search")) searchResults.classList.remove("show");
    });

    searchInput2.addEventListener("focus", () => {
      if (searchInput2.value.trim().length > 0 && searchResults.innerHTML) {
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

  // Enable drag scroll on chord ribbon panel
  _initDragScroll(chordRibbonPanel);

  loadTrack(trackPath).then(() => {
    if (autoplay) audio.play().catch(() => {});
    if (restoreFs) {
      document.documentElement.requestFullscreen().catch(() => {});
      if (btnPageFs) btnPageFs.innerHTML = "&#x2716;";
    }
  });

  // --- AI Auditing Synthesizer (Salamander Grand Piano Sampler) ---
  class PianoSynth {
    constructor() {
      this.ctx = null;
      this.masterGain = null;
      this.volLeft = 1;
      this.volRight = 1;
      this.samples = {};    // MIDI note -> AudioBuffer
      this.loading = false;
      this.loaded = false;
      // Salamander Grand Piano (CC-BY-3.0) via Tone.js CDN
      this._baseUrl = "https://tonejs.github.io/audio/salamander/";
      // Notes we actually load (every 3 semitones covers 88 keys with pitch-shift)
      this._sampleNotes = [
        21, 24, 27, 30, 33, 36, 39, 42, 45, 48, 51, 54,
        57, 60, 63, 66, 69, 72, 75, 78, 81, 84, 87, 90,
        93, 96, 99, 102, 105, 108
      ];
    }

    _noteToName(midi) {
      // Tone.js Salamander uses sharps (Cs, Ds, Fs, Gs, As), not flats
      const names = ['C','Cs','D','Ds','E','F','Fs','G','Gs','A','As','B'];
      const oct = Math.floor(midi / 12) - 1;
      return names[midi % 12] + oct;
    }

    async _loadSamples() {
      if (this.loading || this.loaded) return;
      this.loading = true;
      const promises = this._sampleNotes.map(async (note) => {
        const name = this._noteToName(note);
        const url = this._baseUrl + name + ".mp3";
        try {
          const resp = await fetch(url);
          if (!resp.ok) return;
          const buf = await resp.arrayBuffer();
          this.samples[note] = await this.ctx.decodeAudioData(buf);
        } catch (e) {
          console.warn("Failed to load sample:", name, e);
        }
      });
      await Promise.all(promises);
      this.loaded = true;
      this.loading = false;
      console.log(`Piano samples loaded: ${Object.keys(this.samples).length} notes`);
    }

    _findClosestSample(pitch) {
      let best = this._sampleNotes[0];
      let bestDist = Math.abs(pitch - best);
      for (const n of this._sampleNotes) {
        const d = Math.abs(pitch - n);
        if (d < bestDist) { bestDist = d; best = n; }
      }
      return best;
    }

    init() {
      if (!this.ctx) {
        this.ctx = new (window.AudioContext || window.webkitAudioContext)();
        this.masterGain = this.ctx.createGain();
        this.masterGain.gain.value = 0.5;
        this.masterGain.connect(this.ctx.destination);
        this._loadSamples();
      }
    }

    playNote(pitch, duration, hand, startTime) {
      if (!this.ctx) return;
      if (typeof activeHand !== 'undefined' && activeHand !== "both" && activeHand !== hand) return;

      const vol = hand === 'left' ? this.volLeft : this.volRight;
      if (vol <= 0) return;

      // Sampler mode (preferred)
      if (this.loaded && Object.keys(this.samples).length > 0) {
        const sampleNote = this._findClosestSample(pitch);
        const buffer = this.samples[sampleNote];
        if (!buffer) return;

        const source = this.ctx.createBufferSource();
        source.buffer = buffer;
        // Pitch-shift by adjusting playback rate
        source.playbackRate.value = Math.pow(2, (pitch - sampleNote) / 12);

        const gain = this.ctx.createGain();
        source.connect(gain);
        gain.connect(this.masterGain);

        // Natural piano envelope with release
        gain.gain.setValueAtTime(vol * 0.4, startTime);
        gain.gain.setValueAtTime(vol * 0.4, startTime + Math.max(0, duration - 0.08));
        gain.gain.linearRampToValueAtTime(0, startTime + duration + 0.1);

        source.start(startTime);
        source.stop(startTime + duration + 0.15);
        return;
      }

      // Fallback: oscillator (while samples are loading)
      const osc = this.ctx.createOscillator();
      const gain = this.ctx.createGain();
      osc.type = pitch < 60 ? 'triangle' : 'sine';
      osc.frequency.value = 440 * Math.pow(2, (pitch - 69) / 12);
      osc.connect(gain);
      gain.connect(this.masterGain);
      gain.gain.setValueAtTime(0, startTime);
      gain.gain.linearRampToValueAtTime(vol * 0.3, startTime + 0.02);
      gain.gain.exponentialRampToValueAtTime(vol * 0.1, Math.max(startTime + 0.02, startTime + duration - 0.05));
      gain.gain.linearRampToValueAtTime(0, startTime + duration);
      osc.start(startTime);
      osc.stop(startTime + duration);
    }
  }

  const aiSynth = new PianoSynth();
  let lastScheduledTime = 0;

  function scheduleNotes(currentTime) {
      if (!aiSynth.ctx) return;
      // Handle seek or huge jump
      if (Math.abs(currentTime - lastScheduledTime) > 1.0) lastScheduledTime = currentTime;
      
      const lookahead = 0.2; 
      if (!accData) return;
      
      const scheduleHand = (events, hand) => {
          for (const e of events) {
              if (e.time >= lastScheduledTime && e.time < currentTime + lookahead) {
                  const delay = e.time - currentTime;
                  const targetTime = aiSynth.ctx.currentTime + delay;
                  if (e.pitches) {
                      for (const p of e.pitches) {
                          aiSynth.playNote(p, e.duration || 0.5, hand, targetTime);
                      }
                  } else if (e.pitch) {
                      aiSynth.playNote(e.pitch, e.duration || 0.5, hand, targetTime);
                  }
              }
          }
      };
      
      scheduleHand(accData.left_hand || [], 'left');
      scheduleHand(accData.right_hand || [], 'right');
      
      lastScheduledTime = currentTime + lookahead;
  }

  // Hook into update loop
  const originalUpdate = window.requestAnimationFrame;
  // We can just add it to the existing `update()` loop ...
  // Wait, I will just patch `audio.addEventListener('timeupdate')` or add it to `renderWaterfall`
  // Actually, I can set an interval or hook it since this is inside the IIFE.

  audio.addEventListener("play", () => {
      aiSynth.init();
      if (aiSynth.ctx) aiSynth.ctx.resume();
  });

  setInterval(() => {
      if (!audio.paused) {
          scheduleNotes(audio.currentTime);
      }
  }, 50);

  // Audio Mode UI Bindings (Music -> MIDI -> Mix)
  const btnAudioMode = document.getElementById("btnAudioMode");
  const crossfaderContainer = document.getElementById("crossfaderContainer");
  const crossfaderVol = document.getElementById("crossfaderVol");
  
  let audioMode = 0; // 0: Music, 1: MIDI, 2: Mix

  function applyAudioMode() {
      if (!btnAudioMode) return;
      
      // Get base volume from normal player slider
      const volumeSlider = document.getElementById("volumeSlider");
      const baseVol = volumeSlider ? parseFloat(volumeSlider.value) : 1.0;
      
      if (audioMode === 0) {
          btnAudioMode.innerHTML = "🎵 Music";
          btnAudioMode.style.color = "#03a9f4";
          btnAudioMode.style.borderColor = "#03a9f4";
          audio.volume = baseVol;
          aiSynth.volLeft = 0;
          aiSynth.volRight = 0;
          if (crossfaderContainer) crossfaderContainer.style.display = "none";
      } else if (audioMode === 1) {
          btnAudioMode.innerHTML = "🎹 MIDI";
          btnAudioMode.style.color = "#ff9800";
          btnAudioMode.style.borderColor = "#ff9800";
          audio.volume = 0;
          aiSynth.volLeft = 1.0;
          aiSynth.volRight = 1.0;
          if (crossfaderContainer) crossfaderContainer.style.display = "none";
      } else {
          btnAudioMode.innerHTML = "🎧 Mix";
          btnAudioMode.style.color = "#4caf50";
          btnAudioMode.style.borderColor = "#4caf50";
          if (crossfaderContainer) crossfaderContainer.style.display = "flex";
          
          const mixVal = crossfaderVol ? parseFloat(crossfaderVol.value) : 0.5;
          audio.volume = baseVol * (1 - mixVal);
          aiSynth.volLeft = mixVal;
          aiSynth.volRight = mixVal;
      }
      
      // The hand mute filtering is still robustly done in aiSynth.playNote
  }

  if (btnAudioMode) {
      btnAudioMode.addEventListener("click", () => {
          audioMode = (audioMode + 1) % 3;
          applyAudioMode();
          const modeNames = ["Music (原曲)", "MIDI (純AI伴奏)", "Mix (原曲+AI伴奏)"];
          showToast(`已切換至 ${modeNames[audioMode]} 模式`);
      });
      applyAudioMode(); // init
  }

  if (crossfaderVol) {
      crossfaderVol.addEventListener("input", () => {
          applyAudioMode();
      });
  }

  // MIDI Download
  const btnDownloadMidi = document.getElementById("btnDownloadMidi");
  if (btnDownloadMidi) {
      btnDownloadMidi.addEventListener("click", () => {
          if (window.MidiExporter && accData) {
              const rTitle = document.title.replace(/ — LiveChord$/, "") || trackPath.split("/").pop().replace(/\.\w+$/, "") || "Track";
              try {
                  window.MidiExporter.exportMidi(accData, rTitle, teachStyle, teachLevel);
                  showToast("✅ MIDI 已下載至預設下載目錄");
              } catch (err) {
                  console.error("MIDI export error:", err);
                  showToast("MIDI 匯出失敗: " + err.message, true);
              }
          } else {
              showToast("尚無伴奏資料可下載", true);
          }
      });
  }

  // RLHF Feedback Buttons & Popup
  const btnRateAi = document.getElementById("btnRateAiTop");
  const ratePopup = document.getElementById("ratePopup");
  const btnRlhfGood = document.getElementById("btnRlhfGood");
  const btnRlhfBad = document.getElementById("btnRlhfBad");

  if (btnRateAi && ratePopup) {
      btnRateAi.addEventListener("click", (e) => {
          e.stopPropagation();
          ratePopup.style.display = ratePopup.style.display === "none" ? "flex" : "none";
      });
      document.addEventListener("click", (e) => {
          if (!btnRateAi.contains(e.target) && !ratePopup.contains(e.target)) {
              ratePopup.style.display = "none";
          }
      });
  }

  if (btnRlhfGood) {
      btnRlhfGood.addEventListener("click", () => {
          showToast("感謝老師肯定！評分已記錄！");
          if (ratePopup) ratePopup.style.display = "none";
      });
  }
  if (btnRlhfBad) {
      btnRlhfBad.addEventListener("click", () => {
          showToast("已紀錄此負面特徵 (Negative Sample)，將用於未來訓練！");
          // Here we would normally fetch('/api/ai/evaluate-feedback', { method: 'POST', body: JSON.stringify(...) })
          if (ratePopup) ratePopup.style.display = "none";
      });
  }

  // ===========================================================================
  // STRING INSTRUMENTS — Guitar / Ukulele / ... (registry-based)
  // ===========================================================================

  // Guitar strum style for right-hand waterfall
  var guitarStrumStyle = localStorage.getItem("livechord_guitar_strum_style") || "arpeggio";
  var guitarArpPattern = localStorage.getItem("livechord_guitar_arp_pattern") || "pima";

  // ===========================================================================
  // STRING INSTRUMENT REGISTRY — 新增樂器只需加 config + register()
  // ===========================================================================
  const _playerBridge = {
    $,
    getChordData: () => chordData,
    getDisplayChords: () => _displayChords(),
    getAudio: () => audio,
    getChordCache: () => chordCache,
    getCurrentKey: () => _currentKey(),
    getStrumStyle: () => guitarStrumStyle,
    getArpPattern: () => guitarArpPattern,
    getAccData: () => accData,
    getMelodyData: () => melodyData,
    getActiveTab: () => activeTab,
    drawAITeacherHUD: _drawAITeacherHUD,
    API,
    ChordRender,
  };

  const GUITAR_CONFIG = {
    id: "guitar",
    numStrings: 6,
    openMidi: [40, 45, 50, 55, 59, 64],
    stringLabels: ["E", "A", "D", "G", "B", "e"],
    stringNamesZh: ["6弦 E", "5弦 A", "4弦 D", "3弦 G", "2弦 B", "1弦 e"],
    diagramCacheKey: "diagram_guitar",
    selectors: {
      container: "#chordDisplayGuitar",
      fretboardCanvas: "#guitarVerticalFretboard",
      waterfallCanvas: "#guitarRhWaterfall",
      chordName: "#gtChordName",
      voicingRow: "#gtVoicingRow",
      lhHint: "#gtLhHint",
      rhHint: "#gtRhHint",
    },
  };

  const UKULELE_CONFIG = {
    id: "ukulele",
    numStrings: 4,
    openMidi: [55, 48, 52, 57],
    stringLabels: ["G", "C", "E", "A"],
    stringNamesZh: ["4弦 G", "3弦 C", "2弦 E", "1弦 A"],
    diagramCacheKey: "diagram_ukulele",
    selectors: {
      container: "#chordDisplayUkulele",
      fretboardCanvas: "#ukuleleVerticalFretboard",
      waterfallCanvas: "#ukuleleRhWaterfall",
      chordName: "#ukChordName",
      voicingRow: "#ukVoicingRow",
      lhHint: "#ukLhHint",
      rhHint: "#ukRhHint",
    },
  };

  InstrumentRegistry.register("guitar",  new StringInstrument(GUITAR_CONFIG,  _playerBridge));
  InstrumentRegistry.register("ukulele", new StringInstrument(UKULELE_CONFIG, _playerBridge));

  const ACCORDION_CONFIG = {
    id: "accordion",
    selectors: {
      container: "#chordDisplayAccordion",
      bassGridCanvas: "#accordionBassGrid",
      waterfallCanvas: "#accordionKeyboardWaterfall",
      chordName: "#accChordName",
      lhHint: "#accLhHint",
      rhHint: "#accRhHint",
      patternSelect: "#accBassPatternSelect",
      patternLabel: "#accBassPatternLabel",
    },
  };
  InstrumentRegistry.register("accordion", new AccordionInstrument(ACCORDION_CONFIG, _playerBridge));

  const ARRANGER_CONFIG = {
    id: "arranger",
    selectors: {
      container: "#chordDisplayArranger",
      waterfallCanvas: "#arrangerWaterfall",
      keyboardCanvas: "#arrangerKeyboard",
    },
  };
  InstrumentRegistry.register("arranger", new ArrangerInstrument(ARRANGER_CONFIG, _playerBridge));

})();

// 移調工具函式、簡譜函式 moved to utils.js
