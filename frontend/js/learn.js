// learn.js v4 — /learn 互動學習: ear-training quizzes (和弦聽辨 / 音階聽辨).
// Self-contained WebAudio for chords; scales reuse window.ScaleLab
// (catalogue + playScale). Progress is stored via /api/learn/*.
(function () {
  "use strict";

  const NOTE_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];

  // ---- chord catalogue: id → intervals + labels ----
  const CHORDS = {
    maj:   { iv: [0, 4, 7],           name: "大三和弦",   sfx: "",      desc: "明亮、穩定。根音上大三度 + 純五度。" },
    m:     { iv: [0, 3, 7],           name: "小三和弦",   sfx: "m",     desc: "暗一點、帶感傷。三音降半音。" },
    dim:   { iv: [0, 3, 6],           name: "減三和弦",   sfx: "dim",   desc: "緊張、不安。小三度 + 減五度。" },
    aug:   { iv: [0, 4, 8],           name: "增三和弦",   sfx: "aug",   desc: "懸浮、夢幻。大三度 + 增五度。" },
    maj7:  { iv: [0, 4, 7, 11],       name: "大七和弦",   sfx: "maj7",  desc: "柔和、爵士感。大三和弦加大七度。" },
    m7:    { iv: [0, 3, 7, 10],       name: "小七和弦",   sfx: "m7",    desc: "溫暖、圓潤。小三和弦加小七度。" },
    "7":   { iv: [0, 4, 7, 10],       name: "屬七和弦",   sfx: "7",     desc: "想解決的張力。大三和弦加小七度。" },
    m7b5:  { iv: [0, 3, 6, 10],       name: "半減七和弦", sfx: "m7♭5",  desc: "陰鬱。減三和弦加小七度，常見於小調 ii。" },
    dim7:  { iv: [0, 3, 6, 9],        name: "減七和弦",   sfx: "dim7",  desc: "疊小三度，四個音等距，非常不穩定。" },
    sus2:  { iv: [0, 2, 7],           name: "掛二和弦",   sfx: "sus2",  desc: "開放、沒有大小調色彩。三音換成二度。" },
    sus4:  { iv: [0, 5, 7],           name: "掛四和弦",   sfx: "sus4",  desc: "懸掛、等待解決。三音換成四度。" },
    "6":   { iv: [0, 4, 7, 9],        name: "大六和弦",   sfx: "6",     desc: "復古、甜。大三和弦加大六度。" },
    m6:    { iv: [0, 3, 7, 9],        name: "小六和弦",   sfx: "m6",    desc: "神祕、Dorian 味。小三和弦加大六度。" },
    maj9:  { iv: [0, 4, 7, 11, 14],   name: "大九和弦",   sfx: "maj9",  desc: "寬廣、浪漫。大七和弦再加九度。" },
    m9:    { iv: [0, 3, 7, 10, 14],   name: "小九和弦",   sfx: "m9",    desc: "Neo-Soul 標配。小七和弦加九度。" },
    "9":   { iv: [0, 4, 7, 10, 14],   name: "屬九和弦",   sfx: "9",     desc: "放克、藍調。屬七和弦加九度。" },
    "7b9": { iv: [0, 4, 7, 10, 13],   name: "屬七降九",   sfx: "7♭9",   desc: "黑暗的屬和弦，常解決到小調主和弦。" },
    "7#5": { iv: [0, 4, 8, 10],       name: "屬七升五",   sfx: "7♯5",   desc: "增和弦加小七度，張力更大。" },
    mmaj7: { iv: [0, 3, 7, 11],       name: "小大七和弦", sfx: "m(maj7)", desc: "小三和弦配大七度，懸疑電影感。" },
  };
  // ---- single-note catalogue (relative to a played reference "do") ----
  // semi: semitones from do. jp: jianpu digit; acc: "#"; oct: -1 / 0 / +1.
  const NOTES = {
    do:   { semi: 0,  jp: "1", solf: "do",  iv: "主音（同 do）" },
    re:   { semi: 2,  jp: "2", solf: "re",  iv: "大二度" },
    mi:   { semi: 4,  jp: "3", solf: "mi",  iv: "大三度" },
    fa:   { semi: 5,  jp: "4", solf: "fa",  iv: "完全四度" },
    sol:  { semi: 7,  jp: "5", solf: "sol", iv: "完全五度" },
    la:   { semi: 9,  jp: "6", solf: "la",  iv: "大六度" },
    ti:   { semi: 11, jp: "7", solf: "ti",  iv: "大七度" },
    di:   { semi: 1,  jp: "1", acc: "#", solf: "di (升 do)", iv: "小二度" },
    ri:   { semi: 3,  jp: "2", acc: "#", solf: "ri (升 re)", iv: "小三度" },
    fi:   { semi: 6,  jp: "4", acc: "#", solf: "fi (升 fa)", iv: "增四度 / 三全音" },
    si:   { semi: 8,  jp: "5", acc: "#", solf: "si (升 sol)", iv: "小六度" },
    li:   { semi: 10, jp: "6", acc: "#", solf: "li (升 la)", iv: "小七度" },
    do_hi:  { semi: 12, jp: "1", oct: 1,  solf: "高音 do",  iv: "純八度" },
    re_hi:  { semi: 14, jp: "2", oct: 1,  solf: "高音 re",  iv: "大九度" },
    mi_hi:  { semi: 16, jp: "3", oct: 1,  solf: "高音 mi",  iv: "大十度" },
    ti_lo:  { semi: -1, jp: "7", oct: -1, solf: "低音 ti",  iv: "下方小二度" },
    la_lo:  { semi: -3, jp: "6", oct: -1, solf: "低音 la",  iv: "下方小三度" },
    sol_lo: { semi: -5, jp: "5", oct: -1, solf: "低音 sol", iv: "下方完全四度" },
  };
  const NOTE_LEVELS = {
    1: ["do", "re", "mi", "fa", "sol", "la", "ti"],
    2: ["do", "re", "mi", "fa", "sol", "la", "ti", "fi", "di", "si", "ri", "li"],
    3: ["do", "re", "mi", "fa", "sol", "la", "ti", "do_hi", "ti_lo", "re_hi", "la_lo", "mi_hi", "sol_lo"],
  };
  const NOTE_LEVEL_HINT = {
    1: "先聽 do（參考音），再聽目標音，答它是 do re mi fa sol la ti 哪一個。從 do / re 二選一開始。",
    2: "加入五個半音（升 do、升 re、升 fa、升 sol、升 la），共 12 個音。",
    3: "跨八度：低音 sol 到高音 mi，練習聽出音在 do 的上方還是下方。",
  };

  // ---- interval catalogue ----
  const INTERVALS = {
    P8: { semi: 12, name: "純八度",  en: "P8", song: "Somewhere Over the Rainbow 開頭" },
    P5: { semi: 7,  name: "純五度",  en: "P5", song: "小星星（一閃一閃）" },
    M3: { semi: 4,  name: "大三度",  en: "M3", song: "When the Saints Go Marching In 開頭" },
    m3: { semi: 3,  name: "小三度",  en: "m3", song: "綠袖子 / 布拉姆斯搖籃曲開頭" },
    P4: { semi: 5,  name: "純四度",  en: "P4", song: "婚禮進行曲 Here Comes the Bride" },
    M2: { semi: 2,  name: "大二度",  en: "M2", song: "生日快樂歌開頭" },
    m2: { semi: 1,  name: "小二度",  en: "m2", song: "大白鯊主題" },
    M6: { semi: 9,  name: "大六度",  en: "M6", song: "My Bonnie / NBC 三音" },
    m6: { semi: 8,  name: "小六度",  en: "m6", song: "Love Story 主題開頭" },
    m7: { semi: 10, name: "小七度",  en: "m7", song: "Star Trek 原版主題開頭" },
    M7: { semi: 11, name: "大七度",  en: "M7", song: "Take On Me 副歌" },
    TT: { semi: 6,  name: "三全音",  en: "TT / 增四 / 減五", song: "辛普森家庭主題 The Simp-sons" },
  };
  const INTERVAL_ORDER = ["P8", "P5", "M3", "m3", "P4", "M2", "m2", "M6", "m6", "m7", "M7", "TT"];
  const INTERVAL_LEVELS = { 1: INTERVAL_ORDER, 2: INTERVAL_ORDER, 3: INTERVAL_ORDER };
  const INTERVAL_LEVEL_HINT = {
    1: "上行旋律音程：先低音再高音。從 純八度 / 純五度 二選一開始。",
    2: "隨機上行或下行：先聽方向，再聽距離。",
    3: "和聲音程：兩個音同時響，最難。可用「琶音」拆開聽。",
  };

  const CHORD_LEVELS = {
    1: ["maj", "m", "dim", "aug"],
    2: ["maj", "m", "7", "maj7", "m7", "dim", "aug", "m7b5", "dim7"],
    3: ["maj", "m", "7", "maj7", "m7", "sus4", "sus2", "6", "m6", "dim", "aug", "m7b5", "dim7", "9", "maj9", "m9", "7b9", "7#5", "mmaj7"],
  };
  const CHORD_LEVEL_HINT = {
    1: "四種三和弦：大 / 小 / 減 / 增。",
    2: "加入常用七和弦：maj7 / m7 / 7 / m7♭5 / dim7。",
    3: "全部 19 種，含掛留、六度、九度與變化屬和弦。每題隨機出 8 個選項。",
  };

  const SCALE_LEVELS = {
    1: ["major", "minor", "major_pentatonic", "minor_pentatonic", "harmonic_minor", "blues"],
    2: ["major", "minor", "dorian", "mixolydian", "lydian", "phrygian", "locrian", "melodic_minor"],
    3: ["whole_tone", "half_whole_dim", "whole_half_dim", "altered", "lydian_dominant", "bebop_dominant",
        "harmonic_major", "hungarian_minor", "phrygian_dominant", "double_harmonic", "hirajoshi", "in_sen", "chromatic"],
  };
  const SCALE_LEVEL_HINT = {
    1: "大調、小調、和聲小調、五聲音階、藍調。",
    2: "七個教會調式加旋律小調 — 聽出特徵音（Dorian 的 6、Lydian 的 ♯4…）。",
    3: "爵士、對稱、異國音階。每題隨機出 8 個選項。",
  };
  const MAX_OPTIONS = 8;
  const GROW_STREAK = 5;   // consecutive correct answers before auto-adding an item
  const MIN_ACTIVE = 2;

  const state = {
    module: "chord",
    level: 1,
    question: null,     // { id, root, rootMidi }
    answered: false,
    session: { correct: 0, total: 0 },
    streak: 0,
    stats: null,
    active: [],        // ids currently in play (ordered subset of pool())
    autoGrow: true,
    sinceGrow: 0,      // consecutive correct since the last expansion
  };

  const $ = (id) => document.getElementById(id);
  const refs = {
    tabs: document.querySelectorAll(".learn-tab"),
    levels: document.querySelectorAll(".learn-level"),
    levelHint: $("learnLevelHint"),
    autoGrow: $("learnAutoGrow"),
    pool: $("learnPool"),
    poolCount: $("learnPoolCount"),
    sessionScore: $("learnSessionScore"),
    streak: $("learnStreak"),
    allTime: $("learnAllTime"),
    playBtn: $("learnPlayBtn"),
    arpBtn: $("learnArpBtn"),
    refBtn: $("learnRefBtn"),
    nextBtn: $("learnNextBtn"),
    prompt: $("learnPrompt"),
    options: $("learnOptions"),
    feedback: $("learnFeedback"),
    weak: $("learnWeak"),
  };

  // ---- audio (chords) ----
  let _ac = null;
  let _stopCurrent = null;
  function ctx() {
    if (!_ac) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      _ac = new AC();
    }
    if (_ac.state === "suspended") { try { _ac.resume(); } catch {} }
    return _ac;
  }

  function stopAudio() {
    if (typeof _stopCurrent === "function") { try { _stopCurrent(); } catch {} }
    _stopCurrent = null;
  }

  function noteOn(ac, master, midi, t, dur, gain) {
    const freq = 440 * Math.pow(2, (midi - 69) / 12);
    const g = ac.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(gain, t + 0.015);
    g.gain.exponentialRampToValueAtTime(gain * 0.45, t + dur * 0.5);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    g.connect(master);
    const o1 = ac.createOscillator();
    o1.type = "triangle";
    o1.frequency.value = freq;
    const o2 = ac.createOscillator();
    o2.type = "sine";
    o2.frequency.value = freq * 2;
    const g2 = ac.createGain();
    g2.gain.value = 0.18;
    o1.connect(g);
    o2.connect(g2).connect(g);
    o1.start(t); o2.start(t);
    o1.stop(t + dur + 0.05); o2.stop(t + dur + 0.05);
  }

  /** Play midis as a block (arp=false) or ascending arpeggio then block. */
  function playChord(midis, arp) {
    const ac = ctx();
    if (!ac) return;
    stopAudio();
    const master = ac.createGain();
    master.gain.value = 0.22;
    master.connect(ac.destination);
    const t0 = ac.currentTime + 0.03;
    let t = t0;
    if (arp) {
      midis.forEach((m, i) => noteOn(ac, master, m, t0 + i * 0.32, 0.9, 0.9));
      t = t0 + midis.length * 0.32 + 0.15;
    }
    midis.forEach((m) => noteOn(ac, master, m, t, 1.8, 0.7));
    const endMs = (t - ac.currentTime + 1.9) * 1000;
    const timer = setTimeout(() => { try { master.disconnect(); } catch {} }, endMs);
    _stopCurrent = () => {
      clearTimeout(timer);
      try { master.gain.setTargetAtTime(0.0001, ac.currentTime, 0.02); } catch {}
      setTimeout(() => { try { master.disconnect(); } catch {} }, 120);
    };
  }

  /** Reference do, then the target (or only the reference when id is null). */
  function playNote(rootMidi, id) {
    const ac = ctx();
    if (!ac) return;
    stopAudio();
    const master = ac.createGain();
    master.gain.value = 0.28;
    master.connect(ac.destination);
    const t0 = ac.currentTime + 0.03;
    noteOn(ac, master, rootMidi, t0, 0.7, 0.8);
    let end = t0 + 0.8;
    if (id) {
      noteOn(ac, master, rootMidi + NOTES[id].semi, t0 + 0.85, 1.1, 0.9);
      end = t0 + 2.0;
    }
    const timer = setTimeout(() => { try { master.disconnect(); } catch {} }, (end - ac.currentTime + 0.1) * 1000);
    _stopCurrent = () => {
      clearTimeout(timer);
      try { master.gain.setTargetAtTime(0.0001, ac.currentTime, 0.02); } catch {}
      setTimeout(() => { try { master.disconnect(); } catch {} }, 120);
    };
  }

  /** Two notes: melodic (dir = 1 up / -1 down) or harmonic (dir = 0). arp forces melodic up. */
  function playInterval(rootMidi, id, dir, arp) {
    const ac = ctx();
    if (!ac) return;
    stopAudio();
    const master = ac.createGain();
    master.gain.value = 0.28;
    master.connect(ac.destination);
    const t0 = ac.currentTime + 0.03;
    const hi = rootMidi + INTERVALS[id].semi;
    let end;
    if (dir === 0 && !arp) {
      noteOn(ac, master, rootMidi, t0, 1.6, 0.8);
      noteOn(ac, master, hi, t0, 1.6, 0.8);
      end = t0 + 1.7;
    } else {
      const first = dir < 0 ? hi : rootMidi;
      const second = dir < 0 ? rootMidi : hi;
      noteOn(ac, master, first, t0, 0.7, 0.85);
      noteOn(ac, master, second, t0 + 0.75, 1.0, 0.9);
      end = t0 + 1.8;
    }
    const timer = setTimeout(() => { try { master.disconnect(); } catch {} }, (end - ac.currentTime + 0.1) * 1000);
    _stopCurrent = () => {
      clearTimeout(timer);
      try { master.gain.setTargetAtTime(0.0001, ac.currentTime, 0.02); } catch {}
      setTimeout(() => { try { master.disconnect(); } catch {} }, 120);
    };
  }

  function chordMidis(rootMidi, id) {
    return CHORDS[id].iv.map((iv) => rootMidi + iv);
  }

  function playScaleId(root, id) {
    if (!window.ScaleLab) return;
    stopAudio();
    ctx();
    const built = window.ScaleLab.buildScale(root, id);
    _stopCurrent = window.ScaleLab.playScale(built, { bpm: 170 });
  }

  // ---- question generation ----
  function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }
  function shuffle(arr) {
    const a = arr.slice();
    for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; }
    return a;
  }

  function pool() {
    if (state.module === "note") return NOTE_LEVELS[state.level];
    if (state.module === "interval") return INTERVAL_LEVELS[state.level];
    return state.module === "chord" ? CHORD_LEVELS[state.level] : SCALE_LEVELS[state.level];
  }

  function levelHint() {
    if (state.module === "note") return NOTE_LEVEL_HINT[state.level];
    if (state.module === "interval") return INTERVAL_LEVEL_HINT[state.level];
    return (state.module === "chord" ? CHORD_LEVEL_HINT : SCALE_LEVEL_HINT)[state.level];
  }

  function poolKey() { return `livechord_learn_pool:${state.module}:${state.level}`; }

  function loadActive() {
    const ids = pool();
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(poolKey()) || "null"); } catch {}
    const valid = Array.isArray(saved) ? ids.filter((id) => saved.includes(id)) : [];
    state.active = valid.length >= MIN_ACTIVE ? valid : ids.slice(0, MIN_ACTIVE);
    state.sinceGrow = 0;
  }

  function saveActive() {
    try { localStorage.setItem(poolKey(), JSON.stringify(state.active)); } catch {}
  }

  function nextInactive() {
    return pool().find((id) => !state.active.includes(id)) || null;
  }

  function renderPool() {
    const ids = pool();
    const next = nextInactive();
    refs.poolCount.textContent = `${state.active.length} / ${ids.length}`;
    refs.pool.innerHTML = ids.map((id) => {
      const on = state.active.includes(id);
      const l = optionLabel(id);
      return `<button class="learn-chip ${on ? "is-on" : ""} ${id === next ? "is-next" : ""}" type="button" data-id="${id}" title="${esc(l.sub)}">${esc(l.main)}</button>`;
    }).join("");
    refs.pool.querySelectorAll(".learn-chip").forEach((b) => b.addEventListener("click", () => toggleActive(b.dataset.id)));
  }

  function toggleActive(id) {
    const on = state.active.includes(id);
    if (on) {
      if (state.active.length <= MIN_ACTIVE) { toast(`至少要保留 ${MIN_ACTIVE} 種`); return; }
      state.active = state.active.filter((x) => x !== id);
    } else {
      state.active = pool().filter((x) => x === id || state.active.includes(x));
    }
    state.sinceGrow = 0;
    saveActive();
    renderPool();
    if (state.question && !state.answered && !state.active.includes(state.question.id)) newQuestion();
  }

  function maybeGrow() {
    if (!state.autoGrow || state.sinceGrow < GROW_STREAK) return;
    const next = nextInactive();
    if (!next) { state.sinceGrow = 0; return; }
    state.active = pool().filter((x) => x === next || state.active.includes(x));
    state.sinceGrow = 0;
    saveActive();
    renderPool();
    toast(`🎉 連對 ${GROW_STREAK} 題，加入：${optionLabel(next).main}`);
  }

  function toast(msg) {
    const el = document.getElementById("toast");
    if (!el) return;
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove("show"), 2200);
  }

  function newQuestion() {
    stopAudio();
    const ids = state.active.length >= MIN_ACTIVE ? state.active : pool();
    let id = pick(ids);
    // Avoid the same answer twice in a row when there is a choice.
    if (state.question && ids.length > 1 && id === state.question.id) id = pick(ids.filter((x) => x !== id));
    const rootPc = Math.floor(Math.random() * 12);
    // Notes: keep do around C4 so low-sol / high-mi both sit in a singable range.
    const rootMidi = (state.module === "note" || state.module === "interval") ? 55 + rootPc : 48 + rootPc + (Math.random() < 0.5 ? 0 : 12);
    const q = { id, rootPc, root: NOTE_NAMES[rootPc], rootMidi };
    // Interval direction: L1 up, L2 random, L3 harmonic.
    if (state.module === "interval") q.dir = state.level === 1 ? 1 : state.level === 2 ? (Math.random() < 0.5 ? 1 : -1) : 0;
    let options = ids;
    if (ids.length > MAX_OPTIONS) {
      options = shuffle([id].concat(shuffle(ids.filter((x) => x !== id)).slice(0, MAX_OPTIONS - 1)));
    } else {
      options = ids.slice();
    }
    q.options = options;
    state.question = q;
    state.answered = false;
    renderOptions();
    refs.feedback.hidden = true;
    refs.feedback.className = "learn-feedback";
    refs.nextBtn.disabled = true;
    refs.playBtn.textContent = "🔁 再聽一次";
    refs.prompt.textContent = state.module === "interval"
      ? (state.level === 3 ? "兩個音同時響 — 距離是多少？" : "兩個音的距離是多少？（不用管方向，只答距離）")
      : state.module === "note"
      ? "先聽 do，再聽第二個音 — 第二個音是哪個？（每題的 do 都不同，聽相對距離）"
      : state.module === "chord"
        ? "這是什麼和弦？（根音不固定，聽和弦的色彩）"
        : "這是什麼音階？（上行再下行，聽每一階的距離）";
    playQuestion(false);
  }

  function playQuestion(arp) {
    const q = state.question;
    if (!q) return;
    if (state.module === "note") playNote(q.rootMidi, q.id);
    else if (state.module === "interval") playInterval(q.rootMidi, q.id, q.dir, arp);
    else if (state.module === "chord") playChord(chordMidis(q.rootMidi, q.id), arp);
    else playScaleId(q.root, q.id);
  }

  function noteJpHtml(id) {
    const n = NOTES[id];
    const acc = n.acc ? `<sup>${n.acc}</sup>` : "";
    const up = n.oct > 0 ? `<span class="dot">•</span>` : "";
    const down = n.oct < 0 ? `<span class="dot">•</span>` : "";
    return `<span class="learn-jp">${up}${acc}${n.jp}${down}</span>`;
  }

  function optionLabel(id) {
    if (state.module === "interval") {
      const iv = INTERVALS[id];
      return { main: iv.name, sub: `${iv.en} · ${iv.semi} 半音` };
    }
    if (state.module === "note") {
      const n = NOTES[id];
      return { main: n.solf, sub: n.iv, html: noteJpHtml(id) };
    }
    if (state.module === "chord") {
      const c = CHORDS[id];
      return { main: c.name, sub: "X" + c.sfx };
    }
    const s = window.ScaleLab ? window.ScaleLab.getScale(id) : null;
    return { main: s ? s.name : id, sub: s ? s.en : "" };
  }

  function renderOptions() {
    const q = state.question;
    refs.options.innerHTML = q.options.map((id, i) => {
      const l = optionLabel(id);
      const main = l.html ? `${l.html}<small>${esc(l.main)}</small>` : esc(l.main);
      return `<button class="learn-opt" type="button" data-id="${id}"><span class="learn-key">${i + 1}</span>${main}<small>${esc(l.sub)}</small></button>`;
    }).join("");
    refs.options.querySelectorAll(".learn-opt").forEach((b) => b.addEventListener("click", () => answer(b.dataset.id)));
  }

  // ---- answering ----
  async function answer(id) {
    const q = state.question;
    if (!q || state.answered) return;
    state.answered = true;
    const correct = id === q.id;
    state.session.total += 1;
    if (correct) { state.session.correct += 1; state.streak += 1; state.sinceGrow += 1; } else { state.streak = 0; state.sinceGrow = 0; }
    refs.options.querySelectorAll(".learn-opt").forEach((b) => {
      b.disabled = true;
      if (b.dataset.id === q.id) b.classList.add("is-correct");
      else if (b.dataset.id === id) b.classList.add("is-wrong");
      else b.classList.add("is-dim");
    });
    renderFeedback(id, correct);
    refs.nextBtn.disabled = false;
    updateScoreUi();
    maybeGrow();
    try {
      await fetch("/api/learn/result", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ module: state.module, level: state.level, expected: q.id, answer: id, correct }),
      });
      loadStats();
    } catch {}
  }

  function renderFeedback(answerId, correct) {
    const q = state.question;
    const exp = optionLabel(q.id);
    const isChord = state.module === "chord";
    const isNote = state.module === "note";
    const isInterval = state.module === "interval";
    const expName = isInterval
      ? `${exp.main}（${exp.sub}${q.dir < 0 ? "，下行" : q.dir > 0 ? "，上行" : "，和聲"}）`
      : isNote
      ? `${exp.main}（do = ${q.root}，${exp.sub}）`
      : isChord ? `${q.root}${CHORDS[q.id].sfx}（${exp.main}）` : `${q.root} ${exp.main}`;
    const desc = isInterval ? `參考曲：${INTERVALS[q.id].song}` : isNote ? "答錯時用下面兩個按鈕來回聽：都是先 do 再目標音，比較距離感。" : isChord ? CHORDS[q.id].desc : (window.ScaleLab.getScale(q.id).desc || "");
    let html = `<div class="learn-feedback-title">${correct ? "✅ 答對了！" : "❌ 不是這個。"}正確答案：${esc(expName)}</div>`;
    html += `<div class="learn-feedback-desc">${esc(desc)}</div>`;
    html += `<div class="learn-compare"><button class="learn-btn" type="button" data-play="${q.id}">🔊 聽正確答案</button>`;
    if (!correct) {
      const a = optionLabel(answerId);
      html += `<button class="learn-btn" type="button" data-play="${answerId}">🔊 聽你選的（${esc(a.main)}）</button>`;
    }
    html += `</div>`;
    refs.feedback.innerHTML = html;
    refs.feedback.hidden = false;
    refs.feedback.classList.add(correct ? "is-correct" : "is-wrong");
    refs.feedback.querySelectorAll("[data-play]").forEach((b) => b.addEventListener("click", () => {
      const id = b.dataset.play;
      if (isNote) playNote(q.rootMidi, id);
      else if (isInterval) playInterval(q.rootMidi, id, q.dir, false);
      else if (isChord) playChord(chordMidis(q.rootMidi, id), false);
      else playScaleId(q.root, id);
    }));
  }

  function updateScoreUi() {
    refs.sessionScore.textContent = `${state.session.correct} / ${state.session.total}`;
    refs.streak.textContent = String(state.streak);
    const t = state.stats && state.stats.totals && state.stats.totals[state.module];
    const lv = t && t[String(state.level)];
    refs.allTime.textContent = lv && lv.total ? `${lv.correct} / ${lv.total}（${Math.round(lv.correct / lv.total * 100)}%）` : "—";
  }

  // ---- stats / weak spots ----
  async function loadStats() {
    try {
      const res = await fetch("/api/learn/stats");
      if (!res.ok) throw new Error(String(res.status));
      state.stats = await res.json();
    } catch { state.stats = null; }
    updateScoreUi();
    renderWeak();
  }

  function renderWeak() {
    const t = state.stats && state.stats.totals && state.stats.totals[state.module];
    const items = t && t.by_item ? Object.entries(t.by_item) : [];
    const rows = items
      .filter(([, v]) => v.total >= 2)
      .map(([id, v]) => ({ id, rate: v.correct / v.total, ...v }))
      .sort((a, b) => a.rate - b.rate)
      .slice(0, 12);
    if (!rows.length) { refs.weak.innerHTML = `<div class="learn-weak-empty">還沒有足夠的答題紀錄（每項至少 2 題）。</div>`; return; }
    refs.weak.innerHTML = rows.map((r) => {
      const l = optionLabel(r.id);
      return `<span class="learn-weak-chip ${r.rate >= 0.8 ? "ok" : ""}">${esc(l.main)} <b>${Math.round(r.rate * 100)}%</b> <span class="learn-dim">${r.correct}/${r.total}</span></span>`;
    }).join("");
  }

  // ---- UI wiring ----
  function setModule(m) {
    state.module = m;
    refs.tabs.forEach((t) => t.classList.toggle("is-active", t.dataset.module === m));
    syncModuleButtons();
    resetForNewSet();
  }

  function syncModuleButtons() {
    refs.arpBtn.style.display = (state.module === "chord" || state.module === "interval") ? "" : "none";
    refs.refBtn.style.display = state.module === "note" ? "" : "none";
  }

  function setLevel(l) {
    state.level = l;
    refs.levels.forEach((b) => b.classList.toggle("is-active", Number(b.dataset.level) === l));
    resetForNewSet();
  }

  function resetForNewSet() {
    stopAudio();
    state.question = null;
    state.answered = false;
    state.session = { correct: 0, total: 0 };
    state.streak = 0;
    refs.levelHint.textContent = levelHint();
    loadActive();
    renderPool();
    refs.options.innerHTML = "";
    refs.feedback.hidden = true;
    refs.nextBtn.disabled = true;
    refs.playBtn.textContent = "▶ 播放題目";
    refs.prompt.textContent = "按「播放題目」開始。快捷鍵：空白鍵重聽、數字鍵作答、Enter 下一題。";
    try { localStorage.setItem("livechord_learn", JSON.stringify({ module: state.module, level: state.level })); } catch {}
    updateScoreUi();
    renderWeak();
  }

  function bind() {
    refs.tabs.forEach((t) => t.addEventListener("click", () => setModule(t.dataset.module)));
    refs.levels.forEach((b) => b.addEventListener("click", () => setLevel(Number(b.dataset.level))));
    refs.playBtn.addEventListener("click", () => { if (!state.question) newQuestion(); else playQuestion(false); });
    refs.arpBtn.addEventListener("click", () => { if (!state.question) newQuestion(); else playQuestion(true); });
    refs.nextBtn.addEventListener("click", () => { if (state.answered) newQuestion(); });
    refs.refBtn.addEventListener("click", () => { if (state.question) playNote(state.question.rootMidi, null); });
    refs.autoGrow.addEventListener("change", () => {
      state.autoGrow = refs.autoGrow.checked;
      try { localStorage.setItem("livechord_learn_autogrow", state.autoGrow ? "1" : "0"); } catch {}
    });
    document.addEventListener("keydown", (e) => {
      if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
      if (e.key === " ") { e.preventDefault(); if (!state.question) newQuestion(); else playQuestion(false); return; }
      if (e.key === "Enter") { e.preventDefault(); if (state.answered) newQuestion(); else if (!state.question) newQuestion(); return; }
      const n = Number(e.key);
      if (Number.isInteger(n) && n >= 1 && n <= 9 && state.question && !state.answered) {
        const id = state.question.options[n - 1];
        if (id) answer(id);
      }
    });
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function init() {
    try {
      const saved = JSON.parse(localStorage.getItem("livechord_learn") || "null");
      if (saved && ["note", "interval", "chord", "scale"].includes(saved.module)) state.module = saved.module;
      if (saved && [1, 2, 3].includes(Number(saved.level))) state.level = Number(saved.level);
    } catch {}
    try { state.autoGrow = localStorage.getItem("livechord_learn_autogrow") !== "0"; } catch {}
    refs.autoGrow.checked = state.autoGrow;
    bind();
    refs.tabs.forEach((t) => t.classList.toggle("is-active", t.dataset.module === state.module));
    refs.levels.forEach((b) => b.classList.toggle("is-active", Number(b.dataset.level) === state.level));
    syncModuleButtons();
    resetForNewSet();
    loadStats();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
