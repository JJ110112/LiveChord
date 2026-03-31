/** LiveChord 首頁 — 瀏覽、搜尋、最愛、最近播放 */

(function () {
  // ---- state ----
  let currentPath = "";
  let currentTab = "recent";
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

  // ---- tabs ----

  $$(".tabs button").forEach((btn) => {
    btn.addEventListener("click", () => {
      $$(".tabs button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentTab = btn.dataset.tab;
      showTab(currentTab);
    });
  });

  function showTab(tab) {
    $("#tabBrowse").style.display = tab === "browse" ? "" : "none";
    $("#tabFavorites").style.display = tab === "favorites" ? "" : "none";
    $("#tabRecent").style.display = tab === "recent" ? "" : "none";

    if (tab === "browse") browse(currentPath);
    if (tab === "favorites") loadFavorites();
    if (tab === "recent") loadRecent();
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
      html += `
        <div class="grid-item" data-path="${escapeHtml(f.path)}">
          <div class="cover-placeholder">&#x1F3B5;</div>
          <div class="info">
            <div class="title">${escapeHtml(f.name.replace(/\.flac$/i, ""))}</div>
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
      html += `
        <li data-path="${escapeHtml(f.path)}">
          <span class="track-num">${i + 1}</span>
          <span class="track-title">${escapeHtml(name)}</span>
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
              <div class="r-title">${escapeHtml(r.title || r.path.split("/").pop())}</div>
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
    const container = $("#favList");
    try {
      const data = await API.getFavorites();
      if (!data.favorites || data.favorites.length === 0) {
        container.innerHTML = `<div class="empty"><div class="icon">&#x2764;</div><div class="msg">尚無最愛歌曲</div></div>`;
        return;
      }
      let html = '<ul class="track-list">';
      data.favorites.forEach((f, i) => {
        const name = f.path.split("/").pop().replace(/\.flac$/i, "");
        html += `
          <li data-path="${escapeHtml(f.path)}">
            <span class="track-num">${i + 1}</span>
            <span class="track-title">${escapeHtml(name)}</span>
          </li>`;
      });
      html += "</ul>";
      container.innerHTML = html;
      container.querySelectorAll("li").forEach((li) => {
        li.addEventListener("click", () => goPlayer(li.dataset.path));
      });
    } catch (err) {
      container.innerHTML = `<div class="empty"><div class="msg">${escapeHtml(err.message)}</div></div>`;
    }
  }

  // ---- recent ----

  async function loadRecent() {
    const container = $("#recentList");
    try {
      const data = await API.getRecent();
      if (!data.recent || data.recent.length === 0) {
        container.innerHTML = `<div class="empty"><div class="icon">&#x1F552;</div><div class="msg">尚無播放紀錄</div></div>`;
        return;
      }
      let html = '<ul class="track-list">';
      data.recent.forEach((r, i) => {
        const name = r.path.split("/").pop().replace(/\.flac$/i, "");
        html += `
          <li data-path="${escapeHtml(r.path)}">
            <span class="track-num">${i + 1}</span>
            <span class="track-title">${escapeHtml(name)}</span>
          </li>`;
      });
      html += "</ul>";
      container.innerHTML = html;
      container.querySelectorAll("li").forEach((li) => {
        li.addEventListener("click", () => goPlayer(li.dataset.path));
      });
    } catch (err) {
      container.innerHTML = `<div class="empty"><div class="msg">${escapeHtml(err.message)}</div></div>`;
    }
  }

  // ---- init ----
  showTab(currentTab);
})();
