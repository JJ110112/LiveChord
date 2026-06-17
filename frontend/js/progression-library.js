// progression-library.js v1
// A curated library of classic chord progressions grouped by style.
// Mirrors the Ambient Mode pattern (collapsible homepage section, Tone.js
// loop playback, localStorage favorites) but plays *named* progressions the
// user selects or randomizes, instead of generating ambient voicings.
(function () {
  const ROOT = document.getElementById("secProgLib");
  if (!ROOT) return;

  const FAVORITES_KEY = "livechord_proglib_favorites";
  const FAVORITES_MAX = 16;
  const SETTINGS_KEY = "livechord_proglib_settings";
  const VOLUME_KEY = "livechord_proglib_volume";

  // Quality → semitone intervals + display suffix + minor-ish flag (roman case).
  const QUALITY = {
    maj:     { iv: [0, 4, 7],          sfx: "",      min: false },
    m:       { iv: [0, 3, 7],          sfx: "m",     min: true },
    dim:     { iv: [0, 3, 6],          sfx: "dim",   min: true },
    aug:     { iv: [0, 4, 8],          sfx: "aug",   min: false },
    maj7:    { iv: [0, 4, 7, 11],      sfx: "maj7",  min: false },
    m7:      { iv: [0, 3, 7, 10],      sfx: "m7",    min: true },
    "7":     { iv: [0, 4, 7, 10],      sfx: "7",     min: false },
    m7b5:    { iv: [0, 3, 6, 10],      sfx: "m7♭5", min: true },
    dim7:    { iv: [0, 3, 6, 9],       sfx: "dim7",  min: true },
    maj9:    { iv: [0, 4, 7, 11, 14],  sfx: "maj9",  min: false },
    m9:      { iv: [0, 3, 7, 10, 14],  sfx: "m9",    min: true },
    "9":     { iv: [0, 4, 7, 10, 14],  sfx: "9",     min: false },
    "7sus4": { iv: [0, 5, 7, 10],      sfx: "7sus4", min: false },
    "6":     { iv: [0, 4, 7, 9],       sfx: "6",     min: false },
    m6:      { iv: [0, 3, 7, 9],       sfx: "m6",    min: true },
    add9:    { iv: [0, 4, 7, 14],      sfx: "add9",  min: false },
  };

  // Each progression: chords = [[degreeSemitone, quality], ...] relative to key root.
  const LIBRARY = {
    pop: {
      name: "流行 / 一般",
      progressions: [
        { id: "axis",   name: "萬用四和弦 I–V–vi–IV", desc: "Axis 進行，無數西洋／華語流行歌的骨架。", chords: [[0,"maj"],[7,"maj"],[9,"m"],[5,"maj"]] },
        { id: "6451",   name: "情歌 6451 vi–IV–I–V", desc: "從 vi 起頭，帶點傷感的抒情骨架。", chords: [[9,"m"],[5,"maj"],[0,"maj"],[7,"maj"]] },
        { id: "doowop", name: "50s 進行 I–vi–IV–V", desc: "Doo-wop / 老式情歌的經典循環。", chords: [[0,"maj"],[9,"m"],[5,"maj"],[7,"maj"]] },
        { id: "canon",  name: "卡農 I–V–vi–iii–IV–I–IV–V", desc: "帕海貝爾卡農，下行低音線。", chords: [[0,"maj"],[7,"maj"],[9,"m"],[4,"m"],[5,"maj"],[0,"maj"],[5,"maj"],[7,"maj"]] },
        { id: "royal",  name: "王道進行 IV–V–iii–vi", desc: "日系王道進行 (4536)，動畫／J-pop 常見。", chords: [[5,"maj7"],[7,"7"],[4,"m7"],[9,"m7"]] },
        { id: "251pop", name: "大調 ii–V–I", desc: "最基本的解決進行。", chords: [[2,"m7"],[7,"7"],[0,"maj7"]] },
      ],
    },
    jazz: {
      name: "爵士 Jazz",
      progressions: [
        { id: "251maj", name: "大調 ii–V–I", desc: "爵士最核心的 2-5-1。", chords: [[2,"m7"],[7,"7"],[0,"maj7"]] },
        { id: "251min", name: "小調 iiø–V7–i", desc: "小調 2-5-1，半減七起頭。", chords: [[2,"m7b5"],[7,"7"],[0,"m7"]] },
        { id: "1625",   name: "1625 迴轉 I–VI7–ii–V", desc: "Turnaround，帶副屬和弦的迴轉。", chords: [[0,"maj7"],[9,"7"],[2,"m7"],[7,"7"]] },
        { id: "rhythm", name: "I–vi–ii–V", desc: "Rhythm changes 的 A 段骨架。", chords: [[0,"maj7"],[9,"m7"],[2,"m7"],[7,"7"]] },
        { id: "36251",  name: "iii–vi–ii–V–I", desc: "延伸的下行五度迴圈。", chords: [[4,"m7"],[9,"m7"],[2,"m7"],[7,"7"],[0,"maj7"]] },
      ],
    },
    blues: {
      name: "藍調 Blues",
      progressions: [
        { id: "12bar",  name: "12-bar 藍調", desc: "標準 12 小節藍調。", chords: [[0,"7"],[0,"7"],[0,"7"],[0,"7"],[5,"7"],[5,"7"],[0,"7"],[0,"7"],[7,"7"],[5,"7"],[0,"7"],[7,"7"]] },
        { id: "quick",  name: "Quick-change 藍調", desc: "第 2 小節提前到 IV7 的變體。", chords: [[0,"7"],[5,"7"],[0,"7"],[0,"7"],[5,"7"],[5,"7"],[0,"7"],[0,"7"],[7,"7"],[5,"7"],[0,"7"],[7,"7"]] },
        { id: "minor",  name: "小調藍調", desc: "i7／iv7 為主的小調藍調。", chords: [[0,"m7"],[5,"m7"],[0,"m7"],[0,"m7"],[5,"m7"],[5,"m7"],[0,"m7"],[0,"m7"],[7,"7"],[5,"m7"],[0,"m7"],[7,"7"]] },
      ],
    },
    classical: {
      name: "古典 Classical",
      progressions: [
        { id: "cadence",  name: "正格終止 I–IV–V–I", desc: "最基本的終止式。", chords: [[0,"maj"],[5,"maj"],[7,"maj"],[0,"maj"]] },
        { id: "andal",    name: "安達魯西亞 i–♭VII–♭VI–V", desc: "佛朗明哥下行終止。", chords: [[0,"m"],[10,"maj"],[8,"maj"],[7,"maj"]] },
        { id: "circle",   name: "五度圈 vi–ii–V–I", desc: "五度圈下行解決。", chords: [[9,"m"],[2,"m"],[7,"maj"],[0,"maj"]] },
        { id: "plagal",   name: "I–V–I–IV–I", desc: "正格＋變格 (Amen) 終止組合。", chords: [[0,"maj"],[7,"maj"],[0,"maj"],[5,"maj"],[0,"maj"]] },
      ],
    },
    folk: {
      name: "民謠 Folk",
      progressions: [
        { id: "145",   name: "三和弦 I–IV–V", desc: "民謠／營火三和弦。", chords: [[0,"maj"],[5,"maj"],[7,"maj"]] },
        { id: "1415",  name: "I–IV–I–V", desc: "經典四句民謠循環。", chords: [[0,"maj"],[5,"maj"],[0,"maj"],[7,"maj"]] },
        { id: "1454",  name: "I–IV–V–IV", desc: "搖滾／民謠的擺盪感。", chords: [[0,"maj"],[5,"maj"],[7,"maj"],[5,"maj"]] },
      ],
    },
    rnb: {
      name: "R&B / Neo-Soul",
      progressions: [
        { id: "neo251",  name: "ii9–V9–Imaj9", desc: "厚和聲的 Neo-Soul 2-5-1。", chords: [[2,"m9"],[7,"9"],[0,"maj9"]] },
        { id: "quiet",   name: "Imaj7–iii7–IVmaj7–V7", desc: "Quiet Storm 慢板情歌。", chords: [[0,"maj7"],[4,"m7"],[5,"maj7"],[7,"7"]] },
        { id: "smooth",  name: "Imaj9–iii7–vi9–IVmaj7", desc: "綿延的 smooth groove。", chords: [[0,"maj9"],[4,"m7"],[9,"m9"],[5,"maj7"]] },
      ],
    },
  };

  const KEYS = [
    { name: "C",  pc: 0,  sharp: false },
    { name: "G",  pc: 7,  sharp: true },
    { name: "D",  pc: 2,  sharp: true },
    { name: "A",  pc: 9,  sharp: true },
    { name: "E",  pc: 4,  sharp: true },
    { name: "B",  pc: 11, sharp: true },
    { name: "F#", pc: 6,  sharp: true },
    { name: "F",  pc: 5,  sharp: false },
    { name: "Bb", pc: 10, sharp: false },
    { name: "Eb", pc: 3,  sharp: false },
    { name: "Ab", pc: 8,  sharp: false },
    { name: "Db", pc: 1,  sharp: false },
  ];
  const SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const FLAT_NAMES  = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];
  const ROMAN_BASE = { 0: "I", 1: "♭II", 2: "II", 3: "♭III", 4: "III", 5: "IV", 6: "♭V", 7: "V", 8: "♭VI", 9: "VI", 10: "♭VII", 11: "VII" };
  // Fixed-do (固定調) solfège — C is always 1, matching the player's
  // _notesToJianpu (keySemi = 0). Table chosen by the note's spelling.
  const JP_SHARP = ["1", "#1", "2", "#2", "3", "4", "#4", "5", "#5", "6", "#6", "7"];
  const JP_FLAT  = ["1", "b2", "2", "b3", "3", "4", "b5", "5", "b6", "6", "b7", "7"];

  const state = {
    style: "pop",
    progId: "axis",
    keyName: "C",
    bpm: 100,
    beats: 4,
    volume: 0.7,
    collapsed: false,
    activeIndex: 0,
    chords: [],
    synthReady: false,
  };

  const refs = {
    style: document.getElementById("proglibStyle"),
    prog: document.getElementById("proglibProg"),
    key: document.getElementById("proglibKey"),
    bpm: document.getElementById("proglibBpm"),
    bpmVal: document.getElementById("proglibBpmVal"),
    beats: document.getElementById("proglibBeats"),
    vol: document.getElementById("proglibVol"),
    volVal: document.getElementById("proglibVolVal"),
    collapseBtn: document.getElementById("proglibCollapseBtn"),
    playBtn: document.getElementById("proglibPlayBtn"),
    randomBtn: document.getElementById("proglibRandomBtn"),
    favBtn: document.getElementById("proglibFavBtn"),
    body: document.getElementById("proglibBody"),
    ribbon: document.getElementById("proglibRibbon"),
    desc: document.getElementById("proglibDesc"),
    favorites: document.getElementById("proglibFavorites"),
    favHead: ROOT.querySelector(".proglib-fav-head"),
  };

  let synth = null;
  let outputGain = null;
  let reverb = null;
  let loopTimer = null;
  let loopBusy = false;

  function init() {
    hydrateVolume();
    hydrateSettings();
    populateControls();
    bindEvents();
    rebuild();
    renderFavorites();
    syncCollapseUi();
    syncPlayUi();
  }

  function currentStyle() {
    return LIBRARY[state.style] || LIBRARY.pop;
  }

  function currentProg() {
    const list = currentStyle().progressions;
    return list.find((p) => p.id === state.progId) || list[0];
  }

  function currentKey() {
    return KEYS.find((k) => k.name === state.keyName) || KEYS[0];
  }

  function populateControls() {
    refs.style.innerHTML = Object.entries(LIBRARY)
      .map(([k, v]) => `<option value="${k}">${v.name}</option>`)
      .join("");
    refs.key.innerHTML = KEYS.map((k) => `<option value="${k.name}">${k.name}</option>`).join("");
    refs.style.value = state.style;
    refs.key.value = state.keyName;
    refreshProgOptions();
    refs.bpm.value = String(state.bpm);
    refs.bpmVal.textContent = String(state.bpm);
    refs.beats.value = String(state.beats);
    refs.vol.value = String(Math.round(state.volume * 100));
    refs.volVal.textContent = String(Math.round(state.volume * 100));
  }

  function refreshProgOptions() {
    const list = currentStyle().progressions;
    refs.prog.innerHTML = list.map((p) => `<option value="${p.id}">${p.name}</option>`).join("");
    if (!list.some((p) => p.id === state.progId)) state.progId = list[0].id;
    refs.prog.value = state.progId;
  }

  function bindEvents() {
    refs.style.addEventListener("change", () => {
      state.style = refs.style.value;
      refreshProgOptions();
      persistSettings();
      rebuild();
    });
    refs.prog.addEventListener("change", () => {
      state.progId = refs.prog.value;
      persistSettings();
      rebuild();
    });
    refs.key.addEventListener("change", () => {
      state.keyName = refs.key.value;
      persistSettings();
      rebuild();
    });
    refs.bpm.addEventListener("input", () => {
      state.bpm = Number(refs.bpm.value || 100);
      refs.bpmVal.textContent = String(state.bpm);
      persistSettings();
      restartLoopIfActive(false);
    });
    refs.beats.addEventListener("change", () => {
      state.beats = Number(refs.beats.value || 4);
      persistSettings();
      restartLoopIfActive(false);
    });
    refs.vol.addEventListener("input", () => setVolumeFromPercent(Number(refs.vol.value || 70), true));
    refs.randomBtn.addEventListener("click", randomize);
    refs.favBtn.addEventListener("click", saveFavorite);
    if (refs.collapseBtn) {
      refs.collapseBtn.addEventListener("click", () => {
        state.collapsed = !state.collapsed;
        persistSettings();
        syncCollapseUi();
      });
    }
    if (refs.playBtn) refs.playBtn.addEventListener("click", toggleLoop);
    window.addEventListener("beforeunload", () => stopLoop(false));
  }

  function randomize() {
    const styles = Object.keys(LIBRARY);
    state.style = styles[Math.floor(Math.random() * styles.length)];
    const list = LIBRARY[state.style].progressions;
    state.progId = list[Math.floor(Math.random() * list.length)].id;
    state.keyName = KEYS[Math.floor(Math.random() * KEYS.length)].name;
    refs.style.value = state.style;
    refreshProgOptions();
    refs.key.value = state.keyName;
    persistSettings();
    rebuild();
    restartLoopIfActive(true);
  }

  // ---- build chords from the selected progression + key ----
  function rebuild() {
    const prog = currentProg();
    const key = currentKey();
    state.chords = (prog.chords || []).map(([deg, q]) => buildChord(key, deg, q));
    state.activeIndex = 0;
    renderRibbon();
    renderDesc();
    restartLoopIfActive(false);
  }

  function buildChord(key, deg, quality) {
    const qd = QUALITY[quality] || QUALITY.maj;
    const names = key.sharp ? SHARP_NAMES : FLAT_NAMES;
    const rootPc = (key.pc + deg) % 12;
    const rootName = names[rootPc];
    // Fixed-do jianpu (C = 1), same convention as the player's chord ribbon.
    const jianpu = qd.iv.map((i) => {
      const pc = (key.pc + deg + i) % 12;
      return (names[pc].includes("b") ? JP_FLAT : JP_SHARP)[pc];
    });
    const rootMidi = 48 + rootPc; // C3 = 48; root sits in C3..B3
    const midis = qd.iv.map((i) => rootMidi + i);
    let roman = ROMAN_BASE[((deg % 12) + 12) % 12];
    if (qd.min) roman = roman.toLowerCase();
    return { name: rootName + qd.sfx, roman, romanSfx: romanSuffix(quality), jianpu, midis };
  }

  function romanSuffix(quality) {
    if (quality === "m7b5") return "ø";
    if (quality === "dim" || quality === "dim7") return "°";
    if (quality === "maj7" || quality === "maj9") return "△";
    if (/7|9/.test(quality)) return "7";
    return "";
  }

  // ---- rendering ----
  function renderRibbon() {
    refs.ribbon.innerHTML = state.chords
      .map((ch, idx) => {
        const jianpuHtml = ch.jianpu
          .map((j) => {
            if (j.startsWith("#")) return `<sup>#</sup>${escapeHtml(j.slice(1))}`;
            if (j.startsWith("b")) return `<sup>b</sup>${escapeHtml(j.slice(1))}`;
            return escapeHtml(j);
          })
          .join(" ");
        return `
        <button class="proglib-chord ${idx === state.activeIndex ? "is-active" : ""}" data-idx="${idx}" type="button">
          <div class="proglib-chord-roman">${escapeHtml(ch.roman)}<sup>${escapeHtml(ch.romanSfx)}</sup></div>
          <div class="proglib-chord-name">${escapeHtml(ch.name)}</div>
          <div class="proglib-chord-jianpu">${jianpuHtml}</div>
        </button>`;
      })
      .join("");

    refs.ribbon.querySelectorAll(".proglib-chord").forEach((btn) => {
      btn.addEventListener("click", async () => {
        state.activeIndex = Number(btn.dataset.idx || 0);
        renderRibbon();
        await playChord(state.activeIndex);
        restartLoopIfActive(false);
      });
    });
  }

  function renderDesc() {
    const prog = currentProg();
    refs.desc.textContent = `${currentStyle().name} · ${prog.name}　—　${prog.desc || ""}`;
  }

  // ---- playback ----
  async function toggleLoop() {
    if (loopTimer) { stopLoop(true); return; }
    await startLoop(true, true);
  }

  async function startLoop(playNow, announce) {
    if (!state.chords.length) return;
    await ensureSynth();
    if (!state.synthReady) return;
    stopLoop(false);
    if (playNow) await playChord(state.activeIndex);
    const periodMs = loopIntervalMs();
    loopTimer = setInterval(async () => {
      if (loopBusy || !state.chords.length) return;
      loopBusy = true;
      try {
        state.activeIndex = (state.activeIndex + 1) % state.chords.length;
        renderRibbon();
        await playChord(state.activeIndex);
      } finally {
        loopBusy = false;
      }
    }, periodMs);
    syncPlayUi();
    if (announce) showToastSafe("循環播放中");
  }

  function stopLoop(announce) {
    if (loopTimer) { clearInterval(loopTimer); loopTimer = null; }
    loopBusy = false;
    syncPlayUi();
    if (announce) showToastSafe("已停止播放");
  }

  function restartLoopIfActive(playNow) {
    if (!loopTimer) return;
    startLoop(playNow, false);
  }

  function loopIntervalMs() {
    const safeBpm = clamp(state.bpm || 100, 40, 180);
    return Math.max(450, Math.round((60 / safeBpm) * 1000 * state.beats));
  }

  async function ensureSynth() {
    if (state.synthReady) return;
    if (!window.Tone) {
      showToastSafe("Tone.js 載入失敗，請檢查網路連線");
      return;
    }
    try {
      await window.Tone.start();
      outputGain = new window.Tone.Gain(state.volume).toDestination();
      reverb = new window.Tone.Reverb({ decay: 3.2, wet: 0.22 }).connect(outputGain);
      synth = new window.Tone.PolySynth(window.Tone.Synth, {
        oscillator: { type: "triangle" },
        envelope: { attack: 0.012, decay: 0.5, sustain: 0.5, release: 1.2 },
      }).connect(reverb);
      state.synthReady = true;
    } catch {
      showToastSafe("音訊初始化失敗，請點擊頁面後再試");
    }
  }

  async function playChord(index) {
    const ch = state.chords[index];
    if (!ch) return;
    await ensureSynth();
    if (!state.synthReady) return;
    const pitches = ch.midis.map(midiToTone);
    const durSec = Math.max(0.5, (60 / clamp(state.bpm || 100, 40, 180)) * state.beats * 0.92);
    synth.triggerAttackRelease(pitches, durSec);
  }

  function midiToTone(midi) {
    return SHARP_NAMES[((midi % 12) + 12) % 12] + (Math.floor(midi / 12) - 1);
  }

  // ---- favorites ----
  function saveFavorite() {
    const payload = {
      style: state.style,
      progId: state.progId,
      keyName: state.keyName,
      bpm: state.bpm,
      beats: state.beats,
      createdAt: Date.now(),
      id: `pl_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
    };
    const list = readFavorites();
    const sig = favSignature(payload);
    const exists = list.find((x) => favSignature(x) === sig);
    let next;
    if (exists) {
      next = [{ ...exists, createdAt: Date.now() }, ...list.filter((x) => x.id !== exists.id)];
      showToastSafe("此進行已在最愛，已移至最前");
    } else {
      next = [payload, ...list];
      if (next.length > FAVORITES_MAX) next = next.slice(0, FAVORITES_MAX);
      showToastSafe("已儲存進行最愛");
    }
    try { localStorage.setItem(FAVORITES_KEY, JSON.stringify(next)); } catch {}
    renderFavorites();
  }

  function favSignature(f) {
    return [f && f.style, f && f.progId, f && f.keyName].join("#");
  }

  function readFavorites() {
    try {
      const arr = JSON.parse(localStorage.getItem(FAVORITES_KEY) || "[]");
      return Array.isArray(arr) ? arr : [];
    } catch {
      return [];
    }
  }

  function renderFavorites() {
    const list = readFavorites();
    if (refs.favHead) refs.favHead.textContent = `我的進行最愛 (${list.length}/${FAVORITES_MAX})`;
    if (!list.length) {
      refs.favorites.innerHTML = `<div class="proglib-fav-empty">尚未儲存，按「儲存最愛」即可建立你的進行清單。</div>`;
      return;
    }
    refs.favorites.innerHTML = list
      .map((f) => {
        const sName = LIBRARY[f.style]?.name || f.style;
        const pName = (LIBRARY[f.style]?.progressions || []).find((p) => p.id === f.progId)?.name || f.progId;
        const label = `${escapeHtml(sName)} · ${escapeHtml(f.keyName)} · ${escapeHtml(pName)}`;
        return `<span class="proglib-fav-chip" data-id="${f.id}"><button class="proglib-fav-item" type="button" data-action="load" data-id="${f.id}">${label}</button><button class="proglib-fav-remove" type="button" data-action="remove" data-id="${f.id}" aria-label="刪除收藏" title="刪除收藏">&times;</button></span>`;
      })
      .join("");

    refs.favorites.querySelectorAll("[data-action='load']").forEach((el) => {
      el.addEventListener("click", () => {
        const target = readFavorites().find((x) => x.id === el.dataset.id);
        if (!target) return;
        if (LIBRARY[target.style]) state.style = target.style;
        state.progId = target.progId;
        if (KEYS.some((k) => k.name === target.keyName)) state.keyName = target.keyName;
        if (Number.isFinite(Number(target.bpm))) state.bpm = clamp(Math.round(Number(target.bpm)), 40, 180);
        if ([2, 4, 8].includes(Number(target.beats))) state.beats = Number(target.beats);
        refs.style.value = state.style;
        refreshProgOptions();
        refs.key.value = state.keyName;
        refs.bpm.value = String(state.bpm);
        refs.bpmVal.textContent = String(state.bpm);
        refs.beats.value = String(state.beats);
        persistSettings();
        rebuild();
        restartLoopIfActive(true);
        showToastSafe("已載入進行最愛");
      });
    });
    refs.favorites.querySelectorAll("[data-action='remove']").forEach((el) => {
      el.addEventListener("click", (evt) => {
        evt.stopPropagation();
        const next = readFavorites().filter((f) => f.id !== el.dataset.id);
        try { localStorage.setItem(FAVORITES_KEY, JSON.stringify(next)); } catch {}
        renderFavorites();
        showToastSafe("已刪除進行最愛");
      });
    });
  }

  // ---- collapse / play UI ----
  function syncCollapseUi() {
    const collapsed = !!state.collapsed;
    ROOT.classList.toggle("is-collapsed", collapsed);
    if (refs.body) refs.body.setAttribute("aria-hidden", collapsed ? "true" : "false");
    if (!refs.collapseBtn) return;
    refs.collapseBtn.classList.toggle("is-active", collapsed);
    refs.collapseBtn.textContent = collapsed ? "展開" : "收折";
    refs.collapseBtn.setAttribute("aria-expanded", collapsed ? "false" : "true");
  }

  function syncPlayUi() {
    if (!refs.playBtn) return;
    const active = !!loopTimer;
    refs.playBtn.classList.toggle("is-active", active);
    refs.playBtn.setAttribute("title", active ? "停止" : "循環播放");
    refs.playBtn.setAttribute("aria-label", active ? "Stop" : "Loop playback");
  }

  // ---- persistence ----
  function hydrateVolume() {
    try {
      const raw = localStorage.getItem(VOLUME_KEY);
      if (raw == null) return;
      const pct = Number(raw);
      if (Number.isFinite(pct)) state.volume = clamp(pct / 100, 0, 1);
    } catch {}
  }

  function hydrateSettings() {
    try {
      const obj = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "null");
      if (!obj || typeof obj !== "object") return;
      if (obj.style && LIBRARY[obj.style]) state.style = obj.style;
      if (obj.progId && (LIBRARY[state.style].progressions || []).some((p) => p.id === obj.progId)) state.progId = obj.progId;
      if (obj.keyName && KEYS.some((k) => k.name === obj.keyName)) state.keyName = obj.keyName;
      if (Number.isFinite(Number(obj.bpm))) state.bpm = clamp(Math.round(Number(obj.bpm)), 40, 180);
      if ([2, 4, 8].includes(Number(obj.beats))) state.beats = Number(obj.beats);
      if (typeof obj.collapsed === "boolean") state.collapsed = obj.collapsed;
    } catch {}
  }

  function persistSettings() {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify({
        style: state.style, progId: state.progId, keyName: state.keyName,
        bpm: state.bpm, beats: state.beats, collapsed: state.collapsed,
      }));
    } catch {}
  }

  function setVolumeFromPercent(pct, persist) {
    const safePct = clamp(Math.round(Number(pct) || 0), 0, 100);
    state.volume = safePct / 100;
    refs.volVal.textContent = String(safePct);
    if (outputGain) {
      const now = window.Tone ? window.Tone.now() : 0;
      outputGain.gain.rampTo(state.volume, 0.08, now);
    }
    if (persist) { try { localStorage.setItem(VOLUME_KEY, String(safePct)); } catch {} }
  }

  // ---- utils ----
  function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }

  function showToastSafe(msg) {
    if (typeof window.showToast === "function") { window.showToast(msg, 1800); return; }
    const el = document.getElementById("toast");
    if (el) {
      el.textContent = msg;
      el.classList.add("show");
      setTimeout(() => el.classList.remove("show"), 1800);
    }
  }

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
