/** LiveChord — Jam Tracks 風格曲集 */

(function () {
  const section = document.getElementById("secJamTracks");
  const styleSelect = document.getElementById("jamStyleSelect");
  const countEl = document.getElementById("jamStyleCount");
  const listEl = document.getElementById("jamTracksList");
  if (!section || !styleSelect || !listEl) return;

  const LS_KEY = "livechord_jam_style";

  function goPlayer(path) {
    const clean = path.startsWith("@1/") ? path.slice(3) : path;
    window.location.href = `/player?path=${encodeURIComponent(clean)}&autoplay=1`;
  }

  function renderTracks(tracks) {
    if (!tracks.length) {
      listEl.innerHTML = `<div class="empty" style="padding:20px;color:var(--text-dim)">沒有曲目</div>`;
      return;
    }
    let html = "";
    for (const t of tracks) {
      const clean = t.path.startsWith("@1/") ? t.path.slice(3) : t.path;
      const coverUrl = API.trackCoverUrl(clean);
      const title = t.title || t.path.split("/").pop().replace(/\.flac$/i, "");
      const artist = t.artist || "";
      html += `
        <div class="grid-item jam-track-item" data-path="${escapeHtml(t.path)}" title="${escapeHtml(t.style)} · score ${t.score}">
          <img class="cover" src="${coverUrl}" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" alt="">
          <div class="cover-placeholder" style="display:none">&#x1F3B5;</div>
          <div class="info">
            <div class="title">${escapeHtml(title)}</div>
            ${artist ? `<div class="subtitle" style="font-size:0.75em;opacity:0.6">${escapeHtml(artist)}</div>` : ""}
          </div>
        </div>`;
    }
    listEl.innerHTML = html;
    listEl.querySelectorAll(".jam-track-item").forEach((el) => {
      el.addEventListener("click", () => goPlayer(el.dataset.path));
    });
  }

  async function loadStyleTracks(style) {
    listEl.innerHTML = `<div class="empty" style="padding:20px;color:var(--text-dim)">載入中…</div>`;
    try {
      const data = await API.jamTracksList(style, 100, 0);
      countEl.textContent = `${data.total} 首`;
      renderTracks(data.tracks || []);
    } catch (err) {
      listEl.innerHTML = `<div class="empty" style="padding:20px;color:var(--text-dim)">載入失敗：${escapeHtml(err.message)}</div>`;
    }
  }

  async function init() {
    try {
      const data = await API.jamTracksStyles();
      const styles = data.styles || [];
      if (!styles.length) return;

      section.style.display = "";
      styleSelect.innerHTML = styles
        .map((s) => `<option value="${escapeHtml(s.name)}">${escapeHtml(s.name)} (${s.count})</option>`)
        .join("");

      const saved = localStorage.getItem(LS_KEY);
      const initial = saved && styles.some((s) => s.name === saved) ? saved : styles[0].name;
      styleSelect.value = initial;

      styleSelect.addEventListener("change", () => {
        localStorage.setItem(LS_KEY, styleSelect.value);
        loadStyleTracks(styleSelect.value);
      });

      await loadStyleTracks(initial);
    } catch (err) {
      console.warn("Jam Tracks init failed:", err);
      section.style.display = "none";
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
