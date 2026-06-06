/** LiveChord — Jam Tracks 風格曲集 */

(function () {
  const section = document.getElementById("secJamTracks");
  const styleSelect = document.getElementById("jamStyleSelect");
  const countEl = document.getElementById("jamStyleCount");
  const listEl = document.getElementById("jamTracksList");
  const playSeq = document.getElementById("jamPlaySeq");
  const playShuffle = document.getElementById("jamPlayShuffle");
  if (!section || !styleSelect || !listEl) return;

  const LS_KEY = "livechord_jam_style";

  function _t(k, v) {
    return (window.LiveChordI18n && window.LiveChordI18n.t)
      ? window.LiveChordI18n.t(k, v)
      : k;
  }

  function _seed(style, mode) {
    return `${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}_jam_${mode}_${style || ""}`;
  }

  function _queue(style, mode, seed) {
    return {
      source: "jam",
      mode,
      seed: seed || _seed(style, mode),
      style: style || "",
      label: style || "Jam",
    };
  }

  function _appendQueueParams(qs, queue) {
    qs.set("queue", queue.source);
    qs.set("queue_mode", queue.mode || "sequential");
    qs.set("queue_seed", queue.seed || "");
    if (queue.style) qs.set("queue_style", queue.style);
    if (queue.label) qs.set("queue_label", queue.label);
  }

  function goPlayer(path, queue) {
    const qs = new URLSearchParams({ path, autoplay: "1" });
    if (queue) _appendQueueParams(qs, queue);
    window.location.href = `/player?${qs.toString()}`;
  }

  async function playStyle(mode) {
    const style = styleSelect.value || "";
    const queue = _queue(style, mode);
    try {
      const data = await API.playlist(queue);
      const tracks = data.tracks || [];
      if (!tracks.length) {
        showToast(_t("toast.queue.empty"), 2500);
        return;
      }
      queue.seed = data.seed || queue.seed;
      goPlayer(tracks[0].path, queue);
    } catch (err) {
      const msg = err && err.message ? err.message : String(err || "");
      showToast(_t("toast.queue.failed", { err: msg }), 3500);
    }
  }

  function renderTracks(tracks) {
    if (!tracks.length) {
      listEl.innerHTML = `<div class="empty" style="padding:20px;color:var(--text-dim)">${escapeHtml(_t("home.jam.empty"))}</div>`;
      return;
    }
    let html = "";
    for (const t of tracks) {
      const coverUrl = API.trackCoverUrl(t.path);
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
      el.addEventListener("click", () => goPlayer(el.dataset.path, _queue(styleSelect.value || "", "sequential")));
    });
  }

  async function loadStyleTracks(style) {
    listEl.innerHTML = `<div class="empty" style="padding:20px;color:var(--text-dim)">${escapeHtml(_t("common.loading"))}</div>`;
    try {
      const data = await API.jamTracksList(style, 100, 0);
      countEl.textContent = _t("home.jam.count", { n: data.total });
      renderTracks(data.tracks || []);
    } catch (err) {
      listEl.innerHTML = `<div class="empty" style="padding:20px;color:var(--text-dim)">${escapeHtml(_t("home.jam.failed", { err: err.message }))}</div>`;
    }
  }

  async function init() {
    try {
      const cfg = await fetch("/api/config/public").then((r) => r.json()).catch(() => ({}));
      if (cfg.deployment_mode && cfg.deployment_mode !== "personal") return;

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
      if (playSeq) playSeq.addEventListener("click", (e) => {
        e.preventDefault();
        playStyle("sequential");
      });
      if (playShuffle) playShuffle.addEventListener("click", (e) => {
        e.preventDefault();
        playStyle("shuffle");
      });

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
