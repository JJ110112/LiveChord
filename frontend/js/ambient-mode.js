// ambient-mode.js v17
(function () {
  const ROOT = document.getElementById("secAmbientMode");
  if (!ROOT) return;

  const FAVORITES_KEY = "livechord_ambient_favorites";
  const FAVORITES_MAX = 16;
  const VOLUME_KEY = "livechord_ambient_volume";
  const SETTINGS_KEY = "livechord_ambient_settings";

  const PRESETS = {
    neo: {
      name: "Neo-Soul Chill",
      roles: ["i9", "iv11", "bVIImaj9", "III7sus"],
      sp404: "推薦 SP-404: Vinyl Sim + Cloud Reverb，Pad A1 放雨聲。",
      starts: [0, 5, 10],
      transitions: {
        0: [5, 10, 3],
        5: [10, 3, 0],
        10: [3, 5, 0],
        3: [0, 5],
      },
      templates: [
        ["m9", "m11", "maj9", "7sus4"],
        ["m9", "maj7", "maj9", "m11"],
      ],
    },
    midnight: {
      name: "Midnight City Pop",
      roles: ["Imaj7", "V/IV", "iiim7", "vim7"],
      sp404: "推薦 SP-404: Cassette Sim + SX Reverb，Pad A1 放列車環境音。",
      starts: [0, 7],
      transitions: {
        0: [7, 4, 9],
        7: [4, 9, 0],
        4: [9, 0, 7],
        9: [0, 7],
      },
      templates: [
        ["maj7", "7", "m7", "m7"],
        ["maj9", "7sus4", "m7", "m9"],
      ],
    },
    drone: {
      name: "Cinematic Drone",
      roles: ["I5(add9)", "IVsus2", "bVIImaj7", "I5(add9)"],
      sp404: "推薦 SP-404: Cloud Reverb + Vinyl Sim，Pad A1 放風聲或空間殘響。",
      starts: [0, 5],
      transitions: {
        0: [5, 10, 0],
        5: [10, 0, 7],
        10: [0, 5],
        7: [0, 5],
      },
      templates: [
        ["sus2", "add9", "maj7", "sus4"],
        ["add9", "sus2", "maj9", "add9"],
      ],
    },
    modal: {
      name: "Modal Drift",
      roles: ["im7", "IV7", "bIIImaj7", "II/I"],
      sp404: "推薦 SP-404: Cassette Sim + Reverb，Pad A1 放低頻城市底噪。",
      starts: [0, 2, 9],
      transitions: {
        0: [2, 5, 9],
        2: [5, 9, 0],
        5: [9, 0, 2],
        9: [0, 2, 5],
      },
      templates: [
        ["m7", "7", "maj7", "sus2"],
        ["m9", "7sus4", "maj9", "sus2"],
      ],
      motifs: [
        { roots: [0, 11], qualities: ["maj7", "m7"], roles: ["Imaj7", "VIIm7"] },
      ],
    },
  };

  // 固定顯示拼法（全降記號，唯 F# 升記號）— 與 utils.js NOTE_NAMES_DISPLAY 一致。
  const NOTE_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];
  const NOTE_NAME_TO_PC = {
    C: 0, "B#": 0,
    "C#": 1, Db: 1,
    D: 2,
    "D#": 3, Eb: 3,
    E: 4, Fb: 4,
    F: 5, "E#": 5,
    "F#": 6, Gb: 6,
    G: 7,
    "G#": 8, Ab: 8,
    A: 9,
    "A#": 10, Bb: 10,
    B: 11, Cb: 11,
  };
  const QUALITY_INTERVALS = {
    triad: [0, 4, 7],
    m7: [0, 3, 7, 10],
    maj7: [0, 4, 7, 11],
    "7": [0, 4, 7, 10],
    m9: [0, 3, 7, 10, 14],
    maj9: [0, 4, 7, 11, 14],
    m11: [0, 3, 7, 10, 14, 17],
    sus2: [0, 2, 7],
    sus4: [0, 5, 7],
    add9: [0, 4, 7, 14],
    "7sus4": [0, 5, 7, 10],
  };

  const state = {
    preset: "neo",
    key: "C",
    bpm: 64,
    density: 2,
    semitoneMode: "prefer",
    holdBeats: 8,
    collapsed: false,
    volume: 0.7,
    progression: [],
    activeIndex: 0,
    rngSeed: Date.now() % 100000,
    synthReady: false,
  };

  const refs = {
    preset: document.getElementById("ambientPreset"),
    key: document.getElementById("ambientKey"),
    bpm: document.getElementById("ambientBpm"),
    bpmVal: document.getElementById("ambientBpmVal"),
    density: document.getElementById("ambientDensity"),
    densityVal: document.getElementById("ambientDensityVal"),
    semitoneMode: document.getElementById("ambientSemitoneMode"),
    holdBeats: document.getElementById("ambientHoldBeats"),
    volume: document.getElementById("ambientVolume"),
    volumeVal: document.getElementById("ambientVolumeVal"),
    collapseBtn: document.getElementById("ambientCollapseBtn"),
    loopBtn: document.getElementById("ambientLoopBtn"),
    fullscreenBtn: document.getElementById("ambientFullscreenBtn"),
    body: document.getElementById("ambientBody"),
    ribbon: document.getElementById("ambientRibbon"),
    voiceGuide: document.getElementById("ambientVoiceGuide"),
    spGuide: document.getElementById("ambientSpGuide"),
    surpriseBtn: document.getElementById("ambientSurpriseBtn"),
    favBtn: document.getElementById("ambientFavBtn"),
    favorites: document.getElementById("ambientFavorites"),
    favHead: ROOT.querySelector(".ambient-fav-head"),
  };

  let synth = null;
  let reverb = null;
  let filter = null;
  let outputGain = null;
  let fallbackFullscreen = false;
  let loopTimer = null;
  let loopBusy = false;

  function init() {
    hydrateVolume();
    hydrateSettings();
    populateControls();
    bindEvents();
    regenerate(true);
    renderFavorites();
    syncCollapseUi();
    syncLoopUi();
  }

  function populateControls() {
    refs.preset.innerHTML = Object.entries(PRESETS)
      .map(([k, v]) => `<option value="${k}">${v.name}</option>`)
      .join("");
    refs.key.innerHTML = NOTE_NAMES.map((n) => `<option value="${n}">${n}</option>`).join("");
    refs.preset.value = state.preset;
    refs.key.value = state.key;
    refs.bpm.value = String(state.bpm);
    refs.bpmVal.textContent = String(state.bpm);
    refs.density.value = String(state.density);
    refs.densityVal.textContent = String(state.density);
    if (refs.semitoneMode) refs.semitoneMode.value = state.semitoneMode;
    if (refs.holdBeats) refs.holdBeats.value = String(state.holdBeats);
    refs.volume.value = String(Math.round(state.volume * 100));
    refs.volumeVal.textContent = String(Math.round(state.volume * 100));
  }

  function bindEvents() {
    refs.preset.addEventListener("change", () => {
      state.preset = refs.preset.value;
      persistSettings();
      regenerate(false);
    });
    refs.key.addEventListener("change", () => {
      state.key = refs.key.value;
      persistSettings();
      regenerate(false);
    });
    refs.bpm.addEventListener("input", () => {
      state.bpm = Number(refs.bpm.value || 64);
      refs.bpmVal.textContent = String(state.bpm);
      persistSettings();
      restartLoopIfActive(false);
    });
    refs.density.addEventListener("input", () => {
      state.density = Number(refs.density.value || 2);
      refs.densityVal.textContent = String(state.density);
      persistSettings();
      regenerate(false);
    });
    if (refs.semitoneMode) {
      refs.semitoneMode.addEventListener("change", () => {
        state.semitoneMode = refs.semitoneMode.value || "prefer";
        persistSettings();
        regenerate(false);
      });
    }
    if (refs.holdBeats) {
      refs.holdBeats.addEventListener("change", () => {
        const parsed = Number(refs.holdBeats.value || 8);
        state.holdBeats = parsed >= 16 ? 16 : (parsed >= 8 ? 8 : 4);
        persistSettings();
        restartLoopIfActive(false);
      });
    }
    refs.volume.addEventListener("input", () => {
      const pct = Number(refs.volume.value || 70);
      setVolumeFromPercent(pct, true);
    });
    refs.surpriseBtn.addEventListener("click", () => {
      const keys = Object.keys(PRESETS);
      state.preset = keys[Math.floor(Math.random() * keys.length)];
      state.key = NOTE_NAMES[Math.floor(Math.random() * NOTE_NAMES.length)];
      state.rngSeed = (Math.random() * 99999) | 0;
      refs.preset.value = state.preset;
      refs.key.value = state.key;
      persistSettings();
      regenerate(false);
      restartLoopIfActive(true);
    });
    refs.favBtn.addEventListener("click", saveFavorite);
    if (refs.collapseBtn) {
      refs.collapseBtn.addEventListener("click", async () => {
        // Auto-exit fullscreen before collapsing for better UX
        const isFs = !!document.fullscreenElement || fallbackFullscreen;
        if (isFs && !state.collapsed) {
          await exitFullscreen();
        }
        state.collapsed = !state.collapsed;
        persistSettings();
        syncCollapseUi();
      });
    }
    if (refs.loopBtn) refs.loopBtn.addEventListener("click", toggleLoop);
    if (refs.fullscreenBtn) refs.fullscreenBtn.addEventListener("click", toggleFullscreen);
    document.addEventListener("fullscreenchange", syncFullscreenUi);
    window.addEventListener("beforeunload", () => stopLoop(false));
  }

  async function toggleLoop() {
    if (loopTimer) {
      stopLoop(true);
      return;
    }
    await startLoop(true, true);
  }

  async function startLoop(playNow, announce) {
    if (!state.progression.length) return;
    await ensureSynth();
    if (!state.synthReady) return;

    stopLoop(false);

    if (playNow) {
      await playChord(state.activeIndex);
    }

    const periodMs = loopIntervalMs();
    loopTimer = setInterval(async () => {
      if (loopBusy || !state.progression.length) return;
      loopBusy = true;
      try {
        state.activeIndex = (state.activeIndex + 1) % state.progression.length;
        renderRibbon();
        renderGuides();
        await playChord(state.activeIndex);
      } finally {
        loopBusy = false;
      }
    }, periodMs);

    syncLoopUi();
    if (announce) showToastSafe("循環播放中");
  }

  function stopLoop(announce) {
    if (loopTimer) {
      clearInterval(loopTimer);
      loopTimer = null;
    }
    loopBusy = false;
    syncLoopUi();
    if (announce) showToastSafe("已停止循環播放");
  }

  function restartLoopIfActive(playNow) {
    if (!loopTimer) return;
    startLoop(playNow, false);
  }

  function loopIntervalMs() {
    const safeBpm = clamp(state.bpm || 64, 50, 80);
    const beats = state.holdBeats === 16 ? 16 : (state.holdBeats === 4 ? 4 : 8);
    // Ambient pacing: configurable per chord at 4/8/16 beats.
    return Math.max(900, Math.round((60 / safeBpm) * 1000 * beats));
  }

  function syncLoopUi() {
    if (!refs.loopBtn) return;
    const active = !!loopTimer;
    refs.loopBtn.classList.toggle("is-active", active);
    refs.loopBtn.setAttribute("title", active ? "停止" : "循環播放");
    refs.loopBtn.setAttribute("aria-label", active ? "Stop" : "Loop playback");
  }

  function syncCollapseUi() {
    const collapsed = !!state.collapsed;
    ROOT.classList.toggle("is-collapsed", collapsed);
    if (refs.body) refs.body.setAttribute("aria-hidden", collapsed ? "true" : "false");
    if (!refs.collapseBtn) return;
    refs.collapseBtn.classList.toggle("is-active", collapsed);
    refs.collapseBtn.textContent = collapsed ? "展開" : "收折";
    refs.collapseBtn.setAttribute("aria-expanded", collapsed ? "false" : "true");
  }

  async function toggleFullscreen() {
    const fsTarget = ROOT;
    const isNative = !!document.fullscreenElement;
    if (isNative || fallbackFullscreen) {
      await exitFullscreen();
      return;
    }
    await enterFullscreen(fsTarget);
  }

  async function enterFullscreen(target) {
    try {
      if (target.requestFullscreen) {
        await target.requestFullscreen();
        fallbackFullscreen = false;
      } else if (target.webkitRequestFullscreen) {
        target.webkitRequestFullscreen();
        fallbackFullscreen = false;
      } else {
        fallbackFullscreen = true;
        ROOT.classList.add("is-fullscreen");
      }
    } catch {
      // If browser rejects fullscreen API (gesture policy), fallback to fixed overlay.
      fallbackFullscreen = true;
      ROOT.classList.add("is-fullscreen");
    }
    syncFullscreenUi();
  }

  async function exitFullscreen() {
    try {
      if (document.fullscreenElement && document.exitFullscreen) {
        await document.exitFullscreen();
      } else if (document.webkitFullscreenElement && document.webkitExitFullscreen) {
        document.webkitExitFullscreen();
      }
    } catch {
      // Continue to cleanup fallback state even if API exit fails.
    }
    fallbackFullscreen = false;
    ROOT.classList.remove("is-fullscreen");
    syncFullscreenUi();
  }

  function syncFullscreenUi() {
    const active = !!document.fullscreenElement || fallbackFullscreen || ROOT.classList.contains("is-fullscreen");
    if (active && state.collapsed) {
      state.collapsed = false;
      persistSettings();
      syncCollapseUi();
    }
    document.body.classList.toggle("ambient-fullscreen-lock", active);
    if (!refs.fullscreenBtn) return;
    refs.fullscreenBtn.classList.toggle("is-active", active);
    refs.fullscreenBtn.textContent = active ? "退出全螢幕" : "全螢幕";
  }

  function hydrateVolume() {
    try {
      const raw = localStorage.getItem(VOLUME_KEY);
      if (raw == null) return;
      const pct = Number(raw);
      if (!Number.isFinite(pct)) return;
      state.volume = clamp(pct / 100, 0, 1);
    } catch {
      // Ignore malformed local storage values.
    }
  }

  function hydrateSettings() {
    try {
      const raw = localStorage.getItem(SETTINGS_KEY);
      if (!raw) return;
      const obj = JSON.parse(raw);
      if (!obj || typeof obj !== "object") return;
      if (obj.preset && PRESETS[obj.preset]) state.preset = obj.preset;
      if (obj.key && noteNameToPc(obj.key) >= 0) state.key = pcToName(noteNameToPc(obj.key));
      if (Number.isFinite(Number(obj.bpm))) state.bpm = clamp(Math.round(Number(obj.bpm)), 50, 80);
      if (Number.isFinite(Number(obj.density))) state.density = clamp(Math.round(Number(obj.density)), 1, 3);
      if (obj.semitoneMode && ["off", "prefer", "force"].includes(obj.semitoneMode)) {
        state.semitoneMode = obj.semitoneMode;
      }
      if (Number.isFinite(Number(obj.holdBeats))) {
        const parsed = Number(obj.holdBeats);
        state.holdBeats = parsed >= 16 ? 16 : (parsed >= 8 ? 8 : 4);
      }
      if (typeof obj.collapsed === "boolean") state.collapsed = obj.collapsed;
    } catch {
      // Ignore malformed stored settings.
    }
  }

  function persistSettings() {
    const payload = {
      preset: state.preset,
      key: state.key,
      bpm: state.bpm,
      density: state.density,
      semitoneMode: state.semitoneMode,
      holdBeats: state.holdBeats,
      collapsed: state.collapsed,
    };
    try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(payload)); } catch {}
  }

  function setVolumeFromPercent(pct, persist) {
    const safePct = clamp(Math.round(Number(pct) || 0), 0, 100);
    state.volume = safePct / 100;
    refs.volumeVal.textContent = String(safePct);
    if (outputGain) {
      const now = window.Tone ? window.Tone.now() : 0;
      outputGain.gain.rampTo(state.volume, 0.08, now);
    }
    if (persist) {
      try { localStorage.setItem(VOLUME_KEY, String(safePct)); } catch {}
    }
  }

  function regenerate(isInitial) {
    const candidates = [];
    for (let i = 0; i < 24; i++) {
      candidates.push(generateCandidate(i));
    }
    candidates.sort((a, b) => b.score - a.score);
    const pickIndex = isInitial ? 0 : Math.min(5, Math.floor(Math.random() * 6));
    const winner = candidates[pickIndex] || candidates[0];
    state.progression = winner ? winner.progression : [];
    applyVoiceLeadingDisplay(state.progression);
    state.activeIndex = 0;
    state.rngSeed = (state.rngSeed + 97) % 100000;
    renderRibbon();
    renderGuides();
    restartLoopIfActive(false);
  }

  function applyVoiceLeadingDisplay(prog) {
    if (!Array.isArray(prog) || !prog.length) return;
    const first = prog[0];
    const firstPcs = (first.notes || []).map((n) => noteNameToPc(n)).filter((v) => v >= 0);
    let prevVoicing = seedVoicing(firstPcs, 55);
    attachVoicing(first, prevVoicing);

    for (let i = 1; i < prog.length; i++) {
      const ch = prog[i];
      const pcs = (ch.notes || []).map((n) => noteNameToPc(n)).filter((v) => v >= 0);
      const nextVoicing = voiceLeadFromPrev(prevVoicing, pcs);
      attachVoicing(ch, nextVoicing);
      prevVoicing = nextVoicing;
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
    if (!prevVoicing.length || !targetPcs.length) return seedVoicing(targetPcs, 55);
    const usedPcs = new Set();
    const next = [];
    const freePrev = [];

    // Keep common tones fixed first, preserving exact register.
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

  function attachVoicing(chord, voicedMidis) {
    const names = voicedMidis.map((m) => pcToName(midiToPc(m), !!chord.prefersSharps));
    chord.voicedMidis = voicedMidis.slice();
    chord.voicedNotes = names;
    chord.voicedJianpu = names.map((n) => noteToJianpu(n));
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

  function generateCandidate(iter) {
    const preset = PRESETS[state.preset];
    const keySemi = noteNameToPc(state.key);
    const semitoneMotif = pickSemitoneMotif(iter);
    if (semitoneMotif) {
      const progression = buildMotifProgression(keySemi, semitoneMotif);
      const baseScore = scoreProgression(progression);
      const bonus = state.semitoneMode === "force" ? 260 : 24;
      return {
        progression,
        score: baseScore + bonus,
      };
    }
    const motif = pickMotifIfAny(preset, iter);
    if (motif) {
      return {
        progression: buildMotifProgression(keySemi, motif),
        score: 200,
      };
    }
    const pickTemplate = preset.templates[randomInt(0, preset.templates.length - 1, iter + 31)] || preset.templates[0];
    const count = 2 + randomInt(0, 2, iter + 17);
    const rootPath = buildRootPath(preset, count, iter);
    const progression = [];

    for (let i = 0; i < count; i++) {
      const semi = (keySemi + rootPath[i % rootPath.length]) % 12;
      const quality = adaptQuality(pickTemplate[i % pickTemplate.length]);
      progression.push(buildChord(semi, quality, preset.roles[i % preset.roles.length] || "flow"));
    }

    const score = scoreProgression(progression);
    return { progression, score };
  }

  function pickSemitoneMotif(iter) {
    if (state.semitoneMode === "off") return null;
    const semitonePair = { roots: [0, 11], qualities: ["maj7", "m7"], roles: ["Imaj7", "VIIm7"] };
    if (state.semitoneMode === "force") return semitonePair;
    // prefer mode: bias toward semitone color without making every result identical.
    if (randomInt(0, 99, iter + 71) < 14) return semitonePair;
    return null;
  }

  function pickMotifIfAny(preset, iter) {
    const motifs = preset.motifs || [];
    if (!motifs.length) return null;
    // Keep motif as an occasional color, not the dominant output.
    if (randomInt(0, 99, iter + 43) >= 18) return null;
    return motifs[randomInt(0, motifs.length - 1, iter + 59)];
  }

  function buildMotifProgression(keySemi, motif) {
    const roots = motif.roots || [];
    const qualities = motif.qualities || [];
    const roles = motif.roles || [];
    const progression = [];
    for (let i = 0; i < roots.length; i++) {
      const relative = Number(roots[i]) || 0;
      const semi = (keySemi + relative + 12) % 12;
      const quality = adaptQuality(qualities[i] || "maj7");
      progression.push(buildChord(semi, quality, roles[i] || "motif"));
    }
    return progression;
  }

  function buildRootPath(preset, count, iter) {
    const starts = preset.starts || [0];
    const transitions = preset.transitions || {};
    const path = [];
    let cur = starts[randomInt(0, starts.length - 1, iter + 3)] || 0;
    path.push(cur);
    for (let i = 1; i < count; i++) {
      const nextPool = transitions[cur] || starts;
      cur = nextPool[randomInt(0, nextPool.length - 1, iter + 11 + i)] || starts[0] || 0;
      path.push(cur);
    }
    return path;
  }

  function adaptQuality(q) {
    if (state.density === 1) {
      if (q === "m9" || q === "m11") return "m7";
      if (q === "maj9") return "maj7";
      if (q === "add9" || q === "sus2" || q === "sus4") return "triad";
    }
    if (state.density === 2) {
      if (q === "m11") return "m9";
      if (q === "add9") return "maj7";
    }
    return q;
  }

  function buildChord(rootSemi, quality, role) {
    const intervals = QUALITY_INTERVALS[quality] || QUALITY_INTERVALS.maj7;
    const prefersSharps = chordPrefersSharps(rootSemi);
    const notes = intervals.map((i) => pcToName((rootSemi + i) % 12, prefersSharps));
    const jianpu = notes.map((n) => noteToJianpu(n));
    const name = `${pcToName(rootSemi, prefersSharps)}${quality === "triad" ? "" : quality}`;
    return { rootSemi, quality, notes, jianpu, name, role, prefersSharps };
  }

  function noteToJianpu(noteName) {
    const noteSemi = noteNameToPc(noteName);
    if (noteSemi < 0) return "?";
    const naturals = ["1", "", "2", "", "3", "4", "", "5", "", "6", "", "7"];
    if (noteName.includes("#")) {
      const sharpMap = { 1: "#1", 3: "#2", 6: "#4", 8: "#5", 10: "#6" };
      return sharpMap[noteSemi] || naturals[noteSemi] || "?";
    }
    if (noteName.includes("b")) {
      const flatMap = { 1: "b2", 3: "b3", 6: "b5", 8: "b6", 10: "b7" };
      return flatMap[noteSemi] || naturals[noteSemi] || "?";
    }
    return naturals[noteSemi] || "?";
  }

  function noteNameToPc(name) {
    if (!name || typeof name !== "string") return -1;
    return Object.prototype.hasOwnProperty.call(NOTE_NAME_TO_PC, name) ? NOTE_NAME_TO_PC[name] : -1;
  }

  function pcToName(pc, prefersSharps) {
    // 固定顯示拼法，忽略 prefersSharps（保留參數作向後相容）。
    return NOTE_NAMES[((pc % 12) + 12) % 12];
  }

  function chordPrefersSharps(rootSemi) {
    // Sharp-side keys/chords (G, D, A, E, B, F#, C#) render cleaner with sharps.
    return [7, 2, 9, 4, 11, 6, 1].includes(((rootSemi % 12) + 12) % 12);
  }

  function formatNoteJianpuMap(notes, jianpu) {
    const out = [];
    for (let i = 0; i < notes.length; i++) {
      const n = notes[i] || "?";
      const j = jianpu[i] || "?";
      out.push(`${n}(${j})`);
    }
    return out.join(" · ");
  }

  function scoreProgression(prog) {
    if (!prog.length) return -999;
    let smooth = 0;
    let commonTone = 0;
    let leapPenalty = 0;
    let voicePenalty = 0;
    for (let i = 1; i < prog.length; i++) {
      const prev = prog[i - 1];
      const cur = prog[i];
      const jump = Math.abs(cur.rootSemi - prev.rootSemi);
      const wrappedJump = Math.min(jump, 12 - jump);
      smooth += wrappedJump;
      if (wrappedJump > 5) leapPenalty += 1;
      const overlap = cur.notes.filter((n) => prev.notes.includes(n)).length;
      commonTone += overlap;
      voicePenalty += estimateVoiceDistance(prev, cur);
    }
    const tension = prog.filter((c) => /7|9|11|sus/.test(c.quality)).length;
    const endingPull = Math.min(prog[prog.length - 1].rootSemi, 12 - prog[prog.length - 1].rootSemi);
    return 120 - smooth * 5 - leapPenalty * 8 - voicePenalty * 1.8 + commonTone * 4 + tension * 3 - endingPull * 1.5;
  }

  function estimateVoiceDistance(prev, cur) {
    const maxVoices = Math.min(prev.notes.length, cur.notes.length, 4);
    let total = 0;
    for (let i = 0; i < maxVoices; i++) {
      const a = noteNameToPc(prev.notes[i]);
      const b = noteNameToPc(cur.notes[i]);
      if (a < 0 || b < 0) continue;
      const d = Math.abs(a - b);
      total += Math.min(d, 12 - d);
    }
    return total;
  }

  function renderRibbon() {
    refs.ribbon.innerHTML = state.progression
      .map((ch, idx) => {
        const shownJianpu = ch.voicedJianpu || ch.jianpu || [];
        const jianpuHtml = shownJianpu
          .map((j) => {
            if (j.startsWith("#")) return `<sup>#</sup>${escapeHtml(j.slice(1))}`;
            if (j.startsWith("b")) return `<sup>b</sup>${escapeHtml(j.slice(1))}`;
            return escapeHtml(j);
          })
          .join(" ");
        return `
        <button class="ambient-chord ${idx === state.activeIndex ? "is-active" : ""}" data-idx="${idx}" type="button">
          <div>
            <div class="ambient-chord-name">${escapeHtml(ch.name)}</div>
            <div class="ambient-chord-map">${jianpuHtml}</div>
          </div>
          <div class="ambient-chord-role">${escapeHtml(ch.role)}</div>
        </button>
      `;
      })
      .join("");

    refs.ribbon.querySelectorAll(".ambient-chord").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const idx = Number(btn.dataset.idx || 0);
        state.activeIndex = idx;
        renderRibbon();
        renderGuides();
        await playChord(idx);
        restartLoopIfActive(false);
      });
    });
  }

  function renderGuides() {
    const ch = state.progression[state.activeIndex] || state.progression[0];
    if (!ch) return;
    const next = state.progression[(state.activeIndex + 1) % state.progression.length];
    const leadHint = next ? describeTopMotion(ch, next) : "保持頂音穩定";
    const viewNotes = ch.voicedNotes || ch.notes;
    refs.voiceGuide.textContent = `Voice leading: ${viewNotes.join(" - ")}，${leadHint}。`;
    refs.spGuide.textContent = PRESETS[state.preset].sp404;
  }

  function describeTopMotion(cur, next) {
    const curList = cur.voicedNotes || cur.notes;
    const nextList = next.voicedNotes || next.notes;
    const curTop = curList[curList.length - 1];
    const nextTop = nextList[nextList.length - 1];
    const curIdx = noteNameToPc(curTop);
    const nextIdx = noteNameToPc(nextTop);
    if (curIdx < 0 || nextIdx < 0) return `建議保留最高音 ${curTop}`;
    const raw = nextIdx - curIdx;
    const wrapped = ((raw + 18) % 12) - 6;
    if (wrapped === 0) return `建議保留最高音 ${curTop}`;
    const dir = wrapped > 0 ? "上行" : "下行";
    return `建議最高音由 ${curTop} ${dir} ${Math.abs(wrapped)} 半音到 ${nextTop}`;
  }

  async function ensureSynth() {
    if (state.synthReady) return;
    if (!window.Tone) {
      showToastSafe("Tone.js 載入失敗，請檢查網路連線後重試");
      return;
    }
    try {
      await window.Tone.start();
      outputGain = new window.Tone.Gain(state.volume).toDestination();
      reverb = new window.Tone.Reverb({ decay: 9, wet: 0.34 }).connect(outputGain);
      filter = new window.Tone.Filter({
        type: "lowpass",
        frequency: 1800,
        rolloff: -24,
      });
      synth = new window.Tone.PolySynth(window.Tone.Synth, {
        oscillator: { type: "triangle" },
        envelope: { attack: 2.05, decay: 1.4, sustain: 0.72, release: 3.6 },
      }).connect(filter);
      filter.connect(reverb);
      state.synthReady = true;
    } catch {
      showToastSafe("音訊初始化失敗，請點擊頁面後再試一次");
    }
  }

  async function playChord(index) {
    const ch = state.progression[index];
    if (!ch) return;
    await ensureSynth();
    if (!state.synthReady) return;
    const baseOct = 3;
    const pitches = ch.notes.map((n, i) => `${n}${baseOct + (i > 2 ? 1 : 0)}`);
    const durationSec = chordDurationSec();
    synth.triggerAttackRelease(pitches, durationSec);
  }

  function chordDurationSec() {
    const safeBpm = clamp(state.bpm || 64, 50, 80);
    const beats = state.holdBeats === 16 ? 16 : (state.holdBeats === 4 ? 4 : 8);
    // Keep a tiny tail gap to avoid overlap at loop boundaries.
    return Math.max(1.2, (60 / safeBpm) * beats * 0.94);
  }

  function saveFavorite() {
    const payload = {
      preset: state.preset,
      key: state.key,
      bpm: state.bpm,
      density: state.density,
      semitoneMode: state.semitoneMode,
      holdBeats: state.holdBeats,
      progression: state.progression,
      createdAt: Date.now(),
      id: `am_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
    };
    const list = readFavorites();
    const sig = favoriteSignature(payload);
    const exists = list.find((x) => favoriteSignature(x) === sig);
    let next = list;
    if (exists) {
      next = [
        { ...exists, createdAt: Date.now(), id: exists.id || payload.id },
        ...list.filter((x) => x.id !== exists.id),
      ];
      showToastSafe("此場景已存在，已移到最前面");
    } else {
      next = [payload, ...list];
      if (next.length > FAVORITES_MAX) next = next.slice(0, FAVORITES_MAX);
      showToastSafe("已儲存 Ambient 最愛");
    }
    localStorage.setItem(FAVORITES_KEY, JSON.stringify(next));
    renderFavorites();
  }

  function favoriteSignature(f) {
    const names = ((f && f.progression) || []).map((x) => x && x.name).filter(Boolean).join("|");
    return [f && f.preset, f && f.key, f && f.density, names].join("#");
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
    if (refs.favHead) refs.favHead.textContent = `我的 Ambient 最愛 (${list.length}/${FAVORITES_MAX})`;
    if (!list.length) {
      refs.favorites.innerHTML = `<div class="ambient-fav-empty">尚未儲存，按「儲存最愛」即可建立你的 Ambient 場景。</div>`;
      return;
    }
    refs.favorites.innerHTML = list
      .map((f) => `<span class="ambient-fav-chip" data-id="${f.id}"><button class="ambient-fav-item" type="button" data-action="load" data-id="${f.id}">${escapeHtml(PRESETS[f.preset]?.name || f.preset)} · ${escapeHtml(f.key)} · ${escapeHtml((f.progression || []).map((x) => x.name).join(" / ") || "")}</button><button class="ambient-fav-remove" type="button" data-action="remove" data-id="${f.id}" aria-label="刪除收藏" title="刪除收藏">&times;</button></span>`)
      .join("");

    refs.favorites.querySelectorAll("[data-action='load']").forEach((el) => {
      el.addEventListener("click", () => {
        const target = list.find((x) => x.id === el.dataset.id);
        if (!target) return;
        state.preset = target.preset;
        state.key = target.key;
        state.bpm = target.bpm;
        state.density = target.density;
        if (target.semitoneMode && ["off", "prefer", "force"].includes(target.semitoneMode)) {
          state.semitoneMode = target.semitoneMode;
        }
        if (Number.isFinite(Number(target.holdBeats))) {
          const parsed = Number(target.holdBeats);
          state.holdBeats = parsed >= 16 ? 16 : (parsed >= 8 ? 8 : 4);
        }
        state.progression = target.progression || [];
        applyVoiceLeadingDisplay(state.progression);
        state.activeIndex = 0;
        refs.preset.value = state.preset;
        refs.key.value = state.key;
        refs.bpm.value = String(state.bpm);
        refs.bpmVal.textContent = String(state.bpm);
        refs.density.value = String(state.density);
        refs.densityVal.textContent = String(state.density);
        if (refs.semitoneMode) refs.semitoneMode.value = state.semitoneMode;
        if (refs.holdBeats) refs.holdBeats.value = String(state.holdBeats);
        persistSettings();
        renderRibbon();
        renderGuides();
        restartLoopIfActive(true);
        showToastSafe("已載入 Ambient 最愛");
      });
    });

    refs.favorites.querySelectorAll("[data-action='remove']").forEach((el) => {
      el.addEventListener("click", (evt) => {
        evt.stopPropagation();
        removeFavorite(el.dataset.id || "");
      });
    });
  }

  function removeFavorite(id) {
    if (!id) return;
    const next = readFavorites().filter((f) => f.id !== id);
    localStorage.setItem(FAVORITES_KEY, JSON.stringify(next));
    renderFavorites();
    showToastSafe("已刪除 Ambient 最愛");
  }

  function randomInt(min, max, seedAdd) {
    const x = Math.sin(state.rngSeed + seedAdd * 17.31) * 10000;
    const n = x - Math.floor(x);
    return min + Math.floor(n * (max - min + 1));
  }

  function clamp(v, min, max) {
    return Math.max(min, Math.min(max, v));
  }

  function showToastSafe(msg) {
    if (typeof window.showToast === "function") {
      window.showToast(msg, 1800);
      return;
    }
    const el = document.getElementById("toast");
    if (el) {
      el.textContent = msg;
      el.classList.add("show");
      setTimeout(() => el.classList.remove("show"), 1800);
    }
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  init();
})();
