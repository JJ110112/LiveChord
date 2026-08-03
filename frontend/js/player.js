/** LiveChord 播放頁 */

(function () {
  function _t(k, v) { return (window.LiveChordI18n && window.LiveChordI18n.t) ? window.LiveChordI18n.t(k, v) : k; }
  const $ = (sel) => document.querySelector(sel);

  const params = new URLSearchParams(window.location.search);
  const trackPath = params.get("path");
  const hashMode = params.get("hash");   // process result: load by hash directly
  const autoplay = params.get("autoplay") === "1";
  const restoreFs = params.get("fs") === "1";
  if (!trackPath && !hashMode) { window.location.href = "/"; return; }
  const queueSource = params.get("queue") || "";
  const queueMode = params.get("queue_mode") === "shuffle" ? "shuffle" : "sequential";
  const queueSeed = params.get("queue_seed") || "";
  const queuePath = params.get("queue_path") || "";
  const queueGroup = params.get("queue_group") || "";
  const queueStyle = params.get("queue_style") || "";
  const queueLabel = params.get("queue_label") || "";
  const queueActive = !hashMode && !!trackPath && ["folder", "group", "jam"].includes(queueSource);
  const BEAT_SOURCE_PREF_KEY = "livechord_player_beat_source";
  function _pageBeatSourcePrefKey() {
    const songId = hashMode ? `hash_${hashMode}` : `path_${trackPath || ""}`;
    return `${BEAT_SOURCE_PREF_KEY}_${songId}`;
  }

  function _playerBeatSourcePreference() {
    const v = localStorage.getItem(_pageBeatSourcePrefKey());
    return (v === "librosa" || v === "madmom" || v === "beat_this")
      ? v
      : null;
  }

  function _chordsByHashUrl(hash) {
    const qs = new URLSearchParams({ hash });
    const pref = _playerBeatSourcePreference();
    if (pref) qs.set("beat_source", pref);
    return `/api/chords/by-hash?${qs.toString()}`;
  }

  function _chordsByPathUrl(path, version = null) {
    const qs = new URLSearchParams({ path });
    const pref = _playerBeatSourcePreference();
    if (pref) qs.set("beat_source", pref);
    if (version) qs.set("version", version);
    return `/api/chords?${qs.toString()}`;
  }

  function _rememberBeatSource(mode) {
    if (mode === "librosa" || mode === "madmom" || mode === "beat_this") {
      localStorage.setItem(_pageBeatSourcePrefKey(), mode);
    }
  }

  // ---- state ----
  let isFavorite = false;
  let chordData = null;
  let displayMode = "piano";
  let chordCache = {};
  let siblingTracks = [];
  let currentIndex = -1;
  let queueTracks = [];
  let queueIndex = -1;
  let activeChordIdx = -1;
  let chordElements = [];
  let _ribbonPositions = [];
  let sectionData = null;
  // 88-key piano state
  let piano88Canvas = null;
  let piano88Cache = null;
  let piano88ChordMidis = [];
  // Waterfall hit-spark particles (Sheet Music Boss style).
  // Array of { x, y, vx, vy, life, maxLife, r, g, b, size }.
  let _waterfallParticles = [];
  const _WF_PARTICLE_CAP = 160; // hard cap to keep per-frame cost bounded
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

  // Theme palette — drives canvas colors that can't go through CSS vars
  // (waterfall notes, 88-key labels, pedal sustain). Toggled from Tools popup
  // → #btnTheme; persisted to localStorage `livechord_theme`. The HTML head
  // inline script sets <html data-theme> early so CSS doesn't FOUC; this JS
  // mirrors the same key for canvas-side rendering.
  const _THEME_PALETTE = {
    dark: {
      noteRH: "#4fc3f7",
      noteLH: "#4caf50",
      pedalRgb: "76, 175, 80",
      barLineMajor: "rgba(255,255,255,0.25)",
      barLineMinor: "rgba(255,255,255,0.08)",
      barLabel: "rgba(255,255,255,0.6)",
      gridFaint: "rgba(255,255,255,0.05)",
      activeChordTint: "rgba(33,150,243,0.05)",
      activeChordEdge: "rgba(33,150,243,0.15)",
      keyLabelText: "#fff",
      keyLabelBg: "rgba(0,0,0,0.8)",
      keyLabelStroke: "rgba(255,255,255,0.3)",
      panelOverlay: "rgba(0, 0, 0, 0.3)",
      panelOverlayHeavy: "rgba(10, 10, 10, 0.65)",
      noteEmphasis: "rgba(255,255,255,0.85)",
      noteEdge: "rgba(255,255,255,0.4)",
      // chordOutline traces non-played chord-tone hints; chordTint is the very
      // faint background wash for chord-scale tones. LH/RH active colors are
      // intentionally NOT per-theme — they're fixed system colors (cyan/orange)
      // sourced from the hardcoded fallback in ChordRender.draw88Piano so the
      // "which hand plays now" cue stays consistent across themes.
      chordOutline: "rgba(182, 79, 255, 0.85)",
      chordTint: "#4fc3f7",
    },
    light: {
      noteRH: "#0277BD",
      noteLH: "#2E7D32",
      pedalRgb: "46, 125, 50",
      barLineMajor: "rgba(0,0,0,0.35)",
      barLineMinor: "rgba(0,0,0,0.10)",
      barLabel: "rgba(0,0,0,0.55)",
      gridFaint: "rgba(0,0,0,0.06)",
      activeChordTint: "rgba(25,118,210,0.08)",
      activeChordEdge: "rgba(25,118,210,0.25)",
      keyLabelText: "#1f2933",
      keyLabelBg: "rgba(255,255,255,0.92)",
      keyLabelStroke: "rgba(0,0,0,0.30)",
      panelOverlay: "rgba(255, 255, 255, 0.55)",
      panelOverlayHeavy: "rgba(245, 243, 235, 0.85)",
      noteEmphasis: "rgba(0,0,0,0.78)",
      noteEdge: "rgba(0,0,0,0.40)",
      chordOutline: "rgba(106, 27, 154, 0.85)",  // deep purple
      chordTint: "rgba(2, 119, 189, 0.7)",
    },
    // Forest — dark-bg family. Cyan + orange accents on deep forest.
    forest: {
      noteRH: "#06b6d4",          // cyan highlight
      noteLH: "#ff6b35",          // orange accent
      pedalRgb: "6, 182, 212",    // cyan pedal
      barLineMajor: "rgba(209,250,229,0.28)",
      barLineMinor: "rgba(209,250,229,0.09)",
      barLabel: "rgba(209,250,229,0.65)",
      gridFaint: "rgba(209,250,229,0.06)",
      activeChordTint: "rgba(255,107,53,0.07)",
      activeChordEdge: "rgba(255,107,53,0.20)",
      keyLabelText: "#d1fae5",
      keyLabelBg: "rgba(5, 26, 15, 0.82)",
      keyLabelStroke: "rgba(209,250,229,0.30)",
      panelOverlay: "rgba(5, 26, 15, 0.4)",
      panelOverlayHeavy: "rgba(5, 26, 15, 0.78)",
      noteEmphasis: "rgba(209,250,229,0.88)",
      noteEdge: "rgba(255,107,53,0.50)",
      chordOutline: "rgba(167, 139, 250, 0.85)",
      chordTint: "#06b6d4",
    },
    // Sakura — light-bg family. Rose + mint on warm cream.
    sakura: {
      noteRH: "#ec4899",          // rose accent
      noteLH: "#10b981",          // mint highlight
      pedalRgb: "16, 185, 129",   // mint pedal
      barLineMajor: "rgba(74,29,63,0.32)",
      barLineMinor: "rgba(74,29,63,0.10)",
      barLabel: "rgba(74,29,63,0.55)",
      gridFaint: "rgba(74,29,63,0.06)",
      activeChordTint: "rgba(236,72,153,0.10)",
      activeChordEdge: "rgba(236,72,153,0.28)",
      keyLabelText: "#4a1d3f",
      keyLabelBg: "rgba(255,245,247,0.92)",
      keyLabelStroke: "rgba(74,29,63,0.30)",
      panelOverlay: "rgba(255, 245, 247, 0.55)",
      panelOverlayHeavy: "rgba(254, 230, 236, 0.88)",
      noteEmphasis: "rgba(74,29,63,0.80)",
      noteEdge: "rgba(236,72,153,0.45)",
      chordOutline: "rgba(126, 34, 206, 0.85)",
      chordTint: "rgba(15, 118, 110, 0.7)",
    },
    // Sunny — light-bg family. Ocean teal + sun yellow on warm sand.
    sunny: {
      noteRH: "#0891b2",          // ocean teal
      noteLH: "#d97706",          // amber sun
      pedalRgb: "8, 145, 178",    // teal pedal
      barLineMajor: "rgba(30,58,77,0.30)",
      barLineMinor: "rgba(30,58,77,0.09)",
      barLabel: "rgba(30,58,77,0.55)",
      gridFaint: "rgba(251,191,36,0.10)",
      activeChordTint: "rgba(8,145,178,0.10)",
      activeChordEdge: "rgba(8,145,178,0.28)",
      keyLabelText: "#1e3a4d",
      keyLabelBg: "rgba(255,248,225,0.92)",
      keyLabelStroke: "rgba(30,58,77,0.30)",
      panelOverlay: "rgba(255,248,225,0.55)",
      panelOverlayHeavy: "rgba(254,240,200,0.88)",
      noteEmphasis: "rgba(30,58,77,0.80)",
      noteEdge: "rgba(8,145,178,0.45)",
      chordOutline: "rgba(126, 34, 206, 0.85)",
      chordTint: "rgba(180, 83, 9, 0.65)",
    },
    // Sky — light-bg family. Azure + sun yellow on light blue.
    sky: {
      noteRH: "#2563eb",          // azure
      noteLH: "#f59e0b",          // sun yellow
      pedalRgb: "37, 99, 235",    // azure pedal
      barLineMajor: "rgba(30,58,138,0.30)",
      barLineMinor: "rgba(30,58,138,0.09)",
      barLabel: "rgba(30,58,138,0.55)",
      gridFaint: "rgba(255,255,255,0.45)",  // cloud-white grid against sky
      activeChordTint: "rgba(37,99,235,0.10)",
      activeChordEdge: "rgba(37,99,235,0.28)",
      keyLabelText: "#1e3a8a",
      keyLabelBg: "rgba(219,234,254,0.92)",
      keyLabelStroke: "rgba(30,58,138,0.30)",
      panelOverlay: "rgba(219,234,254,0.55)",
      panelOverlayHeavy: "rgba(198,220,253,0.88)",
      noteEmphasis: "rgba(30,58,138,0.80)",
      noteEdge: "rgba(37,99,235,0.45)",
      chordOutline: "rgba(126, 34, 206, 0.85)",
      chordTint: "rgba(217, 119, 6, 0.65)",
    },
  };
  const _VALID_THEMES = new Set(["dark", "light", "forest", "sakura", "sunny", "sky"]);
  const _LIGHT_BG_THEMES = new Set(["light", "sakura", "sunny", "sky"]);
  let _currentTheme = localStorage.getItem("livechord_theme");
  if (!_VALID_THEMES.has(_currentTheme)) _currentTheme = "dark";
  function _palette() { return _THEME_PALETTE[_currentTheme] || _THEME_PALETTE.dark; }
  function _isLightBg() { return _LIGHT_BG_THEMES.has(_currentTheme); }
  // RH waterfall/keyboard content: "acc" = accompaniment only, "mel" = vocal
  // melody only, "both" = merge. Learner can flip to practice either line;
  // "both" is the power-user overlay view. Default to "acc" (what the hand
  // actually plays if following the arrangement).
  const _RH_MODES = ["acc", "mel", "both"];
  let rhContentMode = localStorage.getItem("livechord_rh_mode") || "acc";
  if (!_RH_MODES.includes(rhContentMode)) rhContentMode = "acc";

  // Resolve right-hand events according to rhContentMode. Single source of truth
  // consumed by waterfall rendering, audio scheduler, and MIDI export so the
  // three paths never disagree.
  function _melodyDuration(m) {
    const dur = Number(m && m.duration);
    if (Number.isFinite(dur) && dur > 0) return dur;
    const start = Number(m && m.start);
    const end = Number(m && m.end);
    if (Number.isFinite(start) && Number.isFinite(end) && end > start) return end - start;
    return 0.25;
  }

  function _melodyNoteEvent(m) {
    const time = Number(m && (m.time != null ? m.time : m.start));
    const midi = Number(m && (m.pitch != null ? m.pitch : m.midi));
    return {
      time: Number.isFinite(time) ? time : 0,
      duration: Math.max(0.05, _melodyDuration(m)),
      pitch: Number.isFinite(midi) ? Math.round(midi) : 60,
      finger: null,
      gate_ratio: Number.isFinite(Number(m && m.gate_ratio)) ? Number(m.gate_ratio) : 1.0,
      voice_lane: "rh_melody",
      schema_version: 2,
    };
  }

  function _resolveRhEvents() {
    if (typeof accData === 'undefined' || !accData) {
      if (typeof melodyData !== 'undefined' && melodyData && rhContentMode !== "acc") {
        return melodyData.map(_melodyNoteEvent);
      }
      return [];
    }
    const accRh = accData.right_hand || [];
    const hasAcc = accRh.length > 0;
    const wantAcc = rhContentMode !== "mel" && hasAcc;
    const wantMel = rhContentMode === "mel" || rhContentMode === "both" || !hasAcc;
    let out = wantAcc ? accRh.slice() : [];
    if (wantMel && typeof melodyData !== 'undefined' && melodyData) {
      out = out.concat(melodyData.map(_melodyNoteEvent));
    }
    return out;
  }

  // Practice-mode suffix for downloaded MIDI filenames.
  function _practiceModeSuffix() {
    if (typeof activeHand === 'undefined') return "";
    if (activeHand === "left") return "L";
    if (activeHand === "right") {
      if (rhContentMode === "mel") return "Rmel";
      if (rhContentMode === "both") return "Rfull";
      return "R";
    }
    // both hands
    if (rhContentMode === "mel") return "mel";
    if (rhContentMode === "both") return "full";
    return "";
  }
  function _syncRhContentBtn() {
    const lab = document.getElementById("btnRhContentLabel");
    if (lab) lab.textContent = _t("player.teach.rh_content_" + rhContentMode) || _t("player.teach.rh_content_acc");
    const btn = document.getElementById("btnRhContent");
    if (btn) btn.title = _t("player.rh_content.title", {
      mode: _t("player.rh_content." + rhContentMode),
    });
  }
  let showFingering = localStorage.getItem("livechord_show_fingering") !== "false"; // 預設開啟
  let teachStyle = localStorage.getItem("livechord_teach_style") || "Auto";
  let teachLevel = localStorage.getItem("livechord_teach_level") || "L1";
  if (!["L1", "L2", "L3"].includes(teachLevel)) teachLevel = "L1";
  let accData = null;  // {left_hand:[], right_hand:[]} from API
  let _beatPhase = 0;  // beat grid phase offset (seconds)
  let _accStaleWarned = false;  // single-shot stale-acc toast guard (Phase 2)
  let currentSecPerBeat = 0.6; // For chord dot lighting
  let _currentBpmMult = 1.0;   // Updated by _buildUnifiedRibbon; read by _virtualBeats
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

  // ---- Local audio file support (beta / remote tester) ----
  const localFileInput = $("#localFileInput");
  const localAudioPrompt = $("#localAudioPrompt");
  const btnLocalFilePrompt = $("#btnLocalFilePrompt");
  const btnLocalFile = $("#btnLocalFile");
  const tbLocalFile = $("#tbLocalFile");
  let _localFileObjectUrl = null;
  let _usingLocalFile = false;

  function _triggerLocalFilePicker() {
    if (localFileInput) localFileInput.click();
  }
  if (btnLocalFilePrompt) btnLocalFilePrompt.addEventListener("click", _triggerLocalFilePicker);
  if (btnLocalFile) btnLocalFile.addEventListener("click", _triggerLocalFilePicker);

  if (localFileInput) {
    localFileInput.addEventListener("change", (e) => {
      const file = e.target.files[0];
      if (!file) return;
      // Revoke previous object URL to prevent memory leak
      if (_localFileObjectUrl) URL.revokeObjectURL(_localFileObjectUrl);
      _localFileObjectUrl = URL.createObjectURL(file);
      _usingLocalFile = true;
      audio.src = _localFileObjectUrl;
      audio.load();
      // Hide the prompt overlay
      if (localAudioPrompt) localAudioPrompt.style.display = "none";
      // Show badge in title to indicate local file
      const badge = songTitle.querySelector(".local-file-badge");
      if (!badge) {
        const b = document.createElement("span");
        b.className = "local-file-badge";
        b.textContent = "LOCAL";
        songTitle.appendChild(b);
      }
      // Auto-play
      audio.play().catch(() => {});
    });
  }

  // Fetch public config once and share the promise across the module
  const _configPromise = fetch("/api/config/public").then(r => r.json()).catch(() => ({}));
  const _isBetaModeAsync = _configPromise.then(cfg => cfg.deployment_mode === "beta");
  const _isDetectDisabledModeAsync = _configPromise.then(cfg =>
    cfg.deployment_mode === "beta" || cfg.deployment_mode === "public"
  );

  function _maybeStartPlayerTutorial() {
    if (!hasChords || !window.LiveChordTutorial) return;
    window.LiveChordTutorial.maybeStart("player");
  }

  // Show local-file toolbar button: always in hash mode, otherwise beta mode only
  if (hashMode && tbLocalFile) {
    tbLocalFile.style.display = "";
  } else {
    _isBetaModeAsync.then(isBeta => {
      if (isBeta && tbLocalFile) tbLocalFile.style.display = "";
    });
  }

  // When audio stream fails (NAS not reachable), show the prompt
  if (audio) {
    audio.addEventListener("error", () => {
      if (_usingLocalFile) return; // don't show prompt for local file errors
      if (localAudioPrompt) localAudioPrompt.style.display = "flex";
      if (tbLocalFile) tbLocalFile.style.display = "";
    });
  }
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

  // Chord JSON's stored duration field — used by length-mismatch warnings
  // when the user loads a local audio file. 0 = unknown / skip the check.
  let _chordDuration = 0;

  // ── AI accompaniment synth (Web Audio) ─────────────────────────────────
  // SampleSynth handles two engine types per SAMPLE_MANIFEST entry:
  //   - "sample":     fetch + decode pitched samples (Salamander piano)
  //   - "oscillator": cheap differentiation via Web Audio oscillator + ADSR
  // getActiveSynth() resolves the right instance per active tab + user
  // override (livechord_sound_<tab>). All synths share volLeft/volRight via
  // _applyVolToAllSynths so applyAudioMode() does the right thing across
  // tab/sound switches. Hoisted near the top of the IIFE so applyAudioMode()
  // during init never ReferenceErrors — same constraint as the old
  // PianoSynth (yt-removal stage 3 / cb584b8 regression).

  const SAMPLE_MANIFEST = {
    "grand-piano": {
      label: "Grand Piano", labelZh: "平台鋼琴", family: "keyboard",
      type: "sample",
      // CDN baseline: tonejs.github.io Salamander samples (CC-BY-3.0).
      // Local override at audio/samples/grand-piano/ shadows this once
      // present (Phase 4b will populate it).
      baseUrl: "https://tonejs.github.io/audio/salamander/",
      localBaseUrl: "/audio/samples/grand-piano/",
      notes: [21,24,27,30,33,36,39,42,45,48,51,54,57,60,63,66,69,72,75,78,81,84,87,90,93,96,99,102,105,108],
      nameFormat: "sharps", extension: "mp3",
      gainScalar: 1.0,
    },
    "upright-piano": {
      label: "Upright Piano", labelZh: "直立鋼琴", family: "keyboard",
      type: "oscillator", oscType: "triangle", detune: 4,
      attack: 0.005, decay: 0.45, sustainLevel: 0.25, release: 0.45,
      gainScalar: 0.85,
    },
    "rhodes": {
      label: "Rhodes", labelZh: "Rhodes 電鋼琴", family: "keyboard",
      type: "oscillator", oscType: "sine", bellHarmonic: 4, bellMix: 0.18,
      attack: 0.008, decay: 0.7, sustainLevel: 0.35, release: 0.7,
      gainScalar: 0.95,
    },
    "wurlitzer": {
      label: "Wurlitzer", labelZh: "Wurlitzer 電鋼琴", family: "keyboard",
      type: "oscillator", oscType: "triangle", bellHarmonic: 3, bellMix: 0.25,
      attack: 0.005, decay: 0.55, sustainLevel: 0.4, release: 0.5,
      gainScalar: 0.9,
    },
    "organ": {
      label: "Organ", labelZh: "電風琴", family: "keyboard",
      type: "sample",
      localBaseUrl: "/audio/samples/organ/",
      notes: [24,27,30,33,36,39,42,45,48,51,54,57,60,63,66,69,72,75,78,81,84],
      extension: "mp3",
      gainScalar: 0.6,
    },
    "nylon-guitar": {
      label: "Nylon Guitar", labelZh: "古典吉他", family: "string",
      type: "sample",
      localBaseUrl: "/audio/samples/nylon-guitar/",
      notes: [35,38,40,42,44,45,47,49,50,52,54,55,57,59,61,63,64,66,68,69,71,73,74,76,78,79,80,81,82],
      extension: "mp3",
      gainScalar: 1.0,
    },
    "steel-guitar": {
      label: "Steel Guitar", labelZh: "鋼弦吉他", family: "string",
      type: "sample",
      localBaseUrl: "/audio/samples/steel-guitar/",
      notes: [38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61,62,63,64,65,66,67,68,69,70,71,72,73,74],
      extension: "mp3",
      gainScalar: 0.95,
    },
    "accordion": {
      label: "Accordion", labelZh: "手風琴", family: "wind",
      type: "sample",
      localBaseUrl: "/audio/samples/accordion/",
      notes: [36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61,62,63,64,65,67,68,69,70,71,72,73,74],
      extension: "mp3",
      gainScalar: 0.7,
    },
    "synth-pad": {
      label: "Synth Pad", labelZh: "合成器", family: "synth",
      type: "oscillator", oscType: "sawtooth", detune: 8,
      attack: 0.4, decay: 0.3, sustainLevel: 0.7, release: 1.4,
      gainScalar: 0.4,
    },
  };

  const DEFAULT_TAB_SOUND = {
    piano:     "grand-piano",
    guitar:    "nylon-guitar",
    ukulele:   "steel-guitar",
    accordion: "accordion",
    arranger:  "synth-pad",
  };

  class SampleSynth {
    constructor(soundId) {
      this.soundId = soundId;
      this.spec = SAMPLE_MANIFEST[soundId] || SAMPLE_MANIFEST["grand-piano"];
      this.ctx = null;
      this.masterGain = null;
      this.volLeft = 1;
      this.volRight = 1;
      this.samples = {};
      this.loading = false;
      this.loaded = (this.spec.type !== "sample"); // oscillator synths don't need loading
      // For sample synths, prefer local hosted samples; CDN is the bootstrap
      // fallback. We probe local on first load and only fall back if 404.
      this._cdnBaseUrl = this.spec.baseUrl || null;
      this._localBaseUrl = this.spec.localBaseUrl || null;
      this._sampleNotes = this.spec.notes || [];
    }

    _noteToName(midi) {
      const names = ['C','Cs','D','Ds','E','F','Fs','G','Gs','A','As','B'];
      const oct = Math.floor(midi / 12) - 1;
      return names[midi % 12] + oct;
    }

    async _loadOneSample(note, baseUrl) {
      const name = this._noteToName(note);
      const url = baseUrl + name + "." + (this.spec.extension || "mp3");
      const resp = await fetch(url);
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const buf = await resp.arrayBuffer();
      this.samples[note] = await this.ctx.decodeAudioData(buf);
    }

    async _loadSamples() {
      if (this.loading || this.loaded) return;
      this.loading = true;

      // Probe one note from local first; if it works, use local for the rest.
      // Otherwise fall back to CDN for the entire set.
      let baseUrl = this._localBaseUrl;
      if (this._localBaseUrl && this._sampleNotes.length) {
        try {
          await this._loadOneSample(this._sampleNotes[0], this._localBaseUrl);
        } catch {
          baseUrl = this._cdnBaseUrl;
        }
      } else {
        baseUrl = this._cdnBaseUrl;
      }

      if (!baseUrl) {
        this.loaded = true;
        this.loading = false;
        return;
      }

      const promises = this._sampleNotes
        .filter(n => !this.samples[n])
        .map(async (note) => {
          try { await this._loadOneSample(note, baseUrl); }
          catch (e) { /* leave gap; _findClosestSample skips missing */ }
        });
      await Promise.all(promises);
      this.loaded = true;
      this.loading = false;
      console.log(`[${this.soundId}] samples loaded:`, Object.keys(this.samples).length, "notes");
    }

    _findClosestSample(pitch) {
      // Only consider notes that actually decoded successfully
      const available = Object.keys(this.samples).map(Number);
      if (!available.length) return null;
      let best = available[0];
      let bestDist = Math.abs(pitch - best);
      for (const n of available) {
        const d = Math.abs(pitch - n);
        if (d < bestDist) { bestDist = d; best = n; }
      }
      return best;
    }

    init() {
      if (!this.ctx) {
        this.ctx = new (window.AudioContext || window.webkitAudioContext)();
        this.masterGain = this.ctx.createGain();
        this.masterGain.gain.value = 0.5 * (this.spec.gainScalar || 1.0);
        this.masterGain.connect(this.ctx.destination);
        if (this.spec.type === "sample") this._loadSamples();
      }
    }

    _peakGain(hand, velocity) {
      const vol = hand === "left" ? this.volLeft : this.volRight;
      if (vol <= 0) return 0;
      const velGain = Math.max(0.15, Math.min(1.0, (velocity || 64) / 100));
      const handBias = hand === "left" ? 0.55 : 1.1;
      return vol * 0.75 * velGain * handBias;
    }

    _gateWindow(duration, gateRatio, releaseTail) {
      const canonicalDuration = Math.max(0.01, Number(duration) || 0.01);
      const rawGate = Number(gateRatio);
      const gate = Number.isFinite(rawGate) ? Math.max(0.05, Math.min(1.0, rawGate)) : 1.0;
      const maxTail = Math.max(0.005, canonicalDuration * 0.5);
      const tail = Math.min(Math.max(0.005, Number(releaseTail) || 0.05), maxTail);
      let audioOffOffset = canonicalDuration * gate;
      if (audioOffOffset + tail > canonicalDuration) {
        audioOffOffset = Math.max(0, canonicalDuration - tail);
      }
      return {
        canonicalDuration,
        audioOffOffset,
        releaseTail: tail,
        stopOffset: Math.min(canonicalDuration, audioOffOffset + tail),
      };
    }

    _playSampleNote(pitch, duration, startTime, peakGain, gateRatio) {
      const sampleNote = this._findClosestSample(pitch);
      if (sampleNote === null) {
        // Samples not yet decoded — fall through to oscillator
        this._playOscillatorNote(pitch, duration, startTime, peakGain, {
          oscType: pitch < 60 ? "triangle" : "sine",
          attack: 0.02, decay: duration * 0.5, sustainLevel: 0.3, release: 0.05,
        }, gateRatio);
        return;
      }
      const gate = this._gateWindow(duration, gateRatio, 0.15);
      const buffer = this.samples[sampleNote];
      const source = this.ctx.createBufferSource();
      source.buffer = buffer;
      source.playbackRate.value = Math.pow(2, (pitch - sampleNote) / 12);
      const gain = this.ctx.createGain();
      source.connect(gain);
      gain.connect(this.masterGain);
      const audioOff = startTime + gate.audioOffOffset;
      const stopTime = startTime + gate.stopOffset;
      gain.gain.setValueAtTime(peakGain, startTime);
      gain.gain.setValueAtTime(peakGain, Math.max(startTime, audioOff - Math.min(0.08, gate.audioOffOffset)));
      gain.gain.linearRampToValueAtTime(0, stopTime);
      source.start(startTime);
      source.stop(stopTime);
    }

    _playOscillatorNote(pitch, duration, startTime, peakGain, specOverride, gateRatio) {
      const spec = specOverride || this.spec;
      const freq = 440 * Math.pow(2, (pitch - 69) / 12);
      const osc = this.ctx.createOscillator();
      osc.type = spec.oscType || "sine";
      osc.frequency.value = freq;
      if (spec.detune) osc.detune.value = spec.detune;

      const gain = this.ctx.createGain();
      osc.connect(gain);

      // Optional bell-harmonic mix-in (Rhodes / Wurlitzer character)
      let bellOsc = null;
      if (spec.bellHarmonic && spec.bellMix) {
        bellOsc = this.ctx.createOscillator();
        bellOsc.type = "sine";
        bellOsc.frequency.value = freq * spec.bellHarmonic;
        const bellGain = this.ctx.createGain();
        bellGain.gain.value = spec.bellMix;
        bellOsc.connect(bellGain);
        bellGain.connect(gain);
      }

      // Optional tremolo (accordion vibrato)
      let tremoloLFO = null, tremoloGain = null;
      if (spec.tremoloHz && spec.tremoloDepth) {
        tremoloLFO = this.ctx.createOscillator();
        tremoloLFO.frequency.value = spec.tremoloHz;
        tremoloGain = this.ctx.createGain();
        tremoloGain.gain.value = spec.tremoloDepth;
        tremoloLFO.connect(tremoloGain);
        tremoloGain.connect(gain.gain);
      }

      gain.connect(this.masterGain);

      // ADSR envelope. Held duration absorbs decay→sustain→release tail.
      const A = Math.max(0.001, spec.attack || 0.01);
      const D = Math.max(0.001, spec.decay || 0.1);
      const S = (spec.sustainLevel != null) ? spec.sustainLevel : 0.5;
      const R = Math.max(0.01, spec.release || 0.1);
      const sustainPeak = peakGain * S;
      const gate = this._gateWindow(duration, gateRatio, R);
      const releaseStart = startTime + gate.audioOffOffset;
      const stopTime = startTime + gate.stopOffset;
      const attackEnd = Math.min(startTime + A, Math.max(startTime + 0.001, releaseStart));
      const sustainStart = Math.min(startTime + A + D, releaseStart);

      gain.gain.setValueAtTime(0, startTime);
      gain.gain.linearRampToValueAtTime(peakGain, attackEnd);
      if (sustainStart > attackEnd + 0.001) {
        gain.gain.exponentialRampToValueAtTime(Math.max(sustainPeak, 0.001), sustainStart);
      }
      gain.gain.setValueAtTime(Math.max(sustainPeak, 0.001), releaseStart);
      gain.gain.exponentialRampToValueAtTime(0.0001, stopTime);

      osc.start(startTime);
      osc.stop(stopTime);
      if (bellOsc) { bellOsc.start(startTime); bellOsc.stop(stopTime); }
      if (tremoloLFO) { tremoloLFO.start(startTime); tremoloLFO.stop(stopTime); }
    }

    playNote(pitch, duration, hand, startTime, velocity, gateRatio) {
      if (!this.ctx) return;
      if (typeof activeHand !== "undefined" && activeHand !== "both" && activeHand !== hand) return;
      const peakGain = this._peakGain(hand, velocity);
      if (peakGain <= 0) return;
      if (this.spec.type === "sample") {
        this._playSampleNote(pitch, duration, startTime, peakGain, gateRatio);
      } else {
        this._playOscillatorNote(pitch, duration, startTime, peakGain, null, gateRatio);
      }
    }

    // Click-to-play preview: a single note triggered by a user gesture on the
    // instrument canvas. Deliberately independent of the playback path —
    // ignores activeHand AND volLeft/volRight (which applyAudioMode() zeroes in
    // Music mode), so a preview is always audible regardless of the audio-mode
    // mix. Uses a fixed peak gain comparable to a mid-velocity played note.
    previewNote(pitch, duration = 0.9) {
      this.init();
      if (!this.ctx) return;
      if (this.ctx.state === "suspended") { try { this.ctx.resume(); } catch {} }
      const peakGain = 0.55;
      const startTime = this.ctx.currentTime + 0.005;
      if (this.spec.type === "sample") {
        this._playSampleNote(pitch, duration, startTime, peakGain, 0.95);
      } else {
        this._playOscillatorNote(pitch, duration, startTime, peakGain, null, 0.95);
      }
    }
  }

  // Synth instance pool — at most one instance per soundId, kept around so
  // tab/sound switching is instantaneous (no re-decode). 9 sounds × ~3 MB
  // worst-case sample memory ≈ 30 MB if user explores everything, acceptable.
  const _synthCache = {};
  function _ensureSynth(soundId) {
    if (!SAMPLE_MANIFEST[soundId]) soundId = "grand-piano";
    if (!_synthCache[soundId]) {
      _synthCache[soundId] = new SampleSynth(soundId);
      // Inherit current vol from any existing instance so audio-mode state
      // persists through tab switches.
      const any = Object.values(_synthCache).find(s => s.soundId !== soundId);
      if (any) {
        _synthCache[soundId].volLeft = any.volLeft;
        _synthCache[soundId].volRight = any.volRight;
      }
    }
    return _synthCache[soundId];
  }
  function _resolveActiveSoundId() {
    const tab = (typeof activeTab !== "undefined" && activeTab) || localStorage.getItem("livechord_tab") || "piano";
    const stored = (() => { try { return localStorage.getItem("livechord_sound_" + tab); } catch { return null; } })();
    return stored || DEFAULT_TAB_SOUND[tab] || "grand-piano";
  }
  function getActiveSynth() {
    return _ensureSynth(_resolveActiveSoundId());
  }
  // Click-to-play: sound the given MIDI pitch on the ACTIVE tab's instrument
  // sound (piano→piano, guitar→nylon-guitar, accordion→accordion, …). Shared
  // by the 88-key keyboard, the string fretboards, and the accordion bass grid
  // via the player bridge. Always audible (see SampleSynth.previewNote).
  function _previewNote(pitch) {
    if (typeof pitch !== "number" || !Number.isFinite(pitch)) return;
    try { getActiveSynth().previewNote(Math.round(pitch)); } catch (_) {}
  }
  function _applyVolToAllSynths(volL, volR) {
    for (const s of Object.values(_synthCache)) {
      s.volLeft = volL; s.volRight = volR;
    }
  }

  // aiSynth is a let alias so it can be re-pointed by getActiveSynth() on
  // each scheduler tick / play handler. Bootstrapped to the default piano
  // synth so any code that reads aiSynth before scheduleNotes() runs (rare)
  // still gets a valid object.
  let aiSynth = _ensureSynth(_resolveActiveSoundId());
  let lastScheduledTime = 0;
  let _melodyPollAbort = null;
  let _melodyPollTimeout = null;

  function _stopMelodyPolling() {
    if (_melodyPollAbort) { try { _melodyPollAbort.abort(); } catch {} _melodyPollAbort = null; }
    if (_melodyPollTimeout) { clearTimeout(_melodyPollTimeout); _melodyPollTimeout = null; }
  }
  window.addEventListener("pagehide", _stopMelodyPolling);
  function _maybeStartMelodyPolling() {
    if (!hashMode) return;
    const raw = sessionStorage.getItem("livechord_fresh_hash") || "";
    const [h, tsStr] = raw.split("|");
    const ts = parseInt(tsStr || "0", 10);
    if (h !== hashMode || !ts || Date.now() - ts > 10 * 60000) return;

    // Toast on every fresh-analysis arrival — even acc-mode users should know
    // a background job started, per loose-coupling rule (see CLAUDE.md
    // "Long-running operations" — start/done notifications mandatory).
    // Toast replaces the previous bottom-left status banner (the start/done
    // toast pair already covers the same information, banner was redundant).
    showToast(_t("toast.melody.started"), 5000);

    _melodyPollAbort = new AbortController();
    const signal = _melodyPollAbort.signal;
    const deadline = Date.now() + 5 * 60000;
    const tick = async () => {
      if (signal.aborted) return;
      if (Date.now() > deadline) {
        _stopMelodyPolling();
        // Loose-coupling rule: surface timeouts so users don't wonder why
        // their melody silently never showed up.
        showToast(_t("toast.melody.timeout"), 5000);
        return;
      }
      try {
        const r = await fetch(`/api/ai/melody?hash=${encodeURIComponent(hashMode)}`, { signal });
        if (signal.aborted) return;
        const d = await r.json();
        if (d.melody && d.melody.length > 0) {
          melodyData = _filterMelody(d.melody);
          _stopMelodyPolling();
          // Always toast completion (loose-coupling rule). The phrasing
          // tells the user where to find the result, not just "done".
          showToast(_t("toast.melody.done"), 5000);
          return;
        }
      } catch (e) {
        if (e && e.name === "AbortError") return;
      }
      if (signal.aborted) return;
      _melodyPollTimeout = setTimeout(tick, 5000);
    };
    _melodyPollTimeout = setTimeout(tick, 8000);  // 8s head start for the melody worker
  }

  // Chord-quality LED: combines data-source hint with user rating summary.
  // Plain-Chinese labels so users don't need to know "BTC" / "MIDI":
  //   來源：AI 偵測 (btc*) · 人工校對 (midi) · 人工匯入 (chordify)
  // Color priority:
  //   - count ≥ 3: rating-driven (avg ≥4 綠, ≥3 黃, <3 紅)
  //   - count <  3: source-driven fallback (midi 綠, btc 黃)
  // Source-label keys live in the i18n dict under player.chord_src.<key>;
  // we look them up via _t() so they switch with the user's language.
  async function _updateChordQualityBadge(cd, ratingKey) {
    const srcBadge = document.getElementById("chordSource");
    if (!srcBadge) return;
    const rawSrc = (cd && cd.source) || "btc";
    const srcKey = ["midi", "btc", "btc_upload", "btc_batch", "chordy", "chordify"]
      .includes(rawSrc) ? `player.chord_src.${rawSrc}` : "player.chord_src.unknown";
    const srcLabel = _t(srcKey);
    const srcShort = rawSrc.startsWith("btc") ? "AI"
                    : rawSrc === "midi" ? _t("player.chord_src.badge_corrected")
                    : rawSrc === "chordify" ? _t("player.chord_src.badge_corrected") : "?";
    // Paint immediately with source-based fallback
    const sourceCssClass =
      rawSrc === "midi" ? "src-midi"
      : rawSrc === "chordify" ? "src-midi"
      : "src-btc";
    srcBadge.className = `chord-source-badge ${sourceCssClass}`;
    srcBadge.textContent = srcShort;
    srcBadge.title = _t("player.chord_src.title_simple", { src: srcLabel });

    // Fetch user rating summary. The rating table's song_hash column just
    // stores whatever the UI submitted — trackPath in DB-path mode, the md5
    // hash in hash mode — so we pass through whichever is active.
    const key = ratingKey || "";
    if (!key) return;
    try {
      const r = await fetch(`/api/feedback/ratings/summary?song_hash=${encodeURIComponent(key)}`);
      if (!r.ok) return;
      const { average, count } = await r.json();
      const MIN_COUNT = 3;
      if (average && count >= MIN_COUNT) {
        const avg = Number(average);
        const ratingClass =
          avg >= 4 ? "src-good"
          : avg >= 3 ? "src-mid"
          : "src-bad";
        srcBadge.className = `chord-source-badge ${ratingClass}`;
        // No "★" glyph — would clash visually with the song-card difficulty
        // stars rendered in the chord ribbon. The colored badge background
        // (good=green, mid=amber, bad=red) already conveys "this is a rating".
        srcBadge.textContent = avg.toFixed(1);
        srcBadge.title = _t("player.chord_src.title_rated",
          { stars: avg.toFixed(1), count, src: srcLabel });
      } else if (count > 0) {
        srcBadge.title = _t("player.chord_src.title_few",
          { count, src: srcLabel });
      } else {
        srcBadge.title = _t("player.chord_src.title_no_rating", { src: srcLabel });
      }
    } catch {}
  }

  // Compute trusted album-track duration from chord JSON. Only returns a value
  // when the JSON has an explicit .duration (saved by the process worker via
  // mutagen audio.info.length, or backfilled by chord_batch). Last-chord-end
  // was a false-positive trap — an outro with no detected chord made a 2:34
  // song look 1:39 long, triggering a bogus desync banner. Returning 0 here
  // lets the desync check short-circuit via its `< 30` gate.
  function _computeChordDuration(c) {
    if (!c) return 0;
    if (typeof c.duration === "number" && c.duration > 0) return c.duration;
    return 0;
  }

  // Unified playback accessors. Audio element is the only playback path now.
  function _playerCurrentTime() { return audio.currentTime || 0; }
  function _playerDuration() { return audio.duration || 0; }
  function _playerSeek(t) { audio.currentTime = t; }

  // ---- A-B Repeat state ----
  const btnABRepeat = $("#btnABRepeat");
  let abState = "idle";  // idle → a_set → active
  let abA = null;        // start time (seconds)
  let abB = null;        // end time (seconds)

  function _updateABRangeUI() {
    const d = _playerDuration() || 1;
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

  function _updateABPopup() {
    const el = document.getElementById("abStatus");
    if (!el) return;
    if (abState === "active" && abA != null && abB != null) {
      el.textContent = _t("ab.range", { a: formatTime(abA), b: formatTime(abB) });
    } else if (abState === "a_set" && abA != null) {
      el.textContent = _t("ab.range_pending_b", { a: formatTime(abA) });
    } else {
      el.textContent = _t("ab.unset");
    }
  }

  // Reset the in-memory A-B state + UI. Does NOT touch localStorage — that's
  // only cleared on explicit user action (the "清除" button), so page reloads
  // and track switches keep the persisted phrase choice alive.
  function _clearABRepeat() {
    abState = "idle";
    abA = null;
    abB = null;
    if (btnABRepeat) {
      btnABRepeat.classList.remove("a-set", "ab-active");
      btnABRepeat.textContent = "A-B";
    }
    // Reset strip selection + highlights; default to manual mode
    _abSelectedSet.clear();
    _paintPills();
    const manualRow = document.getElementById("abManualRow");
    if (manualRow) manualRow.style.display = "flex";
    const _popup = document.querySelector(".tb-popup-ab");
    if (_popup) _popup.classList.remove("ab-semi-set");
    _updateABRangeUI();
    _updateABPopup();
  }

  // User-facing "clear": resets state AND forgets persisted choice for
  // THIS song only. Called when user toggles off the last phrase pill OR
  // clicks the 🗑 in manual A/B mode. Per-song scope — other songs keep
  // their own A-B persistence.
  function _forgetABChoice() {
    _clearABRepeat();
    try {
      const k = _abStorageKey();
      if (k) localStorage.removeItem(k);
    } catch {}
  }

  // Snap a phrase boundary (start or end) to the chord grid — both A and B
  // should align to real chord changes, not section-detector approximations.
  // sections[].start/end from section detection can be several seconds off.
  function _phraseStartOf(secs, idx) {
    const sectionStart = secs[idx].start;
    if (chordData && Array.isArray(chordData.chords)) {
      for (const c of chordData.chords) {
        if (c.time >= sectionStart) return c.time;
      }
    }
    return sectionStart;
  }

  // Compute the true end boundary of a phrase. Same snap strategy as start:
  // find the first chord whose time is >= next-section.start — that chord's
  // time is the actual start of the next phrase, hence this phrase's end.
  function _phraseEndOf(secs, idx) {
    // Non-last section: snap to next phrase's first chord start
    if (idx + 1 < secs.length) {
      const nextSectionStart = secs[idx + 1].start;
      if (chordData && Array.isArray(chordData.chords)) {
        for (const c of chordData.chords) {
          if (c.time >= nextSectionStart) return c.time;
        }
      }
      return nextSectionStart;
    }
    // Last section: use full duration or last chord's end
    if (chordData && chordData.duration) return chordData.duration;
    if (chordData && Array.isArray(chordData.chords) && chordData.chords.length) {
      const last = chordData.chords[chordData.chords.length - 1];
      return (last.end != null) ? last.end : last.time;
    }
    return secs[idx].end;
  }

  // Chord-level snap for manual A/B: users clicking "設定 A" mean "include
  // the chord currently playing from its start"; clicking "設定 B" mean
  // "include that chord through its end". Snap accordingly.
  function _snapForABPoint(t, which) {
    if (chordData && Array.isArray(chordData.chords) && chordData.chords.length) {
      const chords = chordData.chords;
      for (let i = 0; i < chords.length; i++) {
        const c = chords[i];
        const start = c.time;
        const end = (c.end != null) ? c.end :
                    (i + 1 < chords.length ? chords[i + 1].time : start + 4);
        if (t >= start && t < end) {
          return which === "B" ? end : start;
        }
      }
      // After last chord: B -> last chord's end; A -> last chord's start
      const last = chords[chords.length - 1];
      if (t >= last.time) {
        return which === "B" ? ((last.end != null) ? last.end : last.time) : last.time;
      }
    }
    return t;
  }

  // Phrase practice via `< [select] >` picker. Persists the user's last choice
  // per song so reopening the same track restores where they were practising.
  const _AB_STORAGE_KEY_PREFIX = "livechord_ab_phrase:";

  function _abStorageKey() {
    const path = (typeof trackPath !== "undefined" && trackPath) ? trackPath :
                 (typeof hashMode !== "undefined" && hashMode) ? `__hash/${hashMode}` : "";
    return path ? (_AB_STORAGE_KEY_PREFIX + path) : "";
  }
  function _saveABChoice(value) {
    try {
      const k = _abStorageKey();
      if (k) localStorage.setItem(k, value);
    } catch {}
  }
  function _loadABChoice() {
    try {
      const k = _abStorageKey();
      return k ? localStorage.getItem(k) : null;
    } catch { return null; }
  }

  // Map LiveChordI18n.getLang() → internal "zh" | "en" for label rendering.
  // The phrase strip and chord-ribbon section headers used to toggle between
  // these on click; that's gone now (per user request 2026-05-02), and the
  // language follows the global i18n picker via livechord:langchange.
  function _currentPhraseLang() {
    try {
      const g = (window.LiveChordI18n && window.LiveChordI18n.getLang) ? window.LiveChordI18n.getLang() : "en";
      return g === "zh-TW" ? "zh" : "en";
    } catch { return "en"; }
  }

  // Build the phrase labels the same way the chord ribbon does so the two
  // UIs stay consistent: types appearing more than once are numbered
  // (Verse 1, Verse 2, Chorus 1, ...); intro/outro/dialogue stay unnumbered.
  // Returns parallel arrays [{zh, en}] so the AB strip can stamp both onto
  // each pill and re-render on language change without a re-build.
  function _buildPhraseLabelPair(secs) {
    const NO_NUMBER = ["intro", "outro", "dialogue"];
    const totals = {};
    secs.forEach(s => {
      const t = s.type || s.label || "";
      totals[t] = (totals[t] || 0) + 1;
    });
    const occur = {};
    return secs.map((s, i) => {
      const t = s.type || s.label || "";
      occur[t] = (occur[t] || 0) + 1;
      const baseZh = s.label || s.type || _t("player.phrase_fallback", { n: i + 1 });
      // English derived from type, mirroring the chord-ribbon logic at
      // _renderRibbon (`baseType` strip digits/apostrophes, capitalize,
      // replace underscore with space). Keeps "verse" → "Verse",
      // "instrumental" → "Instrumental", "pre_chorus" → "Pre chorus".
      const baseTypeRaw = (s.type || "").replace(/\d+|'/g, "");
      const baseEn = baseTypeRaw
        ? baseTypeRaw.charAt(0).toUpperCase() + baseTypeRaw.slice(1).replace(/_/g, " ")
        : (s.label || `Phrase ${i + 1}`);
      const numbered = totals[t] > 1 && !NO_NUMBER.includes((t || "").toLowerCase());
      const num = numbered ? ` ${occur[t]}` : "";
      return { zh: `${baseZh}${num}`, en: `${baseEn}${num}` };
    });
  }

  // Canonical (English) labels — stable identifier used for AB persistence,
  // because storing the rendered string in localStorage would break across
  // language switches.
  function _buildPhraseLabels(secs) {
    return _buildPhraseLabelPair(secs).map(p => p.en);
  }

  // Toggle-based multi-select: any pill tap flips its on/off state. The loop
  // window is the continuous [min, max] span of all selected indices — pills
  // between selected endpoints display `.in-range` to show they're included
  // in the loop (multi-segment loop not yet implemented).
  const _abSelectedSet = new Set();

  function _escHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  function _paintPills() {
    const wrap = document.querySelector(".ab-strip-wrap");
    if (!wrap) return;
    const pills = wrap.querySelectorAll(".ab-phrase-pill");
    const sorted = Array.from(_abSelectedSet).sort((a, b) => a - b);
    const lo = sorted.length ? sorted[0] : null;
    const hi = sorted.length ? sorted[sorted.length - 1] : null;
    pills.forEach(p => {
      p.classList.remove("endpoint", "in-range", "active");
      const idxAttr = p.getAttribute("data-idx");
      if (idxAttr === "manual") return;
      const i = parseInt(idxAttr, 10);
      if (_abSelectedSet.has(i)) p.classList.add("endpoint");
      else if (lo != null && hi != null && i > lo && i < hi) p.classList.add("in-range");
    });
  }

  function _markManualPillActive() {
    const wrap = document.querySelector(".ab-strip-wrap");
    if (!wrap) return;
    wrap.querySelectorAll(".ab-phrase-pill").forEach(p => p.classList.remove("endpoint", "in-range", "active"));
    const manualPill = wrap.querySelector('.ab-phrase-pill[data-idx="manual"]');
    if (manualPill) manualPill.classList.add("active");
  }

  function _buildPhraseStrip() {
    const strip = document.getElementById("abPhraseStrip");
    if (!strip) return;
    const secs = (sectionData && Array.isArray(sectionData.sections)) ? sectionData.sections : [];
    const pairs = _buildPhraseLabelPair(secs);
    const lang = _currentPhraseLang();
    const html = pairs.map((p, i) =>
      `<button class="ab-phrase-pill" data-idx="${i}" data-zh="${_escHtml(p.zh)}" data-en="${_escHtml(p.en)}" role="option">${_escHtml(p[lang])}</button>`
    );
    strip.innerHTML = html.join("");

    // Bind once via event delegation on .ab-strip-wrap. Manual pill is static
    // HTML (lives in .ab-manual-col sibling) so plain addEventListener on it
    // would accumulate across _buildPhraseStrip() re-runs. Delegation avoids it.
    const wrap = document.querySelector(".ab-strip-wrap");
    if (wrap && !wrap._lcPillHandlerBound) {
      wrap.addEventListener("click", (e) => {
        const pill = e.target.closest(".ab-phrase-pill");
        if (!pill || !wrap.contains(pill)) return;
        e.stopPropagation();
        _onPhrasePillTap(pill.getAttribute("data-idx"));
      });
      wrap._lcPillHandlerBound = true;
    }

    _restoreABFromPersist();

    // Fallback for songs without section detection (e.g. many 8801 tracks):
    // if the strip is empty AND nothing was restored from persist, drop into
    // manual mode so the user immediately sees the A/B buttons.
    if (pairs.length === 0 && _abSelectedSet.size === 0) {
      _applyManualMode({silent: true});
    }
  }

  // Keep old export name so _loadSections still wires through correctly
  window._lcPopulatePhraseSelect = _buildPhraseStrip;

  function _onPhrasePillTap(value) {
    if (value === "manual") {
      _applyManualMode();
      return;
    }
    const idx = parseInt(value, 10);
    const secs = (sectionData && Array.isArray(sectionData.sections)) ? sectionData.sections : [];
    if (!(idx >= 0 && idx < secs.length)) return;

    // Toggle: add if not present, remove if already selected
    if (_abSelectedSet.has(idx)) _abSelectedSet.delete(idx);
    else _abSelectedSet.add(idx);

    if (_abSelectedSet.size === 0) {
      // No selection → user deactivated the loop. Forget persistence so
      // navigating back doesn't auto-restore the just-unchecked phrase
      // (user feedback 2026-04-19: "deactivating = disable auto-restore").
      _forgetABChoice();
      _paintPills();
      return;
    }
    _applyFromSelectedSet();
  }

  function _clearABLoopOnly() {
    abState = "idle";
    abA = null;
    abB = null;
    if (btnABRepeat) {
      btnABRepeat.classList.remove("a-set", "ab-active");
      btnABRepeat.textContent = "A-B";
    }
    const _popup = document.querySelector(".tb-popup-ab");
    if (_popup) _popup.classList.remove("ab-semi-set");
    _updateABRangeUI();
    _updateABPopup();
  }

  function _applyFromSelectedSet(opts = {}) {
    const secs = (sectionData && Array.isArray(sectionData.sections)) ? sectionData.sections : [];
    if (!secs.length || _abSelectedSet.size === 0) return;
    const sorted = Array.from(_abSelectedSet).sort((a, b) => a - b);
    const lo = sorted[0];
    const hi = sorted[sorted.length - 1];
    abA = _phraseStartOf(secs, lo);
    abB = _phraseEndOf(secs, hi);
    abState = "active";
    if (btnABRepeat) {
      btnABRepeat.classList.remove("a-set");
      btnABRepeat.classList.add("ab-active");
      btnABRepeat.textContent = "A-B \u2713";
    }
    const _popup = document.querySelector(".tb-popup-ab");
    if (_popup) _popup.classList.remove("ab-semi-set");
    const manualRow = document.getElementById("abManualRow");
    if (manualRow) manualRow.style.display = "none";

    _paintPills();

    const labels = _buildPhraseLabels(secs);
    const selectedLabels = sorted.map(i => labels[i]);
    const name = (sorted.length === 1)
      ? labels[lo]
      : `${labels[lo]} – ${labels[hi]}`;

    if (!opts.silent) {
      _playerSeek(abA);
      showToast(_t("toast.ab.loop", { name, from: formatTime(abA), to: formatTime(abB) }), 2000);
      // Persist as either single label or JSON array of selected labels
      if (sorted.length === 1) {
        _saveABChoice(labels[lo]);
      } else {
        _saveABChoice(JSON.stringify({selected: selectedLabels}));
      }
      if (typeof updateActiveChord === "function") updateActiveChord(abA, true);
      // Scroll strip so the most recently-affected endpoint stays visible
      const strip = document.getElementById("abPhraseStrip");
      if (strip) {
        const pill = strip.querySelector(`.ab-phrase-pill[data-idx="${hi}"]`);
        if (pill) pill.scrollIntoView({inline: "center", block: "nearest", behavior: "smooth"});
      }
    }
    _updateABRangeUI();
    _updateABPopup();
  }

  function _applyManualMode(opts = {}) {
    _abSelectedSet.clear();
    _markManualPillActive();
    const manualRow = document.getElementById("abManualRow");
    if (manualRow) manualRow.style.display = "flex";
    // Manual mode keeps any user-set A/B untouched
    if (!opts.silent) _saveABChoice("manual");
    _updateABPopup();
  }

  function _restoreABFromPersist() {
    const secs = (sectionData && Array.isArray(sectionData.sections)) ? sectionData.sections : [];
    const saved = _loadABChoice();
    _abSelectedSet.clear();
    if (!saved) return;
    if (saved === "manual") {
      _applyManualMode({silent: true});
      return;
    }
    const labels = _buildPhraseLabels(secs);
    // Format: {"selected": ["Verse 1", "Chorus 1"]} (new toggle-based)
    //         {"start": "...", "end": "..."} (legacy 3-tap range)
    //         plain label string (single phrase)
    try {
      const obj = JSON.parse(saved);
      if (obj && Array.isArray(obj.selected)) {
        obj.selected.forEach(lbl => {
          const idx = labels.indexOf(lbl);
          if (idx >= 0) _abSelectedSet.add(idx);
        });
        if (_abSelectedSet.size > 0) { _applyFromSelectedSet({silent: true}); return; }
      } else if (obj && obj.start && obj.end) {
        const si = labels.indexOf(obj.start);
        const ei = labels.indexOf(obj.end);
        if (si >= 0 && ei >= 0) {
          // Legacy range: select both endpoints (in-range auto-shown)
          _abSelectedSet.add(si);
          if (si !== ei) _abSelectedSet.add(ei);
          _applyFromSelectedSet({silent: true});
          return;
        }
      }
    } catch {}
    // Plain label — single selection
    const matchIdx = labels.indexOf(saved);
    if (matchIdx >= 0) {
      _abSelectedSet.add(matchIdx);
      _applyFromSelectedSet({silent: true});
    } else {
      _applyManualMode({silent: true});  // label vanished after section edit
    }
  }

  function _handleAB(action) {
    if (action === "clear") {
      _forgetABChoice();
      showToast(_t("toast.ab.cleared"), 1500);
      return;
    }
    if (action === "A") {
      const t = _snapForABPoint(_playerCurrentTime(), "A");
      abA = t;
      if (abB != null && t >= abB) abB = null;  // invalidate B if new A is past it
      abState = (abB != null) ? "active" : "a_set";
      if (btnABRepeat) {
        btnABRepeat.classList.remove("a-set", "ab-active");
        if (abState === "active") {
          btnABRepeat.classList.add("ab-active");
          btnABRepeat.textContent = "A-B \u2713";
        } else {
          btnABRepeat.classList.add("a-set");
          btnABRepeat.textContent = "A-\u23F8";
        }
      }
      // Semi-set: keep popup open AND let clicks pass through empty areas so
      // the user can seek via progress bar without first dismissing the popup.
      const _popup = document.querySelector(".tb-popup-ab");
      if (_popup) {
        if (abState === "a_set") _popup.classList.add("ab-semi-set");
        else _popup.classList.remove("ab-semi-set");
      }
      showToast("A \u9EDE: " + formatTime(t), 1500);
      _updateABRangeUI();
      _updateABPopup();
      return;
    }
    if (action === "B") {
      if (abA == null) {
        showToast("\u8ACB\u5148\u8A2D\u5B9A A \u9EDE", 1500);
        return;
      }
      const t = _snapForABPoint(_playerCurrentTime(), "B");
      if (t <= abA) {
        showToast("B \u9EDE\u5FC5\u9808\u5728 A \u9EDE\u4E4B\u5F8C", 1500);
        return;
      }
      abB = t;
      abState = "active";
      if (btnABRepeat) {
        btnABRepeat.classList.remove("a-set");
        btnABRepeat.classList.add("ab-active");
        btnABRepeat.textContent = "A-B \u2713";
      }
      // Leave semi-set mode — popup is pointer-events:auto again and will
      // dismiss normally on outside click.
      const _popup = document.querySelector(".tb-popup-ab");
      if (_popup) _popup.classList.remove("ab-semi-set");
      _playerSeek(abA);
      showToast("A-B \u5FAA\u74B0: " + formatTime(abA) + " \u2192 " + formatTime(abB), 2000);
      _updateABRangeUI();
      _updateABPopup();
      return;
    }
  }

  // Touch-detection gate for state-cycle toolbar buttons (A-B, Loop, Speed,
  // Jazzify). On touch the cycle handlers no-op so the popup picker takes
  // over; on desktop they advance the state. Definition was dropped in
  // 45b72e3 but the 5 call-sites remained — desktop click → ReferenceError.
  // ontouchstart alone misses Chromium DevTools emulation; pointer:coarse
  // covers it.
  const _isTouchLike =
    ('ontouchstart' in window) ||
    (typeof matchMedia === "function" && matchMedia('(pointer:coarse)').matches);

  if (btnABRepeat) {
    btnABRepeat.addEventListener("click", () => {
      if (_isTouchLike) return;  // touch devices use the popup (see .ab-opt handlers)
      // Desktop fast cycle: idle → A → B → clear
      const t = _playerCurrentTime();
      if (abState === "idle") {
        _handleAB("A");
      } else if (abState === "a_set") {
        if (t <= abA) {
          showToast("B \u9EDE\u5FC5\u9808\u5728 A \u9EDE\u4E4B\u5F8C", 1500);
          return;
        }
        _handleAB("B");
      } else {
        _handleAB("clear");
      }
    });
  }
  document.querySelectorAll(".ab-opt").forEach(b => {
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      _handleAB(b.dataset.ab);
      const item = b.closest(".tb-item");
      if (item) item.classList.remove("open");
    });
  });
  // Build the phrase strip now. sectionData may still be null (first paint);
  // _loadSections will call _lcPopulatePhraseSelect -> _buildPhraseStrip again
  // after the /api/ai/sections response arrives.
  _buildPhraseStrip();

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
    // Sound picker tracks per-tab choice — surface the override (or default)
    // for the new tab so the user sees what's actually playing.
    if (typeof window._syncSoundPickerValue === "function") {
      try { window._syncSoundPickerValue(); } catch {}
    }

    // Set displayMode based on tab
    if (tab === "guitar") displayMode = "guitar";
    else if (tab === "ukulele") displayMode = "ukulele";
    else if (tab === "accordion") displayMode = "accordion";
    else if (tab === "arranger") displayMode = "arranger";
    else displayMode = "piano";

    // Update instrument trigger icon. Accordion uses an inline SVG (matches the
    // popup button) because the U+1FA97 emoji renders inconsistently across OSes.
    // Redesigned 2026-05-06 (LiveChord-c9d follow-up): the previous icon was
    // two thin tall rects + tiny chevrons, which read as a dumbbell at 14-18px.
    // Now uses squarer 6×12 body panels + 3 prominent zigzag pleats spanning
    // the full middle height, plus a hint of bass-buttons (left dots) and key
    // lines (right) so the accordion shape is unmistakable on both PC + mobile.
    const ACCORDION_SVG = '<svg class="tb-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="6" width="6" height="12" rx="1"/><rect x="16" y="6" width="6" height="12" rx="1"/><circle cx="5" cy="9.5" r="0.9" fill="currentColor" stroke="none"/><circle cx="5" cy="12.5" r="0.9" fill="currentColor" stroke="none"/><circle cx="5" cy="15.5" r="0.9" fill="currentColor" stroke="none"/><line x1="19" y1="8" x2="19" y2="16"/><path d="M8 7 L11 12 L8 17"/><path d="M11 7 L14 12 L11 17"/><path d="M14 7 L16 12 L14 17"/></svg>';
    // v6 follow-up: ALL tabs use inline tb-icon SVG so the trigger renders
    // identically on every OS \u2014 matching the other 11 toolbar icons. Pre-fix,
    // iconMap mixed emoji with one SVG (only accordion was SVG because U+1FA97
    // renders inconsistently across platforms): on Android/iOS the piano /
    // guitar / uke / arranger emoji fell back to colored Noto, so the bottom
    // toolbar had four colourful icons and one stroke icon. User feedback:
    // "icon not consistent with the others" \u2014 fixed by giving every tab its
    // own inline tb-icon stroke SVG.
    const PIANO_SVG = '<svg class="tb-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="6" width="20" height="12" rx="1.5"/><line x1="7" y1="6" x2="7" y2="18"/><line x1="12" y1="6" x2="12" y2="18"/><line x1="17" y1="6" x2="17" y2="18"/><rect x="5.3" y="6" width="3.4" height="6.5" fill="currentColor" stroke="none"/><rect x="15.3" y="6" width="3.4" height="6.5" fill="currentColor" stroke="none"/></svg>';
    // Guitar / ukulele: vertical orientation, headstock at top, body at
    // bottom, sound hole filled. The first cut had a diagonal connector
    // between an off-center body and a top-right headstock — at 14-18px
    // that read as a snail (round body + antenna). Symmetrical layout reads
    // as a guitar even when small.
    const GUITAR_SVG = '<svg class="tb-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="2" width="6" height="2.5" rx="0.5"/><line x1="12" y1="4.5" x2="12" y2="13"/><line x1="10.5" y1="6.5" x2="13.5" y2="6.5"/><line x1="10.5" y1="9" x2="13.5" y2="9"/><line x1="10.5" y1="11.5" x2="13.5" y2="11.5"/><ellipse cx="12" cy="17.5" rx="6.5" ry="5"/><circle cx="12" cy="17.5" r="1.6" fill="currentColor" stroke="none"/></svg>';
    const UKULELE_SVG = '<svg class="tb-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="10" y="2" width="4" height="2" rx="0.4"/><line x1="12" y1="4" x2="12" y2="11"/><line x1="10.8" y1="6" x2="13.2" y2="6"/><line x1="10.8" y1="8.5" x2="13.2" y2="8.5"/><ellipse cx="12" cy="16" rx="5" ry="6"/><circle cx="12" cy="16" r="1.4" fill="currentColor" stroke="none"/></svg>';
    const ARRANGER_SVG = '<svg class="tb-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="5" cy="4.5" r="1" fill="currentColor" stroke="none"/><circle cx="9.5" cy="4.5" r="1" fill="currentColor" stroke="none"/><circle cx="14.5" cy="4.5" r="1" fill="currentColor" stroke="none"/><circle cx="19" cy="4.5" r="1" fill="currentColor" stroke="none"/><rect x="2" y="9" width="20" height="9" rx="1.2"/><line x1="7" y1="9" x2="7" y2="18"/><line x1="12" y1="9" x2="12" y2="18"/><line x1="17" y1="9" x2="17" y2="18"/><rect x="5.3" y="9" width="3.4" height="5" fill="currentColor" stroke="none"/><rect x="15.3" y="9" width="3.4" height="5" fill="currentColor" stroke="none"/></svg>';
    const iconMap = {
      piano: PIANO_SVG,
      guitar: GUITAR_SVG,
      ukulele: UKULELE_SVG,
      accordion: ACCORDION_SVG,
      arranger: ARRANGER_SVG,
    };
    const btnInstrument = $("#btnInstrument");
    if (btnInstrument) {
      btnInstrument.innerHTML = iconMap[tab] || PIANO_SVG;
    }
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
      // Bug fix: _initWaterfall() is the piano-only path that previously
      // owned the _loadAccompaniment() call. On cold load with a non-piano
      // tab restored from livechord_tab (guitar / ukulele / accordion /
      // arranger), accData stayed null and the audio scheduler skipped
      // every tick — so AI accompaniment was silent until the user
      // bounced through the piano tab. Reproduction:
      //   piano (sound) → guitar → dashboard → click song → player
      //     → guitar tab restored, AI silent
      //   → switch to piano → AI plays → switch back to guitar → AI plays
      // Make the load tab-agnostic so non-piano tabs also bootstrap accData.
      // Internally _loadAccompaniment is idempotent on style/level cache,
      // so this is also safe on re-entry.
      _loadAccompaniment();
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
    const t = (abState === "active" && abA != null) ? abA : 0;
    _playerSeek(t);
  }

  // ---- Unified Ribbon Builder (vertical, piano-style, replaces overview) ----
  function _buildUnifiedRibbon() {
    if (!unifiedRibbonTrack) return;
    unifiedRibbonTrack.innerHTML = "";
    ribbonElements = [];

    const chords = _displayChords();
    if (!chords || chords.length === 0) return;

    // Prefer the BPM persisted by the editor / BTC pipeline; fall back to
    // the chord-spacing heuristic only when the JSON has no stored value.
    // Without this the player re-estimates every render — so editor splits
    // (which halve the median inter-chord diff) look like "BPM 跑掉" even
    // though the editor's songBPM and the saved JSON both agree.
    let estimatedBpm = 0;
    const displayBpm = chordData && typeof chordData.display_bpm === "number" && chordData.display_bpm > 0
      ? chordData.display_bpm : 0;
    if (displayBpm > 0) {
      estimatedBpm = displayBpm;
    } else if (chordData && typeof chordData.bpm === "number" && chordData.bpm > 0) {
      estimatedBpm = chordData.bpm;
    } else if (chords.length >= 4) {
      let diffs = [];
      for(let i=0; i<chords.length-1; i++) {
        let d = chords[i+1].time - chords[i].time;
        if (d > 0.3 && d < 6.0) diffs.push(d);
      }
      if (diffs.length > 0) {
        diffs.sort((a,b) => a-b);
        let median = diffs[Math.floor(diffs.length/2)];
        let beatSec = median;
        if (median > 1.8) beatSec = median / 4;
        else if (median > 0.9) beatSec = median / 2;
        estimatedBpm = 60 / beatSec;
      }
    }
    if (estimatedBpm <= 0) estimatedBpm = 100;
    
    const urlParamsBpm = new URLSearchParams(window.location.search);
    // Hash-mode players (livechord.org) have ?hash= but no ?path=, so the
    // old `get("path") || "default"` made every hash song share one
    // bpm_mult_default key — flipping BPM on song A would reappear on song B.
    let bpmPath = urlParamsBpm.get("path") || urlParamsBpm.get("hash") || "default";
    if (chordData && chordData.meter_correction && chordData.time_signature) {
      bpmPath = `${bpmPath}_meter_${chordData.time_signature}_${chordData.beat_version || 0}`;
    }
    let bpmMult = parseFloat(localStorage.getItem(`bpm_mult_${bpmPath}`)) || 1.0;
    _currentBpmMult = bpmMult;

    estimatedBpm = estimatedBpm * bpmMult;
    currentSecPerBeat = 60 / estimatedBpm;
    const secPerBeat = currentSecPerBeat;
    
    const bpmEl = document.getElementById("chordBpm");
    if (bpmEl) {
        const correction = chordData && chordData.bpm_correction;
        const halved = !!(correction && correction.applied);
        const barCorr = chordData && chordData.bar_correction;
        const barFixed = !!(barCorr && barCorr.applied);
        const globalArb = chordData && chordData.global_arbiter_meta;
        const displayBpmMeta = globalArb && globalArb.display_bpm;
        const globalBpm = !!(displayBpmMeta && displayBpm > 0);
        // ⓘ when either correction applied; tooltip composes both messages
        const showInfo = halved || barFixed || globalBpm;
        bpmEl.textContent = _bpmLabelForTime(audio.currentTime || 0, estimatedBpm, showInfo);
        bpmEl.style.cursor = "pointer";
        const lines = [];
        if (halved) {
            lines.push(_t("player.bpm.halved_notice",
              { orig: Math.round(correction.original || 0) }));
        }
        if (barFixed) {
            const bpb = barCorr.beats_per_bar || 4;
            const conf = (typeof barCorr.score_after === "number")
              ? _t("player.bar_arb.confidence_suffix",
                  { score: barCorr.score_after.toFixed(2) })
              : "";
            const tag = barCorr.model_version === "model_v1"
              ? _t("player.bar_arb.tag_ai")
              : _t("player.bar_arb.tag_rule");
            lines.push(_t("player.bar_arb.notice", { tag, bpb, conf }));
        }
        if (globalBpm) {
            lines.push(`Displayed BPM from song-level arbiter (${displayBpmMeta.source || "global"}). Stored BPM: ${Math.round(chordData.bpm || 0)}.`);
        }
        if (_meterLabel() === "6/8") {
            lines.push("Dynamic BPM is shown as the eighth-note pulse for 6/8 practice.");
        }
        lines.push(_t("player.bpm.click_to_cycle"));
        bpmEl.title = lines.join("\n");
        bpmEl.onclick = () => {
            if (bpmMult === 1.0) bpmMult = 0.5;
            else if (bpmMult === 0.5) bpmMult = 2.0;
            else bpmMult = 1.0;
            localStorage.setItem(`bpm_mult_${bpmPath}`, bpmMult);
            _buildUnifiedRibbon();
        };
    }

    const meterEl = document.getElementById("chordMeter");
    if (meterEl) {
        const meterLabel = _meterLabel();
        if (meterLabel) {
            const beatsPerBar = _meterBeatsPerBar();
            meterEl.textContent = `Meter: ${meterLabel}`;
            meterEl.title = beatsPerBar
              ? `${meterLabel}: ${beatsPerBar} practice beats per bar`
              : `${meterLabel} meter`;
            meterEl.style.display = "";
        } else {
            meterEl.textContent = "";
            meterEl.style.display = "none";
        }
    }

    // Build in time-ascending order: earliest chord top, latest bottom.
    // Active chord therefore moves top→bottom as the song plays — matches
    // the "reading a score" direction (past above, future below).
    // Previously rendered reverse; user feedback 2026-04-19 said vertical
    // mode felt backwards because active drifted upward over time.
    let lastSectionSec = null;
    let _prevMidi = null;

    // First pass: Pre-calculate total section occurrences for numbering
    let typeCounts = {};
    if (sectionData && sectionData.sections) {
        for (let s of sectionData.sections) {
            typeCounts[s.type] = (typeCounts[s.type] || 0) + 1;
        }
    }

    let typeOccurrences = {};
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
        let activeSec = sectionData.sections.find(s => c.time >= s.start - 0.5 && c.time < s.end);
        
        if (!activeSec && sectionData.sections.length > 0) {
            activeSec = sectionData.sections[sectionData.sections.length - 1];
        }
        if (activeSec) {
          phraseColor = activeSec.color || '#888';
          if (activeSec !== lastSectionSec) {
            lastSectionSec = activeSec;
            
            typeOccurrences[activeSec.type] = (typeOccurrences[activeSec.type] || 0) + 1;
            const count = typeOccurrences[activeSec.type];
            const total = typeCounts[activeSec.type] || 1;
            
            // Do not number intro, outro, dialogue unless they really appear multiple times
            const noNumberTypes = ['intro', 'outro', 'dialogue'];
            let numStr = "";
            if (total > 1 && !noNumberTypes.includes(activeSec.type)) {
                numStr = ` ${count}`;
            }
            
            const modeTag = activeSec.mode && activeSec.mode !== "Major" && activeSec.mode !== "Minor" ? ` ${activeSec.mode}` : "";
            const baseType = activeSec.type.replace(/\d+|'/g, ''); 
            const enType = baseType.charAt(0).toUpperCase() + baseType.slice(1).replace('_', ' ');
            
            phraseLabelZh = activeSec.label + numStr + modeTag;
            phraseLabelEn = enType + numStr + modeTag;
            
            sectionHdr = { 
                labelZh: phraseLabelZh, 
                labelEn: phraseLabelEn, 
                color: phraseColor,
                start: activeSec.start,
                type: activeSec.type
            };
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
        
        // Initial label follows the global LiveChordI18n picker. data-zh /
        // data-en stay so the langchange listener can re-render in place.
        gridPhraseEl.textContent = (_currentPhraseLang() === 'zh') ? phraseLabelZh : phraseLabelEn;

        gridPhraseEl.addEventListener("contextmenu", (e) => {
          if (typeof showSectionMenu === 'function' && sectionHdr) {
             let activeSec = null;
             if (sectionData && sectionData.sections) {
                 activeSec = sectionData.sections.find(s => Math.abs(s.start - sectionHdr.start) < 0.1);
             }
             // null for chordTime because they clicked the header, we strictly want to edit the boundary
             showSectionMenu(e, null, activeSec);
          }
        });
        item.appendChild(gridPhraseEl);
      }
      
      item.addEventListener("contextmenu", (e) => {
          if (typeof showSectionMenu === 'function') {
             let activeSec = null;
             if (sectionData && sectionData.sections) {
                 activeSec = sectionData.sections.find(s => c.time >= s.start - 0.5 && c.time < s.end);
             }
             if (!activeSec && sectionData && sectionData.sections && sectionData.sections.length > 0) {
                 activeSec = sectionData.sections[sectionData.sections.length - 1];
             }
             showSectionMenu(e, c.time, activeSec);
          }
      });
      
      item.addEventListener("click", () => {
        audio.currentTime = c.time;
        updateActiveChord(c.time, true);
      });

      const nameEl = document.createElement("div");
      nameEl.className = "rv-chord-name";
      nameEl.textContent = _displayChordName(c.chord);
      item.appendChild(nameEl);

      const strand = c && c.explain && typeof c.explain.strand === "string" ? c.explain.strand : "";
      if (strand) {
        const strandEl = document.createElement("div");
        strandEl.className = "rv-strand-tag";
        strandEl.textContent = _strandLabel(strand);
        strandEl.title = `strand: ${strand}`;
        item.appendChild(strandEl);
      }

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
      timeEl.textContent = formatTime(c.time, "centi");
      item.appendChild(timeEl);

      // 動態節拍指示器 (Dynamic Beat Indicator)
      // Prefer the chord's explicit `end` so the player agrees with the
      // editor (which renders block width + dot count from `end`). Falls
      // back to next-chord-start for legacy JSONs without `end`; if both
      // missing, 2s default.
      let durSec;
      if (c.end) {
          durSec = c.end - c.time;
      } else if (i < chords.length - 1) {
          durSec = chords[i+1].time - c.time;
      } else {
          durSec = 2.0;
      }
      item.dataset.end = (c.time + durSec).toFixed(4);

      const vb = _virtualBeats(durSec, c.time, c.auto_split, c.display_beats);
      const beatsEl = document.createElement("div");
      beatsEl.className = "rv-beats";
      const meterLabel = _meterLabel();
      const meterBeatsPerBar = _meterBeatsPerBar();
      const simpleTripleMeter = meterLabel === "3/4";
      const compoundSixEight = meterLabel === "6/8";
      if (simpleTripleMeter) {
          beatsEl.classList.add("rv-beats-simple-triple", `rv-meter-${meterLabel.replace("/", "-")}`);
          beatsEl.dataset.meter = meterLabel;
          beatsEl.title = `${meterLabel}: three visual pulses for this chord`;
      } else if (compoundSixEight) {
          beatsEl.classList.add("rv-beats-metered", "rv-meter-6-8", "rv-beats-compound");
          beatsEl.dataset.meter = meterLabel;
          beatsEl.dataset.beatsPerBar = String(meterBeatsPerBar || 6);
          beatsEl.title = `${meterLabel}: six eighth-note subdivisions, grouped as two pulses`;
      } else if (meterLabel && meterBeatsPerBar) {
          beatsEl.classList.add("rv-beats-metered", `rv-meter-${meterLabel.replace("/", "-")}`);
          beatsEl.dataset.meter = meterLabel;
          beatsEl.dataset.beatsPerBar = String(meterBeatsPerBar);
          beatsEl.title = `${meterLabel}: ${meterBeatsPerBar} practice beats per bar`;
      }
      let dotHtml = "";
      if (meterLabel && meterBeatsPerBar && !simpleTripleMeter && !compoundSixEight) {
          dotHtml += `<span class="rv-meter-badge">${meterLabel}</span>`;
      }
      const displayDots = simpleTripleMeter
        ? _buildVirtualDots(3, durSec, c.time)
        : vb.dots;
      for (let b = 0; b < displayDots.length; b++) {
          const d = displayDots[b];
          const cls = [
            "beat-dot",
            d.isDownbeat ? "is-downbeat" : "",
            d.beatInBar === 1 ? "is-meter-one" : "",
            compoundSixEight && d.beatInBar === 4 ? "is-compound-pulse" : "",
            d.startsBar ? "starts-bar" : "",
          ].filter(Boolean).join(" ");
          // data-time lets _updateBeatDots advance the highlight by real beat
          // times (not cardDur fraction × dotCount), so the dot pulses at the
          // tracker beat rate even when dot count diverges from elapsed beats.
          const beatAttr = d.beatInBar && !simpleTripleMeter ? ` data-beat="${d.beatInBar}"` : "";
          dotHtml += `<span class="${cls}" data-time="${d.t.toFixed(4)}"${beatAttr}></span>`;
      }
      beatsEl.innerHTML = dotHtml;
          item.insertBefore(beatsEl, nameEl);
      if (vb.short) item.classList.add("chord-short");
      if (vb.dots.length > 0 && vb.dots[0].isDownbeat) item.classList.add("chord-at-downbeat");

      items.push({ item, sectionHdr, idx: i });
    }

    // Append in time-ascending order (earliest first → top).
    for (let j = 0; j < items.length; j++) {
      const { item, sectionHdr } = items[j];
      if (sectionHdr) {
        const hdr = document.createElement("div");
        hdr.className = "rv-section-header";
        // Drive the colored border/text via the same var used on chord cards
        hdr.style.setProperty('--phrase-color', sectionHdr.color);

        const txt = (_currentPhraseLang() === 'zh') ? sectionHdr.labelZh : sectionHdr.labelEn;
        hdr.innerHTML = `<span class="rv-section-dot" style="background:${sectionHdr.color}"></span><span class="rv-section-text" data-zh="${sectionHdr.labelZh}" data-en="${sectionHdr.labelEn}">${txt}</span>`;

        // Section-header left click is reserved for the section-edit context
        // menu (right-click below) — we no longer toggle phrase language here
        // because phrase labels follow the global LiveChordI18n picker.
        // Add Edit context menu
        hdr.addEventListener("contextmenu", (e) => {
          if (typeof showSectionMenu === 'function') {
             showSectionMenu(e, sectionHdr.start, sectionHdr.type);
          }
        });
        
        unifiedRibbonTrack.appendChild(hdr);
      }
      unifiedRibbonTrack.appendChild(item);
    }

    // ribbonElements keeps normal index order for updateActiveChord
    ribbonElements = items.map(it => it.item);

    // Scroll to top on fresh load — DOM is now time-ascending (earliest first)
    // so chord 0 lives at the top of the scroll area. Previously scrolled to
    // bottom when DOM was reversed.
    if (chordRibbonPanel) {
      const isFreshLoad = activeChordIdx < 0 && (!audio || !audio.currentTime || audio.currentTime < 0.1);
      if (isFreshLoad) {
        requestAnimationFrame(() => {
          chordRibbonPanel.scrollTop = 0;
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
      // No-op drag when either side is fully collapsed — there's no
      // split to resize. The `.has-hidden-side` class is the single
      // source of truth for this state (set by _applyRibbonLayout).
      if (resizeHandle.classList.contains("has-hidden-side")) return;
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
      // Belt-and-braces relayout: chordDisplay88's ResizeObserver is debounced
      // 100 ms, which can race the next paint after drag-end — leaving the
      // waterfall buffer at pre-drag dimensions for a frame or two. Trigger an
      // immediate sync for the piano tab; other tabs handle sizing per frame.
      if (typeof activeTab !== "undefined" && activeTab === "piano") {
        try {
          _init88Piano();
          _resizeWaterfall();
          update88Piano(audio.currentTime || 0);
        } catch (_) { /* swallow — observer + RAF will recover next tick */ }
      }
    });
    resizeHandle.addEventListener("pointercancel", () => { _resizing = false; });

    // Restore saved width
    const savedRibbonW = parseInt(localStorage.getItem("livechord_ribbon_width"));
    if (savedRibbonW > 0) chordRibbonPanel.style.width = savedRibbonW + "px";
  }

  // ---- Ribbon diagram scale (+/−) ----
  // Scale persists per layout combo: portrait/landscape × normal/overview.
  // Four keys total (v_normal / v_overview / h_normal / h_overview). Was
  // per-instrument; user asked for per-layout because zoom preference
  // changes dramatically between phone-portrait and desktop-landscape,
  // and between compact vs overview grids.
  function _ribbonLayoutKey() {
    const o = window.matchMedia("(orientation: portrait)").matches ? "v" : "h";
    // `.ribbon-wide` (waterfall-hidden, ribbon-100%) uses the SAME grid
    // layout as `.overview-mode`, just at full width. User thinks of the
    // two as one "grid mode" and expects a single scale preference to
    // apply to both. Treating them as a shared key satisfies that.
    const isGrid = chordRibbonPanel && (
      chordRibbonPanel.classList.contains("overview-mode") ||
      chordRibbonPanel.classList.contains("ribbon-wide")
    );
    return `${o}_${isGrid ? "overview" : "normal"}`;
  }
  function _readRibbonScale(key) {
    const v = parseFloat(localStorage.getItem(`livechord_ribbon_scale_${key}`));
    return (v >= 0.1 && v <= 3) ? v : 1.0;
  }
  let ribbonScale = _readRibbonScale(_ribbonLayoutKey());
  const scaleLabel = $("#scaleLabel");

  function _loadRibbonScale() {
    ribbonScale = _readRibbonScale(_ribbonLayoutKey());
    _updateScaleLabel();
    if (chordRibbonPanel) chordRibbonPanel.style.setProperty("--ribbon-scale", ribbonScale);
  }
  function _updateScaleLabel() {
    if (scaleLabel) scaleLabel.textContent = ribbonScale.toFixed(1);
  }
  function _changeRibbonScale(delta) {
    ribbonScale = Math.round(Math.max(0.1, Math.min(3, ribbonScale + delta)) * 10) / 10;
    localStorage.setItem(`livechord_ribbon_scale_${_ribbonLayoutKey()}`, ribbonScale);
    _updateScaleLabel();
    if (chordRibbonPanel) chordRibbonPanel.style.setProperty("--ribbon-scale", ribbonScale);
    _buildUnifiedRibbon();
    updateActiveChord(audio.currentTime || 0, true);
  }
  const btnScaleUp = $("#btnScaleUp");
  const btnScaleDown = $("#btnScaleDown");
  if (btnScaleUp) btnScaleUp.addEventListener("click", () => _changeRibbonScale(0.1));
  if (btnScaleDown) btnScaleDown.addEventListener("click", () => _changeRibbonScale(-0.1));
  _updateScaleLabel();

  // Reapply scale on orientation flip (portrait ↔ landscape). Safari iOS
  // doesn't always emit matchMedia change; resize covers both.
  let _ribbonLastKey = _ribbonLayoutKey();
  window.addEventListener("resize", () => {
    const newKey = _ribbonLayoutKey();
    if (newKey !== _ribbonLastKey) {
      _ribbonLastKey = newKey;
      _loadRibbonScale();
    }
  });

  // ---- Overview Mode Toggle ----
  const btnToggleOverview = $("#btnToggleOverview");
  let isOverviewMode = localStorage.getItem("livechord_overview_mode") === "true";
  
  function _applyOverviewMode() {
      if (btnToggleOverview) btnToggleOverview.classList.toggle("active", isOverviewMode);
      if (chordRibbonPanel) {
        if (isOverviewMode) {
          chordRibbonPanel.classList.add("overview-mode");
        } else {
          chordRibbonPanel.classList.remove("overview-mode");
        }
      }
      // Score layer is hidden when ribbon is in overview-mode (overview itself
      // is the "全曲一覽" view — no need to also show sheet music on the right).
      // Forward-reference: defined further down with the other Score wiring.
      // try/catch shields the initial sync invocation (during player.js load),
      // when the Score block's const DOM refs are still in TDZ. The initial
      // computation is handled separately by setTimeout(_recomputeScoreEligible, 0).
      try { if (typeof _recomputeScoreEligible === "function") _recomputeScoreEligible(); } catch (_) {}
  }
  
  if (btnToggleOverview) {
    _applyOverviewMode();
    btnToggleOverview.addEventListener("click", (e) => {
      e.stopPropagation();
      isOverviewMode = !isOverviewMode;
      localStorage.setItem("livechord_overview_mode", isOverviewMode);
      _applyOverviewMode();
      // Layout flipped normal↔overview — reload the scale stored for the
      // new layout combo so users keep distinct zoom preferences.
      _ribbonLastKey = _ribbonLayoutKey();
      _loadRibbonScale();
      // .rv-item has a 0.2s CSS transition; wait past that before scrolling
      // so the active card's final position is measurable.
      setTimeout(() => {
        if (activeChordIdx >= 0 && activeChordIdx < ribbonElements.length && chordRibbonPanel) {
          const el = ribbonElements[activeChordIdx];
          // Manual scroll (instant) — scrollIntoView smooth tends to fight the CSS
          // transition and land wrong when the layout changes between row/grid.
          const target = el.offsetTop - (chordRibbonPanel.clientHeight - el.offsetHeight) / 2;
          chordRibbonPanel.scrollTop = Math.max(0, target);
        }
      }, 240);
    });
  }

  // Restore last tab (deferred here so _ribbonScales is initialized)
  _switchTab(activeTab);

  // ---- Two independent collapse toggles (one per side) ----
  // Mental model: the user has two "hide me" buttons on the divider. Each
  // collapses its own side and expands the other to full width. Invariant:
  // at most one side hidden at a time. Click "hide X" while Y is hidden
  // → auto-swap (Y restores, X hides) in a single click.
  const btnCollapseRibbon = $("#btnCollapseRibbon");
  const btnCollapseWaterfall = $("#btnCollapseWaterfall");
  // instrumentPanel is already declared at the top of the IIFE (line 194)

  let ribbonHidden = false;
  let waterfallHidden = false;

  // Migrate earlier state-cycle key + legacy boolean key, then clear them.
  // Reading both once; writing only the new per-side keys going forward.
  const legacyLayout = localStorage.getItem("livechord_ribbon_layout");
  const legacyVisible = localStorage.getItem("livechord_ribbon_visible");
  if (legacyLayout !== null) {
    const n = parseInt(legacyLayout);
    if (n === 1) ribbonHidden = true;
    else if (n === 2) waterfallHidden = true;
    localStorage.removeItem("livechord_ribbon_layout");
  } else {
    if (localStorage.getItem("livechord_ribbon_hidden") === "true") ribbonHidden = true;
    if (localStorage.getItem("livechord_waterfall_hidden") === "true") waterfallHidden = true;
    // Very-old pre-cycle key: treat as "ribbon hidden"
    if (!ribbonHidden && !waterfallHidden && legacyVisible === "false") ribbonHidden = true;
  }

  function _applyRibbonLayout() {
    // Touch devices have orientation-driven single-panel layouts (CSS hides
    // the other side via !important). Align JS state to match so the
    // inline `display:` written below doesn't fight the CSS.
    //   portrait → chord ribbon only (waterfall hidden)
    //   landscape → waterfall only (chord ribbon hidden)
    // Stale localStorage from a prior desktop session would otherwise
    // leave the wrong side hidden after orientation flip.
    const _isTouch = window.matchMedia("(pointer: coarse)").matches;
    if (_isTouch) {
      const _isPortraitTouch = window.matchMedia("(orientation: portrait)").matches;
      ribbonHidden = !_isPortraitTouch;       // hide ribbon in landscape
      waterfallHidden = _isPortraitTouch;     // hide waterfall in portrait
    }
    if (chordRibbonPanel) {
      chordRibbonPanel.style.display = ribbonHidden ? "none" : "";
      // When waterfall is hidden, ribbon takes the full row with a
      // wrap-grid reflow (same visual as overview-mode but at 100% width).
      chordRibbonPanel.classList.toggle("ribbon-wide", waterfallHidden);
    }
    if (instrumentPanel) instrumentPanel.style.display = waterfallHidden ? "none" : "";
    if (resizeHandle) resizeHandle.classList.toggle("has-hidden-side", ribbonHidden || waterfallHidden);

    // Button visibility + glyph. Rule: when a side is hidden, the OTHER
    // side's "hide me" button disappears (otherwise clicking it would
    // leave both sides hidden). Only the restore-direction button stays
    // visible in collapsed states. This keeps restore a pure undo —
    // click `>` from (0/100) goes back to the previous split (50/50 if
    // overview was on), NOT auto-swap to (100/0).
    //
    // Glyphs are fixed per-button regardless of state (user request): the
    // ribbon toggle is always `▶` and the waterfall toggle is always `◀`.
    // Only the tooltip reflects the next action.
    // Tooltip model: arrow direction = direction the adjacent panel will
    // expand. In both-visible state, `>` (left, adjacent to ribbon) makes
    // chords 100 % and `<` (right, adjacent to waterfall) makes waterfall
    // 100 %. In hidden state, the single remaining arrow points in the
    // direction the hidden panel will re-expand.
    // Orientation-aware glyphs: portrait split is vertical (chord on top,
    // waterfall on bottom) so use ▼/▲ to match the axis the user is folding
    // along; landscape stays ▶/◀. Re-runs on resize via the listener that
    // already calls _loadRibbonScale on orientation flip — extended below to
    // also re-apply this layout.
    const _isPortrait = window.matchMedia("(orientation: portrait)").matches;
    const _ribbonGlyph = _isPortrait ? "&#x25BC;" : "&#x276F;";   // ▼ / ▶
    const _waterfallGlyph = _isPortrait ? "&#x25B2;" : "&#x276E;"; // ▲ / ◀
    if (btnCollapseRibbon) {
      btnCollapseRibbon.style.display = waterfallHidden ? "none" : "";
      btnCollapseRibbon.innerHTML = _ribbonGlyph;
      btnCollapseRibbon.title = ribbonHidden
        ? _t("player.layout.expand_chords")
        : _t("player.layout.collapse_piano");
    }
    if (btnCollapseWaterfall) {
      btnCollapseWaterfall.style.display = ribbonHidden ? "none" : "";
      btnCollapseWaterfall.innerHTML = _waterfallGlyph;
      btnCollapseWaterfall.title = waterfallHidden
        ? _t("player.layout.expand_piano")
        : _t("player.layout.collapse_chords");
    }

    // Layout key depends on `.ribbon-wide` (which we just toggled); reload
    // the scale for the new key so the user's saved zoom for this mode is
    // applied. Without this, flipping to ribbon-wide kept the normal-mode
    // scale (usually 1.0) and cards stayed tiny in the grid view.
    if (typeof _loadRibbonScale === "function") {
      try { _loadRibbonScale(); } catch {}
    }
    if (typeof _ribbonLastKey !== "undefined") {
      _ribbonLastKey = _ribbonLayoutKey();
    }
    // Notify instrument modules that their canvas containers may have just
    // resized. Instrument code listens for this event and trailing-edge
    // debounces a redraw — ResizeObserver alone has been unreliable when
    // display:none ↔ "" transitions race with flex-driven width changes.
    try {
      document.dispatchEvent(new CustomEvent("livechord:panelresize"));
    } catch {}
  }

  function _persistRibbonLayout() {
    if (ribbonHidden) localStorage.setItem("livechord_ribbon_hidden", "true");
    else localStorage.removeItem("livechord_ribbon_hidden");
    if (waterfallHidden) localStorage.setItem("livechord_waterfall_hidden", "true");
    else localStorage.removeItem("livechord_waterfall_hidden");
  }

  _applyRibbonLayout();
  _persistRibbonLayout();  // writes migrated state, clears legacy keys' effect

  // Re-run on orientation flip so fold-button glyphs swap between ▶/◀
  // (landscape) and ▼/▲ (portrait). Cheap call — just rewrites button HTML
  // + display flags, no DOM rebuild.
  window.addEventListener("resize", _applyRibbonLayout);

  // Pure 2-state toggles — no auto-swap. The "other side is hidden"
  // case is handled by hiding the conflicting button in _applyRibbonLayout,
  // so these handlers only ever see the case where flipping is valid.
  // The two click handlers below are intentionally asymmetric between the
  // both-visible and hidden states, and each button's ID is a misnomer in
  // the both-visible branch (kept as-is to limit blast radius). Mental
  // model: the arrow points in the direction the adjacent visible panel
  // will expand, so `>` (left button, #btnCollapseRibbon) grows the ribbon
  // rightward → hide waterfall, and `<` (right, #btnCollapseWaterfall)
  // grows the waterfall leftward → hide ribbon. In the hidden state each
  // button acts as the restore for its own panel (unchanged).
  if (btnCollapseRibbon) {
    btnCollapseRibbon.addEventListener("click", (e) => {
      e.stopPropagation();
      if (ribbonHidden) ribbonHidden = false;          // restore ribbon
      else waterfallHidden = true;                      // both → chords 100 %
      _persistRibbonLayout();
      _applyRibbonLayout();
    });
  }
  if (btnCollapseWaterfall) {
    btnCollapseWaterfall.addEventListener("click", (e) => {
      e.stopPropagation();
      if (waterfallHidden) waterfallHidden = false;    // restore waterfall
      else ribbonHidden = true;                         // both → waterfall 100 %
      _persistRibbonLayout();
      _applyRibbonLayout();
    });
  }

  // ---- Top progress bar seek ----
  if (topProgressBar) {
    let _draggingTop = false;
    function _seekFromTopProgress(e) {
      const rect = topProgressBar.getBoundingClientRect();
      const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      const dur = _playerDuration();
      if (dur > 0) _playerSeek(pct * dur);
    }
    topProgressBar.addEventListener("pointerdown", (e) => {
      _draggingTop = true;
      try { topProgressBar.setPointerCapture(e.pointerId); } catch {}
      _seekFromTopProgress(e);
      e.preventDefault();
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
  // `ontouchstart in window` alone misses Chromium DevTools mobile emulation AND
  // some real devices — widen detection via matchMedia(pointer:coarse) so popups
  // actually toggle on tap.
  // Toolbar popup: always use click-to-pin pattern (no hover-dismiss).
  // Previously this was touch-only; PC hover-dismiss was annoying so everyone
  // now opens via click and stays open until an outside click.

  // Horizontal-edge clamp for toolbar popups — CSS handles max-width, this
  // handles POSITION overflow when the trigger is near the viewport edge.
  // Called right after .open is added so getBoundingClientRect sees the
  // actual rendered popup. Reset on close via a complementary reset call.
  function _clampPopupToViewport(popup) {
    if (!popup) return;
    // Clear any prior shift so measurement reflects the default center-on-trigger position.
    popup.style.transform = "";
    const rect = popup.getBoundingClientRect();
    const viewportW = window.innerWidth;
    const margin = 8;
    let shift = 0;
    if (rect.right > viewportW - margin) shift = (viewportW - margin) - rect.right;
    if (rect.left < margin)               shift = margin - rect.left;
    if (shift !== 0) {
      // Preserve the existing translateX(-50%) centering and add our shift.
      popup.style.transform = `translateX(calc(-50% + ${shift}px))`;
    }
  }
  // Re-clamp on viewport resize/orientation change so rotating a phone
  // mid-popup-open doesn't leave it clipping off-screen.
  window.addEventListener("resize", () => {
    document.querySelectorAll(".tb-item.open > .tb-popup").forEach(_clampPopupToViewport);
  });

  document.querySelectorAll(".tb-item").forEach(item => {
    const trigger = item.querySelector(".tb-trigger, a.tb-trigger");
    if (!trigger) return;
    const popup = item.querySelector(".tb-popup");
    const hasPopup = !!popup;
    // Inject a × close button into every toolbar popup so touch users don't
    // have to reach for an outside-tap to dismiss. Delegated click handler
    // below closes the nearest .tb-item.
    if (popup && !popup.querySelector(".tb-popup-close")) {
      const closeBtn = document.createElement("button");
      closeBtn.className = "tb-popup-close";
      closeBtn.setAttribute("aria-label", _t("common.close"));
      closeBtn.setAttribute("type", "button");
      closeBtn.innerHTML = "&times;";
      closeBtn.addEventListener("click", (e) => {
        e.stopPropagation();  // don't let .tb-popup's stopPropagation race us
        item.classList.remove("open");
      });
      popup.insertBefore(closeBtn, popup.firstChild);
    }
    if (hasPopup) {
      trigger.addEventListener("click", (e) => {
        // Close other open popups
        document.querySelectorAll(".tb-item.open").forEach(other => {
          if (other !== item) other.classList.remove("open");
        });
        // Also dismiss the bug-report modal if it's open — same "only one
        // floating layer" rule as between toolbar popups.
        const _bugDlg = document.getElementById("bugReportDialog");
        if (_bugDlg && _bugDlg.style.display !== "none") _bugDlg.style.display = "none";
        // Open-only — clicking the trigger never closes its own popup. Field
        // feedback: tapping the speaker icon a second time was dismissing the
        // volume popup unintentionally (icon sits adjacent to the slider on
        // mobile; thumb scrubs frequently brushed it). The × close button and
        // outside-tap remain the close paths.
        const wasOpen = item.classList.contains("open");
        item.classList.add("open");
        if (!wasOpen) _clampPopupToViewport(popup);
        e.stopPropagation();
      });
    }
    // Click delegation: if the tap lands on the tb-item's own padding/gap
    // (not on the trigger or its children), synthesize a click on the trigger.
    item.addEventListener("click", (e) => {
      if (e.target === item && trigger && !trigger.disabled) {
        e.stopPropagation();
        trigger.click();
      }
    });
  });
  // Clicks inside an open popup shouldn't close it (buttons inside work normally
  // via their own handlers but the popup container click bubbles up).
  document.querySelectorAll(".tb-popup").forEach(popup => {
    popup.addEventListener("click", e => e.stopPropagation());
  });
  // Track pointer-press origin so a drag that releases outside the popup
  // (e.g. volume slider thumb on mobile — popup is ~140px and slider track
  // ~114px so the thumb easily escapes the popup edge) doesn't synthesize a
  // click on <body> that the outside-click handler then uses to close.
  let _popupPressOrigin = null;
  document.addEventListener("pointerdown", (e) => {
    _popupPressOrigin = e.target;
  }, true);
  document.addEventListener("click", () => {
    document.querySelectorAll(".tb-item.open").forEach(i => {
      // Keep A-B popup open while user is in the middle of setting A→B manually
      // (so they can tap the progress bar to seek, then tap "設定 B").
      if (i.id === "tbAB" && abState === "a_set") return;
      // Press started inside this popup → drag-release click; don't close.
      if (_popupPressOrigin && i.contains(_popupPressOrigin)) return;
      i.classList.remove("open");
    });
    _popupPressOrigin = null;
  });
  function _appendQueueParams(qs) {
    if (!queueActive) return;
    qs.set("queue", queueSource);
    qs.set("queue_mode", queueMode);
    qs.set("queue_seed", queueSeed);
    if (queuePath) qs.set("queue_path", queuePath);
    if (queueGroup) qs.set("queue_group", queueGroup);
    if (queueStyle) qs.set("queue_style", queueStyle);
    if (queueLabel) qs.set("queue_label", queueLabel);
  }
  function _navUrl(path) {
    const qs = new URLSearchParams({ path, autoplay: "1" });
    if (document.fullscreenElement) qs.set("fs", "1");
    _appendQueueParams(qs);
    return `/player?${qs.toString()}`;
  }
  function _hasQueueNext() {
    return queueTracks.length > 0 && queueIndex >= 0 && queueIndex < queueTracks.length - 1;
  }
  function _hasQueuePrev() {
    return queueTracks.length > 0 && queueIndex > 0;
  }
  function _navPrev() {
    if (loopMode === "favorites" && favTracks.length > 0) {
      const i = favTracks.indexOf(trackPath);
      const prev = (i <= 0) ? favTracks.length - 1 : i - 1;
      window.location.href = _navUrl(favTracks[prev]);
    } else if (_hasQueuePrev()) {
      window.location.href = _navUrl(queueTracks[queueIndex - 1].path);
    } else if (siblingTracks.length > 0 && currentIndex > 0) {
      window.location.href = _navUrl(siblingTracks[currentIndex - 1].path);
    }
  }
  function _navNext() {
    if (loopMode === "favorites" && favTracks.length > 0) {
      const i = favTracks.indexOf(trackPath);
      const next = (i < 0 || i >= favTracks.length - 1) ? 0 : i + 1;
      window.location.href = _navUrl(favTracks[next]);
    } else if (_hasQueueNext()) {
      window.location.href = _navUrl(queueTracks[queueIndex + 1].path);
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

  function _displayKey(key) {
    return normalizeKeyForDisplay(key || "");
  }

  function _displayChordName(chord) {
    return normalizeChordNameForDisplay(chord || "");
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
    _setLoadingState(true, _t("loading.song"), _t("loading.song_detail"));
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

      currentChordVersion = null;
      await loadVersions(path);
      await loadChords(path, currentChordVersion);
      if (queueActive) {
        await loadQueue(path);
        if (!queueTracks.length) loadSiblings(path);
      } else {
        loadSiblings(path);
      }
    } finally {
      _setLoadingState(false);
      _maybeStartPlayerTutorial();
    }
  }

  async function loadSiblings(path) {
    const parts = path.split("/");
    parts.pop();
    const dir = parts.join("/");
    try {
      const data = await API.browse(dir);
      siblingTracks = data.entries.filter((e) => !e.is_dir && /\.(flac|mp3|wav|ogg)$/i.test(e.name));
      currentIndex = siblingTracks.findIndex((t) => t.path === path);
    } catch {}
  }

  async function loadQueue(path) {
    try {
      const data = await API.playlist({
        source: queueSource,
        mode: queueMode,
        seed: queueSeed,
        path: queuePath,
        group_id: queueGroup,
        style: queueStyle,
        limit: 20000,
      });
      queueTracks = (data.tracks || []).filter((e) => e && e.path);
      queueIndex = queueTracks.findIndex((t) => t.path === path);
    } catch (err) {
      console.warn("queue load failed:", err);
      queueTracks = [];
      queueIndex = -1;
    }
  }

  // ---- melody data (for Dynamic Lead Sheet) ----
  let melodyData = null;
  let _melodyPendingPlay = false;

  // Cleanse spurious melody notes + fold octave-drop errors.
  // Two-pass:
  //  1) Drop very-low-pitch + very-low-confidence (pitch tracker noise).
  //  2) Octave-fold: compute median MIDI and any note > 12 semitones away from it
  //     gets shifted by whole octaves until it's within ±1 octave. This fixes both
  //     (a) wide-range RH that's unplayable (Biol-101) and (b) octave-dropped vocal
  //     that collides with LH accompaniment (Taylor Swift — Fate of Ophelia).
  function _filterMelody(notes) {
    if (!Array.isArray(notes) || !notes.length) return notes;
    // Pass 1: confidence filter
    let kept = notes.filter(n => {
      const midi = n && n.midi;
      const conf = n && typeof n.confidence === "number" ? n.confidence : 1;
      if (midi == null) return false;
      if (midi < 48 && conf < 0.03) return false;
      return true;
    });
    if (!kept.length) return kept;
    // Pass 2: octave-fold around median
    const pitches = kept.map(n => n.midi).slice().sort((a, b) => a - b);
    const median = pitches[Math.floor(pitches.length / 2)];
    return kept.map(n => {
      let m = n.midi;
      while (m < median - 12) m += 12;
      while (m > median + 12) m -= 12;
      return m === n.midi ? n : { ...n, midi: m };
    });
  }

  // Path-mode melody load lifecycle — same pagehide-cancel discipline as the
  // hash-mode poller so browser-back during a ~1min uncached extraction
  // doesn't leave the previous page waiting on a zombie fetch.
  let _melodyLoadAbort = null;
  let _melodyLoadTimeout = null;
  function _stopMelodyLoad() {
    if (_melodyLoadAbort) { try { _melodyLoadAbort.abort(); } catch {} _melodyLoadAbort = null; }
    if (_melodyLoadTimeout) { clearTimeout(_melodyLoadTimeout); _melodyLoadTimeout = null; }
  }
  window.addEventListener("pagehide", _stopMelodyLoad);
  function _applyMelodyData(notes) {
    melodyData = _filterMelody(notes);
    if (typeof _scoreRedraw === "function") _scoreRedraw();
  }
  async function _loadMelody(path) {
    // Loose-coupling (CLAUDE.md "Long-running operations"): path-mode
    // library songs with no melody cache are extracted by a background
    // worker (backend melody_extract_queue) — the GET returns
    // {melody:[], pending:true} immediately instead of blocking the
    // request thread on Phase 11b's heavy extractor. We poll until the
    // cache lands, mirroring the hash-mode poller's discipline.
    const showUi = rhContentMode !== "acc";
    _stopMelodyLoad();
    _melodyLoadAbort = new AbortController();
    const signal = _melodyLoadAbort.signal;
    const url = `/api/ai/melody?path=${encodeURIComponent(path)}`;
    try {
      const res = await fetch(url, { signal });
      const data = await res.json();
      if (data.melody && data.melody.length > 0) {
        _applyMelodyData(data.melody);  // cache hit — instant, stay silent
        return;
      }
      if (!data.pending) return;  // genuinely no melody (e.g. file missing)
    } catch { return; }

    // Background extraction in flight — toast start, then poll for the cache.
    if (showUi) showToast(_t("toast.melody.started"), 5000);
    const deadline = Date.now() + 5 * 60000;
    const tick = async () => {
      if (signal.aborted) return;
      if (Date.now() > deadline) {
        _stopMelodyLoad();
        if (showUi) showToast(_t("toast.melody.timeout"), 5000);
        return;
      }
      try {
        const r = await fetch(url, { signal });
        if (signal.aborted) return;
        const d = await r.json();
        if (d.melody && d.melody.length > 0) {
          _applyMelodyData(d.melody);
          _stopMelodyLoad();
          if (showUi) showToast(_t("toast.melody.done"), 5000);
          return;
        }
      } catch (e) {
        if (e && e.name === "AbortError") return;
      }
      if (signal.aborted) return;
      _melodyLoadTimeout = setTimeout(tick, 5000);
    };
    _melodyLoadTimeout = setTimeout(tick, 8000);  // 8s head start for the worker
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
    const container = chordDisplay88 || pianoWaterfallView;
    const w = (container && container.clientWidth) || 800;
    // Derive keyboard height share from actual container height instead of
    // a hardcoded 178. The old constant capped width at ~1814 CSS px
    // regardless of container, producing blank L/R margins at waterfall 100%.
    // Floor 178 preserves the original min-height-200 clipping guarantee on
    // narrow panels; ceiling 280 stops a 4K-tall container from making the
    // keyboard visually dominate the waterfall (kh = kw*6).
    const containerH = (container && container.clientHeight) || 200;
    const reservedH = Math.max(178, Math.min(280, Math.round(containerH * 0.3)));
    return Math.min(w, Math.round(reservedH / 5.1 * 52));
  }

  // ---- Click-to-play: 88-key keyboard ----
  // Transient "pressed" highlights for clicked keys. Map midi -> expiry (ms,
  // performance.now()-based). update88Piano merges these into the highlight
  // set so a clicked key lights up even while paused; a short rAF loop keeps
  // redrawing until the flashes expire.
  const _keyFlash = new Map();
  var _keyFlashRaf = null;
  function _flashKey(midi) {
    _keyFlash.set(midi, performance.now() + 300);
    if (_keyFlashRaf) return;
    const tick = () => {
      const now = performance.now();
      for (const [m, exp] of _keyFlash) { if (now >= exp) _keyFlash.delete(m); }
      try { update88Piano((typeof audio !== "undefined" && audio.currentTime) || 0); } catch (_) {}
      _keyFlashRaf = _keyFlash.size ? requestAnimationFrame(tick) : null;
    };
    _keyFlashRaf = requestAnimationFrame(tick);
  }
  // Hit-test a pointer position (client coords) against the cached key
  // geometry. Black keys are checked first (drawn on top, occupy only the
  // upper band). Geometry in piano88Cache is in the same CSS-px space as the
  // canvas's style.width/height, so scale client coords by that ratio.
  function _piano88MidiAt(clientX, clientY) {
    if (!piano88Canvas || !piano88Cache) return null;
    const rect = piano88Canvas.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return null;
    const cssW = parseFloat(piano88Canvas.style.width) || rect.width;
    const cssH = parseFloat(piano88Canvas.style.height) || rect.height;
    const cx = (clientX - rect.left) * (cssW / rect.width);
    const cy = (clientY - rect.top) * (cssH / rect.height);
    const { whiteXs, blackXs } = piano88Cache;
    for (const m in blackXs) {
      const b = blackXs[m];
      if (cx >= b.x && cx <= b.x + b.w && cy >= 0 && cy <= b.h) return parseInt(m, 10);
    }
    for (const m in whiteXs) {
      const w = whiteXs[m];
      if (cx >= w.x && cx <= w.x + w.w && cy >= 0 && cy <= w.h) return parseInt(m, 10);
    }
    return null;
  }

  // var (not let) — _init88Piano is called from _switchTab during boot before
  // this line evaluates; let would put us in TDZ and throw, breaking the rest
  // of player.js initialisation (jianpu toggle, etc.).
  var _piano88Resizer = null;
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
    // draw static keyboard immediately (theme palette so initial light-theme
    // render doesn't briefly show dark-mode default highlights)
    ChordRender.draw88Piano(piano88Canvas, piano88Cache, [], -1, { colors: _palette() });

    // Click-to-play: tap a key to hear it in the current piano sound.
    // Assigned via the onpointerdown PROPERTY (not addEventListener) so it is
    // inherently idempotent — _init88Piano runs multiple times (boot, resize,
    // tab switch) and a property assignment just replaces the prior handler,
    // so a key can never double-trigger. Reads the live piano88Cache so it
    // stays correct after resize.
    piano88Canvas.style.cursor = "pointer";
    piano88Canvas.style.touchAction = "manipulation";
    piano88Canvas.onpointerdown = (e) => {
      const midi = _piano88MidiAt(e.clientX, e.clientY);
      if (midi == null) return;
      e.preventDefault();
      _previewNote(midi);
      _flashKey(midi);
    };

    // Re-init on container resize so highlights stay bound to keys after the
    // user toggles the waterfall, rotates the device, or resizes the window.
    if (_piano88Resizer) { try { _piano88Resizer.disconnect(); } catch (_) {} _piano88Resizer = null; }
    if (typeof ResizeObserver !== "undefined") {
      let lastW = maxW;
      _piano88Resizer = new ResizeObserver(() => {
        const newW = _get88PianoMaxWidth();
        if (Math.abs(newW - lastW) > 16) {
          lastW = newW;
          _init88Piano();
        }
      });
      const target = chordDisplay88 || pianoWaterfallView;
      if (target) _piano88Resizer.observe(target);
    }
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
      nameEl.textContent = _displayChordName(chord.chord);
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

    if (newIdx !== piano88LastIdx) {
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

    // determine actual played notes + fingering from accData
    // fingeringMap includes lookahead: current + next 1s of notes
    // Gates (must match waterfall + audio scheduler — see §3 UX_CONVENTION.md):
    //   - activeHand gates which hand is scanned at all
    //   - RH events come from _resolveRhEvents() which respects rhContentMode
    //     (acc vs mel vs both) so keyboard highlight + finger numbers match
    //     what the user sees in the waterfall and hears in the audio.
    let activeLh = [];
    let activeRh = [];
    let fingeringMap = {};  // midi -> {finger, hand, upcoming}
    const FINGER_LOOKAHEAD = 1.0; // show fingering 1s ahead
    const _wantLh = (typeof activeHand === 'undefined') || activeHand === "both" || activeHand === "left";
    const _wantRh = (typeof activeHand === 'undefined') || activeHand === "both" || activeHand === "right";

    if (waterfallActive && accData) {
       if (_wantLh) {
         for (const e of (accData.left_hand||[])) {
             const playing = e.time <= currentTime && e.time + e.duration >= currentTime;
             const upcoming = !playing && e.time > currentTime && e.time <= currentTime + FINGER_LOOKAHEAD;
             if (playing) activeLh.push(e.pitch);
             if ((playing || upcoming) && e.finger) {
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
       }
       if (_wantRh) {
         // Use _resolveRhEvents() so mel / acc / both choice is respected here
         // just like the waterfall and scheduler do.
         for (const e of _resolveRhEvents()) {
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
       }
    } else {
       // No accData (e.g. hash mode / not-yet-AI'd song) — synth a simple
       // LH fingering (5-3-1 etc.) so the keyboard still shows numbers when
       // showFingering is ON. Also honor _wantLh so R-only practice doesn't
       // leak LH chord highlight into the keyboard.
       if (_wantLh) {
         activeLh = [...piano88ChordMidis];
         if (activeLh.length) {
           const sortedLh = [...activeLh].sort((a, b) => a - b);
           const LH_FMAP = { 1: [5], 2: [5, 1], 3: [5, 3, 1], 4: [5, 3, 2, 1], 5: [5, 4, 3, 2, 1] };
           const fingers = LH_FMAP[sortedLh.length] || [];
           for (let i = 0; i < sortedLh.length; i++) {
             if (!fingers[i]) continue;
             fingeringMap[sortedLh[i]] = { finger: fingers[i], hand: "left", upcoming: false };
           }
         }
       }
       // Hash-mode melody-only fallback: if waterfallActive=false (no accData)
       // but user wants RH melody and we have melodyData, surface the current
       // melody pitch on the keyboard.
       if (_wantRh && typeof melodyData !== 'undefined' && melodyData) {
         const melNow = _getMelodyMidi(currentTime);
         if (melNow >= 0) activeRh.push(melNow);
       }
    }

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

    // Click-to-play: light up keys the user just tapped (transient, ~300ms).
    if (_keyFlash.size) {
      for (const m of _keyFlash.keys()) {
        if (!activeRh.includes(m) && !activeLh.includes(m)) activeRh.push(m);
      }
    }

    ChordRender.draw88Piano(piano88Canvas, piano88Cache, activeLh, activeRh, {
      chordTones: chordTones,
      now: currentTime,
      fingeringMap: showFingering ? fingeringMap : null,
      pedalActive: pedalActive,
      pedalDepth: pedalDepth,
      colors: _palette(),
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

  // Helper: the path used to derive song_hash on the backend. Hash mode has
  // trackPath="" but chordData.path = "__upload/<job_id>" works equally since
  // /api/ai/accompaniment only uses path to compute song_hash → reads the same
  // chord JSON at data/chords/<h>.json.
  function _accPath() {
    return trackPath || (chordData && chordData.path) || "";
  }

  function _loadAccompaniment(forceRefresh) {
    const p = _accPath();
    if (!p || accLoading) return;
    // v6: instrument axis. Guitar/uke get string-family RH events from the
    // backend; everything else (piano/accordion/arranger) keeps piano-pitch
    // events. Cache check must include instrument or a tab switch
    // piano↔guitar will silently reuse the wrong stream.
    //
    // Use a static name list, NOT InstrumentRegistry.isStringInstrument.
    // The registry is populated near end-of-IIFE (string instances register
    // ~line 7590), but _switchTab(activeTab) runs once at line 1928 BEFORE
    // that, and inside it _loadAccompaniment() fires regardless of the
    // `if (inst)` guard. A registry-based check returns false too early on
    // cold load → request goes out as piano → guitar accData never lands
    // until the user manually toggles tabs.
    const _STRING_TAB_IDS = ["guitar", "ukulele"];
    const inst = _STRING_TAB_IDS.includes(activeTab) ? activeTab : "piano";
    if (!forceRefresh && accData
        && accData._style === teachStyle
        && accData._level === teachLevel
        && accData._instrument === inst) return;
    // v6: instrument changed (piano↔guitar/uke) → clear immediately so the
    // current tab doesn't draw the previous tab's events for the ~1 RTT it
    // takes the new fetch to land. Style/level changes are same-instrument
    // so existing visuals stay valid; we only clear on instrument flip.
    if (accData && accData._instrument && accData._instrument !== inst) {
      accData = null;
    }
    accLoading = true;
    _setLoadingState(true, forceRefresh ? _t("loading.acc_regen") : _t("loading.acc_extract"),
                     forceRefresh ? _t("loading.acc_regen_detail") : _t("loading.acc_extract_detail"));
    // encodeURIComponent on teachStyle too — the "1+3" style name has a
    // literal `+`, and `+` in application/x-www-form-urlencoded query
    // strings decodes to a SPACE on the server. Without this, FastAPI saw
    // style=" 3" and fell through to the default Block, so guitar tab's
    // 1+3 selection generated strum instead of the arpeggio idiom.
    let url = `/api/ai/accompaniment?path=${encodeURIComponent(p)}&style=${encodeURIComponent(teachStyle)}&level=${encodeURIComponent(teachLevel)}&instrument=${encodeURIComponent(inst)}`;
    if (forceRefresh) url += "&nocache=1";
    fetch(url).then(r => r.json()).then(data => {
      if (data.error) {
        console.warn("Accompaniment:", data.error);
        accData = null;
      } else {
        data._style = teachStyle;
        data._level = teachLevel;
        data._instrument = inst;
        // Stamp the canonical instrument the backend actually generated for,
        // so string-instrument.js can verify before consuming events. Backend
        // also returns this field; trust it over the request param in case
        // the server downgraded an unknown instrument to piano.
        if (!data.instrument) data.instrument = inst;
        _detectCrossings(data.left_hand, "left");
        _detectCrossings(data.right_hand, "right");
        accData = data;
        if (typeof _scoreRedraw === "function") _scoreRedraw();
        // Phase 2: detect stale accompaniment cache. When the chord JSON
        // has been re-analyzed with dynamic beats (chordData.beat_version
        // bumped) but the acc cache was generated before that, LH/RH
        // event timing is from an older beat grid and won't track rubato.
        // Only warn once per song-load — toast, no banner — so the user
        // knows but isn't blocked.
        try {
          const cdv = (chordData && Number(chordData.beat_version)) || 0;
          const adv = Number(data.source_beat_version) || 0;
          if (cdv > 0 && adv < cdv && !_accStaleWarned) {
            _accStaleWarned = true;
            if (typeof showToast === "function") {
              showToast(_t("toast.acc.stale_after_beat_upgrade"), 4000);
            } else {
              console.info("[acc] stale: chord beat_version=" + cdv + " > acc source_beat_version=" + adv);
            }
          }
        } catch (_e) { /* non-fatal */ }
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
        // v6 follow-up: when audio is paused the per-frame animation loop
        // doesn't fire, so neither the piano waterfall nor the string-
        // instrument waterfall would redraw on its own after accData
        // updates. Style flips on guitar tab were silently ineffective
        // until the user pressed play. Force the active tab's waterfall
        // to redraw at the current playhead so the new events surface
        // immediately, paused or not.
        try {
          const t = (typeof audio !== "undefined" && audio) ? (audio.currentTime || 0) : 0;
          if (activeTab === "piano") {
            if (typeof drawWaterfall === "function" && waterfallActive) drawWaterfall(t);
          } else if (typeof InstrumentRegistry !== "undefined") {
            const _activeInst = InstrumentRegistry.get(activeTab);
            if (_activeInst && typeof _activeInst._drawRhWaterfall === "function") {
              _activeInst._drawRhWaterfall(t);
            }
            // Refresh the RH hint label too — accData drives it (idiom
            // inferred from event shape) so a style flip needs to repaint
            // the label even if the active chord index hasn't changed.
            if (_activeInst && typeof _activeInst.refreshLabels === "function") {
              _activeInst.refreshLabels();
            }
          }
        } catch (_) { /* non-fatal */ }
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
  // i18n is fetched async; if _generateTeacherMessage runs before the dict
  // arrives, _t() returns the raw key (e.g. "teach.hint.style_arpeggio")
  // and the 1.5s currentTime gate keeps that raw key cached even after
  // i18n finishes loading. Two-prong fix:
  //   1. Listen for the i18nready event and flush the teacher cache.
  //   2. _generateTeacherMessage detects raw-key output and skips caching
  //      it, so even an event miss recovers on the next render tick.
  document.addEventListener("livechord:i18nready", () => {
    _teacherMsgCache = "";
    _teacherMsgTime = -999;
  });
  function _looksLikeI18nKey(s) {
    // Raw key shape: "teach.hint.style_arpeggio" — dotted, no spaces, no
    // CJK. Translated values always contain a space or non-ASCII char.
    return typeof s === "string" && /^[a-z0-9_]+(\.[a-z0-9_]+)+$/i.test(s);
  }

  function drawWaterfall(currentTime) {
    if (!waterfallCanvas || !waterfallCtx || !waterfallActive) return;
    if (!piano88Cache) return;

    // Self-heal: mirror piano88's sizing convention EXACTLY so bar-X and key-X
    // share one coordinate system. piano88Cache is built with init88PianoCache
    // (maxW) and its whiteXs[m].x values live in CSS px in [0, maxW]. The
    // waterfall canvas must also render its CSS box at maxW, with internal
    // pixel buffer maxW × dpr, AND inherit CSS `max-width: 100%` so the layout
    // engine can scale BOTH canvases down identically when zoomed/narrow.
    // Without this, browser zoom (Ctrl+/−) caused piano88 to be capped to
    // parent.clientWidth by max-width:100% while waterfall stayed at full maxW
    // — bars at column X stopped lining up with keys at column X.
    const maxW = _get88PianoMaxWidth();
    const h = waterfallCanvas.clientHeight;
    if (maxW < 10 || h < 10) return;
    const dpr = window.devicePixelRatio || 1;
    const expectedW = Math.round(maxW * dpr);
    const expectedH = Math.round(h * dpr);
    if (waterfallCanvas.width !== expectedW || waterfallCanvas.height !== expectedH ||
        waterfallCanvas.style.width !== (maxW + "px")) {
      waterfallCanvas.width = expectedW;
      waterfallCanvas.height = expectedH;
      waterfallCanvas.style.width = maxW + "px";
      waterfallCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    const w = maxW;

    const ctx = waterfallCtx;
    ctx.clearRect(0, 0, w, h);

    // Draw vertical piano key grid
    ctx.strokeStyle = _palette().gridFaint;
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

    // Semantic Colors — Synthesia-style bright, fully opaque so bars pop
    // against the dark backdrop. Lifted hue + alpha 1.0 (prior 0.9 blended
    // into the #0d1117 page bg and looked muddy, especially for LH blue).
    const LH_COLOR = "rgba(79, 172, 254, 1)";     // Bright sky blue (左手)
    const RH_COLOR = "rgba(255, 171, 64, 1)";     // Bright amber (右手)
    const LH_GLOW  = "rgba(79, 172, 254, 0.55)";
    const RH_GLOW  = "rgba(255, 171, 64, 0.55)";

    // 畫拍線網格 — chord-based grid (aligned to actual note positions)
    const _gridChords = _displayChords();
    ctx.textAlign = "left";
    ctx.textBaseline = "bottom";
    ctx.font = "11px sans-serif";
    if (_gridChords && _gridChords.length > 0) {
      for (let ci = 0; ci < _gridChords.length; ci++) {
        const gridLines = _waterfallBeatGrid(_gridChords, ci);
        for (const line of gridLines) {
          const bt = line.time;
          if (bt < currentTime - 0.1 || bt > currentTime + lookAhead) continue;
          const y = h - (bt - currentTime) * pxPerSec;
          if (y < 0 || y > h) continue;
          const isBarLine = !!line.isBarLine;
          ctx.strokeStyle = isBarLine ? _palette().barLineMajor : _palette().barLineMinor;
          ctx.lineWidth = isBarLine ? 2 : 1;
          ctx.beginPath();
          ctx.moveTo(0, y);
          ctx.lineTo(w, y);
          ctx.stroke();
          // Beat number at left edge
          ctx.fillStyle = isBarLine ? _palette().barLabel : _palette().barLineMinor;
          ctx.fillText(line.label, 8, y - 2);
        }
      }
    }

    // A-B Repeat boundary lines on waterfall
    if (abState !== "idle" && abA != null) {
      const yA = h - (abA - currentTime) * pxPerSec;
      if (yA >= 0 && yA <= h) {
        ctx.strokeStyle = _palette().noteRH;
        ctx.lineWidth = 2;
        ctx.setLineDash([6, 3]);
        ctx.beginPath(); ctx.moveTo(0, yA); ctx.lineTo(w, yA); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = _palette().noteRH;
        ctx.font = "bold 11px sans-serif";
        ctx.textAlign = "right";
        ctx.fillText("A", w - 6, yA - 4);
      }
      if (abState === "active" && abB != null) {
        const yB = h - (abB - currentTime) * pxPerSec;
        if (yB >= 0 && yB <= h) {
          ctx.strokeStyle = _palette().noteLH;
          ctx.lineWidth = 2;
          ctx.setLineDash([6, 3]);
          ctx.beginPath(); ctx.moveTo(0, yB); ctx.lineTo(w, yB); ctx.stroke();
          ctx.setLineDash([]);
          ctx.fillStyle = _palette().noteLH;
          ctx.font = "bold 11px sans-serif";
          ctx.textAlign = "right";
          ctx.fillText("B", w - 6, yB - 4);
        }
        // Dim area outside A-B range
        const yAclamped = Math.max(0, Math.min(h, yA));
        const yBclamped = Math.max(0, Math.min(h, yB));
        if (yBclamped < yAclamped) {
          ctx.fillStyle = _palette().panelOverlay;
          if (yBclamped > 0) ctx.fillRect(0, 0, w, yBclamped);
          if (yAclamped < h) ctx.fillRect(0, yAclamped, w, h - yAclamped);
        }
      }
    }

    const allEvents = [];
    if (accData) {
      if (activeHand === "both" || activeHand === "left") {
        allEvents.push(...(accData.left_hand || []).map(e => ({...e, _hand: "left"})));
      }
      if (activeHand === "both" || activeHand === "right") {
        const rhEvents = _resolveRhEvents();
        allEvents.push(...rhEvents.map(e => ({...e, _hand: "right"})));
      }
    } else {
      // Fallback: 如果 AI 伴奏還沒演算完，降級顯示普通的和弦方塊！
      if (activeHand === "both" || activeHand === "left") {
        if (_gridChords && _gridChords.length > 0) {
            for (let ci = 0; ci < _gridChords.length; ci++) {
                const gc = _gridChords[ci];
                const gcEnd = (ci + 1 < _gridChords.length) ? _gridChords[ci + 1].time : gc.time + 4;
                if (gcEnd < currentTime || gc.time > currentTime + lookAhead) continue;
                const cache = chordCache[gc.chord] || {};
                const notes = cache.notes || [];
                const midis = ChordRender.voiceChordForLeftHand(notes, null);
                // Sort low→high, then assign LH fingers 5-3-1 (pinky-middle-thumb)
                // or 5-3-2-1 for 4-note voicings — a simple default so fingering
                // toggle (showFingering) has something to render without waiting
                // for the AI accompaniment endpoint (which doesn't exist for
                // hash-mode beta songs anyway).
                const sorted = [...midis].sort((a, b) => a - b);
                const LH_FINGER_MAP = {
                  2: [5, 1],
                  3: [5, 3, 1],
                  4: [5, 3, 2, 1],
                  5: [5, 4, 3, 2, 1],
                };
                const fingers = LH_FINGER_MAP[sorted.length] || [];
                for (let idx = 0; idx < sorted.length; idx++) {
                    const m = sorted[idx];
                    allEvents.push({
                        time: gc.time,
                        duration: gcEnd - gc.time,
                        pitch: m,
                        finger: fingers[idx] || null,
                        _hand: "left",
                        velocity: 70
                    });
                }
            }
        }
      }
      if (activeHand === "both" || activeHand === "right") {
        if (typeof melodyData !== 'undefined' && melodyData) {
          const rhEvents = melodyData.map(m => ({ ..._melodyNoteEvent(m), _hand: "right" }));
          allEvents.push(...rhEvents);
        }
      }
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
        ctx.fillStyle = _palette().activeChordTint; // very faint accent box
        ctx.fillRect(minX - 4, minY - 4, maxX - minX + 8, maxY - minY + 8);
        ctx.strokeStyle = _palette().activeChordEdge;
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

      // If user seeked backwards past this note, allow it to spark again
      if (evt._sparked && currentTime < noteStart) evt._sparked = false;

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
      // Sheet Music Boss style: neon-saturated base colors; velocity still
      // modulates brightness but the floor is lifted so even pp notes look
      // luminous instead of dull grey/brown.
      let cr, cg, cb, glowColor;
      if (isLeft) {
        // LH: deep cyan (pp) → electric blue (ff)
        cr = Math.round(40 + velP * 100);
        cg = Math.round(150 + velP * 85);
        cb = Math.round(230 + velP * 25);
        glowColor = `rgba(120, 200, 255, 1)`;
      } else {
        // RH: hot amber (pp) → blazing orange (ff)
        cr = Math.round(230 + velP * 25);
        cg = Math.round(130 + velP * 90);
        cb = Math.round(40 + velP * 40);
        glowColor = `rgba(255, 200, 120, 1)`;
      }
      const color = `rgba(${cr}, ${cg}, ${cb}, 1)`;

      // Landing-pad glow on the keys just before the note hits
      if (yBottom > h - 40 && yBottom < h) {
        ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, 0.35)`;
        ctx.fillRect(x, h - 5, kw, -20);
      }

      // Main note bar with always-on bloom (150–200% intensity range)
      const rr = Math.min(4, noteH / 2);
      ctx.save();
      ctx.shadowColor = glowColor;
      ctx.shadowBlur = Math.round(12 + velP * 36); // 12~48px — aggressive bloom
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.roundRect(x + 1, yTop, kw - 2, noteH, rr);
      ctx.fill();
      // Double-pass bloom for loud notes
      if (velP > 0.4) {
        ctx.shadowBlur = Math.round(velP * 55);
        ctx.fill();
      }
      ctx.restore();

      // Bright top highlight — makes the bar look like an energy column
      // instead of a flat rect. Semi-transparent white gradient at the head.
      if (noteH > 4) {
        const hlH = Math.min(8, noteH * 0.35);
        const grd = ctx.createLinearGradient(0, yTop, 0, yTop + hlH);
        grd.addColorStop(0, "rgba(255,255,255,0.55)");
        grd.addColorStop(1, "rgba(255,255,255,0)");
        ctx.fillStyle = grd;
        ctx.beginPath();
        ctx.roundRect(x + 1, yTop, kw - 2, hlH, rr);
        ctx.fill();
      }

      // Hot leading edge at the bottom of the bar — sells the "falling energy" feel
      if (yBottom < h && noteH > 3) {
        ctx.fillStyle = `rgba(255, 255, 255, ${0.25 + velP * 0.35})`;
        ctx.fillRect(x + 2, yBottom - 2, kw - 4, 2);
      }

      // Contact burst when the note crosses the keyboard line
      if (yBottom >= h && yTop <= h) {
         ctx.save();
         ctx.fillStyle = color;
         ctx.shadowColor = glowColor;
         ctx.shadowBlur = 14 + velP * 30;
         ctx.fillRect(x + 1, h - 4, kw - 2, 8);
         // White-hot core
         ctx.fillStyle = `rgba(255,255,255,${0.5 + velP * 0.5})`;
         ctx.shadowBlur = 8 + velP * 20;
         ctx.shadowColor = "#fff";
         ctx.fillRect(x + 3, h - 2, kw - 6, 4);
         ctx.restore();

         // Spawn firework-style particle sparks once per note hit
         if (!evt._sparked && _waterfallParticles.length < _WF_PARTICLE_CAP) {
           evt._sparked = true;
           const n = 6 + Math.round(velP * 14); // 6~20 particles by velocity
           const cx = x + kw / 2;
           for (let i = 0; i < n; i++) {
             const ang = -Math.PI/2 + (Math.random() - 0.5) * Math.PI * 0.9;
             const spd = 1.5 + Math.random() * (2 + velP * 4);
             _waterfallParticles.push({
               x: cx + (Math.random() - 0.5) * kw * 0.6,
               y: h - 2,
               vx: Math.cos(ang) * spd,
               vy: Math.sin(ang) * spd * 1.3,
               life: 0,
               maxLife: 0.4 + Math.random() * 0.35,
               r: cr, g: cg, b: cb,
               size: 1.2 + Math.random() * (1.2 + velP * 1.5)
             });
           }
         }
      }

      // Phase 11: Articulation markers
      if (evt.articulation === "staccato") {
        // Staccato dot at bottom of note block
        ctx.fillStyle = _palette().keyLabelText;
        ctx.beginPath();
        ctx.arc(x + kw / 2, yBottom - 4, 2.5, 0, Math.PI * 2);
        ctx.fill();
      } else if (evt.articulation === "legato" && noteH > 12) {
        // Legato curve connecting to next note (subtle arc at top)
        ctx.strokeStyle = _palette().keyLabelStroke;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(x + kw / 2, yTop, kw * 0.4, Math.PI, 0);
        ctx.stroke();
      }

      // Show finger numbers for all mapped notes
      if (evt.finger) {
        // Waterfall intentionally does NOT render finger numbers — the falling
        // bars get cluttered fast. Fingering is reserved for the 88-key piano
        // area below (see `fingeringMap` at ~line 1504), where 1-2 numbers at
        // the active moment are readable.
        let showF = false;
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
          ctx.strokeStyle = _palette().keyLabelBg;
          ctx.strokeText(text, x + kw / 2, Math.max(fy, 15));
          ctx.fillStyle = _palette().keyLabelText;
          ctx.fillText(text, x + kw / 2, Math.max(fy, 15));
        }
        
        if (isLeft) lhLastF = evt.finger; else rhLastF = evt.finger;
      }
    }

    // ---- Phase 11: Pedal visualization ----
    // Design: saturated RIGHT-edge gutter stripe + near-invisible full-width
    // wash. Right side chosen so the gutter doesn't collide visually with
    // the ribbon/waterfall divider on the left.
    if (accData && accData.pedal && accData.pedal.length > 0) {
      const gutterW = 10;
      const gutterX = w - gutterW;
      for (const ped of accData.pedal) {
        const pedStart = ped.start;
        const pedEnd = ped.end;
        if (pedEnd < currentTime || pedStart > currentTime + lookAhead) continue;

        const yPedBottom = h - (pedStart - currentTime) * pxPerSec;
        const yPedTop = h - (pedEnd - currentTime) * pxPerSec;
        const depth = ped.depth || 1.0;
        const yTop = Math.max(0, yPedTop);
        const regionH = Math.min(h, yPedBottom) - yTop;

        // Faint full-width wash — peripheral awareness only, does not fight note bars
        ctx.fillStyle = `rgba(${_palette().pedalRgb}, ${depth * 0.05})`;
        ctx.fillRect(0, yTop, w, regionH);

        // Saturated right gutter — the primary pedal signal
        ctx.fillStyle = `rgba(${_palette().pedalRgb}, ${0.4 + depth * 0.25})`;
        ctx.fillRect(gutterX, yTop, gutterW, regionH);

        // Pedal change marker (horizontal dashed line at pedal start)
        if (yPedBottom > 0 && yPedBottom < h) {
          ctx.strokeStyle = depth >= 1.0 ? `rgba(${_palette().pedalRgb}, 0.6)` : `rgba(${_palette().pedalRgb}, 0.35)`;
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

    // ---- Firework spark particles (Sheet Music Boss style) ----
    // Particles were spawned in the note-hit branch above. Here we
    // update + render survivors, discard dead ones.
    if (_waterfallParticles.length > 0) {
      const dt = 0.016;
      ctx.save();
      for (let i = _waterfallParticles.length - 1; i >= 0; i--) {
        const p = _waterfallParticles[i];
        p.life += dt;
        if (p.life >= p.maxLife) { _waterfallParticles.splice(i, 1); continue; }
        // Physics: initial upward burst, gravity pulls back, slight drag
        p.vy += 0.18;       // gravity
        p.vx *= 0.985;
        p.vy *= 0.985;
        p.x += p.vx;
        p.y += p.vy;
        // Fade with life; keep a bright core that lingers
        const t = p.life / p.maxLife;
        const alpha = (1 - t) * (1 - t); // ease-out quad
        const size = p.size * (1 - t * 0.4);
        ctx.shadowColor = `rgba(${p.r}, ${p.g}, ${p.b}, 0.9)`;
        ctx.shadowBlur = 8;
        ctx.fillStyle = `rgba(${p.r}, ${p.g}, ${p.b}, ${alpha})`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, size, 0, Math.PI * 2);
        ctx.fill();
        // Hot core — white on dark-bg themes, near-black on light-bg themes
        // (so the particle tip pops against either page bg).
        if (t < 0.5) {
          ctx.fillStyle = _isLightBg()
            ? `rgba(20, 25, 35, ${alpha * 0.85})`
            : `rgba(255, 255, 255, ${alpha * 0.9})`;
          ctx.beginPath();
          ctx.arc(p.x, p.y, size * 0.45, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.restore();
    }

    // Landing line
    ctx.strokeStyle = _palette().noteEdge;
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

    // 每 1.5 秒更新一次訊息，避免閃爍。
    // 但若上一次 cache 結果像是未翻譯的 i18n key (i18n 還沒載入)，立即重試
    // 不要鎖在 1.5s window — 否則使用者要等 1.5s 播放才會看到正確文字。
    if (currentTime - _teacherMsgTime > 1.5
        || !_teacherMsgCache
        || _looksLikeI18nKey(_teacherMsgCache)) {
      _teacherMsgCache = _generateTeacherMessage(currentTime, nowPlaying, upcoming);
      _teacherMsgTime = currentTime;
    }

    if (!_teacherMsgCache) return;

    // 繪製底部右側浮動提示框 (靠近右手區域)
    // Scale font with canvas width: phones are wide-ish in landscape but the
    // hint was still dominating the waterfall. Keep it compact.
    const _hintFont = w < 480 ? 11 : 10;
    const padding = 8;
    ctx.font = `${_hintFont}px 'Segoe UI', sans-serif`;
    const metrics = ctx.measureText(_teacherMsgCache);
    const boxW = Math.min(metrics.width + padding * 2 + 18, w * 0.6);
    const boxH = _hintFont + 12;
    const boxX = w - boxW - 12;
    const boxY = h - 42;

    // 背景 — pill 在深色用半透明黑、淡色用半透明米白
    ctx.fillStyle = _palette().panelOverlayHeavy;
    ctx.beginPath();
    ctx.roundRect(boxX, boxY, boxW, boxH, 14);
    ctx.fill();

    // 右側彩色小圓點 (呼吸動畫)
    const pulse = 0.6 + 0.4 * Math.sin(currentTime * 3);
    ctx.fillStyle = `rgba(${_palette().pedalRgb}, ${pulse})`;
    ctx.beginPath();
    ctx.arc(boxX + boxW - 16, boxY + boxH / 2, 4, 0, Math.PI * 2);
    ctx.fill();

    // 文字 (右對齊，圓點左邊)
    ctx.fillStyle = _palette().noteEmphasis;
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
    if (avgVel > 95) dynHint = _t("teach.hint.dyn_ff");
    else if (avgVel > 80) dynHint = _t("teach.hint.dyn_f");
    else if (avgVel < 50) dynHint = _t("teach.hint.dyn_pp");
    else if (avgVel < 65) dynHint = _t("teach.hint.dyn_p");

    // Articulation 提示
    const artTypes = upcoming.map(e => e.articulation).filter(Boolean);
    let artHint = "";
    if (artTypes.includes("staccato")) artHint = _t("teach.hint.staccato");
    else if (artTypes.includes("legato")) artHint = _t("teach.hint.legato");

    // 組合多樣化的教學訊息 — 根據情境優先級
    const msgs = [];

    // 高優先: 技術警告
    if (hasThumbCross) {
      msgs.push(_t("teach.hint.thumb_cross"));
    }
    if (hasLargeJump) {
      msgs.push(_t("teach.hint.large_jump"));
    }
    if (hasBlackKey && upRH.length > 2) {
      msgs.push(_t("teach.hint.black_keys"));
    }

    // 中優先: 和弦/換和弦提示
    if (nextChordName && upcoming.length > 0) {
      const timeToNext = upcoming[0].time - t;
      if (timeToNext < 0.3 && nextChordName !== currentChordName) {
        msgs.push(_t("teach.hint.next_chord", {chord: nextChordName}));
      }
    }

    // 表情提示
    if (dynHint) msgs.push(dynHint);

    // 踏板提示
    if (pedalActive) {
      msgs.push(_t("teach.hint.pedal"));
    }

    // Articulation + 風格
    if (artHint) msgs.push(artHint.trim());

    // 低優先: 風格基礎提示
    if (msgs.length === 0) {
      if (teachStyle === "Auto") msgs.push(_t("teach.hint.style_auto"));
      else if (teachStyle === "Arpeggio") msgs.push(_t("teach.hint.style_arpeggio"));
      else if (teachStyle === "Block") msgs.push(_t("teach.hint.style_block"));
      else if (teachStyle === "Rhythm") msgs.push(_t("teach.hint.style_rhythm"));
      else if (teachStyle === "Walking") msgs.push(_t("teach.hint.style_walking"));
      else if (teachStyle === "Stride") msgs.push(_t("teach.hint.style_stride"));
      else if (teachStyle === "Shell") msgs.push(_t("teach.hint.style_shell"));
      else msgs.push(_t("teach.hint.style_default"));
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

    // ── Instrument sound picker ────────────────────────────────────────
    // Per-tab override stored in localStorage.livechord_sound_<tab>.
    // Picker reflects the current tab's stored value (or DEFAULT_TAB_SOUND
    // if unset). On change, persist + lazy-init the chosen synth so the
    // first scheduleNotes tick already has it ready. Disabled while
    // audioMode === 0 (Music) because synth output is muted there.
    const soundSelect = $("#instrumentSound");
    const soundResetBtn = $("#btnResetSound");
    function _syncSoundPickerValue() {
      if (!soundSelect) return;
      soundSelect.value = _resolveActiveSoundId();
    }
    function _syncSoundPickerEnabled() {
      if (!soundSelect) return;
      // Always enabled. The picker used to be disabled in Music mode (synth
      // output is muted there), but click-to-play previews now sound the
      // chosen instrument regardless of audio mode, so the selection is
      // always meaningful. Keep the function (applyAudioMode still calls it)
      // so a future mode-dependent tweak has a single hook.
      soundSelect.disabled = false;
      if (soundResetBtn) soundResetBtn.disabled = false;
      soundSelect.title = "Instrument sound — used for click-to-play and MIDI / Mix mode";
    }
    // Expose for applyAudioMode() — declared earlier in IIFE
    window._syncSoundPickerEnabled = _syncSoundPickerEnabled;
    if (soundSelect) {
      _syncSoundPickerValue();
      _syncSoundPickerEnabled();
      soundSelect.addEventListener("change", () => {
        const tab = (typeof activeTab !== "undefined" && activeTab) || "piano";
        const newSoundId = soundSelect.value;
        try { localStorage.setItem("livechord_sound_" + tab, newSoundId); } catch {}
        // Lazy-init so the upcoming scheduler tick doesn't have to wait.
        const s = _ensureSynth(newSoundId);
        try { s.init(); } catch {}
        // Inherit current vol so MIDI/Mix mode keeps its level.
        if (typeof aiSynth !== "undefined" && aiSynth) {
          s.volLeft = aiSynth.volLeft;
          s.volRight = aiSynth.volRight;
        }
        if (typeof showToast === "function") {
          const label = (SAMPLE_MANIFEST[newSoundId] && SAMPLE_MANIFEST[newSoundId].label) || newSoundId;
          showToast(label, 1500);
        }
      });
    }
    if (soundResetBtn) {
      soundResetBtn.addEventListener("click", () => {
        const tab = (typeof activeTab !== "undefined" && activeTab) || "piano";
        try { localStorage.removeItem("livechord_sound_" + tab); } catch {}
        _syncSoundPickerValue();
        if (typeof showToast === "function") {
          const label = (SAMPLE_MANIFEST[DEFAULT_TAB_SOUND[tab]] && SAMPLE_MANIFEST[DEFAULT_TAB_SOUND[tab]].label) || "default";
          showToast("→ " + label, 1500);
        }
      });
    }
    // Hook tab switch: keep picker in sync with active tab's sound choice
    window._syncSoundPickerValue = _syncSoundPickerValue;

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
          const handKey = activeHand === "left" ? "player.hand.left_only"
                        : activeHand === "right" ? "player.hand.right_only"
                        : "player.hand.both";
          btn.title = _t("player.hand.switch_title", { hand: _t(handKey) });
        });
      };
      // Init UI state
      updateHandToggleUI();

      handToggleBtns.forEach(btn => {
        btn.addEventListener("click", () => {
          if (activeHand === "both") {
            activeHand = "left";
            showToast(_t("toast.hand.left"));
          } else if (activeHand === "left") {
            activeHand = "right";
            showToast(_t("toast.hand.right"));
          } else {
            activeHand = "both";
            showToast(_t("toast.hand.both"));
          }
          localStorage.setItem("livechord_active_hand", activeHand);
          updateHandToggleUI();
          if (typeof _syncPracticeModeUI === 'function') _syncPracticeModeUI();
        });
      });
    }

    // Practice-mode picker: 6 presets that set activeHand + rhContentMode together.
    // Encoding: L | R-acc | R-mel | R-both | LR-acc | LR-mel | LR-both.
    function _currentPracticeCode() {
      if (activeHand === "left") return "L";
      if (activeHand === "right") return `R-${rhContentMode}`;
      return `LR-${rhContentMode}`;
    }
    function _syncPracticeModeUI() {
      const code = _currentPracticeCode();
      document.querySelectorAll(".practice-opt").forEach(b => {
        b.classList.toggle("active", b.getAttribute("data-practice") === code);
      });
    }
    function _setPracticeMode(code) {
      const parts = code.split("-");
      const hand = parts[0];  // L | R | LR
      const rh = parts[1];    // acc | mel | both (or undefined for L)
      if (hand === "L") {
        activeHand = "left";
      } else if (hand === "R") {
        activeHand = "right";
        if (rh) rhContentMode = rh;
      } else {
        activeHand = "both";
        if (rh) rhContentMode = rh;
      }
      try { localStorage.setItem("livechord_active_hand", activeHand); } catch {}
      try { localStorage.setItem("livechord_rh_mode", rhContentMode); } catch {}
      // Mirror existing updaters so all 3 UIs (hand toggle, RH content label, practice picker) stay in sync.
      if (typeof updateHandToggleUI === 'function') updateHandToggleUI();
      if (typeof _syncRhContentBtn === 'function') _syncRhContentBtn();
      _syncPracticeModeUI();
      if (typeof update88Piano === 'function' && typeof audio !== 'undefined') {
        try { update88Piano(audio.currentTime || 0); } catch {}
      }
      // v6 follow-up: also redraw the active string-instrument waterfall so
      // toggling between R-acc / R-mel / R-both via a Practice preset takes
      // effect immediately on guitar/uke (not just after the next play tick).
      // v7 follow-up: refresh labels too — rhContentMode now drives the
      // RH hint label, not just the waterfall content.
      try {
        if (activeTab !== "piano" && typeof InstrumentRegistry !== "undefined") {
          const _activeInst = InstrumentRegistry.get(activeTab);
          if (_activeInst && typeof _activeInst._drawRhWaterfall === "function") {
            _activeInst._drawRhWaterfall(audio.currentTime || 0);
          }
          if (_activeInst && typeof _activeInst.refreshLabels === "function") {
            _activeInst.refreshLabels();
          }
        }
      } catch (_) { /* non-fatal */ }
    }
    document.querySelectorAll(".practice-opt").forEach(btn => {
      btn.addEventListener("click", () => {
        const code = btn.getAttribute("data-practice");
        if (!code) return;
        _setPracticeMode(code);
        const label = btn.textContent.trim();
        showToast(_t("toast.practice.mode", { label }), 1400);
      });
    });
    _syncPracticeModeUI();

    if (aiBtn) {
      aiBtn.addEventListener("click", () => {
        // Use _accPath so hash-mode (8801 beta) songs also get AI suggestions —
        // `trackPath` is empty in hash mode but chordData.path carries the same
        // canonical path that song_hash() will resolve server-side.
        const p = _accPath();
        if (!p) {
          showToast(_t("toast.song.not_loaded"));
          return;
        }
        showToast(_t("toast.style.analyzing"));
        fetch(`/api/ai/suggest-style?path=${encodeURIComponent(p)}`)
          .then(r => r.json())
          .then(data => {
            if (data.suggested_styles && data.suggested_styles.length > 0) {
              const best = data.suggested_styles[0];
              if (styleSelect) styleSelect.value = best;
              teachStyle = best;
              localStorage.setItem("livechord_teach_style", best);
              accData = null;
              if (waterfallActive) _loadAccompaniment();
              showToast(_t("toast.style.suggested", { styles: data.suggested_styles.join(", ") }));
            } else {
              showToast(_t("toast.style.no_data"));
            }
          }).catch(() => showToast(_t("toast.style.failed")));
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

    // RH content mode cycle: 伴 → 旋 → 全 → 伴
    const btnRhContent = $("#btnRhContent");
    if (btnRhContent) {
      _syncRhContentBtn();
      btnRhContent.addEventListener("click", () => {
        const i = _RH_MODES.indexOf(rhContentMode);
        rhContentMode = _RH_MODES[(i + 1) % _RH_MODES.length];
        try { localStorage.setItem("livechord_rh_mode", rhContentMode); } catch {}
        _syncRhContentBtn();
        const names = { acc: _t("toast.rh.acc"), mel: _t("toast.rh.mel"), both: _t("toast.rh.both") };
        showToast(_t("toast.rh.content", { name: names[rhContentMode] }), 1500);
        update88Piano(audio.currentTime || 0);
        if (typeof _scoreRedraw === "function") _scoreRedraw();
        // v6 follow-up: redraw the active string-instrument waterfall too,
        // so when the user toggles to "R melody" mode the strum/pluck bars
        // disappear immediately (instead of staying onscreen until next
        // animation tick — which never fires when audio is paused).
        try {
          if (activeTab !== "piano" && typeof InstrumentRegistry !== "undefined") {
            const _activeInst = InstrumentRegistry.get(activeTab);
            if (_activeInst && typeof _activeInst._drawRhWaterfall === "function") {
              _activeInst._drawRhWaterfall(audio.currentTime || 0);
            }
            if (_activeInst && typeof _activeInst.refreshLabels === "function") {
              _activeInst.refreshLabels();
            }
          }
        } catch (_) { /* non-fatal */ }
        if (typeof _syncPracticeModeUI === 'function') _syncPracticeModeUI();
      });
    }
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
        showToast(_t(showFingering ? "toast.fingering.on" : "toast.fingering.off"), 1500);
      });
    }

    // Phase 11: Force refresh accompaniment (clear cache)
    const btnRefreshAcc = $("#btnRefreshAcc");
    if (btnRefreshAcc) {
      btnRefreshAcc.addEventListener("click", () => {
        accData = null;
        _loadAccompaniment(true);
        showToast(_t("toast.acc.regen"), 3000);
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
      // v7: the AI Acc style (#teachStyle) now governs guitar/uke idiom
      // too — STRING_IDIOM_BY_STYLE in accompaniment_generator.py picks
      // arpeggio/strum/offbeat per style. So #teachStyle stays VISIBLE on
      // every tab. The legacy #guitarStyleSelect (arpeggio/pattern/block)
      // is decorative now and was actively confusing — picking "Block"
      // there did nothing because it doesn't trigger an accData refetch
      // and the chosen value was bypassed by the v6+ event-driven idiom
      // detection. Hide it permanently. Same for the gtRhStyleLabel and
      // arpSelectorDiv decorations that branched off guitarStrumStyle.
      if (arpSelectorDiv) arpSelectorDiv.style.display = "none";
      const styleLabel = $("#gtRhStyleLabel");
      if (styleLabel) styleLabel.style.display = "none";
      if (guitarStyleSel) guitarStyleSel.style.display = "none";
      // Show the p/i/m/a finger legend on string tabs (the AI emits pluck
      // events for arpeggio styles regardless of the legacy picker now,
      // so the legend's relevance is governed by tab, not strum style).
      const rhLegend = $("#gtRhFingerLegend");
      if (rhLegend) rhLegend.style.display = isStringTab ? "" : "none";
      // #teachStyle drives the AI Acc style for ALL tabs in v7+ — keep it
      // visible always. (Pre-v7 it was hidden on string tabs because the
      // backend ignored it for guitar/uke; that path is gone.)
      const pianoStyleSel = $("#teachStyle");
      if (pianoStyleSel) pianoStyleSel.style.display = "";
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
      // Hash mode: backend supports ?hash=... directly (avoids path-hash mismatch).
      const url = hashMode
        ? `/api/ai/sections?hash=${encodeURIComponent(hashMode)}`
        : `/api/ai/sections?path=${encodeURIComponent(path || "")}`;
      const res = await fetch(url);
      sectionData = await res.json();
      if (sectionData.sections && sectionData.sections.length > 0) {
        _renderSectionMarkers();
      }
      // Refresh A-B phrase picker now that section labels are known
      if (typeof window._lcPopulatePhraseSelect === "function") {
        window._lcPopulatePhraseSelect();
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
  
  let currentChordVersion = null;
  let availableVersions = [];

  async function loadVersions(path) {
    try {
        const res = await API.getChordVersions(path);
        availableVersions = res.versions || [];
        _renderVersionsDropdown(path);
    } catch(e) {
        console.error("loadVersions error:", e);
    }
  }

  function _renderStarWidget(ver, path) {
      const wrap = document.createElement("div");
      wrap.className = "rating-stars" + (ver.can_rate ? "" : " disabled");
      const baseTitle = ver.count > 0
          ? _t("player.rating.avg_n_votes", { stars: ver.rating.toFixed(1), n: ver.count })
          : _t("player.rating.none_yet");
      wrap.title = ver.can_rate ? baseTitle
        : (ver.is_self ? _t("player.rating.cant_self") : _t("player.rating.signin_first"));

      const stars = [];
      const RATE_ICON_SVG = '<svg class="lc-rate-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/></svg>';
      for (let i = 1; i <= 5; i++) {
          const s = document.createElement("span");
          s.className = "rs-star" + (i <= (ver.my_rating || 0) ? " mine" : "");
          s.innerHTML = RATE_ICON_SVG;
          s.dataset.score = String(i);
          stars.push(s);
          wrap.appendChild(s);
      }

      const paint = (n, mode) => {
          stars.forEach((el, idx) => {
              el.classList.toggle("hover", mode === "hover" && idx < n);
              el.classList.toggle("mine", mode !== "hover" && idx < n);
          });
      };

      if (!ver.can_rate) return wrap;

      stars.forEach(s => {
          const score = parseInt(s.dataset.score);
          s.addEventListener("mouseenter", (e) => { e.stopPropagation(); paint(score, "hover"); });
          s.addEventListener("click", async (e) => {
              e.stopPropagation();
              const next = (ver.my_rating === score) ? 0 : score;
              try {
                  const res = await API.rateChordVersion(path, ver.id, next);
                  ver.my_rating = res.my_rating;
                  ver.rating = res.rating;
                  ver.count = res.count;
                  paint(ver.my_rating, "set");
                  const avgEl = wrap.parentElement && wrap.parentElement.querySelector(".rating-avg");
                  if (avgEl) {
                      avgEl.textContent = ver.count > 0 ? `${ver.rating.toFixed(1)}/5 (${ver.count})` : "—";
                  }
                  wrap.title = ver.count > 0
                      ? _t("player.rating.avg_n_votes", { stars: ver.rating.toFixed(1), n: ver.count })
                      : _t("player.rating.none_yet");
                  showToast(next === 0 ? _t("toast.rate.cleared") : _t("toast.rate.set", { n: next }));
              } catch (err) {
                  console.error("rate failed:", err);
                  showToast(_t("toast.rate.failed"), true);
              }
          });
      });
      wrap.addEventListener("mouseleave", () => paint(ver.my_rating || 0, "set"));

      return wrap;
  }

  function _renderVersionsDropdown(path) {
      const container = $("#versionsContainer");
      const listEl = $("#versionsItems");
      const currentNameEl = $("#currentVersionName");

      if (!container || !listEl || !currentNameEl) return;
      if (availableVersions.length <= 1) {
          container.style.display = "none";
          return;
      }

      container.style.display = "inline-block";
      listEl.innerHTML = "";

      let currentVersionData = availableVersions.find(v => v.id === (currentChordVersion || "official")) || availableVersions[0];
      currentNameEl.textContent = currentVersionData.name;

      availableVersions.forEach(ver => {
          const item = document.createElement("div");
          item.className = "version-item" + (currentVersionData.id === ver.id ? " active" : "");

          const left = document.createElement("div");
          left.className = "version-name";
          left.textContent = ver.name;

          const right = document.createElement("div");
          right.className = "version-rating";
          const stars = _renderStarWidget(ver, path);
          const avg = document.createElement("span");
          avg.className = "rating-avg";
          avg.textContent = ver.count > 0 ? `${ver.rating.toFixed(1)}/5 (${ver.count})` : "—";
          right.appendChild(stars);
          right.appendChild(avg);

          item.appendChild(left);
          item.appendChild(right);

          item.addEventListener("click", async (e) => {
              if (e.target.closest(".rating-stars")) return; // star clicks don't switch versions
              $("#versionsContainer").classList.remove("active");
              if (currentChordVersion === ver.id) return;

              currentChordVersion = ver.id;
              currentNameEl.textContent = ver.name;

              // Highlight selected
              listEl.querySelectorAll(".version-item").forEach(el => el.classList.remove("active"));
              item.classList.add("active");

              // Reload chords smoothly
              _setLoadingState(true, _t("loading.version_switch_title"), _t("loading.version_switch_detail", { name: ver.name }));
              try { await loadChords(path, currentChordVersion); }
              finally { _setLoadingState(false); }
              if (window._updateEditLink) window._updateEditLink();
          });
          listEl.appendChild(item);
      });
  }

  const btnVersionsToggle = $("#btnVersionsToggle");
  if (btnVersionsToggle) {
      btnVersionsToggle.addEventListener("click", (e) => {
          const container = $("#versionsContainer");
          if(container) container.classList.toggle("active");
          e.stopPropagation();
      });
      document.addEventListener("click", () => {
          const container = $("#versionsContainer");
          if(container) container.classList.remove("active");
      });
      const listEl = $("#versionsList");
      if(listEl) listEl.addEventListener("click", e => e.stopPropagation());
  }

  function _telemetrySource() {
    if (!chordData) return "unknown";
    const p = String(chordData.path || "");
    if (chordData.demo_audio_url || p.startsWith("__demo/")) return "demo";
    if (p.startsWith("__upload/")) return "upload";
    if (hashMode) return "upload";
    return "library";
  }

  function _baseSongTelemetry(mode, key) {
    const source = _telemetrySource();
    const bar = chordData && chordData.bar_correction;
    return {
      mode,
      song_hash: mode === "hash" ? key : (hashMode || ""),
      path: mode === "path" ? key : (trackPath || ""),
      title: chordData.title || document.title.replace(/ — LiveChord$/, ""),
      song_title: chordData.title || (songTitle ? songTitle.textContent : ""),
      has_demo_audio: !!chordData.demo_audio_url,
      is_demo: source === "demo",
      source,
      chord_count: Array.isArray(chordData.chords) ? chordData.chords.length : 0,
      key: chordData.key || "",
      time_signature: chordData.time_signature || "",
      has_tempo_curve: Array.isArray(chordData.tempo_curve) && chordData.tempo_curve.length > 1,
      bar_correction_applied: !!(bar && bar.applied),
      bar_correction_score: bar && typeof bar.score_after === "number" ? bar.score_after : null,
      beats_per_bar: bar && bar.beats_per_bar ? bar.beats_per_bar : (chordData.beats_per_bar || null)
    };
  }

  function _trackPlayerLoaded(mode, key) {
    if (!chordData) return;
    const payload = _baseSongTelemetry(mode, key);
    if (window.LiveChordAnalytics) window.LiveChordAnalytics.track("player_loaded", payload);
    if (window.API && API.trackEvent) API.trackEvent("player_loaded", payload);
  }

  let _playerQualityTrackedKey = "";
  function _trackPlayerQualityView(mode, key) {
    if (!chordData) return;
    const songKey = `${mode}:${key || ""}:${chordData.beat_version || ""}:${chordData.updated_at || ""}`;
    if (_playerQualityTrackedKey === songKey) return;
    _playerQualityTrackedKey = songKey;

    requestAnimationFrame(() => {
      const cards = Array.from(document.querySelectorAll("#unifiedRibbonTrack .rv-item"));
      const expected = Array.isArray(chordData.chords) ? chordData.chords.length : 0;
      const rendered = cards.length;
      const blankNames = cards.filter((card) => {
        const text = (card.querySelector(".rv-chord-name")?.textContent || "").trim();
        return !text || text === "—" || text === "--";
      }).length;
      const beatRows = cards.filter((card) => card.querySelector(".rv-beats")).length;
      const beatDotCount = document.querySelectorAll("#unifiedRibbonTrack .beat-dot").length;
      const downbeatCards = cards.filter((card) => card.classList.contains("chord-at-downbeat")).length;
      const shortCards = cards.filter((card) => card.classList.contains("chord-short")).length;
      const meterText = (document.getElementById("chordMeter")?.textContent || "").trim();
      const bpmText = (document.getElementById("chordBpm")?.textContent || "").trim();
      const sourceBadge = (document.getElementById("chordSource")?.textContent || "").trim();
      const issues = [];

      if (expected > 0 && rendered === 0) issues.push("no_chord_cards");
      if (expected > 0 && rendered > 0 && Math.abs(rendered - expected) > Math.max(2, Math.ceil(expected * 0.1))) {
        issues.push("card_count_mismatch");
      }
      if (blankNames > 0) issues.push("blank_chord_names");
      if (rendered > 0 && beatRows < Math.ceil(rendered * 0.5)) issues.push("few_beat_rows");
      if (expected >= 4 && beatDotCount === 0) issues.push("no_beat_dots");
      if (!bpmText) issues.push("missing_bpm_label");

      const severe = ["no_chord_cards", "card_count_mismatch", "blank_chord_names", "no_beat_dots"];
      const qualityStatus = issues.some((issue) => severe.includes(issue)) ? "bad" : (issues.length ? "warn" : "ok");
      const payload = {
        ..._baseSongTelemetry(mode, key),
        quality_status: qualityStatus,
        quality_issues: issues,
        expected_chords: expected,
        rendered_cards: rendered,
        blank_chord_names: blankNames,
        beat_row_count: beatRows,
        beat_dot_count: beatDotCount,
        downbeat_card_count: downbeatCards,
        short_card_count: shortCards,
        meter_label_visible: !!meterText,
        bpm_label_visible: !!bpmText,
        source_badge_visible: !!sourceBadge
      };
      if (window.LiveChordAnalytics) window.LiveChordAnalytics.track("player_quality_view", payload);
      if (window.API && API.trackEvent) API.trackEvent("player_quality_view", payload);
    });
  }

  function _trackQualityFeedback(action) {
    if (!chordData) return;
    const payload = {
      ..._baseSongTelemetry(hashMode ? "hash" : "path", hashMode || trackPath || ""),
      action
    };
    if (window.LiveChordAnalytics) window.LiveChordAnalytics.track("quality_feedback", payload);
    if (window.API && API.trackEvent) API.trackEvent("quality_feedback", payload);
  }

  let _songPlayTracked = false;
  function _trackSongPlay() {
    if (_songPlayTracked || !chordData) return;
    _songPlayTracked = true;
    const source = _telemetrySource();
    const payload = {
      song_hash: hashMode || "",
      path: trackPath || "",
      song_title: chordData.title || (songTitle ? songTitle.textContent : ""),
      source,
      is_demo: source === "demo",
      chord_count: Array.isArray(chordData.chords) ? chordData.chords.length : 0
    };
    if (window.LiveChordAnalytics) window.LiveChordAnalytics.track("song_play", payload);
    if (window.API && API.trackEvent) API.trackEvent("song_play", payload);
  }
  if (audio) audio.addEventListener("play", _trackSongPlay);

  async function loadChords(path, version = null) {
    try {
      chordData = await API.get(_chordsByPathUrl(path, version));
      if (chordData.exists && chordData.chords && chordData.chords.length > 0) {
        hasChords = true;
        _chordDuration = _computeChordDuration(chordData);
        // 和弦品質燈號（helper handles both source + user rating summary）
        _updateChordQualityBadge(chordData, /*key*/ trackPath);
        if (chordData.key) {
          const keyInfo = $("#chordKey");
          const _ma = { Mixolydian:"Mix", Dorian:"Dor", Lydian:"Lyd", Aeolian:"Aeo", Blues:"Blues" };
          const _ml = chordData.mode && _ma[chordData.mode] ? ` ${_ma[chordData.mode]}` : "";
          const displayKey = _displayKey(chordData.key);
          if (keyInfo) keyInfo.textContent = `Key: ${displayKey}${_ml}`;
        }
        if (chordData.capo) {
          capo = chordData.capo;
          const capoSel = $("#capoSelect");
          if (capoSel) capoSel.value = capo;
        }
        await preloadChordInfo(chordData.chords);
        buildChordDOM();
        _trackPlayerLoaded("path", path);
        _trackPlayerQualityView("path", path);
        try { _updateBarArbitrateLabel && _updateBarArbitrateLabel(); } catch {}
        // Re-init active instrument so it picks up new chord data
        if (activeTab !== "piano") {
          const inst = InstrumentRegistry.get(activeTab);
          if (inst) {
            const container = $(inst._config.selectors.container);
            if (container) container.style.display = "flex";
            inst.init();
          }
        }
        // Track DB-path-mode play in recent.json. Until 2026-05-06 only the
        // hash-mode branch posted to /api/recent, so NAS-library songs
        // played via ?path=... never appeared at the top of the homepage's
        // 最近播放 (and stayed at whatever stale order /api/recent had cached).
        // keepalive=true so the POST survives a quick "back" click.
        try {
          fetch("/api/recent", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            keepalive: true,
            body: JSON.stringify({
              path: path,
              title: chordData.title || "",
            }),
          }).catch(() => {});
        } catch (_) { /* non-fatal */ }
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
    // Task 6: empty state. Personal mode → auto-run detection (user is alone, GPU is theirs,
    // zero friction for "I just added this song"). Beta admin → show hero button so they
    // decide when to spend GPU. Beta non-admin never reaches this branch (hash mode).
    if (unifiedRibbonTrack) {
      unifiedRibbonTrack.innerHTML = `
        <div class="chord-empty-state">
          <div class="chord-empty-msg">${_t("player.chord_empty.msg_no_sheet")}</div>
          <button id="btnDetectHero" class="chord-empty-btn">${_t("player.chord_empty.btn_detect")}</button>
          <div class="chord-empty-hint">${_t("player.chord_empty.hint_detect_time")}</div>
        </div>`;
    }
    _isBetaModeAsync.then(isBeta => {
      if (!isBeta && trackPath) runChordDetection();
    }).catch(() => {});
  }

  // bfcache refresh — user edits chords in /editor then presses the browser
  // Back button; Chrome/Safari restore this page from memory without re-running
  // init, so chordData is stale (the canonical case: last chord still shows
  // the pre-edit name). Re-fetch when restored from bfcache.
  window.addEventListener("pageshow", (ev) => {
    if (!ev.persisted) return;
    if (trackPath) {
      loadVersions(trackPath).catch(() => {});
      loadChords(trackPath, currentChordVersion).catch(() => {});
    }
  });

  // Re-fetch the chord JSON and rebuild the ribbon in place, without touching
  // sections/melody/YT embed/PiP state. Used after 升級節拍 so beats/downbeats/
  // tempo_curve land in `chordData` immediately — avoids the foot-gun where
  // the user runs 自動切分 next, splits using stale state, and triggers the
  // pre-merge save path that wiped the new beat fields.
  async function _refreshChordDataInPlace() {
    try {
      let fresh = null;
      if (hashMode) {
        const r = await fetch(_chordsByHashUrl(hashMode));
        if (!r.ok) return false;
        fresh = await r.json();
      } else if (trackPath) {
        fresh = await API.get(_chordsByPathUrl(trackPath, currentChordVersion));
      }
      if (!fresh || !fresh.exists || !Array.isArray(fresh.chords) || !fresh.chords.length) {
        return false;
      }
      chordData = fresh;
      _chordDuration = _computeChordDuration(chordData);
      _updateChordQualityBadge(chordData,
        hashMode ? hashMode : trackPath);
      await preloadChordInfo(chordData.chords);
      buildChordDOM();
      try { _updateBarArbitrateLabel && _updateBarArbitrateLabel(); } catch {}
      return true;
    } catch (e) {
      console.warn("_refreshChordDataInPlace failed:", e);
      return false;
    }
  }

  /** 播放時自動偵測（顯示 overlay） */
  async function autoDetectAndPlay() {
    detectOverlay.style.display = "";
    detectMsg.textContent = _t("detect.progress.msg");
    detectDetail.textContent = _t("detect.progress.detail");

    try {
      const result = await API.detectChords(trackPath);
      detectMsg.textContent = _t("detect.progress.done", { count: result.chord_count });
      detectDetail.textContent = _t("detect.progress.done_key", { key: result.key });

      chordCache = {};
      await loadVersions(trackPath);
      await loadChords(trackPath);
    } catch (err) {
      detectMsg.textContent = _t("detect.progress.failed");
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

    // Fast-path early-return only when nothing needs doing AND no scroll is demanded.
    // forceScroll=true callers (zoom, overview toggle, section jump, tab switch) may
    // have rebuilt the ribbon, so we must re-apply `.active` and scroll regardless.
    if (newIdx === activeChordIdx && !forceScroll) return;

    // Remove old highlight
    if (activeChordIdx >= 0 && activeChordIdx < ribbonElements.length) {
      ribbonElements[activeChordIdx].classList.remove("active");
      ribbonElements[activeChordIdx].classList.add("played");
      const activeDots = ribbonElements[activeChordIdx].querySelectorAll(".beat-active");
      activeDots.forEach(d => d.classList.remove("beat-active"));
    }

    // Clear previous upcoming markers (look-ahead window from old index)
    if (activeChordIdx >= 0) {
      for (let k = 1; k <= 4; k++) {
        const prev = ribbonElements[activeChordIdx + k];
        if (prev) prev.classList.remove("upcoming", "upcoming-next");
      }
    }

    activeChordIdx = newIdx;

    if (activeChordIdx >= 0 && activeChordIdx < ribbonElements.length) {
      const el = ribbonElements[activeChordIdx];
      el.classList.remove("played");
      el.classList.add("active");

      // Tag the next 3 chords so the user can read ahead without straining
      for (let k = 1; k <= 3; k++) {
        const nxt = ribbonElements[activeChordIdx + k];
        if (!nxt) break;
        nxt.classList.add("upcoming");
        if (k === 1) nxt.classList.add("upcoming-next");
      }

      // Auto-scroll ribbon to keep active chord visible. Gate must cover both
      // NAS audio playback (8800) and YT embed playback (8801 hash mode) —
      // audio.paused is always true in hash mode because <audio> never starts.
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
    // Hash mode without audio loaded: open file picker
    if (hashMode && !_usingLocalFile && (!audio.src || audio.src === location.href)) {
      if (localFileInput) localFileInput.click();
      return;
    }
    if (audio.paused) audio.play();
    else audio.pause();
  });

  const btnPrev = $("#btnPrev");
  if (btnPrev) {
    btnPrev.addEventListener("click", () => {
      if ((audio.currentTime || 0) > 3) _rewindToStart();
      else _navPrev();
    });
  }
  const btnNext = $("#btnNext");
  if (btnNext) {
    btnNext.addEventListener("click", () => {
      _navNext();
    });
  }

  function _setSmartView(playing) {
    // No-op: overview removed, ribbon always scrolls
  }
  let _audioIsLoading = false;
  function _setAudioLoadingState(isLoading) {
    if (isLoading && !_audioIsLoading) {
      _audioIsLoading = true;
      _setLoadingState(true, _t("loading.streaming"), _t("loading.streaming_detail"), "music");
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
    let _stallTicks = 0;

    _streamWatcherTimer = setInterval(() => {
      if (audio.paused) {
        _stopStreamWatcher();
        return;
      }
      if (audio.currentTime === _streamLastTime) {
        _stallTicks++;
        if (_stallTicks >= 4) _setAudioLoadingState(true);
      } else {
        _stallTicks = 0;
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
    btnPlay.classList.add("is-playing");
    _setSmartView(true);
    if (!hashMode || _usingLocalFile) _startStreamWatcher();
  });
  
  audio.addEventListener("pause", () => {
    btnPlay.classList.remove("is-playing");
    _setSmartView(false);
    _stopStreamWatcher();
  });

  audio.addEventListener("loadedmetadata", () => {
    timeDuration.textContent = formatTime(audio.duration);
  });

  // requestAnimationFrame sync
  let rafId = null;

  // Local sec/beat at audio time `t`. Reads chordData.tempo_curve when
  // present (rubato songs); falls back to the scalar currentSecPerBeat
  // (set once at ribbon build from chordData.bpm or median heuristic).
  function _secPerBeatAt(t) {
    if (chordData && chordData.tempo_curve && chordData.tempo_curve.length
        && window.BeatSync) {
      const fb = (typeof chordData.bpm === "number" && chordData.bpm > 0)
        ? chordData.bpm : 60.0 / currentSecPerBeat;
      return window.BeatSync.beatDurationAt(chordData.tempo_curve, t, fb);
    }
    return currentSecPerBeat;
  }

  // Virtual beat mapping: ignore uneven BTC splits (e.g. 5+3) and render
  // bar-aligned dot counts based on duration. Short chords keep raw beats +
  // get a 'chord-short' visual-degrade class. Dots carry absolute time so the
  // renderer can tag downbeats. Safety flag: localStorage.livechord_virtual_beats="0"
  // falls back to the legacy floor(durSec/secPerBeat).
  function _virtualBeats(durSec, cStart, autoSplit, displayBeats) {
    const off = (typeof localStorage !== "undefined"
                 && localStorage.getItem("livechord_virtual_beats") === "0");
    // _secPerBeatAt returns the BASE-rate beat duration (rubato-tracked
    // against chordData.bpm). The user-facing bpmMult (½× / 2× cycle) is
    // NOT baked into _secPerBeatAt — apply it here so rawBeats halves when
    // BPM is halved and doubles when BPM is doubled, otherwise the dot
    // count would freeze on the original-tempo bar partition.
    const spb = _secPerBeatAt(cStart) / (_currentBpmMult || 1.0);
    const rawBeats = durSec / spb;
    let tsBeats = 4;
    try {
      if (window.CC && typeof window.CC.inferBeatsPerBar === "function") {
        tsBeats = window.CC.inferBeatsPerBar(chordData, spb) || 4;
      }
    } catch (e) { /* keep 4 */ }

    const forcedBeats = Number(displayBeats || 0);
    if (!off && forcedBeats >= 1 && forcedBeats <= 16) {
      // Sanity: backend stamps display_beats=N as "render this card as N
      // beats". When the splitter is vetoed (fragment-guard on slow songs
      // leaves a 2-bar card stamped display_beats=4), forcing N evenly-
      // spaced dots over the actual duration yields cursor speed =
      // (durSec / (N*spb))× the beat rate — visibly 2× on cards that span
      // ~2 bars. Compare durSec against the expected N-beat duration:
      //   • within ±25% → honor forcedBeats as-is
      //   • outside that → fall through to the realBeats path below, which
      //     already does bar-snap / half-bar / stride-resample correctly.
      // Manual BPM override (_currentBpmMult != 1.0) always honors the
      // hint — the user picked the practice rate.
      const songBpm = (chordData && typeof chordData.bpm === "number" && chordData.bpm > 0)
        ? chordData.bpm : (60.0 / Math.max(0.001, currentSecPerBeat));
      const songSpb = (60.0 / songBpm) / (_currentBpmMult || 1.0);
      const expectedDur = forcedBeats * songSpb;
      const ratio = expectedDur > 0 ? durSec / expectedDur : 1;
      const manualOverrideForced = Math.abs(_currentBpmMult - 1.0) > 1e-3;
      if (manualOverrideForced || (ratio >= 0.75 && ratio <= 1.25)) {
        // Anchor dots on real beats[] when the count matches (cursor's
        // Path A advances by data-time, so this keeps highlight at the
        // tracker's beat rate exactly).
        const explicit = _explicitBeatTimes(cStart, durSec, forcedBeats);
        return {
          count: forcedBeats,
          short: false,
          dots: _buildVirtualDots(forcedBeats, durSec, cStart, explicit),
        };
      }
    }

    // 6/8 cards render eighth-note subdivisions: half-bar = 3 dots, full bar = 6.
    // Subdivisions per bar is a META property (always 6 for 6/8), NOT a tempo
    // property — must stay invariant to the BPM dropdown (_currentBpmMult)
    // so the half-bar vs full-bar dot count doesn't flip when the user
    // cycles practice tempo. _secPerBeatAt returns the BASE-rate spb that
    // tracks tempo_curve rubato but ignores _currentBpmMult — exactly what
    // we want here.
    if (!off && _meterLabel() === "6/8") {
      const subdivisions = Number(chordData && chordData.display_subdivisions_per_bar) || tsBeats;
      const halfBar = subdivisions / 2;
      const baseSpb = _secPerBeatAt(cStart);
      const nominalEighths = baseSpb > 0 ? (durSec / baseSpb) : rawBeats;
      const n = nominalEighths <= (halfBar + subdivisions) / 2
        ? Math.round(halfBar)
        : Math.round(subdivisions);
      // Anchor dots on real eighth-note beats from chordData.beats[] when
      // available, so the highlight ticks at the song's actual eighth rate
      // regardless of card duration. For sub-bar cards (e.g. a 2.13s "bar"
      // with only 5 real eighths inside), reconcile via off-by-one or
      // stride-resample so the visual count still matches `n`.
      const explicitTimes = _explicitBeatTimes(cStart, durSec, n);
      return {
        count: n,
        short: false,
        dots: _buildVirtualDots(n, durSec, cStart, explicitTimes),
      };
    }

    // Backend auto_split: this card represents exactly one bar. The splitter
    // already did the math against arbitrated downbeats. Trust that and force
    // tsBeats dots, regardless of what spb / beats[] heuristics would compute.
    // This is the authoritative path: when the player's local BPM estimate is
    // off by 2× (common on slow songs) we'd otherwise render 8 dots in a 1-bar
    // card. With auto_split we know better — emit 4 evenly-spaced dots.
    //
    // Two escape hatches:
    //   (a) Manual BPM override (_currentBpmMult != 1.0): user is saying
    //       "interpret rhythm at this rate" — that intent should win over
    //       the backend split decision.
    //   (b) Sanity check: if the card is suspiciously short relative to the
    //       expected bar duration (tsBeats × spb), the backend over-split
    //       (e.g. beat_refiner over-densified downbeats, see LiveChord-3kh).
    //       Falling through to the chordData.beats[] path renders the real
    //       beats inside the card instead of forcing tsBeats dots in a
    //       half-bar duration (which looks like 2x/4x dot speed).
    if (autoSplit && tsBeats >= 1 && tsBeats <= 16) {
      const manualOverride = Math.abs(_currentBpmMult - 1.0) > 1e-3;
      // Use SONG-NOMINAL bpm for the bar-duration sanity check, not the
      // local tempo_curve rate. tempo_curve can reflect transient
      // beat-tracker lock-on failures (e.g. beat_this emits half-density
      // beats during a slow jazz intro, so tempo_curve says "55 BPM" when
      // the real song is still 110). Using local rate here over-rejects
      // bar-snap on legitimate 1-bar cards in those sections — the chord
      // ends up showing realBeats=2 instead of musical 4. Song-nominal
      // bpm is the right reference for "is this card approximately 1 bar".
      const songBpm = (chordData && typeof chordData.bpm === "number" && chordData.bpm > 0)
        ? chordData.bpm : (60.0 / Math.max(0.001, currentSecPerBeat));
      const songSpb = (60.0 / songBpm) / (_currentBpmMult || 1.0);
      const expectedBarDur = tsBeats * songSpb;
      const tooShort = expectedBarDur > 0 && durSec < expectedBarDur * 0.65;
      if (!manualOverride && !tooShort) {
        return {
          count: tsBeats,
          short: false,
          dots: _buildVirtualDots(tsBeats, durSec, cStart),
        };
      }
    }

    // Prefer actual beat times from the backend tracker when available —
    // each dot lands on a real beat, so the playback highlight advances at
    // the true beat rate regardless of card duration. This is what fixes
    // the "8-beat chord moves dots at half-beat speed" issue for cards
    // the backend didn't split (low-confidence downbeats path).
    //
    // BUT skip when the user has manually overridden BPM via the click
    // cycle — they're saying "interpret rhythm at this rate", not "trust
    // the tracker". The override path below honors that intent.
    const manualOverrideForRealBeats = Math.abs(_currentBpmMult - 1.0) > 1e-3;
    if (!off && !manualOverrideForRealBeats && Array.isArray(chordData && chordData.beats) && chordData.beats.length) {
      let realBeats = _beatsInRange(chordData.beats, cStart, cStart + durSec);
      // beat_this/madmom on slow songs sometimes locks onto the sub-beat
      // pulse (e.g. emits 8 beats per bar at 88 BPM instead of 4). Downsample
      // when realBeats count is roughly an integer multiple of the bar-snap
      // count, so we render one dot per *musical* beat instead of one per
      // tracker tick — otherwise a 1-bar card shows 8 dots and looks 24-beat.
      const expectedBars = Math.max(1, Math.round(rawBeats / tsBeats));
      const expectedCount = expectedBars * tsBeats;
      if (realBeats.length > expectedCount * 1.5) {
        const stride = Math.max(2, Math.round(realBeats.length / expectedCount));
        const down = [];
        for (let i = 0; i < realBeats.length; i += stride) down.push(realBeats[i]);
        realBeats = down;
      }
      if (realBeats.length >= 1 && realBeats.length <= 16) {
        // Bar-snap: when the card duration is within ±20% of an integer-bar
        // multiple, force tsBeats × bars evenly-spaced dots. This corrects
        // the off-by-one realBeats artefact (5+3 / 3+5 patterns) caused by
        // _beatsInRange epsilon: when a beat lies near the chord boundary,
        // it can be assigned to the neighbour, leaving the present chord
        // with 3 or 5 real beats inside what is musically a 1-bar card.
        // Cards that are NOT bar-aligned (e.g. 0.73-bar passing chords)
        // fall through to the realBeats path — those genuinely have an
        // odd beat count and should render as such.
        const songBpm = (chordData && typeof chordData.bpm === "number" && chordData.bpm > 0)
          ? chordData.bpm : (60.0 / Math.max(0.001, currentSecPerBeat));
        const nominalSpb = (60.0 / songBpm) / (_currentBpmMult || 1.0);
        const expectedBeatsForCard = (nominalSpb > 0) ? (durSec / nominalSpb) : ((spb > 0) ? (durSec / spb) : tsBeats);
        const barsApprox = expectedBeatsForCard / tsBeats;
        const roundedBars = Math.round(barsApprox);
        const halfBarBeats = tsBeats / 2;
        const isHalfBarAligned = halfBarBeats >= 1
          && Math.abs(expectedBeatsForCard - halfBarBeats) < 0.45;
        if (isHalfBarAligned) {
          return {
            count: halfBarBeats,
            short: false,
            dots: _buildVirtualDots(halfBarBeats, durSec, cStart),
          };
        }
        // 1-bar tolerance used to be 0.30, which swallowed cards as short as
        // 0.70 bar (e.g. a 0.745-bar passing chord with 3 real beats was
        // forced to 4 dots — _explicitBeatTimes then synthesized a fake 4th
        // beat at chord end, leaving cursor stepping through 4 dots in
        // 3-beat time). Tighten to 0.15 so only cards genuinely close to
        // a full bar (0.85–1.15) snap up; sub-bar cards fall through to
        // the realBeats fallback (which displays the actual beat count).
        const barSnapTol = roundedBars === 1 ? 0.15 : 0.20;
        const isBarAligned = roundedBars >= 1 && Math.abs(barsApprox - roundedBars) < barSnapTol;
        if (isBarAligned) {
          const targetBeats = Math.min(16, roundedBars * tsBeats);
          // Anchor on real beats[] when count matches (cursor's Path A
          // reads data-time, so every dot tick = a real audio beat instead
          // of `durSec / N` even spacing). Without this, multi-bar cards
          // accumulate drift as the song's tempo curve diverges from the
          // card's nominal spb.
          const explicit = _explicitBeatTimes(cStart, durSec, targetBeats);
          return {
            count: targetBeats,
            short: false,
            dots: _buildVirtualDots(targetBeats, durSec, cStart, explicit),
          };
        }
        return {
          count: realBeats.length,
          short: realBeats.length < tsBeats * 0.75,
          dots: _buildVirtualDots(realBeats.length, durSec, cStart, realBeats),
        };
      }
    }

    // When the user has manually overridden BPM via the click cycle,
    // bypass bar-snapping. Bar-snap can clamp the dot count so doubling
    // BPM doesn't double the dots (e.g. 5.4 raw → 1 bar → 4 dots both
    // before and after a small BPM change), making the highlight visibly
    // race through fixed dots. Manual override means "interpret the
    // rhythm at this rate" — honor it with raw proportional dots.
    if (off || Math.abs(_currentBpmMult - 1.0) > 1e-3) {
      let n = Math.round(rawBeats);
      if (n < 1) n = 1; if (n > 16) n = 16;
      return { count: n, short: false, dots: _buildVirtualDots(n, durSec, cStart) };
    }

    // Short chord — keep raw (passing-chord / syncopation). Visual degrade.
    if (rawBeats < tsBeats * 0.75) {
      const n = Math.max(1, Math.round(rawBeats));
      return { count: n, short: true, dots: _buildVirtualDots(n, durSec, cStart) };
    }
    // Long chord — snap to nearest whole-bar multiple.
    const bars = Math.max(1, Math.round(rawBeats / tsBeats));
    const count = Math.min(16, bars * tsBeats);
    return { count, short: false, dots: _buildVirtualDots(count, durSec, cStart) };
  }

  function _chordVisualEnd(chords, idx, fallbackDur = 4.0) {
    const c = Array.isArray(chords) ? chords[idx] : null;
    if (!c) return 0;
    const start = Number(c.time);
    if (!Number.isFinite(start)) return 0;
    const explicitEnd = Number(c.end);
    if (Number.isFinite(explicitEnd) && explicitEnd > start + 0.001) return explicitEnd;
    const nextStart = Number(chords[idx + 1] && chords[idx + 1].time);
    if (Number.isFinite(nextStart) && nextStart > start + 0.001) return nextStart;
    return start + fallbackDur;
  }

  function _waterfallBeatGrid(chords, idx) {
    const c = Array.isArray(chords) ? chords[idx] : null;
    if (!c) return [];
    const start = Number(c.time);
    if (!Number.isFinite(start)) return [];
    const end = _chordVisualEnd(chords, idx, 4.0);
    const durSec = end - start;
    if (!(durSec > 0)) return [];

    const dots = (_meterLabel() === "3/4")
      ? _buildVirtualDots(3, durSec, start)
      : ((_virtualBeats(durSec, start, c.auto_split, c.display_beats) || {}).dots || []);

    const count = dots.length || 4;
    const fallbackStep = durSec / count;
    return (dots.length ? dots : _buildVirtualDots(count, durSec, start)).map((d, i) => {
      const t = Number(d && d.t);
      const label = Number(d && d.beatInBar);
      return {
        time: Number.isFinite(t) ? t : start + i * fallbackStep,
        label: Number.isFinite(label) && label > 0 ? String(label) : String(i + 1),
        isBarLine: i === 0,
      };
    });
  }

  function _meterLabel() {
    const raw = chordData && typeof chordData.time_signature === "string"
      ? chordData.time_signature.trim()
      : "";
    if (/^\d+\s*\/\s*\d+$/.test(raw)) return raw.replace(/\s+/g, "");
    const bpb = _meterBeatsPerBar();
    return bpb ? `${bpb}/4` : "";
  }

  function _meterBeatsPerBar() {
    const displaySubdivisions = Number(chordData && chordData.display_subdivisions_per_bar);
    if (Number.isFinite(displaySubdivisions) && displaySubdivisions >= 1 && displaySubdivisions <= 16) {
      return Math.round(displaySubdivisions);
    }
    const explicit = Number(chordData && chordData.beats_per_bar);
    if (Number.isFinite(explicit) && explicit >= 1 && explicit <= 16) {
      return Math.round(explicit);
    }
    const label = chordData && typeof chordData.time_signature === "string"
      ? chordData.time_signature.trim().replace(/\s+/g, "")
      : "";
    if (label === "6/8") return 2;
    const match = label.match(/^(\d+)\/\d+$/);
    if (match) {
      const n = Number(match[1]);
      if (Number.isFinite(n) && n >= 1 && n <= 16) return n;
    }
    return 0;
  }

  function _rawTempoBpmAt(t) {
    const curve = chordData && Array.isArray(chordData.tempo_curve) ? chordData.tempo_curve : [];
    if (curve.length) {
      let best = curve[0];
      for (let i = 0; i < curve.length; i++) {
        if (Number(curve[i].t) <= t + 0.001) best = curve[i];
        else break;
      }
      const bpm = Number(best && best.bpm);
      if (Number.isFinite(bpm) && bpm > 0) return bpm;
    }
    const spb = _secPerBeatAt(t);
    return spb > 0 ? 60.0 / spb : 0;
  }

  function _practiceBpmAt(t) {
    let bpm = _rawTempoBpmAt(t) * (_currentBpmMult || 1.0);
    if (_meterLabel() === "6/8") {
      const subdivisions = Number(chordData && chordData.display_subdivisions_per_bar);
      const pulses = Number(chordData && chordData.practice_pulses_per_bar);
      if (Number.isFinite(subdivisions) && subdivisions > 0 && Number.isFinite(pulses) && pulses > 0) {
        bpm *= pulses / subdivisions;
      }
    }
    return bpm;
  }

  function _bpmLabelForTime(t, fallbackBpm, showInfo) {
    const dynamicBpm = _practiceBpmAt(t);
    const bpm = dynamicBpm > 0 ? dynamicBpm : fallbackBpm;
    const dynamicSuffix = (chordData && Array.isArray(chordData.tempo_curve) && chordData.tempo_curve.length)
      ? "~" : "";
    return `BPM: ${Math.round(bpm)}${dynamicSuffix}${showInfo ? " \u24D8" : ""}`;
  }

  function _updateDynamicBpmLabel(t) {
    const bpmEl = document.getElementById("chordBpm");
    if (!bpmEl) return;
    const showInfo = bpmEl.textContent.indexOf("\u24D8") >= 0;
    bpmEl.textContent = _bpmLabelForTime(t, 60.0 / Math.max(0.001, currentSecPerBeat), showInfo);
  }

  // Pick beats that fall inside [start, end). Small epsilon so a beat exactly
  // on the boundary (chord/downbeat snapped to the same grid point) still
  // counts toward the chord that's about to start.
  function _beatsInRange(beats, start, end) {
    const eps = 0.05;
    const out = [];
    for (let i = 0; i < beats.length; i++) {
      const b = beats[i];
      if (b >= start - eps && b < end - eps) out.push(b);
      if (b >= end) break;
    }
    return out;
  }

  // Pull `n` real beat times out of chordData.beats[] for the card at
  // [cStart, cStart+durSec]. Used by 6/8 eighth-note dots and by the
  // forcedBeats / bar-snap paths so dots anchor on the tracker's actual
  // beats — keeps cursor (Path A in _updateBeatDots) aligned with each
  // dot at audio-time precision. Reconciles common off-by-one cases:
  // boundary epsilon (n+1), missing terminal beat (n-1), stride-resample
  // (within ±2). Returns null when beats[] is unavailable or too far off
  // — callers then fall back to even-spacing inside _buildVirtualDots.
  function _explicitBeatTimes(cStart, durSec, n) {
    if (!Array.isArray(chordData && chordData.beats) || !chordData.beats.length) return null;
    if (!(n >= 1)) return null;
    const realBeats = _beatsInRange(chordData.beats, cStart, cStart + durSec);
    if (!realBeats.length) return null;
    if (realBeats.length === n) return realBeats;
    if (realBeats.length === n + 1) {
      // boundary epsilon: realBeats[0] may sit slightly BEFORE cStart (the
      // ±eps tolerance in _beatsInRange lets a beat just past the previous
      // chord's end leak in). Drop it in that case so the visible first
      // dot lands at the chord's actual start. Otherwise keep the head —
      // a chord whose first dot is at/inside the start is more useful
      // (highlight ticks at the chord change) than one that opens with a
      // gap because we dropped a downbeat aligned with cStart.
      const headSlack = realBeats[0] - cStart;
      if (headSlack < -0.01) return realBeats.slice(1);
      return realBeats.slice(0, n);
    }
    if (realBeats.length === n - 1) {
      // synthesize the missing terminal eighth from the local beat step
      const last = realBeats[realBeats.length - 1];
      const step = realBeats.length >= 2
        ? (last - realBeats[0]) / (realBeats.length - 1)
        : (durSec / n);
      return realBeats.concat([Math.min(cStart + durSec - 0.01, last + step)]);
    }
    if (Math.abs(realBeats.length - n) <= 2) {
      // stride-resample so anchors at least land on real ticks (better than
      // even-spacing when realBeats has 4 ticks but card wants 6 dots)
      const out = new Array(n);
      const denom = Math.max(1, n - 1);
      for (let i = 0; i < n; i++) {
        const src = Math.min(realBeats.length - 1,
          Math.round(i * (realBeats.length - 1) / denom));
        out[i] = realBeats[src];
      }
      return out;
    }
    return null;
  }

  function _buildVirtualDots(count, durSec, cStart, explicitTimes) {
    const dbs = (chordData && Array.isArray(chordData.downbeats)) ? chordData.downbeats : [];
    const meterBeatsPerBar = _meterBeatsPerBar();
    const step = durSec / count;
    const tol = Math.min(0.12, step * 0.35);
    const out = new Array(count);
    for (let i = 0; i < count; i++) {
      // explicitTimes (from chordData.beats[]) places the dot on a real
      // tracker beat; without it, we evenly distribute across the card
      // (legacy behaviour for songs without trustworthy beats).
      const t = (explicitTimes && explicitTimes[i] != null)
        ? explicitTimes[i]
        : cStart + i * step;
      let isDownbeat = false;
      if (_meterLabel() === "6/8") {
        isDownbeat = (i === 0 || (count === 6 && i === 3));
      } else if (_meterLabel() === "3/4") {
        // 3/4 cards are bar-anchored by chord_splitter (using bars[] from
        // _simple_3_4_meter_info). Always mark dot 0 as the downbeat to
        // match the 6/8 convention — relying on dbs[] proximity matching
        // misses cards whose chord boundary lands one tick off the nearest
        // downbeat (verified on Moon River: 25/84 cards lost the marker).
        isDownbeat = (i === 0);
      } else if (dbs.length > 0) {
        for (let k = 0; k < dbs.length; k++) {
          if (Math.abs(dbs[k] - t) < tol) { isDownbeat = true; break; }
        }
      }
      const beatInBar = meterBeatsPerBar ? (i % meterBeatsPerBar) + 1 : 0;
      out[i] = { t, isDownbeat, beatInBar, startsBar: meterBeatsPerBar ? beatInBar === 1 : false };
    }
    return out;
  }

  function _updateBeatDots(t) {
    if (activeChordIdx >= 0 && activeChordIdx < ribbonElements.length) {
      const el = ribbonElements[activeChordIdx];
      const startTime = parseFloat(el.dataset.time);
      const dots = el.querySelectorAll(".beat-dot");
      if (!dots.length) return;

      // Prefer per-dot data-time (set when dots are aligned to real beats):
      // active = the latest dot whose time has passed. This makes the highlight
      // tick exactly once per beat, regardless of how many dots fit in the card,
      // fixing the "8-beat chord moves at half-beat speed" bug.
      let beatIdx = -1;
      const firstDotTime = parseFloat(dots[0].dataset.time);
      if (isFinite(firstDotTime)) {
        for (let i = 0; i < dots.length; i++) {
          const dt = parseFloat(dots[i].dataset.time);
          if (isFinite(dt) && dt <= t + 0.001) beatIdx = i;
          else break;
        }
        if (beatIdx < 0) beatIdx = 0;
      } else {
        // Legacy fallback: linear progress mapping (used only if data-time
        // is missing — older cached renders).
        const endTime = parseFloat(el.dataset.end);
        const cardDur = Math.max(0.001, (isFinite(endTime) ? endTime - startTime : dots.length * _secPerBeatAt(startTime)));
        let progress = (t - startTime) / cardDur;
        if (progress < 0) progress = 0;
        if (progress > 0.9999) progress = 0.9999;
        beatIdx = Math.floor(progress * dots.length);
        if (beatIdx >= dots.length) beatIdx = dots.length - 1;
      }

      dots.forEach((dot, idx) => {
        if (idx === beatIdx) dot.classList.add("beat-active");
        else dot.classList.remove("beat-active");
      });
    }
  }

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
      _updateDynamicBpmLabel(t);
      updateActiveChord(t);
      _updateBeatDots(t);
      _updateKeyDisplay(t);
      if (activeTab === "piano") {
        update88Piano(t);
        drawWaterfall(t);
        if (typeof _updateScore === "function") _updateScore(t);
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
    _updateDynamicBpmLabel(t);
    updateActiveChord(t);
    _updateBeatDots(t);
    if (activeTab === "piano") { update88Piano(t); drawWaterfall(t); }
    if (typeof _updateScore === "function") _updateScore(t);
  });

  audio.addEventListener("seeked", () => {
    const t = audio.currentTime;
    _updateProgress(t);
    _updateDynamicBpmLabel(t);
    // forceScroll=true so rewind-to-start (and any scrub) re-centers the
    // ribbon even when paused. The default _isAnyPlaying gate suppresses
    // scrolling on a paused player which is correct for pause-without-seek
    // but wrong for seek events — the user just moved the playhead and
    // expects the chord display to follow.
    updateActiveChord(t, true);
    if (activeTab === "piano") { piano88LastIdx = -1; update88Piano(t); drawWaterfall(t); }
    // Score: a large seek may land outside the current page → force a redraw
    // so the new page renders before the cursor jumps. Without this the
    // cursor falls behind by exactly (seek delta) until the next tickSync.
    if (typeof _scoreRedraw === "function") _scoreRedraw(t);
  });

  // Also sync score on plain timeupdate events (covers paused-scrub via
  // dragging the progress bar at a slow pace, which fires timeupdate but
  // not seeked between snaps).
  audio.addEventListener("timeupdate", () => {
    if (audio.paused && typeof _updateScore === "function") {
      _updateScore(audio.currentTime || 0);
    }
  });

  // ---- 循環模式：off → single → favorites ----
  const btnLoop = $("#btnLoop");
  const LOOP_MODES = ["off", "single", "favorites"];
  // LOOP_LABELS resolved per-call via _t so UI updates on language switch.
  const _loopLabel = (k) => _t("player.loop.label_" + k);
  // Lucide SVG variants — keep in sync with the inline SVG baseline in player.html so
  // the whole toolbar renders identically across Android/iOS/desktop.
  const _LUCIDE_REPEAT = '<svg class="tb-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/></svg>';
  const _LUCIDE_REPEAT_1 = '<svg class="tb-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/><path d="M11 10h1v4"/></svg>';
  const _LUCIDE_FAV_BADGE = '<svg class="loop-fav-badge" viewBox="0 0 24 24" fill="#ff4757" aria-hidden="true"><path d="M12 21s-7-4.5-9.5-9.5C.5 7 3.5 3 7.5 3c2 0 3 1 4.5 2.5C13.5 4 14.5 3 16.5 3c4 0 7 4 5 8.5C19 16.5 12 21 12 21z"/></svg>';
  let loopMode = localStorage.getItem("livechord_loop_mode") || "off";

  function _updateLoopUI() {
    audio.loop = (loopMode === "single");
    if (loopMode === "favorites") {
      btnLoop.innerHTML = `<span class="loop-icon-wrap">${_LUCIDE_REPEAT}${_LUCIDE_FAV_BADGE}</span>`;
    } else if (loopMode === "single") {
      btnLoop.innerHTML = _LUCIDE_REPEAT_1;
    } else {
      btnLoop.innerHTML = _LUCIDE_REPEAT;
    }
    btnLoop.classList.toggle("modified", loopMode !== "off");
    document.querySelectorAll(".loop-opt").forEach(b => {
      b.classList.toggle("active", b.dataset.loop === loopMode);
    });
  }
  _updateLoopUI();

  // favTracks 在 loadTrack() 中載入，與 isFavorite 同步

  function _setLoopMode(mode) {
    if (LOOP_MODES.indexOf(mode) < 0) return;
    loopMode = mode;
    localStorage.setItem("livechord_loop_mode", loopMode);
    _updateLoopUI();
    showToast(_loopLabel(loopMode), 1500);
  }
  btnLoop.addEventListener("click", () => {
    if (_isTouchLike) return;  // touch devices use the popup (see .loop-opt handlers)
    const idx = (LOOP_MODES.indexOf(loopMode) + 1) % LOOP_MODES.length;
    _setLoopMode(LOOP_MODES[idx]);
  });
  document.querySelectorAll(".loop-opt").forEach(b => {
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      _setLoopMode(b.dataset.loop);
      const item = b.closest(".tb-item");
      if (item) item.classList.remove("open");
    });
  });

  audio.addEventListener("ended", () => {
    if (loopMode === "single") return; // audio.loop handles it

    if (loopMode === "favorites" && favTracks.length > 0) {
      _navNext();
      return;
    }

    if (_hasQueueNext()) {
      _navNext();
      return;
    }

    // off — stop
    btnPlay.classList.remove("is-playing");
    if (topProgressFill) topProgressFill.style.width = "0%";
    if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
    if (activeChordIdx >= 0 && activeChordIdx < ribbonElements.length) {
      ribbonElements[activeChordIdx].classList.remove("active");
      for (let k = 1; k <= 4; k++) {
        const nxt = ribbonElements[activeChordIdx + k];
        if (nxt) nxt.classList.remove("upcoming", "upcoming-next");
      }
    }
    activeChordIdx = -1;
  });

  // 平板 FLAC 串流可能不觸發 ended，用 timeupdate 偵測播放結束
  audio.addEventListener("timeupdate", () => {
    const shouldAdvance = (loopMode === "favorites" && favTracks.length > 0) || _hasQueueNext();
    if (loopMode !== "single" && shouldAdvance && audio.duration > 0) {
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
    const v = parseFloat(volumeSlider.value);
    audio.volume = v;
    audio.muted = false;
    localStorage.setItem("livechord_volume", volumeSlider.value);
    if (btnMute) btnMute.classList.toggle("is-muted", v === 0);
  });

  // Speaker icon is popup-only on every platform — no mute-on-click. Earlier
  // desktop mute toggle was muting the audio before the popup appeared, so
  // clicking the icon felt like "click → audio dies → popup". Slider-to-0 is
  // the mute path.
  const btnMute = $("#btnMute");

  // ---- 播放速度 ----
  // Music students typically want to slow down first, not speed up — cycle
  // 1× → 0.75× → 0.5× → 1.25× → 1.5× → 2× → back to 1×.
  const SPEEDS = [1, 0.75, 0.5, 1.25, 1.5, 2];
  const btnSpeed = $("#btnSpeed");
  let speedIdx = SPEEDS.indexOf(1);

  function _syncSpeedUI() {
    const s = SPEEDS[speedIdx];
    const label = s + "x";
    if (btnSpeed) { btnSpeed.textContent = label; btnSpeed.classList.toggle("modified", s !== 1); }
    document.querySelectorAll(".speed-opt").forEach(b => {
      b.classList.toggle("active", parseFloat(b.dataset.speed) === s);
    });
  }

  const savedSpeed = localStorage.getItem("livechord_speed");
  if (savedSpeed !== null) {
    const s = parseFloat(savedSpeed);
    const i = SPEEDS.indexOf(s);
    if (i >= 0) { speedIdx = i; audio.playbackRate = s; }
  }
  _syncSpeedUI();

  function _setSpeed(s) {
    const i = SPEEDS.indexOf(s);
    if (i < 0) return;
    speedIdx = i;
    audio.playbackRate = s;
    _syncSpeedUI();
    localStorage.setItem("livechord_speed", s);
  }
  // Click on the speed trigger only opens the popup (handled by the generic
  // .tb-trigger handler) — no desktop cycle. User feedback: cycling on click
  // skipped past the speed they wanted; popup-pick is the predictable path.
  document.querySelectorAll(".speed-opt").forEach(b => {
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      _setSpeed(parseFloat(b.dataset.speed));
      const item = b.closest(".tb-item");
      if (item) item.classList.remove("open");
    });
  });

  // ---- edit link ----
  const btnEdit = $("#btnEdit");
  if (btnEdit) {
    function _updateEditLink() {
      // The /editor page is path-based; hash-mode songs (beta users) have no
      // NAS path and the page can't load their chords. Hide the link rather
      // than let users land on `/editor?path=null` with an empty timeline.
      if (!trackPath) {
        btnEdit.style.display = "none";
        btnEdit.removeAttribute("href");
        return;
      }
      btnEdit.style.display = "";
      let editUrl = `/editor?path=${encodeURIComponent(trackPath)}`;
      if (currentChordVersion) editUrl += `&version=${encodeURIComponent(currentChordVersion)}`;
      btnEdit.href = editUrl;
    }
    _updateEditLink();
    window._updateEditLink = _updateEditLink;
    // Stamp the current playhead time just before navigating so the editor
    // can scroll/seek to the same spot — saves the user from scrolling from
    // the start of the song every time.
    btnEdit.addEventListener("click", () => {
      const t = Math.max(0, _playerCurrentTime() || 0);
      try {
        const url = new URL(btnEdit.href, window.location.origin);
        url.searchParams.set("t", t.toFixed(2));
        btnEdit.href = url.toString();
      } catch {}
    });
  }

  // ---- Chord Correction buttons ----
  // Auto-save to user version — user asked for parity with phrase/section
  // edits which save without a manual button. Debounced 800ms so a burst of
  // corrections (e.g., a multi-chord quantize) POSTs once instead of once
  // per tap. Revert is still a local-only UI action.
  let _autoSaveTimer = null;
  // Defensive: sort chord array by time and drop near-duplicates before
  // POSTing. Past bug: a series of segmented calibrations + edits left the
  // admin file with two out-of-order runs glued together (chord[20].time=183
  // → chord[21].time=66 regression), which showed up as overlapping blocks
  // in the editor. Frontend should never persist an out-of-order array.
  function _cleanChordsForSave(chords) {
    const sorted = [...chords].sort((a, b) => (a.time || 0) - (b.time || 0));
    const MIN_GAP = 0.2;
    const cleaned = [];
    for (const c of sorted) {
      if (cleaned.length && (c.time || 0) - (cleaned[cleaned.length - 1].time || 0) < MIN_GAP) {
        cleaned[cleaned.length - 1] = c;  // prefer later-indexed (newer edit)
      } else {
        cleaned.push(c);
      }
    }
    for (let i = 0; i < cleaned.length - 1; i++) {
      cleaned[i].end = cleaned[i + 1].time;
    }
    return cleaned;
  }

  function _autoSaveCorrection() {
    if (!chordData || !trackPath) return;
    if (!window.ChordCorrection || !window.ChordCorrection.hasBackup()) return;
    clearTimeout(_autoSaveTimer);
    _autoSaveTimer = setTimeout(async () => {
      try {
        const cleanChords = _cleanChordsForSave(chordData.chords);
        // Write back in-memory too so the ribbon stays consistent with disk
        chordData.chords = cleanChords;
        const result = await API.saveChords({
          path: trackPath,
          key: chordData.key || "",
          capo: capo,
          bpm: chordData.bpm || 0,
          chords: cleanChords,
        });
        if (result && result.version) {
          currentChordVersion = result.version;
          const url = new URL(window.location);
          url.searchParams.set("version", result.version);
          window.history.replaceState({}, "", url);
          if (window._updateEditLink) window._updateEditLink();
        }
        showToast(_t("toast.corr.autosaved"), 1500);
      } catch (err) {
        showToast(_t("toast.corr.autosave_failed", { err: err.message }), 3000);
      }
    }, 800);
  }

  const _corrRebuild = () => {
    chordCache = {};
    buildChordDOM();
    activeChordIdx = -1;
    requestAnimationFrame(() => updateActiveChord(audio.currentTime || -1, true));
    const btnSave = $("#btnSaveCorrected"), btnRevert = $("#btnRevertCorrection");
    if (window.ChordCorrection && window.ChordCorrection.hasBackup()) {
      // Save button hidden — auto-save handles it. Keep Revert visible so
      // the user can undo the whole backup in one click.
      if (btnSave) btnSave.style.display = "none";
      if (btnRevert) btnRevert.style.display = "";
      _autoSaveCorrection();
    }
  };
  // Expose state for chord-correction.js
  Object.defineProperty(window, '_playerActiveChordIdx', { get: () => activeChordIdx });
  Object.defineProperty(window, '_playerSecPerBeat', { get: () => currentSecPerBeat });

  // chord-correction.js reads .paused / .currentTime / .duration — the
  // raw HTMLAudioElement is now the only playback source so a passthrough
  // is enough.
  const _audioForCorrection = audio;

  const btnBeatTap = $("#btnBeatTap");
  if (btnBeatTap) {
    btnBeatTap.addEventListener("click", () => {
      if (!chordData || !chordData.chords || chordData.chords.length === 0) {
        showToast(_t("toast.error.no_chord_data"), 2000); return;
      }
      window.ChordCorrection.enterBeatTap(chordData, _audioForCorrection, _corrRebuild);
    });
  }

  const btnChordCalibrate = $("#btnChordCalibrate");
  if (btnChordCalibrate) {
    btnChordCalibrate.addEventListener("click", () => {
      if (!chordData || !chordData.chords || chordData.chords.length === 0) {
        showToast(_t("toast.error.no_chord_data"), 2000); return;
      }
      window.ChordCorrection.enterChordCalibrate(chordData, _audioForCorrection, _corrRebuild, {
        sections: (sectionData && Array.isArray(sectionData.sections)) ? sectionData.sections : null,
      });
    });
  }

  // Theme picker — lives in the Tools popup. Four options: dark / light /
  // court (deep forest + orange) / sakura (warm cream + rose). Flips the
  // <html data-theme> attribute (CSS reads it), updates localStorage,
  // mirrors the JS-side _currentTheme so canvas renderers pick the right
  // palette, then forces a redraw so the change is visible immediately
  // rather than at the next RAF tick. Inline <head> script in every HTML
  // restores the attribute on next load to avoid FOUC.
  // Note: option buttons use `data-pick` (NOT `data-theme`) on purpose. A
  // `data-theme="light"` on a <button> would itself match the `:root`-level
  // `[data-theme="light"]` rule and locally re-cascade the light palette into
  // that one button — making the "淡色" / "櫻夢" labels render in their own
  // theme's text color (washed-out on the current theme's bg). `data-pick`
  // sidesteps that collision entirely.
  const _THEME_META_COLORS = {
    dark: "#1a1a2e",
    light: "#f7f5f0",
    forest: "#0d2818",
    sakura: "#fef5f7",
    sunny: "#fff8e1",
    sky: "#dbeafe",
  };
  function _applyTheme(theme) {
    _currentTheme = _VALID_THEMES.has(theme) ? theme : "dark";
    document.documentElement.setAttribute("data-theme", _currentTheme);
    try { localStorage.setItem("livechord_theme", _currentTheme); } catch (_) {}
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", _THEME_META_COLORS[_currentTheme]);
    // Sync popup .active state
    document.querySelectorAll(".tb-popup .theme-opt").forEach(b => {
      b.classList.toggle("active", b.dataset.pick === _currentTheme);
    });
    // Force redraw — waterfall and 88-key both read _palette() per frame, but
    // a paused player won't redraw until next event. Kick both renderers.
    try { if (typeof drawWaterfall === "function") drawWaterfall(); } catch (_) {}
    try { if (typeof draw88Keys === "function") draw88Keys(); } catch (_) {}
  }
  // Bind every option button in the Theme popup.
  document.querySelectorAll(".tb-popup .theme-opt").forEach(btn => {
    btn.addEventListener("click", () => _applyTheme(btn.dataset.pick));
  });
  // Initial sync — picks up the value the inline <head> script set so the
  // label/meta reflect persisted state on first paint.
  _applyTheme(_currentTheme);

  // Jianpu (簡譜) visibility toggle. Hidden via body.no-jianpu CSS class so
  // both ribbon variants (.rv-jianpu chord ribbon + .chord-jianpu/.melody-jianpu
  // 88-key ribbon) flip together. localStorage keeps the choice across reloads.
  const _jianpuStored = localStorage.getItem("livechord_show_jianpu");
  let _showJianpu = _jianpuStored == null ? true : _jianpuStored === "true";
  function _applyJianpuVisibility() {
    document.body.classList.toggle("no-jianpu", !_showJianpu);
    const lab = document.getElementById("btnToggleJianpuLabel");
    if (lab) {
      const key = _showJianpu ? "player.tools.jianpu_on" : "player.tools.jianpu_off";
      const fb  = _t(_showJianpu ? "player.tools.jianpu_on" : "player.tools.jianpu_off");
      lab.textContent = (window.LiveChordI18n ? window.LiveChordI18n.t(key) : key) || fb;
      if (lab.textContent === key) lab.textContent = fb;
    }
    const btn = document.getElementById("btnToggleJianpu");
    if (btn) btn.setAttribute("aria-pressed", _showJianpu ? "true" : "false");
  }
  const btnToggleJianpu = $("#btnToggleJianpu");
  if (btnToggleJianpu) {
    btnToggleJianpu.addEventListener("click", () => {
      _showJianpu = !_showJianpu;
      try { localStorage.setItem("livechord_show_jianpu", _showJianpu ? "true" : "false"); } catch (_) {}
      _applyJianpuVisibility();
    });
  }
  _applyJianpuVisibility();
  document.addEventListener("livechord:langchange", _applyJianpuVisibility);
  document.addEventListener("livechord:i18nready",  _applyJianpuVisibility);

  // ===== Score (sheet music) layer =====
  // Lazy-rendered VexFlow grand staff slotted above the waterfall canvas.
  // Eligibility = (waterfall wrapper width ≥ SCORE_MIN_WIDTH) AND
  // (chord ribbon is NOT in overview-mode while visible). User can also
  // hard-disable via Tools popup "Score" toggle (persisted in
  // livechord_show_score). When body.no-score is on, ScoreRender.destroy()
  // is called so VexFlow SVG doesn't keep eating memory on hidden state.
  const SCORE_MIN_WIDTH = 540;
  // Minimum viewport height for the score to render. The grand staff
  // (treble + bass) is 220 px tall by CSS; on phones in landscape with
  // height < ~500 px, the 220 + waterfall (≥ 120) + piano (~178) total
  // exceeds the viewport vertical budget and either piano gets cut or
  // the treble half of the score gets clipped off-screen above by the
  // flex:end anchor. Below this threshold, hide the score so the
  // waterfall + piano stay fully visible (the user's primary practice
  // surface). Threshold lets iPad landscape (744+) and tablet landscape
  // through, blocks phone landscape.
  const SCORE_MIN_HEIGHT = 500;
  const _scoreStored = localStorage.getItem("livechord_show_score");
  let _showScore = _scoreStored == null ? true : _scoreStored === "true";
  let _scoreInited = false;
  let _scoreCurrentRange = { start: 0, end: 0 };
  let _scoreRangeDuration = 0;     // = end - start; cached on each _scoreRedraw
  const _SCORE_BARS_PER_PAGE_WIDE = 4;
  const _SCORE_BARS_PER_PAGE_NARROW = 2;
  // Density gate — below this stage-width threshold, drop to 2 bars per
  // page so each bar gets ~2× the horizontal pixel budget. Set high (1000)
  // because even at 50/50 split on a 1920-wide viewport, stage width is
  // ~900 px and 4 bars still pack densely with sharp accidentals + flags.
  // Only kicks back to 4 bars on full-width waterfall (ribbon collapsed)
  // or very wide viewports (≥ 2200 px where 50/50 gives stage > 1000).
  const _SCORE_BARS_NARROW_WIDTH = 1000;
  function _scoreBarsPerPage() {
    const w = _scoreStageEl ? _scoreStageEl.clientWidth : 0;
    return w < _SCORE_BARS_NARROW_WIDTH ? _SCORE_BARS_PER_PAGE_NARROW : _SCORE_BARS_PER_PAGE_WIDE;
  }
  const _scoreLayerEl = document.getElementById("scoreLayer");
  const _scoreStageEl = document.getElementById("scoreStage");
  const _scoreCursorEl = document.getElementById("scoreCursor");
  const _scoreChordRowEl = document.getElementById("scoreChordRow");
  const _pianoWaterfallViewEl = document.getElementById("pianoWaterfallView");

  function _isOverviewActive() {
    if (!chordRibbonPanel) return false;
    const visible = getComputedStyle(chordRibbonPanel).display !== "none";
    return visible && chordRibbonPanel.classList.contains("overview-mode");
  }

  function _scoreBeatSecs() {
    const bpm = (chordData && +chordData.bpm) || 120;
    return 60 / Math.max(40, bpm);
  }
  function _scoreTimeSigParts() {
    const raw = (chordData && chordData.time_signature) || "4/4";
    const m = String(raw).replace(/\s+/g, "").match(/^(\d+)\/(\d+)$/);
    if (!m) return { numerator: 4, denominator: 4 };
    const numerator = Math.max(1, parseInt(m[1], 10) || 4);
    const denominator = Math.max(1, parseInt(m[2], 10) || 4);
    return { numerator, denominator };
  }
  function _scoreBarSecs() {
    const ts = _scoreTimeSigParts();
    return (ts.numerator * (4 / ts.denominator)) * _scoreBeatSecs();
  }

  function _gatherScoreNotes() {
    const acc = accData || {};
    if (rhContentMode === "mel" && Array.isArray(melodyData) && melodyData.length) {
      // Melody is {start, end, midi, ...}. Normalize to {time, duration, pitch}.
      const rh = melodyData
        .filter(m => Number.isFinite(+m.midi) && +m.midi > 0)
        .map(_melodyNoteEvent);
      return { rh, lh: acc.left_hand || [] };
    }
    return { rh: acc.right_hand || [], lh: acc.left_hand || [] };
  }

  function _scorePageRange(currentTime) {
    const barSecs = _scoreBarSecs();
    const barsPerPage = _scoreBarsPerPage();
    const pageSecs = barsPerPage * barSecs;
    // Anchor pages to downbeats[] when present so bar lines on the score
    // match musical bars; fall back to "BPM × beats × N" grid otherwise.
    const downs = (chordData && Array.isArray(chordData.downbeats)) ? chordData.downbeats : null;
    if (downs && downs.length >= 2) {
      // If currentTime is BEFORE the first downbeat (intro silence / pickup),
      // anchor the page at t=0 with synthetic bar grid. Without this, the
      // page snaps forward to downs[0] (~2.47s) and notes in the [0, downs[0])
      // window get filtered out — silently disagreeing with the waterfall
      // which is happily rendering them. Symptom: score shows fewer notes
      // than waterfall AND lags by exactly the intro length.
      if (currentTime < downs[0] - 0.01) {
        // Back the page up to t=0 (or just enough to cover the pickup).
        const start = 0;
        // Bar boundaries from t=0 in pageSecs steps until we hit downs[0],
        // then continue from downs onward. Caller doesn't see bar boundaries
        // directly — it just needs {start, end}. So just use pageSecs.
        return { start, end: start + pageSecs };
      }
      // Find page-aligned start (every 4th downbeat).
      let idx = 0;
      for (let i = 0; i < downs.length; i++) {
        if (downs[i] > currentTime) { idx = Math.max(0, i - 1); break; }
        if (i === downs.length - 1) idx = i;
      }
      const pageIdx = Math.floor(idx / barsPerPage);
      const startI = pageIdx * barsPerPage;
      const endI = Math.min(downs.length - 1, startI + barsPerPage);
      const start = downs[startI];
      const end = (endI < downs.length && endI > startI) ? downs[endI] : start + pageSecs;
      return { start, end };
    }
    const pageIdx = Math.floor(Math.max(0, currentTime) / pageSecs);
    return { start: pageIdx * pageSecs, end: (pageIdx + 1) * pageSecs };
  }

  function _scoreChordsInRange(range) {
    const all = (chordData && Array.isArray(chordData.chords)) ? chordData.chords : [];
    return all.filter(c => +c.time >= range.start - 0.001 && +c.time < range.end - 0.001);
  }

  function _scoreRenderChordRow(range, chordsInRange) {
    if (!_scoreChordRowEl) return;
    _scoreChordRowEl.innerHTML = "";
    // Position labels using ScoreRender's bar-layout pixel coordinates so
    // chord labels sit directly above the corresponding notation. Falls back
    // to percentage-of-container if timeToX isn't available (defensive).
    const useTimeToX = window.ScoreRender && typeof window.ScoreRender.timeToX === "function";
    const span = Math.max(0.001, range.end - range.start);
    for (const c of chordsInRange) {
      const el = document.createElement("span");
      el.textContent = _displayChordName(c.chord || "");
      if (useTimeToX) {
        el.style.left = window.ScoreRender.timeToX(+c.time) + "px";
      } else {
        el.style.left = (((+c.time - range.start) / span) * 100) + "%";
      }
      _scoreChordRowEl.appendChild(el);
    }
  }

  // Apply a fade + slight slide-from-right transition to the score stage and
  // chord row. Triggered on page flips (range change), not on initial render
  // or in-place redraws. Cursor is NOT animated — it must reflect the actual
  // play time immediately. Stage + chord-row briefly mis-align with cursor
  // during the 220 ms slide; acceptable since the animation is short and the
  // alternative (animating cursor too) breaks the "this is where the music
  // is right now" semantics.
  let _scoreFlipCleanupTimer = null;
  function _scoreApplyFlipAnimation() {
    const els = [_scoreStageEl, _scoreChordRowEl].filter(Boolean);
    if (!els.length) return;
    // Reset starting state without animating (transition:none).
    for (const el of els) {
      el.style.transition = "none";
      el.style.opacity = "0";
      el.style.transform = "translateX(28px)";
    }
    // Force reflow once so the browser commits the starting state.
    // eslint-disable-next-line no-unused-expressions
    els[0].offsetWidth;
    // Apply the actual transition.
    for (const el of els) {
      el.style.transition = "opacity 220ms ease-out, transform 220ms ease-out";
      el.style.opacity = "1";
      el.style.transform = "translateX(0)";
    }
    // Clear inline transition after settle so future tick-driven cursor
    // updates aren't accidentally animated.
    if (_scoreFlipCleanupTimer) clearTimeout(_scoreFlipCleanupTimer);
    _scoreFlipCleanupTimer = setTimeout(() => {
      for (const el of els) {
        el.style.transition = "";
        // Leave opacity:1 and transform:translateX(0) — they're the steady
        // state; clearing them risks a flash on the next reset.
      }
      _scoreFlipCleanupTimer = null;
    }, 260);
  }

  function _scoreRedraw(currentTime) {
    if (!_scoreLayerEl) return;
    if (document.body.classList.contains("no-score")) return;
    if (!window.ScoreRender || !window.Vex || !window.Vex.Flow) return;
    const t = (typeof currentTime === "number") ? currentTime : ((audio && audio.currentTime) || 0);
    const range = _scorePageRange(t);
    const { rh, lh } = _gatherScoreNotes();
    if ((!rh || rh.length === 0) && (!lh || lh.length === 0)) {
      // No data → hide layer rather than draw an empty staff.
      _scoreLayerEl.setAttribute("hidden", "");
      return;
    }
    _scoreLayerEl.removeAttribute("hidden");
    if (!_scoreInited) {
      const w = _scoreStageEl ? (_scoreStageEl.clientWidth || 600) : 600;
      const h = _scoreStageEl ? (_scoreStageEl.clientHeight || 160) : 160;
      _scoreInited = window.ScoreRender.init(_scoreStageEl, { width: w, height: h });
      if (!_scoreInited) return;
    }
    // Page-flip detection: old range exists AND new range differs. First
    // render (oldRange.end === 0) is NOT a flip — it's the initial display
    // and should appear instantly without animation.
    const oldRange = _scoreCurrentRange;
    const hadOldRange = oldRange && oldRange.end > 0;
    const isPageFlip = hadOldRange &&
      (Math.abs(range.start - oldRange.start) > 0.05 ||
       Math.abs(range.end - oldRange.end) > 0.05);

    _scoreCurrentRange = range;
    _scoreRangeDuration = range.end - range.start;
    const key = (chordData && chordData.key) ? String(chordData.key).replace(/m$/, "") : "C";
    window.ScoreRender.render({
      timeRange: range,
      timeSig: (chordData && chordData.time_signature) || "4/4",
      key,
      bpm: (chordData && +chordData.bpm) || 120,
      barLines: (chordData && Array.isArray(chordData.downbeats)) ? chordData.downbeats : [],
      rhNotes: rh,
      lhNotes: lh,
    });
    _scoreRenderChordRow(range, _scoreChordsInRange(range));
    // Cursor: position to current play time immediately (not 0), then force
    // reflow without transition so it doesn't slide-back / rubber-band.
    if (_scoreCursorEl) {
      const cursorX = (window.ScoreRender && typeof window.ScoreRender.timeToX === "function")
        ? window.ScoreRender.timeToX(t) : 0;
      _scoreCursorEl.style.transition = "none";
      _scoreCursorEl.style.transform = "translateX(" + cursorX + "px)";
      // eslint-disable-next-line no-unused-expressions
      _scoreCursorEl.offsetWidth;
      _scoreCursorEl.style.transition = "";
    }
    if (isPageFlip) _scoreApplyFlipAnimation();
  }

  function _updateScore(currentTime) {
    if (!_scoreLayerEl || document.body.classList.contains("no-score")) return;
    if (!_scoreInited || !window.ScoreRender) return;
    if (currentTime < _scoreCurrentRange.start || currentTime >= _scoreCurrentRange.end) {
      _scoreRedraw(currentTime);
      return;
    }
    // Cursor x is derived from ScoreRender's pre-cached _barLayout — no
    // getBoundingClientRect() / clientWidth in the hot path.
    const x = window.ScoreRender.timeToX(currentTime);
    if (_scoreCursorEl) _scoreCursorEl.style.transform = "translateX(" + x + "px)";
    // Highlight active chord pill in the chord row.
    if (_scoreChordRowEl) {
      const spans = _scoreChordRowEl.children;
      const all = (chordData && Array.isArray(chordData.chords)) ? chordData.chords : [];
      const visible = all.filter(c => +c.time >= _scoreCurrentRange.start - 0.001 && +c.time < _scoreCurrentRange.end - 0.001);
      let activeI = -1;
      for (let i = 0; i < visible.length; i++) {
        if (+visible[i].time <= currentTime + 1e-3) activeI = i;
      }
      for (let i = 0; i < spans.length; i++) {
        spans[i].setAttribute("data-active", i === activeI ? "true" : "false");
      }
    }
  }

  function _applyScoreVisibility() {
    if (!_scoreLayerEl) return;
    const eligible = document.body.classList.contains("score-eligible");
    const shouldShow = eligible && _showScore;
    document.body.classList.toggle("no-score", !shouldShow);
    if (shouldShow) {
      _scoreRedraw();
    } else if (_scoreInited && window.ScoreRender) {
      // Tear down so hidden state doesn't leak memory.
      window.ScoreRender.destroy();
      _scoreInited = false;
      // Reset the range so the next render() (when score becomes visible
      // again) is treated as a first render rather than a page flip — we
      // want the score to appear instantly on toggle/re-eligible, not slide.
      _scoreCurrentRange = { start: 0, end: 0 };
    }
    const lab = document.getElementById("btnToggleScoreLabel");
    if (lab) {
      const key = _showScore ? "player.tools.score_on" : "player.tools.score_off";
      lab.textContent = (window.LiveChordI18n ? window.LiveChordI18n.t(key) : key) || (_showScore ? "Score: On" : "Score: Off");
      if (lab.textContent === key) lab.textContent = _showScore ? "Score: On" : "Score: Off";
    }
    const btn = document.getElementById("btnToggleScore");
    if (btn) btn.setAttribute("aria-pressed", _showScore ? "true" : "false");
  }

  function _recomputeScoreEligible() {
    if (!_pianoWaterfallViewEl) return;
    const w = _pianoWaterfallViewEl.getBoundingClientRect().width;
    const h = window.innerHeight;
    // Overview-mode no longer gates score visibility — on PC the chord
    // ribbon (overview or card) and the waterfall are side-by-side, so
    // the waterfall area's own width remains the only meaningful gate.
    // The earlier rationale ("overview = song-overview, no need for
    // score") was rejected by the user after seeing overview + score
    // coexist comfortably side-by-side at 50/50 split.
    const eligible = (w >= SCORE_MIN_WIDTH) && (h >= SCORE_MIN_HEIGHT) &&
                     activeTab === "piano";
    document.body.classList.toggle("score-eligible", eligible);
    _applyScoreVisibility();
  }

  // ResizeObserver on the waterfall wrapper — reacts to ribbon collapse,
  // window resize, and tab switches (since piano tab is the only active host).
  if (typeof ResizeObserver !== "undefined" && _pianoWaterfallViewEl) {
    try { new ResizeObserver(_recomputeScoreEligible).observe(_pianoWaterfallViewEl); } catch (_) {}
  }
  window.addEventListener("resize", _recomputeScoreEligible);

  const btnToggleScore = $("#btnToggleScore");
  if (btnToggleScore) {
    btnToggleScore.addEventListener("click", () => {
      _showScore = !_showScore;
      try { localStorage.setItem("livechord_show_score", _showScore ? "true" : "false"); } catch (_) {}
      _applyScoreVisibility();
    });
  }
  document.addEventListener("livechord:langchange", _applyScoreVisibility);
  document.addEventListener("livechord:i18nready",  _applyScoreVisibility);
  // Initial gate — runs once layout is settled.
  setTimeout(_recomputeScoreEligible, 0);

  const _arrangerSplitButtons = {
    down: $("#btnArrangerSplitDown"),
    up: $("#btnArrangerSplitUp"),
    reset: $("#btnArrangerSplitReset"),
    value: $("#arrangerSplitValue"),
  };
  function _arrangerSplitName(midi) {
    // 固定顯示拼法（全降記號，唯 F# 升記號）。
    return semitoneToDisplay(midi) + (Math.floor(midi / 12) - 1);
  }
  function _getArrangerSplit() {
    const inst = (typeof InstrumentRegistry !== "undefined") ? InstrumentRegistry.get("arranger") : null;
    if (inst && typeof inst.getSplitPoint === "function") return inst.getSplitPoint();
    const stored = parseInt(localStorage.getItem("livechord_arranger_split") || "56", 10);
    return Number.isFinite(stored) ? Math.max(48, Math.min(60, stored)) : 56;
  }
  function _syncArrangerSplitUI() {
    const split = _getArrangerSplit();
    const note = (typeof ArrangerInstrument !== "undefined" && ArrangerInstrument.midiToName)
      ? ArrangerInstrument.midiToName(split)
      : _arrangerSplitName(split);
    if (_arrangerSplitButtons.value) _arrangerSplitButtons.value.textContent = note;
    if (_arrangerSplitButtons.down) _arrangerSplitButtons.down.disabled = split <= 48;
    if (_arrangerSplitButtons.up) _arrangerSplitButtons.up.disabled = split >= 60;
  }
  function _setArrangerSplit(next) {
    const inst = (typeof InstrumentRegistry !== "undefined") ? InstrumentRegistry.get("arranger") : null;
    const split = inst && typeof inst.setSplitPoint === "function"
      ? inst.setSplitPoint(next)
      : Math.max(48, Math.min(60, parseInt(next, 10)));
    if (!inst) localStorage.setItem("livechord_arranger_split", String(split));
    _syncArrangerSplitUI();
    const note = (typeof ArrangerInstrument !== "undefined" && ArrangerInstrument.midiToName)
      ? ArrangerInstrument.midiToName(split)
      : _arrangerSplitName(split);
    showToast(_t("toast.arranger_split.changed", { note }), 1200);
  }
  if (_arrangerSplitButtons.down) {
    _arrangerSplitButtons.down.addEventListener("click", () => _setArrangerSplit(_getArrangerSplit() - 1));
  }
  if (_arrangerSplitButtons.up) {
    _arrangerSplitButtons.up.addEventListener("click", () => _setArrangerSplit(_getArrangerSplit() + 1));
  }
  if (_arrangerSplitButtons.reset) {
    _arrangerSplitButtons.reset.addEventListener("click", () => _setArrangerSplit(56));
  }
  _syncArrangerSplitUI();
  document.addEventListener("livechord:i18nready", _syncArrangerSplitUI);

  // Export-data lives inside the toolbar Tools popup (moved from homepage
  // header menu) so the user reaches it mid-practice, where they're most
  // likely to want a backup of their ratings/recents/favorites.
  const btnExportData = $("#btnExportData");
  if (btnExportData) {
    btnExportData.addEventListener("click", async () => {
      try {
        showToast(_t("toast.export.exporting"), 2000);
        const res = await fetch("/api/export-data");
        if (!res.ok) { showToast(_t("toast.export.no_data"), 3000); return; }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.style.display = "none";
        a.href = url;
        a.download = `${localStorage.getItem("livechord_username") || "livechord"}_backup.zip`;
        document.body.appendChild(a);
        a.click();
        URL.revokeObjectURL(url);
        a.remove();
      } catch (e) {
        showToast(_t("toast.export.failed", { err: e.message || _t("toast.export.network_error") }), 3000);
      }
    });
  }

  const btnExportChords = $("#btnExportChords");
  if (btnExportChords) {
    btnExportChords.addEventListener("click", () => {
      if (!chordData || !chordData.chords || chordData.chords.length === 0) {
        showToast(_t("toast.error.no_chord_data"), 2000); return;
      }
      if (!window.ChordExporter || !window.ChordExporter.openModal) {
        showToast("ChordExporter not loaded", 2000); return;
      }
      // Close any open Tools popup so the modal isn't covered by it.
      document.querySelectorAll(".tb-item.open").forEach(i => i.classList.remove("open"));
      window.ChordExporter.openModal({
        chordData,
        sectionData,
        chordCache,
        transpose,
        capo,
        title: (chordData && chordData.title) || (songTitle ? songTitle.textContent : ""),
      });
    });
  }

  const btnChordAlign = $("#btnChordAlign");
  if (btnChordAlign) {
    btnChordAlign.addEventListener("click", () => {
      if (!chordData || !chordData.chords || chordData.chords.length === 0) {
        showToast(_t("toast.error.no_chord_data"), 2000); return;
      }
      window.ChordCorrection.enterChordAlign(
        chordData, _audioForCorrection, () => activeChordIdx, _corrRebuild
      );
    });
  }

  const btnAutoSplit = $("#btnAutoSplit");
  if (btnAutoSplit) {
    btnAutoSplit.addEventListener("click", () => {
      if (!chordData || !chordData.chords || chordData.chords.length === 0) {
        showToast(_t("toast.error.no_chord_data"), 2000); return;
      }
      _showAutoSplitPanel();
    });
  }

  function _showAutoSplitPanel() {
    // Remove any existing panel first
    document.querySelectorAll(".auto-split-panel").forEach(el => el.remove());

    // Load saved preferences — let the user's last choice stick across songs
    let mode = "bar";               // "bar" | "barsnap" | "ratio"
    let tsOverride = "auto";         // "auto" | "3" | "4" | "6"
    let threshold = 8;
    let ratio = "1:1";
    try {
      const s = JSON.parse(localStorage.getItem("livechord_auto_split") || "{}");
      if (s.mode === "bar" || s.mode === "barsnap" || s.mode === "ratio") mode = s.mode;
      if (s.tsOverride) tsOverride = String(s.tsOverride);
      if (s.threshold) threshold = s.threshold;
      if (s.ratio) ratio = s.ratio;
    } catch {}

    const CC = window.ChordCorrection;
    const inferred = CC.inferBeatsPerBar(chordData, currentSecPerBeat);
    // Drop "(預設)" when we're already inside "自動 (…)" — nested parens read awkwardly.
    const inferredLabel = inferred ? `${inferred}` : "4";
    const inferredHint = inferred
      ? _t("player.bar_split.inferred_hint", { n: inferred })
      : _t("player.bar_split.fallback_hint");
    // Guardrail: bar / barsnap modes need real downbeats[] to align cuts to
    // the music. Without them the algorithm falls back to a synthetic grid
    // anchored on the first chord, which is rarely on a real bar line — so
    // bars come out shifted, and the resulting card durations don't match
    // beatsPerBar × secPerBeat (visible as 1-2 dots per card after reload).
    // Force the user to run 升級節拍 first.
    const _hasDownbeats = Array.isArray(chordData && chordData.downbeats)
      && chordData.downbeats.length >= 2;
    if (!_hasDownbeats && (mode === "bar" || mode === "barsnap")) {
      mode = "ratio";
    }

    // Type B modal — backdrop + .lc-modal card. All styling via classes in player.css
    // (shared .lc-modal-backdrop + .lc-modal + auto-split-specific .as-* classes).
    const backdrop = document.createElement("div");
    backdrop.className = "auto-split-panel lc-modal-backdrop";

    const panel = document.createElement("div");
    panel.className = "lc-modal";
    panel.innerHTML = `
      <div class="lc-title">${_t("player.auto_split.title")}</div>
      ${!_hasDownbeats ? `
      <div class="as-no-downbeats-warn" style="background:rgba(255,193,7,.12); border:1px solid rgba(255,193,7,.4); color:#ffd54f; padding:8px 10px; border-radius:6px; font-size:13px; line-height:1.5; margin-bottom:10px;">
        ${_t("player.auto_split.no_downbeats_warn")}
      </div>` : ``}
      <div class="as-mode-row">
        <button class="as-mode-btn" data-mode="bar"${!_hasDownbeats ? ' disabled style="opacity:.4;cursor:not-allowed"' : ""}>${_t("player.auto_split.mode_bar")}</button>
        <button class="as-mode-btn" data-mode="barsnap"${!_hasDownbeats ? ' disabled style="opacity:.4;cursor:not-allowed"' : ""}>${_t("player.auto_split.mode_barsnap")}</button>
        <button class="as-mode-btn" data-mode="ratio">${_t("player.auto_split.mode_ratio")}</button>
      </div>

      <div class="as-bar-section as-section">
        <div class="as-section-label">${_t("player.auto_split.ts_label")}</div>
        <div class="as-ts-row"></div>
        <div class="as-bar-hint">${inferredHint}${_t("player.auto_split.bar_hint_suffix")}</div>
        <div class="as-snap-hint" style="display:none">
          ${_t("player.auto_split.snap_hint")}
        </div>
      </div>

      <div class="as-ratio-section as-section">
        <div class="as-section-label">${_t("player.auto_split.thresh_label")}</div>
        <div class="as-thresh-row">
          <button class="as-dec">−</button>
          <input class="as-thresh" type="number" min="2" max="32" value="${threshold}">
          <button class="as-inc">+</button>
          <span style="color:var(--text-dim); font-size:12px;">${_t("player.auto_split.thresh_unit")}</span>
        </div>
        <div class="as-section-label" style="margin-top:10px;">${_t("player.auto_split.ratio_label")}</div>
        <div class="as-ratios"></div>
        <div class="as-hint-small">${_t("player.auto_split.ratio_example")}</div>
      </div>

      <div class="lc-modal-actions">
        <button class="as-cancel">${_t("common.cancel")}</button>
        <button class="as-apply">${_t("common.apply")}</button>
      </div>
    `;
    backdrop.appendChild(panel);

    // --- Mode toggle ---
    const barSection = panel.querySelector(".as-bar-section");
    const ratioSection = panel.querySelector(".as-ratio-section");
    const barHint = panel.querySelector(".as-bar-hint");
    const snapHint = panel.querySelector(".as-snap-hint");
    const modeBtns = panel.querySelectorAll(".as-mode-btn");
    function syncModeUI() {
      modeBtns.forEach(b => b.classList.toggle("active", b.dataset.mode === mode));
      // bar + barsnap share the meter section (TS chips); ratio hides it.
      barSection.style.display = (mode === "bar" || mode === "barsnap") ? "" : "none";
      ratioSection.style.display = mode === "ratio" ? "" : "none";
      barHint.style.display = mode === "bar" ? "" : "none";
      snapHint.style.display = mode === "barsnap" ? "" : "none";
    }
    modeBtns.forEach(b => b.addEventListener("click", () => { mode = b.dataset.mode; syncModeUI(); }));
    syncModeUI();

    // --- Time-signature override chips ---
    const TS_OPTS = [
      { v: "auto", label: _t("player.bar_split.ts_auto", { n: inferredLabel }) },
      { v: "3",    label: "3/4" },
      { v: "4",    label: "4/4" },
      { v: "6",    label: "6/8 → 6" },
    ];
    const tsRow = panel.querySelector(".as-ts-row");
    TS_OPTS.forEach(o => {
      const btn = document.createElement("button");
      btn.textContent = o.label;
      btn.dataset.ts = o.v;
      btn.className = "as-chip";
      if (o.v === tsOverride) btn.classList.add("active");
      btn.addEventListener("click", () => {
        tsOverride = o.v;
        tsRow.querySelectorAll(".as-chip").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
      });
      tsRow.appendChild(btn);
    });

    // --- Ratio chips (legacy mode) ---
    const RATIOS = ["1:1", "1:2", "2:1", "1:3", "3:1", "2:3", "3:2", "1:1:1", "1:2:1"];
    const ratiosDiv = panel.querySelector(".as-ratios");
    RATIOS.forEach(r => {
      const btn = document.createElement("button");
      btn.textContent = r;
      btn.dataset.ratio = r;
      btn.className = "as-chip as-ratio-chip";
      if (r === ratio) btn.classList.add("active");
      btn.addEventListener("click", () => {
        ratio = r;
        ratiosDiv.querySelectorAll(".as-chip").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
      });
      ratiosDiv.appendChild(btn);
    });

    panel.querySelector(".as-dec").addEventListener("click", () => {
      const inp = panel.querySelector(".as-thresh");
      inp.value = Math.max(2, (parseInt(inp.value) || 8) - 1);
    });
    panel.querySelector(".as-inc").addEventListener("click", () => {
      const inp = panel.querySelector(".as-thresh");
      inp.value = Math.min(32, (parseInt(inp.value) || 8) + 1);
    });

    const close = () => { backdrop.remove(); document.removeEventListener("keydown", keyHandler); };
    const keyHandler = (e) => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", keyHandler);
    // Click on backdrop (not on modal card) closes the dialog.
    backdrop.addEventListener("click", (e) => { if (e.target === backdrop) close(); });
    panel.querySelector(".as-cancel").addEventListener("click", close);

    panel.querySelector(".as-apply").addEventListener("click", () => {
      threshold = Math.max(2, Math.min(32, parseInt(panel.querySelector(".as-thresh").value) || 8));
      try {
        localStorage.setItem("livechord_auto_split",
          JSON.stringify({ mode, tsOverride, threshold, ratio }));
      } catch {}

      CC.backup(chordData);

      // COMMIT the currently-displayed BPM into chordData.bpm before
      // splitting. Two reasons:
      //  1. After split, _buildUnifiedRibbon recomputes estimatedBpm. If
      //     chordData.bpm is missing (e.g., BTC-fresh song where librosa
      //     judged tempo < 40 and beat_snap rejected it) the heuristic
      //     kicks in and DOUBLES after split (median inter-chord diff
      //     halves), giving user a shocking jump from "98" → "195".
      //  2. Clear bpm_mult_* so the committed value doesn't get re-
      //     multiplied on top, producing yet another wrong reading.
      const committedBpm = Math.max(30, Math.min(300,
        Math.round(60 / currentSecPerBeat)));
      if (committedBpm > 0) {
        chordData.bpm = committedBpm;
        try {
          const bpmPath = new URLSearchParams(window.location.search).get("path") || "default";
          localStorage.removeItem(`bpm_mult_${bpmPath}`);
        } catch {}
      }

      let count = 0;
      let alignFills = 0;
      let snapCount = 0;
      let fragmentSkips = 0;

      // --- Shared: nearest-bar-line resolver for bar + barsnap modes ---
      // Uses real downbeats[] when present; falls back to a synthetic grid
      // anchored on the first detected downbeat/beat, else the first chord.
      const resolvedBeatsPerBar =
        tsOverride !== "auto" ? parseInt(tsOverride, 10) : (inferred || 4);
      const phase = (chordData.downbeats && chordData.downbeats[0])
        || (Array.isArray(chordData.beats) && chordData.beats[0])
        || (chordData.chords && chordData.chords[0] && chordData.chords[0].time)
        || 0;
      const barDur = resolvedBeatsPerBar * currentSecPerBeat;
      const nearestBarLine = (t) => {
        // Prefer real downbeats (madmom-detected)
        const dbs = chordData.downbeats || [];
        if (dbs.length >= 2) {
          // Binary search would be faster, but chord counts are small.
          let best = dbs[0], bestD = Math.abs(t - dbs[0]);
          for (let j = 1; j < dbs.length; j++) {
            const d = Math.abs(t - dbs[j]);
            if (d < bestD) { best = dbs[j]; bestD = d; }
          }
          // Extrapolate beyond last downbeat if t is way past it
          const last = dbs[dbs.length - 1];
          if (t > last) {
            const k = Math.round((t - last) / barDur);
            const extrapolated = last + k * barDur;
            if (Math.abs(t - extrapolated) < bestD) return extrapolated;
          }
          return best;
        }
        // Fallback: synthetic grid
        const k = Math.round((t - phase) / barDur);
        return phase + k * barDur;
      };

      if (mode === "barsnap") {
        // --- Boundary relocation: snap inner boundaries to nearest bar line ---
        // Tolerance = half a bar; outside that, leave alone (probably intentional
        // off-grid placement). Forward pass — first chord's start and last chord's
        // end are preserved (song endpoints don't move).
        const tol = barDur / 2;
        const chords = chordData.chords;
        for (let i = 0; i < chords.length - 1; i++) {
          const t = chords[i].end;
          if (t == null) continue;
          const bl = nearestBarLine(t);
          const dist = Math.abs(bl - t);
          if (dist > tol || dist < 1e-3) continue;
          // Don't collapse a chord to zero or near-zero, and don't overlap previous end.
          const minGap = currentSecPerBeat * 0.5;
          if (bl - chords[i].time < minGap) continue;
          if (i + 1 < chords.length && chords[i + 1].end != null && chords[i + 1].end - bl < minGap) continue;
          chords[i].end = Math.round(bl * 100) / 100;
          chords[i + 1].time = chords[i].end;
          snapCount++;
        }
        // After snap, fall through into the bar-split loop to slice any chord
        // still longer than one bar (e.g. a 2-bar chord snapped cleanly remains
        // 8 beats and the user still wants per-bar cards).
        // (No 'return' here — continue into the bar-mode loop below.)
      }

      if (mode === "bar" || mode === "barsnap") {
        // --- Bar-aligned split (runs for both bar and barsnap; barsnap already
        // relocated boundaries above, this pass slices any chord still >1 bar) ---
        const beatsPerBar = resolvedBeatsPerBar;

        const nextBarAnchor = (t) => {
          const db = CC.nextDownbeatAfter(chordData, t);
          if (db != null) return db;
          // Fallback: grid derived from shared phase + beatsPerBar.
          const k = Math.ceil((t - phase - 1e-6) / barDur);
          return phase + k * barDur;
        };

        // Reverse iteration so inserted chords (from splits) don't disturb
        // our walk. Each split inserts at idx+1, left-side indices unaffected.
        for (let i = chordData.chords.length - 1; i >= 0; i--) {
          const c = chordData.chords[i];
          const dur = c.end
            ? c.end - c.time
            : (i < chordData.chords.length - 1 ? chordData.chords[i + 1].time - c.time : 2.0);
          const beats = Math.round(dur / currentSecPerBeat);
          if (beats <= beatsPerBar) continue;

          // First cut: fill the remainder of the current bar if not aligned.
          const anchor = nextBarAnchor(c.time);
          let firstSplitBeats;
          if (anchor > c.time + 1e-3 && anchor < c.time + dur - 1e-3) {
            firstSplitBeats = Math.round((anchor - c.time) / currentSecPerBeat);
            // Guard against rounding that would produce a whole-bar or
            // zero-beat first slice — fall back to a clean bar-sized cut.
            if (firstSplitBeats < 1 || firstSplitBeats >= beats) {
              firstSplitBeats = beatsPerBar;
            }
          } else {
            firstSplitBeats = beatsPerBar;
          }

          if (beatsPerBar === 4
              && ((beats === 4 && (firstSplitBeats === 1 || firstSplitBeats === 3))
                  || (beats === 5 && (firstSplitBeats === 1 || firstSplitBeats === 4)))) {
            fragmentSkips++;
            continue;
          }

          let cursor = i;
          let remaining = beats;
          let didSplit = false;

          if (firstSplitBeats < beats && firstSplitBeats !== beatsPerBar) {
            CC.splitChord(chordData, cursor, firstSplitBeats, remaining - firstSplitBeats, currentSecPerBeat);
            remaining -= firstSplitBeats;
            cursor++;
            alignFills++;
            didSplit = true;
          }

          while (remaining > beatsPerBar) {
            CC.splitChord(chordData, cursor, beatsPerBar, remaining - beatsPerBar, currentSecPerBeat);
            remaining -= beatsPerBar;
            cursor++;
            didSplit = true;
          }

          if (didSplit) count++;
        }

        close();
        if (mode === "barsnap") {
          if (snapCount > 0 || count > 0) {
            _corrRebuild();
            const parts = [];
            if (snapCount > 0) parts.push(_t("toast.split.aligned_msg", { snapCount }));
            if (count > 0) parts.push(_t("toast.split.split_msg", { count }));
            showToast(_t("toast.split.barsnap_done", { parts: parts.join("、"), beatsPerBar }), 2500);
          } else {
            showToast(_t("toast.split.barsnap_none", { beatsPerBar }), 2000);
          }
        } else {
          if (count > 0) {
            _corrRebuild();
            const tail = alignFills > 0 ? _t("toast.split.bar_filltail", { n: alignFills }) : "";
            const guardTail = fragmentSkips > 0 ? ` (${fragmentSkips} suspicious fragments skipped)` : "";
            showToast(_t("toast.split.bar_done", { count, beatsPerBar, tail }) + guardTail, 2500);
          } else if (fragmentSkips > 0) {
            showToast(`Skipped ${fragmentSkips} suspicious 1+3/3+1/4+1 split(s)`, 2500);
          } else {
            showToast(_t("toast.split.bar_none", { beatsPerBar }), 2000);
          }
        }
        return;
      }

      // --- Legacy ratio split ---
      const parts = ratio.split(":").map(Number);
      const partSum = parts.reduce((a, b) => a + b, 0);

      for (let i = chordData.chords.length - 1; i >= 0; i--) {
        const c = chordData.chords[i];
        const dur = c.end
          ? c.end - c.time
          : (i < chordData.chords.length - 1 ? chordData.chords[i + 1].time - c.time : 2.0);
        const beats = Math.round(dur / currentSecPerBeat);
        if (beats <= threshold) continue;

        const raw = parts.map(p => Math.max(1, Math.round(beats * p / partSum)));
        const allocated = raw.reduce((a, b) => a + b, 0);
        const diff = beats - allocated;
        if (diff !== 0) {
          const maxIdx = raw.indexOf(Math.max(...raw));
          raw[maxIdx] += diff;
        }
        if (raw.some(v => v < 1) || raw.reduce((a, b) => a + b, 0) !== beats) continue;

        let cursor = i;
        let remaining = beats;
        for (let p = 0; p < raw.length - 1; p++) {
          CC.splitChord(chordData, cursor, raw[p], remaining - raw[p], currentSecPerBeat);
          remaining -= raw[p];
          cursor++;
        }
        count++;
      }

      close();
      if (count > 0) {
        _corrRebuild();
        showToast(_t("toast.split.ratio_done", { count, threshold, ratio }), 2500);
      } else {
        showToast(_t("toast.split.ratio_none", { threshold }), 2000);
      }
    });

    document.body.appendChild(backdrop);
  }

  const btnSaveCorrected = $("#btnSaveCorrected");
  if (btnSaveCorrected) {
    btnSaveCorrected.addEventListener("click", async () => {
      if (!chordData) return;
      try {
        const result = await API.saveChords({
          path: trackPath,
          key: chordData.key || "",
          capo: capo,
          bpm: chordData.bpm || 0,
          chords: chordData.chords,
        });
        if (result && result.version) {
          currentChordVersion = result.version;
          const url = new URL(window.location);
          url.searchParams.set("version", result.version);
          window.history.replaceState({}, "", url);
          if (window._updateEditLink) window._updateEditLink();
        }
        showToast(_t("toast.corr.saved"), 2000);
        btnSaveCorrected.style.display = "none";
      } catch (err) {
        showToast(_t("toast.corr.save_failed", { err: err.message }), 3000);
      }
    });
  }

  const btnRevertCorrection = $("#btnRevertCorrection");
  if (btnRevertCorrection) {
    btnRevertCorrection.addEventListener("click", async () => {
      if (window.ChordCorrection && window.ChordCorrection.hasBackup()) {
        window.ChordCorrection.revert(chordData, _corrRebuild);
        btnSaveCorrected.style.display = "none";
        btnRevertCorrection.style.display = "none";
        // Persist the reverted (original) state so disk matches what the
        // user now sees. Without this, next page-load would re-serve the
        // corrected version from the user file.
        if (trackPath) {
          try {
            const cleanChords = _cleanChordsForSave(chordData.chords);
            chordData.chords = cleanChords;
            const result = await API.saveChords({
              path: trackPath,
              key: chordData.key || "",
              capo: capo,
              bpm: chordData.bpm || 0,
              chords: cleanChords,
            });
            if (result && result.version) currentChordVersion = result.version;
            showToast(_t("toast.corr.reverted_saved"), 1800);
          } catch (err) {
            showToast(_t("toast.corr.reverted_save_failed", { err: err.message }), 2500);
          }
        } else {
          showToast(_t("toast.corr.reverted"), 2000);
        }
      }
    });
  }

  // ---- AI 建議按鈕 ----
  const btnAiSuggest = $("#btnAiSuggest");
  if (btnAiSuggest) {
    btnAiSuggest.addEventListener("click", async () => {
      if (!chordData || !chordData.chords || chordData.chords.length === 0) {
        showToast(_t("toast.error.no_chord_data"), 2000);
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
          showToast(_t("toast.ai.suggest", { recent: recent.join("→"), msg }), 5000);
        } else {
          showToast(_t("toast.ai.failed_to_predict"), 2000);
        }
      } catch (err) {
        showToast(_t("toast.ai.predict_failed", { err: err.message }), 3000);
      }
    });
  }

  // ---- Jazzify 按鈕 (merged: off → L1 → L2 → L3 → ✨AI → off) ----
  const btnJazzify = $("#btnJazzify");
  const jazzExplainEl = $("#jazzExplain");
  const JAZZ_STRANDS_KEY = "livechord_jazzify_strands";
  const ALLOWED_JAZZ_EXTRA_STRANDS = ["diminished_leading", "modal_interchange", "five_alternatives"];
  let jazzifyLevel = 0;  // 0=off, 1/2/3=rule-based, 4=AI transformer
  let originalChords = null;
  let jazzifyReqGen = 0;  // generation counter — stale async callbacks check this
  let jazzifyExtraStrands = _loadJazzifyExtraStrands();

  function _loadJazzifyExtraStrands() {
    try {
      const raw = localStorage.getItem(JAZZ_STRANDS_KEY);
      if (!raw) return [];
      const arr = JSON.parse(raw);
      if (!Array.isArray(arr)) return [];
      return arr.filter(s => ALLOWED_JAZZ_EXTRA_STRANDS.includes(String(s)));
    } catch (_) {
      return [];
    }
  }

  function _saveJazzifyExtraStrands() {
    try {
      localStorage.setItem(JAZZ_STRANDS_KEY, JSON.stringify(jazzifyExtraStrands));
    } catch (_) {}
  }

  function _baseJazzStrandsByLevel(lvl) {
    if (lvl >= 3) return ["diatonic", "ii_v", "tritone_sub", "secondary_dominant"];
    if (lvl === 2) return ["diatonic", "ii_v"];
    if (lvl === 1) return ["diatonic"];
    return [];
  }

  function _effectiveJazzStrands(apiLevel) {
    const set = new Set(_baseJazzStrandsByLevel(apiLevel));
    jazzifyExtraStrands.forEach(s => set.add(s));
    return Array.from(set);
  }

  function _syncJazzStrandPopup() {
    const activeSet = new Set(jazzifyExtraStrands);
    document.querySelectorAll(".jazz-strand-opt").forEach(btn => {
      const on = activeSet.has(btn.dataset.strand);
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function _strandLabel(strand) {
    switch (String(strand || "")) {
      case "ii_v": return "II-V";
      case "tritone_sub": return "三全音";
      case "secondary_dominant": return "次屬";
      case "diminished_leading": return "減導";
      case "modal_interchange": return "借用";
      case "five_alternatives": return "V替代";
      case "diatonic":
      default:
        return "基礎";
    }
  }

  function _attachJazzExplainToChords(chords, explainSteps) {
    if (!Array.isArray(chords) || chords.length === 0) return;
    const posToStrand = new Map();
    if (Array.isArray(explainSteps)) {
      explainSteps.forEach(step => {
        const pos = Number(step && step.position);
        if (Number.isInteger(pos) && pos >= 0 && pos < chords.length) {
          posToStrand.set(pos, String(step.strand || "diatonic"));
        }
      });
    }

    chords.forEach((ch, idx) => {
      if (!ch || typeof ch !== "object") return;
      const strand = posToStrand.get(idx) || (ch.explain && ch.explain.strand) || "diatonic";
      const source = (ch.explain && ch.explain.source) || (posToStrand.has(idx) ? "changed" : (ch.inserted ? "inserted" : "carried"));
      ch.explain = { strand, source };
    });
  }

  function _renderJazzExplain(res) {
    if (!jazzExplainEl) return;
    if (!res || !Array.isArray(res.explain) || !res.explain.length) {
      jazzExplainEl.textContent = _t(
        "player.jazzify.explain_empty",
        null,
        "Choose a Jazzify level to preview theory steps."
      );
      return;
    }
    const top = res.explain.slice(0, 3);
    const lines = top.map(x => `${x.step}. [${_strandLabel(x.strand)}] ${x.rule}: ${x.from} -> ${x.to}`).join("\n");
    const more = res.explain.length > top.length ? `\n+${res.explain.length - top.length} more` : "";
    jazzExplainEl.innerHTML =
      `<span class="jazz-explain-title">${_t("player.jazzify.explain_title", null, "Theory steps")}</span>` +
      `<span>${lines.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>")}${more ? `<br>${more}` : ""}</span>`;
  }

  function _syncJazzifyPopup() {
    document.querySelectorAll(".jazz-opt").forEach(b => {
      b.classList.toggle("active", parseInt(b.dataset.jazz, 10) === jazzifyLevel);
    });
  }
  _syncJazzifyPopup();
  _syncJazzStrandPopup();
  _renderJazzExplain(null);

  async function _setJazzifyLevel(lvl, force = false) {
    // Allow the call to proceed even if current chords went empty — so long
    // as we have an `originalChords` backup to restore to. Without this, a
    // degenerate API response (empty res.chords) would pin the button on AI
    // state forever.
    const chordsEmpty = !chordData || !chordData.chords || chordData.chords.length === 0;
    if (chordsEmpty && !originalChords) {
      showToast("\u5C1A\u7121\u548C\u5F26\u8CC7\u6599", 2000);
      return;
    }
    if (lvl === jazzifyLevel && lvl !== 0 && !force) return;  // no-op: already at level

    jazzifyLevel = lvl;
    _syncJazzifyPopup();
    const myGen = ++jazzifyReqGen;

    if (jazzifyLevel === 0) {
      if (originalChords) {
        chordData.chords = originalChords;
        originalChords = null;
      }
      btnJazzify.textContent = "\u{1F3B7}";
      btnJazzify.style.background = "";
      btnJazzify.style.color = "";
      chordCache = {};
      await preloadChordInfo(chordData.chords);
      if (myGen !== jazzifyReqGen) return;
      buildChordDOM();
      activeChordIdx = -1;
      requestAnimationFrame(() => updateActiveChord(audio.currentTime || -1, true));
      _renderJazzExplain(null);
      showToast("\u5DF2\u9084\u539F\u539F\u59CB\u548C\u5F26", 1500);
      return;
    }

    if (!originalChords) {
      originalChords = [...chordData.chords];
    }

    btnJazzify.textContent = "\u23F3";

    const isAI = (jazzifyLevel === 4);
    const apiLevel = isAI ? 3 : jazzifyLevel;
    const mode = isAI ? "transformer" : "rule-based";
    const strandFlags = _effectiveJazzStrands(apiLevel);

    try {
      const res = await API.jazzify(
        originalChords,
        chordData.key || "C",
        apiLevel,
        mode,
        chordData && chordData.bpm || null,
        strandFlags,
      );
      if (myGen !== jazzifyReqGen) return;
      if (res.error) throw new Error(res.error);
      if (!Array.isArray(res.chords) || res.chords.length === 0) {
        throw new Error("\u4F3A\u670D\u5668\u672A\u56DE\u50B3\u548C\u5F26");
      }
      chordData.chords = res.chords;
      _attachJazzExplainToChords(chordData.chords, res.explain);

      if (isAI) {
        btnJazzify.textContent = "\u2728AI";
        btnJazzify.style.background = "rgba(156,39,176,.3)";
        btnJazzify.style.color = "#9c27b0";
      } else {
        btnJazzify.textContent = `${jazzifyLevel}`;
        btnJazzify.style.background = "rgba(255,152,0,.3)";
        btnJazzify.style.color = "#ff9800";
      }

      chordCache = {};
      await preloadChordInfo(chordData.chords);
      if (myGen !== jazzifyReqGen) return;
      buildChordDOM();
      activeChordIdx = -1;
      requestAnimationFrame(() => updateActiveChord(audio.currentTime || -1, true));
      _renderJazzExplain(res);
      const label = isAI ? "AI Transformer" : `Jazzify L${jazzifyLevel}`;
      showToast(`${label}: ${res.original_count}\u2192${res.jazzified_count} \u548C\u5F26, ${res.changes.length} \u8B8A\u66F4`, 3000);
    } catch (err) {
      if (myGen !== jazzifyReqGen) return;
      showToast("Jazzify \u5931\u6557: " + err.message, 3000);
      jazzifyLevel = 0;
      _syncJazzifyPopup();
      _renderJazzExplain(null);
      btnJazzify.textContent = "\u{1F3B7}";
      btnJazzify.style.background = "";
      btnJazzify.style.color = "";
    }
  }

  // Jazzify trigger: popup-only (no click-cycle). The state cycle was easy to
  // misfire — opening the popup is the same gesture as every other toolbar
  // button, so users can pick L1/L2/L3/AI/Off explicitly.
  document.querySelectorAll(".jazz-opt").forEach(b => {
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      _setJazzifyLevel(parseInt(b.dataset.jazz, 10));
      const item = b.closest(".tb-item");
      if (item) item.classList.remove("open");
    });
  });

  document.querySelectorAll(".jazz-strand-opt").forEach(b => {
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      const strand = b.dataset.strand;
      if (!ALLOWED_JAZZ_EXTRA_STRANDS.includes(strand)) return;
      if (jazzifyExtraStrands.includes(strand)) {
        jazzifyExtraStrands = jazzifyExtraStrands.filter(s => s !== strand);
      } else {
        jazzifyExtraStrands = [...jazzifyExtraStrands, strand];
      }
      _saveJazzifyExtraStrands();
      _syncJazzStrandPopup();
      if (jazzifyLevel !== 0) _setJazzifyLevel(jazzifyLevel, true);
    });
  });

  // ---- manual detect: shared by Tools popup button + hero empty-state button (Task 6) ----
  async function runChordDetection() {
    if (!trackPath) { showToast(_t("toast.detect.hash_only"), 3000); return; }
    detectOverlay.style.display = "";
    detectMsg.textContent = _t("detect.progress.ai_analyzing");
    detectDetail.textContent = _t("detect.progress.ai_analyzing_detail");

    try {
      // BTC-only detection — MIDI auto-import was retired after fuzzy
      // substring matches polluted 10k chord JSONs (LiveChord-a7c).
      const result = await API.detectChords(trackPath);
      if (result.chord_count === 0) {
        showToast(_t("toast.detect.no_chords"), 5000);
      } else {
        showToast(_t("toast.detect.done", { count: result.chord_count, key: result.key }), 3000);
      }
      chordCache = {};
      await loadChords(trackPath);
      updateActiveChord(audio.currentTime || -1);
    } catch (err) {
      showToast(_t("toast.detect.failed", { err: err.message }), 4000);
    } finally {
      detectOverlay.style.display = "none";
    }
  }

  const btnDetect = $("#btnDetect");
  if (btnDetect) {
    btnDetect.addEventListener("click", () => runChordDetection());
    // In beta mode the detect / MIDI-import path requires trackPath (NAS), which
    // beta non-admin users don't have — hide the button rather than let them
    // tap it and get "此頁為 hash 模式" toast.
    _isDetectDisabledModeAsync.then(disabled => {
      if (disabled) btnDetect.style.display = "none";
    }).catch(() => {});
  }
  // Hero detect button (Task 6) is injected into empty chord state; bind via delegation.
  document.addEventListener("click", (e) => {
    if (e.target && e.target.id === "btnDetectHero") runChordDetection();
  });

  // ---- 動態節拍偵測 (背景處理) ----
  // Default ingest uses librosa (fast); this opt-in enqueues a madmom job
  // on the backend and polls for completion. Client is non-blocking — user
  // can navigate around while the ~30s job runs. Toast on done/error.
  const btnUpgradeBeats = $("#btnUpgradeBeats");
  if (btnUpgradeBeats) {
    let _upgradePoll = null;  // setInterval handle for the active poll
    btnUpgradeBeats.addEventListener("click", async () => {
      const h = (typeof hashMode === "string" && hashMode) ? hashMode :
                (chordData && chordData.path && chordData.path.startsWith("__hash/")
                 ? chordData.path.slice(7) : null);
      let targetHash = h;
      if (!targetHash && chordData && chordData.path) {
        showToast(_t("toast.beat_upgrade.no_hash_use_admin"), 3000);
        return;
      }
      if (!targetHash) {
        showToast(_t("toast.beat_upgrade.no_song_hash"), 2500);
        return;
      }
      if (_upgradePoll) {
        showToast(_t("toast.beat_upgrade.in_progress"), 2500);
        return;
      }
      const ok = confirm(_t("player.beat_upgrade.confirm"));
      if (!ok) return;

      btnUpgradeBeats.disabled = true;
      try {
        const res = await fetch(`/api/process/upgrade-beats?hash=${encodeURIComponent(targetHash)}`,
                                { method: "POST" });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          const detail = data.detail || `HTTP ${res.status}`;
          showToast(_t("toast.beat_upgrade.cannot_start", { detail }), 5000);
          btnUpgradeBeats.disabled = false;
          return;
        }
        const songTitle = data.title || _t("toast.beat_switch.queued_song");
        if (data.duplicate) {
          showToast(_t("toast.beat_upgrade.duplicate", { title: songTitle }), 3500);
        } else {
          showToast(_t("toast.beat_upgrade.started", { title: songTitle }), 4500);
        }
        // Start polling — non-blocking, user free to navigate.
        // Sub-status (running:downloading / running:analyzing) updates the
        // toast wording so YT-mode users see "重新下載音檔中…" before the
        // ~30s madmom phase kicks in.
        let _lastSubStatus = "";
        _upgradePoll = setInterval(async () => {
          try {
            const sres = await fetch(
              `/api/process/upgrade-beats/status?hash=${encodeURIComponent(targetHash)}`
            );
            if (!sres.ok) return;
            const sdata = await sres.json();
            const st = sdata.status;
            if (st === "done") {
              clearInterval(_upgradePoll); _upgradePoll = null;
              btnUpgradeBeats.disabled = false;
              const r = sdata.result || {};
              const refreshed = await _refreshChordDataInPlace();
              const tail = refreshed ? _t("toast.beat_upgrade.applied_inplace") : _t("toast.beat_upgrade.reload_required");
              showToast(
                _t("toast.beat_upgrade.done", { title: sdata.title || songTitle, bpm: r.bpm, nBeats: r.n_beats, tempoRange: r.tempo_range, tail }),
                7000
              );
            } else if (st === "error") {
              clearInterval(_upgradePoll); _upgradePoll = null;
              btnUpgradeBeats.disabled = false;
              showToast(
                _t("toast.beat_upgrade.failed", { title: sdata.title || songTitle, err: sdata.error || _t("toast.beat_upgrade.unknown_err") }),
                6000
              );
            } else if (st === "running:downloading" && _lastSubStatus !== "running:downloading") {
              _lastSubStatus = "running:downloading";
              showToast(_t("toast.beat_upgrade.redownloading", { title: sdata.title || songTitle }), 4500);
            } else if (st === "running:analyzing" && _lastSubStatus !== "running:analyzing") {
              _lastSubStatus = "running:analyzing";
              showToast(_t("toast.beat_upgrade.analyzing", { title: sdata.title || songTitle }), 4500);
            } else if (st === "not_found") {
              // Worker may not have picked it up yet; keep polling a few more rounds
            }
          } catch (e) {
            // Network blip — keep polling
            console.warn("upgrade-beats poll error:", e);
          }
        }, 4000);
      } catch (e) {
        console.error("upgrade-beats POST failed:", e);
        showToast(_t("toast.beat_upgrade.cannot_start", { detail: e.message || e }), 5000);
        btnUpgradeBeats.disabled = false;
      }
    });
  }

  // ---- 自動節拍調整 (bar arbitrator force / restore toggle) ----
  // Personal-only test tool. Calls the same admin endpoints as the admin UI,
  // but scoped to the currently-loaded song and toggles between
  // apply (force=true) and restore based on chordData.bar_correction.applied.
  const btnBarArbitrate = $("#btnBarArbitrate");
  const btnBarArbitrateLabel = $("#btnBarArbitrateLabel");

  function _updateBarArbitrateLabel() {
    if (!btnBarArbitrate || !btnBarArbitrateLabel) return;
    const bar = chordData && chordData.bar_correction;
    const applied = !!(bar && bar.applied);
    if (applied) {
      const tag = (bar.model_version === "model_v1") ? "AI" : _t("bar.tag_rule");
      const conf = (typeof bar.score_after === "number") ? ` ${bar.score_after.toFixed(2)}` : "";
      btnBarArbitrateLabel.textContent = _t("bar.applied", { tag, bpb: bar.beats_per_bar, conf });
    } else {
      btnBarArbitrateLabel.textContent = _t("player.tools.bar_arbitrate");
    }
  }

  if (btnBarArbitrate) {
    btnBarArbitrate.addEventListener("click", async () => {
      if (!chordData) { showToast(_t("toast.error.chord_data_not_loaded"), 3000); return; }
      const bar = chordData.bar_correction;
      const isApplied = !!(bar && bar.applied);
      const hasSnapshot = !!(bar && Array.isArray(bar.downbeats_original));
      const body = hashMode ? { hash: hashMode } : { path: trackPath };
      const url = isApplied ? "/api/admin/bar/restore" : "/api/admin/bar/recompute";
      const payload = isApplied ? body : { ...body, force: true };

      // Restore needs an existing snapshot — early-warn user
      if (isApplied && !hasSnapshot) {
        showToast(_t("toast.bar.no_snapshot"), 5000);
        return;
      }
      btnBarArbitrate.disabled = true;
      const origLabel = btnBarArbitrateLabel.textContent;
      btnBarArbitrateLabel.textContent = _t(isApplied ? "player.bar_arb.reverting" : "player.bar_arb.arbitrating");
      try {
        const r = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) {
          showToast(_t("toast.bar.failed_status", { status: r.status, detail: data.detail || r.statusText }), 5000);
          btnBarArbitrateLabel.textContent = origLabel;
          return;
        }
        // Refresh chord data + tooltip + ribbon. _refreshChordDataInPlace handles
        // tooltip via the BPM badge code path on rebuild.
        const refreshed = await _refreshChordDataInPlace();
        if (refreshed) {
          _buildUnifiedRibbon();
          _updateBarArbitrateLabel();
          if (isApplied) {
            showToast(_t("toast.bar.reverted", { n: data.n_downbeats || "?" }), 3500);
          } else {
            showToast(
              _t("toast.bar.applied", {
                version: data.model_version || "model",
                beatsPerBar: data.beats_per_bar,
                before: data.n_downbeats_before,
                after: data.n_downbeats_after,
                conf: (data.score_after || 0).toFixed(2)
              }),
              5000
            );
          }
        } else {
          showToast(_t("toast.bar.applied_reload_failed"), 4000);
          btnBarArbitrateLabel.textContent = origLabel;
        }
      } catch (e) {
        console.error("bar arbitrate failed:", e);
        showToast(_t("toast.bar.failed", { err: e.message || e }), 4000);
        btnBarArbitrateLabel.textContent = origLabel;
      } finally {
        btnBarArbitrate.disabled = false;
      }
    });
  }
  // Refresh label whenever chordData reloads — wire into existing refresh hook
  // by patching _refreshChordDataInPlace's caller surface lightly: every place
  // that calls it already builds chord DOM, so we hook off chord-quality badge.
  // Simpler: also call once at script-init below after chordData is first loaded.

  // ---- 節拍來源切換 (personal 8800 only) ----
  // 3-way librosa / madmom / beat_this picker. The backend serves the
  // canonical cached tracker by default; this picker persists per-song manual
  // overrides without leaking one song's choice into another.
  // Backend endpoint is gated by
  // require_personal_mode so beta (8801) gets 404; we also hide the UI on beta.
  // Cached swaps are <1s (reuse .bak.librosa / .bak.madmom / .bak.beat_this);
  // fresh librosa runs sync (~2s); fresh madmom enqueues (~30s bg) — reuses
  // upgrade-beats poll. beat_this has no on-demand path on NUC (no CUDA) —
  // backend returns 503 if .bak.beat_this missing; user must run PC bulk batch.
  const btnBeatLibrosa = $("#btnBeatLibrosa");
  const btnBeatMadmom  = $("#btnBeatMadmom");
  const btnBeatBeatThis = $("#btnBeatBeatThis");
  const _isBetaLane = location.port === "8801"
                      || location.hostname.endsWith("livechord.org");

  function _categoryOf(src) {
    const s = String(src || "").toLowerCase();
    if (s.includes("beat_this")) return "beat_this";
    if (s.includes("madmom")) return "madmom";
    return "librosa";
  }

  function _currentBeatCategory() {
    return _categoryOf(chordData && chordData.beats_source);
  }

  // Sync toggle active class from an explicit source (authoritative) or from
  // chordData.beats_source (fallback). Called both on initial load and after
  // a switch response — the server-returned beats_source is the source of
  // truth, not the potentially-stale chordData.
  function _syncBeatSourceToggle(overrideSrc) {
    if (!btnBeatLibrosa || !btnBeatMadmom) return;
    if (_isBetaLane) return;
    const src = (overrideSrc !== undefined)
      ? overrideSrc
      : (chordData && chordData.beats_source);
    const cat = _categoryOf(src);
    btnBeatLibrosa.classList.toggle("active", cat === "librosa");
    btnBeatMadmom.classList.toggle("active", cat === "madmom");
    if (btnBeatBeatThis) btnBeatBeatThis.classList.toggle("active", cat === "beat_this");
  }

  // Diagnostic hook — call window.__lcBeatDebug() in DevTools to dump the
  // current toggle vs chordData vs backend view side-by-side.
  window.__lcBeatDebug = async function () {
    const info = {
      chordData_beats_source: chordData && chordData.beats_source,
      chordData_bpm: chordData && chordData.bpm,
      n_downbeats: chordData && Array.isArray(chordData.downbeats)
        ? chordData.downbeats.length : null,
      libActive: btnBeatLibrosa && btnBeatLibrosa.classList.contains("active"),
      madActive: btnBeatMadmom  && btnBeatMadmom.classList.contains("active"),
      btActive:  btnBeatBeatThis && btnBeatBeatThis.classList.contains("active"),
      requested_default: _playerBeatSourcePreference(),
    };
    try {
      const p = new URLSearchParams();
      if (hashMode) p.set("hash", hashMode);
      else if (chordData && chordData.path) p.set("path", chordData.path);
      p.set("beat_source", _playerBeatSourcePreference());
      const r = await fetch(`/api/chords${hashMode
        ? `/by-hash?${p.toString()}`
        : `?${p.toString()}`}`);
      const d = await r.json();
      info.backend_beats_source = d.beats_source;
      info.backend_bpm = d.bpm;
      info.backend_n_downbeats = Array.isArray(d.downbeats)
        ? d.downbeats.length : null;
      info.backend_current_version = d.current_version;
    } catch (e) { info.backend_error = String(e); }
    console.table(info);
    return info;
  };

  // Reveal toggle on personal lane; hide the old single-direction button.
  // On beta, keep the old button, hide the toggle (status quo).
  if (btnBeatLibrosa && btnBeatMadmom && !_isBetaLane) {
    btnBeatLibrosa.style.display = "";
    btnBeatMadmom.style.display = "";
    if (btnBeatBeatThis) btnBeatBeatThis.style.display = "";
    if (btnUpgradeBeats) btnUpgradeBeats.style.display = "none";
  }

  let _beatSwitchPoll = null;
  let _beatSwitchBusy = false;

  async function _switchBeatsTo(mode) {
    if (_beatSwitchBusy) {
      showToast(_t("toast.beat_switch.in_progress"), 2000);
      return;
    }
    // No client-side early-return: client view of chordData.beats_source can
    // diverge from the server's canonical sheet (ChordSheet Pydantic strips
    // beat fields from user-version files, browser cache may be stale, etc).
    // Always let the backend decide — it responds {already: true} with the
    // authoritative beats_source when no-op, which we use to sync the UI.

    // Decide hash vs path routing (same dual-mode logic as upgrade-beats).
    // 8800 path-mode sends path; hash-mode sends hash — backend accepts either.
    const params = new URLSearchParams({ mode });
    if (hashMode) {
      params.set("hash", hashMode);
    } else if (chordData && chordData.path) {
      if (chordData.path.startsWith("__hash/")) {
        params.set("hash", chordData.path.slice(7));
      } else {
        params.set("path", chordData.path);
      }
    } else {
      showToast(_t("toast.beat_switch.no_id"), 2500);
      return;
    }

    _beatSwitchBusy = true;
    btnBeatLibrosa.disabled = true;
    btnBeatMadmom.disabled = true;
    if (btnBeatBeatThis) btnBeatBeatThis.disabled = true;
    try {
      const res = await fetch(`/api/process/beats/switch?${params.toString()}`,
                              { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(_t("toast.beat_switch.failed", { detail: data.detail || `HTTP ${res.status}` }), 5000);
        _beatSwitchBusy = false;
        btnBeatLibrosa.disabled = false;
        btnBeatMadmom.disabled = false;
        if (btnBeatBeatThis) btnBeatBeatThis.disabled = false;
        return;
      }
      _rememberBeatSource(mode);
      if (data.already) {
        // Backend says this is already the target mode. Use the backend's
        // reported beats_source to force-sync the UI (our client state may
        // have been stale — e.g., user-version chord file lagging behind
        // the canonical sheet).
        _syncBeatSourceToggle(data.beats_source || mode);
        showToast(_t("toast.beat_switch.already", { mode }), 1800);
        _beatSwitchBusy = false;
        btnBeatLibrosa.disabled = false;
        btnBeatMadmom.disabled = false;
        if (btnBeatBeatThis) btnBeatBeatThis.disabled = false;
        return;
      }
      if (data.switched) {
        const how = data.cached ? _t("toast.beat_switch.cache_hit") : _t("toast.beat_switch.fresh_compute");
        showToast(_t("toast.beat_switch.switched", { mode, how }), 2000);
        setTimeout(() => location.reload(), 900);
        return;
      }
      if (data.queued) {
        if (data.duplicate) {
          showToast(_t("toast.beat_switch.queued_dup", { title: data.title || _t("toast.beat_switch.queued_song") }), 3500);
        } else {
          showToast(_t("toast.beat_switch.queued_started"), 4000);
        }
        // Poll the upgrade-beats status endpoint (shared queue).
        let _lastSub = "";
        _beatSwitchPoll = setInterval(async () => {
          try {
            const targetHash = params.get("hash")
              || (params.get("path") ? null : null);
            // When we sent path, compute no status lookup (worker keys by hash
            // and we don't have it client-side); fall back to a loose check.
            // In practice path-mode still gets the hash back in response;
            // but for poll we rely on the fact the server used that hash.
            // Simplest: also send the same params to /upgrade-beats/status
            // when we have hash.
            if (!targetHash) {
              // Can't poll without hash — just wait. Reload after 45s as fallback.
              return;
            }
            const sres = await fetch(
              `/api/process/upgrade-beats/status?hash=${encodeURIComponent(targetHash)}`);
            if (!sres.ok) return;
            const sd = await sres.json();
            const st = sd.status;
            if (st === "done") {
              clearInterval(_beatSwitchPoll); _beatSwitchPoll = null;
              _beatSwitchBusy = false;
              const r = sd.result || {};
              showToast(_t("toast.beat_switch.madmom_done", { title: sd.title || "", bpm: r.bpm }), 3500);
              setTimeout(() => location.reload(), 900);
            } else if (st === "error") {
              clearInterval(_beatSwitchPoll); _beatSwitchPoll = null;
              _beatSwitchBusy = false;
              btnBeatLibrosa.disabled = false;
              btnBeatMadmom.disabled = false;
              if (btnBeatBeatThis) btnBeatBeatThis.disabled = false;
              showToast(_t("toast.beat_switch.madmom_failed", { err: sd.error || _t("toast.beat_upgrade.unknown_err") }), 6000);
            } else if (st === "running:downloading" && _lastSub !== st) {
              _lastSub = st;
              showToast(_t("toast.beat_switch.redownloading"), 3500);
            } else if (st === "running:analyzing" && _lastSub !== st) {
              _lastSub = st;
              showToast(_t("toast.beat_switch.analyzing"), 3500);
            }
          } catch (e) {
            console.warn("beat switch poll error:", e);
          }
        }, 4000);
        // Fallback reload if poll couldn't use hash
        if (!params.get("hash")) {
          setTimeout(() => location.reload(), 45000);
        }
      }
    } catch (e) {
      console.error("switch beats failed:", e);
      showToast(_t("toast.beat_switch.failed", { detail: e.message || e }), 5000);
      _beatSwitchBusy = false;
      btnBeatLibrosa.disabled = false;
      btnBeatMadmom.disabled = false;
      if (btnBeatBeatThis) btnBeatBeatThis.disabled = false;
    }
  }

  if (btnBeatLibrosa && !_isBetaLane) {
    btnBeatLibrosa.addEventListener("click", () => _switchBeatsTo("librosa"));
  }
  if (btnBeatMadmom && !_isBetaLane) {
    btnBeatMadmom.addEventListener("click", () => _switchBeatsTo("madmom"));
  }
  if (btnBeatBeatThis && !_isBetaLane) {
    btnBeatBeatThis.addEventListener("click", () => _switchBeatsTo("beat_this"));
  }

  // Sync active state once chordData is available. loadChords / hash-mode both
  // set chordData; we run periodically for the first second to pick up whichever
  // path finishes first, then on a custom 'chord:loaded' event if fired elsewhere.
  // Cheap, runs at most a handful of times.
  let _syncTries = 0;
  const _syncTimer = setInterval(() => {
    _syncTries++;
    if (chordData && chordData.exists) {
      _syncBeatSourceToggle();
      clearInterval(_syncTimer);
    } else if (_syncTries > 20) {
      clearInterval(_syncTimer);
    }
  }, 300);

  // ---- favorite ----
  const _favPath = trackPath || (hashMode ? `__hash/${hashMode}` : "");
  btnFav.addEventListener("click", async () => {
    if (!_favPath) return;
    // Guest gate: anonymous users (no token) cannot persist favorites —
    // /api/favorites returns 401. Surface "sign in to save" instead of
    // letting the request fire and rendering a generic failure toast.
    if (window.LiveChordAuth && window.LiveChordAuth.isAnonymous()) {
      showToast(_t("toast.login_required.fav"));
      return;
    }
    try {
      if (isFavorite) {
        await API.removeFavorite(_favPath);
        isFavorite = false;
        favTracks = favTracks.filter(p => p !== _favPath);
        showToast(_t("toast.fav.removed"));
      } else {
        await API.addFavorite(_favPath);
        isFavorite = true;
        if (!favTracks.includes(_favPath)) favTracks.unshift(_favPath);
        showToast(_t("toast.fav.added"));
      }
      updateFavButton();
    } catch (err) {
      showToast(_t("toast.fav.failed", { err: err.message }));
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
    const baseKeyRaw = shift === 0 ? chordData.key : transposeChord(chordData.key, shift);
    const baseKey = _displayKey(baseKeyRaw);

    // Detect per-section key changes & mode. The global arbiter's
    // local_key_windows are diagnostic key-estimation windows, not confirmed
    // modulations; do not display them as a key-change run.
    if (sectionData && sectionData.sections) {
      const globalIsMajor = !baseKey.endsWith("m");
      const globalRoot = baseKey.replace(/m$/, "");
      const rawRoot = k => (k || "").replace(/m.*$/, "");
      const normKey = k => _displayKey(k);
      const normalize = k => {
        if (!globalIsMajor || !/^[A-G][b#]?m$/.test(k || "")) return normKey(k);
        const mRoot = k.replace(/m$/, "");
        if (mRoot === globalRoot) return baseKey;
        return normKey(transposeChord(mRoot, 3));
      };
      const normalizeShifted = k => normalize(shift === 0 ? k : transposeChord(k, shift));
      const pushUniqueRoot = (arr, key) => {
        const r = rawRoot(key);
        if (r && (arr.length === 0 || rawRoot(arr[arr.length - 1]) !== r)) arr.push(key);
      };

      const sectionKeyWindows = sectionData.sections
        .filter(s => s.key)
        .map(s => ({
          start: Number(s.start) || 0,
          end: Number(s.end) || Infinity,
          key: normalizeShifted(s.key),
        }))
        .filter(w => w.key);
      const arbiter = chordData && chordData.global_arbiter_meta;
      const modulationCandidates = (arbiter && Array.isArray(arbiter.modulation_candidates))
        ? arbiter.modulation_candidates
          .filter(c => c && c.to)
          .map(c => ({
            time: Number(c.time) || 0,
            from: c.from ? normalizeShifted(c.from) : "",
            to: normalizeShifted(c.to),
          }))
        : [];
      const keyWindows = sectionKeyWindows;

      // Filter section-derived keys: key must persist 2+ consecutive sections.
      // Arbiter windows are already sustained song-level key regions.
      const stable = [];
      if (sectionKeyWindows.length) {
        const secKeys = sectionKeyWindows.map(w => w.key);
        for (let i = 0; i < secKeys.length; i++) {
          const r = rawRoot(secKeys[i]);
          const prevR = i > 0 ? rawRoot(secKeys[i - 1]) : null;
          const nextR = i < secKeys.length - 1 ? rawRoot(secKeys[i + 1]) : null;
          // Keep if: same as previous OR same as next (sustained)
          if (r === prevR || r === nextR) {
            pushUniqueRoot(stable, secKeys[i]);
          }
        }
      } else if (modulationCandidates.length) {
        const first = modulationCandidates[0];
        pushUniqueRoot(stable, first.from || baseKey);
        modulationCandidates.forEach(c => pushUniqueRoot(stable, c.to));
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
        let curRaw = curSec && curSec.key ? normalizeShifted(curSec.key) : "";
        let activeStableIdx = -1;
        if (!curRaw && keyWindows.length) {
          let curWindowIdx = keyWindows.findIndex(w => t >= w.start && t < w.end);
          if (curWindowIdx < 0) curWindowIdx = keyWindows.findIndex(w => t < w.start);
          if (curWindowIdx < 0) curWindowIdx = keyWindows.length - 1;
          const curWindow = keyWindows[curWindowIdx];
          curRaw = curWindow ? curWindow.key : "";
          for (let i = 0; i <= curWindowIdx; i++) {
            if (i === 0 || rawRoot(keyWindows[i].key) !== rawRoot(keyWindows[i - 1].key)) {
              activeStableIdx++;
            }
          }
        }
        if (!curRaw && modulationCandidates.length) {
          let curCandidateIdx = -1;
          for (let i = modulationCandidates.length - 1; i >= 0; i--) {
            if (t >= modulationCandidates[i].time) {
              curCandidateIdx = i;
              break;
            }
          }
          const curCandidate = curCandidateIdx >= 0 ? modulationCandidates[curCandidateIdx] : null;
          curRaw = curCandidate ? curCandidate.to : "";
          activeStableIdx = curCandidateIdx >= 0 ? curCandidateIdx + 1 : 0;
        }
        if (!curRaw) curRaw = baseKey;
        if (activeStableIdx < 0 || activeStableIdx >= stable.length || rawRoot(stable[activeStableIdx]) !== rawRoot(curRaw)) {
          activeStableIdx = stable.findIndex(k => rawRoot(k) === rawRoot(curRaw));
        }
        const display = stable.map((k, idx) => {
          if (idx === activeStableIdx) {
            return `<span style="color:#00e5ff;text-shadow:0 0 8px rgba(0,229,255,0.5)">${k}</span>`;
          }
          return `<span style="opacity:0.35">${k}</span>`;
        }).join(' <span style="opacity:0.3">→</span> ');
        const modeSuffix = curModeLabel ? ` <span style="color:#00e5ff;opacity:0.7;font-size:0.85em">${curModeLabel}</span>` : "";
        keyInfo.classList.add("has-modulation");
        keyInfo.dataset.mobileKey = curRaw;
        const keyRun = `${display}${modeSuffix}`;
        keyInfo.innerHTML = `Key:&nbsp;<span class="key-modulation-full">${keyRun}</span>`;
        return;
      }
      // No modulation — show single key with mode if non-standard
      if (curModeLabel) {
        keyInfo.classList.remove("has-modulation");
        keyInfo.dataset.mobileKey = baseKey;
        keyInfo.innerHTML = `Key:&nbsp;${baseKey} <span style="color:#00e5ff;opacity:0.7;font-size:0.85em">${curModeLabel}</span>`;
        return;
      }
    }
    keyInfo.classList.remove("has-modulation");
    keyInfo.dataset.mobileKey = baseKey;
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
            searchResults.innerHTML = `<div style="padding:12px;color:var(--text-dim);font-size:12px">${_t("player.search.no_results")}</div>`;
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
                const key = _displayKey(r.chord_key || "");
                diffHtml = ` <span class="difficulty" style="font-size:0.8em;opacity:0.6;margin-left:6px">${"⭐".repeat(stars)}${key ? " " + key : ""}</span>`;
              }
              
              html += `
                <div class="result-item" data-path="${escapeHtml(r.path)}" data-hash="${escapeHtml(r.hash || "")}">
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
              const h = el.dataset.hash;
              window.location.href = h
                ? `/player?hash=${encodeURIComponent(h)}&autoplay=1`
                : `/player?path=${encodeURIComponent(el.dataset.path)}&autoplay=1`;
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

  if (hashMode) {
    // Hash mode: load chord data directly by hash (from process results)
    (async () => {
      _setLoadingState(true, _t("loading.chord"), _t("loading.chord_detail"));
      try {
        const res = await fetch(_chordsByHashUrl(hashMode));
        if (!res.ok) throw new Error(_t("player.error.no_chord_data"));
        chordData = await res.json();
        if (chordData.exists && chordData.chords && chordData.chords.length > 0) {
          hasChords = true;
          _chordDuration = _computeChordDuration(chordData);
          const title = chordData.title
            || (chordData.path ? chordData.path.split("/").pop().replace(/\.\w+$/i, "") : "")
            || _t("player.title.analysis_result");
          songTitle.textContent = title;
          songTitle.title = title;
          _checkMarquee(songTitle);
          document.title = `${title} — LiveChord`;
          if (chordData.key) {
            const keyInfo = $("#chordKey");
            if (keyInfo) keyInfo.textContent = `Key: ${_displayKey(chordData.key)}`;
          }
          _updateChordQualityBadge(chordData, hashMode);
          await preloadChordInfo(chordData.chords);
          buildChordDOM();
          _trackPlayerLoaded("hash", hashMode);
          _trackPlayerQualityView("hash", hashMode);

          // Hash mode parity with DB-path mode: kick off AI accompaniment fetch
          // so LH/RH bars + fingering come from the real algorithm instead of
          // the chord-voicing fallback (which looks flat & desynced next to
          // per-onset events).
          if (waterfallActive) _loadAccompaniment();

          // Hash mode parity: load section data so the A-B phrase picker works
          // (previously skipped, leaving beta player strip empty).
          _loadSections(chordData.path || "");

          // Load favorites for hash mode
          try {
            const favData = await API.getFavorites();
            favTracks = (favData.favorites || []).map(f => f.path);
            isFavorite = favTracks.includes(_favPath);
            updateFavButton();
          } catch {}

          // Track hash-mode play in recent.json so processed songs show on home page.
          // `keepalive: true` lets the POST complete even if the user navigates
          // back within a second — default fetch would get cancelled on unload
          // and the song would be missing from 最近播放 until bfcache eventually refreshes.
          fetch("/api/recent", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            keepalive: true,
            body: JSON.stringify({
              path: `__hash/${hashMode}`,
              title: chordData.title || title || "",
              youtube_url: chordData.youtube_url || "",
            }),
          }).catch(() => {});

          // Load melody for waterfall (try hash, then path from chord data)
          try {
            const melPath = chordData.path || "";
            if (melPath) {
              // Path-mode: _loadMelody handles cache-hit AND the new
              // pending→poll path (library songs whose melody is extracted
              // on-demand by the background worker). Don't rely on
              // _maybeStartMelodyPolling here — it only polls fresh-analyzed
              // hashes, so an old library song opened in hash mode would
              // otherwise enqueue extraction but never pick up the result.
              _loadMelody(melPath);
            } else {
              const melRes = await fetch(`/api/ai/melody?hash=${encodeURIComponent(hashMode)}`);
              const melData = await melRes.json();
              if (melData.melody && melData.melody.length > 0) {
                melodyData = _filterMelody(melData.melody);
              } else {
                // Freshly-analyzed hash → ingest melody worker still running; poll.
                _maybeStartMelodyPolling();
              }
            }
          } catch {}

          // Try to auto-load audio from IndexedDB (uploaded file pass-through).
          // Keep the blob — users can land on this hash again from 最近播放 or
          // 本機音樂 cards, and recent-card clicks don't have the local-id ↔
          // hash copy step that the local-tracks card uses, so deleting the
          // blob after first play stranded users with the YT fallback panel
          // on every replay. IndexedDB is bounded by user-explicit add/remove
          // via the local-tracks list; uncapped growth is preferable to the
          // surprise "I uploaded this 5 seconds ago, why doesn't it play?"
          let audioLoaded = false;
          try {
            const blob = await audioDBLoad(hashMode);
            if (blob) {
              const objUrl = URL.createObjectURL(blob);
              audio.src = objUrl;
              _usingLocalFile = true;
              audio.play().catch(() => {});
              audioLoaded = true;
            }
          } catch (e) { console.warn("IndexedDB load failed:", e); }

          // Demo songs ship audio at /static/demo/<id>.mp3 — chord JSON carries
          // the URL so the player can wire it up without a separate manifest
          // round-trip. Marked _usingLocalFile=false so the file-picker fallback
          // doesn't fire and so seek/play behaviour follows the streaming path.
          if (!audioLoaded && chordData && chordData.demo_audio_url) {
            audio.src = chordData.demo_audio_url;
            _usingLocalFile = false;
            audio.play().catch(() => {});
            audioLoaded = true;
            _showDemoAttribution(chordData);
          }

          // No bundled audio — show the fallback panel so the user can
          // load a local audio file. Legacy chord JSONs that have a
          // youtube_url field are no longer auto-embedded; users must
          // upload the audio file themselves.
          if (!audioLoaded) {
            _showYtFallbackPanel();
          }
        } else {
          // Hash mode, but no chord data (song exists in library metadata but not yet analyzed,
          // or the chord JSON was wiped). Give the user an out: clear empty state in the chord
          // area, plus a fallback panel so they can paste a YT URL or load a local audio file
          // and at least audition the track while analysis is pending.
          const fallbackTitle = chordData.title
            || (chordData.path ? chordData.path.split("/").pop().replace(/\.\w+$/i, "") : "")
            || _t("player.title.not_analyzed");
          songTitle.textContent = fallbackTitle;
          songTitle.title = fallbackTitle;
          _checkMarquee(songTitle);
          document.title = `${fallbackTitle} — LiveChord`;
          if (unifiedRibbonTrack) {
            unifiedRibbonTrack.innerHTML = `
              <div class="chord-empty-state">
                <div class="chord-empty-msg">${_t("player.chord_empty.msg_unanalyzed")}</div>
                <div class="chord-empty-hint">${_t("player.chord_empty.hint_unanalyzed")}</div>
              </div>`;
          }
          showToast(_t("toast.load.unanalyzed"), 5000);
          _showYtFallbackPanel();
        }
      } catch (e) {
        songTitle.textContent = _t("loading.failed");
        showToast(_t("toast.load.failed", { err: e.message }), 4000);
      } finally {
        _setLoadingState(false);
        _maybeStartPlayerTutorial();
      }
    })();
  } else {
    loadTrack(trackPath).then(async () => {
      // In beta the YouTube iframe is the primary playback surface and autoplays itself;
      // only kick the NAS audio element for personal mode.
      const isBeta = await _isBetaModeAsync.catch(() => false);
      if (autoplay && !isBeta) audio.play().catch(() => {});
      if (restoreFs) {
        document.documentElement.requestFullscreen().catch(() => {});
        if (btnPageFs) btnPageFs.innerHTML = "&#x2716;";
      }
    });
  }

  // YouTube IFrame embed and yt-dlp-driven analysis were removed when
  // LiveChord was open-sourced (2026-05-04). The remaining fallback panel
  // is a file-picker only — surfaced when the chord JSON has no bundled
  // audio and no IndexedDB blob. Legacy chord JSONs that contain a
  // ``youtube_url`` field still load (the field is silently ignored);
  // users have to upload the audio file to get playback.

  // Demo-song attribution strip — required for CC-BY tracks (Kevin MacLeod,
  // Chris Zabriskie); harmless for Public Domain tracks. Injected next to the
  // song title in the topbar; idempotent, safe to call repeatedly. Reads
  // `artist`, `license`, `license_url`, `source_url` from the chord JSON.
  function _showDemoAttribution(cd) {
    if (!cd || !songTitle) return;
    let strip = document.getElementById("demoAttribution");
    if (!strip) {
      strip = document.createElement("a");
      strip.id = "demoAttribution";
      strip.className = "demo-attribution";
      strip.target = "_blank";
      strip.rel = "noopener";
      // Insert after songTitle's parent (.topbar-center) so the strip sits
      // on its own line below the topbar instead of cramming the title row.
      const topbar = document.querySelector(".player-topbar");
      if (topbar && topbar.parentNode) {
        topbar.parentNode.insertBefore(strip, topbar.nextSibling);
      } else {
        document.body.insertBefore(strip, document.body.firstChild);
      }
    }
    const artist = cd.artist || "";
    const license = cd.license || "";
    const licenseUrl = cd.license_url || cd.source_url || "";
    const parts = ["♪ Sample"];
    if (artist) parts.push(artist);
    if (license) parts.push(license);
    strip.textContent = parts.join(" · ");
    strip.href = licenseUrl || "#";
    strip.style.display = "";
  }

  function _showYtFallbackPanel() {
    const old = document.getElementById("ytFallbackPanel");
    if (old) { old.remove(); }
    const backdrop = document.createElement("div");
    backdrop.id = "ytFallbackPanel";
    backdrop.className = "yt-fallback-panel lc-modal-backdrop";
    backdrop.innerHTML = `
      <div class="lc-modal">
        <button class="lc-close yt-fb-close" aria-label="${_t("common.close")}">&times;</button>
        <div class="lc-title">${_t("player.yt_fb.title")}</div>
        <div class="yt-fb-hint">${_t("player.yt_fb.hint")}</div>
        <div class="yt-fb-row">
          <input id="ytFbFile" type="file" accept="audio/*" />
          <button id="ytFbFileSubmit" class="yt-fb-btn secondary">${_t("player.yt_fb.btn_local")}</button>
        </div>
      </div>
    `;
    document.body.appendChild(backdrop);
    const close = () => backdrop.remove();
    backdrop.querySelector(".yt-fb-close")?.addEventListener("click", close);
    backdrop.addEventListener("click", (e) => { if (e.target === backdrop) close(); });
    backdrop.querySelector("#ytFbFileSubmit")?.addEventListener("click", () => _onYtFbFileSubmit(backdrop));
  }

  function _onYtFbFileSubmit(panel) {
    const input = panel.querySelector("#ytFbFile");
    const file = input && input.files && input.files[0];
    if (!file) { showToast(_t("toast.audio.pick_file"), 3000); return; }
    const objUrl = URL.createObjectURL(file);
    audio.src = objUrl;
    _usingLocalFile = true;
    audio.play().catch(() => {});
    panel.remove();
    showToast(_t("toast.audio.local_loaded", { name: file.name }), 3000);
    audio.addEventListener("loadedmetadata", function onMeta() {
      audio.removeEventListener("loadedmetadata", onMeta);
      if (!_chordDuration || _chordDuration < 30) return;
      const d = audio.duration;
      if (!d || isNaN(d)) return;
      const ratio = Math.abs(d - _chordDuration) / _chordDuration;
      if (ratio > 0.10) {
        showToast(_t("toast.audio.length_mismatch", { pct: Math.round(ratio*100) }), 6000);
      }
    });
  }

  function scheduleNotes(currentTime) {
      // Re-resolve per tick so tab/sound switches take effect immediately
      // without restarting playback. _ensureSynth caches instances.
      aiSynth = getActiveSynth();
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
                  // e.velocity is MIDI 0-127 from backend; undefined falls back to 64 in playNote.
                  const vel = e.velocity;
                  const gate = e.gate_ratio;
                  if (e.pitches) {
                      for (const p of e.pitches) {
                          aiSynth.playNote(p, e.duration || 0.5, hand, targetTime, vel, gate);
                      }
                  } else if (e.pitch) {
                      aiSynth.playNote(e.pitch, e.duration || 0.5, hand, targetTime, vel, gate);
                  }
              }
          }
      };

      scheduleHand(accData.left_hand || [], 'left');
      // Right-hand scheduling mirrors the waterfall — _resolveRhEvents()
      // respects rhContentMode so audio plays what the user sees.
      scheduleHand(_resolveRhEvents(), 'right');
      
      lastScheduledTime = currentTime + lookahead;
  }

  // Hook into update loop
  const originalUpdate = window.requestAnimationFrame;
  // We can just add it to the existing `update()` loop ...
  // Wait, I will just patch `audio.addEventListener('timeupdate')` or add it to `renderWaterfall`
  // Actually, I can set an interval or hook it since this is inside the IIFE.

  audio.addEventListener("play", () => {
      const s = getActiveSynth();
      s.init();
      if (s.ctx) s.ctx.resume();
  });

  // Beta YT mode never fires `audio` play events — hook note scheduling to
  // whatever surface is actually playing. scheduleNotes also lazy-inits
  // the active synth so MIDI / Mix modes work without having to have hit ▶
  // on <audio>.
  setInterval(() => {
      if (audio.paused) return;
      const s = getActiveSynth();
      if (!s.ctx) {
          try { s.init(); } catch {}
      }
      if (s.ctx && s.ctx.state === "suspended") {
          try { s.ctx.resume(); } catch {}
      }
      scheduleNotes(audio.currentTime);
  }, 50);

  // Audio Mode UI Bindings (Music -> MIDI -> Mix)
  const btnAudioMode = document.getElementById("btnAudioMode");
  const crossfaderContainer = document.getElementById("crossfaderContainer");
  const crossfaderVol = document.getElementById("crossfaderVol");
  // Restore persisted crossfader value BEFORE applyAudioMode() init so the first
  // render uses the saved value instead of HTML-default 0.5.
  if (crossfaderVol) {
    try {
      const saved = localStorage.getItem("livechord_mix_val");
      if (saved !== null && !isNaN(parseFloat(saved))) crossfaderVol.value = saved;
    } catch {}
  }
  
  // Persisted across page loads so user's choice sticks.
  let audioMode = 0; // 0: Music, 1: MIDI, 2: Mix
  try {
    const _saved = parseInt(localStorage.getItem("livechord_audio_mode"), 10);
    if ([0, 1, 2].includes(_saved)) audioMode = _saved;
  } catch {}

  function _setSourceVolume(vol01) {
      audio.volume = Math.max(0, Math.min(1, vol01));
  }

  function applyAudioMode() {
      if (!btnAudioMode) return;

      const volumeSlider = document.getElementById("volumeSlider");
      const baseVol = volumeSlider ? parseFloat(volumeSlider.value) : 1.0;

      if (audioMode === 0) {
          btnAudioMode.innerHTML = "🎵 Music";
          btnAudioMode.style.color = "#03a9f4";
          btnAudioMode.style.borderColor = "#03a9f4";
          _setSourceVolume(baseVol);
          _applyVolToAllSynths(0, 0);
          if (crossfaderContainer) crossfaderContainer.style.display = "none";
      } else if (audioMode === 1) {
          btnAudioMode.innerHTML = "🎹 MIDI";
          btnAudioMode.style.color = "#ff9800";
          btnAudioMode.style.borderColor = "#ff9800";
          _setSourceVolume(0);
          _applyVolToAllSynths(1.0, 1.0);
          if (crossfaderContainer) crossfaderContainer.style.display = "none";
      } else {
          btnAudioMode.innerHTML = "🎧 Mix";
          btnAudioMode.style.color = "#4caf50";
          btnAudioMode.style.borderColor = "#4caf50";
          if (crossfaderContainer) crossfaderContainer.style.display = "flex";

          const mixVal = crossfaderVol ? parseFloat(crossfaderVol.value) : 0.5;
          _setSourceVolume(baseVol * (1 - mixVal));
          _applyVolToAllSynths(mixVal, mixVal);
      }

      // Sound picker UI feedback — disabled in Music mode
      if (typeof _syncSoundPickerEnabled === "function") _syncSoundPickerEnabled();

      // The hand mute filtering is still robustly done in SampleSynth.playNote
  }

  if (btnAudioMode) {
      btnAudioMode.addEventListener("click", () => {
          audioMode = (audioMode + 1) % 3;
          try { localStorage.setItem("livechord_audio_mode", String(audioMode)); } catch {}
          applyAudioMode();
          const modeNames = [_t("toast.audio_mode.music"), _t("toast.audio_mode.midi"), _t("toast.audio_mode.mix")];
          showToast(_t("toast.audio_mode.switched", { mode: modeNames[audioMode] }));
      });
      applyAudioMode(); // init
  }

  if (crossfaderVol) {
      crossfaderVol.addEventListener("input", () => {
          try { localStorage.setItem("livechord_mix_val", crossfaderVol.value); } catch {}
          applyAudioMode();
      });
  }

  // MIDI Download
  const btnDownloadMidi = document.getElementById("btnDownloadMidi");
  if (btnDownloadMidi) {
      btnDownloadMidi.addEventListener("click", () => {
          if (window.MidiExporter && accData) {
              const rTitle = document.title.replace(/ — LiveChord$/, "") || trackPath.split("/").pop().replace(/\.\w+$/, "") || "Track";
              // Export matches what the user is practicing: gate left by activeHand,
              // right by activeHand AND rhContentMode (via _resolveRhEvents()).
              const isLeftActive = (typeof activeHand === 'undefined') || activeHand === "both" || activeHand === "left";
              const isRightActive = (typeof activeHand === 'undefined') || activeHand === "both" || activeHand === "right";
              const leftEvents = isLeftActive ? (accData.left_hand || []) : [];
              const rightEvents = isRightActive ? _resolveRhEvents() : [];
              try {
                  window.MidiExporter.exportMidi(accData, rTitle, teachStyle, teachLevel, {
                      leftEvents, rightEvents, modeSuffix: _practiceModeSuffix(),
                  });
                  showToast(_t("toast.midi.downloaded"));
              } catch (err) {
                  console.error("MIDI export error:", err);
                  showToast(_t("toast.midi.export_failed", { err: err.message }), true);
              }
          } else {
              showToast(_t("toast.midi.no_acc_data"), true);
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
          showToast(_t("toast.rlhf.thanks_good"));
          _trackQualityFeedback("good");
          const urlParams = new URLSearchParams(window.location.search);
          const path = urlParams.get('path');
          if (path) {
              fetch('/api/ai/evaluate-feedback', {
                  method: 'POST',
                  headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify({ path: path, action: "good", context: { time: audio ? audio.currentTime : 0 } })
              });
          }
          if (ratePopup) ratePopup.style.display = "none";
      });
  }
  if (btnRlhfBad) {
      btnRlhfBad.addEventListener("click", () => {
          showToast(_t("toast.rlhf.thanks_bad"));
          _trackQualityFeedback("bad");
          const urlParams = new URLSearchParams(window.location.search);
          const path = urlParams.get('path');
          if (path) {
              fetch('/api/ai/evaluate-feedback', {
                  method: 'POST',
                  headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify({ path: path, action: "bad", context: { time: audio ? audio.currentTime : 0 } })
              });
          }
          if (ratePopup) ratePopup.style.display = "none";
      });
  }

  // --- RLHF Section Editor ---
  window.showSectionMenu = function(e, chordTime, activeSec) {
      e.preventDefault();
      e.stopPropagation();
      
      document.querySelectorAll(".rv-section-menu").forEach(m => m.remove());
      
      const menu = document.createElement("div");
      menu.className = "rv-section-menu";
      menu.style.left = e.pageX + "px";
      menu.style.top = e.pageY + "px";
      
      const types = [
        { type: "dialogue",     label: _t("player.section.dialogue") },
        { type: "intro",        label: _t("player.section.intro") },
        { type: "verse",        label: _t("player.section.verse") },
        { type: "pre_chorus",   label: _t("player.section.pre_chorus") },
        { type: "chorus",       label: _t("player.section.chorus") },
        { type: "instrumental", label: _t("player.section.instrumental") },
        { type: "bridge",       label: _t("player.section.bridge") },
        { type: "outro",        label: _t("player.section.outro") },
      ];
      
      const isBoundary = (chordTime === null || (activeSec && Math.abs(chordTime - activeSec.start) < 0.5));
      
      function _adjustBounds() {
          const rect = menu.getBoundingClientRect();
          let top = e.pageY;
          if (e.clientY + rect.height > window.innerHeight) {
              top = e.pageY - rect.height;
              if (top < window.scrollY) top = window.scrollY + 10;
          }
          menu.style.top = top + "px";
      }
      
      function renderTypeList(titleText, callback, opts) {
          opts = opts || {};
          const isModify = !!opts.isModify;
          menu.innerHTML = "";
          const t = document.createElement("div");
          t.className = "title";
          t.innerHTML = `<span>${titleText}</span><span style="opacity:0.6;float:right;cursor:pointer">${_t("player.menu.back")}</span>`;
          t.querySelector("span:last-child").onclick = (ev) => { ev.stopPropagation(); renderMain(); };
          menu.appendChild(t);

          let originalType = activeSec ? activeSec.type : null;
          types.forEach(tObj => {
              const item = document.createElement("div");
              item.className = "rv-section-menu-item";
              const isSelected = (isModify && originalType === tObj.type);
              if (isSelected) {
                  item.style.fontWeight = "bold";
                  item.style.color = "#4caf50";
              }
              item.innerHTML = `<span style="flex:1">${tObj.label}</span>${isSelected ? "✓" : ""}`;
              item.onclick = async (ev) => {
                  ev.stopPropagation();
                  menu.remove();
                  await callback(tObj.type);
              };
              menu.appendChild(item);
          });
          _adjustBounds();
      }

      // ---- Chord Split sub-menu (integrated with section assignment) ----
      function renderSplitOptions(chordIdx, chordName, totalBeats) {
          const CC = window.ChordCorrection;
          if (!CC) return;
          menu.innerHTML = "";
          const t = document.createElement("div");
          t.className = "title";
          t.innerHTML = `<span>${_t("player.menu.title.split_chord", { chord: chordName, beats: totalBeats })}</span><span style="opacity:0.6;float:right;cursor:pointer">${_t("player.menu.back")}</span>`;
          t.querySelector("span:last-child").onclick = (ev) => { ev.stopPropagation(); renderMain(); };
          menu.appendChild(t);

          const options = CC.generateSplitOptions(totalBeats);

          // Group 1: Just split
          const h1 = document.createElement("div");
          h1.className = "rv-section-menu-label";
          h1.textContent = _t("split.menu.split_only");
          menu.appendChild(h1);

          options.forEach(([l, r]) => {
              const item = document.createElement("div");
              item.className = "rv-section-menu-item";
              item.innerHTML = `<span style="flex:1">${l}+${r}</span>`;
              item.onclick = (ev) => {
                  ev.stopPropagation();
                  menu.remove();
                  CC.backup(chordData);
                  CC.splitChord(chordData, chordIdx, l, r, currentSecPerBeat);
                  if (typeof _corrRebuild === "function") _corrRebuild();
                  else if (typeof window._chordRebuild === "function") window._chordRebuild();
                  showToast(_t("toast.split.menu_done", { l, r }), 2000);
              };
              menu.appendChild(item);
          });

          // Separator
          const sep = document.createElement("div");
          sep.className = "rv-section-menu-sep";
          menu.appendChild(sep);

          // Group 2: Split + section boundary
          const h2 = document.createElement("div");
          h2.className = "rv-section-menu-label";
          h2.textContent = _t("split.menu.split_with_section");
          menu.appendChild(h2);

          options.forEach(([l, r]) => {
              const item = document.createElement("div");
              item.className = "rv-section-menu-item";
              item.innerHTML = `<span style="flex:1">${_t("player.menu.item.split_with_section", { l, r })}</span>`;
              item.onclick = (ev) => {
                  ev.stopPropagation();
                  renderTypeList(_t("player.menu.title.split_with_section", { l, r }), async (newType) => {
                      CC.backup(chordData);
                      const splitTime = CC.splitChord(chordData, chordIdx, l, r, currentSecPerBeat);
                      await window.saveSectionFeedback(splitTime, newType);
                      if (typeof _corrRebuild === "function") _corrRebuild();
                      else if (typeof window._chordRebuild === "function") window._chordRebuild();
                      showToast(_t("toast.split.menu_with_section", { l, r }), 2500);
                  });
              };
              menu.appendChild(item);
          });
          _adjustBounds();
      }

      // ---- Beat-count adjust sub-menu ----
      function renderBeatAdjustOptions(chordIdx, chordName, currentBeats) {
          const CC = window.ChordCorrection;
          if (!CC || typeof CC.setBeats !== "function") return;
          menu.innerHTML = "";
          const t = document.createElement("div");
          t.className = "title";
          t.innerHTML = `<span>${_t("player.menu.title.beats_adjust", { chord: chordName, n: currentBeats })}</span><span style="opacity:0.6;float:right;cursor:pointer">${_t("player.menu.back")}</span>`;
          t.querySelector("span:last-child").onclick = (ev) => { ev.stopPropagation(); renderMain(); };
          menu.appendChild(t);

          const hint = document.createElement("div");
          hint.className = "rv-section-menu-label";
          hint.style.fontSize = "11px";
          hint.textContent = _t("split.menu.beats_hint");
          menu.appendChild(hint);

          const choices = [1, 2, 3, 4, 5, 6, 8, 12, 16];
          for (const beats of choices) {
              const item = document.createElement("div");
              item.className = "rv-section-menu-item";
              if (beats === currentBeats) {
                  item.style.fontWeight = "bold";
                  item.style.color = "#4caf50";
              }
              item.innerHTML = `<span style="flex:1">${_t("player.menu.item.beats_choice", { n: beats, check: beats === currentBeats ? " ✓" : "" })}</span>`;
              item.onclick = (ev) => {
                  ev.stopPropagation();
                  menu.remove();
                  if (beats === currentBeats) return;
                  CC.backup(chordData);
                  CC.setBeats(chordData, chordIdx, beats, currentSecPerBeat);
                  if (typeof _corrRebuild === "function") _corrRebuild();
                  else if (typeof window._chordRebuild === "function") window._chordRebuild();
                  showToast(_t("toast.beats.set", { chordName, beats }), 2000);
              };
              menu.appendChild(item);
          }
          _adjustBounds();
      }

      function renderMain() {
          menu.innerHTML = "";
          const t1 = document.createElement("div");
          t1.className = "title";
          t1.textContent = activeSec
            ? _t("player.menu.title.section_current", { label: activeSec.labelZh || activeSec.type })
            : _t("player.menu.title.section_default");
          menu.appendChild(t1);

          const renameItem = document.createElement("div");
          renameItem.className = "rv-section-menu-item";
          renameItem.innerHTML = `<span style="flex:1">${_t("player.menu.item.rename_section")}</span>`;
          renameItem.onclick = (ev) => {
              ev.stopPropagation();
              let exactTime = activeSec ? activeSec.start : chordTime;
              renderTypeList(_t("player.menu.title.modify_name"), async (newType) => {
                  await window.saveSectionFeedback(exactTime, newType);
              }, { isModify: true });
          };
          menu.appendChild(renameItem);

          if (chordTime !== null && !isBoundary) {
              const splitItem = document.createElement("div");
              splitItem.className = "rv-section-menu-item";
              splitItem.innerHTML = `<span style="flex:1">${_t("player.menu.item.split_section")}</span>`;
              splitItem.onclick = (ev) => {
                  ev.stopPropagation();
                  renderTypeList(_t("player.menu.title.split_out"), async (newType) => {
                      await window.saveSectionFeedback(chordTime, newType);
                  });
              };
              menu.appendChild(splitItem);
          }

          // ---- Chord Split (切分和弦) ----
          if (chordTime !== null && window.ChordCorrection) {
              const chords = chordData ? chordData.chords : [];
              const ci = chords.findIndex(c => Math.abs(c.time - chordTime) < 0.15);
              if (ci >= 0) {
                  // Rename chord — text input via prompt(), one-shot rename.
                  // Positioned first so it's the fastest-reach item on the
                  // menu (most-used chord correction: "wrong chord name").
                  const renameItem = document.createElement("div");
                  renameItem.className = "rv-section-menu-item";
                  renameItem.innerHTML = `<span style="flex:1">${_t("player.menu.item.rename_chord", { chord: chords[ci].chord })}</span>`;
                  renameItem.onclick = (ev) => {
                      ev.stopPropagation();
                      menu.remove();
                      const newName = prompt(_t("player.prompt.new_chord_name"), chords[ci].chord);
                      if (!newName) return;
                      const trimmed = newName.trim();
                      if (!trimmed || trimmed === chords[ci].chord) return;
                      const CC = window.ChordCorrection;
                      CC.backup(chordData);
                      const oldName = chords[ci].chord;
                      chords[ci].chord = trimmed;
                      if (typeof _corrRebuild === "function") _corrRebuild();
                      else if (typeof window._chordRebuild === "function") window._chordRebuild();
                      showToast(_t("toast.chord.renamed", { oldName, newName: trimmed }), 1800);
                  };
                  menu.appendChild(renameItem);

                  // Match the ribbon's dot calculation: prefer c.end so the
                  // menu's "(N 拍)" label agrees with the card's dot count.
                  const durSec = chords[ci].end
                      ? chords[ci].end - chords[ci].time
                      : (ci < chords.length - 1
                          ? chords[ci + 1].time - chords[ci].time
                          : 2.0);
                  const beats = Math.round(durSec / currentSecPerBeat);
                  if (beats >= 4) {
                      const csItem = document.createElement("div");
                      csItem.className = "rv-section-menu-item";
                      csItem.style.color = "#ff9800";
                      csItem.innerHTML = `<span style="flex:1">${_t("player.menu.item.split_chord", { n: beats })}</span>`;
                      csItem.onclick = (ev) => {
                          ev.stopPropagation();
                          renderSplitOptions(ci, chords[ci].chord, beats);
                      };
                      menu.appendChild(csItem);
                  }

                  // Quick beat-count adjust — user spots "this chord should
                  // be 4 not 3" and fixes it in one tap without leaving the
                  // player. Available for every chord, not gated on >= 4.
                  // No inline color — inline style would survive the hover
                  // rule (specificity) and the text becomes invisible when
                  // the hover background matches the inline color.
                  const baItem = document.createElement("div");
                  baItem.className = "rv-section-menu-item";
                  baItem.innerHTML = `<span style="flex:1">${_t("player.menu.item.adjust_beats", { n: beats })}</span>`;
                  baItem.onclick = (ev) => {
                      ev.stopPropagation();
                      renderBeatAdjustOptions(ci, chords[ci].chord, beats);
                  };
                  menu.appendChild(baItem);

                  // Tap-calibrate starting from this chord — user calibrates
                  // a segment via 1/2/3/4 keys, chords before this one stay
                  // untouched. Supports iterative "do a few, resume later".
                  const ccItem = document.createElement("div");
                  ccItem.className = "rv-section-menu-item";
                  ccItem.innerHTML = `<span style="flex:1">${_t("player.menu.item.calibrate_from_here")}</span>`;
                  ccItem.onclick = (ev) => {
                      ev.stopPropagation();
                      menu.remove();
                      window.ChordCorrection.enterChordCalibrate(
                          chordData, _audioForCorrection, _corrRebuild,
                          {
                              startChordIdx: ci,
                              sections: (sectionData && Array.isArray(sectionData.sections)) ? sectionData.sections : null,
                          }
                      );
                  };
                  menu.appendChild(ccItem);

                  // "延伸至此和弦" — apply the most recent calibrate session's
                  // transform onto [lastEnd+1..ci]. Only shown when a recent
                  // (<5min) calibration exists AND the right-clicked chord is
                  // strictly past the calibrated segment. Intended for the
                  // 均速/變速/均速 workflow where the user calibrates one
                  // constant region, then marks where it should end by
                  // right-clicking the boundary chord.
                  try {
                      const lastCal = (window.ChordCorrection && typeof window.ChordCorrection.getLastCalibration === "function")
                          ? window.ChordCorrection.getLastCalibration() : null;
                      if (lastCal && ci > lastCal.endIdx && (Date.now() - lastCal.ts) < 5 * 60 * 1000) {
                          const extItem = document.createElement("div");
                          extItem.className = "rv-section-menu-item";
                          extItem.innerHTML = `<span style="flex:1">${_t("player.menu.item.extend_calibration")}</span>`;
                          extItem.onclick = (ev) => {
                              ev.stopPropagation();
                              menu.remove();
                              window.ChordCorrection.applyLastCalibrateToIdx(chordData, ci, _corrRebuild);
                          };
                          menu.appendChild(extItem);
                      }
                  } catch (e) { /* ignore — menu entry is purely additive */ }

                  // Merge adjacent chords — BTC sometimes splits a single
                  // 8-beat chord into "1-beat + 7-beat" fragments; merging
                  // lets the user fix that in one click. Show both
                  // directions when both neighbors exist so the user picks
                  // which name survives.
                  if (ci > 0) {
                      const mPrevItem = document.createElement("div");
                      mPrevItem.className = "rv-section-menu-item";
                      const prevName = chords[ci - 1].chord;
                      mPrevItem.innerHTML = `<span style="flex:1">${_t("player.menu.item.merge_into_prev", { prevName })}</span>`;
                      mPrevItem.onclick = (ev) => {
                          ev.stopPropagation();
                          menu.remove();
                          const CC = window.ChordCorrection;
                          CC.backup(chordData);
                          if (CC.mergeChord(chordData, ci, "prev")) {
                              if (typeof _corrRebuild === "function") _corrRebuild();
                              else if (typeof window._chordRebuild === "function") window._chordRebuild();
                              showToast(_t("toast.chord.merged_into_prev", { prevName }), 1800);
                          }
                      };
                      menu.appendChild(mPrevItem);
                  }
                  if (ci < chords.length - 1) {
                      const mNextItem = document.createElement("div");
                      mNextItem.className = "rv-section-menu-item";
                      const nextName = chords[ci + 1].chord;
                      const currName = chords[ci].chord;
                      mNextItem.innerHTML = `<span style="flex:1">${_t("player.menu.item.merge_next_into", { nextName, currName })}</span>`;
                      mNextItem.onclick = (ev) => {
                          ev.stopPropagation();
                          menu.remove();
                          const CC = window.ChordCorrection;
                          CC.backup(chordData);
                          if (CC.mergeChord(chordData, ci, "next")) {
                              if (typeof _corrRebuild === "function") _corrRebuild();
                              else if (typeof window._chordRebuild === "function") window._chordRebuild();
                              showToast(_t("toast.chord.merged_next_into", { nextName, currName }), 1800);
                          }
                      };
                      menu.appendChild(mNextItem);
                  }
              }
          }

          if (activeSec) {
              const delItem = document.createElement("div");
              delItem.className = "rv-section-menu-item";
              delItem.style.color = "#f44";
              delItem.innerHTML = `<span style="flex:1">${_t("player.menu.item.delete_section")}</span>`;
              delItem.onclick = async (ev) => {
                  ev.stopPropagation();
                  menu.remove();
                  await window.deleteSectionBoundary(activeSec.start);
              };
              menu.appendChild(delItem);
          }
          _adjustBounds();
      }
      
      document.body.appendChild(menu);

      // Previously a "fast-path" rendered an abbreviated section-only menu
      // when the clicked chord was at a section boundary — that stripped out
      // 切分 / 調整拍數 / 和弦校正 / 合併和弦, so user couldn't reach those on
      // a phrase's first chord. renderMain shows everything and handles the
      // boundary case internally (e.g. hides "從此和弦切出新段" when already
      // on a boundary). One extra click for "rename section" is a fair price
      // for chord ops always being reachable.
      renderMain();
      
      const closeMenu = () => { menu.remove(); document.removeEventListener("click", closeMenu); };
      setTimeout(() => document.addEventListener("click", closeMenu), 0);
  };
  
  window.saveSectionFeedback = async function(splitTime, newType) {
      if (!sectionData || !sectionData.sections) return;
      
      // Relax tolerance to 0.2s so that clicking the first chord block
      // correctly snaps to the backend-rounded boundary (which is rounded to 0.1s).
      const TOLERANCE = 0.2; // seconds
      
      // Look for the CLOSEST exact boundary
      let sec = null;
      let minDiff = TOLERANCE + 0.001;
      for (let s of sectionData.sections) {
          let diff = Math.abs(s.start - splitTime);
          if (diff <= TOLERANCE && diff < minDiff) {
              minDiff = diff;
              sec = s;
          }
      }
      
      if (sec) {
          sec.type = newType;
      } else {
          // It's a SPLIT! We are cutting a section into two.
          let parentSec = sectionData.sections.find(s => splitTime > s.start && splitTime < s.end);
          if (parentSec) {
              // 當使用者在同一大段落中切分，都視為「插入一個新段落」，不論名稱為何
              let newSec = {
                  type: newType,
                  start: splitTime,
                  end: parentSec.end
              };
              parentSec.end = splitTime;
              sectionData.sections.push(newSec);
              sectionData.sections.sort((a,b) => a.start - b.start);
          } else {
              // Edge case: no parent, just append
              sectionData.sections.push({ type: newType, start: splitTime, end: splitTime + 10 });
              sectionData.sections.sort((a,b) => a.start - b.start);
          }
      }
          
      const urlParams = new URLSearchParams(window.location.search);
      const path = urlParams.get('path');
      if (!path) return;
          
      await _syncSectionsToBackend(path);
  };
  
  window.deleteSectionBoundary = async function(splitTime) {
      if (!sectionData || !sectionData.sections) return;
      
      const TOLERANCE = 0.2;
      let secIdx = -1;
      for (let i = 0; i < sectionData.sections.length; i++) {
          if (Math.abs(sectionData.sections[i].start - splitTime) <= TOLERANCE) {
              secIdx = i; break;
          }
      }
      
      if (secIdx > 0) {
          // 向上合併
          sectionData.sections[secIdx - 1].end = sectionData.sections[secIdx].end;
          sectionData.sections.splice(secIdx, 1);
          
          const urlParams = new URLSearchParams(window.location.search);
          const path = urlParams.get('path');
          if (path) {
              await _syncSectionsToBackend(path);
          }
      } else if (secIdx === 0) {
          showToast(_t("toast.section.first_no_merge_up"));
      }
  };
  
  async function _syncSectionsToBackend(path) {
          
          const body = {
              path: path,
              sections: sectionData.sections.map(s => ({
                  type: s.type,
                  start: s.start,
                  end: s.end
              }))
          };
          
          try {
              showToast(_t("toast.section.saving"));
              let res = await fetch('/api/ai/sections/feedback', {
                  method: 'POST',
                  headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify(body)
              });
              if (res.ok) {
                  showToast(_t("toast.section.saved"));
                  _loadSections(path);
              } else {
                  showToast(_t("toast.section.save_failed_server"), true);
              }
          } catch(e) {
              showToast(_t("toast.section.save_failed_err", { err: e.message }), true);
          }
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
  // Shared firework-particle helpers so non-piano instruments (accordion,
  // arranger, guitar, ukulele) render the same contact-burst effect as the
  // piano waterfall. Piano keeps its inline spawn/draw for historical reasons
  // (identical params), but these helpers are the canonical entry point.
  function _spawnWaterfallParticles(cx, baseY, kw, cr, cg, cb, velP) {
    if (_waterfallParticles.length >= _WF_PARTICLE_CAP) return;
    const n = 6 + Math.round(velP * 14);
    for (let i = 0; i < n; i++) {
      const ang = -Math.PI / 2 + (Math.random() - 0.5) * Math.PI * 0.9;
      const spd = 1.5 + Math.random() * (2 + velP * 4);
      _waterfallParticles.push({
        x: cx + (Math.random() - 0.5) * kw * 0.6,
        y: baseY,
        vx: Math.cos(ang) * spd,
        vy: Math.sin(ang) * spd * 1.3,
        life: 0,
        maxLife: 0.4 + Math.random() * 0.35,
        r: cr, g: cg, b: cb,
        size: 1.2 + Math.random() * (1.2 + velP * 1.5),
      });
    }
  }
  function _drawWaterfallParticles(ctx) {
    if (_waterfallParticles.length === 0) return;
    const dt = 0.016;
    ctx.save();
    for (let i = _waterfallParticles.length - 1; i >= 0; i--) {
      const p = _waterfallParticles[i];
      p.life += dt;
      if (p.life >= p.maxLife) { _waterfallParticles.splice(i, 1); continue; }
      p.vy += 0.18;
      p.vx *= 0.985;
      p.vy *= 0.985;
      p.x += p.vx;
      p.y += p.vy;
      const t = p.life / p.maxLife;
      const alpha = (1 - t) * (1 - t);
      const size = p.size * (1 - t * 0.4);
      ctx.shadowColor = `rgba(${p.r}, ${p.g}, ${p.b}, 0.9)`;
      ctx.shadowBlur = 8;
      ctx.fillStyle = `rgba(${p.r}, ${p.g}, ${p.b}, ${alpha})`;
      ctx.beginPath();
      ctx.arc(p.x, p.y, size, 0, Math.PI * 2);
      ctx.fill();
      if (t < 0.5) {
        ctx.fillStyle = _isLightBg()
          ? `rgba(20, 25, 35, ${alpha * 0.85})`
          : `rgba(255, 255, 255, ${alpha * 0.9})`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, size * 0.45, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.restore();
  }

  const _playerBridge = {
    $,
    getChordData: () => chordData,
    getDisplayChords: () => _displayChords(),
    getWaterfallBeatGrid: (chords, idx) => _waterfallBeatGrid(chords, idx),
    getAudio: () => audio,
    getChordCache: () => chordCache,
    getCurrentKey: () => _currentKey(),
    getStrumStyle: () => guitarStrumStyle,
    getArpPattern: () => guitarArpPattern,
    getAccData: () => accData,
    getMelodyData: () => melodyData,
    getActiveTab: () => activeTab,
    previewNote: (pitch) => _previewNote(pitch),
    drawAITeacherHUD: _drawAITeacherHUD,
    spawnWaterfallParticles: _spawnWaterfallParticles,
    drawWaterfallParticles: _drawWaterfallParticles,
    API,
    ChordRender,
  };

  const GUITAR_CONFIG = {
    id: "guitar",
    numStrings: 6,
    openMidi: [40, 45, 50, 55, 59, 64],
    stringLabels: ["E", "A", "D", "G", "B", "e"],
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

  // Re-apply the restored tab now that guitar/ukulele/accordion/arranger are
  // registered. The earlier `_switchTab(activeTab)` (near line 1589) ran
  // BEFORE this block — for non-piano tabs it hit the else branch with
  // `InstrumentRegistry.get(tab) === undefined`, so the `if (inst)` guard
  // fell through and the instrument container stayed at display:none from
  // `_setAllTabsInactive()`. Users saw an empty panel on first load and
  // could only "wake it up" by picking an instrument from the Tools popup —
  // which re-ran `_switchTab`, this time with a populated registry. Piano
  // never hit this because its code path doesn't consult the registry.
  if (activeTab !== "piano") _switchTab(activeTab);

  // ===========================================================================
  // Beta Feedback: 5-star rating + comment, bug report
  // ===========================================================================
  (function initBetaFeedback() {
    const betaPopup = $("#betaRatePopup");
    const betaStars = document.querySelectorAll("#betaStars .beta-star");
    const betaComment = $("#betaRateComment");
    const btnSubmit = $("#btnBetaRateSubmit");
    const btnCancel = $("#btnBetaRateCancel");
    const btnBug = $("#btnBugReport");
    const bugDialog = $("#bugReportDialog");
    const bugDesc = $("#bugDescription");
    const bugCat = $("#bugCategory");
    const bugContact = $("#bugContact");
    const bugWebsite = $("#bugWebsite");
    const btnBugSubmit = $("#btnBugSubmit");
    const btnBugCancel = $("#btnBugCancel");
    const btnBugClose = $("#btnBugClose");
    const btnBugCopyEmail = $("#btnBugCopyEmail");
    const btnReportProblemSettings = $("#btnReportProblemSettings");

    let _betaRating = 0;
    let _betaMode = false;

    // Check deployment mode and wire up beta UI
    fetch("/api/config/public").then(r => r.json()).then(cfg => {
      if (cfg.deployment_mode !== "beta") return;
      _betaMode = true;

      // Show bug report button in toolbar
      const tbBug = document.getElementById("tbBugReport");
      if (tbBug) tbBug.style.display = "";
      // Override star button to open beta popup instead of RLHF popup
      const btnRate = document.getElementById("btnRateAiTop");
      if (btnRate && betaPopup) {
        // Remove old listener by cloning
        const clone = btnRate.cloneNode(true);
        btnRate.parentNode.replaceChild(clone, btnRate);
        clone.addEventListener("click", (e) => {
          e.stopPropagation();
          const rp = document.getElementById("ratePopup");
          if (rp) rp.style.display = "none"; // hide old popup
          betaPopup.style.display = betaPopup.style.display === "none" ? "block" : "none";
          // Load existing rating for this song
          if (trackPath && betaPopup.style.display !== "none") {
            API.getMyRating(trackPath).then(d => {
              if (d.rating) { _betaRating = d.rating; _renderStars(); }
              if (d.comment) betaComment.value = d.comment;
            }).catch(() => {});
          }
        });
        document.addEventListener("click", (e) => {
          if (!clone.contains(e.target) && !betaPopup.contains(e.target)) {
            betaPopup.style.display = "none";
          }
        });
      }
    }).catch(() => {});

    // Star hover + click
    betaStars.forEach(s => {
      s.addEventListener("mouseenter", () => {
        const v = +s.dataset.v;
        betaStars.forEach(x => x.classList.toggle("hover", +x.dataset.v <= v));
      });
      s.addEventListener("mouseleave", () => {
        betaStars.forEach(x => x.classList.remove("hover"));
      });
      s.addEventListener("click", () => {
        _betaRating = +s.dataset.v;
        _renderStars();
      });
    });

    function _renderStars() {
      betaStars.forEach(x => {
        const v = +x.dataset.v;
        x.classList.toggle("active", v <= _betaRating);
      });
    }

    // Submit rating
    if (btnSubmit) {
      btnSubmit.addEventListener("click", async () => {
        if (!_betaRating) { showToast(_t("toast.beta.pick_rating_first")); return; }
        // Guest gate: rating is per-user. Anonymous callers get a 401 from
        // /api/feedback/rating; pre-empt with a clearer "sign in" message.
        if (window.LiveChordAuth && window.LiveChordAuth.isAnonymous()) {
          showToast(_t("toast.login_required.rate"));
          return;
        }
        try {
          const title = songTitle ? songTitle.textContent : "";
          await API.submitRating(trackPath, _betaRating, betaComment.value.trim(), title);
          _trackQualityFeedback(`rating_${_betaRating}`);
          showToast(_t("toast.beta.rated", { stars: "★".repeat(_betaRating) + "☆".repeat(5 - _betaRating) }));
          if (betaPopup) betaPopup.style.display = "none";
        } catch (e) { showToast(_t("toast.beta.rate_failed", { err: e.message })); }
      });
    }
    if (btnCancel) {
      btnCancel.addEventListener("click", () => {
        if (betaPopup) betaPopup.style.display = "none";
      });
    }

    function _closeBugDialog() {
      if (bugDialog) bugDialog.style.display = "none";
    }

    function _openBugDialog() {
      document.querySelectorAll(".tb-item.open").forEach(i => i.classList.remove("open"));
      if (bugDialog) bugDialog.style.display = "flex";
    }

    function _bugReportContext() {
      return {
        song_hash: hashMode || "",
        song_title: (chordData && chordData.title) || (songTitle ? songTitle.textContent : ""),
        contact: bugContact ? bugContact.value.trim() : "",
        website: bugWebsite ? bugWebsite.value.trim() : "",
      };
    }

    // Bug report — close any open toolbar popup first so the bug modal isn't
    // sandwiched under a leftover Tools / AI teaching popup.
    if (btnBug) btnBug.addEventListener("click", _openBugDialog);
    if (btnReportProblemSettings) btnReportProblemSettings.addEventListener("click", _openBugDialog);
    if (btnBugSubmit) {
      btnBugSubmit.addEventListener("click", async () => {
        const desc = bugDesc ? bugDesc.value.trim() : "";
        if (!desc) { showToast(_t("toast.bug.describe_first")); return; }
        try {
          const cat = bugCat ? bugCat.value : "other";
          const info = navigator.userAgent;
          await API.submitBug(cat, desc, window.location.href, info, _bugReportContext());
          showToast(_t("toast.bug.thanks"));
          if (window.LiveChordAnalytics) {
            window.LiveChordAnalytics.track("report_problem_submit", {
              category: cat,
              source: "player",
              song_hash: hashMode || "",
            });
          }
          if (bugDialog) bugDialog.style.display = "none";
          if (bugDesc) bugDesc.value = "";
          if (bugContact) bugContact.value = "";
        } catch (e) { showToast(_t("toast.bug.submit_failed", { err: e.message })); }
      });
    }
    if (btnBugCopyEmail) {
      btnBugCopyEmail.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText("livechordcookie@gmail.com");
          showToast(_t("toast.email_copied"));
        } catch (_) {
          showToast("livechordcookie@gmail.com");
        }
      });
    }
    if (btnBugCancel) {
      btnBugCancel.addEventListener("click", _closeBugDialog);
    }
    if (btnBugClose) btnBugClose.addEventListener("click", _closeBugDialog);
    if (bugDialog) {
      bugDialog.addEventListener("click", (e) => {
        if (e.target === bugDialog) _closeBugDialog();
      });
    }
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && bugDialog && bugDialog.style.display !== "none") {
        _closeBugDialog();
      }
    });
  })();

  // Phrase / section labels follow the global LiveChordI18n picker. Listen
  // for language changes and re-render every annotated label in place from
  // its data-zh / data-en attributes — covers the chord-ribbon section
  // headers (.rv-section-text), per-card phrase headers (.rv-grid-phrase),
  // and the A-B segment loop pills (.ab-phrase-pill).
  document.addEventListener("livechord:langchange", () => {
    const lang = _currentPhraseLang();
    document.querySelectorAll(".rv-grid-phrase, .rv-section-text, .ab-phrase-pill").forEach(el => {
      const v = el.dataset && el.dataset[lang];
      if (v) el.textContent = v;
    });
  });

  // ---------------------------------------------------------------------
  // i18n re-renderers for JS-set tooltip / title / textContent that don't
  // ride the data-i18n applyDom() path. Without this listener, dict-key
  // references would resolve to literals on first render (race with the
  // async dict fetch in i18n.js _boot) and never refresh on lang flips.
  // ---------------------------------------------------------------------
  function _refreshI18nTooltips() {
    try { _syncRhContentBtn(); } catch (e) {}
    try {
      if (typeof chordData !== 'undefined' && chordData) {
        _updateChordQualityBadge(chordData, hashMode || trackPath);
      }
    } catch (e) {}
    // _buildUnifiedRibbon owns the BPM element title — re-running it on
    // langchange repaints bpmEl.title with the new dict values.
    try {
      if (typeof chordData !== 'undefined' && chordData
          && typeof _buildUnifiedRibbon === 'function') {
        _buildUnifiedRibbon();
      }
    } catch (e) {}
  }
  document.addEventListener("livechord:i18nready", _refreshI18nTooltips);
  document.addEventListener("livechord:langchange", _refreshI18nTooltips);

})();

// 移調工具函式、簡譜函式 moved to utils.js
