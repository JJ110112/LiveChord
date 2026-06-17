// trends-explorer.js v1
// Interactive chord-progression "Trends" explorer for the homepage, modelled on
// Hooktheory's chordProgressionNode but driven by the NAS library.
//
// You start from a tonic chord (major "I" or minor "i") and the backend reports
// the probability of every chord that follows it across the whole library
// (/api/progression/trends, key-free / transposition-invariant). Click a
// candidate to extend the progression; the path is shown as a breadcrumb and the
// matching library songs (/api/progression/match) are rendered as cards below.
(function () {
  const ROOT = document.getElementById("secTrends");
  if (!ROOT) return;

  const COLLAPSE_KEY = "livechord_trends_collapsed";
  const SCALE_KEY = "livechord_trends_scale";
  const KEY_KEY = "livechord_trends_key";
  const MATCH_PAGE_SIZE = 12;

  // Relative degrees are shown as letter chord names anchored to the chosen key
  // (degree 0 = the tonic). The distribution itself is key-independent, so the
  // key only re-labels what's on screen — no refetch. Each key carries a
  // sharp/flat spelling preference so chromatic roots stay idiomatic. Mirrors
  // the KEYS table in progression-library.js.
  const KEYS = [
    { name: "C", pc: 0, sharp: false }, { name: "G", pc: 7, sharp: true },
    { name: "D", pc: 2, sharp: true }, { name: "A", pc: 9, sharp: true },
    { name: "E", pc: 4, sharp: true }, { name: "B", pc: 11, sharp: true },
    { name: "F#", pc: 6, sharp: true }, { name: "F", pc: 5, sharp: false },
    { name: "Bb", pc: 10, sharp: false }, { name: "Eb", pc: 3, sharp: false },
    { name: "Ab", pc: 8, sharp: false }, { name: "Db", pc: 1, sharp: false },
  ];
  // 固定顯示拼法（全降記號，唯 F# 升記號）— 和弦／音名文字一律採此拼法，不依調號升降。
  const FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];

  const state = {
    // path: ordered list of {deg, minor} relative to the start tonic (path[0].deg === 0).
    path: [{ deg: 0, minor: false }],
    scale: "major", // "major" | "minor" — the starting tonic quality
    keyName: "C", // display key (re-labels names only; data is key-independent)
    collapsed: true,
    initialized: false, // first trends fetch is lazy (on first expand)
    matchPage: 0,
    matchTotal: 0,
    lastNext: null, // last trends payload, so a key change can re-label instantly
  };

  const refs = {
    collapseBtn: document.getElementById("trendsCollapseBtn"),
    resetBtn: document.getElementById("trendsResetBtn"),
    body: document.getElementById("trendsBody"),
    scaleMajor: document.getElementById("trendsScaleMajor"),
    scaleMinor: document.getElementById("trendsScaleMinor"),
    key: document.getElementById("trendsKey"),
    breadcrumb: document.getElementById("trendsBreadcrumb"),
    next: document.getElementById("trendsNext"),
    nextHead: document.getElementById("trendsNextHead"),
    matches: document.getElementById("trendsMatches"),
    matchCount: document.getElementById("trendsMatchCount"),
    pager: document.getElementById("trendsPager"),
  };

  let _trendsToken = 0;
  let _matchToken = 0;

  function init() {
    hydrate();
    populateKey();
    bindEvents();
    syncScaleUi();
    syncCollapseUi();
    // Only fetch once the section is open — keeps the homepage load free of the
    // corpus scan for users who never expand Trends.
    if (!state.collapsed) ensureInitialized();
  }

  function bindEvents() {
    if (refs.collapseBtn) {
      refs.collapseBtn.addEventListener("click", () => {
        state.collapsed = !state.collapsed;
        persistCollapsed();
        syncCollapseUi();
        if (!state.collapsed) ensureInitialized();
      });
    }
    if (refs.resetBtn) refs.resetBtn.addEventListener("click", resetPath);
    if (refs.scaleMajor) refs.scaleMajor.addEventListener("click", () => setScale("major"));
    if (refs.scaleMinor) refs.scaleMinor.addEventListener("click", () => setScale("minor"));
    if (refs.key) refs.key.addEventListener("change", () => setKey(refs.key.value));
  }

  function populateKey() {
    if (!refs.key) return;
    refs.key.innerHTML = KEYS.map((k) => `<option value="${k.name}">${k.name}</option>`).join("");
    refs.key.value = state.keyName;
  }

  function currentKey() {
    return KEYS.find((k) => k.name === state.keyName) || KEYS[0];
  }

  // Key changes only re-label the displayed names — the underlying distribution
  // and song matches are key-independent, so there's no refetch.
  function setKey(name) {
    if (!KEYS.some((k) => k.name === name)) return;
    state.keyName = name;
    persistKey();
    syncScaleUi();
    renderBreadcrumb();
    if (state.lastNext) renderNext(state.lastNext);
  }

  function ensureInitialized() {
    if (state.initialized) return;
    state.initialized = true;
    refresh();
  }

  // ---- path manipulation ----
  function setScale(scale) {
    if (state.scale === scale && state.path.length === 1) return;
    state.scale = scale;
    state.path = [{ deg: 0, minor: scale === "minor" }];
    persistScale();
    syncScaleUi();
    refresh();
  }

  function resetPath() {
    state.path = [{ deg: 0, minor: state.scale === "minor" }];
    refresh();
  }

  function appendChord(deg, minor) {
    state.path.push({ deg: ((deg % 12) + 12) % 12, minor: !!minor });
    refresh();
  }

  function truncatePath(len) {
    if (len < 1 || len >= state.path.length) return;
    state.path = state.path.slice(0, len);
    refresh();
  }

  // ---- serialization (matches backend _parse_seq) ----
  function pathToSeq() {
    return state.path.map((c) => `${c.deg}${c.minor ? "m" : "M"}`).join("-");
  }

  function chordName(deg, minor) {
    const k = currentKey();
    const pc = (k.pc + ((deg % 12) + 12) % 12) % 12;
    const base = FLAT_NAMES[pc] || "?";
    return minor ? base + "m" : base;
  }

  // ---- main refresh: redraw breadcrumb, then fetch distribution + songs ----
  function refresh() {
    renderBreadcrumb();
    fetchTrends();
    state.matchPage = 0;
    fetchMatches();
  }

  function renderBreadcrumb() {
    const chips = state.path.map((c, idx) => {
      const last = idx === state.path.length - 1;
      return `<button class="trends-crumb ${last ? "is-active" : ""}" type="button" data-len="${idx + 1}" title="回到此步">${escapeHtml(chordName(c.deg, c.minor))}</button>`;
    });
    refs.breadcrumb.innerHTML = chips.join('<span class="trends-arrow">→</span>');
    refs.breadcrumb.querySelectorAll(".trends-crumb").forEach((b) => {
      b.addEventListener("click", () => truncatePath(Number(b.dataset.len)));
    });
  }

  // ---- next-chord distribution ----
  async function fetchTrends() {
    const token = ++_trendsToken;
    const seq = pathToSeq();
    state.lastNext = null; // path changed — old distribution no longer applies
    refs.next.innerHTML = `<div class="trends-empty">分析中…</div>`;
    if (refs.nextHead) refs.nextHead.textContent = "接下來最可能的和弦";
    try {
      const res = await fetch(`/api/progression/trends?seq=${encodeURIComponent(seq)}`);
      if (!res.ok) throw new Error(String(res.status));
      const data = await res.json();
      if (token !== _trendsToken) return;
      // No index on this deployment (e.g. the public VPS) → hide the whole section.
      if (!data.indexed) {
        ROOT.style.display = "none";
        return;
      }
      renderNext(data);
    } catch (e) {
      if (token !== _trendsToken) return;
      refs.next.innerHTML = `<div class="trends-empty">此伺服器未提供趨勢資料。</div>`;
    }
  }

  function renderNext(data) {
    state.lastNext = data; // keep for instant re-label on key change
    const list = (data && data.next) || [];
    if (refs.nextHead) {
      refs.nextHead.textContent = data.total
        ? `接下來最可能的和弦（依 ${data.total.toLocaleString()} 次進行統計）`
        : "接下來最可能的和弦";
    }
    if (!list.length) {
      refs.next.innerHTML = `<div class="trends-empty">音樂庫中沒有接續此進行的資料 — 試試別的起點或回到上一步。</div>`;
      return;
    }
    refs.next.innerHTML = list
      .map((n) => {
        const pct = Math.round((n.prob || 0) * 100);
        const name = escapeHtml(chordName(n.deg, n.minor));
        return `<button class="trends-next-btn" type="button" data-deg="${n.deg}" data-minor="${n.minor ? 1 : 0}" title="加入 ${name}（${pct}%）">
          <span class="trends-next-bar" style="width:${Math.max(4, pct)}%"></span>
          <span class="trends-next-roman">${name}</span>
          <span class="trends-next-pct">${pct}%</span>
        </button>`;
      })
      .join("");
    refs.next.querySelectorAll(".trends-next-btn").forEach((b) => {
      b.addEventListener("click", () => appendChord(Number(b.dataset.deg), b.dataset.minor === "1"));
    });
  }

  // ---- matching library songs (reuses /api/progression/match) ----
  async function fetchMatches() {
    if (!refs.matches) return;
    // The match endpoint needs at least 2 chords; a lone tonic shows no cards.
    if (state.path.length < 2) {
      refs.matches.innerHTML = `<div class="proglib-match-empty">選一個接續和弦即可看到相關歌曲。</div>`;
      if (refs.matchCount) refs.matchCount.textContent = "";
      if (refs.pager) refs.pager.innerHTML = "";
      state.matchTotal = 0;
      return;
    }
    const token = ++_matchToken;
    const seq = pathToSeq();
    refs.matches.innerHTML = `<div class="proglib-match-empty">搜尋中…</div>`;
    if (refs.matchCount) refs.matchCount.textContent = "";
    if (refs.pager) refs.pager.innerHTML = "";
    try {
      const offset = state.matchPage * MATCH_PAGE_SIZE;
      const res = await fetch(`/api/progression/match?seq=${encodeURIComponent(seq)}&limit=${MATCH_PAGE_SIZE}&offset=${offset}`);
      if (!res.ok) throw new Error(String(res.status));
      const data = await res.json();
      if (token !== _matchToken) return;
      state.matchTotal = data.count || 0;
      renderMatches(data);
      renderPager();
    } catch (e) {
      if (token !== _matchToken) return;
      state.matchTotal = 0;
      refs.matches.innerHTML = `<div class="proglib-match-empty">此伺服器未提供歌曲比對。</div>`;
    }
  }

  function renderMatches(data) {
    const songs = (data && data.songs) || [];
    if (refs.matchCount) refs.matchCount.textContent = data && data.count ? `共 ${data.count} 首` : "";
    if (!songs.length) {
      refs.matches.innerHTML = `<div class="proglib-match-empty">音樂庫中沒有符合此進行的歌曲。</div>`;
      return;
    }
    // Card markup mirrors progression-library.js renderMatches so the two
    // sections share the .proglib-song-card styling.
    refs.matches.innerHTML = songs
      .map((s) => {
        const h = encodeURIComponent(s.hash);
        const title = escapeHtml(s.title || "Untitled");
        const key = escapeHtml(s.key || "");
        const isLib = s.path && !s.path.startsWith("__");
        const href = isLib ? `/player?path=${encodeURIComponent(s.path)}&autoplay=1` : `/player?hash=${h}`;
        const cover = s.path ? `/api/track/cover?path=${encodeURIComponent(s.path)}` : `/api/process/cover/${h}`;
        return `<a class="proglib-song-card" href="${href}" title="${title}">
          <span class="proglib-song-cover no-cover"><img src="${cover}" loading="lazy" alt="" onload="this.parentElement.classList.remove('no-cover')" onerror="this.remove()"><span class="proglib-song-ph">♪</span></span>
          <span class="proglib-song-meta"><span class="proglib-song-title">${title}</span>${key ? `<span class="proglib-song-key">${key}</span>` : ""}</span>
        </a>`;
      })
      .join("");
  }

  function renderPager() {
    if (!refs.pager) return;
    const totalPages = Math.max(1, Math.ceil(state.matchTotal / MATCH_PAGE_SIZE));
    if (state.matchTotal <= MATCH_PAGE_SIZE) {
      refs.pager.innerHTML = "";
      return;
    }
    refs.pager.innerHTML =
      `<button class="proglib-pg-btn" type="button" data-dir="-1" aria-label="上一頁" title="上一頁" ${state.matchPage <= 0 ? "disabled" : ""}>‹</button>` +
      `<span class="proglib-pg-info">第 ${state.matchPage + 1} / ${totalPages} 頁</span>` +
      `<button class="proglib-pg-btn" type="button" data-dir="1" aria-label="下一頁" title="下一頁" ${state.matchPage >= totalPages - 1 ? "disabled" : ""}>›</button>`;
    refs.pager.querySelectorAll(".proglib-pg-btn").forEach((b) => {
      b.addEventListener("click", () => {
        const totalPgs = Math.max(1, Math.ceil(state.matchTotal / MATCH_PAGE_SIZE));
        const next = Math.min(totalPgs - 1, Math.max(0, state.matchPage + Number(b.dataset.dir)));
        if (next === state.matchPage) return;
        state.matchPage = next;
        fetchMatches();
      });
    });
  }

  // ---- collapse / scale UI ----
  function syncCollapseUi() {
    const collapsed = !!state.collapsed;
    ROOT.classList.toggle("is-collapsed", collapsed);
    if (refs.body) refs.body.setAttribute("aria-hidden", collapsed ? "true" : "false");
    if (!refs.collapseBtn) return;
    refs.collapseBtn.classList.toggle("is-active", collapsed);
    refs.collapseBtn.textContent = collapsed ? "展開" : "收折";
    refs.collapseBtn.setAttribute("aria-expanded", collapsed ? "false" : "true");
  }

  function syncScaleUi() {
    const root = currentKey().name;
    if (refs.scaleMajor) {
      refs.scaleMajor.classList.toggle("is-active", state.scale === "major");
      refs.scaleMajor.textContent = `大調 ${root}`;
    }
    if (refs.scaleMinor) {
      refs.scaleMinor.classList.toggle("is-active", state.scale === "minor");
      refs.scaleMinor.textContent = `小調 ${root}m`;
    }
  }

  // ---- persistence ----
  function hydrate() {
    try {
      const c = localStorage.getItem(COLLAPSE_KEY);
      if (c === "0") state.collapsed = false;
      else if (c === "1") state.collapsed = true;
      const s = localStorage.getItem(SCALE_KEY);
      if (s === "minor" || s === "major") {
        state.scale = s;
        state.path = [{ deg: 0, minor: s === "minor" }];
      }
      const k = localStorage.getItem(KEY_KEY);
      if (k && KEYS.some((x) => x.name === k)) state.keyName = k;
    } catch {}
  }

  function persistCollapsed() {
    try { localStorage.setItem(COLLAPSE_KEY, state.collapsed ? "1" : "0"); } catch {}
  }

  function persistKey() {
    try { localStorage.setItem(KEY_KEY, state.keyName); } catch {}
  }

  function persistScale() {
    try { localStorage.setItem(SCALE_KEY, state.scale); } catch {}
  }

  // ---- utils ----
  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  init();
})();
