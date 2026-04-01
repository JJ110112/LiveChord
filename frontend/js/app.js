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
  const toast = $("#toast");

  // ---- helpers ----

  function showToast(msg, ms = 2000) {
    toast.textContent = msg;
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), ms);
  }

  function showLoading(show) {
    loading.style.display = show ? "" : "none";
  }

  function formatDuration(sec) {
    if (!sec) return "";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s.toString().padStart(2, "0")}`;
  }

  function escapeHtml(str) {
    const d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
  }

  function goPlayer(path) {
    window.location.href = `/player?path=${encodeURIComponent(path)}`;
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

  // ---- dashboard init ----

  async function initDashboard() {
    // Parallel loading avoids blocking the UI
    try {
      showLoading(true);
      await Promise.allSettled([
        loadRecent(),
        loadFavorites(),
        browse(currentPath)
      ]);
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
        renderTrackList(files);
      } else {
        renderGrid(dirs, files);
      }
    } catch (err) {
      browseGrid.innerHTML = `<div class="empty"><div class="icon">&#x26A0;</div><div class="msg">${escapeHtml(err.message)}</div></div>`;
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
        const coverUrl = API.trackCoverUrl(r.path);
        html += `
          <div class="result-item" data-path="${escapeHtml(r.path)}">
            <img class="r-cover" src="${coverUrl}" onerror="this.style.display='none'" loading="lazy" alt="">
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
          goPlayer(el.dataset.path);
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
      
      let html = '';
      data.favorites.forEach((f, i) => {
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
      });
      container.innerHTML = html;
      container.querySelectorAll(".grid-item").forEach((el) => {
        el.addEventListener("click", () => goPlayer(el.dataset.path));
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

  // ---- 橫向捲動：滾輪 + 拖曳（含慣性）----
  function _initHorizontalScroll(el) {
    if (el.dataset.hscroll) return;
    el.dataset.hscroll = "1";

    // 滾輪→橫向
    el.addEventListener("wheel", (e) => {
      if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
        e.preventDefault();
        el.scrollLeft += e.deltaY;
      }
    }, { passive: false });

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
