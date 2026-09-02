// progression-library.js v3
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
  const STRANDS_MAX_INSERT_RATIO = 0.5;
  const STRANDS_MAX_LINK_MODULES = 3;
  const STRANDS_LINK_INTENSITIES = {
    light: { label: "輕量", modules: 1, extraConnector: false, connectorBias: "close" },
    standard: { label: "標準", modules: 2, extraConnector: false, connectorBias: "balanced" },
    evolution: { label: "進化", modules: 3, extraConnector: true, connectorBias: "wide" },
  };

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
    "6/9":  { iv: [0, 4, 7, 9, 14],   sfx: "6/9",   min: false },
    "11":   { iv: [0, 4, 7, 10, 14, 17], sfx: "11", min: false },
    m11:     { iv: [0, 3, 7, 10, 14, 17], sfx: "m11", min: true },
    "13":   { iv: [0, 4, 7, 10, 14, 21], sfx: "13", min: false },
    sus2:    { iv: [0, 2, 7],              sfx: "sus2", min: false },
    sus4:    { iv: [0, 5, 7],              sfx: "sus4", min: false },
    // Altered / colour dominants and minor-major (custom-progression input).
    "7b9":   { iv: [0, 4, 7, 10, 13],      sfx: "7♭9",  min: false },
    "7#9":   { iv: [0, 4, 7, 10, 15],      sfx: "7♯9",  min: false },
    "7b5":   { iv: [0, 4, 6, 10],          sfx: "7♭5",  min: false },
    "7#5":   { iv: [0, 4, 8, 10],          sfx: "7♯5",  min: false },
    "7b13":  { iv: [0, 4, 7, 10, 20],      sfx: "7♭13", min: false },
    "7#11":  { iv: [0, 4, 7, 10, 18],      sfx: "7♯11", min: false },
    "maj7#11": { iv: [0, 4, 7, 11, 18],    sfx: "maj7♯11", min: false },
    "m7b9":  { iv: [0, 3, 7, 10, 13],      sfx: "m7♭9", min: true },
    mmaj7:   { iv: [0, 3, 7, 11],          sfx: "m(maj7)", min: true },
    "9sus4": { iv: [0, 5, 7, 10, 14],      sfx: "9sus4", min: false },
    "m6/9":  { iv: [0, 3, 7, 9, 14],       sfx: "m6/9",  min: true },
  };

  function normDeg(v) {
    return ((v % 12) + 12) % 12;
  }

  function vOf(targetDeg) {
    return [normDeg(targetDeg + 7), "7"];
  }

  function dimLeadTo(targetDeg) {
    return [normDeg(targetDeg - 1), "dim7"];
  }

  function tritoneSubOfV() {
    return [1, "7"];
  }

  function buildStrandsProgression(progId, keyObj) {
    const isFlatKey = keyObj && keyObj.sharp === false;
    const fiveAlt = isFlatKey ? tritoneSubOfV() : [7, "7sus4"];
    switch (progId) {
      case "strand_colors":
        // modal interchange + backdoor flavor (4 chords)
        return [[0, "maj7"], [5, "m7"], [10, "7"], [0, "6/9"]];
      case "strand_bridge":
        // secondary dominant -> ii-V -> tonic (5 chords)
        return [[0, "maj7"], vOf(2), [2, "m7"], [7, "13"], [0, "6/9"]];
      case "strand_lift":
        // diminished approach + ii-V alternative (6 chords)
        return [[0, "maj7"], [9, "m7"], dimLeadTo(2), [2, "m9"], fiveAlt, [0, "maj9"]];
      case "strand_run":
        // extended turnaround with strand-inspired substitutions (8 chords)
        return [[0, "maj7"], vOf(9), [9, "m7"], vOf(2), [2, "m9"], tritoneSubOfV(), [0, "maj7"], [7, "13"]];
      default:
        return [[0, "maj7"], [9, "m7"], [2, "m7"], [7, "7"]];
    }
  }

  const EVOLUTION_MODULES = [
    { id: "evo_pop_axis", role: "open", chords: [[0, "maj"], [7, "maj"], [9, "m"], [5, "maj"]] },
    { id: "evo_jazz_turn", role: "release", chords: [[2, "m7"], [7, "7"], [0, "maj7"]] },
    { id: "evo_modal_lift", role: "modal", chords: [[0, "maj"], [10, "maj"], [5, "maj"], [0, "maj"]] },
    { id: "evo_borrow_color", role: "color", chords: [[5, "maj"], [5, "m"], [0, "maj"]] },
    { id: "evo_secondary_push", role: "tension", chords: [[0, "maj"], [4, "7"], [9, "m"]] },
    { id: "evo_circle_release", role: "release", chords: [[9, "m7"], [2, "m7"], [7, "7"], [0, "maj7"]] },
    { id: "evo_dominant_walk", role: "tension", chords: [[0, "maj7"], [9, "7"], [2, "m7"], [7, "7"]] },
  ];

  function cloneProgressionSpecs(specs) {
    return (Array.isArray(specs) ? specs : []).map((pair) => [normDeg(Number(pair && pair[0])), String(pair && pair[1] || "")]);
  }

  function pickRandom(items) {
    if (!Array.isArray(items) || !items.length) return null;
    return items[Math.floor(Math.random() * items.length)];
  }

  function progressionFirstDegree(specs) {
    const first = Array.isArray(specs) && specs.length ? specs[0] : null;
    return first ? normDeg(Number(first[0])) : 0;
  }

  function makeLinkConnector(prevSpec, nextSpec, strength) {
    const prevDeg = progressionFirstDegree([prevSpec]);
    const nextDeg = progressionFirstDegree([nextSpec]);
    const step = (nextDeg - prevDeg + 12) % 12;
    const mode = (strength && strength.connectorBias) || "balanced";

    if (step === 0 || step === 1 || step === 2 || step === 10 || step === 11) return [];
    if (nextDeg === 0) return mode === "close" ? [[7, "7"]] : [[2, "m7"], [7, "7"]];
    if (nextDeg === 2 || nextDeg === 9) return mode === "close" ? [[7, "7"]] : [[normDeg(nextDeg + 7), "7"]];
    if (nextDeg === 5 || nextDeg === 8) return [[normDeg(nextDeg - 1), "dim7"]];
    if (nextDeg === 10) return mode === "close" ? [[5, "7"]] : [[5, "m7"], [10, "7"]];
    return mode === "wide" ? [[normDeg(nextDeg - 1), "dim7"], [normDeg(nextDeg + 7), "7"]] : [[normDeg(nextDeg + 7), "7"]];
  }

  function appendLinkedSpecs(out, segment, strength) {
    const next = cloneProgressionSpecs(segment);
    if (!next.length) return;

    if (!out.length) {
      out.push(...next);
      return;
    }

    const prev = out[out.length - 1];
    const first = next[0];
    const connector = makeLinkConnector(prev, first, strength);

    connector.forEach((spec) => {
      const last = out[out.length - 1];
      if (!last || last[0] !== spec[0] || last[1] !== spec[1]) {
        out.push(spec);
      }
    });

    next.forEach((spec, idx) => {
      if (idx === 0) {
        const last = out[out.length - 1];
        if (last && last[0] === spec[0] && last[1] === spec[1]) return;
      }
      out.push(spec);
    });
  }

  function buildEvolutionaryStrandsProgression(baseProgId, keyObj, strengthKey = "standard") {
    const base = cloneProgressionSpecs(buildStrandsProgression(baseProgId, keyObj));
    const pool = EVOLUTION_MODULES.filter((mod) => mod.id !== baseProgId);
    if (!pool.length) return base;

    const strength = STRANDS_LINK_INTENSITIES[strengthKey] || STRANDS_LINK_INTENSITIES.standard;

    const picks = [];
    const targetCount = Math.max(1, Math.min(STRANDS_MAX_LINK_MODULES, strength.modules));
    const roleOrder = ["tension", "modal", "color", "release", "open"];
    const used = new Set([baseProgId]);

    for (const role of roleOrder) {
      if (picks.length >= targetCount) break;
      const candidates = pool.filter((mod) => !used.has(mod.id) && mod.role === role);
      const pick = pickRandom(candidates);
      if (pick) {
        picks.push(pick);
        used.add(pick.id);
      }
    }

    while (picks.length < targetCount) {
      const fallback = pickRandom(pool.filter((mod) => !used.has(mod.id)));
      if (!fallback) break;
      picks.push(fallback);
      used.add(fallback.id);
    }

    const out = [];
    appendLinkedSpecs(out, base, strength);
    picks.forEach((mod) => appendLinkedSpecs(out, mod.chords, strength));

    if (strength.extraConnector && picks.length >= 2) {
      const tail = picks[picks.length - 1] && picks[picks.length - 1].chords;
      const penultimate = picks[picks.length - 2] && picks[picks.length - 2].chords;
      if (tail && penultimate) {
        appendLinkedSpecs(out, [[2, "m7"], [7, "7"], [0, "maj7"]], strength);
      }
    }
    return out.length ? out : base;
  }

  function applyHalfstepApproachDominants(chords) {
    if (!Array.isArray(chords) || chords.length < 2) return Array.isArray(chords) ? chords.slice() : [];
    const out = [];
    let inserted = 0;
    const maxInsert = Math.max(1, Math.floor(chords.length * STRANDS_MAX_INSERT_RATIO));

    for (let i = 0; i < chords.length; i++) {
      const cur = chords[i];
      const curDeg = normDeg(Number(cur && cur[0]));
      // Density guard: only approach every other transition and cap total inserts.
      if (i > 0 && (i % 2 === 1) && inserted < maxInsert) {
        const approach = [normDeg(curDeg + 1), "7"];
        const prev = out[out.length - 1];
        if (!(prev && prev[0] === approach[0] && prev[1] === approach[1])) {
          out.push(approach);
          inserted += 1;
        }
      }

      out.push([curDeg, String((cur && cur[1]) || "")]);
    }

    return out;
  }

  // Each progression: chords = [[degreeSemitone, quality], ...] relative to key root.
  const LIBRARY = {
    pop: {
      // Popular progressions per hooktheory.com/theorytab/popular-chord-progressions.
      name: "流行 / 一般",
      progressions: [
        { id: "axis",   name: "萬用四和弦 I–V–vi–IV", desc: "最熱門的進行，無數西洋／華語流行歌的骨架。", chords: [[0,"maj"],[7,"maj"],[9,"m"],[5,"maj"]] },
        { id: "6451",   name: "情歌 6451 vi–IV–I–V", desc: "從 vi 起頭，帶點傷感的抒情骨架。", chords: [[9,"m"],[5,"maj"],[0,"maj"],[7,"maj"]] },
        { id: "doowop", name: "50s 進行 / Those magic changes  I–vi–IV–V", desc: "Doo-wop / 老式情歌的經典循環。", chords: [[0,"maj"],[9,"m"],[5,"maj"],[7,"maj"]] },
        { id: "vi545",  name: "vi–V–IV–V", desc: "各曲風通吃的循環 (Effective in all genres)。", chords: [[9,"m"],[7,"maj"],[5,"maj"],[7,"maj"]] },
        { id: "i4645",  name: "I–IV–vi–V", desc: "近年竄紅的排列 (Gaining popularity)。", chords: [[0,"maj"],[5,"maj"],[9,"m"],[7,"maj"]] },
        { id: "i545",   name: "I–V–IV–V", desc: "歷久不衰的三和弦循環 (Timeless)。", chords: [[0,"maj"],[7,"maj"],[5,"maj"],[7,"maj"]] },
        { id: "canon",  name: "卡農 I–V–vi–iii–IV–I–IV–V", desc: "帕海貝爾卡農，下行低音線。", chords: [[0,"maj"],[7,"maj"],[9,"m"],[4,"m"],[5,"maj"],[0,"maj"],[5,"maj"],[7,"maj"]] },
        { id: "royal",  name: "王道進行 IV–V–iii–vi", desc: "日系王道進行 (4536)，動畫／J-pop 常見。", chords: [[5,"maj7"],[7,"7"],[4,"m7"],[9,"m7"]] },
        { id: "vofvi",  name: "I–V7/vi–vi 副屬和弦", desc: "用 vi 的屬和弦 (V/vi) 加深色彩。", chords: [[0,"maj"],[4,"7"],[9,"m"]] },
        { id: "iv_iv_i", name: "IV–iv–I 借用小調 iv", desc: "大調 IV 轉小調 iv 的鄉愁感終止。", chords: [[5,"maj"],[5,"m"],[0,"maj"]] },
        { id: "mixo",   name: "I–♭VII–IV 米索利地安", desc: "搖滾常見的下屬調式 vamp (Sweet Home Alabama)。", chords: [[0,"maj"],[10,"maj"],[5,"maj"]] },
        { id: "bvi_v",  name: "I–♭VI–V", desc: "用 ♭VI 製造張力再解決到 V。", chords: [[0,"maj"],[8,"maj"],[7,"maj"]] },
        { id: "251pop", name: "大調 ii–V–I", desc: "最基本的解決進行。", chords: [[2,"m7"],[7,"7"],[0,"maj7"]] },
      ],
    },
    jazz: {
      // Progressions follow jazzguitar.be "Jazz Chord Progressions".
      name: "爵士 Jazz",
      progressions: [
        { id: "251maj", name: "大調 ii–V–I", desc: "爵士最核心的 2-5-1，幾乎每首標準曲都有。", chords: [[2,"m7"],[7,"7"],[0,"maj7"]] },
        { id: "251min", name: "小調 iiø–V7–i", desc: "小調 2-5-1，半減七起頭，常配 7alt。", chords: [[2,"m7b5"],[7,"7"],[0,"m7"]] },
        { id: "rhythm", name: "Rhythm Changes A (I–vi–ii–V–iii–VI–ii–V)", desc: "I Got Rhythm 的 A 段，1-6-2-5 迴轉骨架。", chords: [[0,"maj7"],[9,"m7"],[2,"m7"],[7,"7"],[4,"m7"],[9,"7"],[2,"m7"],[7,"7"]] },
        { id: "atrain", name: "Take the A Train (I–II7–ii–V)", desc: "II7 副屬 (V/V) 延遲解決，Ellington 名曲。", chords: [[0,"maj7"],[2,"7"],[2,"m7"],[7,"7"]] },
        { id: "1625",   name: "1625 迴轉 I–VI7–ii–V", desc: "帶屬功能 VI7 的經典迴轉。", chords: [[0,"maj7"],[9,"7"],[2,"m7"],[7,"7"]] },
        { id: "dimpass", name: "減和弦經過 Imaj7–♯i°–ii–♯ii°–iii–VI7", desc: "減七經過和弦串起半音上行低音 (Easy Living)。", chords: [[0,"maj7"],[1,"dim7"],[2,"m7"],[3,"dim7"],[4,"m7"],[9,"7"]] },
        { id: "desc251", name: "下行 ii–V–I (全音下行)", desc: "每組 2-5-1 往下移全音，How High the Moon／Tune Up。", chords: [[0,"maj7"],[0,"m7"],[5,"7"],[10,"maj7"],[10,"m7"],[3,"7"],[8,"maj7"],[8,"m7"],[1,"7"]] },
        { id: "circle5", name: "五度圈 vi–ii–V–I–IV–viiø–III7–vi", desc: "混合大／小／半減／屬和弦的五度圈下行。", chords: [[9,"m7"],[2,"m7"],[7,"7"],[0,"maj7"],[5,"maj7"],[11,"m7b5"],[4,"7"],[9,"m7"]] },
        { id: "miniv",  name: "小調 iv 借用 I–I7–IV–iv–iii–VI–ii–V", desc: "IVmaj7→ivm7 的鄉愁感借用和弦 (All of Me)。", chords: [[0,"maj7"],[0,"7"],[5,"maj7"],[5,"m7"],[4,"m7"],[9,"7"],[2,"m7"],[7,"7"]] },
        { id: "backdoor", name: "後門 ii–V  ivm7–♭VII7–I", desc: "從小調 iv 側逼近 Imaj7 的後門解決。", chords: [[5,"m7"],[10,"7"],[0,"maj7"]] },
        { id: "itoiv",  name: "I 到 IV  Imaj7–(ii–V)–IVmaj7", desc: "用 IV 的 2-5 過渡到 IVmaj7。", chords: [[0,"maj7"],[7,"m7"],[0,"7"],[5,"maj7"]] },
        { id: "straycat", name: "Stray Cat Strut  i–♭VI7–V7", desc: "小調迴轉，下行低音的搖擺感。", chords: [[0,"m7"],[8,"7"],[7,"7"]] },
        { id: "36251",  name: "iii–vi–ii–V–I", desc: "延伸的下行五度迴圈。", chords: [[4,"m7"],[9,"m7"],[2,"m7"],[7,"7"],[0,"maj7"]] },
        { id: "jazz_ii_v13_i69", name: "ii9–V13–I6/9", desc: "你提到的 Dm9–G13–C6/9 類型延伸和弦解決。", chords: [[2,"m9"],[7,"13"],[0,"6/9"]] },
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
    lofi: {
      // Lo-fi hip-hop / chill progressions. Sources: melodics.com, landr.com, mondoloops.com, richardpryn.com.
      name: "Lo-fi / Chill",
      progressions: [
        { id: "lofi_major_stack",  name: "Imaj9–IVmaj9  大調堆疊", desc: "兩個大調九和弦，最簡單也最飄的 lo-fi 底色。", chords: [[0,"maj9"],[5,"maj9"]] },
        { id: "lofi_i_vi_ii_v",    name: "Imaj7–vim7–iim7–V7  標準抒情", desc: "lo-fi 最常見的四和弦迴圈，厚擴音 voicing 一秒入味。", chords: [[0,"maj7"],[9,"m7"],[2,"m7"],[7,"7"]] },
        { id: "lofi_i_v_vi_iv",    name: "Imaj9–V7–vim9–IVmaj9  夢幻 I-V-vi-IV", desc: "萬用四和弦加九度，lo-fi 版本。", chords: [[0,"maj9"],[7,"7"],[9,"m9"],[5,"maj9"]] },
        { id: "lofi_para",         name: "im9–♭iim9  半音平行移動", desc: "兩個小九和弦半音移位，lo-fi 的慵懶色彩來源。", chords: [[0,"m9"],[1,"m9"]] },
        { id: "lofi_minor_neo",    name: "im9–♭VImaj9  小調轉大調", desc: "小調出發，挪到同根大調九和弦，帶點日落感。", chords: [[0,"m9"],[8,"maj9"]] },
        { id: "lofi_minor_vamp",   name: "im9–IVm9–im9–V7  小調 Vamp", desc: "i–iv 來回的 lo-fi 標準小調循環，V7 收尾。", chords: [[0,"m9"],[5,"m9"],[0,"m9"],[7,"7"]] },
        { id: "lofi_bvii",         name: "im9–♭VIImaj7  借用下屬", desc: "從 Dorian/Aeolian 借來的 ♭VII，帶城市夜晚氣息。", chords: [[0,"m9"],[10,"maj7"]] },
        { id: "lofi_chromatic",    name: "im9–♭iim7  半音滑動", desc: "只有兩個和弦，半音下滑，極簡最有效。", chords: [[0,"m9"],[1,"m7"]] },
        { id: "lofi_251_ext",      name: "im9–IVm9–iim7♭5–V7  lo-fi 251 擴展", desc: "小調 2-5-1 加入 iv，半減七帶點憂鬱色彩。", chords: [[0,"m9"],[5,"m9"],[2,"m7b5"],[7,"7"]] },
        { id: "lofi_circle_maj",   name: "Imaj9–♭VIImaj9–Vmaj9  大調圓圈", desc: "全大調九和弦圓圈，清爽的日系 lo-fi 感。", chords: [[0,"maj9"],[10,"maj9"],[7,"maj9"]] },
      ],
    },
    worship: {
      // Worship / Praise progressions compiled from:
      // worshiparts.net, hearandplay.com, learngospelmusic.com
      name: "敬拜 Worship",
      progressions: [
        // ── 基礎骨架 ──
        { id: "basic_145",      name: "I–IV–V–I  基本敬拜", desc: "最經典的敬拜進行，簡潔有力。(worshiparts.net)", chords: [[0,"maj"],[5,"maj"],[7,"maj"],[0,"maj"]] },
        { id: "axis_worship",   name: "I–V–vi–IV  流行敬拜", desc: "現代敬拜最常用的四和弦，感情豐沛。", chords: [[0,"maj"],[7,"maj"],[9,"m"],[5,"maj"]] },
        { id: "vi_worship",     name: "vi–IV–I–V  內斂敬拜", desc: "從 vi 起頭，低迴然後漸高，適合沈思祈禱。", chords: [[9,"m"],[5,"maj"],[0,"maj"],[7,"maj"]] },
        { id: "lift_145",       name: "I–IV–I–V  提昇進行", desc: "重複 I–IV 製造期待感，最後 V 迎向高峰。", chords: [[0,"maj"],[5,"maj"],[0,"maj"],[7,"maj"]] },
        { id: "anthemic_4151",  name: "IV–I–V–I  讚歌進行", desc: "以 IV 起頭的雄壯進行，大合唱的標準骨架。", chords: [[5,"maj"],[0,"maj"],[7,"maj"],[0,"maj"]] },
        { id: "modern_v451",    name: "V–IV–I  現代上升", desc: "從屬和弦起頭往下解決，帶衝擊力的詩歌開場。", chords: [[7,"maj"],[5,"maj"],[0,"maj"]] },
        // ── 上升/推進型 ──
        { id: "i_iii_iv_v",     name: "I–iii–IV–V  1-3-4-5 上升", desc: "大調音階逐步上升，Hillsong 早期敬拜骨架。(learngospelmusic)", chords: [[0,"maj"],[4,"m"],[5,"maj"],[7,"maj"]] },
        { id: "iv_v_vi",        name: "IV–V–vi  4-5-6 推進", desc: "三和弦向上推進，強調高潮前的期待感。(learngospelmusic)", chords: [[5,"maj"],[7,"maj"],[9,"m"]] },
        { id: "ii_iv_i_v",      name: "ii–IV–I–V  2-4-1-5", desc: "ii 起頭繞一圈解決，worshiparts.net 推薦的敬拜骨架。", chords: [[2,"m"],[5,"maj"],[0,"maj"],[7,"maj"]] },
        { id: "full_diatonic",  name: "I–IV–iii–vi–ii–V–I  全音階進行", desc: "除了 vii° 以外的七個大調和弦全部用到，worshiparts.net 練習用標準進行。", chords: [[0,"maj"],[5,"maj"],[4,"m"],[9,"m"],[2,"m"],[7,"maj"],[0,"maj"]] },
        // ── Sus / 懸置解決 ──
        { id: "worship_sus",    name: "Vsus4–V–I  懸置解決", desc: "G7sus4→G→C：敬拜音樂最常見的懸置解決，製造強烈期待。", chords: [[7,"sus4"],[7,"maj"],[0,"maj"]] },
        { id: "worship_add9",   name: "Iadd9–IVadd9  Add9 清新 Vamp", desc: "加上九音的清透質感，現代讚美詩清新風格。(learngospelmusic)", chords: [[0,"add9"],[5,"add9"],[0,"add9"],[7,"add9"]] },
        // ── Gospel 借用色彩 ──
        { id: "gospel_borrow",  name: "I–I7–IVmaj7–iv6  Gospel 借用", desc: "對應 C–C7–Fmaj7–Fm6 的教會常見色彩。", chords: [[0,"maj"],[0,"7"],[5,"maj7"],[5,"m6"]] },
        { id: "gospel_chrom",   name: "I–II–iv–I  半音 Gospel 接近", desc: "C–D–Fm–C：大調 II 接小 iv 的半音色彩，Gospel / hearandplay.com 敬拜進行 #1。", chords: [[0,"maj"],[2,"maj"],[5,"m"],[0,"maj"]] },
        { id: "bvii_iv_ivm",    name: "I–♭VII–IV–iv  Gospel 結尾", desc: "C–Bb–F–Fm：♭VII 到 IV 再借用小 iv 收尾，hearandplay.com 敬拜進行 #2。", chords: [[0,"maj"],[10,"maj"],[5,"maj"],[5,"m"]] },
        // ── 現代敬拜 Build / 推進 ──
        { id: "bvi_bvii_i",     name: "♭VI–♭VII–I  現代敬拜 Build", desc: "Ab–Bb–C：從 ♭vi 往上推進到 I，Hillsong／Bethel 最標誌性的 Build-up。(hearandplay #3)", chords: [[8,"maj"],[10,"maj"],[0,"maj"]] },
        { id: "iv_bvii_i",      name: "IV–♭VII–I  反向推進", desc: "F–Bb–C：下屬 ♭VII 接 I，learngospelmusic 4-♭7-1 敬拜進行。", chords: [[5,"maj"],[10,"maj"],[0,"maj"]] },
        // ── 七和弦質感 ──
        { id: "worship_maj7",   name: "Imaj7–IVmaj7–Vmaj7–Imaj7  醇厚敬拜", desc: "七度和弦的豐厚質感，現代讚美詩的層次感。", chords: [[0,"maj7"],[5,"maj7"],[7,"maj7"],[0,"maj7"]] },
        { id: "hymn_circle",    name: "vi–ii–V–I  聖詩圓進行", desc: "傳統聖詩的五度圈下行，肅穆莊嚴。", chords: [[9,"m"],[2,"m"],[7,"maj"],[0,"maj"]] },
        // ── 小調敬拜 ──
        { id: "minor_145",      name: "im–iv–V  小調敬拜", desc: "C minor 敬拜的基本骨架，莊嚴沉穩。(learngospelmusic Minor 1-4-5)", chords: [[0,"m"],[5,"m"],[7,"maj"]] },
        // ── 根音固定 Pedal-C 進行 (低音持續 I，上方和聲移動) ──
        { id: "pedal_march_1",  name: "敬拜進行曲 1  I–D/I–Fm/I–I", desc: "低音持續在根音，上方和聲 C→D→Fm→C，常見於敬拜前奏、間奏。", chords: [[0,"maj"],[2,"maj",0],[5,"m",0],[0,"maj"]] },
        { id: "pedal_march_2",  name: "敬拜進行曲 2  I–Bb/I–F/I–Fm/I", desc: "低音持續在根音，上方和聲 C→Bb→F→Fm，Bb 轉 F 再借小 iv，有推進感後帶著鄉愁收尾。", chords: [[0,"maj"],[10,"maj",0],[5,"maj",0],[5,"m",0]] },
        { id: "pedal_hymn_open", name: "固定根音 A  I–IV/I–V/I–IV/I", desc: "現代讚美詩常見的開闊鋪墊：C→F/C→G/C→F/C，低音穩定、上方榮耀感推進。", chords: [[0,"maj"],[5,"maj",0],[7,"maj",0],[5,"maj",0]] },
        { id: "pedal_minor_prayer", name: "固定根音 B  vi/I–V/I–IV/I–V/I", desc: "內省禱告色彩：Am/C→G/C→F/C→G/C，低音不動但和聲帶出悔改與渴慕的張力。", chords: [[9,"m",0],[7,"maj",0],[5,"maj",0],[7,"maj",0]] },
        { id: "pedal_line_cliche", name: "固定根音 C  I–Imaj7–I7–IV/I", desc: "內聲部下行 line cliche：1→7→b7→6（C→Cmaj7→C7→F/C），史詩感與情緒遞進兼具。", chords: [[0,"maj"],[0,"maj7"],[0,"7"],[5,"maj",0]] },
      ],
    },
    modal: {
      name: "調式 Modal",
      progressions: [
        // ── Mixolydian (大調 ♭7，特色：v 小和弦、♭VII 大和弦) ──
        { id: "mixo_classic",  name: "I–♭VII–IV–I  Mixolydian 經典", desc: "混合利地安最基本形態，Sweet Home Alabama / Lay Down Sally 骨架。", chords: [[0,"maj"],[10,"maj"],[5,"maj"],[0,"maj"]] },
        { id: "mixo_vm11",     name: "I–vm11–IV–I  Mixolydian 小五和弦", desc: "C–Gm11–F–C：混合利地安獨特的 v 小和弦，帶飄逸的模態色彩。", chords: [[0,"maj"],[7,"m11"],[5,"maj"],[0,"maj"]] },
        { id: "mixo_iv_bvii",  name: "I–IV–♭VII–I  Mixolydian 回旋", desc: "IV 往 ♭VII 下行再回 I，Rock / 民搖的基本 riff。", chords: [[0,"maj"],[5,"maj"],[10,"maj"],[0,"maj"]] },
        { id: "mixo_vm_bvii",  name: "I–vm–♭VII–IV  Mixolydian 延伸", desc: "Gm→♭VII→IV 串接，用小 v 突顯調式色彩。", chords: [[0,"maj"],[7,"m"],[10,"maj"],[5,"maj"]] },
        { id: "mixo_sus",      name: "Isus4–I–♭VII–IV  Mixolydian Sus 開場", desc: "Sus4 懸掛後解決再入 ♭VII，開闊的音場感。", chords: [[0,"sus4"],[0,"maj"],[10,"maj"],[5,"maj"]] },
        // ── Dorian (小調 ♯6，特色：IV 大和弦) ──
        { id: "dorian_i_iv",   name: "im–IV–im–IV  Dorian Vamp", desc: "Dorian 調式最核心的 i–IV 來回，So What / Scarborough Fair 底色。", chords: [[0,"m7"],[5,"maj"],[0,"m7"],[5,"maj"]] },
        { id: "dorian_full",   name: "im7–IV–♭VII–im7  Dorian 圓圈", desc: "加入 ♭VII 的完整 Dorian 迴轉，Funk / Soul 常見。", chords: [[0,"m7"],[5,"maj"],[10,"maj"],[0,"m7"]] },
        // ── Phrygian (小調 ♭2，特色：♭II 大和弦) ──
        { id: "phryg_classic",  name: "i–♭II–i  Phrygian 終止", desc: "Phrygian 最標誌性的 ♭II 和弦，佛朗明哥 / 金屬搖滾常用。", chords: [[0,"m"],[1,"maj"],[0,"m"]] },
        { id: "phryg_andal",   name: "i–♭VII–♭VI–♭II  安達魯西亞 Phrygian", desc: "下行低音線 i→♭VII→♭VI→♭II，最具異國感的終止。", chords: [[0,"m"],[10,"maj"],[8,"maj"],[1,"maj"]] },
      ],
    },
    strands: {
      name: "Strands 模式",
      progressions: [
        { id: "strand_colors", name: "Strands 色彩 4 和弦", desc: "規則生成：Modal interchange + backdoor，4 和弦。", generated: true },
        { id: "strand_bridge", name: "Strands 橋接 5 和弦", desc: "規則生成：Secondary dominant 接 ii-V，再回主和弦。", generated: true },
        { id: "strand_lift", name: "Strands 推進 6 和弦", desc: "規則生成：Diminished leading + V 替代，6 和弦。", generated: true },
        { id: "strand_run", name: "Strands 迴轉 8 和弦", desc: "規則生成：延伸 turnaround，含 tritone / secondary 元素。", generated: true },
      ],
    },
    custom: {
      // User-collected progressions, loaded from /api/progression/custom.
      name: "✏️ 我的收集",
      progressions: [],
    },
  };
  const CUSTOM_STYLE = "custom";
  const CUSTOM_API = "/api/progression/custom";

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
    strandsHalfstepApproach: false,
    strandsLinkEvolution: false,
    strandsLinkIntensity: "standard",
    collapsed: false,
    activeIndex: 0,
    chords: [],
    rawChords: [],
    synthReady: false,
  };

  const refs = {
    controls: ROOT.querySelector(".proglib-controls"),
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
    matches: document.getElementById("proglibMatches"),
    matchCount: document.getElementById("proglibMatchCount"),
    pager: document.getElementById("proglibMatchPager"),
    strandsToggleWrap: null,
    strandsToggle: null,
    strandsLinkWrap: null,
    strandsLinkToggle: null,
    strandsLinkIntensity: null,
    addBtn: null,
    editor: null,
  };
  let _customEditingId = null;

  // Chord qualities that count as minor-family for progression matching.
  const MINOR_QUALITIES = new Set(["m", "m7", "m9", "m11", "m6", "dim", "dim7", "m7b5"]);
  const MATCH_PAGE_SIZE = 16;
  const MATCH_PAGE_KEY = "livechord_proglib_matchpage";
  let _matchToken = 0;
  let _matchSeq = null;
  let _matchPage = 0;
  let _matchTotal = 0;

  let synth = null;
  let outputGain = null;
  let reverb = null;
  let loopTimer = null;
  let loopBusy = false;

  function init() {
    hydrateVolume();
    hydrateSettings();
    ensureStrandsToggleControl();
    ensureStrandsLinkControl();
    ensureCustomEditor();
    populateControls();
    bindEvents();
    rebuild();
    renderFavorites();
    syncCollapseUi();
    syncPlayUi();
    loadCustomProgressions();
  }

  function currentStyle() {
    return LIBRARY[state.style] || LIBRARY.pop;
  }

  // May return null when the custom category is empty.
  function currentProg() {
    const list = currentStyle().progressions;
    return list.find((p) => p.id === state.progId) || list[0] || null;
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
    syncStrandsToggleUi();
    syncStrandsLinkUi();
  }

  function ensureStrandsToggleControl() {
    if (!refs.controls || refs.strandsToggleWrap) return;
    const wrap = document.createElement("label");
    wrap.className = "proglib-field";
    wrap.id = "proglibStrandsHalfstepWrap";
    wrap.innerHTML =
      `<span>Strands 開關</span>` +
      `<span style="display:flex;align-items:center;gap:8px">` +
      `<input id="proglibStrandsHalfstep" type="checkbox">` +
      `<span>上方半音屬七（低密度）</span>` +
      `</span>`;
    refs.controls.appendChild(wrap);
    refs.strandsToggleWrap = wrap;
    refs.strandsToggle = wrap.querySelector("#proglibStrandsHalfstep");
  }

  function ensureStrandsLinkControl() {
    if (!refs.controls || refs.strandsLinkWrap) return;
    const wrap = document.createElement("label");
    wrap.className = "proglib-field";
    wrap.id = "proglibStrandsLinkWrap";
    wrap.innerHTML =
      `<span>Strands 開關</span>` +
      `<span style="display:flex;align-items:center;gap:8px">` +
      `<input id="proglibStrandsLink" type="checkbox">` +
      `<span>串接和弦（演化式）</span>` +
      `</span>`;
    refs.controls.appendChild(wrap);
    refs.strandsLinkWrap = wrap;
    refs.strandsLinkToggle = wrap.querySelector("#proglibStrandsLink");
    const strength = document.createElement("select");
    strength.id = "proglibStrandsIntensity";
    strength.innerHTML = Object.entries(STRANDS_LINK_INTENSITIES)
      .map(([k, v]) => `<option value="${k}">${v.label}</option>`)
      .join("");
    wrap.appendChild(strength);
    refs.strandsLinkIntensity = strength;
  }

  function syncStrandsToggleUi() {
    if (!refs.strandsToggleWrap || !refs.strandsToggle) return;
    const visible = state.style === "strands";
    refs.strandsToggleWrap.style.display = visible ? "" : "none";
    refs.strandsToggle.checked = !!state.strandsHalfstepApproach;
  }

  function syncStrandsLinkUi() {
    if (!refs.strandsLinkWrap || !refs.strandsLinkToggle) return;
    const visible = state.style === "strands";
    refs.strandsLinkWrap.style.display = visible ? "" : "none";
    refs.strandsLinkToggle.checked = !!state.strandsLinkEvolution;
    if (refs.strandsLinkIntensity) refs.strandsLinkIntensity.value = state.strandsLinkIntensity;
  }

  function refreshProgOptions() {
    const list = currentStyle().progressions;
    if (!list.length) {
      refs.prog.innerHTML = `<option value="">（尚無，按「＋ 新增進行」）</option>`;
      state.progId = "";
      refs.prog.value = "";
      return;
    }
    refs.prog.innerHTML = list.map((p) => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join("");
    if (!list.some((p) => p.id === state.progId)) state.progId = list[0].id;
    refs.prog.value = state.progId;
  }

  function bindEvents() {
    refs.style.addEventListener("change", () => {
      state.style = refs.style.value;
      refreshProgOptions();
      syncStrandsToggleUi();
      syncStrandsLinkUi();
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
    if (refs.strandsToggle) {
      refs.strandsToggle.addEventListener("change", () => {
        state.strandsHalfstepApproach = !!refs.strandsToggle.checked;
        persistSettings();
        rebuild();
      });
    }
    if (refs.strandsLinkToggle) {
      refs.strandsLinkToggle.addEventListener("change", () => {
        state.strandsLinkEvolution = !!refs.strandsLinkToggle.checked;
        persistSettings();
        rebuild();
      });
    }
    if (refs.strandsLinkIntensity) {
      refs.strandsLinkIntensity.addEventListener("change", () => {
        state.strandsLinkIntensity = refs.strandsLinkIntensity.value;
        persistSettings();
        rebuild();
      });
    }
    refs.randomBtn.addEventListener("click", randomize);
    refs.favBtn.addEventListener("click", saveFavorite);
    if (refs.addBtn) refs.addBtn.addEventListener("click", () => openCustomEditor(null));
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
    const styles = Object.keys(LIBRARY).filter((k) => LIBRARY[k].progressions.length);
    state.style = styles[Math.floor(Math.random() * styles.length)];
    const list = LIBRARY[state.style].progressions;
    state.progId = list[Math.floor(Math.random() * list.length)].id;
    state.keyName = KEYS[Math.floor(Math.random() * KEYS.length)].name;
    refs.style.value = state.style;
    refreshProgOptions();
    syncStrandsToggleUi();
    syncStrandsLinkUi();
    refs.key.value = state.keyName;
    persistSettings();
    rebuild();
    restartLoopIfActive(true);
  }

  // ---- build chords from the selected progression + key ----
  function rebuild() {
    const prog = currentProg();
    const key = currentKey();
    if (!prog) {
      state.chords = [];
      state.rawChords = [];
      state.activeIndex = 0;
      stopLoop(false);
      refs.ribbon.innerHTML = "";
      renderDesc();
      if (refs.matches) refs.matches.innerHTML = "";
      if (refs.matchCount) refs.matchCount.textContent = "";
      if (refs.pager) refs.pager.innerHTML = "";
      _matchSeq = null;
      return;
    }
    const rawChords = Array.isArray(prog.chords)
      ? prog.chords
      : (prog.generated ? buildStrandsProgression(prog.id, key) : []);
    const evolvedRaw = (state.style === "strands" && state.strandsLinkEvolution)
      ? buildEvolutionaryStrandsProgression(prog.id, key, state.strandsLinkIntensity)
      : rawChords;
    const effectiveRaw = (state.style === "strands" && state.strandsHalfstepApproach)
      ? applyHalfstepApproachDominants(evolvedRaw)
      : evolvedRaw;
    const chords = effectiveRaw.map(([deg, q, bass]) => buildChord(key, deg, q, bass));
    applyVoiceLeading(chords);
    // Octave dots are relative to the progression's central register, so most
    // notes stay dot-free and only genuine high/low voices get a dot.
    const octs = chords.flatMap((ch) => ch.voicedMidis.map((m) => Math.floor(m / 12) - 1));
    const baseOct = octs.length ? Math.round(octs.reduce((a, b) => a + b, 0) / octs.length) : 4;
    chords.forEach((ch) => {
      ch.jianpu = ch.voicedMidis.map((m) => midiToJianpu(m, ch.sharp, baseOct));
      ch.midis = ch.voicedMidis; // playback follows the voiced realization
    });
    state.chords = chords;
    state.rawChords = effectiveRaw;
    state.activeIndex = 0;
    renderRibbon();
    renderDesc();
    fetchMatches();
    restartLoopIfActive(false);
  }

  // ---- matching library songs -------------------------------------------
  function progSeqString() {
    const seqSource = state.rawChords && state.rawChords.length
      ? state.rawChords
      : ((currentProg() || {}).chords || []);
    return seqSource
      .map(([deg, q]) => `${((deg % 12) + 12) % 12}${MINOR_QUALITIES.has(q) ? "m" : "M"}`)
      .join("-");
  }

  // Called on progression/random change. The match pattern is key-independent,
  // so a key/bpm change doesn't refetch — only a new progression does.
  function fetchMatches() {
    if (!refs.matches) return;
    const seq = progSeqString();
    if (seq === _matchSeq) return;
    _matchSeq = seq;
    _matchPage = _loadPersistedPage(seq); // resume the page for this progression
    doFetchMatches();
  }

  function goToMatchPage(delta) {
    const totalPages = Math.max(1, Math.ceil(_matchTotal / MATCH_PAGE_SIZE));
    const next = Math.min(totalPages - 1, Math.max(0, _matchPage + delta));
    if (next === _matchPage) return;
    _matchPage = next;
    _persistPage(_matchSeq, _matchPage);
    doFetchMatches();
  }

  async function doFetchMatches() {
    const seq = _matchSeq;
    const token = ++_matchToken;
    refs.matches.innerHTML = `<div class="proglib-match-empty">搜尋中…</div>`;
    if (refs.matchCount) refs.matchCount.textContent = "";
    if (refs.pager) refs.pager.innerHTML = "";
    try {
      const offset = _matchPage * MATCH_PAGE_SIZE;
      const res = await fetch(
        `/api/progression/match?seq=${encodeURIComponent(seq)}&limit=${MATCH_PAGE_SIZE}&offset=${offset}`
      );
      if (!res.ok) throw new Error(String(res.status));
      const data = await res.json();
      if (token !== _matchToken) return; // superseded by a newer request
      _matchTotal = data.count || 0;
      // A persisted page can fall out of range if the match set shrank; clamp
      // and refetch from the last valid page.
      const totalPages = Math.max(1, Math.ceil(_matchTotal / MATCH_PAGE_SIZE));
      if (_matchPage > totalPages - 1 && _matchTotal > 0) {
        _matchPage = totalPages - 1;
        _persistPage(_matchSeq, _matchPage);
        doFetchMatches();
        return;
      }
      renderMatches(data);
      renderPagination();
    } catch (e) {
      if (token !== _matchToken) return;
      _matchTotal = 0;
      if (refs.matchCount) refs.matchCount.textContent = "";
      if (refs.pager) refs.pager.innerHTML = "";
      refs.matches.innerHTML = `<div class="proglib-match-empty">此伺服器未提供歌曲比對。</div>`;
    }
  }

  function renderMatches(data) {
    const songs = (data && data.songs) || [];
    if (refs.matchCount) {
      refs.matchCount.textContent = data && data.count ? `共 ${data.count} 首` : "";
    }
    if (!songs.length) {
      refs.matches.innerHTML = `<div class="proglib-match-empty">音樂庫中沒有符合此進行的歌曲。</div>`;
      return;
    }
    refs.matches.innerHTML = songs
      .map((s) => {
        const h = encodeURIComponent(s.hash);
        const title = escapeHtml(s.title || "Untitled");
        const key = escapeHtml(s.key || "");
        // Library songs (real NAS paths) open in path mode so the player can
        // stream the audio; hash mode can't locate it. Uploads (__upload/...)
        // and pathless rows fall back to hash mode.
        const isLib = s.path && !s.path.startsWith("__");
        const href = isLib
          ? `/player?path=${encodeURIComponent(s.path)}&autoplay=1`
          : `/player?hash=${h}`;
        const cover = s.path
          ? `/api/track/cover?path=${encodeURIComponent(s.path)}`
          : `/api/process/cover/${h}`;
        return `<a class="proglib-song-card" href="${href}" title="${title}">
          <span class="proglib-song-cover no-cover"><img src="${cover}" loading="lazy" alt="" onload="this.parentElement.classList.remove('no-cover')" onerror="this.remove()"><span class="proglib-song-ph">♪</span></span>
          <span class="proglib-song-meta"><span class="proglib-song-title">${title}</span>${key ? `<span class="proglib-song-key">${key}</span>` : ""}</span>
        </a>`;
      })
      .join("");
  }

  function renderPagination() {
    if (!refs.pager) return;
    const totalPages = Math.max(1, Math.ceil(_matchTotal / MATCH_PAGE_SIZE));
    if (_matchTotal <= MATCH_PAGE_SIZE) {
      refs.pager.innerHTML = "";
      return;
    }
    refs.pager.innerHTML =
      `<button class="proglib-pg-btn" type="button" data-dir="-1" aria-label="上一頁" ${_matchPage <= 0 ? "disabled" : ""}>‹</button>` +
      `<span class="proglib-pg-info">第 ${_matchPage + 1} / ${totalPages} 頁</span>` +
      `<button class="proglib-pg-btn" type="button" data-dir="1" aria-label="下一頁" ${_matchPage >= totalPages - 1 ? "disabled" : ""}>›</button>`;
    refs.pager.querySelectorAll(".proglib-pg-btn").forEach((b) => {
      b.addEventListener("click", () => goToMatchPage(Number(b.dataset.dir)));
    });
  }

  function _loadPersistedPage(seq) {
    try {
      const o = JSON.parse(localStorage.getItem(MATCH_PAGE_KEY) || "null");
      if (o && o.seq === seq && Number.isFinite(Number(o.page))) return Math.max(0, Number(o.page));
    } catch {}
    return 0;
  }

  function _persistPage(seq, page) {
    try {
      localStorage.setItem(MATCH_PAGE_KEY, JSON.stringify({ seq, page }));
    } catch {}
  }

  function buildChord(key, deg, quality, bassDeg) {
    const qd = QUALITY[quality] || QUALITY.maj;
    const names = key.sharp ? SHARP_NAMES : FLAT_NAMES;
    const rootPc = (key.pc + deg) % 12;
    const tonePcs = qd.iv.map((i) => (key.pc + deg + i) % 12);
    let roman = ROMAN_BASE[((deg % 12) + 12) % 12];
    if (qd.min) roman = roman.toLowerCase();
    const chordName = names[rootPc] + qd.sfx;
    let slash = null;
    if (bassDeg != null) {
      const bassPc = (key.pc + bassDeg) % 12;
      const bassName = names[bassPc];
      if (bassName !== names[rootPc]) slash = bassName;
    }
    const displayName = slash ? `${chordName}/${slash}` : chordName;
    return { name: chordName, slash, displayName, roman, romanSfx: romanSuffix(quality), tonePcs, sharp: key.sharp };
  }

  // ---- voice leading (ported from Ambient Mode) -------------------------
  // First chord is seeded in root position around C4; each subsequent chord
  // keeps common tones at the same register and moves the rest minimally.
  function applyVoiceLeading(chords) {
    if (!chords.length) return;
    let prev = seedVoicing(chords[0].tonePcs, 60);
    chords[0].voicedMidis = prev;
    for (let i = 1; i < chords.length; i++) {
      const v = voiceLeadFromPrev(prev, chords[i].tonePcs);
      chords[i].voicedMidis = v;
      prev = v;
    }
  }

  function seedVoicing(pcs, minMidi) {
    if (!pcs.length) return [];
    const out = [];
    let cursor = minMidi;
    for (let i = 0; i < pcs.length; i++) {
      const midi = nearestMidiAtOrAbove(pcs[i], cursor);
      out.push(midi);
      cursor = midi + 2;
    }
    return out;
  }

  function voiceLeadFromPrev(prevVoicing, targetPcs) {
    if (!prevVoicing.length || !targetPcs.length) return seedVoicing(targetPcs, 60);
    const usedPcs = new Set();
    const next = [];
    const freePrev = [];
    for (let i = 0; i < prevVoicing.length; i++) {
      const midi = prevVoicing[i];
      const pc = midiToPc(midi);
      if (targetPcs.includes(pc) && !usedPcs.has(pc)) {
        next.push(midi);
        usedPcs.add(pc);
      } else {
        freePrev.push(midi);
      }
    }
    const missingPcs = targetPcs.filter((pc) => !usedPcs.has(pc));
    for (let i = 0; i < missingPcs.length; i++) {
      const pc = missingPcs[i];
      const anchor = freePrev[i] != null ? freePrev[i] : (prevVoicing[prevVoicing.length - 1] || 60);
      let midi = nearestMidiTo(pc, anchor);
      while (next.includes(midi)) midi += 12;
      next.push(midi);
    }
    return next.sort((a, b) => a - b);
  }

  function nearestMidiAtOrAbove(pc, minMidi) {
    let m = nearestMidiTo(pc, minMidi);
    while (m < minMidi) m += 12;
    return m;
  }

  function nearestMidiTo(pc, anchorMidi) {
    const base = Math.floor(anchorMidi / 12) * 12 + pc;
    const cands = [base - 12, base, base + 12];
    cands.sort((a, b) => Math.abs(a - anchorMidi) - Math.abs(b - anchorMidi));
    return cands[0];
  }

  function midiToPc(midi) {
    return ((midi % 12) + 12) % 12;
  }

  // Fixed-do solfège token + octave-dot count (relative to baseOct).
  function midiToJianpu(midi, sharp, baseOct) {
    const pc = midiToPc(midi);
    const names = sharp ? SHARP_NAMES : FLAT_NAMES;
    const tok = (names[pc].includes("b") ? JP_FLAT : JP_SHARP)[pc];
    const oct = Math.floor(midi / 12) - 1;
    return { tok, oct: clamp(oct - baseOct, -2, 2) };
  }

  function romanSuffix(quality) {
    if (quality === "m7b5") return "ø";
    if (quality === "dim" || quality === "dim7") return "°";
    if (quality === "maj7" || quality === "maj9") return "△";
    if (/7|9|11|13/.test(quality)) return "7";
    return "";
  }

  // ---- rendering ----
  function renderRibbon() {
    const rows = [];
    for (let i = 0; i < state.chords.length; i += 8) {
      rows.push(state.chords.slice(i, i + 8));
    }
    refs.ribbon.innerHTML = rows
      .map((row, rowIdx) => {
        const startIdx = rowIdx * 8;
        return `<div class="proglib-ribbon-row">${row
          .map((ch, offset) => {
            const idx = startIdx + offset;
            const jianpuHtml = ch.jianpu.map(renderJianpuNote).join("");
            return `
            <button class="proglib-chord ${idx === state.activeIndex ? "is-active" : ""}" data-idx="${idx}" type="button">
              <div class="proglib-chord-roman">${escapeHtml(ch.roman)}<sup>${escapeHtml(ch.romanSfx)}</sup></div>
              <div class="proglib-chord-name" data-len="${Math.min(12, String(ch.displayName || ch.name).length)}">${escapeHtml(ch.displayName || ch.name)}</div>
              <div class="proglib-chord-jianpu">${jianpuHtml}</div>
            </button>`;
          })
          .join("")}</div>`;
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

  function renderJianpuNote(j) {
    const tok = j && j.tok != null ? j.tok : String(j || "");
    const oct = j && typeof j.oct === "number" ? j.oct : 0;
    let acc = "";
    let digit = tok;
    if (tok[0] === "#" || tok[0] === "b") { acc = tok[0]; digit = tok.slice(1); }
    const accHtml = acc ? `<sup class="pl-jp-acc">${acc}</sup>` : "";
    let dotsHtml = "";
    if (oct > 0) dotsHtml = `<span class="pl-jp-dot up">${"•".repeat(Math.min(oct, 2))}</span>`;
    else if (oct < 0) dotsHtml = `<span class="pl-jp-dot down">${"•".repeat(Math.min(-oct, 2))}</span>`;
    return `<span class="pl-jp-note">${accHtml}<span class="pl-jp-d">${escapeHtml(digit)}</span>${dotsHtml}</span>`;
  }

  function renderDesc() {
    const prog = currentProg();
    if (!prog) {
      refs.desc.textContent = state.style === CUSTOM_STYLE
        ? "還沒有收集任何進行。按右上角「＋ 新增進行」，貼上網路上看到的和弦進行即可。"
        : "";
      return;
    }
    if (state.style !== CUSTOM_STYLE) {
      refs.desc.textContent = `${currentStyle().name} · ${prog.name}　—　${prog.desc || ""}`;
      return;
    }
    const srcHtml = prog.source_url
      ? ` <a class="proglib-src-link" href="${escapeHtml(prog.source_url)}" target="_blank" rel="noopener">來源 ↗</a>`
      : "";
    refs.desc.innerHTML =
      `<span>${escapeHtml(currentStyle().name)} · ${escapeHtml(prog.name)}　—　${escapeHtml(prog.desc || "")}${srcHtml}</span>` +
      `<span class="proglib-desc-actions">` +
      `<button class="proglib-mini-btn" type="button" data-action="edit">編輯</button>` +
      `<button class="proglib-mini-btn proglib-mini-danger" type="button" data-action="delete">刪除</button>` +
      `</span>`;
    refs.desc.querySelector("[data-action='edit']").addEventListener("click", () => openCustomEditor(prog));
    refs.desc.querySelector("[data-action='delete']").addEventListener("click", () => deleteCustomProgression(prog));
  }

  // ---- custom progressions (user-collected) ------------------------------
  async function loadCustomProgressions() {
    try {
      const res = await fetch(CUSTOM_API);
      if (!res.ok) throw new Error(String(res.status));
      const data = await res.json();
      LIBRARY.custom.progressions = (data.items || []).map(customToProg);
    } catch {
      LIBRARY.custom.progressions = [];
    }
    if (state.style === CUSTOM_STYLE) {
      refreshProgOptions();
      rebuild();
    }
    renderFavorites();
  }

  function customToProg(it) {
    return {
      id: it.id,
      name: it.name,
      desc: it.desc || "",
      source_url: it.source_url || "",
      input_text: it.input_text || "",
      chords: (it.chords || []).map((c) => (c.length > 2 && c[2] != null ? [c[0], c[1], c[2]] : [c[0], c[1]])),
    };
  }

  // Roman numerals: I..VII with optional b/# prefix. Case decides maj/min
  // unless the suffix says otherwise.
  const ROMAN_VAL = { I: 0, II: 2, III: 4, IV: 5, V: 7, VI: 9, VII: 11 };
  const NOTE_VAL = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };

  function normalizeSuffix(sfx) {
    return String(sfx || "")
      .replace(/[()]/g, "").replace(/♭/g, "b").replace(/♯/g, "#")
      .replace(/^maj/i, "maj").replace(/^min/i, "m").replace(/^sus/i, "sus").replace(/^dim/i, "dim").replace(/^aug/i, "aug").replace(/^add/i, "add")
      .replace(/Δ/g, "maj").replace(/ø7?/g, "m7b5").replace(/°7|o7/g, "dim7").replace(/°|^o$/g, "dim")
      .replace(/^\+$/, "aug").replace(/^7\+$/, "7#5").replace(/^min/, "m").replace(/^M(?=7|9)/, "maj").replace(/^ma(?=7|9)/, "maj")
      .replace(/^mM7$|^mmaj7$|^minmaj7$|^m\/maj7$/, "mmaj7")
      .replace(/^-/, "m").replace(/^69$/, "6/9").replace(/^sus$/, "sus4").replace(/^dom7$/, "7").replace(/^7alt$/, "7#9");
  }

  function suffixToQuality(sfx, lower) {
    const n = normalizeSuffix(sfx);
    if (n === "") return lower ? "m" : "maj";
    if (n === "m") return "m";
    if (n === "7") return lower ? "m7" : "7";
    if (n === "9") return lower ? "m9" : "9";
    if (n === "11") return lower ? "m11" : "11";
    if (n === "6") return lower ? "m6" : "6";
    if (n === "6/9") return lower ? "m6/9" : "6/9";
    if (QUALITY[n]) return n;
    // Unknown alteration tail (e.g. 13b9, 7b9#11): strip trailing alterations
    // one at a time until a known quality remains.
    let base = n;
    while (/(b|#)(5|9|11|13)$|add(9|11|13)$/.test(base)) {
      base = base.replace(/(b|#)(5|9|11|13)$|add(9|11|13)$/, "");
      const q = suffixToQuality(base, lower);
      if (q) return q;
    }
    return null;
  }

  // Parse "I–V–vi–IV", "ii7 V7 Imaj7", "bVII IV I" or "C G Am F" (with keyName)
  // into LIBRARY chord specs. Returns { chords, error }.
  // "C" / "F#" / "Am" / "Bbm" → pitch class of the tonic.
  function keyNameToPc(name) {
    const m = String(name || "").match(/^([A-G])([b#])?(m)?$/);
    if (!m) return 0;
    return normDeg(NOTE_VAL[m[1]] + (m[2] === "b" ? -1 : m[2] === "#" ? 1 : 0));
  }

  const MINOR_KEY_NAMES = ["Am", "Em", "Bm", "F#m", "C#m", "G#m", "Dm", "Gm", "Cm", "Fm", "Bbm", "Ebm"];

  // Guess the key from absolute chord tokens: score all 24 keys by diatonic
  // fit, with a bonus for the tonic at the start / end. Returns "C" or "Am".
  function detectKeyFromChords(parsed) {
    if (!parsed.length) return null;
    const MAJ = { 0: "maj", 2: "m", 4: "m", 5: "maj", 7: "maj", 9: "m", 11: "dim" };
    const MIN = { 0: "m", 2: "dim", 3: "maj", 5: "m", 7: "maj", 8: "maj", 10: "maj" };
    const fam = (q) => (/dim/.test(q) ? "dim" : QUALITY[q] && QUALITY[q].min ? "m" : "maj");
    let best = null;
    for (let tonic = 0; tonic < 12; tonic++) {
      for (const minor of [false, true]) {
        const table = minor ? MIN : MAJ;
        let score = 0;
        parsed.forEach(({ pc, q }, i) => {
          const d = normDeg(pc - tonic);
          const expect = table[d];
          if (expect == null) { score -= 1; return; }
          score += 1;
          const f = fam(q);
          if (f === expect || (minor && d === 7 && f === "maj") || (minor && d === 7 && f === "m")) score += 1;
          if (d === 0 && f === expect) { if (i === 0) score += 3; if (i === parsed.length - 1) score += 2; }
          if (d === 7 && i === parsed.length - 1) score += 1;
        });
        if (!best || score > best.score) best = { score, tonic, minor };
      }
    }
    const names = best.minor ? MINOR_KEY_NAMES : KEYS.map((k) => k.name);
    return names.find((n) => keyNameToPc(n) === best.tonic) || null;
  }

  function parseProgressionInput(text, keyName) {
    const src = String(text || "").replace(/♭/g, "b").replace(/♯/g, "#").replace(/[–—→>|,]/g, " ").trim();
    const tokens = src.split(/[\s-]+/).filter(Boolean);
    if (tokens.length < 2) return { chords: [], error: "至少需要 2 個和弦" };
    const key = { pc: keyNameToPc(keyName) };
    const absolute = [];
    const chords = [];
    for (const rawTok of tokens) {
      // Slash bass: "C/E", "Cmaj9/E", "I/III", "V/VII". "6/9" is not a slash.
      let tok = rawTok;
      let bassTok = null;
      const sm = rawTok.match(/^(.+)\/([A-G][b#]?|[b#]?[ivIV]+)$/);
      if (sm) { tok = sm[1]; bassTok = sm[2]; }
      let deg, lower = false, sfx;
      let m = tok.match(/^([b#])?([ivIV]+)(.*)$/);
      if (m && ROMAN_VAL[m[2].toUpperCase()] != null && (m[2] === m[2].toUpperCase() || m[2] === m[2].toLowerCase())) {
        deg = ROMAN_VAL[m[2].toUpperCase()] + (m[1] === "b" ? -1 : m[1] === "#" ? 1 : 0);
        lower = m[2] === m[2].toLowerCase();
        sfx = m[3];
      } else if ((m = tok.match(/^([A-G])([b#])?(.*)$/))) {
        const pc = normDeg(NOTE_VAL[m[1]] + (m[2] === "b" ? -1 : m[2] === "#" ? 1 : 0));
        deg = pc - key.pc;
        sfx = m[3];
        const aq = suffixToQuality(sfx, false);
        if (aq) absolute.push({ pc, q: aq });
      } else {
        return { chords: [], error: `看不懂的和弦：${tok}` };
      }
      const q = suffixToQuality(sfx, lower);
      if (!q) return { chords: [], error: `不支援的和弦類型：${rawTok}` };
      let bassDeg = null;
      if (bassTok) {
        let bm = bassTok.match(/^([A-G])([b#])?$/);
        if (bm) bassDeg = normDeg(NOTE_VAL[bm[1]] + (bm[2] === "b" ? -1 : bm[2] === "#" ? 1 : 0) - key.pc);
        else if ((bm = bassTok.match(/^([b#])?([ivIV]+)$/)) && ROMAN_VAL[bm[2].toUpperCase()] != null) {
          bassDeg = normDeg(ROMAN_VAL[bm[2].toUpperCase()] + (bm[1] === "b" ? -1 : bm[1] === "#" ? 1 : 0));
        } else return { chords: [], error: `看不懂的低音：${rawTok}` };
      }
      chords.push(bassDeg != null ? [normDeg(deg), q, bassDeg] : [normDeg(deg), q]);
    }
    return { chords, error: null, detectedKey: absolute.length === tokens.length ? detectKeyFromChords(absolute) : null };
  }

  function specsToRoman(specs) {
    return specs.map(([deg, q, bass]) => {
      const qd = QUALITY[q] || QUALITY.maj;
      const base = ROMAN_BASE[normDeg(deg)];
      const r = qd.min ? base.toLowerCase() + qd.sfx.replace(/^m(?!aj)/, "") : base + qd.sfx;
      return bass != null ? `${r}/${ROMAN_BASE[normDeg(bass)]}` : r;
    }).join("–");
  }

  function ensureCustomEditor() {
    const actions = ROOT.querySelector(".proglib-head-actions");
    if (actions && !refs.addBtn) {
      const btn = document.createElement("button");
      btn.id = "proglibAddBtn";
      btn.className = "proglib-btn";
      btn.type = "button";
      btn.textContent = "＋ 新增進行";
      actions.insertBefore(btn, refs.favBtn);
      refs.addBtn = btn;
    }
    if (refs.editor || !refs.desc) return;
    const ed = document.createElement("form");
    ed.id = "proglibCustomEditor";
    ed.className = "proglib-editor";
    ed.hidden = true;
    ed.innerHTML =
      `<div class="proglib-editor-title"></div>` +
      `<label class="proglib-field"><span>名稱</span><input name="name" type="text" maxlength="80" placeholder="例：Creep 進行" required></label>` +
      `<label class="proglib-field"><span>和弦序列（羅馬數字或和弦名）</span><input name="text" type="text" maxlength="300" placeholder="I–III–IV–iv 或 G B C Cm" required></label>` +
      `<label class="proglib-field proglib-field-inkey"><span>和弦名所在的調</span><select name="inkey"></select></label>` +
      `<div class="proglib-editor-preview"></div>` +
      `<label class="proglib-field"><span>備註</span><input name="desc" type="text" maxlength="500" placeholder="出處、用法、感想"></label>` +
      `<label class="proglib-field"><span>來源網址</span><input name="url" type="url" maxlength="500" placeholder="https://"></label>` +
      `<div class="proglib-editor-actions">` +
      `<button class="proglib-btn proglib-btn-accent" type="submit">儲存</button>` +
      `<button class="proglib-btn" type="button" data-action="cancel">取消</button>` +
      `</div>`;
    refs.desc.insertAdjacentElement("afterend", ed);
    refs.editor = ed;
    const inkey = ed.querySelector("[name=inkey]");
    inkey.innerHTML =
      `<optgroup label="大調">${KEYS.map((k) => `<option value="${k.name}">${k.name}</option>`).join("")}</optgroup>` +
      `<optgroup label="小調">${MINOR_KEY_NAMES.map((n) => `<option value="${n}">${n}</option>`).join("")}</optgroup>`;
    const refresh = () => renderEditorPreview();
    ed.querySelector("[name=text]").addEventListener("input", refresh);
    inkey.addEventListener("change", () => { ed.dataset.inkeyTouched = "1"; refresh(); });
    ed.querySelector("[data-action=cancel]").addEventListener("click", closeCustomEditor);
    ed.addEventListener("submit", (evt) => { evt.preventDefault(); submitCustomEditor(); });
  }

  function renderEditorPreview() {
    const ed = refs.editor;
    const text = ed.querySelector("[name=text]").value;
    const usesNames = /(^|[\s,|–—>-])[A-G][b#]?/.test(text.replace(/♭/g, "b").replace(/♯/g, "#"));
    ed.querySelector(".proglib-field-inkey").style.display = usesNames ? "" : "none";
    const out = ed.querySelector(".proglib-editor-preview");
    if (!text.trim()) { out.textContent = ""; out.dataset.ok = ""; return; }
    const inkey = ed.querySelector("[name=inkey]");
    let r = parseProgressionInput(text, inkey.value);
    let autoNote = "";
    // Auto-pick the key from the chord names until the user overrides it.
    if (usesNames && !r.error && r.detectedKey && ed.dataset.inkeyTouched !== "1" && r.detectedKey !== inkey.value) {
      inkey.value = r.detectedKey;
      r = parseProgressionInput(text, inkey.value);
    }
    if (usesNames && !r.error && ed.dataset.inkeyTouched !== "1") autoNote = `（自動判斷 ${inkey.value}，可手動改）`;
    if (r.error) { out.textContent = "⚠ " + r.error; out.dataset.ok = "0"; return; }
    out.textContent = "解析：" + specsToRoman(r.chords) + autoNote;
    out.dataset.ok = "1";
  }

  function openCustomEditor(prog) {
    ensureCustomEditor();
    const ed = refs.editor;
    _customEditingId = prog ? prog.id : null;
    ed.querySelector(".proglib-editor-title").textContent = prog ? `編輯：${prog.name}` : "新增自訂進行";
    ed.querySelector("[name=name]").value = prog ? prog.name : "";
    ed.querySelector("[name=text]").value = prog ? (prog.input_text || specsToRoman(prog.chords)) : "";
    ed.querySelector("[name=inkey]").value = state.keyName;
    ed.dataset.inkeyTouched = "";
    ed.querySelector("[name=desc]").value = prog ? prog.desc : "";
    ed.querySelector("[name=url]").value = prog ? prog.source_url : "";
    ed.hidden = false;
    if (state.collapsed) { state.collapsed = false; persistSettings(); syncCollapseUi(); }
    renderEditorPreview();
    ed.querySelector(prog ? "[name=text]" : "[name=name]").focus();
  }

  function closeCustomEditor() {
    if (refs.editor) refs.editor.hidden = true;
    _customEditingId = null;
  }

  async function submitCustomEditor() {
    const ed = refs.editor;
    const text = ed.querySelector("[name=text]").value;
    const parsed = parseProgressionInput(text, ed.querySelector("[name=inkey]").value);
    if (parsed.error) { showToastSafe(parsed.error); return; }
    const body = {
      name: ed.querySelector("[name=name]").value.trim(),
      chords: parsed.chords,
      desc: ed.querySelector("[name=desc]").value.trim(),
      source_url: ed.querySelector("[name=url]").value.trim(),
      input_text: text.trim(),
    };
    if (!body.name) { showToastSafe("請輸入名稱"); return; }
    const editing = _customEditingId;
    try {
      const res = await fetch(editing ? `${CUSTOM_API}/${encodeURIComponent(editing)}` : CUSTOM_API, {
        method: editing ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `${res.status} ${res.statusText}`);
      const prog = customToProg(data);
      const list = LIBRARY.custom.progressions;
      const idx = list.findIndex((p) => p.id === prog.id);
      if (idx >= 0) list[idx] = prog; else list.push(prog);
      closeCustomEditor();
      state.style = CUSTOM_STYLE;
      state.progId = prog.id;
      refs.style.value = state.style;
      refreshProgOptions();
      syncStrandsToggleUi();
      syncStrandsLinkUi();
      persistSettings();
      rebuild();
      renderFavorites();
      showToastSafe(editing ? "已更新進行" : "已新增到「我的收集」");
    } catch (e) {
      showToastSafe(`儲存失敗：${e.message || e}`);
    }
  }

  async function deleteCustomProgression(prog) {
    if (!prog || !window.confirm(`刪除「${prog.name}」？`)) return;
    try {
      const res = await fetch(`${CUSTOM_API}/${encodeURIComponent(prog.id)}`, { method: "DELETE" });
      if (!res.ok) throw new Error(String(res.status));
      LIBRARY.custom.progressions = LIBRARY.custom.progressions.filter((p) => p.id !== prog.id);
      closeCustomEditor();
      refreshProgOptions();
      persistSettings();
      rebuild();
      renderFavorites();
      showToastSafe("已刪除");
    } catch (e) {
      showToastSafe(`刪除失敗：${e.message || e}`);
    }
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
      .filter((f) => f.style !== CUSTOM_STYLE || LIBRARY.custom.progressions.some((p) => p.id === f.progId))
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
      if (obj.progId && (state.style === CUSTOM_STYLE || (LIBRARY[state.style].progressions || []).some((p) => p.id === obj.progId))) state.progId = obj.progId;
      if (obj.keyName && KEYS.some((k) => k.name === obj.keyName)) state.keyName = obj.keyName;
      if (Number.isFinite(Number(obj.bpm))) state.bpm = clamp(Math.round(Number(obj.bpm)), 40, 180);
      if ([2, 4, 8].includes(Number(obj.beats))) state.beats = Number(obj.beats);
      if (typeof obj.strandsHalfstepApproach === "boolean") state.strandsHalfstepApproach = obj.strandsHalfstepApproach;
      if (typeof obj.strandsLinkEvolution === "boolean") state.strandsLinkEvolution = obj.strandsLinkEvolution;
      if (typeof obj.strandsLinkIntensity === "string" && STRANDS_LINK_INTENSITIES[obj.strandsLinkIntensity]) state.strandsLinkIntensity = obj.strandsLinkIntensity;
      if (typeof obj.collapsed === "boolean") state.collapsed = obj.collapsed;
    } catch {}
  }

  function persistSettings() {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify({
        style: state.style, progId: state.progId, keyName: state.keyName,
        bpm: state.bpm, beats: state.beats, strandsHalfstepApproach: state.strandsHalfstepApproach,
        strandsLinkEvolution: state.strandsLinkEvolution,
        strandsLinkIntensity: state.strandsLinkIntensity,
        collapsed: state.collapsed,
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
