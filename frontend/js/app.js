/** LiveChord 首頁 — 瀏覽、搜尋、最愛、最近播放 */

(function () {
  // ---- state ----
  let currentPath = "";
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
    return ` <span class="difficulty" style="font-size:0.8em;opacity:0.6;margin-left:6px">${"⭐".repeat(stars)}${key ? " " + key : ""}</span>`;
  }

  // ---- beta mode: hide NAS-dependent sections for non-admin ----
  let _isBetaNonAdmin = false;
  let _isBetaMode = false;

  async function _checkBetaAccess() {
    try {
      const [cfgRes, adminRes] = await Promise.all([
        fetch("/api/config/public").then(r => r.json()),
        fetch("/api/auth/is_admin").then(r => r.json()),
      ]);
      _isBetaMode = cfgRes.deployment_mode === "beta";
      if (_isBetaMode && !adminRes.is_admin) {
        _isBetaNonAdmin = true;
        // Hide NAS-dependent sections (but keep favorites — works with __hash/ paths)
        const secBrowse = $("#secBrowse");
        const secRecent = $("#secRecent");
        if (secBrowse) secBrowse.style.display = "none";
        if (secRecent) secRecent.style.display = "none";
        // Show beta sections
        const secUpload = $("#secUpload");
        const secBetaRecent = $("#secBetaRecent");
        const secHistory = $("#secHistory");
        if (secUpload) secUpload.style.display = "";
        if (secBetaRecent) secBetaRecent.style.display = "";
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
    if (file.size > 200 * 1024 * 1024) { alert("檔案超過 200 MB 上限"); return; }
    _betaSelectedFile = file;
    $("#betaFileName").textContent = file.name;
    $("#betaFileSize").textContent = `(${(file.size / 1024 / 1024).toFixed(1)} MB)`;
    $("#betaFileInfo").style.display = "flex";
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
    fill.style.width = "10%";
    text.textContent = "上傳中...";
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
      text.textContent = "已排入佇列，分析中...";
      pct.textContent = "30%";
      _betaPollJob(data.job_id, fill, text, pct);
    } catch (e) {
      text.textContent = "失敗: " + e.message;
      fill.style.width = "0%";
      btn.disabled = false;
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
    fill.style.width = "10%";
    text.textContent = "提交中...";
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
      fill.style.width = "20%";
      text.textContent = "已排入佇列，分析中...";
      pct.textContent = "20%";
      _betaPollJob(data.job_id, fill, text, pct);
      input.value = "";
    } catch (e) {
      text.textContent = "失敗: " + e.message;
      fill.style.width = "0%";
    }
    btn.disabled = false;
  };

  function _betaPollJob(jobId, fill, statusText, pctText) {
    let maxProgress = 0;
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`/api/process/status/${jobId}`);
        if (!res.ok) { clearInterval(timer); return; }
        const d = await res.json();
        maxProgress = Math.max(maxProgress, d.progress);
        if (fill) fill.style.width = maxProgress + "%";
        if (pctText) pctText.textContent = maxProgress + "%";
        const labels = { queued: "排隊中", processing: "分析中", done: "完成！", error: "失敗" };
        if (statusText) statusText.textContent = labels[d.status] || d.status;

        if (d.status === "done" && d.result_hash) {
          clearInterval(timer);
          // Store audio blob in IndexedDB for auto-play
          const pendingFile = _betaPendingFiles[jobId];
          if (pendingFile) {
            await audioDBStore(d.result_hash, pendingFile);
            delete _betaPendingFiles[jobId];
          }
          // Navigate to player
          setTimeout(() => {
            window.location.href = `/player?hash=${encodeURIComponent(d.result_hash)}`;
          }, 500);
        } else if (d.status === "error") {
          clearInterval(timer);
          if (statusText) statusText.textContent = "失敗: " + (d.error || "Unknown");
          $("#betaUploadBtn") && ($("#betaUploadBtn").disabled = false);
          $("#betaYtBtn") && ($("#betaYtBtn").disabled = false);
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

      // Recent plays — show for non-admin (their only recent section), or when many items
      if (recentContainer && items.length > 0 && (_isBetaNonAdmin || items.length > 8)) {
        recentContainer.innerHTML = items.slice(0, 8).map(h => {
          const title = h.title || "分析結果";
          return `<div class="grid-item" data-hash="${escapeHtml(h.result_hash)}" style="cursor:pointer">
            ${_buildCoverHtml(h)}
            <div class="info">
              <div class="title">${escapeHtml(title)}</div>
              ${getDifficultyHtml(h)}
            </div>
          </div>`;
        }).join("");
        recentContainer.querySelectorAll(".grid-item").forEach(el => {
          el.addEventListener("click", () => goPlayer("", el.dataset.hash));
        });
        if (recentSection) recentSection.style.display = "";
      }

      // Library grid (all items)
      if (grid) {
        if (items.length === 0) {
          grid.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-dim)">尚無歌曲，上傳音檔或貼上 YouTube URL 開始分析</div>';
          return;
        }
        grid.innerHTML = items.map(h => {
          const title = h.title || "分析結果";
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
        tasks.push(_loadBetaHistory(), loadFavorites());
      } else {
        tasks.push(loadRecent(), loadFavorites(), browse(currentPath));
        if (_isBetaMode) tasks.push(_loadBetaHistory());
      }
      await Promise.allSettled(tasks);
    } finally {
      showLoading(false);
    }
  }

  // ---- browse ----

  async function browse(path) {
    currentPath = path;
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

    browseGrid.innerHTML = html || `<div class="empty"><div class="icon">&#x1F4C2;</div><div class="msg">空目錄</div></div>`;

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

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".search-box")) searchResults.classList.remove("show");
  });

  async function doSearch(q) {
    try {
      const data = await API.search(q);
      if (data.error) {
        searchResults.innerHTML = `<div style="padding:12px;color:var(--text-dim)">${escapeHtml(data.error)}</div>`;
        searchResults.classList.add("show");
        return;
      }
      if (data.results.length === 0) {
        searchResults.innerHTML = `<div style="padding:12px;color:var(--text-dim)">找不到結果</div>`;
        searchResults.classList.add("show");
        return;
      }
      let html = "";
      for (const r of data.results) {
        const hasHash = r.hash || r.path.startsWith("__hash/");
        const coverUrl = hasHash ? "" : API.trackCoverUrl(r.path);
        html += `
          <div class="result-item" data-path="${escapeHtml(r.path)}" ${r.hash ? `data-hash="${escapeHtml(r.hash)}"` : ""}>
            ${coverUrl ? `<img class="r-cover" src="${coverUrl}" onerror="this.style.display='none'" loading="lazy" alt="">` : ""}
            <div class="r-info">
              <div class="r-title">${escapeHtml(r.title || r.path.split("/").pop())}${getDifficultyHtml(r)}</div>
              <div class="r-artist">${escapeHtml(r.artist || "")} ${r.album ? "— " + escapeHtml(r.album) : ""}</div>
            </div>
          </div>`;
      }
      searchResults.innerHTML = html;
      searchResults.classList.add("show");
      searchResults.querySelectorAll(".result-item").forEach((el) => {
        el.addEventListener("click", () => {
          searchResults.classList.remove("show");
          goPlayer(el.dataset.path, el.dataset.hash || "");
        });
      });
    } catch (err) {
      console.error("search error", err);
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
      
      let html = '';
      data.recent.forEach((r, i) => {
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
        el.addEventListener("click", () => goPlayer(el.dataset.path));
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

  // 啟動 Dashboard (Lazy + Parallel)
  initDashboard();
})();
