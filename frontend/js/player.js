/** LiveChord 播放頁 */

(function () {
  const $ = (sel) => document.querySelector(sel);

  const params = new URLSearchParams(window.location.search);
  const trackPath = params.get("path");
  const hashMode = params.get("hash");   // process result: load by hash directly
  const autoplay = params.get("autoplay") === "1";
  const restoreFs = params.get("fs") === "1";
  if (!trackPath && !hashMode) { window.location.href = "/"; return; }

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
  // RH waterfall/keyboard content: "acc" = accompaniment only, "mel" = vocal
  // melody only, "both" = merge. Learner can flip to practice either line;
  // "both" is the power-user overlay view. Default to "acc" (what the hand
  // actually plays if following the arrangement).
  const _RH_MODES = ["acc", "mel", "both"];
  const _RH_LABELS = { acc: "伴奏", mel: "旋律", both: "伴奏加旋律" };
  let rhContentMode = localStorage.getItem("livechord_rh_mode") || "acc";
  if (!_RH_MODES.includes(rhContentMode)) rhContentMode = "acc";
  function _syncRhContentBtn() {
    const lab = document.getElementById("btnRhContentLabel");
    if (lab) lab.textContent = _RH_LABELS[rhContentMode] || "伴奏";
    const btn = document.getElementById("btnRhContent");
    if (btn) btn.title = `右手內容：${rhContentMode === "acc" ? "伴奏" : rhContentMode === "mel" ? "旋律" : "全部"}（點擊切換）`;
  }
  let showFingering = localStorage.getItem("livechord_show_fingering") !== "false"; // 預設開啟
  let teachStyle = localStorage.getItem("livechord_teach_style") || "Auto";
  let teachLevel = localStorage.getItem("livechord_teach_level") || "L1";
  if (!["L1", "L2", "L3"].includes(teachLevel)) teachLevel = "L1";
  let accData = null;  // {left_hand:[], right_hand:[]} from API
  let _beatPhase = 0;  // beat grid phase offset (seconds)
  let currentSecPerBeat = 0.6; // For chord dot lighting
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

  // ---- YouTube embed state (hoisted so helpers below can reference) ----
  let _ytPlayer = null;
  let _ytSyncTimer = null;
  // Duration desync state: set when chord data loads (Task 1).
  // _chordDuration: song length from chord JSON (seconds). 0 = unknown/skip check.
  // _ytSyncDisabled: true → YT timer skips chord/piano/key updates (time+progress still update).
  // _ytVerifiedOk: true → duration Δ < 5%, safe to auto-learn YT→library mapping (Task 2 gate).
  let _chordDuration = 0;
  let _ytSyncDisabled = false;
  let _ytVerifiedOk = false;
  // YT PiP widget (drag + resize + show/hide + localStorage persist).
  // Position/size are persisted PER ORIENTATION so rotating the device
  // restores the size the user chose for that orientation.
  const _YT_PIP_KEY = "livechord_yt_pip";
  function _getYtOrient() {
    return (window.innerHeight > window.innerWidth) ? "p" : "l";
  }
  function _loadYtPipRoot() {
    try { return JSON.parse(localStorage.getItem(_YT_PIP_KEY) || "{}") || {}; }
    catch { return {}; }
  }
  function _loadYtPipState() {
    // Flat shape {hidden, x, y, width, height} with per-orientation overrides
    // under .p / .l — callers read the merged view for the current orientation.
    const root = _loadYtPipRoot();
    const orient = _getYtOrient();
    const per = (root[orient] && typeof root[orient] === "object") ? root[orient] : {};
    return {
      hidden: !!root.hidden,
      x: typeof per.x === "number" ? per.x : undefined,
      y: typeof per.y === "number" ? per.y : undefined,
      width: typeof per.width === "number" ? per.width : undefined,
      height: typeof per.height === "number" ? per.height : undefined,
    };
  }
  function _saveYtPipState(partial) {
    try {
      const root = _loadYtPipRoot();
      const orient = _getYtOrient();
      if (!root[orient] || typeof root[orient] !== "object") root[orient] = {};
      if (typeof partial.hidden === "boolean") root.hidden = partial.hidden;
      for (const k of ["x", "y", "width", "height"]) {
        if (typeof partial[k] === "number") root[orient][k] = partial[k];
      }
      localStorage.setItem(_YT_PIP_KEY, JSON.stringify(root));
    } catch {}
  }
  function _applyYtPipState() {
    const container = document.getElementById("ytEmbedContainer");
    if (!container) return;
    const s = _loadYtPipState();
    // Clear stale inline sizing first so a smaller orientation doesn't inherit
    // larger values from the previous one.
    container.style.left = ""; container.style.top = "";
    container.style.right = ""; container.style.bottom = "";
    container.style.width = ""; container.style.height = "";
    // Clamp size to viewport so saved width/height from a larger window
    // don't cause the PiP to overflow here.
    const vw = window.innerWidth, vh = window.innerHeight;
    let w = typeof s.width === "number" ? s.width : 320;
    let h = typeof s.height === "number" ? s.height : 200;
    w = Math.max(180, Math.min(vw - 16, w));
    h = Math.max(120, Math.min(Math.round(vh * 0.7), h));
    if (typeof s.width === "number") container.style.width = w + "px";
    if (typeof s.height === "number") container.style.height = h + "px";
    // Clamp position to viewport — a saved x/y from a larger window (or after
    // a window resize/rotate) can park the PiP off-screen. Top clamp matches
    // drag handler (44 = topbar height) so PiP never hides behind it.
    const TOPBAR_H = 44;
    if (typeof s.x === "number") {
      const x = Math.max(0, Math.min(vw - w, s.x));
      container.style.left = x + "px"; container.style.right = "auto";
    }
    if (typeof s.y === "number") {
      const y = Math.max(TOPBAR_H, Math.min(vh - h, s.y));
      container.style.top = y + "px"; container.style.bottom = "auto";
    }
  }
  function _updateYtReopenBtn() {
    const btn = document.getElementById("ytFloatBtn");
    if (!btn) return;
    const hidden = !!_loadYtPipState().hidden;
    const show = hidden && !!_ytPlayer;
    btn.style.display = show ? "flex" : "none";
  }
  function _showYtPip() {
    const container = document.getElementById("ytEmbedContainer");
    if (!container) return;
    container.style.display = "";
    _saveYtPipState({ hidden: false });
    _updateYtReopenBtn();
  }
  function _initYtPipControls() {
    const container = document.getElementById("ytEmbedContainer");
    const dragzone = document.getElementById("ytPipDragzone");
    const handle = document.getElementById("ytPipResize");
    const closeBtn = document.getElementById("ytEmbedClose");
    const reopenFab = document.getElementById("ytFloatBtn");
    if (!container) return;
    _applyYtPipState();

    // Drag anywhere on the PiP body (overlay). Distinguishes tap vs drag by
    // movement threshold: pure tap (<6px) is re-routed to YT API as play/pause
    // (replacing the native iframe click the overlay swallows); drag moves
    // the PiP. Top clamp = 44 so the PiP never hides behind the player-topbar.
    if (dragzone) dragzone.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      dragzone.setPointerCapture?.(e.pointerId);
      const rect = container.getBoundingClientRect();
      const startX = e.clientX, startY = e.clientY;
      const startL = rect.left, startT = rect.top;
      let moved = false;
      const THRESH = 6;
      const TOPBAR_H = 44;
      const move = (ev) => {
        const dx = ev.clientX - startX;
        const dy = ev.clientY - startY;
        if (!moved && Math.hypot(dx, dy) > THRESH) moved = true;
        if (!moved) return;
        const w = container.offsetWidth, h = container.offsetHeight;
        const nx = Math.max(0, Math.min(window.innerWidth - w, startL + dx));
        const ny = Math.max(TOPBAR_H, Math.min(window.innerHeight - h, startT + dy));
        container.style.left = nx + "px";
        container.style.top = ny + "px";
        container.style.right = "auto";
        container.style.bottom = "auto";
      };
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", up);
        if (!moved) {
          // Pure tap — toggle YT play/pause via API (iframe never saw the click)
          if (_ytPlayer && typeof _ytPlayer.getPlayerState === "function") {
            try {
              const s = _ytPlayer.getPlayerState();
              if (s === 1) _ytPlayer.pauseVideo();
              else if (typeof _ytPlayer.playVideo === "function") _ytPlayer.playVideo();
            } catch {}
          }
        } else {
          const r = container.getBoundingClientRect();
          _saveYtPipState({ x: r.left, y: r.top });
        }
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", up);
    });

    // Resize via bottom-right handle. Container IS the body now (no header
    // element), so maintain 16:9 across the whole container — keeps iframe
    // exactly video-aspect so there are no residual black bars.
    const BODY_ASPECT = 16 / 9;
    if (handle) handle.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      e.stopPropagation();
      handle.setPointerCapture?.(e.pointerId);
      const rect = container.getBoundingClientRect();
      const startX = e.clientX, startY = e.clientY;
      const startW = rect.width;
      const move = (ev) => {
        const maxW = window.innerWidth - rect.left - 4;
        const maxH = window.innerHeight - rect.top - 4;
        // Drive resize off whichever axis moved more so diagonal drag feels
        // natural; derive the other axis from the 16:9 rule.
        const dX = ev.clientX - startX;
        const dY = ev.clientY - startY;
        let nw;
        if (Math.abs(dY) > Math.abs(dX)) {
          const nh0 = Math.max(100, Math.min(maxH, (startW / BODY_ASPECT) + dY));
          nw = nh0 * BODY_ASPECT;
        } else {
          nw = Math.max(140, Math.min(maxW, startW + dX));
        }
        nw = Math.max(140, Math.min(maxW, nw));
        let nh = nw / BODY_ASPECT;
        if (nh > maxH) { nh = maxH; nw = nh * BODY_ASPECT; }
        container.style.width = Math.round(nw) + "px";
        container.style.height = Math.round(nh) + "px";
        // Nudge YT API so the player UI inside the iframe re-lays out to the
        // new size — CSS alone scales the iframe box, but YT's internal player
        // only re-rules on an explicit setSize call.
        if (_ytPlayer && typeof _ytPlayer.setSize === "function") {
          try { _ytPlayer.setSize(Math.round(nw), Math.round(nh)); } catch {}
        }
      };
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", up);
        const r = container.getBoundingClientRect();
        _saveYtPipState({ width: r.width, height: r.height });
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", up);
    });

    // Close = hide the PiP (YT keeps playing audio; toolbar btn reopens it)
    if (closeBtn) closeBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      container.style.display = "none";
      _saveYtPipState({ hidden: true });
      _updateYtReopenBtn();
    });

    // Floating FAB re-shows the PiP (replaces old toolbar button)
    if (reopenFab) reopenFab.addEventListener("click", _showYtPip);

    // Lock toggle — flips .unlocked class so dragzone goes pointer-events:none
    // and user can tap YT's native captions / settings / fullscreen.
    const lockBtn = document.getElementById("ytPipLock");
    if (lockBtn) lockBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const unlocked = container.classList.toggle("unlocked");
      lockBtn.innerHTML = unlocked ? "&#x1F513;" : "&#x1F512;";   // 🔓 : 🔒
      lockBtn.title = unlocked ? "鎖回拖曳模式" : "解鎖以使用字幕/設定";
    });
  }
  // Clear the "hidden" flag on every page load so new navigation shows the PiP
  // by default (user can still close per-visit). Keep x/y/width/height persisted.
  _saveYtPipState({ hidden: false });

  // Wire up immediately — DOM is already present in the IIFE scope
  _initYtPipControls();

  // On rotate / viewport resize → re-apply the orientation-specific PiP state
  // so the widget snaps back to the size the user chose for that orientation.
  let _ytOrientLast = _getYtOrient();
  window.addEventListener("resize", () => {
    const now = _getYtOrient();
    if (now !== _ytOrientLast) {
      _ytOrientLast = now;
      _applyYtPipState();
      if (_ytPlayer && typeof _ytPlayer.setSize === "function") {
        const c = document.getElementById("ytEmbedContainer");
        if (c) {
          const r = c.getBoundingClientRect();
          try { _ytPlayer.setSize(Math.round(r.width), Math.round(r.height)); } catch {}
        }
      }
    }
  });

  // Melody-pending banner: when the user lands on a freshly-analyzed hash,
  // the melody worker is still running in the background (~40–60s). Show a
  // small spinner + poll /api/ai/melody until it lands, then swap into
  // melodyData so the waterfall picks it up on the next drawWaterfall tick.
  function _showMelodyStatusBanner(text) {
    let el = document.getElementById("ytMelodyBanner");
    if (!el) {
      el = document.createElement("div");
      el.id = "ytMelodyBanner";
      el.className = "yt-melody-banner";
      el.innerHTML = '<span class="yt-mel-spinner"></span><span class="yt-mel-text"></span>';
      document.body.appendChild(el);
    }
    el.querySelector(".yt-mel-text").textContent = text || "";
  }
  function _hideMelodyStatusBanner() {
    const el = document.getElementById("ytMelodyBanner");
    if (el) el.remove();
  }
  // Polling lifecycle handles: AbortController cancels in-flight fetch and
  // the timeout keeps the retry chain alive. Both are torn down on `pagehide`
  // so hitting browser-back during extraction doesn't leave a 5-minute retry
  // chain chained to an orphaned fetch — that was making the previous page
  // feel unresponsive for ~1 minute while the browser waited for the fetch
  // to settle (melody endpoint can take ~1 minute uncached).
  let _melodyPollAbort = null;
  let _melodyPollTimeout = null;
  function _stopMelodyPolling() {
    if (_melodyPollAbort) { try { _melodyPollAbort.abort(); } catch {} _melodyPollAbort = null; }
    if (_melodyPollTimeout) { clearTimeout(_melodyPollTimeout); _melodyPollTimeout = null; }
    _hideMelodyStatusBanner();
  }
  window.addEventListener("pagehide", _stopMelodyPolling);
  function _maybeStartMelodyPolling() {
    if (!hashMode) return;
    const raw = sessionStorage.getItem("livechord_fresh_hash") || "";
    const [h, tsStr] = raw.split("|");
    const ts = parseInt(tsStr || "0", 10);
    if (h !== hashMode || !ts || Date.now() - ts > 10 * 60000) return;

    // Banner only when the user actually intends to see melody. In the
    // default `acc` mode the melody data arrives silently into `melodyData`
    // so if they later toggle to `mel`/`both` it's already populated; a
    // 1-minute "擷取中" banner over a page where chords+audio play fine is
    // just confusing noise.
    const showUi = rhContentMode !== "acc";
    if (showUi) _showMelodyStatusBanner("旋律擷取中，完成後可從 AI 教學 切換右手顯示");

    _melodyPollAbort = new AbortController();
    const signal = _melodyPollAbort.signal;
    const deadline = Date.now() + 5 * 60000;
    const tick = async () => {
      if (signal.aborted) return;
      if (Date.now() > deadline) { _stopMelodyPolling(); return; }
      try {
        const r = await fetch(`/api/ai/melody?hash=${encodeURIComponent(hashMode)}`, { signal });
        if (signal.aborted) return;
        const d = await r.json();
        if (d.melody && d.melody.length > 0) {
          melodyData = _filterMelody(d.melody);
          _stopMelodyPolling();
          // Only toast when the user was seeing the banner — acc-mode users
          // didn't ask about melody, don't surprise them.
          if (showUi) showToast("旋律擷取完成 ✓", 3000);
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
  const _SOURCE_LABEL = {
    midi: "人工校對",
    btc: "AI 偵測",
    btc_upload: "AI 偵測（上傳）",
    btc_batch: "AI 偵測",
    chordy: "AI 偵測",
    chordify: "人工匯入",
  };
  async function _updateChordQualityBadge(cd, ratingKey) {
    const srcBadge = document.getElementById("chordSource");
    if (!srcBadge) return;
    const rawSrc = (cd && cd.source) || "btc";
    const srcLabel = _SOURCE_LABEL[rawSrc] || "未知來源";
    const srcShort = rawSrc.startsWith("btc") ? "AI"
                    : rawSrc === "midi" ? "校"
                    : rawSrc === "chordify" ? "校" : "?";
    // Paint immediately with source-based fallback
    const sourceCssClass =
      rawSrc === "midi" ? "src-midi"
      : rawSrc === "chordify" ? "src-midi"
      : "src-btc";
    srcBadge.className = `chord-source-badge ${sourceCssClass}`;
    srcBadge.textContent = srcShort;
    srcBadge.title = `來源：${srcLabel}`;

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
        srcBadge.textContent = `${avg.toFixed(1)}★`;
        srcBadge.title = `${avg.toFixed(1)}★ × ${count} 人評分　·　來源：${srcLabel}`;
      } else if (count > 0) {
        srcBadge.title = `尚未累積足夠評分（${count} 人）　·　來源：${srcLabel}`;
      } else {
        srcBadge.title = `尚無評分　·　來源：${srcLabel}`;
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

  // Always-available debug hook — safe to call from DevTools Console at any point
  // in the page lifecycle, whether YT has booted or not.
  window.__lcYtDebug = () => {
    try {
      const fill = document.querySelector("#topProgressFill");
      const tc = document.querySelector("#timeCurrent");
      const td = document.querySelector("#timeDuration");
      return {
        hasPlayer: !!_ytPlayer,
        playerIsObj: typeof _ytPlayer === "object",
        state: _ytPlayer && _ytPlayer.getPlayerState ? _ytPlayer.getPlayerState() : "(no getPlayerState)",
        currentTime: _ytPlayer && _ytPlayer.getCurrentTime ? _ytPlayer.getCurrentTime() : "(no getCurrentTime)",
        duration: _ytPlayer && _ytPlayer.getDuration ? _ytPlayer.getDuration() : "(no getDuration)",
        fillWidth: fill ? fill.style.width : "(no el)",
        fillComputedWidth: fill ? getComputedStyle(fill).width : "(no el)",
        timeText: tc ? tc.textContent : "(no el)",
        durationText: td ? td.textContent : "(no el)",
        timerAlive: !!_ytSyncTimer,
        lastError: window.__lcYtError || null,
        ytApiLoaded: !!window.YT,
        chordDuration: _chordDuration,
        syncDisabled: _ytSyncDisabled,
        verifiedOk: _ytVerifiedOk,
      };
    } catch (e) { return { err: e && e.message, stack: e && e.stack }; }
  };

  // ---- Unified playback accessors (YouTube iframe takes precedence over audio element) ----
  function _ytActive() {
    return !!(_ytPlayer && typeof _ytPlayer.getCurrentTime === "function");
  }
  function _playerCurrentTime() {
    if (_ytActive()) {
      try { return _ytPlayer.getCurrentTime() || 0; } catch { return 0; }
    }
    return audio.currentTime || 0;
  }
  function _playerDuration() {
    if (_ytActive()) {
      try {
        const d = _ytPlayer.getDuration();
        if (d && !isNaN(d)) return d;
      } catch {}
    }
    return audio.duration || 0;
  }
  function _playerSeek(t) {
    if (_ytActive() && typeof _ytPlayer.seekTo === "function") {
      try {
        _ytPlayer.seekTo(t, true);
        // Reflect the seek on the UI immediately — don't wait for the 50ms sync interval
        try {
          const dur = _ytPlayer.getDuration() || 0;
          if (timeCurrent) timeCurrent.textContent = formatTime(t);
          if (dur > 0 && topProgressFill) topProgressFill.style.width = ((t / dur) * 100) + "%";
        } catch {}
        return;
      } catch {}
    }
    audio.currentTime = t;
  }

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
      el.textContent = `A ${formatTime(abA)} → B ${formatTime(abB)}`;
    } else if (abState === "a_set" && abA != null) {
      el.textContent = `A ${formatTime(abA)}（待設 B）`;
    } else {
      el.textContent = "未設定";
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

  // Build the phrase label the same way the chord ribbon does so the two UIs
  // stay consistent: types appearing more than once are numbered (Verse 1,
  // Verse 2, Chorus 1, ...); intro/outro/dialogue stay unnumbered.
  function _buildPhraseLabels(secs) {
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
      const base = s.label || s.type || `Phrase ${i + 1}`;
      if (totals[t] > 1 && !NO_NUMBER.includes((t || "").toLowerCase())) {
        return `${base} ${occur[t]}`;
      }
      return base;
    });
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
    const labels = _buildPhraseLabels(secs);
    const html = labels.map((label, i) =>
      `<button class="ab-phrase-pill" data-idx="${i}" role="option">${_escHtml(label)}</button>`
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
    if (labels.length === 0 && _abSelectedSet.size === 0) {
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
      showToast(`循環：${name}（${formatTime(abA)} → ${formatTime(abB)}）`, 2000);
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
      showToast("A-B 循環已取消", 1500);
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

    // 估算 BPM 與每拍秒數
    let estimatedBpm = 100;
    if (chords.length >= 4) {
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
    
    const urlParamsBpm = new URLSearchParams(window.location.search);
    const bpmPath = urlParamsBpm.get("path") || "default";
    let bpmMult = parseFloat(localStorage.getItem(`bpm_mult_${bpmPath}`)) || 1.0;
    
    estimatedBpm = estimatedBpm * bpmMult;
    currentSecPerBeat = 60 / estimatedBpm;
    const secPerBeat = currentSecPerBeat;
    
    const bpmEl = document.getElementById("chordBpm");
    if (bpmEl) {
        bpmEl.textContent = `BPM: ${Math.round(estimatedBpm)}`;
        bpmEl.style.cursor = "pointer";
        bpmEl.title = "點擊切換 BPM 倍率 (自動儲存)";
        bpmEl.onclick = () => {
            if (bpmMult === 1.0) bpmMult = 0.5;
            else if (bpmMult === 0.5) bpmMult = 2.0;
            else bpmMult = 1.0;
            localStorage.setItem(`bpm_mult_${bpmPath}`, bpmMult);
            _buildUnifiedRibbon();
        };
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
        
        // Initial setup
        const curLang = window.liveChordPhraseLang || 'zh';
        gridPhraseEl.textContent = curLang === 'zh' ? phraseLabelZh : phraseLabelEn;
        
        // Toggle language function on left click
        gridPhraseEl.addEventListener("click", (e) => {
          e.stopPropagation();
          const nextLang = (window.liveChordPhraseLang || 'zh') === 'zh' ? 'en' : 'zh';
          window.liveChordPhraseLang = nextLang;
          document.querySelectorAll(".rv-grid-phrase, .rv-section-text").forEach(el => {
            el.textContent = el.dataset[nextLang];
          });
        });

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
        if (_ytPlayer && typeof _ytPlayer.seekTo === "function") {
          _ytPlayer.seekTo(c.time, true);
        } else {
          audio.currentTime = c.time;
        }
        updateActiveChord(c.time, true);
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

      // 動態節拍指示器 (Dynamic Beat Indicator)
      let durSec = 2.0; // 預設最後一拍長度
      if (i < chords.length - 1) {
          durSec = chords[i+1].time - c.time;
      }
      let beats = Math.round(durSec / secPerBeat);
      if (beats < 1) beats = 1;
      if (beats > 16) beats = 16;
      
      const beatsEl = document.createElement("div");
      beatsEl.className = "rv-beats";
      let dotHtml = "";
      for (let b=1; b<=beats; b++) {
          dotHtml += `<span class="beat-dot"></span>`;
      }
      beatsEl.innerHTML = dotHtml;
      item.appendChild(beatsEl);

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
  // Scale persists per layout combo: portrait/landscape × normal/overview.
  // Four keys total (v_normal / v_overview / h_normal / h_overview). Was
  // per-instrument; user asked for per-layout because zoom preference
  // changes dramatically between phone-portrait and desktop-landscape,
  // and between compact vs overview grids.
  function _ribbonLayoutKey() {
    const o = window.matchMedia("(orientation: portrait)").matches ? "v" : "h";
    const m = (chordRibbonPanel && chordRibbonPanel.classList.contains("overview-mode"))
      ? "overview" : "normal";
    return `${o}_${m}`;
  }
  function _readRibbonScale(key) {
    const v = parseFloat(localStorage.getItem(`livechord_ribbon_scale_${key}`));
    return (v >= 0.5 && v <= 3) ? v : 1.0;
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
    ribbonScale = Math.round(Math.max(0.5, Math.min(3, ribbonScale + delta)) * 10) / 10;
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
  document.querySelectorAll(".tb-item").forEach(item => {
    const trigger = item.querySelector(".tb-trigger, a.tb-trigger");
    if (!trigger) return;
    const hasPopup = !!item.querySelector(".tb-popup");
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
        item.classList.toggle("open");
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
  document.addEventListener("click", () => {
    document.querySelectorAll(".tb-item.open").forEach(i => {
      // Keep A-B popup open while user is in the middle of setting A→B manually
      // (so they can tap the progress bar to seek, then tap "設定 B").
      if (i.id === "tbAB" && abState === "a_set") return;
      i.classList.remove("open");
    });
  });
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

      currentChordVersion = null;
      await loadVersions(path);
      await loadChords(path, currentChordVersion);
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
  function _stopMelodyLoad() {
    if (_melodyLoadAbort) { try { _melodyLoadAbort.abort(); } catch {} _melodyLoadAbort = null; }
    _hideMelodyStatusBanner();
  }
  window.addEventListener("pagehide", _stopMelodyLoad);
  async function _loadMelody(path) {
    // Bottom-left banner instead of a centered overlay so chord + audio stay
    // interactive. Banner auto-hides once data lands. Only shown when the
    // user is actually going to look at melody — in `acc` mode the data
    // silently lands in `melodyData` for a later mode-toggle.
    const showUi = rhContentMode !== "acc";
    let showedBanner = false;
    const bannerTimer = showUi ? setTimeout(() => {
      _showMelodyStatusBanner("AI 旋律擷取中，完成後可從 AI 教學 切換右手顯示");
      showedBanner = true;
    }, 600) : null;  // don't flash for instant cached reads
    _melodyLoadAbort = new AbortController();
    try {
      const res = await fetch(`/api/ai/melody?path=${encodeURIComponent(path)}`,
                              { signal: _melodyLoadAbort.signal });
      const data = await res.json();
      if (data.melody && data.melody.length > 0) {
        melodyData = _filterMelody(data.melody);
      }
    } catch {} finally {
      if (bannerTimer) clearTimeout(bannerTimer);
      if (showedBanner) _hideMelodyStatusBanner();
      _melodyLoadAbort = null;
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
       // No accData (e.g. hash mode / not-yet-AI'd song) — synth a simple
       // LH fingering (5-3-1 etc.) so the keyboard still shows numbers when
       // showFingering is ON. Sort low→high, assign descending fingers.
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
    
    const mel = _getMelodyMidi(currentTime);
    // Mirror the waterfall rhContentMode gate so keyboard highlight matches
    // which bar type the user chose to show (avoids ghost keys / doubled
    // highlight mismatches).
    const _rhHasAcc = !!(accData && (accData.right_hand || []).length);
    const _wantMelKey = rhContentMode === "mel" || rhContentMode === "both" || !_rhHasAcc;
    if (mel >= 0 && _wantMelKey && !activeRh.includes(mel)) activeRh.push(mel);

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
    if (!forceRefresh && accData && accData._style === teachStyle && accData._level === teachLevel) return;
    accLoading = true;
    _setLoadingState(true, forceRefresh ? "AI 伴奏重新生成中..." : "AI 伴奏提取中...",
                     forceRefresh ? "清除快取並重新演算（含踏板/力度）..." : "首次播放需要進行即時演算...");
    let url = `/api/ai/accompaniment?path=${encodeURIComponent(p)}&style=${teachStyle}&level=${teachLevel}`;
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
    if (!waterfallCanvas || !waterfallCtx || !waterfallActive) return;
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
    if (accData) {
      if (activeHand === "both" || activeHand === "left") {
        allEvents.push(...(accData.left_hand || []).map(e => ({...e, _hand: "left"})));
      }
      if (activeHand === "both" || activeHand === "right") {
        const accRh = accData.right_hand || [];
        const hasAcc = accRh.length > 0;
        // rhContentMode drives what shows: "acc" (伴奏), "mel" (主唱旋律),
        // "both" (疊加). Falls through to melody if the user picked acc but
        // accData has no right_hand — so "acc" mode isn't accidentally empty.
        const wantAcc = rhContentMode !== "mel" && hasAcc;
        const wantMel = rhContentMode === "mel" || rhContentMode === "both" || !hasAcc;
        let rhEvents = wantAcc ? accRh.slice() : [];
        if (wantMel && typeof melodyData !== 'undefined' && melodyData) {
          const melEvents = melodyData.map(m => ({
            time: m.start,
            duration: m.end - m.start,
            pitch: m.midi,
            finger: null
          }));
          rhEvents = [...rhEvents, ...melEvents];
        }
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
          const rhEvents = melodyData.map(m => ({
            time: m.start,
            duration: m.end - m.start,
            pitch: m.midi,
            finger: null,
            _hand: "right"
          }));
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
          ctx.strokeStyle = "rgba(0,0,0,0.8)";
          ctx.strokeText(text, x + kw / 2, Math.max(fy, 15));
          ctx.fillStyle = "#fff";
          ctx.fillText(text, x + kw / 2, Math.max(fy, 15));
        }
        
        if (isLeft) lhLastF = evt.finger; else rhLastF = evt.finger;
      }
    }

    // ---- Phase 11: Pedal visualization ----
    if (accData && accData.pedal && accData.pedal.length > 0) {
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
        // Use _accPath so hash-mode (8801 beta) songs also get AI suggestions —
        // `trackPath` is empty in hash mode but chordData.path carries the same
        // canonical path that song_hash() will resolve server-side.
        const p = _accPath();
        if (!p) {
          showToast("尚未載入歌曲");
          return;
        }
        showToast("AI 分析曲風中...");
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
              showToast("AI 推薦風格：" + data.suggested_styles.join(", "));
            } else {
              showToast("AI 無法分析（資料不足）");
            }
          }).catch(() => showToast("AI 推薦失敗"));
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
        const names = { acc: "伴奏", mel: "旋律", both: "全部（伴奏+旋律）" };
        showToast(`右手：${names[rhContentMode]}`, 1500);
        update88Piano(audio.currentTime || 0);
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
          ? `平均 ${ver.rating.toFixed(1)} ★ (${ver.count} 票)`
          : "尚無評分";
      wrap.title = ver.can_rate ? baseTitle : (ver.is_self ? "不能對自己的版本評分" : "請先登入");

      const stars = [];
      for (let i = 1; i <= 5; i++) {
          const s = document.createElement("span");
          s.className = "rs-star" + (i <= (ver.my_rating || 0) ? " mine" : "");
          s.textContent = "★";
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
                      avgEl.textContent = ver.count > 0 ? `${ver.rating.toFixed(1)} ★ (${ver.count})` : "—";
                  }
                  wrap.title = ver.count > 0
                      ? `平均 ${ver.rating.toFixed(1)} ★ (${ver.count} 票)`
                      : "尚無評分";
                  showToast(next === 0 ? "已收回評分" : `已評 ${next} 顆星`);
              } catch (err) {
                  console.error("rate failed:", err);
                  showToast("評分失敗", true);
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
          avg.textContent = ver.count > 0 ? `${ver.rating.toFixed(1)} ★ (${ver.count})` : "—";
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
              _setLoadingState(true, "切換版本中...", "載入 " + ver.name + " 的和弦");
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

  async function loadChords(path, version = null) {
    try {
      chordData = await API.getChords(path, version);
      if (chordData.exists && chordData.chords && chordData.chords.length > 0) {
        hasChords = true;
        // Task 1: record album-track duration for YT desync detection; reset gates.
        _chordDuration = _computeChordDuration(chordData);
        _ytSyncDisabled = false;
        _ytVerifiedOk = false;
        // 和弦品質燈號（helper handles both source + user rating summary）
        _updateChordQualityBadge(chordData, /*key*/ trackPath);
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
        // Beta: attach YouTube iframe for video sync (DB-path mode parity with hash mode)
        _isBetaModeAsync.then(isBeta => {
          if (!isBeta || _usingLocalFile) return;
          const ytUrl = chordData.youtube_url || "";
          const _extractId = typeof extractYouTubeId === "function" ? extractYouTubeId
            : (u) => { const m = (u||"").match(/(?:v=|youtu\.be\/|\/shorts\/)([A-Za-z0-9_-]{11})/); return m ? m[1] : null; };
          const ytVideoId = _extractId(ytUrl);
          const willEmbed = ytVideoId || (chordData.title || songTitle?.textContent || "").trim();
          if (willEmbed) {
            // Pause NAS audio so it doesn't play alongside YouTube (if YT fails, user can hit ▶ to resume)
            try { audio.pause(); } catch {}
          }
          if (ytVideoId) {
            _initYouTubeEmbed(ytVideoId);
          } else {
            const t = chordData.title || (songTitle && songTitle.textContent) || "";
            const a = chordData.artist || "";
            const q = (a ? `${a} ${t}` : t).trim();
            if (q) _searchAndEmbedYouTube(q);
          }
        });
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
          <div class="chord-empty-msg">尚無和弦譜</div>
          <button id="btnDetectHero" class="chord-empty-btn">一鍵偵測和弦</button>
          <div class="chord-empty-hint">首次分析約 30 秒 ~ 1 分鐘</div>
        </div>`;
    }
    _isBetaModeAsync.then(isBeta => {
      if (!isBeta && trackPath) runChordDetection();
    }).catch(() => {});
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
      await loadVersions(trackPath);
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

    activeChordIdx = newIdx;

    if (activeChordIdx >= 0 && activeChordIdx < ribbonElements.length) {
      const el = ribbonElements[activeChordIdx];
      el.classList.remove("played");
      el.classList.add("active");

      // Auto-scroll ribbon to keep active chord visible. Gate must cover both
      // NAS audio playback (8800) and YT embed playback (8801 hash mode) —
      // audio.paused is always true in hash mode because <audio> never starts.
      const _isAnyPlaying = _ytActive()
        ? (_ytPlayer.getPlayerState && _ytPlayer.getPlayerState() === 1)
        : !audio.paused;
      if (chordRibbonPanel && (_isAnyPlaying || forceScroll)) {
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
    // YouTube embed mode: control YouTube player
    if (_ytPlayer && typeof _ytPlayer.getPlayerState === "function") {
      const state = _ytPlayer.getPlayerState();
      if (state === 1) { _ytPlayer.pauseVideo(); btnPlay.classList.remove("is-playing"); }
      else { _ytPlayer.playVideo(); btnPlay.classList.add("is-playing"); }
      return;
    }
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
      _rewindToStart();
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

  function _updateBeatDots(t) {
    if (activeChordIdx >= 0 && activeChordIdx < ribbonElements.length) {
      const el = ribbonElements[activeChordIdx];
      const startTime = parseFloat(el.dataset.time);
      const elapsed = t - startTime;
      let beatIdx = Math.floor(elapsed / currentSecPerBeat);
      if (beatIdx < 0) beatIdx = 0;
      
      const dots = el.querySelectorAll(".beat-dot");
      if (dots.length > 0) {
          if (beatIdx >= dots.length) beatIdx = dots.length - 1; // Clamp to last dot
          dots.forEach((dot, idx) => {
              if (idx === beatIdx) {
                  dot.classList.add("beat-active");
              } else {
                  dot.classList.remove("beat-active");
              }
          });
      }
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
      updateActiveChord(t);
      _updateBeatDots(t);
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
    _updateBeatDots(t);
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
    showToast(LOOP_LABELS[loopMode], 1500);
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

    // off — stop
    btnPlay.classList.remove("is-playing");
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
    const v = parseFloat(volumeSlider.value);
    audio.volume = v;
    audio.muted = false;
    if (_ytPlayer && typeof _ytPlayer.setVolume === "function") {
      _ytPlayer.setVolume(v * 100);
      _ytPlayer.unMute();
    }
    localStorage.setItem("livechord_volume", volumeSlider.value);
    if (btnMute) btnMute.classList.toggle("is-muted", v === 0);
  });

  // Mute toggle
  const btnMute = $("#btnMute");
  let _preMuteVol = 1;
  if (btnMute) {
    btnMute.addEventListener("click", () => {
      // Touch devices: let the click open the popup only. Users complained
      // that tapping the speaker silenced audio when they just wanted the
      // volume slider (field feedback 2026-04-19).
      if (_isTouchLike) return;
      audio.muted = !audio.muted;
      if (_ytPlayer && typeof _ytPlayer.isMuted === "function") {
        if (_ytPlayer.isMuted()) _ytPlayer.unMute(); else _ytPlayer.mute();
      }
      btnMute.classList.toggle("is-muted", audio.muted);
    });
  }

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
    if (_ytPlayer && typeof _ytPlayer.setPlaybackRate === "function") {
      try { _ytPlayer.setPlaybackRate(s); } catch (e) {}
    }
    _syncSpeedUI();
    localStorage.setItem("livechord_speed", s);
  }
  function _cycleSpeed() {
    if (_isTouchLike) return;  // touch devices use the popup (see .speed-opt handlers)
    const i = (speedIdx + 1) % SPEEDS.length;
    _setSpeed(SPEEDS[i]);
  }
  if (btnSpeed) btnSpeed.addEventListener("click", _cycleSpeed);
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
      let editUrl = `/editor?path=${encodeURIComponent(trackPath)}`;
      if (currentChordVersion) editUrl += `&version=${encodeURIComponent(currentChordVersion)}`;
      btnEdit.href = editUrl;
    }
    _updateEditLink();
    window._updateEditLink = _updateEditLink;
  }

  // ---- Chord Correction buttons ----
  const _corrRebuild = () => {
    chordCache = {};
    buildChordDOM();
    activeChordIdx = -1;
    requestAnimationFrame(() => updateActiveChord(audio.currentTime || -1, true));
    const btnSave = $("#btnSaveCorrected"), btnRevert = $("#btnRevertCorrection");
    if (window.ChordCorrection && window.ChordCorrection.hasBackup()) {
      if (btnSave) btnSave.style.display = "";
      if (btnRevert) btnRevert.style.display = "";
    }
  };
  // Expose state for chord-correction.js
  Object.defineProperty(window, '_playerActiveChordIdx', { get: () => activeChordIdx });
  Object.defineProperty(window, '_playerSecPerBeat', { get: () => currentSecPerBeat });

  // Wrapper that exposes playback state transparently for YT mode too.
  // chord-correction.js only reads `.paused`, `.currentTime`, `.duration` —
  // delegate those to the YT player when it's active (beta hash mode), else
  // forward to the raw HTMLAudioElement (8800 path mode).
  const _audioForCorrection = new Proxy(audio, {
    get(target, prop) {
      if (_ytActive()) {
        if (prop === "paused") {
          try { return _ytPlayer.getPlayerState() !== 1; } catch { return true; }
        }
        if (prop === "currentTime") {
          try { return _ytPlayer.getCurrentTime() || 0; } catch { return 0; }
        }
        if (prop === "duration") {
          try { return _ytPlayer.getDuration() || 0; } catch { return 0; }
        }
      }
      const v = target[prop];
      return typeof v === "function" ? v.bind(target) : v;
    },
  });

  const btnBeatTap = $("#btnBeatTap");
  if (btnBeatTap) {
    btnBeatTap.addEventListener("click", () => {
      if (!chordData || !chordData.chords || chordData.chords.length === 0) {
        showToast("尚無和弦資料", 2000); return;
      }
      window.ChordCorrection.enterBeatTap(chordData, _audioForCorrection, _corrRebuild);
    });
  }

  // Export-data lives inside the toolbar Tools popup (moved from homepage
  // header menu) so the user reaches it mid-practice, where they're most
  // likely to want a backup of their ratings/recents/favorites.
  const btnExportData = $("#btnExportData");
  if (btnExportData) {
    btnExportData.addEventListener("click", async () => {
      try {
        showToast("匯出中…", 2000);
        const res = await fetch("/api/export-data");
        if (!res.ok) { showToast("無資料可匯出", 3000); return; }
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
        showToast("匯出失敗：" + (e.message || "網路錯誤"), 3000);
      }
    });
  }

  const btnChordAlign = $("#btnChordAlign");
  if (btnChordAlign) {
    btnChordAlign.addEventListener("click", () => {
      if (!chordData || !chordData.chords || chordData.chords.length === 0) {
        showToast("尚無和弦資料", 2000); return;
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
        showToast("尚無和弦資料", 2000); return;
      }
      const CC = window.ChordCorrection;
      CC.backup(chordData);
      let count = 0;
      for (let i = chordData.chords.length - 1; i >= 0; i--) {
        const dur = (i < chordData.chords.length - 1)
          ? chordData.chords[i + 1].time - chordData.chords[i].time : 2.0;
        const beats = Math.round(dur / currentSecPerBeat);
        if (beats >= 8) {
          const half = Math.floor(beats / 2);
          CC.splitChord(chordData, i, half, beats - half, currentSecPerBeat);
          count++;
        }
      }
      if (count > 0) { _corrRebuild(); showToast(`已切分 ${count} 個長和弦`, 2500); }
      else showToast("沒有需要切分的長和弦 (≥8拍)", 2000);
    });
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
        showToast("校正版本已儲存", 2000);
        btnSaveCorrected.style.display = "none";
      } catch (err) {
        showToast("儲存失敗: " + err.message, 3000);
      }
    });
  }

  const btnRevertCorrection = $("#btnRevertCorrection");
  if (btnRevertCorrection) {
    btnRevertCorrection.addEventListener("click", () => {
      if (window.ChordCorrection && window.ChordCorrection.hasBackup()) {
        window.ChordCorrection.revert(chordData, _corrRebuild);
        btnSaveCorrected.style.display = "none";
        btnRevertCorrection.style.display = "none";
        showToast("已還原至校正前", 2000);
      }
    });
  }

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

  // ---- Jazzify 按鈕 (merged: off → L1 → L2 → L3 → ✨AI → off) ----
  const btnJazzify = $("#btnJazzify");
  let jazzifyLevel = 0;  // 0=off, 1/2/3=rule-based, 4=AI transformer
  let originalChords = null;
  let jazzifyReqGen = 0;  // generation counter — stale async callbacks check this

  function _syncJazzifyPopup() {
    document.querySelectorAll(".jazz-opt").forEach(b => {
      b.classList.toggle("active", parseInt(b.dataset.jazz, 10) === jazzifyLevel);
    });
  }
  _syncJazzifyPopup();

  async function _setJazzifyLevel(lvl) {
    // Allow the call to proceed even if current chords went empty — so long
    // as we have an `originalChords` backup to restore to. Without this, a
    // degenerate API response (empty res.chords) would pin the button on AI
    // state forever.
    const chordsEmpty = !chordData || !chordData.chords || chordData.chords.length === 0;
    if (chordsEmpty && !originalChords) {
      showToast("\u5C1A\u7121\u548C\u5F26\u8CC7\u6599", 2000);
      return;
    }
    if (lvl === jazzifyLevel && lvl !== 0) return;  // no-op: already at level

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

    try {
      const res = await API.jazzify(originalChords, chordData.key || "C", apiLevel, mode);
      if (myGen !== jazzifyReqGen) return;
      if (res.error) throw new Error(res.error);
      if (!Array.isArray(res.chords) || res.chords.length === 0) {
        throw new Error("\u4F3A\u670D\u5668\u672A\u56DE\u50B3\u548C\u5F26");
      }
      chordData.chords = res.chords;

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
      const label = isAI ? "AI Transformer" : `Jazzify L${jazzifyLevel}`;
      showToast(`${label}: ${res.original_count}\u2192${res.jazzified_count} \u548C\u5F26, ${res.changes.length} \u8B8A\u66F4`, 3000);
    } catch (err) {
      if (myGen !== jazzifyReqGen) return;
      showToast("Jazzify \u5931\u6557: " + err.message, 3000);
      jazzifyLevel = 0;
      _syncJazzifyPopup();
      btnJazzify.textContent = "\u{1F3B7}";
      btnJazzify.style.background = "";
      btnJazzify.style.color = "";
    }
  }

  if (btnJazzify) {
    btnJazzify.addEventListener("click", () => {
      if (_isTouchLike) return;  // touch devices use the popup (see .jazz-opt handlers)
      _setJazzifyLevel((jazzifyLevel + 1) % 5);
    });
  }
  document.querySelectorAll(".jazz-opt").forEach(b => {
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      _setJazzifyLevel(parseInt(b.dataset.jazz, 10));
      const item = b.closest(".tb-item");
      if (item) item.classList.remove("open");
    });
  });

  // ---- manual detect: shared by Tools popup button + hero empty-state button (Task 6) ----
  async function runChordDetection() {
    if (!trackPath) { showToast("無法偵測：此頁為 hash 模式", 3000); return; }
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
  }

  const btnDetect = $("#btnDetect");
  if (btnDetect) {
    btnDetect.addEventListener("click", () => runChordDetection());
    // In beta mode the detect / MIDI-import path requires trackPath (NAS), which
    // beta non-admin users don't have — hide the button rather than let them
    // tap it and get "此頁為 hash 模式" toast.
    _isBetaModeAsync.then(isBeta => {
      if (isBeta) btnDetect.style.display = "none";
    }).catch(() => {});
  }
  // Hero detect button (Task 6) is injected into empty chord state; bind via delegation.
  document.addEventListener("click", (e) => {
    if (e.target && e.target.id === "btnDetectHero") runChordDetection();
  });

  // ---- favorite ----
  const _favPath = trackPath || (hashMode ? `__hash/${hashMode}` : "");
  btnFav.addEventListener("click", async () => {
    if (!_favPath) return;
    try {
      if (isFavorite) {
        await API.removeFavorite(_favPath);
        isFavorite = false;
        favTracks = favTracks.filter(p => p !== _favPath);
        showToast("已取消收藏");
      } else {
        await API.addFavorite(_favPath);
        isFavorite = true;
        if (!favTracks.includes(_favPath)) favTracks.unshift(_favPath);
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
                ? `/player?hash=${encodeURIComponent(h)}`
                : `/player?path=${encodeURIComponent(el.dataset.path)}`;
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
      _setLoadingState(true, "載入和弦中...", "讀取分析結果...");
      try {
        const res = await fetch(`/api/chords/by-hash?hash=${encodeURIComponent(hashMode)}`);
        if (!res.ok) throw new Error("找不到和弦資料");
        chordData = await res.json();
        if (chordData.exists && chordData.chords && chordData.chords.length > 0) {
          hasChords = true;
          // Task 1: record album-track duration for YT desync detection; reset gates.
          _chordDuration = _computeChordDuration(chordData);
          _ytSyncDisabled = false;
          _ytVerifiedOk = false;
          const title = chordData.title
            || (chordData.path ? chordData.path.split("/").pop().replace(/\.\w+$/i, "") : "")
            || "分析結果";
          songTitle.textContent = title;
          songTitle.title = title;
          _checkMarquee(songTitle);
          document.title = `${title} — LiveChord`;
          if (chordData.key) {
            const keyInfo = $("#chordKey");
            if (keyInfo) keyInfo.textContent = `Key: ${chordData.key}`;
          }
          _updateChordQualityBadge(chordData, hashMode);
          await preloadChordInfo(chordData.chords);
          buildChordDOM();

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
            const melUrl = melPath
              ? `/api/ai/melody?path=${encodeURIComponent(melPath)}`
              : `/api/ai/melody?hash=${encodeURIComponent(hashMode)}`;
            const melRes = await fetch(melUrl);
            const melData = await melRes.json();
            if (melData.melody && melData.melody.length > 0) {
              melodyData = _filterMelody(melData.melody);
            } else {
              // Freshly-analyzed hash → melody worker is still running; poll + banner.
              _maybeStartMelodyPolling();
            }
          } catch {}

          // Try to auto-load audio from IndexedDB (uploaded file pass-through)
          let audioLoaded = false;
          try {
            const blob = await audioDBLoad(hashMode);
            if (blob) {
              const objUrl = URL.createObjectURL(blob);
              audio.src = objUrl;
              _usingLocalFile = true;
              audio.play().catch(() => {});
              audioLoaded = true;
              audioDBDelete(hashMode); // clean up storage
            }
          } catch (e) { console.warn("IndexedDB load failed:", e); }

          // YouTube embed mode
          const ytUrl = chordData.youtube_url || "";
          const _extractId = typeof extractYouTubeId === "function" ? extractYouTubeId
            : (u) => { const m = (u||"").match(/(?:v=|youtu\.be\/|\/shorts\/)([A-Za-z0-9_-]{11})/); return m ? m[1] : null; };
          const ytVideoId = _extractId(ytUrl);
          if (!audioLoaded && ytVideoId) {
            _initYouTubeEmbed(ytVideoId);
          } else if (!audioLoaded) {
            // No audio blob and no youtube_url — try searching YouTube by title.
            // If we can't even search (no title), surface the fallback panel so
            // the user can paste a URL or load a local file instead of being
            // stranded with just a toast.
            const searchTitle = chordData.title || "";
            if (searchTitle) {
              _searchAndEmbedYouTube(searchTitle);
            } else {
              _showYtFallbackPanel();
            }
          }
        } else {
          // Hash mode, but no chord data (song exists in library metadata but not yet analyzed,
          // or the chord JSON was wiped). Give the user an out: clear empty state in the chord
          // area, plus a fallback panel so they can paste a YT URL or load a local audio file
          // and at least audition the track while analysis is pending.
          const fallbackTitle = chordData.title
            || (chordData.path ? chordData.path.split("/").pop().replace(/\.\w+$/i, "") : "")
            || "尚未分析";
          songTitle.textContent = fallbackTitle;
          songTitle.title = fallbackTitle;
          _checkMarquee(songTitle);
          document.title = `${fallbackTitle} — LiveChord`;
          if (unifiedRibbonTrack) {
            unifiedRibbonTrack.innerHTML = `
              <div class="chord-empty-state">
                <div class="chord-empty-msg">此曲尚未分析</div>
                <div class="chord-empty-hint">先載入音源試聽，等分析完成後即可顯示和弦</div>
              </div>`;
          }
          showToast("此曲尚未分析 — 可貼 YouTube URL 或上傳音檔試聽", 5000);
          _showYtFallbackPanel();
        }
      } catch (e) {
        songTitle.textContent = "載入失敗";
        showToast("載入失敗: " + e.message, 4000);
      } finally {
        _setLoadingState(false);
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

  // --- YouTube IFrame embed for chord sync ---
  // (_ytPlayer / _ytSyncTimer declared near top of IIFE so playback helpers can reference them)

  async function _searchAndEmbedYouTube(title) {
    try {
      showToast("搜尋 YouTube 對應曲目...", 3000);
      const res = await fetch(`/api/process/youtube-search?q=${encodeURIComponent(title)}`);
      if (!res.ok) { _showYtFallbackPanel(); return; }
      const data = await res.json();
      if (data.video_id) {
        _initYouTubeEmbed(data.video_id);
      } else {
        // Task 7: no YT match found — let user paste URL or load local file.
        _showYtFallbackPanel();
      }
    } catch (e) {
      _showYtFallbackPanel();
    }
  }

  // Task 7: fallback panel — user pastes correct YT URL or uploads a local audio file
  // when auto-search fails or desync banner indicates wrong version.
  function _showYtFallbackPanel() {
    const old = document.getElementById("ytFallbackPanel");
    if (old) { old.remove(); }
    const panel = document.createElement("div");
    panel.id = "ytFallbackPanel";
    panel.className = "yt-fallback-panel";
    panel.innerHTML = `
      <button class="yt-fb-close" aria-label="關閉">&times;</button>
      <h3>選擇正確的音源</h3>
      <div class="yt-fb-hint">自動搜尋找不到對應版本，或版本長度不符和弦。你可以：</div>
      <div class="yt-fb-row">
        <input id="ytFbUrl" type="text" placeholder="貼上 YouTube URL" />
        <button id="ytFbUrlSubmit">使用</button>
      </div>
      <div class="yt-fb-sep"><span>或</span></div>
      <div class="yt-fb-row">
        <input id="ytFbFile" type="file" accept="audio/*" />
        <button id="ytFbFileSubmit" class="secondary">載入本地檔</button>
      </div>
    `;
    document.body.appendChild(panel);
    panel.querySelector(".yt-fb-close")?.addEventListener("click", () => panel.remove());
    panel.querySelector("#ytFbUrlSubmit")?.addEventListener("click", () => _onYtFbUrlSubmit(panel));
    panel.querySelector("#ytFbUrl")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") _onYtFbUrlSubmit(panel);
    });
    panel.querySelector("#ytFbFileSubmit")?.addEventListener("click", () => _onYtFbFileSubmit(panel));
  }

  function _onYtFbUrlSubmit(panel) {
    const input = panel.querySelector("#ytFbUrl");
    const raw = (input && input.value || "").trim();
    const m = raw.match(/(?:v=|youtu\.be\/|\/shorts\/)([A-Za-z0-9_-]{11})/);
    const vid = m ? m[1] : "";
    if (!vid) { showToast("無法識別 YouTube URL", 3000); return; }
    // Tear down existing YT player so _initYouTubeEmbed can rebuild cleanly
    if (_ytPlayer) { try { _ytPlayer.destroy(); } catch {} _ytPlayer = null; }
    if (_ytSyncTimer) { clearInterval(_ytSyncTimer); _ytSyncTimer = null; }
    _ytSyncDisabled = false; _ytVerifiedOk = false;
    // Clear guard so re-learn can fire after new duration check
    try {
      for (const k of Object.keys(sessionStorage)) {
        if (k.startsWith("ytlib:")) sessionStorage.removeItem(k);
      }
    } catch {}
    panel.remove();
    _initYouTubeEmbed(vid);
    // User manually picked a URL — strong "this is the version I want" signal.
    // Always kick off analysis so chord data matches their chosen version.
    // Backend reuses existing result if URL was already processed (library map
    // or prior YT job), so the cost is zero when nothing new needs analyzing.
    if (hashMode) {
      _startAnalysisForUrl(raw || `https://www.youtube.com/watch?v=${vid}`);
    }
  }

  function _startAnalysisForUrl(url) {
    _showAnalysisBanner("提交分析中…", 0);
    fetch("/api/process/youtube", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    }).then(r => r.json().then(d => ({ ok: r.ok, d }))).then(({ ok, d }) => {
      if (!ok) {
        _showAnalysisBanner(`提交失敗：${(d && d.detail) || "未知錯誤"}`, null, /*error=*/true);
        return;
      }
      if (d.status === "done" && d.result_hash) {
        _showAnalysisBanner("已有現成分析結果，切換中…", 100);
        setTimeout(() => {
          window.location.href = `/player?hash=${encodeURIComponent(d.result_hash)}`;
        }, 600);
        return;
      }
      if (d.job_id) _pollAnalysisJob(d.job_id);
    }).catch(e => {
      _showAnalysisBanner(`提交失敗：${e.message}`, null, true);
    });
  }

  function _pollAnalysisJob(jobId) {
    let maxProgress = 0;
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`/api/process/status/${jobId}`);
        if (!res.ok) { clearInterval(timer); return; }
        const d = await res.json();
        maxProgress = Math.max(maxProgress, d.progress || 0);
        const stage = (d.stage && d.status === "processing") ? d.stage : (d.status || "");
        _showAnalysisBanner(stage, maxProgress);
        if (d.status === "done" && d.result_hash) {
          clearInterval(timer);
          _showAnalysisBannerDone(d.result_hash);
        } else if (d.status === "error") {
          clearInterval(timer);
          _showAnalysisBanner(`分析失敗：${d.error || "未知錯誤"}`, null, true);
        }
      } catch {}
    }, 2000);
  }

  function _showAnalysisBanner(text, pct, isError = false) {
    let el = document.getElementById("ytAnalysisBanner");
    if (!el) {
      el = document.createElement("div");
      el.id = "ytAnalysisBanner";
      el.className = "yt-analysis-banner";
      el.innerHTML = `
        <div class="yt-ab-row">
          <span class="yt-ab-text"></span>
          <span class="yt-ab-pct"></span>
          <button class="yt-ab-close" aria-label="關閉">&times;</button>
        </div>
        <div class="yt-ab-bar"><div class="yt-ab-fill"></div></div>
      `;
      document.body.appendChild(el);
      el.querySelector(".yt-ab-close").addEventListener("click", () => el.remove());
    }
    el.classList.toggle("is-error", !!isError);
    el.querySelector(".yt-ab-text").textContent = text || "";
    el.querySelector(".yt-ab-pct").textContent = pct == null ? "" : `${pct}%`;
    const fill = el.querySelector(".yt-ab-fill");
    if (pct != null) fill.style.width = `${Math.max(0, Math.min(100, pct))}%`;
  }

  function _showAnalysisBannerDone(resultHash) {
    const el = document.getElementById("ytAnalysisBanner");
    if (!el) return;
    el.querySelector(".yt-ab-text").textContent = "分析完成！";
    el.querySelector(".yt-ab-pct").textContent = "100%";
    el.querySelector(".yt-ab-fill").style.width = "100%";
    // Replace close with a "view chords" CTA
    const row = el.querySelector(".yt-ab-row");
    row.querySelector(".yt-ab-close")?.remove();
    const btn = document.createElement("button");
    btn.className = "yt-ab-cta";
    btn.textContent = "看和弦";
    btn.addEventListener("click", () => {
      // Flag fresh so the destination player shows the "旋律擷取中" banner.
      try { sessionStorage.setItem("livechord_fresh_hash", `${resultHash}|${Date.now()}`); } catch {}
      window.location.href = `/player?hash=${encodeURIComponent(resultHash)}`;
    });
    row.appendChild(btn);
  }

  function _onYtFbFileSubmit(panel) {
    const input = panel.querySelector("#ytFbFile");
    const file = input && input.files && input.files[0];
    if (!file) { showToast("請先選擇音檔", 3000); return; }
    // Tear down YT so the audio element takes over playback
    if (_ytPlayer) { try { _ytPlayer.destroy(); } catch {} _ytPlayer = null; }
    if (_ytSyncTimer) { clearInterval(_ytSyncTimer); _ytSyncTimer = null; }
    const ytContainer = document.getElementById("ytEmbedContainer");
    if (ytContainer) ytContainer.style.display = "none";
    _ytSyncDisabled = false;
    const objUrl = URL.createObjectURL(file);
    audio.src = objUrl;
    _usingLocalFile = true;
    audio.play().catch(() => {});
    panel.remove();
    showToast(`已載入本地音檔：${file.name}`, 3000);
    // Verify audio duration vs chord duration — same 10% gate as YT.
    audio.addEventListener("loadedmetadata", function onMeta() {
      audio.removeEventListener("loadedmetadata", onMeta);
      if (!_chordDuration || _chordDuration < 30) return;
      const d = audio.duration;
      if (!d || isNaN(d)) return;
      const ratio = Math.abs(d - _chordDuration) / _chordDuration;
      if (ratio > 0.10) {
        showToast(`本地檔長度與和弦差 ${Math.round(ratio*100)}%，播放可能不同步`, 6000);
      }
    });
  }

  function _initYouTubeEmbed(videoId) {
    const container = document.getElementById("ytEmbedContainer");
    if (!container) return;
    // Respect persisted hidden state (user closed the PiP last time) — YT still boots
    // and plays audio; toolbar shows "重新顯示 YouTube" button so they can re-open.
    const pipState = _loadYtPipState();
    container.style.display = pipState.hidden ? "none" : "";
    _applyYtPipState();
    _updateYtReopenBtn();

    // Show a loading overlay until onReady clears it — iframe_api + player boot can take 5–15s
    _setLoadingState(true, "載入 YouTube 播放器…", "首次載入約 5–15 秒");

    // Load YouTube IFrame API
    if (!window.YT) {
      const tag = document.createElement("script");
      tag.src = "https://www.youtube.com/iframe_api";
      document.head.appendChild(tag);
      window.onYouTubeIframeAPIReady = () => _createYTPlayer(videoId);
    } else {
      _createYTPlayer(videoId);
    }
  }

  // Task 1: compare YT video length against chord JSON length; show banner + gate sync on mismatch.
  // Retries up to 3× because getDuration() may return 0 during buffering.
  function _checkYtDuration(attempt = 0) {
    if (!_ytPlayer || typeof _ytPlayer.getDuration !== "function") return;
    if (!_chordDuration || _chordDuration < 30) return;  // skip check if unknown or too short
    let dYt = 0;
    try { dYt = _ytPlayer.getDuration() || 0; } catch { dYt = 0; }
    if (!dYt || isNaN(dYt)) {
      if (attempt < 3) setTimeout(() => _checkYtDuration(attempt + 1), 500);
      return;
    }
    const ratio = Math.abs(dYt - _chordDuration) / _chordDuration;
    if (ratio > 0.10) {
      _ytSyncDisabled = true;
      _ytVerifiedOk = false;
      _showDesyncBanner(dYt, _chordDuration, ratio);
    } else if (ratio <= 0.05) {
      _ytVerifiedOk = true;
      _maybeLearnLibraryMapping();
    }
  }

  // Task 2: auto-learn YT URL → library hash mapping when duration matches tightly.
  // Single-shot per (hash, videoId) pair, guarded in sessionStorage.
  function _maybeLearnLibraryMapping() {
    if (!hashMode || !_ytVerifiedOk || !_ytPlayer) return;
    let videoUrl = "";
    try { videoUrl = _ytPlayer.getVideoUrl ? _ytPlayer.getVideoUrl() : ""; } catch { videoUrl = ""; }
    const m = videoUrl.match(/(?:v=|youtu\.be\/|\/shorts\/)([A-Za-z0-9_-]{11})/);
    const vid = m ? m[1] : "";
    if (!vid) return;
    const canonical = `https://www.youtube.com/watch?v=${vid}`;
    const guardKey = `ytlib:${hashMode}:${vid}`;
    try { if (sessionStorage.getItem(guardKey)) return; } catch {}
    fetch("/api/process/yt-library-learn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ youtube_url: canonical, library_hash: hashMode }),
    }).then(r => {
      if (r.ok) {
        try { sessionStorage.setItem(guardKey, "1"); } catch {}
      }
    }).catch(() => {});
  }

  function _showDesyncBanner(dYt, dChord, ratio) {
    // Remove any existing banner first
    const old = document.getElementById("ytDesyncBanner");
    if (old) old.remove();
    const pct = Math.round(ratio * 100);
    const el = document.createElement("div");
    el.id = "ytDesyncBanner";
    el.className = "yt-desync-banner";
    el.innerHTML = `
      <span>⚠ YouTube 版本長度 ${formatTime(dYt)} 與和弦 ${formatTime(dChord)} 差 ${pct}%，同步已停用</span>
      <button id="ytDesyncFallback" class="yt-desync-btn">換版本/上傳音檔</button>
      <button id="ytDesyncClose" class="yt-desync-close" aria-label="關閉">&times;</button>
    `;
    document.body.appendChild(el);
    document.getElementById("ytDesyncClose")?.addEventListener("click", () => el.remove());
    document.getElementById("ytDesyncFallback")?.addEventListener("click", () => {
      el.remove();
      if (typeof _showYtFallbackPanel === "function") _showYtFallbackPanel();
    });
  }

  function _createYTPlayer(videoId) {
    _ytPlayer = new YT.Player("ytEmbed", {
      videoId: videoId,
      playerVars: { autoplay: 1, modestbranding: 1, rel: 0 },
      events: {
        onReady: () => {
          _setLoadingState(false);
          showToast("YouTube 播放器就緒", 2000);
          btnPlay.classList.add("is-playing");
          try {
            const v = (volumeSlider && volumeSlider.value != null) ? parseFloat(volumeSlider.value) : audio.volume;
            _ytPlayer.setVolume(Math.max(0, Math.min(1, v)) * 100);
            if (audio.muted) _ytPlayer.mute(); else _ytPlayer.unMute();
            const s = SPEEDS[speedIdx];
            if (s !== 1 && typeof _ytPlayer.setPlaybackRate === "function") _ytPlayer.setPlaybackRate(s);
          } catch (e) {}
          _startYTSync();
          // Task 1: verify YT length matches chord length (with retry for buffering).
          setTimeout(() => _checkYtDuration(0), 600);
          // PiP toolbar button reflects current player existence.
          _updateYtReopenBtn();
          // Sync YT internal layout to the persisted PiP size (setSize triggers
          // re-layout that CSS %-sizing alone doesn't always flow through).
          try {
            const c = document.getElementById("ytEmbedContainer");
            if (c && _ytPlayer && typeof _ytPlayer.setSize === "function") {
              const r = c.getBoundingClientRect();
              _ytPlayer.setSize(Math.round(r.width), Math.round(r.height));
            }
          } catch {}
        },
        onStateChange: (e) => {
          // 0=ended, 1=playing, 2=paused, 3=buffering, 5=cued
          if (e.data === 1) {
            btnPlay.classList.add("is-playing");
            // Re-arm the sync timer in case it was cleared (close-button, destroy, etc.)
            _startYTSync();
          }
          else if (e.data === 2) btnPlay.classList.remove("is-playing");
          else if (e.data === 0) {
            if (loopMode === "single") {
              try { _ytPlayer.seekTo(0, true); _ytPlayer.playVideo(); } catch (err) {}
            } else if (loopMode === "favorites" && favTracks.length > 0) {
              _navNext();
            } else {
              btnPlay.classList.remove("is-playing");
            }
          }
        },
        onError: (e) => {
          _ytPlayer = null;
          const container = document.getElementById("ytEmbedContainer");
          if (container) container.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-dim);font-size:13px">此影片無法嵌入播放<br>請在 YouTube 開啟播放</div>';
          showToast("按 ▶ 播放鍵載入本地音檔", 8000);
          _updateYtReopenBtn();
        }
      }
    });
  }

  function _startYTSync() {
    if (_ytSyncTimer) clearInterval(_ytSyncTimer);
    _ytSyncTimer = setInterval(() => {
      if (!_ytPlayer || typeof _ytPlayer.getCurrentTime !== "function") return;
      try {
        const state = _ytPlayer.getPlayerState();
        // -1=unstarted, 0=ended, 1=playing, 2=paused, 3=buffering, 5=cued
        // Always refresh time/progress UI for states 1/2/3 so paused + buffering show the real position;
        // skip heavy chord/instrument animation unless actually playing.
        if (state === -1) return;
        let t = _ytPlayer.getCurrentTime();
        if (abState === "active" && abA != null && abB != null && t >= abB) {
          _ytPlayer.seekTo(abA, true);
          t = abA;
        }

        // Always keep the time display + progress bar in sync with the real YT currentTime,
        // including when paused / buffering, so a seek while paused visibly lands.
        const dur = _ytPlayer.getDuration();
        timeCurrent.textContent = formatTime(t);
        if (dur > 0) {
          timeDuration.textContent = formatTime(dur);
          const pct = (t / dur) * 100;
          if (topProgressFill) topProgressFill.style.width = pct + "%";
        }

        // Chord/instrument animation only while playing (state 1) to avoid burning cycles when paused
        if (state !== 1) return;
        // Task 1: gate chord/instrument updates when YT length ≠ chord length (time/progress still update above).
        if (_ytSyncDisabled) return;
        if (t > 0) {
          updateActiveChord(t);
          _updateBeatDots(t);
          _updateKeyDisplay(t);
          if (activeTab === "piano") {
            update88Piano(t);
            drawWaterfall(t);
          } else {
            const _inst = InstrumentRegistry.get(activeTab);
            if (_inst) _inst.update(t);
          }
        }
      } catch (e) {
        // Expose for post-mortem debugging: open DevTools on the player page,
        // check window.__lcYtError for the last exception.
        window.__lcYtError = { msg: e && e.message, when: Date.now() };
      }
    }, 50); // 20 fps for smooth animation
  }

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

  // Beta YT mode never fires `audio` play events — hook note scheduling to
  // whatever surface is actually playing. scheduleNotes also lazy-inits
  // aiSynth so MIDI / Mix modes work without having to have hit ▶ on <audio>.
  setInterval(() => {
      const ytMode = _ytActive();
      const playing = ytMode
          ? (_ytPlayer.getPlayerState && _ytPlayer.getPlayerState() === 1)
          : !audio.paused;
      if (!playing) return;
      if (!aiSynth.ctx) {
          try { aiSynth.init(); } catch {}
      }
      if (aiSynth.ctx && aiSynth.ctx.state === "suspended") {
          try { aiSynth.ctx.resume(); } catch {}
      }
      const t = ytMode
          ? (_ytPlayer.getCurrentTime ? _ytPlayer.getCurrentTime() : 0)
          : audio.currentTime;
      scheduleNotes(t);
  }, 50);

  // Audio Mode UI Bindings (Music -> MIDI -> Mix)
  const btnAudioMode = document.getElementById("btnAudioMode");
  const crossfaderContainer = document.getElementById("crossfaderContainer");
  const crossfaderVol = document.getElementById("crossfaderVol");
  
  // Persisted across page loads so user's choice sticks.
  let audioMode = 0; // 0: Music, 1: MIDI, 2: Mix
  try {
    const _saved = parseInt(localStorage.getItem("livechord_audio_mode"), 10);
    if ([0, 1, 2].includes(_saved)) audioMode = _saved;
  } catch {}

  // Route source-audio volume to whichever surface is actually playing:
  // YT iframe in beta hash mode, HTMLAudioElement in 8800 path mode.
  function _setSourceVolume(vol01) {
      if (_ytActive() && typeof _ytPlayer.setVolume === "function") {
          try { _ytPlayer.setVolume(Math.max(0, Math.min(1, vol01)) * 100); } catch {}
      }
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
          aiSynth.volLeft = 0;
          aiSynth.volRight = 0;
          if (crossfaderContainer) crossfaderContainer.style.display = "none";
      } else if (audioMode === 1) {
          btnAudioMode.innerHTML = "🎹 MIDI";
          btnAudioMode.style.color = "#ff9800";
          btnAudioMode.style.borderColor = "#ff9800";
          _setSourceVolume(0);
          aiSynth.volLeft = 1.0;
          aiSynth.volRight = 1.0;
          if (crossfaderContainer) crossfaderContainer.style.display = "none";
      } else {
          btnAudioMode.innerHTML = "🎧 Mix";
          btnAudioMode.style.color = "#4caf50";
          btnAudioMode.style.borderColor = "#4caf50";
          if (crossfaderContainer) crossfaderContainer.style.display = "flex";

          const mixVal = crossfaderVol ? parseFloat(crossfaderVol.value) : 0.5;
          _setSourceVolume(baseVol * (1 - mixVal));
          aiSynth.volLeft = mixVal;
          aiSynth.volRight = mixVal;
      }

      // The hand mute filtering is still robustly done in aiSynth.playNote
  }

  if (btnAudioMode) {
      btnAudioMode.addEventListener("click", () => {
          audioMode = (audioMode + 1) % 3;
          try { localStorage.setItem("livechord_audio_mode", String(audioMode)); } catch {}
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
          showToast("已紀錄此負面特徵 (Negative Sample)，將用於未來訓練！");
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
        { type: "dialogue", label: "對白 (Dialogue)" },
        { type: "intro", label: "前奏 (Intro)" },
        { type: "verse", label: "主歌 (Verse)" },
        { type: "pre_chorus", label: "導歌 (PreChorus)" },
        { type: "chorus", label: "副歌 (Chorus)" },
        { type: "instrumental", label: "間奏 (Interlude)" },
        { type: "bridge", label: "橋段 (Bridge)" },
        { type: "outro", label: "尾奏 (Outro)" }
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
      
      function renderTypeList(titleText, callback) {
          menu.innerHTML = "";
          const t = document.createElement("div");
          t.className = "title";
          t.innerHTML = `<span>${titleText}</span><span style="opacity:0.6;float:right;cursor:pointer">🔙 回上層</span>`;
          t.querySelector("span:last-child").onclick = (ev) => { ev.stopPropagation(); renderMain(); };
          menu.appendChild(t);
          
          let originalType = activeSec ? activeSec.type : null;
          types.forEach(tObj => {
              const item = document.createElement("div");
              item.className = "rv-section-menu-item";
              const isSelected = (titleText.includes("修改") && originalType === tObj.type);
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
          t.innerHTML = `<span>\u2702 切分 ${chordName} (${totalBeats} 拍)</span><span style="opacity:0.6;float:right;cursor:pointer">\uD83D\uDD19 回上層</span>`;
          t.querySelector("span:last-child").onclick = (ev) => { ev.stopPropagation(); renderMain(); };
          menu.appendChild(t);

          const options = CC.generateSplitOptions(totalBeats);

          // Group 1: Just split
          const h1 = document.createElement("div");
          h1.className = "rv-section-menu-label";
          h1.textContent = "僅切割:";
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
                  showToast(`已切分 ${l}+${r} 拍`, 2000);
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
          h2.textContent = "切割並分段:";
          menu.appendChild(h2);

          options.forEach(([l, r]) => {
              const item = document.createElement("div");
              item.className = "rv-section-menu-item";
              item.innerHTML = `<span style="flex:1">${l}+${r} \u2192 選段落...</span>`;
              item.onclick = (ev) => {
                  ev.stopPropagation();
                  renderTypeList(`切分 ${l}+${r} \u2192 新段落`, async (newType) => {
                      CC.backup(chordData);
                      const splitTime = CC.splitChord(chordData, chordIdx, l, r, currentSecPerBeat);
                      await window.saveSectionFeedback(splitTime, newType);
                      if (typeof _corrRebuild === "function") _corrRebuild();
                      else if (typeof window._chordRebuild === "function") window._chordRebuild();
                      showToast(`已切分 ${l}+${r} 拍，並建立段落`, 2500);
                  });
              };
              menu.appendChild(item);
          });
          _adjustBounds();
      }

      function renderMain() {
          menu.innerHTML = "";
          const t1 = document.createElement("div");
          t1.className = "title";
          t1.textContent = activeSec ? `目前段落: ${activeSec.labelZh || activeSec.type}` : `樂句模型標籤`;
          menu.appendChild(t1);

          const renameItem = document.createElement("div");
          renameItem.className = "rv-section-menu-item";
          renameItem.innerHTML = `<span style="flex:1">📝 修改本段名稱...</span>`;
          renameItem.onclick = (ev) => {
              ev.stopPropagation();
              let exactTime = activeSec ? activeSec.start : chordTime;
              renderTypeList("修改名稱", async (newType) => {
                  await window.saveSectionFeedback(exactTime, newType);
              });
          };
          menu.appendChild(renameItem);

          if (chordTime !== null && !isBoundary) {
              const splitItem = document.createElement("div");
              splitItem.className = "rv-section-menu-item";
              splitItem.innerHTML = `<span style="flex:1">✂️ 從此和弦切出新段...</span>`;
              splitItem.onclick = (ev) => {
                  ev.stopPropagation();
                  renderTypeList("切出新段落", async (newType) => {
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
                  const durSec = ci < chords.length - 1
                      ? chords[ci + 1].time - chords[ci].time
                      : (chords[ci].end ? chords[ci].end - chords[ci].time : 2.0);
                  const beats = Math.round(durSec / currentSecPerBeat);
                  if (beats >= 4) {
                      const csItem = document.createElement("div");
                      csItem.className = "rv-section-menu-item";
                      csItem.style.color = "#ff9800";
                      csItem.innerHTML = `<span style="flex:1">\u2702 切分和弦 (${beats} 拍)...</span>`;
                      csItem.onclick = (ev) => {
                          ev.stopPropagation();
                          renderSplitOptions(ci, chords[ci].chord, beats);
                      };
                      menu.appendChild(csItem);
                  }
              }
          }

          if (activeSec) {
              const delItem = document.createElement("div");
              delItem.className = "rv-section-menu-item";
              delItem.style.color = "#f44";
              delItem.innerHTML = `<span style="flex:1">❌ 移除本段 (向上合併)</span>`;
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

      if (isBoundary) {
          // Fast-path: directly show the 8 types + 1 delete button
          menu.innerHTML = "";
          const t1 = document.createElement("div");
          t1.className = "title";
          t1.textContent = activeSec ? `📝 修改目前段落 (${activeSec.type})` : `修改樂句標籤`;
          menu.appendChild(t1);
          
          let originalType = activeSec ? activeSec.type : null;
          types.forEach(tObj => {
              const item = document.createElement("div");
              item.className = "rv-section-menu-item";
              if (originalType === tObj.type) {
                  item.style.fontWeight = "bold";
                  item.style.color = "#4caf50";
              }
              item.innerHTML = `<span style="flex:1">${tObj.label}</span>${originalType === tObj.type ? "✓" : ""}`;
              item.onclick = async (ev) => {
                  ev.stopPropagation();
                  menu.remove();
                  let exactTime = activeSec ? activeSec.start : chordTime;
                  await window.saveSectionFeedback(exactTime, tObj.type);
              };
              menu.appendChild(item);
          });
          
          if (activeSec) {
              const delItem = document.createElement("div");
              delItem.className = "rv-section-menu-item";
              delItem.style.color = "#f44";
              delItem.innerHTML = `<span style="flex:1">❌ 移除分界點 (向上合併)</span>`;
              delItem.onclick = async (ev) => {
                  ev.stopPropagation();
                  menu.remove();
                  await window.deleteSectionBoundary(activeSec.start);
              };
              menu.appendChild(delItem);
          }
          _adjustBounds();
      } else {
          renderMain();
      }
      
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
          showToast("這是第一段，無法向上合併！");
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
              showToast("正在儲存人工修正...");
              let res = await fetch('/api/ai/sections/feedback', {
                  method: 'POST',
                  headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify(body)
              });
              if (res.ok) {
                  showToast("✅ 修正成功！重新載入...");
                  _loadSections(path);
              } else {
                  showToast("❌ 修正失敗（伺服器回應異常）", true);
              }
          } catch(e) {
              showToast("❌ 修正失敗：" + e.message, true);
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
    const btnBugSubmit = $("#btnBugSubmit");
    const btnBugCancel = $("#btnBugCancel");

    let _betaRating = 0;
    let _betaMode = false;

    // Check deployment mode and wire up beta UI
    fetch("/api/config/public").then(r => r.json()).then(cfg => {
      if (cfg.deployment_mode !== "beta") return;
      _betaMode = true;

      // Analytics: track page view
      API.trackEvent("page_view", { page: "player", song_hash: trackPath });

      // Analytics: track song play
      audio.addEventListener("play", () => {
        const title = songTitle ? songTitle.textContent : "";
        API.trackEvent("song_play", { song_hash: trackPath, song_title: title });
      }, { once: true }); // only first play per session

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
        x.innerHTML = v <= _betaRating ? "&#x2605;" : "&#x2606;";
      });
    }

    // Submit rating
    if (btnSubmit) {
      btnSubmit.addEventListener("click", async () => {
        if (!_betaRating) { showToast("請先選擇星等"); return; }
        try {
          const title = songTitle ? songTitle.textContent : "";
          await API.submitRating(trackPath, _betaRating, betaComment.value.trim(), title);
          showToast(`已評價 ${"★".repeat(_betaRating)}${"☆".repeat(5 - _betaRating)}`);
          if (betaPopup) betaPopup.style.display = "none";
        } catch (e) { showToast("評價送出失敗: " + e.message); }
      });
    }
    if (btnCancel) {
      btnCancel.addEventListener("click", () => {
        if (betaPopup) betaPopup.style.display = "none";
      });
    }

    // Bug report — close any open toolbar popup first so the bug modal isn't
    // sandwiched under a leftover Tools / AI teaching popup.
    if (btnBug) {
      btnBug.addEventListener("click", () => {
        document.querySelectorAll(".tb-item.open").forEach(i => i.classList.remove("open"));
        if (bugDialog) bugDialog.style.display = "flex";
      });
    }
    if (btnBugSubmit) {
      btnBugSubmit.addEventListener("click", async () => {
        const desc = bugDesc ? bugDesc.value.trim() : "";
        if (!desc) { showToast("請描述問題"); return; }
        try {
          const cat = bugCat ? bugCat.value : "other";
          const info = navigator.userAgent;
          await API.submitBug(cat, desc, window.location.href, info);
          showToast("感謝回報！");
          if (bugDialog) bugDialog.style.display = "none";
          if (bugDesc) bugDesc.value = "";
        } catch (e) { showToast("回報送出失敗: " + e.message); }
      });
    }
    if (btnBugCancel) {
      btnBugCancel.addEventListener("click", () => {
        if (bugDialog) bugDialog.style.display = "none";
      });
    }
  })();

})();

// 移調工具函式、簡譜函式 moved to utils.js
