/** LiveChord 首頁 — 瀏覽、搜尋、最愛、最近播放 */

(function () {
  function _t(k, v) { return (window.LiveChordI18n && window.LiveChordI18n.t) ? window.LiveChordI18n.t(k, v) : k; }

  // ---- state ----
  let currentPath = localStorage.getItem("livechord_home_path") || "";
  let currentTab = localStorage.getItem("livechord_home_tab") || "recent";
  let searchTimer = null;

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

  function getDifficultyHtml(item) {
    const uc = item.unique_chords || 0;
    if (!uc || uc <= 0) return "";
    let stars = 1;
    if (uc >= 15) stars = 4;
    else if (uc >= 9) stars = 3;
    else if (uc >= 5) stars = 2;
    const key = item.chord_key || "";
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
      if (_isBetaMode && !adminRes.is_admin) {
        _isBetaNonAdmin = true;
        // Hide remaining NAS-leaking section for non-admin users.
        // (secBrowse already hidden above for the admin path too.)
        const secRecent = $("#secRecent");
        if (secRecent) secRecent.style.display = "none";
        // Show beta sections (no standalone FAB — modal opens from search empty state)
        const secBetaRecent = $("#secBetaRecent");
        const secHistory = $("#secHistory");
        if (secBetaRecent) secBetaRecent.style.display = "";
        if (secHistory) secHistory.style.display = "";
        // Modal close via × button or backdrop click
        const closeBtn = $("#betaFabClose");
        const backdrop = $("#betaFabBackdrop");
        const closeAddSongModal = () => {
          const p = $("#betaFabPanel");
          if (p) {
            p.classList.remove("open");
            p.classList.remove("file-only");
            p.classList.remove("analyzing");
          }
          if (backdrop) backdrop.classList.remove("open");
          // Reset progress rows so the next open starts clean (otherwise a
          // stale "讀取 YouTube 標題... 100%" bar would still be visible).
          const up = $("#betaUploadProgress"); if (up) up.style.display = "none";
          const yt = $("#betaYtProgress"); if (yt) yt.style.display = "none";
        };
        if (closeBtn) closeBtn.addEventListener("click", closeAddSongModal);
        if (backdrop) backdrop.addEventListener("click", closeAddSongModal);
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

  function _betaPickFile(file) {
    if (file.size > 200 * 1024 * 1024) { alert(_t("home.alert.file_too_big")); return; }
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
    const tracks = _getLocalTracks();
    const analyzed = tracks.filter(t => t.analyzedHash);
    const pending = tracks.filter(t => !t.analyzedHash);

    // Analyzed: horizontal grid-item cards (like 最近播放)
    analyzedRow.innerHTML = analyzed.map(t => {
      const safeName = escapeHtml(t.name.replace(/\.[^.]+$/, ""));
      return `
        <div class="grid-item local-analyzed-card" data-id="${escapeHtml(t.id)}" style="cursor:pointer; position:relative">
          <div class="cover-placeholder" style="display:flex">&#x1F3B5;</div>
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

    // Pending: current vertical list layout
    if (pending.length === 0 && analyzed.length === 0) {
      pendingList.innerHTML = `<div style="color:var(--text-dim); font-size:13px; padding:8px 0">
        ${_t("home.local.empty_hint")}
      </div>`;
      return;
    }
    pendingList.innerHTML = pending.map(t => {
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
    const panel = $("#betaFabPanel");
    const backdrop = $("#betaFabBackdrop");
    if (panel) { panel.classList.add("open"); panel.classList.add("file-only"); }
    if (backdrop) backdrop.classList.add("open");
  }

  // ─── YT playlists registry ───
  // Persist user-added playlists + their video lists with existing_hash info.
  // Same UX concept as local tracks: one-at-a-time analyze, click ▶ to play
  // videos that are already analyzed anywhere (user's own OR library map).
  const YT_PLAYLISTS_KEY = "livechord_yt_playlists";
  let _currentAnalyzingPlaylistVid = null;   // "<list_id>|<video_id>" stamp for on-done marking

  function _getPlaylists() {
    try { return JSON.parse(localStorage.getItem(YT_PLAYLISTS_KEY) || "[]"); }
    catch { return []; }
  }
  function _savePlaylists(arr) {
    try { localStorage.setItem(YT_PLAYLISTS_KEY, JSON.stringify(arr)); } catch {}
  }
  async function _addPlaylist(url) {
    const listMatch = url.match(_YT_PLAYLIST_RE);
    const listId = listMatch ? listMatch[1] : "";
    if (!listId) { alert(_t("home.alert.playlist_no_list_param")); return null; }
    const existing = _getPlaylists().find(p => p.list_id === listId);
    if (existing) {
      alert(_t("home.alert.playlist_exists"));
      _renderPlaylists(true, listId);
      _scrollToPlaylistSection(listId);
      return existing;
    }
    try {
      const res = await fetch(`/api/process/playlist-info?url=${encodeURIComponent(url)}`);
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert(_t("home.alert.playlist_read_failed_prefix") + (err.detail || res.statusText));
        return null;
      }
      const data = await res.json();
      if (!data.videos || data.videos.length === 0) {
        alert(_t("home.alert.playlist_empty"));
        return null;
      }
      const entry = {
        list_id: listId,
        title: data.playlist_title || `Playlist ${listId.slice(0, 10)}`,
        videos: data.videos,       // [{video_id, title, duration, existing_hash}]
        url,
        addedAt: Date.now(),
      };
      const arr = _getPlaylists();
      arr.unshift(entry);
      _savePlaylists(arr);
      _renderPlaylists(true, listId);
      _scrollToPlaylistSection(listId);
      return entry;
    } catch (e) {
      alert(_t("home.alert.playlist_read_failed_prefix") + e.message);
      return null;
    }
  }
  function _removePlaylist(listId) {
    _savePlaylists(_getPlaylists().filter(p => p.list_id !== listId));
    _renderPlaylists();
  }
  function _markPlaylistVideoAnalyzed(listId, videoId, resultHash) {
    const arr = _getPlaylists();
    const p = arr.find(x => x.list_id === listId);
    if (!p) return;
    const v = p.videos.find(x => x.video_id === videoId);
    if (v) {
      v.existing_hash = resultHash;
      _savePlaylists(arr);
    }
  }
  async function _refreshPlaylist(listId) {
    const p = _getPlaylists().find(x => x.list_id === listId);
    if (!p) return;
    try {
      const res = await fetch(`/api/process/playlist-info?url=${encodeURIComponent(p.url)}`);
      if (!res.ok) return;
      const data = await res.json();
      if (data.videos) {
        p.videos = data.videos;
        p.title = data.playlist_title || p.title;
        _savePlaylists(_getPlaylists().map(x => x.list_id === listId ? p : x));
        _renderPlaylists(true, listId);
      }
    } catch {}
  }

  function _fmtDuration(sec) {
    sec = Math.max(0, Math.round(sec || 0));
    const m = Math.floor(sec / 60), s = sec % 60;
    return `${m}:${s.toString().padStart(2, "0")}`;
  }

  function _scrollToPlaylistSection(listId) {
    // Defer one frame so the newly-rendered card is in the DOM before we scroll.
    requestAnimationFrame(() => {
      const section = $("#secBetaPlaylists");
      if (!section || section.style.display === "none") return;
      const card = listId ? section.querySelector(`.yt-pl-card[data-id="${CSS.escape(listId)}"]`) : null;
      (card || section).scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  function _renderPlaylists(forceShow, openListId) {
    const section = $("#secBetaPlaylists");
    const container = $("#betaPlaylistList");
    if (!section || !container) return;
    const playlists = _getPlaylists();
    if (playlists.length === 0) {
      section.style.display = "none";
      return;
    }
    section.style.display = "";
    container.innerHTML = playlists.map(p => {
      const total = p.videos.length;
      const analyzed = p.videos.filter(v => v.existing_hash).length;
      const open = (forceShow && openListId === p.list_id);
      return `
        <div class="yt-pl-card${open ? " open" : ""}" data-id="${escapeHtml(p.list_id)}">
          <div class="yt-pl-header">
            <div class="yt-pl-title">${escapeHtml(p.title)}</div>
            <div class="yt-pl-meta">${_t("home.playlist.analyzed_count", {analyzed, total})}</div>
            <button class="yt-pl-refresh" data-action="refresh" data-id="${escapeHtml(p.list_id)}" title="${_t("home.playlist.refresh_title")}">↻</button>
            <button class="yt-pl-remove" data-action="remove" data-id="${escapeHtml(p.list_id)}" title="${_t("home.playlist.remove_title")}">&times;</button>
            <button class="yt-pl-toggle" data-action="toggle" data-id="${escapeHtml(p.list_id)}" aria-label="${_t("home.playlist.expand_aria")}">${open ? "▲" : "▼"}</button>
          </div>
          <div class="yt-pl-videos">
            ${p.videos.map(v => {
              const isDone = !!v.existing_hash;
              const action = isDone ? "play" : "analyze";
              const label = isDone ? _t("home.playlist.play") : _t("home.playlist.analyze");
              return `
                <div class="yt-pl-video${isDone ? " is-done" : ""}" data-list="${escapeHtml(p.list_id)}" data-vid="${escapeHtml(v.video_id)}">
                  <div class="ytv-info">
                    <div class="ytv-title" title="${escapeHtml(v.title)}">${escapeHtml(v.title)}</div>
                    <div class="ytv-meta">${_fmtDuration(v.duration)}${isDone ? " · " + _t("home.playlist.analyzed_marker") : ""}</div>
                  </div>
                  <button class="ytv-action btn-small ${isDone ? "btn-accent" : ""}" data-action="${action}" data-list="${escapeHtml(p.list_id)}" data-vid="${escapeHtml(v.video_id)}">${label}</button>
                </div>`;
            }).join("")}
          </div>
        </div>`;
    }).join("");

    container.querySelectorAll("[data-action='toggle']").forEach(btn => {
      btn.addEventListener("click", () => {
        const card = btn.closest(".yt-pl-card");
        if (card) card.classList.toggle("open");
        btn.textContent = card.classList.contains("open") ? "▲" : "▼";
      });
    });
    container.querySelectorAll("[data-action='refresh']").forEach(btn => {
      btn.addEventListener("click", () => _refreshPlaylist(btn.dataset.id));
    });
    container.querySelectorAll("[data-action='remove']").forEach(btn => {
      btn.addEventListener("click", () => {
        if (confirm(_t("home.confirm.remove_playlist")))
          _removePlaylist(btn.dataset.id);
      });
    });
    container.querySelectorAll("[data-action='play'],[data-action='analyze']").forEach(btn => {
      btn.addEventListener("click", () => _onPlaylistVideoAction(btn));
    });
  }

  function _onPlaylistVideoAction(btn) {
    const listId = btn.dataset.list;
    const videoId = btn.dataset.vid;
    const action = btn.dataset.action;
    const pl = _getPlaylists().find(p => p.list_id === listId);
    if (!pl) return;
    const v = pl.videos.find(x => x.video_id === videoId);
    if (!v) return;
    if (action === "play" && v.existing_hash) {
      window.location.href = `/player?hash=${encodeURIComponent(v.existing_hash)}`;
      return;
    }
    // Analyze — reuse _betaStartYoutube flow with the video's canonical URL.
    const canonical = `https://www.youtube.com/watch?v=${videoId}`;
    const urlInput = $("#betaYtUrl");
    const panel = $("#betaFabPanel");
    const backdrop = $("#betaFabBackdrop");
    if (!urlInput) return;
    urlInput.value = canonical;
    if (panel) { panel.classList.add("open"); panel.classList.remove("file-only"); }
    if (backdrop) backdrop.classList.add("open");
    _currentAnalyzingPlaylistVid = `${listId}|${videoId}`;
    if (typeof window._betaStartYoutube === "function") window._betaStartYoutube();
  }

  function _initBetaPlaylists() {
    _renderPlaylists();
  }

  // Homepage "+ 選取本機音檔" — multi-select; each file goes into the local
  // tracks list. List item click decides analyze vs play.
  function _initBetaLocalAudio() {
    const btn = $("#betaBrowseLocalBtn");
    const input = $("#betaLocalAudioInput");
    const section = $("#secBetaLocalTracks");
    if (!btn || !input || !section) return;
    section.style.display = "";
    _renderLocalTracks();
    btn.addEventListener("click", () => input.click());
    input.addEventListener("change", async () => {
      const files = Array.from(input.files || []);
      if (!files.length) return;
      for (const file of files) {
        if (file.size > 200 * 1024 * 1024) continue;
        await _addLocalTrackEntry(file);
      }
      _renderLocalTracks();
      input.value = "";  // re-pick same files allowed
    });
  }

  window._betaStartUpload = async function() {
    if (!_betaSelectedFile) return;
    const btn = $("#betaUploadBtn");
    btn.disabled = true;
    const prog = $("#betaUploadProgress");
    const fill = $("#betaProgressFill");
    const text = $("#betaProgressText");
    const pct = $("#betaProgressPct");
    prog.style.display = "";
    // Hide drop-zone + YT URL row while analyzing — reduces modal clutter
    // and prevents a second concurrent submit mid-run.
    $("#betaFabPanel")?.classList.add("analyzing");
    fill.style.width = "10%";
    text.textContent = _t("home.progress.uploading");
    pct.textContent = "10%";

    try {
      const form = new FormData();
      form.append("file", _betaSelectedFile);
      const res = await fetch("/api/process/upload", { method: "POST", body: form });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || res.statusText);
      }
      const data = await res.json();
      _betaPendingFiles[data.job_id] = _betaSelectedFile;
      fill.style.width = "30%";
      text.textContent = _t("home.progress.queued_analyzing");
      pct.textContent = "30%";
      _betaPollJob(data.job_id, fill, text, pct);
    } catch (e) {
      text.textContent = _t("home.progress.failed_prefix") + e.message;
      fill.style.width = "0%";
      btn.disabled = false;
      // Unhide inputs so the user can retry with a different file/URL.
      $("#betaFabPanel")?.classList.remove("analyzing");
    }
    _betaSelectedFile = null;
    $("#betaFileInfo").style.display = "none";
    $("#betaFileInput").value = "";
  };

  window._betaStartYoutube = async function() {
    const input = $("#betaYtUrl");
    const url = input.value.trim();
    if (!url) return;
    const btn = $("#betaYtBtn");
    btn.disabled = true;
    const prog = $("#betaYtProgress");
    const fill = $("#betaYtFill");
    const text = $("#betaYtText");
    const pct = $("#betaYtPct");
    prog.style.display = "";
    // Hide drop-zone + YT URL row while analyzing — reduces modal clutter
    // and prevents a second concurrent submit mid-run.
    $("#betaFabPanel")?.classList.add("analyzing");
    fill.style.width = "10%";
    text.textContent = _t("home.progress.submitting");
    pct.textContent = "10%";

    try {
      const res = await fetch("/api/process/youtube", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || res.statusText);
      }
      const data = await res.json();
      if (data.status === "done" && data.result_hash) {
        // Already analyzed — go straight to player
        fill.style.width = "100%";
        text.textContent = _t("home.progress.already_done_opening");
        pct.textContent = "100%";
        input.value = "";
        setTimeout(() => {
          window.location.href = `/player?hash=${encodeURIComponent(data.result_hash)}`;
        }, 500);
        return;
      }
      fill.style.width = "20%";
      text.textContent = _t("home.progress.queued_analyzing");
      pct.textContent = "20%";
      _betaPollJob(data.job_id, fill, text, pct);
      input.value = "";
    } catch (e) {
      text.textContent = _t("home.progress.failed_prefix") + e.message;
      fill.style.width = "0%";
      // Unhide inputs so the user can retry with a different URL.
      $("#betaFabPanel")?.classList.remove("analyzing");
    }
    btn.disabled = false;
  };

  function _betaPollJob(jobId, fill, statusText, pctText) {
    // Seed with the current visual width (already set by caller to 20–30%) so the
    // bar never jumps backwards when the first poll returns a lower backend value.
    let maxProgress = fill ? parseInt((fill.style.width || "0").replace("%", "")) || 0 : 0;
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`/api/process/status/${jobId}`);
        if (!res.ok) { clearInterval(timer); return; }
        const d = await res.json();
        maxProgress = Math.max(maxProgress, d.progress);
        if (fill) fill.style.width = maxProgress + "%";
        if (pctText) pctText.textContent = maxProgress + "%";
        // 細分狀態標籤：有 stage 時以 stage 為準，否則 fallback 到 status
        const labels = {
          queued: _t("home.status.queued"),
          processing: _t("home.status.processing"),
          done: _t("home.status.done"),
          error: _t("home.status.error"),
        };
        if (statusText) {
          const txt = (d.stage && d.status === "processing") ? d.stage : (labels[d.status] || d.status);
          statusText.textContent = txt;
        }

        if (d.status === "done" && d.result_hash) {
          clearInterval(timer);
          // Store audio blob in IndexedDB for auto-play
          const pendingFile = _betaPendingFiles[jobId];
          if (pendingFile) {
            await audioDBStore(d.result_hash, pendingFile);
            delete _betaPendingFiles[jobId];
          }
          // Flag this hash as freshly-analyzed so the player can show a
          // "旋律擷取中" banner while the melody worker finishes in the background.
          try { sessionStorage.setItem("livechord_fresh_hash", `${d.result_hash}|${Date.now()}`); } catch {}
          // If this job came from a local-tracks-list entry, stamp the
          // analyzedHash back onto it so next click goes straight to play.
          if (_currentAnalyzingLocalId) {
            _markLocalTrackAnalyzed(_currentAnalyzingLocalId, d.result_hash);
            _currentAnalyzingLocalId = null;
          }
          // Same for playlist videos — mark existing_hash so the card's list
          // shows ▶ 播放 next time user opens it.
          if (_currentAnalyzingPlaylistVid) {
            const [lid, vid] = _currentAnalyzingPlaylistVid.split("|");
            _markPlaylistVideoAnalyzed(lid, vid, d.result_hash);
            _currentAnalyzingPlaylistVid = null;
          }
          // Navigate to player
          setTimeout(() => {
            window.location.href = `/player?hash=${encodeURIComponent(d.result_hash)}`;
          }, 500);
        } else if (d.status === "error") {
          clearInterval(timer);
          if (statusText) statusText.textContent = _t("home.progress.failed_prefix") + (d.error || "Unknown");
          $("#betaUploadBtn") && ($("#betaUploadBtn").disabled = false);
          $("#betaYtBtn") && ($("#betaYtBtn").disabled = false);
          // Unhide inputs so the user can retry.
          $("#betaFabPanel")?.classList.remove("analyzing");
        }
      } catch (e) {}
    }, POLL_MS);
  }

  // ---- beta history ----
  function _buildCoverHtml(h) {
    const isYT = h.source_type === "youtube";
    const _extId = typeof extractYouTubeId === "function" ? extractYouTubeId
      : (u) => { const m = (u||"").match(/(?:v=|youtu\.be\/|\/shorts\/)([A-Za-z0-9_-]{11})/); return m ? m[1] : null; };
    const videoId = isYT ? _extId(h.youtube_url || "") : null;
    const imgSrc = videoId
      ? `https://img.youtube.com/vi/${videoId}/mqdefault.jpg`
      : `/api/process/cover/${escapeHtml(h.result_hash)}`;
    return `<img class="cover" src="${imgSrc}" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" alt="">
      <div class="cover-placeholder" style="display:none">&#x1F3B5;</div>`;
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

      // Also fetch NAS library recent plays and merge into the recent section
      let recentItems = [];
      try {
        const recentData = await API.getRecent();
        recentItems = (recentData.recent || []).map(r => {
          // __hash/ 項目是處理結果（YouTube / 上傳分析），當成 hash-card 處理，不走 library path
          if (typeof r.path === "string" && r.path.startsWith("__hash/")) {
            return {
              _isLibrary: false,
              result_hash: r.path.slice(7),
              title: (r.title || _t("home.label.analysis_result")),
              youtube_url: r.youtube_url || "",
              source_type: r.youtube_url ? "youtube" : "upload",
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

      // Merge: process history first, then NAS library (deduplicate by title)
      const seenTitles = new Set(items.map(h => (h.title || "").toLowerCase()));
      const mergedRecent = [...items];
      for (const r of recentItems) {
        if (!seenTitles.has(r.title.toLowerCase())) {
          mergedRecent.push(r);
          seenTitles.add(r.title.toLowerCase());
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
          return `<div class="grid-item" data-hash="${escapeHtml(h.result_hash)}" style="cursor:pointer">
            ${_buildCoverHtml(h)}
            <div class="info">
              <div class="title">${escapeHtml(title)}</div>
              ${getDifficultyHtml(h)}
            </div>
          </div>`;
        }).join("");
        recentContainer.querySelectorAll(".grid-item").forEach(el => {
          if (el.dataset.path) el.addEventListener("click", () => goPlayer(el.dataset.path));
          else el.addEventListener("click", () => goPlayer("", el.dataset.hash));
        });
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
          return `<div class="grid-item" data-hash="${escapeHtml(h.result_hash)}" style="cursor:pointer">
            ${_buildCoverHtml(h)}
            <div class="info">
              <div class="title">${escapeHtml(title)}</div>
              ${getDifficultyHtml(h)}
            </div>
          </div>`;
        }).join("");
        grid.querySelectorAll(".grid-item").forEach(el => {
          el.addEventListener("click", () => goPlayer("", el.dataset.hash));
        });
      }
    } catch (e) {
      console.error("loadBetaHistory error:", e);
    }
  }

  // ---- dashboard init ----

  async function initDashboard() {
    // Check beta access first
    await _checkBetaAccess();

    // Parallel loading avoids blocking the UI
    try {
      showLoading(true);
      const tasks = [];
      if (_isBetaNonAdmin) {
        _initBetaUpload();
        _initBetaLocalAudio();
        _initBetaPlaylists();
        // _loadBetaHistory fills both #historyGrid (now null, guarded) AND
        // #betaRecentList (最近播放) — must keep it for the recent list.
        tasks.push(_loadBetaHistory(), loadFavorites());
      } else {
        // Admin path. NAS browse is personal-only — skip in beta/public to
        // avoid /api/browse 404 spam (the section is already hidden by
        // _checkBetaAccess but the fetch was still firing here).
        tasks.push(loadRecent(), loadFavorites());
        if (!_isBetaMode) tasks.push(browse(currentPath));
        if (_isBetaMode) tasks.push(_loadBetaHistory());
      }
      await Promise.allSettled(tasks);
    } finally {
      showLoading(false);
    }

    // Handle ?youtube=<url> (from Web Share Target / bookmark): open FAB + prefill + submit
    if (_isBetaMode) {
      const params = new URLSearchParams(window.location.search);
      const ytParam = params.get("youtube");
      if (ytParam) {
        _autoStartYoutubeFromParam(ytParam);
      }
    }

    // bfcache refresh — mobile browsers snapshot this page when the user
    // navigates into the player; pressing Back restores the snapshot instantly
    // (no init re-run), so a song the user just analyzed+played would NOT
    // show up in 最近播放 until some other trigger reflowed the list. Rerun
    // the beta history fetch whenever we're restored from bfcache.
    window.addEventListener("pageshow", (ev) => {
      if (!ev.persisted) return;  // normal reload/forward — init already ran
      if (_isBetaMode) _loadBetaHistory();
      else if (typeof loadRecent === "function") loadRecent();
    });
  }

  function _autoStartYoutubeFromParam(url) {
    const tryOpen = () => {
      const panel = $("#betaFabPanel");
      const input = $("#betaYtUrl");
      if (!input) return false;
      if (panel) { panel.classList.add("open"); panel.classList.remove("file-only"); }
      input.value = url;
      if (typeof window._betaStartYoutube === "function") {
        window._betaStartYoutube();
      }
      // Strip the param so reload doesn't re-trigger
      const clean = window.location.pathname;
      window.history.replaceState({}, "", clean);
      return true;
    };
    if (!tryOpen()) {
      // Panel may not be wired yet (admin path) — retry once after a tick
      setTimeout(tryOpen, 300);
    }
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

  // Shared between input (URL preview) and keydown (Enter fast path).
  const _YT_URL_RE = /^https?:\/\/((www|m)\.)?(youtube\.com\/(watch|shorts)|youtu\.be\/|music\.youtube\.com\/watch)/i;
  const _YT_PLAYLIST_RE = /[?&]list=([A-Za-z0-9_-]+)/;
  function _isYtPlaylistUrl(s) {
    return _YT_URL_RE.test(s) && _YT_PLAYLIST_RE.test(s);
  }
  function _searchTriggerUrlAnalyze(url) {
    searchResults.classList.remove("show");
    const panel = $("#betaFabPanel");
    const backdrop = $("#betaFabBackdrop");
    const urlInput = $("#betaYtUrl");
    if (panel) { panel.classList.add("open"); panel.classList.remove("file-only"); }
    if (backdrop) backdrop.classList.add("open");
    if (urlInput) {
      urlInput.value = url;
      searchInput.value = "";
      if (typeof window._betaStartYoutube === "function") window._betaStartYoutube();
    }
  }

  searchInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    const q = searchInput.value.trim();
    if (q.length < 1) {
      searchResults.classList.remove("show");
      return;
    }
    // Paste of a YouTube URL — don't hit /api/search (it would return "找不到結果"
    // and confuse the user). Distinguish single-video vs playlist URL.
    if (_isBetaMode && _YT_URL_RE.test(q)) {
      const isPlaylist = _isYtPlaylistUrl(q);
      const msg = isPlaylist ? _t("home.search.yt_playlist_detected") : _t("home.search.yt_url_detected");
      const btnLabel = isPlaylist ? _t("home.search.yt_playlist_btn") : _t("home.search.yt_url_btn");
      searchResults.innerHTML = `
        <div class="search-empty">
          <div class="search-empty-msg">${msg}</div>
          <button id="searchAnalyzeUrlBtn" class="search-empty-btn">${btnLabel}</button>
        </div>`;
      searchResults.classList.add("show");
      const btn = document.getElementById("searchAnalyzeUrlBtn");
      if (btn) btn.addEventListener("click", (e) => {
        e.stopPropagation();
        if (isPlaylist) _onAddPlaylistFromSearch(q);
        else _searchTriggerUrlAnalyze(q);
      });
      return;
    }
    searchTimer = setTimeout(() => doSearch(q), 300);
  });

  async function _onAddPlaylistFromSearch(url) {
    searchResults.classList.remove("show");
    searchInput.value = "";
    await _addPlaylist(url);
  }

  searchInput.addEventListener("focus", () => {
    if (searchResults.children.length > 0) searchResults.classList.add("show");
  });

  // Enter on the search box. Paths:
  //   (a) value is a YouTube URL → analyze immediately (beta admin + non-admin)
  //   (b) beta non-admin text with no match → open add-song modal empty
  //   (c) otherwise → standard browser form submit (no-op)
  searchInput.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const raw = searchInput.value.trim();
    if (_isBetaMode && _YT_URL_RE.test(raw)) {
      e.preventDefault();
      if (_isYtPlaylistUrl(raw)) _onAddPlaylistFromSearch(raw);
      else _searchTriggerUrlAnalyze(raw);
      return;
    }
    if (!_isBetaNonAdmin) return;
    e.preventDefault();
    searchResults.classList.remove("show");
    const panel = $("#betaFabPanel");
    const backdrop = $("#betaFabBackdrop");
    const urlInput = $("#betaYtUrl");
    if (panel) { panel.classList.add("open"); panel.classList.remove("file-only"); }
    if (backdrop) backdrop.classList.add("open");
    if (urlInput) setTimeout(() => urlInput.focus(), 50);
  });

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".search-box")) searchResults.classList.remove("show");
  });

  // Library search renders immediately; YouTube search runs in parallel
  // and patches its own section in when ready (yt-dlp takes 3-5 s, library
  // /api/search is sub-200 ms — don't block one on the other).
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

  function _renderYtSectionHtml(results) {
    if (!results || results.length === 0) return "";
    let html = `<div class="search-section-header">${_t("home.search.yt_section_title")}</div>`;
    for (const r of results) {
      const dur = r.duration ? `<span class="yt-r-dur">${escapeHtml(r.duration)}</span>` : "";
      html += `
        <div class="result-item yt-result" data-yt-url="${escapeHtml(r.url)}">
          <img class="r-cover yt-thumb" src="${escapeHtml(r.thumbnail_url)}" onerror="this.style.display='none'" loading="lazy" alt="">
          <div class="r-info">
            <div class="r-title">${escapeHtml(r.title)}${dur}</div>
            <div class="r-artist">${escapeHtml(r.channel || "")} <span class="yt-r-source">YouTube</span></div>
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
        const urlInput = $("#betaYtUrl");
        if (urlInput) setTimeout(() => urlInput.focus(), 50);
      });
    } else {
      searchResults.innerHTML = `<div style="padding:12px;color:var(--text-dim)">${_t("home.search.no_results")}</div>`;
    }
  }

  function _bindYtClicks() {
    searchResults.querySelectorAll(".result-item.yt-result").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        const url = el.getAttribute("data-yt-url");
        searchResults.classList.remove("show");
        if (url && typeof _searchTriggerUrlAnalyze === "function") {
          _searchTriggerUrlAnalyze(url);
        }
      });
    });
  }

  function _bindLibraryClicks() {
    searchResults.querySelectorAll(".result-item:not(.yt-result)").forEach((el) => {
      el.addEventListener("click", () => {
        searchResults.classList.remove("show");
        goPlayer(el.dataset.path, el.dataset.hash || "");
      });
    });
  }

  // Tracks the currently in-flight query so a stale slow YT response from a
  // previous keystroke doesn't paint over fresher results.
  let _searchEpoch = 0;

  async function doSearch(q) {
    const epoch = ++_searchEpoch;

    // Fire library search and YouTube search in parallel. Library typically
    // resolves first; we render it immediately + show a YT placeholder. YT
    // results stream in afterward.
    const libP = API.search(q).catch(err => ({ error: String(err).slice(0, 200) }));
    const ytP = API.youtubeSearchList(q, 5).catch(() => ({ results: [] }));

    const lib = await libP;
    if (epoch !== _searchEpoch) return; // user typed something else; bail

    if (lib && lib.error) {
      searchResults.innerHTML = `<div style="padding:12px;color:var(--text-dim)">${escapeHtml(lib.error)}</div>`;
      searchResults.classList.add("show");
      return;
    }
    const libResults = (lib && lib.results) || [];

    let baseHtml = "";
    if (libResults.length > 0) {
      baseHtml += _renderLibraryHtml(libResults);
    }
    // Always reserve a YT placeholder so users see we're looking
    baseHtml += `<div class="yt-section-slot"><div class="search-section-header">${_t("home.search.yt_section_title")}</div><div class="yt-loading">${_t("home.search.yt_searching")}</div></div>`;
    searchResults.innerHTML = baseHtml;
    searchResults.classList.add("show");
    _bindLibraryClicks();

    // Wait for YT, then patch the placeholder slot
    const yt = await ytP;
    if (epoch !== _searchEpoch) return;

    const ytResults = (yt && yt.results) || [];
    const slot = searchResults.querySelector(".yt-section-slot");
    if (!slot) return;

    if (libResults.length === 0 && ytResults.length === 0) {
      _renderEmptyState(q);
      return;
    }
    if (ytResults.length === 0) {
      slot.remove();
    } else {
      slot.outerHTML = _renderYtSectionHtml(ytResults);
      _bindYtClicks();
    }
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
          if (cd.exists) hashTitles[hash] = { title: cd.title || hash, youtube_url: cd.youtube_url || "" };
        } catch {}
      }

      let html = '';
      data.favorites.forEach((f) => {
        const isHash = f.path.startsWith("__hash/");
        const hash = isHash ? f.path.replace("__hash/", "") : "";
        if (isHash) {
          const info = hashTitles[hash] || { title: hash, youtube_url: "" };
          const _extId = typeof extractYouTubeId === "function" ? extractYouTubeId
            : (u) => { const m = (u||"").match(/(?:v=|youtu\.be\/|\/shorts\/)([A-Za-z0-9_-]{11})/); return m ? m[1] : null; };
          const vid = _extId(info.youtube_url);
          const coverHtml = vid
            ? `<img class="cover" src="https://img.youtube.com/vi/${vid}/mqdefault.jpg" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" alt="">`
            : `<img class="cover" src="/api/process/cover/${escapeHtml(hash)}" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" alt="">`;
          html += `
            <div class="grid-item" data-hash="${escapeHtml(hash)}">
              ${coverHtml}
              <div class="cover-placeholder" style="display:none">&#x1F3B5;</div>
              <div class="info"><div class="title">${escapeHtml(info.title)}</div></div>
            </div>`;
        } else {
          const name = f.path.split("/").pop().replace(/\.flac$/i, "");
          const coverUrl = API.trackCoverUrl(f.path);
          html += `
            <div class="grid-item" data-path="${escapeHtml(f.path)}">
              <img class="cover" src="${coverUrl}" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" alt="">
              <div class="cover-placeholder" style="display:none">&#x1F3B5;</div>
              <div class="info">
                <div class="title">${escapeHtml(name)}</div>
                ${getDifficultyHtml(f)}
              </div>
            </div>`;
        }
      });
      container.innerHTML = html;
      container.querySelectorAll(".grid-item").forEach((el) => {
        el.addEventListener("click", () => {
          if (el.dataset.hash) goPlayer("", el.dataset.hash);
          else goPlayer(el.dataset.path);
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

      const _extId = (u) => { const m = (u||"").match(/(?:v=|youtu\.be\/|\/shorts\/)([A-Za-z0-9_-]{11})/); return m ? m[1] : null; };

      let html = '';
      data.recent.forEach((r) => {
        const isHash = typeof r.path === "string" && r.path.startsWith("__hash/");
        if (isHash) {
          const hash = r.path.slice(7);
          const vid = _extId(r.youtube_url || "");
          const coverUrl = vid
            ? `https://img.youtube.com/vi/${vid}/mqdefault.jpg`
            : `/api/process/cover/${encodeURIComponent(hash)}`;
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
