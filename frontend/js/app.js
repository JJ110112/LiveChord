/** LiveChord 首頁 — 瀏覽、搜尋、最愛、最近播放 */

(function () {
  function _t(k, v) { return (window.LiveChordI18n && window.LiveChordI18n.t) ? window.LiveChordI18n.t(k, v) : k; }

  // ---- state ----
  let currentPath = localStorage.getItem("livechord_home_path") || "";
  let currentTab = localStorage.getItem("livechord_home_tab") || "recent";
  let searchTimer = null;
  let _betaActiveAnalysis = null;
  const ACTIVE_UPLOAD_KEY = "livechord_active_upload_job";
  const ACTIVE_UPLOAD_TTL_MS = 24 * 60 * 60 * 1000;

  // ---- DOM refs ----
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  const searchInput = $("#searchInput");
  const searchResults = $("#searchResults");
  const browseGrid = $("#browseGrid");
  const trackList = $("#trackList");
  const breadcrumb = $("#breadcrumb");
  const loading = $("#loading");
  // showToast, escapeHtml moved to utils.js

  function showLoading(show) {
    loading.style.display = show ? "" : "none";
  }

  function goPlayer(path, hash) {
    if (hash) {
      window.location.href = `/player?hash=${encodeURIComponent(hash)}`;
    } else {
      window.location.href = `/player?path=${encodeURIComponent(path)}&autoplay=1`;
    }
  }

  function _hideAddSongPanelOnly() {
    const p = $("#betaFabPanel");
    const backdrop = $("#betaFabBackdrop");
    if (p) {
      p.classList.remove("open");
      p.classList.remove("file-only");
      p.classList.remove("analyzing");
    }
    if (backdrop) backdrop.classList.remove("open");
  }

  function _fileTitle(file) {
    return ((file && file.name) || "").replace(/\.[^.]+$/, "") || "Untitled";
  }

  function _uploadBlobKey(jobId) {
    return jobId ? `upload_job_${jobId}` : "";
  }

  function _persistBetaActiveAnalysis() {
    if (!_betaActiveAnalysis || !_betaActiveAnalysis.jobId) return;
    try {
      _betaActiveAnalysis.updatedAt = Date.now();
      localStorage.setItem(ACTIVE_UPLOAD_KEY, JSON.stringify(_betaActiveAnalysis));
    } catch {}
  }

  function _forgetPersistedBetaAnalysis() {
    try { localStorage.removeItem(ACTIVE_UPLOAD_KEY); } catch {}
  }

  function _readPersistedBetaAnalysis() {
    try {
      const raw = localStorage.getItem(ACTIVE_UPLOAD_KEY);
      if (!raw) return null;
      const saved = JSON.parse(raw);
      if (!saved || !saved.jobId) return null;
      const updatedAt = Number(saved.updatedAt || saved.startedAt || 0);
      if (updatedAt && Date.now() - updatedAt > ACTIVE_UPLOAD_TTL_MS) {
        _forgetPersistedBetaAnalysis();
        return null;
      }
      return saved;
    } catch {
      _forgetPersistedBetaAnalysis();
      return null;
    }
  }

  function _ensureActiveAnalysisVisible() {
    setTimeout(() => {
      const row = document.querySelector(".local-track-item.lt-analyzing");
      if (!row) return;
      const rect = row.getBoundingClientRect();
      if (rect.top >= 0 && rect.bottom <= window.innerHeight) return;
      row.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 60);
  }

  function _resumeBetaActiveAnalysis() {
    const saved = _readPersistedBetaAnalysis();
    if (!saved) return;
    if (_betaActiveAnalysis && _betaActiveAnalysis.jobId
        && _betaActiveAnalysis.jobId !== saved.jobId) return;
    _betaActiveAnalysis = {
      ...saved,
      statusText: saved.statusText || _t("home.progress.queued_analyzing"),
    };
    if (_betaActiveAnalysis.localId) {
      _currentAnalyzingLocalId = _betaActiveAnalysis.localId;
    }
    _renderLocalTracks();
    _ensureActiveAnalysisVisible();
    if (_betaActiveAnalysis.jobId) {
      if (_betaActivePollTimer) {
        clearInterval(_betaActivePollTimer);
        _betaActivePollTimer = null;
      }
      _betaPollJob(_betaActiveAnalysis.jobId, null, null, null, { immediate: true });
    }
  }

  function _beginBetaActiveAnalysis(file, localId) {
    _betaActiveAnalysis = {
      jobId: "",
      localId: localId || "",
      title: _fileTitle(file),
      sizeMb: file && file.size ? (file.size / 1048576).toFixed(1) : "",
      progress: 10,
      status: "uploading",
      statusText: _t("home.progress.uploading"),
      uploadBlobKey: "",
      startedAt: Date.now(),
      updatedAt: Date.now(),
    };
    _renderLocalTracks();
    _ensureActiveAnalysisVisible();
  }

  function _updateBetaActiveAnalysis(jobId, patch) {
    if (!_betaActiveAnalysis) return;
    if (jobId && _betaActiveAnalysis.jobId && _betaActiveAnalysis.jobId !== jobId) return;
    Object.assign(_betaActiveAnalysis, patch || {});
    if (jobId && !_betaActiveAnalysis.jobId) _betaActiveAnalysis.jobId = jobId;
    _persistBetaActiveAnalysis();
    _renderLocalTracks();
  }

  function _clearBetaActiveAnalysis(jobId, delayMs = 0) {
    const clear = () => {
      if (jobId && _betaActiveAnalysis && _betaActiveAnalysis.jobId && _betaActiveAnalysis.jobId !== jobId) return;
      _betaActiveAnalysis = null;
      _forgetPersistedBetaAnalysis();
      _renderLocalTracks();
    };
    if (delayMs > 0) setTimeout(clear, delayMs);
    else clear();
  }

  function getDifficultyHtml(item) {
    const uc = item.unique_chords || 0;
    if (!uc || uc <= 0) return "";
    let stars = 1;
    if (uc >= 15) stars = 4;
    else if (uc >= 9) stars = 3;
    else if (uc >= 5) stars = 2;
    const key = normalizeKeyForDisplay(item.chord_key || "");
    return ` <span class="difficulty" style="font-size:0.65em;opacity:0.6;margin-left:6px;white-space:nowrap;letter-spacing:-1px">${"⭐".repeat(stars)}${key ? `<span style="margin-left:4px;letter-spacing:normal">${key}</span>` : ""}</span>`;
  }

  // ---- beta mode: hide NAS-dependent sections for non-admin ----
  // Variable name predates Phase B/C; "user-facing" today means beta OR public.
  // Both modes share the same UI shape (no NAS browse for non-admin, search-bar
  // is the entry, beta-style add-song modal). _isBetaMode stays the central
  // "user-facing" flag; _isPublicMode lets callers distinguish when the auth
  // semantics matter (forced login in beta vs anonymous-allowed in public).
  let _isBetaNonAdmin = false;
  let _isBetaMode = false;
  let _isPublicMode = false;

  async function _checkBetaAccess() {
    try {
      const [cfgRes, adminRes] = await Promise.all([
        fetch("/api/config/public").then(r => r.json()),
        fetch("/api/auth/is_admin").then(r => r.json()),
      ]);
      _isPublicMode = cfgRes.deployment_mode === "public";
      _isBetaMode = cfgRes.deployment_mode === "beta" || _isPublicMode;
      // NAS browse section is personal-mode-only — the /api/browse endpoint
      // is gated by require_personal_mode and returns 404 in beta/public.
      // Admins in public mode were seeing the section render a 404 warning;
      // the NAS volume isn't even mounted on the VPS so there's nothing
      // useful to show. Hide unconditionally outside personal mode.
      if (_isBetaMode) {
        const secBrowse = $("#secBrowse");
        if (secBrowse) secBrowse.style.display = "none";
      }
      if (_isBetaMode) {
        // Modal close via × button or backdrop click. Wired for ANY beta/
        // public visitor (admin + anonymous + signed-in non-admin) — the
        // add-song modal is reachable from every entry point so its close
        // handler must be unconditional. Earlier this lived inside the
        // !is_admin branch; admins logged in on livechord.org found that
        // the × button did nothing on error and had to reload the page.
        const closeBtn = $("#betaFabClose");
        const backdrop = $("#betaFabBackdrop");
        const closeAddSongModal = () => {
          const keepAnalysisAlive = !!_betaActiveAnalysis;
          const p = $("#betaFabPanel");
          if (p) {
            p.classList.remove("open");
            p.classList.remove("file-only");
            p.classList.remove("analyzing");
          }
          if (backdrop) backdrop.classList.remove("open");
          // Reset progress rows so the next open starts clean (otherwise a
          // stale "Reading title... 100%" bar would still be visible).
          const up = $("#betaUploadProgress"); if (up) up.style.display = "none";
          const yt = $("#betaYtProgress"); if (yt) yt.style.display = "none";
          const tt = $("#betaProgressTitle"); if (tt) tt.textContent = "";
          // Cancel any in-flight analysis poll. Backend job keeps running —
          // result hash will still land via process_audit if the user
          // re-uploads the same file (file_hash dedupe). What we MUST
          // reset is the modal state so re-opening doesn't render a
          // stuck-disabled 分析 button + null _betaSelectedFile + hidden
          // file-info row, which is what the user hit when dismissing
          // mid-upload then clicking the per-track 分析 again.
          if (!keepAnalysisAlive) {
            if (_betaActivePollTimer) {
              clearInterval(_betaActivePollTimer);
              _betaActivePollTimer = null;
            }
            _betaSelectedFile = null;
            _currentAnalyzingLocalId = null;
          }
          const fi = $("#betaFileInfo"); if (fi) fi.style.display = "none";
          const ub = $("#betaUploadBtn"); if (ub) ub.disabled = false;
          const yb = $("#betaYtBtn"); if (yb) yb.disabled = false;
        };
        if (closeBtn) closeBtn.addEventListener("click", closeAddSongModal);
        if (backdrop) backdrop.addEventListener("click", closeAddSongModal);
      }
      // Public mode: split the homepage into TWO mutually exclusive views:
      //   - Logged-in  → dashboard only (recent / favorites / local-music).
      //     The marketing sections stay hidden; the user already chose to
      //     sign up, no need to re-pitch them.
      //   - Logged-out (incl. "Continue as guest") → full marketing landing
      //     (intro banner + hero + how-it-works + open-source). Header is
      //     hidden — clean sign-up funnel, no LC logo / search bar above
      //     the pitch. The hero CTA still works for guests (opens the
      //     upload modal directly instead of round-tripping /login).
      if (_isPublicMode) {
        const isLoggedIn = !!localStorage.getItem("livechord_token");
        const headerEl = document.querySelector("header.header");
        const intro = $("#secHomeIntro");
        const heroSec = $("#secHomeHero");
        const techSec = $("#secHomeTechCred");
        const hiwSec = $("#secHomeHowItWorks");
        const osSec = $("#secHomeOpenSource");
        const secBetaLocal = $("#secBetaLocalTracks");
        const secBetaRecent = $("#secBetaRecent");
        const secFavorites = $("#secFavorites");

        if (isLoggedIn) {
          // Logged-in dashboard: hide marketing, show user-data sections.
          if (headerEl) headerEl.style.display = "";
          if (intro) intro.style.display = "none";
          if (heroSec) heroSec.style.display = "none";
          if (techSec) techSec.style.display = "none";
          if (hiwSec) hiwSec.style.display = "none";
          if (osSec) osSec.style.display = "none";
          if (secBetaLocal) secBetaLocal.style.display = "";
          if (secBetaRecent) secBetaRecent.style.display = "";
          if (secFavorites) secFavorites.style.display = "";
        } else {
          // Marketing view (logged-out, incl. guest-acked): show landing
          // surfaces, hide user-data sections (they'd be empty for an
          // anonymous visitor and would bury the sign-up CTA below empty
          // placeholder cards). Top bar hidden too — clean funnel, /login
          // is reachable via the hero CTA.
          if (headerEl) headerEl.style.display = "none";
          if (intro) intro.style.display = "";
          if (heroSec) heroSec.style.display = "";
          if (techSec) techSec.style.display = "";
          if (hiwSec) hiwSec.style.display = "";
          if (osSec) osSec.style.display = "";
          if (secBetaLocal) secBetaLocal.style.display = "none";
          if (secBetaRecent) secBetaRecent.style.display = "none";
          if (secFavorites) secFavorites.style.display = "none";
        }
        // Hero "Get started for free" CTA — always /login when no token.
        // Reading livechord_guest_acked here was a UX trap: the flag is
        // sticky in localStorage, so a returning user (browser still has
        // the flag from any prior /login visit) could not reach /login
        // from the homepage at all — every CTA click opened the upload
        // modal instead of the sign-up page. The actual guest-upload path
        // is /login → "Continue as guest" → /?upload=1 (the link below
        // carries the query param), and #upload-auto-open below picks it
        // up so the modal still surfaces in one click for guests.
        const cta = $("#heroUploadBtn");
        if (cta && !cta._lcWired) {
          cta._lcWired = true;
          cta.addEventListener("click", () => {
            const tokenNow = !!localStorage.getItem("livechord_token");
            if (window.LiveChordAnalytics) {
              window.LiveChordAnalytics.track("hero_cta_click", {
                logged_in: tokenNow,
                destination: tokenNow ? "upload_modal" : "login",
                source: "homepage",
                page_path: location.pathname
              });
            }
            if (window.API && API.trackEvent) {
              API.trackEvent("hero_cta_click", {
                logged_in: tokenNow,
                destination: tokenNow ? "upload_modal" : "login",
                source: "homepage",
                page_path: location.pathname
              });
            }
            if (tokenNow) {
              const panel = $("#betaFabPanel");
              const backdrop = $("#betaFabBackdrop");
              if (panel) {
                panel.classList.add("open");
                panel.classList.remove("file-only");
              }
              if (backdrop) backdrop.classList.add("open");
            } else {
              window.location.href = "/login";
            }
          });
        }
        // Auto-open the upload modal when arriving with ?upload=1 — set by
        // /login's "Continue as guest" link. Guests then see the modal
        // immediately on landing instead of having to spot the CTA. Strip
        // the param after firing so a hard-reload doesn't re-trigger.
        try {
          const params = new URLSearchParams(window.location.search);
          if (params.get("upload") === "1") {
            const panel = $("#betaFabPanel");
            const backdrop = $("#betaFabBackdrop");
            if (panel) {
              panel.classList.add("open");
              panel.classList.remove("file-only");
            }
            if (backdrop) backdrop.classList.add("open");
            params.delete("upload");
            const cleanQs = params.toString();
            const cleanUrl = window.location.pathname +
              (cleanQs ? "?" + cleanQs : "") + window.location.hash;
            history.replaceState({}, "", cleanUrl);
          }
        } catch (_e) {}
      } else if (_isBetaMode) {
        // Beta-mode (legacy invite-only). Show local-music for everyone.
        const secBetaLocal = $("#secBetaLocalTracks");
        if (secBetaLocal) secBetaLocal.style.display = "";
      }
      if (_isBetaMode && !adminRes.is_admin) {
        _isBetaNonAdmin = true;
        // Hide remaining NAS-leaking section for non-admin users.
        // (secBrowse already hidden above for the admin path too.)
        const secRecent = $("#secRecent");
        if (secRecent) secRecent.style.display = "none";
        // Show beta sections (no standalone FAB — modal opens from search empty state)
        const secBetaRecent = $("#secBetaRecent");
        const secHistory = $("#secHistory");
        // In public mode the logged-in branch above already handled this;
        // in beta non-admin we need to surface the section regardless of
        // the marketing-vs-dashboard split (no marketing in beta).
        if (!_isPublicMode && secBetaRecent) secBetaRecent.style.display = "";
        if (secHistory) secHistory.style.display = "";
      } else if (_isBetaMode) {
        // Admin in beta mode: show history section alongside regular sections
        const secHistory = $("#secHistory");
        if (secHistory) secHistory.style.display = "";
      }
    } catch {}
  }

  // ---- beta upload logic (homepage) ----
  let _betaSelectedFile = null;
  const _betaPendingFiles = {};
  // Hoisted out of _betaPollJob so closeAddSongModal can stop the polling
  // when the user dismisses the modal mid-analysis. Without this the
  // interval keeps running, _betaSelectedFile=null at end of upload, and
  // a re-opened modal lands in a half-broken state (analyze button stuck
  // disabled, file-info row hidden).
  let _betaActivePollTimer = null;
  const POLL_MS = 2000;

  function _initBetaUpload() {
    const dropZone = $("#betaDropZone");
    const fileInput = $("#betaFileInput");
    if (!dropZone || !fileInput) return;

    dropZone.addEventListener("click", () => fileInput.click());
    dropZone.addEventListener("dragover", e => { e.preventDefault(); dropZone.classList.add("dragover"); });
    dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
    dropZone.addEventListener("drop", e => {
      e.preventDefault();
      dropZone.classList.remove("dragover");
      if (e.dataTransfer.files.length) _betaPickFile(e.dataTransfer.files[0]);
    });
    fileInput.addEventListener("change", () => {
      if (fileInput.files.length) _betaPickFile(fileInput.files[0]);
    });
  }

  // Public mode is fronted by Cloudflare which caps the request body
  // at 100 MB on the free plan — bigger files get a 413 from Cloudflare's
  // edge before uvicorn ever sees them, which surfaced to users as
  // "失敗 10%" with no real error message. Personal/beta mode hits the
  // backend directly with no Cloudflare in front, so 200 MB stays the
  // ceiling there.
  function _maxUploadBytes() {
    return _isPublicMode ? 100 * 1024 * 1024 : 200 * 1024 * 1024;
  }
  function _maxUploadMb() {
    return _isPublicMode ? 100 : 200;
  }
  function _alertFileTooBig(file) {
    const mb = (file.size / 1024 / 1024).toFixed(1);
    const limit = _maxUploadMb();
    // Useful hint: lossless FLAC at CD quality is roughly 30-50 MB per
    // 5-min track, so a 180 MB FLAC is usually a high-bitrate or
    // long-duration source. Suggest MP3 320 kbps (~12 MB / 5 min)
    // which still gives clean chord detection.
    alert(_t("home.alert.file_too_big_hint", { size: mb, limit: limit }));
  }

  function _betaPickFile(file) {
    if (file.size > _maxUploadBytes()) { _alertFileTooBig(file); return; }
    _betaSelectedFile = file;
    $("#betaFileName").textContent = file.name;
    $("#betaFileSize").textContent = `(${(file.size / 1024 / 1024).toFixed(1)} MB)`;
    $("#betaFileInfo").style.display = "flex";
  }

  // ─── Local tracks registry (multi-select + IndexedDB persist) ───
  // Persistent list of user-picked local audio files. Metadata in localStorage
  // (small, fast to read every render), blobs in IndexedDB keyed by track id
  // (blobs are MB-scale, don't fit in localStorage). Registry survives page
  // reloads, so "pick folder once → play many" UX without re-picking each time.
  const LOCAL_TRACKS_KEY = "livechord_local_tracks";
  let _currentAnalyzingLocalId = null;  // set before _betaStartUpload; read after done

  function _getLocalTracks() {
    try { return JSON.parse(localStorage.getItem(LOCAL_TRACKS_KEY) || "[]"); }
    catch { return []; }
  }
  function _saveLocalTracks(arr) {
    try { localStorage.setItem(LOCAL_TRACKS_KEY, JSON.stringify(arr)); } catch {}
  }
  async function _addLocalTrackEntry(file) {
    const tracks = _getLocalTracks();
    const dup = tracks.find(t =>
      t.name === file.name && t.size === file.size && t.lastModified === file.lastModified);
    if (dup) return dup;
    const id = `local_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    try { await audioDBStore(id, file); } catch {}
    const entry = {
      id,
      name: file.name,
      size: file.size,
      lastModified: file.lastModified || 0,
      analyzedHash: null,
      addedAt: Date.now(),
    };
    tracks.unshift(entry);
    _saveLocalTracks(tracks);
    return entry;
  }
  function _markLocalTrackAnalyzed(id, resultHash) {
    if (!id || !resultHash) return;
    const tracks = _getLocalTracks();
    const t = tracks.find(x => x.id === id);
    if (t) {
      t.analyzedHash = resultHash;
      _saveLocalTracks(tracks);
    }
  }
  async function _removeLocalTrack(id) {
    try { await audioDBDelete(id); } catch {}
    _saveLocalTracks(_getLocalTracks().filter(t => t.id !== id));
  }

  function _renderLocalTracks() {
    const section = $("#secBetaLocalTracks");
    const analyzedRow = $("#betaLocalAnalyzedRow");
    const pendingList = $("#betaLocalTrackList");
    if (!section || !analyzedRow || !pendingList) return;
    if (_betaActiveAnalysis) section.style.display = "";
    const tracks = _getLocalTracks();
    const analyzed = tracks.filter(t => t.analyzedHash);
    const activeLocalId = _betaActiveAnalysis && _betaActiveAnalysis.localId;
    const pending = tracks.filter(t => !t.analyzedHash && t.id !== activeLocalId);

    // Analyzed: horizontal grid-item cards (like 最近播放). Backend extracts
    // an embedded cover at upload time (process_queue.py _extract_cover) and
    // serves it from /api/process/cover/<hash>; render the same <img> the
    // recent-list uses so the card matches 最近播放 visually.
    analyzedRow.innerHTML = analyzed.map(t => {
      const safeName = escapeHtml(t.name.replace(/\.[^.]+$/, ""));
      const hash = escapeHtml(t.analyzedHash);
      return `
        <div class="grid-item local-analyzed-card" data-id="${escapeHtml(t.id)}" style="cursor:pointer; position:relative">
          <img class="cover" src="/api/process/cover/${hash}" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" alt="">
          <div class="cover-placeholder" style="display:none">&#x1F3B5;</div>
          <div class="info">
            <div class="title" title="${safeName}">${safeName}</div>
          </div>
          <button class="la-remove" data-action="la-remove" data-id="${escapeHtml(t.id)}" title="${_t("home.local.remove_btn")}" aria-label="${_t("home.local.remove_btn")}">&times;</button>
        </div>`;
    }).join("");
    analyzedRow.style.display = analyzed.length ? "" : "none";
    analyzedRow.querySelectorAll(".grid-item").forEach(el => {
      el.addEventListener("click", (e) => {
        if (e.target.closest("[data-action='la-remove']")) return;
        _onLocalTrackAction(el.dataset.id);
      });
    });
    analyzedRow.querySelectorAll("[data-action='la-remove']").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm(_t("home.confirm.remove_local"))) return;
        await _removeLocalTrack(btn.dataset.id);
        _renderLocalTracks();
      });
    });

    const activeHtml = _betaActiveAnalysis ? (() => {
      const a = _betaActiveAnalysis;
      const pct = Math.max(0, Math.min(100, Number(a.progress) || 0));
      const safeTitle = escapeHtml(a.title || "");
      const metaParts = [];
      metaParts.push(`<span class="lt-state lt-analyzing-state">${escapeHtml(a.statusText || _t("home.progress.queued_analyzing"))}</span>`);
      if (a.sizeMb) metaParts.push(`${escapeHtml(a.sizeMb)} ${_t("home.local.mb")}`);
      return `
        <div class="local-track-item lt-analyzing" data-job-id="${escapeHtml(a.jobId || "")}">
          <div class="lt-analysis-head">
            <div class="lt-info">
              <div class="lt-name" title="${safeTitle}">${safeTitle}</div>
              <div class="lt-meta">${metaParts.join(" · ")}</div>
            </div>
            <div class="lt-analysis-pct">${pct}%</div>
          </div>
          <div class="lt-progress-track" aria-hidden="true">
            <div class="lt-progress-fill" style="width:${pct}%"></div>
          </div>
        </div>`;
    })() : "";

    // Pending: current vertical list layout
    if (pending.length === 0 && analyzed.length === 0 && !activeHtml) {
      pendingList.innerHTML = `<div style="color:var(--text-dim); font-size:13px; padding:8px 0">
        ${_t("home.local.empty_hint")}
      </div>`;
      return;
    }
    const pendingHtml = pending.map(t => {
      const sizeMb = (t.size / 1048576).toFixed(1);
      const safeName = escapeHtml(t.name);
      return `
        <div class="local-track-item" data-id="${escapeHtml(t.id)}">
          <div class="lt-info">
            <div class="lt-name" title="${safeName}">${safeName}</div>
            <div class="lt-meta"><span class="lt-state lt-pending">${_t("home.local.pending")}</span> · ${sizeMb} ${_t("home.local.mb")}</div>
          </div>
          <button class="lt-action btn-small btn-accent" data-action="play-or-analyze" data-id="${escapeHtml(t.id)}">${_t("home.local.analyze")}</button>
          <button class="lt-remove" data-action="remove" data-id="${escapeHtml(t.id)}" title="${_t("home.local.remove_btn")}" aria-label="${_t("home.local.remove_btn")}">&times;</button>
        </div>`;
    }).join("");
    pendingList.innerHTML = activeHtml + pendingHtml;
    pendingList.querySelectorAll("[data-action='play-or-analyze']").forEach(btn => {
      btn.addEventListener("click", () => _onLocalTrackAction(btn.dataset.id));
    });
    pendingList.querySelectorAll("[data-action='remove']").forEach(btn => {
      btn.addEventListener("click", async () => {
        if (!confirm(_t("home.confirm.remove_local"))) return;
        await _removeLocalTrack(btn.dataset.id);
        _renderLocalTracks();
      });
    });
  }

  async function _onLocalTrackAction(id) {
    const t = _getLocalTracks().find(x => x.id === id);
    if (!t) return;
    if (t.analyzedHash) {
      // Copy local blob under the analyzed hash key so player's audioDBLoad
      // finds it (player reads by hash, not by local id).
      try {
        const blob = await audioDBLoad(id);
        if (blob) await audioDBStore(t.analyzedHash, blob);
      } catch {}
      window.location.href = `/player?hash=${encodeURIComponent(t.analyzedHash)}`;
      return;
    }
    // Not yet analyzed → hand off to existing upload modal flow.
    let blob;
    try { blob = await audioDBLoad(id); } catch {}
    if (!blob) {
      alert(_t("home.alert.local_blob_lost"));
      return;
    }
    const file = new File([blob], t.name, {
      type: blob.type || "audio/mpeg",
      lastModified: t.lastModified || Date.now(),
    });
    _betaPickFile(file);
    _currentAnalyzingLocalId = id;
    // Belt-and-suspenders: ensure analyze button is clickable on
    // re-open. closeAddSongModal already resets this, but if anything
    // ever calls _onLocalTrackAction without going through the close
    // path the button could still be disabled from a prior upload.
    const ub = $("#betaUploadBtn"); if (ub) ub.disabled = false;
    const panel = $("#betaFabPanel");
    const backdrop = $("#betaFabBackdrop");
    if (panel) { panel.classList.add("open"); panel.classList.add("file-only"); }
    if (backdrop) backdrop.classList.add("open");
  }

  // Homepage "+ 選取本機音檔" — multi-select; each file goes into the local
  // tracks list. List item click decides analyze vs play.
  function _initBetaLocalAudio() {
    const btn = $("#betaBrowseLocalBtn");
    const input = $("#betaLocalAudioInput");
    const section = $("#secBetaLocalTracks");
    if (!btn || !input || !section) return;
    // Public-mode anonymous visitors land in this code path because
    // _isBetaNonAdmin gets set whenever _isBetaMode (which is true for
    // any public deployment) AND !is_admin — but the local-music section
    // is logged-in-only. _checkBetaAccess hid it; do not un-hide for
    // anon visitors. Beta deployments always force login so they always
    // have a token, so this gate doesn't disturb beta UX.
    if (_isPublicMode && !localStorage.getItem("livechord_token")) return;
    section.style.display = "";
    _renderLocalTracks();
    btn.addEventListener("click", () => input.click());
    input.addEventListener("change", async () => {
      const files = Array.from(input.files || []);
      if (!files.length) return;
      const limit = _maxUploadBytes();
      const valid = files.filter(f => f.size <= limit);
      if (valid.length < files.length) {
        // Find the first oversize file so the alert can name its size.
        const big = files.find(f => f.size > limit);
        if (big) _alertFileTooBig(big);
      }
      const entries = [];
      for (const file of valid) {
        entries.push(await _addLocalTrackEntry(file));
      }
      _renderLocalTracks();
      input.value = "";  // re-pick same files allowed
      // Single-file shortcut: skip the explicit "click 分析" step and
      // hand off to _onLocalTrackAction (which opens the upload modal
      // and primes _betaSelectedFile), then immediately fire the upload
      // so the user lands on the progress bar without an extra click.
      // Multi-pick keeps the existing per-track analyze button so users
      // batch-add files and analyze them on their own schedule.
      if (valid.length === 1 && entries[0]) {
        const entry = entries[0];
        await _onLocalTrackAction(entry.id);
        // _onLocalTrackAction routes to the player immediately when the
        // entry already has analyzedHash (re-pick of an already-analyzed
        // file), so guard the auto-upload to the unanalyzed branch.
        if (!entry.analyzedHash && typeof window._betaStartUpload === "function") {
          window._betaStartUpload();
        }
      }
    });
  }

  window._betaStartUpload = async function() {
    if (!_betaSelectedFile) return;
    const selectedFile = _betaSelectedFile;
    const selectedLocalId = _currentAnalyzingLocalId;
    const btn = $("#betaUploadBtn");
    btn.disabled = true;
    const prog = $("#betaUploadProgress");
    const fill = $("#betaProgressFill");
    const text = $("#betaProgressText");
    const pct = $("#betaProgressPct");
    const titleEl = $("#betaProgressTitle");
    // Populate the progress-title with the song name (filename minus
    // extension) so the user can see what's being analyzed once the
    // .beta-file-info row is hidden by the .analyzing class. Pre-pic3
    // bug: only the rights-notice + spinner showed, no song identity.
    if (titleEl) {
      titleEl.textContent = (selectedFile.name || "").replace(/\.[^.]+$/, "");
    }
    _beginBetaActiveAnalysis(selectedFile, selectedLocalId);
    _hideAddSongPanelOnly();
    prog.style.display = "";
    // Hide drop-zone + YT URL row while analyzing — reduces modal clutter
    // and prevents a second concurrent submit mid-run.
    $("#betaFabPanel")?.classList.add("analyzing");
    fill.style.width = "10%";
    text.textContent = _t("home.progress.uploading");
    pct.textContent = "10%";

    try {
      if (window.LiveChordAnalytics) {
        window.LiveChordAnalytics.track("upload_start", {
          file_ext: ((selectedFile.name || "").split(".").pop() || "").toLowerCase(),
          file_size_mb: Math.round((selectedFile.size || 0) / 10485.76) / 100,
          source: "upload",
          is_demo: false
        });
      }
      if (window.API && API.trackEvent) {
        API.trackEvent("upload_start", {
          file_ext: ((selectedFile.name || "").split(".").pop() || "").toLowerCase(),
          file_size_mb: Math.round((selectedFile.size || 0) / 10485.76) / 100,
          source: "upload",
          is_demo: false
        });
      }
      const form = new FormData();
      form.append("file", selectedFile);
      const res = await fetch("/api/process/upload", { method: "POST", body: form });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || res.statusText);
      }
      const data = await res.json();
      if (window.API && API.trackEvent) {
        API.trackEvent("upload_queued", { job_id: data.job_id || "", source: "upload", is_demo: false });
      }
      const jobId = data.job_id || "";
      if (!jobId) throw new Error("Missing upload job id");
      const uploadBlobKey = _uploadBlobKey(jobId);
      await audioDBStore(uploadBlobKey, selectedFile);
      _betaPendingFiles[jobId] = selectedFile;
      fill.style.width = "30%";
      text.textContent = _t("home.progress.queued_analyzing");
      pct.textContent = "30%";
      _updateBetaActiveAnalysis(jobId, {
        jobId,
        progress: 30,
        status: "queued",
        statusText: _t("home.progress.queued_analyzing"),
        uploadBlobKey,
      });
      _betaPollJob(jobId, fill, text, pct, { immediate: true });
    } catch (e) {
      text.textContent = _t("home.progress.failed_prefix") + e.message;
      fill.style.width = "0%";
      btn.disabled = false;
      _updateBetaActiveAnalysis("", {
        progress: 0,
        status: "error",
        statusText: _t("home.progress.failed_prefix") + e.message,
      });
      _clearBetaActiveAnalysis("", 5000);
      _currentAnalyzingLocalId = null;
      // Unhide inputs so the user can retry with a different file/URL.
      $("#betaFabPanel")?.classList.remove("analyzing");
    }
    _betaSelectedFile = null;
    $("#betaFileInfo").style.display = "none";
    $("#betaFileInput").value = "";
  };

  function _betaPollJob(jobId, fill, statusText, pctText, opts = {}) {
    // Seed with the current visual width (already set by caller to 20–30%) so the
    // bar never jumps backwards when the first poll returns a lower backend value.
    let maxProgress = fill ? parseInt((fill.style.width || "0").replace("%", "")) || 0 : 0;
    // Cancel any prior poll so two analyses can't race their progress
    // updates against the same DOM elements.
    if (_betaActivePollTimer) { clearInterval(_betaActivePollTimer); _betaActivePollTimer = null; }
    let timer = null;
    const pollOnce = async () => {
      try {
        const res = await fetch(`/api/process/status/${jobId}`);
        if (!res.ok) { clearInterval(timer); _betaActivePollTimer = null; return; }
        const d = await res.json();
        maxProgress = Math.max(maxProgress, Number(d.progress) || 0);
        if (fill) fill.style.width = maxProgress + "%";
        if (pctText) pctText.textContent = maxProgress + "%";
        // 細分狀態標籤：有 stage 時以 stage 為準，否則 fallback 到 status
        const labels = {
          queued: _t("home.status.queued"),
          processing: _t("home.status.processing"),
          done: _t("home.status.done"),
          error: _t("home.status.error"),
        };
        let stageDisplay = d.stage;
        if (stageDisplay && typeof stageDisplay === "string"
            && stageDisplay.startsWith("home.job_stage.")) {
          stageDisplay = _t(stageDisplay);
        }
        let activeStatusText = (stageDisplay && d.status === "processing")
            ? stageDisplay
            : (labels[d.status] || d.status);
        if (statusText) {
          // Backend stages travel as i18n keys (home.job_stage.*) so the
          // same /api/process/status response renders correctly in en + zh-TW
          // without server-side locale negotiation. Older deploys may still
          // emit literal CJK strings — the prefix check passes those through
          // unchanged so a rolling backend/frontend update doesn't show keys.
          let stageDisplay = d.stage;
          if (stageDisplay && typeof stageDisplay === "string"
              && stageDisplay.startsWith("home.job_stage.")) {
            stageDisplay = _t(stageDisplay);
          }
          const txt = (stageDisplay && d.status === "processing")
              ? stageDisplay
              : (labels[d.status] || d.status);
          statusText.textContent = txt;
        }

        _updateBetaActiveAnalysis(jobId, {
          progress: maxProgress,
          status: d.status || "",
          statusText: activeStatusText,
        });

        if (d.status === "done" && d.result_hash) {
          clearInterval(timer);
          _betaActivePollTimer = null;
          _updateBetaActiveAnalysis(jobId, {
            progress: 100,
            status: "done",
            statusText: _t("home.status.done"),
          });
          const activeSnapshot = _betaActiveAnalysis && _betaActiveAnalysis.jobId === jobId
              ? { ..._betaActiveAnalysis }
              : {};
          const finishedLocalId = activeSnapshot.localId || _currentAnalyzingLocalId;
          // Store audio blob in IndexedDB for auto-play
          let pendingFile = _betaPendingFiles[jobId];
          if (!pendingFile && activeSnapshot.uploadBlobKey) {
            pendingFile = await audioDBLoad(activeSnapshot.uploadBlobKey);
          }
          if (!pendingFile && finishedLocalId) {
            pendingFile = await audioDBLoad(finishedLocalId);
          }
          if (pendingFile) {
            await audioDBStore(d.result_hash, pendingFile);
            delete _betaPendingFiles[jobId];
          }
          if (activeSnapshot.uploadBlobKey) {
            try { await audioDBDelete(activeSnapshot.uploadBlobKey); } catch {}
          }
          // Flag this hash as freshly-analyzed so the player can show a
          // "旋律擷取中" banner while the melody worker finishes in the background.
          try { sessionStorage.setItem("livechord_fresh_hash", `${d.result_hash}|${Date.now()}`); } catch {}
          if (window.LiveChordAnalytics) {
            window.LiveChordAnalytics.track("upload_success", {
              job_id: jobId,
              song_hash: d.result_hash,
              source: "upload",
              is_demo: false
            });
          }
          if (window.API && API.trackEvent) {
            API.trackEvent("upload_success", {
              job_id: jobId,
              song_hash: d.result_hash,
              title: d.title || "",
              source: "upload",
              is_demo: false
            });
          }
          // If this job came from a local-tracks-list entry, stamp the
          // analyzedHash back onto it so next click goes straight to play.
          if (finishedLocalId) {
            _markLocalTrackAnalyzed(finishedLocalId, d.result_hash);
            _currentAnalyzingLocalId = null;
          }
          _clearBetaActiveAnalysis(jobId);
          // Same for playlist videos — mark existing_hash so the card's list
          // shows ▶ 播放 next time user opens it.
          // Navigate to player
          setTimeout(() => {
            window.location.href = `/player?hash=${encodeURIComponent(d.result_hash)}`;
          }, 500);
        } else if (d.status === "error") {
          clearInterval(timer);
          _betaActivePollTimer = null;
          if (window.API && API.trackEvent) {
            API.trackEvent("upload_error", {
              job_id: jobId,
              error: d.error || "Unknown",
              source: "upload",
              is_demo: false
            });
          }
          if (statusText) statusText.textContent = _t("home.progress.failed_prefix") + (d.error || "Unknown");
          _updateBetaActiveAnalysis(jobId, {
            progress: maxProgress,
            status: "error",
            statusText: _t("home.progress.failed_prefix") + (d.error || "Unknown"),
          });
          _clearBetaActiveAnalysis(jobId, 5000);
          _currentAnalyzingLocalId = null;
          $("#betaUploadBtn") && ($("#betaUploadBtn").disabled = false);
          $("#betaYtBtn") && ($("#betaYtBtn").disabled = false);
          // Unhide inputs so the user can retry.
          $("#betaFabPanel")?.classList.remove("analyzing");
        }
      } catch (e) {}
    };
    timer = setInterval(pollOnce, POLL_MS);
    _betaActivePollTimer = timer;
    if (opts.immediate) pollOnce();
  }

  // ---- beta history ----
  function _buildCoverHtml(h) {
    const imgSrc = `/api/process/cover/${escapeHtml(h.result_hash)}`;
    return `<img class="cover" src="${imgSrc}" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" alt="">
      <div class="cover-placeholder" style="display:none">&#x1F3B5;</div>`;
  }

  function _buildHistoryDeleteButton(h) {
    const id = h && h.id != null ? String(h.id) : "";
    if (!id) return "";
    const label = escapeHtml(_t("home.history.delete_btn"));
    return `<button class="history-remove" data-action="history-delete" data-id="${escapeHtml(id)}" title="${label}" aria-label="${label}">&times;</button>`;
  }

  async function _deleteAnalyzedHistory(id) {
    if (!id) return;
    const res = await fetch(`/api/process/my-history/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || _t("home.history.delete_failed"));
    }
    if (typeof showToast === "function") showToast(_t("home.history.deleted"), 1800);
    await _loadBetaHistory();
  }

  function _wireHistoryCards(container) {
    if (!container) return;
    container.querySelectorAll(".grid-item").forEach(el => {
      el.addEventListener("click", (e) => {
        if (e.target.closest("[data-action='history-delete']")) return;
        if (el.dataset.path) goPlayer(el.dataset.path);
        else if (el.dataset.hash) goPlayer("", el.dataset.hash);
      });
    });
    container.querySelectorAll("[data-action='history-delete']").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (!confirm(_t("home.confirm.delete_analysis"))) return;
        btn.disabled = true;
        try {
          await _deleteAnalyzedHistory(btn.dataset.id);
        } catch (err) {
          btn.disabled = false;
          const msg = err && err.message ? err.message : _t("home.history.delete_failed");
          if (typeof showToast === "function") showToast(msg, 3000);
          else alert(msg);
        }
      });
    });
  }

  async function _loadBetaHistory() {
    const grid = $("#historyGrid");
    const recentContainer = $("#betaRecentList");
    const recentSection = $("#secBetaRecent");
    try {
      const res = await fetch("/api/process/my-history?limit=30");
      if (!res.ok) return;
      const data = await res.json();
      const items = (data.history || []).filter(h => h.status === "done" && h.result_hash);
      const auditByHash = new Map(items.map(h => [h.result_hash, h]));
      if (recentContainer) recentContainer.innerHTML = "";
      if (recentSection) recentSection.style.display = "none";

      // Also fetch NAS library recent plays and merge into the recent section
      let recentItems = [];
      try {
        const recentData = await API.getRecent();
        recentItems = (recentData.recent || []).map(r => {
          // __hash/ 項目是上傳分析的處理結果，當成 hash-card 處理，不走 library path
          if (typeof r.path === "string" && r.path.startsWith("__hash/")) {
            const resultHash = r.path.slice(7);
            const audit = auditByHash.get(resultHash) || {};
            return {
              ...audit,
              _isLibrary: false,
              result_hash: resultHash,
              title: (r.title || audit.title || _t("home.label.analysis_result")),
              source_type: "upload",
            };
          }
          return {
            _isLibrary: true,
            path: r.path,
            title: (r.title || r.path.split("/").pop()).replace(/\.flac$/i, ""),
            result_hash: r.hash || "",
          };
        });
      } catch {}

      // Merge: PLAY history first (most-recently-played at top), then
      // fall back to analysis history for songs analyzed but not yet
      // played manually. The previous order put `items` first which is
      // sorted by analyze time, so the user-most-recent song appeared
      // wherever it landed in analyze order — they had to scan the row
      // to find what they just played. /api/recent already returns
      // play-time DESC (so most-recent first).
      const seenTitles = new Set();
      const mergedRecent = [];
      for (const r of recentItems) {
        const key = (r.title || "").toLowerCase();
        if (key && !seenTitles.has(key)) {
          mergedRecent.push(r);
          seenTitles.add(key);
        }
      }
      for (const h of items) {
        const key = (h.title || "").toLowerCase();
        if (key && !seenTitles.has(key)) {
          mergedRecent.push(h);
          seenTitles.add(key);
        }
      }

      // Recent plays — show for non-admin (their only recent section), or when many items
      if (recentContainer && mergedRecent.length > 0 && (_isBetaNonAdmin || mergedRecent.length > 8)) {
        recentContainer.innerHTML = mergedRecent.slice(0, 8).map(h => {
          if (h._isLibrary) {
            const coverUrl = API.trackCoverUrl(h.path);
            return `<div class="grid-item" data-path="${escapeHtml(h.path)}" style="cursor:pointer">
              <img class="cover" src="${coverUrl}" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" alt="">
              <div class="cover-placeholder" style="display:none">&#x1F3B5;</div>
              <div class="info">
                <div class="title">${escapeHtml(h.title)}</div>
                ${getDifficultyHtml(h)}
              </div>
            </div>`;
          }
          const title = h.title || _t("home.label.analysis_result");
          return `<div class="grid-item history-card" data-hash="${escapeHtml(h.result_hash)}" style="cursor:pointer">
            ${_buildCoverHtml(h)}
            <div class="info">
              <div class="title">${escapeHtml(title)}</div>
              ${getDifficultyHtml(h)}
            </div>
            ${_buildHistoryDeleteButton(h)}
          </div>`;
        }).join("");
        _wireHistoryCards(recentContainer);
        if (recentSection) recentSection.style.display = "";
      }

      // Library grid (all items)
      if (grid) {
        if (items.length === 0) {
          grid.innerHTML = `<div style="text-align:center;padding:20px;color:var(--text-dim)">${_t("home.search.no_songs_empty")}</div>`;
          return;
        }
        grid.innerHTML = items.map(h => {
          const title = h.title || _t("home.label.analysis_result");
          return `<div class="grid-item history-card" data-hash="${escapeHtml(h.result_hash)}" style="cursor:pointer">
            ${_buildCoverHtml(h)}
            <div class="info">
              <div class="title">${escapeHtml(title)}</div>
              ${getDifficultyHtml(h)}
            </div>
            ${_buildHistoryDeleteButton(h)}
          </div>`;
        }).join("");
        _wireHistoryCards(grid);
      }
    } catch (e) {
      console.error("loadBetaHistory error:", e);
    }
  }

  // ---- demo songs (royalty-free pre-analyzed tracks shipped under data/demo/) ----
  // Renders into either #secDemoSongsHero (logged-out marketing landing, prominent)
  // or #secDemoSongs (logged-in dashboard strip, compact). Same backing data
  // either way; visibility flipped by login state. Click → /player?hash=<h>.
  // The /api/demo/list endpoint is unauthenticated by design — anonymous
  // visitors are the primary audience. Silent failure (empty manifest, missing
  // endpoint, 404) is fine: the section just stays hidden.
  async function _loadDemoSongs() {
    let demos = [];
    try {
      const res = await fetch("/api/demo/list");
      if (!res.ok) return;
      demos = await res.json();
    } catch { return; }
    if (!Array.isArray(demos) || demos.length === 0) return;

    const isLoggedIn = !!localStorage.getItem("livechord_token");
    const targetId = isLoggedIn ? "#secDemoSongs" : "#secDemoSongsHero";
    const sec = $(targetId);
    if (!sec) return;

    // Group manifest entries by category. Manifest order within a category
    // is preserved (build_demo.py keeps "easy/recognized first" ordering).
    const byCat = { classical: [], folk: [], jazz: [], easy: [], pop: [] };
    demos.forEach(d => { (byCat[d.category] || byCat.easy).push(d); });

    const openDemo = (d, source) => {
      if (!d || !d.hash) return;
      try { sessionStorage.setItem("livechord_from_demo", "1"); } catch (_) {}
      if (window.API && API.trackEvent) {
        API.trackEvent("demo_click", {
          source,
          is_demo: true,
          demo_id: d.id || "",
          song_hash: d.hash,
          title: d.title || ""
        });
      }
      if (window.LiveChordAnalytics) {
        window.LiveChordAnalytics.track("demo_click", {
          source,
          is_demo: true,
          demo_id: d.id || "",
          song_hash: d.hash,
          title: d.title || ""
        });
      }
      goPlayer("", d.hash);
    };

    const renderCard = (d) => {
      const cover = d.cover_url || "";
      const license = d.license || "";
      const licenseUrl = d.license_url || "";
      const licenseHtml = license
        ? (licenseUrl
            ? ` · <a class="demo-license-link" href="${escapeHtml(licenseUrl)}" target="_blank" rel="noopener">${escapeHtml(license)}</a>`
            : ` · ${escapeHtml(license)}`)
        : "";
      return `<div class="grid-item demo-card" data-hash="${escapeHtml(d.hash)}" style="cursor:pointer">
        ${cover
          ? `<img class="cover" src="${escapeHtml(cover)}" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" alt="">
             <div class="cover-placeholder" style="display:none">&#x1F3B5;</div>`
          : `<div class="cover-placeholder" style="display:flex">&#x1F3B5;</div>`}
        <div class="info">
          <div class="title">${escapeHtml(d.title || "")}</div>
          <div class="demo-credit">${escapeHtml(d.artist || "")}${licenseHtml}</div>
        </div>
      </div>`;
    };

    let totalRendered = 0;
    const featuredEl = sec.querySelector("[data-featured-demo]");
    if (featuredEl) {
      const featured = demos.find(d => d.id === "canon_in_d") || demos[0];
      if (featured) {
        const cover = featured.cover_url || "";
        featuredEl.innerHTML = `
          <button class="demo-featured-card" type="button" data-hash="${escapeHtml(featured.hash)}">
            <span class="demo-featured-media">
              ${cover
                ? `<img src="${escapeHtml(cover)}" alt="" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
                   <span class="demo-featured-placeholder" style="display:none">&#x1F3B5;</span>`
                : `<span class="demo-featured-placeholder" style="display:flex">&#x1F3B5;</span>`}
            </span>
            <span class="demo-featured-copy">
              <span class="demo-featured-kicker">${escapeHtml(_t("home.demo.featured.kicker", null, "Best first demo"))}</span>
              <span class="demo-featured-title">${escapeHtml(featured.title || "")}</span>
              <span class="demo-featured-body">${escapeHtml(_t("home.demo.featured.body", null, "Start with a familiar progression and see how LiveChord lines up chords, beats, and instrument views before you upload anything."))}</span>
            </span>
            <span class="demo-featured-action">${escapeHtml(_t("home.demo.featured.cta", null, "Open demo"))}</span>
          </button>`;
        featuredEl.querySelector(".demo-featured-card")?.addEventListener("click", () => openDemo(featured, "featured_demo"));
      } else {
        featuredEl.innerHTML = "";
      }
    }

    sec.querySelectorAll(".demo-category").forEach(catEl => {
      const cat = catEl.dataset.cat;
      const items = byCat[cat] || [];
      const grid = catEl.querySelector(".demo-grid");
      if (!grid) return;
      if (items.length === 0) {
        catEl.style.display = "none";
        return;
      }
      catEl.style.display = "";
      grid.innerHTML = items.map(renderCard).join("");
      totalRendered += items.length;
    });

    // Click delegation across all category sub-grids. Stamp a sessionStorage
    // flag before navigating so when the user lands back on / (browser back
    // or explicit return) we can scroll them straight to the demo block
    // instead of dropping them at the top of the marketing landing.
    sec.querySelectorAll(".demo-card").forEach(el => {
      el.addEventListener("click", () => {
        const d = demos.find(x => x.hash === el.dataset.hash) || { hash: el.dataset.hash };
        openDemo(d, "demo_grid");
      });
    });

    // Attribution block — required for CC-BY/SA tracks, helpful for PD too.
    // Lists every demo with its source link + license. Sits at the bottom of
    // the section in DOM order (one .demo-attribution-block per section).
    const attrEl = sec.querySelector(".demo-attribution-block");
    if (attrEl && demos.length > 0) {
      const heading = _t("home.demo.attribution_heading", null);
      const headingText = (heading && heading !== "home.demo.attribution_heading")
        ? heading : "Source credits";
      const rows = demos.map(d => {
        const t = escapeHtml(d.title || "");
        const a = escapeHtml(d.artist || "");
        const lic = escapeHtml(d.license || "");
        const licUrl = d.license_url || "";
        const srcUrl = d.source_url || "";
        const srcLink = srcUrl
          ? `<a class="demo-attr-link" href="${escapeHtml(srcUrl)}" target="_blank" rel="noopener">source</a>`
          : "";
        const licLink = (lic && licUrl)
          ? `<a class="demo-attr-link" href="${escapeHtml(licUrl)}" target="_blank" rel="noopener">${lic}</a>`
          : escapeHtml(lic);
        return `<li><span class="demo-attr-title">${t}</span> · <span class="demo-attr-artist">${a}</span> · ${licLink}${srcLink ? ` · ${srcLink}` : ""}</li>`;
      }).join("");
      attrEl.innerHTML = `
        <h4 class="demo-attr-heading">${escapeHtml(headingText)}</h4>
        <ul class="demo-attr-list">${rows}</ul>`;
    }

    if (totalRendered > 0) sec.style.display = "";
  }

  // No-op: kept for backward compatibility with the bfcache hook below.
  // Section ordering is now handled entirely by CSS `order:` on
  // .dashboard-layout — see frontend/css/home.css "Section ordering"
  // block. Empty sections collapse via display:none so fresh users still
  // see demos high; users with content see them below their library.
  function _repositionDemoSection() {}

  // If the user just clicked a demo card, _loadDemoSongs() set a
  // sessionStorage flag before navigating to /player. When they come back
  // to / (explicit click on logo, browser back, bfcache restore), scroll
  // them straight to the demo block instead of dumping them at the top of
  // the marketing landing or the dashboard. One-shot — flag clears on use.
  function _scrollToDemoIfFromPlayer() {
    let from = "";
    try { from = sessionStorage.getItem("livechord_from_demo") || ""; } catch (_) {}
    if (!from) return;
    try { sessionStorage.removeItem("livechord_from_demo"); } catch (_) {}
    // Pick whichever demo section is currently visible (logged-out hero
    // variant vs logged-in dashboard variant).
    const candidates = [
      document.getElementById("secDemoSongsHero"),
      document.getElementById("secDemoSongs"),
    ];
    const sec = candidates.find(el =>
      el && getComputedStyle(el).display !== "none");
    if (!sec) return;
    // Slight delay so the demo section's async render (cards + attribution
    // block) has settled before we measure its position.
    setTimeout(() => {
      sec.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 80);
  }

  // ---- dashboard init ----

  async function initDashboard() {
    // Check beta access first
    await _checkBetaAccess();

    // Parallel loading avoids blocking the UI
    try {
      showLoading(true);
      const tasks = [];
      // Demo songs render in both beta/public modes (logged-in OR logged-out).
      // _loadDemoSongs picks the target section by login state itself, so a
      // single unconditional call covers all paths. Personal mode (admin on
      // LAN) also gets them — they're harmless on the dashboard and the
      // endpoint is mode-agnostic. Run early in the parallel batch.
      tasks.push(_loadDemoSongs());
      if (_isBetaNonAdmin) {
        _initBetaUpload();
        _initBetaLocalAudio();
        // _loadBetaHistory fills both #historyGrid (now null, guarded) AND
        // #betaRecentList (最近播放) — must keep it for the recent list.
        tasks.push(_loadBetaHistory(), loadFavorites());
      } else {
        // Admin path. NAS browse is personal-only — skip in beta/public to
        // avoid /api/browse 404 spam (the section is already hidden by
        // _checkBetaAccess but the fetch was still firing here).
        // loadRecent populates #secRecent (NAS-library recents). Skip
        // it in public mode — _loadBetaHistory already populates
        // #secBetaRecent with the same merged process_audit + library
        // data, and rendering both gave the user TWO "Recently played"
        // rows on the public dashboard.
        if (!_isPublicMode) tasks.push(loadRecent());
        tasks.push(loadFavorites());
        if (!_isBetaMode) tasks.push(browse(currentPath));
        if (_isBetaMode) tasks.push(_loadBetaHistory());
        // Public-mode users need the upload modal wired so the hero CTA
        // (and signed-in Add-song flows) can analyze audio. The local-
        // music dashboard section + its "+ Pick local audio" button are
        // only meaningful when signed in (the section is force-hidden
        // for anon visitors above) — _initBetaLocalAudio also calls
        // section.style.display = "" which would un-hide it for anon
        // visitors if called unconditionally.
        if (_isPublicMode) {
          _initBetaUpload();
          if (localStorage.getItem("livechord_token")) _initBetaLocalAudio();
        }
      }
      await Promise.allSettled(tasks);
      _resumeBetaActiveAnalysis();
      _repositionDemoSection();
      _scrollToDemoIfFromPlayer();
    } finally {
      showLoading(false);
    }

    // bfcache refresh — mobile browsers snapshot this page when the user
    // navigates into the player; pressing Back restores the snapshot instantly
    // (no init re-run), so a song the user just analyzed+played would NOT
    // show up in 最近播放 until some other trigger reflowed the list. Rerun
    // the beta history fetch whenever we're restored from bfcache.
    window.addEventListener("pageshow", (ev) => {
      _resumeBetaActiveAnalysis();
      if (!ev.persisted) {
        // First-render path or full reload — initDashboard's bottom call
        // already covered the scroll; nothing to do here.
        return;
      }
      if (_isBetaMode) _loadBetaHistory().then(_repositionDemoSection);
      else if (typeof loadRecent === "function") loadRecent();
      // bfcache: demo section is already in the DOM from the snapshot, so
      // we can fire the scroll restore immediately without waiting for any
      // async render.
      _scrollToDemoIfFromPlayer();
    });
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) _resumeBetaActiveAnalysis();
    });
    window.addEventListener("focus", () => _resumeBetaActiveAnalysis());
  }

  // ---- browse ----

  async function browse(path) {
    currentPath = path;
    try { localStorage.setItem("livechord_home_path", path || ""); } catch {}
    showLoading(true);
    browseGrid.innerHTML = "";
    trackList.innerHTML = "";
    trackList.style.display = "none";
    browseGrid.style.display = "";

    try {
      const data = await API.browse(path);
      renderBreadcrumb(data.current);

      const dirs = data.entries.filter((e) => e.is_dir);
      const files = data.entries.filter((e) => !e.is_dir);

      if (files.length > 0 && dirs.length === 0) {
        // 純音軌 → 列表模式
        browseGrid.style.display = "none";
        trackList.style.display = "";
        const filterInput = $("#dirFilter");
        filterInput.style.display = files.length > 15 ? "" : "none";
        filterInput.value = "";
        renderTrackList(files);
      } else {
        $("#dirFilter").style.display = "none";
        renderGrid(dirs, files);
      }
    } catch (err) {
      // Saved path may be stale (folder renamed/deleted) — fall back to root once.
      if (path) {
        try { localStorage.removeItem("livechord_home_path"); } catch {}
        showLoading(false);
        return browse("");
      }
      browseGrid.innerHTML = `<div class="empty"><div class="icon">&#x26A0;</div><div class="msg">${escapeHtml(err.message)}</div></div>`;
    } finally {
      showLoading(false);
    }
  }

  function renderBreadcrumb(current) {
    const parts = current === "." ? [] : current.split("/");
    let html = `<a href="#" data-path="">Music</a>`;
    let acc = "";
    for (const p of parts) {
      if (!p) continue;
      acc += (acc ? "/" : "") + p;
      html += `<span class="sep">/</span><a href="#" data-path="${escapeHtml(acc)}">${escapeHtml(p)}</a>`;
    }
    breadcrumb.innerHTML = html;
    breadcrumb.querySelectorAll("a").forEach((a) => {
      a.addEventListener("click", (e) => {
        e.preventDefault();
        browse(a.dataset.path);
      });
    });
  }

  function renderGrid(dirs, files) {
    let html = "";

    for (const d of dirs) {
      const coverUrl = d.has_cover
        ? API.trackCoverUrl(d.path)
        : "";
      html += `
        <div class="grid-item" data-path="${escapeHtml(d.path)}" data-dir="1">
          ${coverUrl
            ? `<img class="cover" src="${coverUrl}" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" alt=""><div class="cover-placeholder" style="display:none">&#x1F4C1;</div>`
            : `<div class="cover-placeholder">&#x1F4C1;</div>`
          }
          <div class="info">
            <div class="title">${escapeHtml(d.name)}</div>
          </div>
        </div>`;
    }

    for (const f of files) {
      const coverUrl = API.trackCoverUrl(f.path);
      html += `
        <div class="grid-item" data-path="${escapeHtml(f.path)}">
          <img class="cover" src="${coverUrl}" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" alt="">
          <div class="cover-placeholder" style="display:none">&#x1F3B5;</div>
          <div class="info">
            <div class="title">${escapeHtml(f.name.replace(/\.flac$/i, ""))}${getDifficultyHtml(f)}</div>
          </div>
        </div>`;
    }

    browseGrid.innerHTML = html || `<div class="empty"><div class="icon">&#x1F4C2;</div><div class="msg">${_t("home.browse.empty_dir")}</div></div>`;

    browseGrid.querySelectorAll(".grid-item").forEach((el) => {
      el.addEventListener("click", () => {
        if (el.dataset.dir) {
          browse(el.dataset.path);
        } else {
          goPlayer(el.dataset.path);
        }
      });
    });
  }

  function renderTrackList(files) {
    let html = "";
    files.forEach((f, i) => {
      const name = f.name.replace(/\.flac$/i, "");
      const coverUrl = API.trackCoverUrl(f.path);
      html += `
        <li data-path="${escapeHtml(f.path)}">
          <img src="${coverUrl}" style="width:36px;height:36px;border-radius:4px;object-fit:cover;background:#222;flex-shrink:0" loading="lazy" onerror="this.style.display='none'" alt="">
          <span class="track-title">${escapeHtml(name)}${getDifficultyHtml(f)}</span>
        </li>`;
    });
    trackList.innerHTML = html;
    trackList.querySelectorAll("li").forEach((li) => {
      li.addEventListener("click", () => goPlayer(li.dataset.path));
    });
  }

  // ---- directory filter ----
  $("#dirFilter").addEventListener("input", (e) => {
    const term = e.target.value.toLowerCase();
    trackList.querySelectorAll("li").forEach(li => {
      const title = li.querySelector(".track-title").textContent.toLowerCase();
      li.style.display = title.includes(term) ? "" : "none";
    });
  });

  // ---- search ----

  searchInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    const q = searchInput.value.trim();
    if (q.length < 1) {
      searchResults.classList.remove("show");
      return;
    }
    searchTimer = setTimeout(() => doSearch(q), 300);
  });

  searchInput.addEventListener("focus", () => {
    if (searchResults.children.length > 0) searchResults.classList.add("show");
  });

  // Enter on the search box for beta non-admin opens the add-song modal —
  // gives a clear "no results, want to upload?" affordance.
  searchInput.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    if (!_isBetaNonAdmin) return;
    e.preventDefault();
    searchResults.classList.remove("show");
    const panel = $("#betaFabPanel");
    const backdrop = $("#betaFabBackdrop");
    if (panel) { panel.classList.add("open"); panel.classList.remove("file-only"); }
    if (backdrop) backdrop.classList.add("open");
  });

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".search-box")) searchResults.classList.remove("show");
  });

  function _renderLibraryHtml(results) {
    let html = "";
    for (const r of results) {
      const hasHash = r.hash || r.path.startsWith("__hash/");
      const coverUrl = hasHash ? "" : API.trackCoverUrl(r.path);
      const uploadBadge = r.is_user_upload
        ? `<span class="r-upload-badge">${_t("home.search.upload_badge")}</span>`
        : "";
      html += `
        <div class="result-item${r.is_user_upload ? " is-upload" : ""}" data-path="${escapeHtml(r.path)}" ${r.hash ? `data-hash="${escapeHtml(r.hash)}"` : ""}>
          ${coverUrl ? `<img class="r-cover" src="${coverUrl}" onerror="this.style.display='none'" loading="lazy" alt="">` : ""}
          <div class="r-info">
            <div class="r-title">${escapeHtml(r.title || r.path.split("/").pop())}${uploadBadge}${getDifficultyHtml(r)}</div>
            <div class="r-artist">${escapeHtml(r.artist || "")} ${r.album ? "— " + escapeHtml(r.album) : ""}</div>
          </div>
        </div>`;
    }
    return html;
  }

  function _renderEmptyState(q) {
    if (_isBetaNonAdmin) {
      searchResults.innerHTML = `
        <div class="search-empty">
          <div class="search-empty-msg">${_t("home.search.no_match", {q: escapeHtml(q)})}</div>
          <button id="searchAddSongBtn" class="search-empty-btn">${_t("home.search.add_song_btn")}</button>
        </div>`;
      const btn = document.getElementById("searchAddSongBtn");
      if (btn) btn.addEventListener("click", (e) => {
        e.stopPropagation();
        searchResults.classList.remove("show");
        const panel = $("#betaFabPanel");
        const backdrop = $("#betaFabBackdrop");
        if (panel) { panel.classList.add("open"); panel.classList.remove("file-only"); }
        if (backdrop) backdrop.classList.add("open");
      });
    } else {
      searchResults.innerHTML = `<div style="padding:12px;color:var(--text-dim)">${_t("home.search.no_results")}</div>`;
    }
  }

  function _bindLibraryClicks() {
    searchResults.querySelectorAll(".result-item").forEach((el) => {
      el.addEventListener("click", () => {
        searchResults.classList.remove("show");
        goPlayer(el.dataset.path, el.dataset.hash || "");
      });
    });
  }

  function _searchVisibleCards(q) {
    const qLower = q.toLowerCase();
    const tokens = qLower.split(/\W+/).filter(Boolean);
    if (tokens.length === 0 && qLower) tokens.push(qLower);
    if (tokens.length === 0) return [];
    const seen = new Set();
    const results = [];
    document.querySelectorAll(".grid-item[data-path], .grid-item[data-hash]").forEach((el) => {
      if (el.closest("#searchResults")) return;
      const title = (el.querySelector(".title")?.textContent || "").trim();
      if (!title) return;
      const artist = (el.querySelector(".demo-credit")?.textContent || "").trim();
      const searchable = `${title} ${artist}`.toLowerCase();
      if (!tokens.every(tok => searchable.includes(tok))) return;
      const hash = el.dataset.hash || "";
      const path = el.dataset.path || (hash ? `__hash/${hash}` : "");
      if (!path) return;
      const key = hash || path;
      if (seen.has(key)) return;
      seen.add(key);
      results.push({
        path,
        hash,
        title,
        artist,
        album: hash ? "Sample / recent" : "",
        has_chords: true,
      });
    });
    return results.slice(0, 20);
  }

  // Tracks the currently in-flight query so a stale response from a previous
  // keystroke doesn't paint over fresher results.
  let _searchEpoch = 0;

  async function doSearch(q) {
    const epoch = ++_searchEpoch;
    const lib = await API.search(q).catch(err => ({ error: String(err).slice(0, 200) }));
    if (epoch !== _searchEpoch) return; // user typed something else; bail

    if (lib && lib.error) {
      const fallbackResults = _searchVisibleCards(q);
      if (fallbackResults.length > 0) {
        searchResults.innerHTML = _renderLibraryHtml(fallbackResults);
        searchResults.classList.add("show");
        _bindLibraryClicks();
        return;
      }
      if (_isBetaMode) {
        _renderEmptyState(q);
        searchResults.classList.add("show");
        return;
      }
      searchResults.innerHTML = `<div style="padding:12px;color:var(--text-dim)">${escapeHtml(lib.error)}</div>`;
      searchResults.classList.add("show");
      return;
    }
    const libResults = (lib && lib.results) || [];
    if (libResults.length === 0) {
      const fallbackResults = _searchVisibleCards(q);
      if (fallbackResults.length > 0) {
        searchResults.innerHTML = _renderLibraryHtml(fallbackResults);
        searchResults.classList.add("show");
        _bindLibraryClicks();
        return;
      }
      _renderEmptyState(q);
      searchResults.classList.add("show");
      return;
    }
    searchResults.innerHTML = _renderLibraryHtml(libResults);
    searchResults.classList.add("show");
    _bindLibraryClicks();
  }

  // ---- favorites ----
  async function loadFavorites() {
    const section = $("#secFavorites");
    const container = $("#favList");
    try {
      const data = await API.getFavorites();
      if (!data.favorites || data.favorites.length === 0) {
        section.style.display = "none";
        return;
      }
      section.style.display = "";

      // For hash-based favorites, fetch chord titles
      const hashFavs = data.favorites.filter(f => f.path.startsWith("__hash/"));
      const hashTitles = {};
      for (const f of hashFavs) {
        const hash = f.path.replace("__hash/", "");
        try {
          const cd = await fetch(`/api/chords/by-hash?hash=${hash}`).then(r => r.json());
          if (cd.exists) hashTitles[hash] = { title: cd.title || hash };
        } catch {}
      }

      let html = '';
      data.favorites.forEach((f) => {
        const isHash = f.path.startsWith("__hash/");
        const hash = isHash ? f.path.replace("__hash/", "") : "";
        const removeLabel = escapeHtml(_t("home.local.remove_btn"));
        const removeBtn = `<button class="favorite-remove" data-action="favorite-remove" data-path="${escapeHtml(f.path)}" title="${removeLabel}" aria-label="${removeLabel}">&times;</button>`;
        if (isHash) {
          const info = hashTitles[hash] || { title: hash };
          html += `
            <div class="grid-item favorite-card" data-hash="${escapeHtml(hash)}">
              <img class="cover" src="/api/process/cover/${escapeHtml(hash)}" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" alt="">
              <div class="cover-placeholder" style="display:none">&#x1F3B5;</div>
              <div class="info"><div class="title">${escapeHtml(info.title)}</div></div>
              ${removeBtn}
            </div>`;
        } else {
          const name = f.path.split("/").pop().replace(/\.flac$/i, "");
          const coverUrl = API.trackCoverUrl(f.path);
          html += `
            <div class="grid-item favorite-card" data-path="${escapeHtml(f.path)}">
              <img class="cover" src="${coverUrl}" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" alt="">
              <div class="cover-placeholder" style="display:none">&#x1F3B5;</div>
              <div class="info">
                <div class="title">${escapeHtml(name)}</div>
                ${getDifficultyHtml(f)}
              </div>
              ${removeBtn}
            </div>`;
        }
      });
      container.innerHTML = html;
      container.querySelectorAll(".grid-item").forEach((el) => {
        el.addEventListener("click", (e) => {
          if (e.target.closest("[data-action='favorite-remove']")) return;
          if (el.dataset.hash) goPlayer("", el.dataset.hash);
          else goPlayer(el.dataset.path);
        });
      });
      container.querySelectorAll("[data-action='favorite-remove']").forEach((btn) => {
        btn.addEventListener("click", async (e) => {
          e.preventDefault();
          e.stopPropagation();
          btn.disabled = true;
          try {
            await API.removeFavorite(btn.dataset.path || "");
            if (typeof showToast === "function") showToast(_t("toast.fav.removed"), 1800);
            await loadFavorites();
          } catch (err) {
            btn.disabled = false;
            const msg = err && err.message ? err.message : String(err || "");
            if (typeof showToast === "function") {
              showToast(_t("toast.fav.failed", { err: msg }), 3000);
            }
          }
        });
      });
    } catch (err) {
      section.style.display = "none";
    }
  }

  // ---- recent ----

  async function loadRecent() {
    const section = $("#secRecent");
    const container = $("#recentList");
    try {
      const data = await API.getRecent();
      if (!data.recent || data.recent.length === 0) {
        section.style.display = "none";
        return;
      }
      section.style.display = "";

      let html = '';
      data.recent.forEach((r) => {
        const isHash = typeof r.path === "string" && r.path.startsWith("__hash/");
        if (isHash) {
          const hash = r.path.slice(7);
          const coverUrl = `/api/process/cover/${encodeURIComponent(hash)}`;
          const title = r.title || _t("home.label.analysis_result");
          html += `
            <div class="grid-item" data-hash="${escapeHtml(hash)}">
              <img class="cover" src="${coverUrl}" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" alt="">
              <div class="cover-placeholder" style="display:none">&#x1F3B5;</div>
              <div class="info">
                <div class="title">${escapeHtml(title)}</div>
              </div>
            </div>`;
          return;
        }
        const name = r.path.split("/").pop().replace(/\.flac$/i, "");
        const coverUrl = API.trackCoverUrl(r.path);
        html += `
          <div class="grid-item" data-path="${escapeHtml(r.path)}">
            <img class="cover" src="${coverUrl}" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" alt="">
            <div class="cover-placeholder" style="display:none">&#x1F3B5;</div>
            <div class="info">
              <div class="title">${escapeHtml(name)}</div>
              ${getDifficultyHtml(r)}
            </div>
          </div>`;
      });
      container.innerHTML = html;
      container.querySelectorAll(".grid-item").forEach((el) => {
        if (el.dataset.hash) el.addEventListener("click", () => goPlayer("", el.dataset.hash));
        else el.addEventListener("click", () => goPlayer(el.dataset.path));
      });
    } catch (err) {
      section.style.display = "none";
    }
  }

  // ---- 橫向捲動：拖曳（含慣性）----
  // 滑鼠滾輪保留給整頁垂直捲動；想要橫向捲動請用拖曳、觸控板或水平滾輪。
  function _initHorizontalScroll(el) {
    if (el.dataset.hscroll) return;
    el.dataset.hscroll = "1";

    // 拖曳 + 慣性
    let isDragging = false;
    let startX = 0, scrollStart = 0;
    let lastX = 0, lastTime = 0, velocity = 0;
    let momentumId = null;

    function _stopMomentum() {
      if (momentumId) { cancelAnimationFrame(momentumId); momentumId = null; }
    }

    function _startDrag(x) {
      _stopMomentum();
      isDragging = true;
      startX = x;
      scrollStart = el.scrollLeft;
      lastX = x;
      lastTime = Date.now();
      velocity = 0;
      el.style.cursor = "grabbing";
      el.style.userSelect = "none";
    }

    function _moveDrag(x) {
      if (!isDragging) return;
      const dx = x - startX;
      el.scrollLeft = scrollStart - dx;

      // 計算速度
      const now = Date.now();
      const dt = now - lastTime;
      if (dt > 0) {
        velocity = (lastX - x) / dt * 16; // px per frame
      }
      lastX = x;
      lastTime = now;
    }

    function _endDrag() {
      if (!isDragging) return;
      isDragging = false;
      el.style.cursor = "";
      el.style.userSelect = "";

      // 慣性滑動
      if (Math.abs(velocity) > 0.5) {
        function coast() {
          velocity *= 0.95; // 摩擦力
          if (Math.abs(velocity) < 0.3) return;
          el.scrollLeft += velocity;
          momentumId = requestAnimationFrame(coast);
        }
        momentumId = requestAnimationFrame(coast);
      }
    }

    // 滑鼠事件
    el.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      _startDrag(e.clientX);
      e.preventDefault(); // 防止選取文字
    });
    window.addEventListener("mousemove", (e) => {
      if (!isDragging) return;
      // 如果滑鼠按鈕已放開（buttons === 0），強制結束
      if (e.buttons === 0) { _endDrag(); return; }
      _moveDrag(e.clientX);
    });
    window.addEventListener("mouseup", _endDrag);
    // 滑鼠離開視窗時也結束
    document.addEventListener("mouseleave", _endDrag);

    // 觸控事件（平板）
    el.addEventListener("touchstart", (e) => {
      _startDrag(e.touches[0].clientX);
    }, { passive: true });
    el.addEventListener("touchmove", (e) => {
      _moveDrag(e.touches[0].clientX);
    }, { passive: true });
    el.addEventListener("touchend", _endDrag);

    // 防止拖曳時觸發點擊
    el.addEventListener("click", (e) => {
      if (Math.abs(velocity) > 1 || Math.abs(lastX - startX) > 5) {
        e.stopPropagation();
        e.preventDefault();
      }
    }, true);
  }

  // 初始化已有的 + 動態載入的
  document.querySelectorAll(".horizontal-scroll").forEach(_initHorizontalScroll);
  new MutationObserver(() => {
    document.querySelectorAll(".horizontal-scroll").forEach(_initHorizontalScroll);
  }).observe(document.body, { childList: true, subtree: true });

  // ---- init ----

  // First-run instrument picker. Shown once if `livechord_tab` is unset
  // (which is the same key player.js uses to remember the last chosen
  // instrument tab — picking here pre-seeds it). Skipping or closing
  // writes "piano" so the modal doesn't reappear.
  function _initInstrumentPicker() {
    // Public logged-out visitors should reach the hero CTA / demo cards first.
    // The picker is a preference, not a gate; show it only after sign-in.
    if (!localStorage.getItem("livechord_token")) return;

    const SKIP_KEY = "livechord_tab";
    let saved;
    try { saved = localStorage.getItem(SKIP_KEY); } catch { saved = null; }
    if (saved) return; // user already has a default — never nag

    const backdrop = $("#instrumentPickerBackdrop");
    const panel = $("#instrumentPickerPanel");
    if (!backdrop || !panel) return;

    function close(pick) {
      try { localStorage.setItem(SKIP_KEY, pick); } catch {}
      backdrop.style.display = "none";
      panel.style.display = "none";
    }

    backdrop.style.display = "block";
    panel.style.display = "block";

    panel.querySelectorAll(".ip-card").forEach(btn => {
      btn.addEventListener("click", () => {
        const pick = btn.getAttribute("data-pick") || "piano";
        close(pick);
      });
    });
    const skip = $("#ipSkip");
    if (skip) skip.addEventListener("click", () => close("piano"));
    backdrop.addEventListener("click", () => close("piano"));
  }
  _initInstrumentPicker();

  // 啟動 Dashboard (Lazy + Parallel)
  initDashboard();
})();
