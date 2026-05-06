/**
 * StringInstrument — 弦樂器共用類別
 * 統一 guitar / ukulele / 未來新樂器的 init / prefetch / render / update 邏輯。
 * 差異透過 config 物件注入，不需為每種樂器複製程式碼。
 *
 * Usage:
 *   const guitar = new StringInstrument(GUITAR_CONFIG, bridge);
 *   InstrumentRegistry.register("guitar", guitar);
 */

const NOTE_SEMIS = { C:0,"C#":1,Db:1,D:2,"D#":3,Eb:3,E:4,F:5,"F#":6,Gb:6,G:7,"G#":8,Ab:8,A:9,"A#":10,Bb:10,B:11 };
const SEMI_NAMES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];

function _t(k, v) { return (window.LiveChordI18n && window.LiveChordI18n.t) ? window.LiveChordI18n.t(k, v) : k; }

class StringInstrument {
  /**
   * @param {Object} config — instrument-specific configuration
   *   id, numStrings, openMidi, stringLabels, stringNamesZh,
   *   diagramCacheKey, selectors: { container, fretboardCanvas, waterfallCanvas,
   *   chordName, voicingRow, lhHint, rhHint }
   * @param {Object} bridge — shared player state accessors
   *   $, getDisplayChords, getAudio, getChordCache, getCurrentKey,
   *   getStrumStyle, getArpPattern, getAccData, API, ChordRender
   */
  constructor(config, bridge) {
    this._config = config;
    this._b = bridge;
    this._initialized = false;
    this._voicingsCache = {};
    this._analysisCache = {};
    this._activeIdx = -1;
    this._voicingIdx = 0;
  }

  // ---- Init / Prefetch ----

  init() {
    const chordData = this._b.getChordData();
    if (!chordData || !chordData.chords || chordData.chords.length === 0) {
      const id = this._config.id;
      const self = this;
      setTimeout(() => {
        const activeTab = this._b.getActiveTab();
        if (activeTab === id) self.init();
      }, 1000);
      return;
    }
    this._initialized = true;
    this._activeIdx = -1;
    this._voicingIdx = 0;
    this.prefetchData();
    const chords = this._b.getDisplayChords();
    if (chords && chords.length > 0) {
      this._activeIdx = 0;
      this.renderFretboard(chords[0].chord, 0);
      this._drawRhWaterfall(this._b.getAudio().currentTime || 0);
    }
    this._installResizeObserver();
  }

  // The two canvases live in side-by-side flex panels (.gt-left-panel /
  // .gt-right-panel) inside .chord-display-area. The user-draggable
  // divider sits OUTSIDE this layout — between .chord-ribbon-panel and
  // .chord-display-area — and resizing it causes both inner panels to
  // shrink/expand together. Without an observer the LH fretboard only
  // redraws on chord change, and the RH waterfall only on update() ticks
  // (which don't fire while audio is paused). Both canvases keep their
  // pre-resize pixel buffer and get visually stretched until something
  // else triggers a redraw — typically a full page reload.
  //
  // Observing the canvases directly turned out to be unreliable in some
  // browsers when the canvas's CSS width is a percentage of its parent;
  // observing the parent panel itself catches every flex-driven resize.
  // Redraw is deferred to rAF so callback runs after layout settles, with
  // a coalescing flag so a continuous drag doesn't queue dozens of redraws.
  _installResizeObserver() {
    if (this._resizeObserver) return;
    if (typeof ResizeObserver === "undefined") return;
    const $ = this._b.$;
    const cfg = this._config;
    const fbCanvas = $(cfg.selectors.fretboardCanvas);
    const rhCanvas = $(cfg.selectors.waterfallCanvas);
    const lhPanel = fbCanvas ? fbCanvas.closest(".gt-left-panel") : null;
    const rhPanel = rhCanvas ? rhCanvas.closest(".gt-right-panel") : null;
    if (!lhPanel && !rhPanel && !fbCanvas && !rhCanvas) return;

    // Trailing-edge debounce: every observation cancels the previous timer
    // and schedules a new one. After 80 ms of no further resize events we
    // redraw at the final size. Leading-edge with rAF (the previous attempt)
    // captured the size at transition START, not END — when the user toggled
    // ribbon collapse the panel went display:none and the layout settled
    // over multiple frames; the first observation saw stale dimensions.
    this._resizeTimer = null;
    this._resizeObserver = new ResizeObserver(() => {
      if (this._resizeTimer) clearTimeout(this._resizeTimer);
      this._resizeTimer = setTimeout(() => {
        this._resizeTimer = null;
        if (this._b.getActiveTab() !== this._config.id) return;

        const chords = this._b.getDisplayChords();
        if (chords && chords.length > 0 && this._activeIdx >= 0 && this._activeIdx < chords.length) {
          this.renderFretboard(chords[this._activeIdx].chord, this._voicingIdx);
        }
        const audio = this._b.getAudio();
        this._drawRhWaterfall(audio ? (audio.currentTime || 0) : 0);
      }, 80);
    });
    // Observe both the panels (catches outer flex resize) and the canvases
    // themselves (catches direct CSS changes / ribbon-mode toggles).
    if (lhPanel) this._resizeObserver.observe(lhPanel);
    if (rhPanel) this._resizeObserver.observe(rhPanel);
    if (fbCanvas) this._resizeObserver.observe(fbCanvas);
    if (rhCanvas) this._resizeObserver.observe(rhCanvas);

    // Belt-and-suspenders: also listen for an explicit layout-change event
    // dispatched by player.js _applyRibbonLayout. ResizeObserver alone isn't
    // 100% reliable when the parent flips display:none ↔ "" in the same
    // frame as a flex-driven resize — Chrome occasionally batches the size
    // change in a way that gives us only the start frame.
    this._onPanelResize = () => {
      if (this._resizeTimer) clearTimeout(this._resizeTimer);
      this._resizeTimer = setTimeout(() => {
        this._resizeTimer = null;
        if (this._b.getActiveTab() !== this._config.id) return;
        const chords = this._b.getDisplayChords();
        if (chords && chords.length > 0 && this._activeIdx >= 0 && this._activeIdx < chords.length) {
          this.renderFretboard(chords[this._activeIdx].chord, this._voicingIdx);
        }
        const audio = this._b.getAudio();
        this._drawRhWaterfall(audio ? (audio.currentTime || 0) : 0);
      }, 80);
    };
    document.addEventListener("livechord:panelresize", this._onPanelResize);
  }

  async prefetchData() {
    const chords = this._b.getDisplayChords();
    if (!chords) return;
    const names = [...new Set(chords.map(c => c.chord))];
    const key = this._b.getCurrentKey();
    const API = this._b.API;
    const id = this._config.id;
    await Promise.all(names.map(async (name) => {
      try {
        if (!this._voicingsCache[name])
          this._voicingsCache[name] = await API.chordVoicings(id, name);
      } catch {}
      try {
        if (!this._analysisCache[name])
          this._analysisCache[name] = await API.chordAnalysis(key, name);
      } catch {}
    }));
    this.update(this._b.getAudio().currentTime || 0);
  }

  // ---- Fretboard Rendering ----

  renderFretboard(chordName, voicingIdx) {
    const $ = this._b.$;
    const cfg = this._config;
    const canvas = $(cfg.selectors.fretboardCanvas);
    const nameEl = $(cfg.selectors.chordName);
    const voicingRow = $(cfg.selectors.voicingRow);
    if (!canvas) return;

    const voicingsData = this._voicingsCache[chordName];
    const voicings = voicingsData ? voicingsData.voicings : [];
    const chordCache = this._b.getChordCache();
    const diagram = voicings[voicingIdx] || (chordCache[chordName] || {})[cfg.diagramCacheKey];

    if (nameEl) nameEl.textContent = chordName;
    if (!diagram) return;

    // Get next chord diagram for ghost overlay
    let nextDiag = null;
    const chords = this._b.getDisplayChords();
    if (chords && this._activeIdx >= 0 && this._activeIdx < chords.length - 1) {
      const nextName = chords[this._activeIdx + 1].chord;
      const nv = this._voicingsCache[nextName];
      const nd = (nv ? nv.voicings[0] : null) || (chordCache[nextName] || {})[cfg.diagramCacheKey];
      if (nd) nextDiag = { ...nd, numStrings: cfg.numStrings, name: nextName };
    }

    const drawData = cfg.numStrings !== 6
      ? { ...diagram, numStrings: cfg.numStrings, _stringLabels: cfg.stringLabels }
      : diagram;

    this._b.ChordRender.drawVerticalFretboard(canvas, drawData, {
      canvasW: canvas.clientWidth,
      canvasH: canvas.clientHeight,
      nextData: nextDiag,
    });

    // Voicing pills
    if (voicingRow) {
      voicingRow.innerHTML = '';
      if (voicings.length > 1) {
        const self = this;
        voicings.forEach((v, idx) => {
          const btn = document.createElement("button");
          btn.className = "gt-voicing-btn" + (idx === voicingIdx ? " active" : "");
          btn.textContent = String.fromCodePoint(0x2460 + idx);
          btn.title = v.label || _t("instrument.position_label", { n: idx + 1 });
          btn.addEventListener("click", () => {
            self._voicingIdx = idx;
            self.renderFretboard(chordName, idx);
          });
          voicingRow.appendChild(btn);
        });
      }
    }
  }

  renderFretboardAnimated(chordName, voicingIdx, countdown) {
    const $ = this._b.$;
    const cfg = this._config;
    const canvas = $(cfg.selectors.fretboardCanvas);
    if (!canvas) return;

    const voicingsData = this._voicingsCache[chordName];
    const voicings = voicingsData ? voicingsData.voicings : [];
    const chordCache = this._b.getChordCache();
    const diagram = voicings[voicingIdx] || (chordCache[chordName] || {})[cfg.diagramCacheKey];
    if (!diagram) return;

    let nextDiag = null;
    const chords = this._b.getDisplayChords();
    if (chords && this._activeIdx >= 0 && this._activeIdx < chords.length - 1) {
      const nextName = chords[this._activeIdx + 1].chord;
      const nv = this._voicingsCache[nextName];
      const nd = (nv ? nv.voicings[0] : null) || (chordCache[nextName] || {})[cfg.diagramCacheKey];
      if (nd) nextDiag = { ...nd, numStrings: cfg.numStrings, name: nextName };
    }

    const blinkFreq = countdown < 0.5 ? 12 : countdown < 1.0 ? 8 : 4;
    const blinkAlpha = 0.3 + 0.5 * (0.5 + 0.5 * Math.sin(performance.now() / 1000 * blinkFreq * Math.PI * 2));

    const drawData = cfg.numStrings !== 6
      ? { ...diagram, numStrings: cfg.numStrings, _stringLabels: cfg.stringLabels }
      : diagram;

    this._b.ChordRender.drawVerticalFretboard(canvas, drawData, {
      canvasW: canvas.clientWidth,
      canvasH: canvas.clientHeight,
      nextData: nextDiag,
      nextAlpha: blinkAlpha,
    });
  }

  // ---- Tick Update (called every frame) ----

  update(currentTime) {
    const activeTab = this._b.getActiveTab();
    if (activeTab !== this._config.id || !this._initialized) return;

    // Draw right-hand waterfall
    this._drawRhWaterfall(currentTime);

    const chords = this._b.getDisplayChords();
    if (!chords) return;

    let activeIdx = -1;
    for (let i = chords.length - 1; i >= 0; i--) {
      if (currentTime >= chords[i].time) { activeIdx = i; break; }
    }

    // Blink ghost near chord change
    const nextChordTime = (activeIdx >= 0 && activeIdx < chords.length - 1) ? chords[activeIdx + 1].time : null;
    const countdown = nextChordTime != null ? nextChordTime - currentTime : 99;
    if (countdown < 2.0 && activeIdx >= 0) {
      this.renderFretboardAnimated(chords[activeIdx].chord, this._voicingIdx, countdown);
    }

    if (activeIdx === this._activeIdx) return;
    this._activeIdx = activeIdx;
    this._voicingIdx = 0;

    if (activeIdx < 0 || activeIdx >= chords.length) return;
    const chordName = chords[activeIdx].chord;
    this.renderFretboard(chordName, 0);

    // Update hint panels
    this._updateHints(chordName, activeIdx, chords);
  }

  // Re-run the hint panels (LH chord transition, RH idiom label) without
  // waiting for the next activeIdx change. Called from player.js after
  // _loadAccompaniment lands so a style flip on a paused song refreshes
  // the "RH …" label immediately instead of staying stuck on the
  // previous idiom until playback advances to the next chord.
  refreshLabels() {
    const chords = this._b.getDisplayChords();
    if (!chords || chords.length === 0) return;
    const idx = (this._activeIdx >= 0 && this._activeIdx < chords.length) ? this._activeIdx : 0;
    this._updateHints(chords[idx].chord, idx, chords);
  }

  _updateHints(chordName, activeIdx, chords) {
    const $ = this._b.$;
    const cfg = this._config;
    const lhInfo = $(cfg.selectors.lhHint);
    const rhInfo = $(cfg.selectors.rhHint);

    if (lhInfo) {
      const nextName = activeIdx < chords.length - 1 ? chords[activeIdx + 1].chord : null;
      if (nextName) {
        const chordCache = this._b.getChordCache();
        const curV = this._voicingsCache[chordName];
        const nextV = this._voicingsCache[nextName];
        const curDiag = (curV ? curV.voicings[this._voicingIdx] : null) || (chordCache[chordName] || {})[cfg.diagramCacheKey];
        const nextDiag = (nextV ? nextV.voicings[0] : null) || (chordCache[nextName] || {})[cfg.diagramCacheKey];
        let jumpLabel = "";
        if (curDiag && nextDiag && curDiag.strings && nextDiag.strings) {
          const curMin = Math.min(...curDiag.strings.filter(f => f > 0), 99);
          const nxtMin = Math.min(...nextDiag.strings.filter(f => f > 0), 99);
          const dist = Math.abs(nxtMin - curMin);
          if (dist >= 2) jumpLabel = " " + (nxtMin > curMin
            ? _t("instrument.fret_jump_down", { n: dist })
            : _t("instrument.fret_jump_up", { n: dist }));
        }
        lhInfo.textContent = _t("instrument.lh.next",
          { chord: chordName, next: nextName, jump: jumpLabel });
      } else {
        lhInfo.textContent = _t("instrument.lh.current", { chord: chordName });
      }
    }

    if (rhInfo) {
      // v6: read the idiom from the backend events (accData.right_hand) —
      // strum_id ⇒ strum sweep, finger=p/i/m/a ⇒ pluck arpeggio. The legacy
      // getStrumStyle() picker is now decorative (the AI Acc style governs
      // the idiom server-side), so trusting it here makes the label drift
      // from what's actually playing — e.g. AI Acc style "Block" → backend
      // rh_mode "1+3_once" → arpeggio events on the wire, but the picker
      // localStorage still says "block" → label "RH downstroke" while the
      // user sees pima circles. Fall back to the picker only when accData
      // hasn't loaded yet.
      const idiom = this._inferAccIdiom(this._b.getAccData());
      if (idiom === "arpeggio") {
        rhInfo.textContent = _t("instrument.rh.arpeggio");
      } else if (idiom === "offbeat") {
        rhInfo.textContent = _t("instrument.rh.offbeat");
      } else if (idiom === "strum") {
        rhInfo.textContent = _t("instrument.rh.strum_pattern");
      } else {
        const strumStyle = this._b.getStrumStyle();
        if (strumStyle === "arpeggio") {
          const pat = ARPEGGIO_PATTERNS[this._b.getArpPattern()];
          rhInfo.textContent = pat
            ? _t("instrument.rh.with_pattern", { name: pat.name })
            : _t("instrument.rh.arpeggio");
        } else {
          const styleLabels = {
            block:   _t("instrument.rh.strum_block"),
            pattern: _t("instrument.rh.strum_pattern"),
          };
          rhInfo.textContent = styleLabels[strumStyle] || _t("instrument.rh.fallback");
        }
      }
    }
  }

  // Inspect a slice of backend events to decide whether the active layer is
  // a strum sweep or a pluck arpeggio. Strum events have strum_id; pluck
  // events have a single-letter finger (p/i/m/a). Returns null when accData
  // is empty or carries non-string events (e.g. piano accData on a string
  // tab during a tab-flip refetch).
  _inferAccIdiom(accData) {
    if (!accData || !Array.isArray(accData.right_hand)) return null;
    const sample = accData.right_hand;
    const limit = Math.min(sample.length, 100);
    // Group strum events by strum_id so a 5-string sweep counts as ONE
    // direction, not 5. Then "all up" strums ⇒ offbeat skank, mixed ⇒
    // full D-DU-UDU strum, neither ⇒ pluck arpeggio if any p/i/m/a.
    const strumDirs = new Set();   // unique sids per direction
    const upSids = new Set();
    const downSids = new Set();
    let hasPluck = false;
    for (let i = 0; i < limit; i++) {
      const e = sample[i];
      if (!e) continue;
      if (e.strum_id) {
        if (e.strum_dir === "up") upSids.add(e.strum_id);
        else downSids.add(e.strum_id);
        strumDirs.add(e.strum_id);
      } else if (typeof e.finger === "string" && /^[pima]$/i.test(e.finger)) {
        hasPluck = true;
      }
    }
    if (strumDirs.size > 0) {
      if (downSids.size === 0 && upSids.size > 0) return "offbeat";
      return "strum";
    }
    if (hasPluck) return "arpeggio";
    return null;
  }

  // ---- Right-Hand Event Extractor (v6: read backend accData.right_hand) ----
  //
  // Pre-v6 this class generated arpeggio/strum events client-side, completely
  // independent of the audio synth's accData.right_hand. The two streams
  // drifted on offbeat chord starts (engine v5 piano tiled by beat-period
  // from chord.time, while the local generator used cycleDur-from-time —
  // they coincided only when chords landed on bar boundaries). LiveChord-vxa.
  //
  // Now the backend emits per-string pluck/strum events with both `pitch`
  // (synth consumes unchanged) and `string`/`strum_id`/`strum_dir`/`finger`
  // (visual consumes here). Visual + audio share one source of truth.
  //
  // Returns the same shape the renderer below already expects:
  //   { time, dur, type: "strum"|"pick"|"pluck", dir?, strings?, string?,
  //     finger?, fingers?, chordIdx }
  _extractStringRhEvents(accData, currentTime, lookAhead) {
    // v6 follow-up: honor rhContentMode. When the user picks "R melody"
    // (rhContentMode === "mel"), the audio synth path schedules melody
    // pitches only — drawing AI strum/pluck bars in that mode would
    // visually contradict what's playing (user hears single melody notes,
    // sees 刷弦). Map each melody note to a (string, fret) on the current
    // tuning and render as a pick on that string so visual = audio. For
    // "both" mode, acc events draw normally (with p/i/m/a finger labels)
    // PLUS melody on top (no finger label, so the two layers stay
    // visually distinguishable).
    let _rhMode = "acc";
    try {
      const v = window.localStorage && window.localStorage.getItem("livechord_rh_mode");
      if (v === "mel" || v === "both") _rhMode = v;
    } catch (_) { /* localStorage blocked — keep default */ }

    const winLo = currentTime - 0.5;
    const winHi = currentTime + lookAhead + 0.5;
    const out = [];

    // ── Acc layer (strum / pluck from accData.right_hand) ──
    if (_rhMode !== "mel" && accData && Array.isArray(accData.right_hand)) {
      const accOut = this._extractAccStringEvents(accData.right_hand, winLo, winHi);
      out.push(...accOut);
    }

    // ── Melody layer (mapped to strings) ──
    if (_rhMode === "mel" || _rhMode === "both") {
      const melodyData = (this._b.getMelodyData && this._b.getMelodyData()) || null;
      if (Array.isArray(melodyData)) {
        for (const m of melodyData) {
          if (m == null || typeof m.start !== "number") continue;
          if (m.start > winHi) break;
          const noteEnd = (typeof m.end === "number") ? m.end : (m.start + 0.2);
          if (noteEnd < winLo) continue;
          const sf = this._pitchToString(m.midi);
          if (!sf) continue;
          out.push({
            time: m.start,
            dur: noteEnd - m.start,
            type: "pick",
            string: sf.string,
            // Leave finger=null so the renderer falls back to the generic
            // PICK_CLR. The acc events have p/i/m/a labels — visually the
            // user can see at a glance which layer each bar comes from.
            finger: null,
            chordIdx: 0,
          });
        }
      }
    }

    out.sort((a, b) => a.time - b.time);
    return out;
  }

  // Pick the lowest-fret playable string for a melody pitch on the current
  // tuning. Lowest fret tends to put melody on the higher strings (since
  // lower-pitch open strings need more frets for the same target pitch),
  // which matches how a player would actually pick a melodic line. Returns
  // null if the pitch is below all open strings or beyond fret 15.
  _pitchToString(pitch) {
    const openMidi = this._config.openMidi;
    if (!openMidi || typeof pitch !== "number") return null;
    let bestS = -1;
    let bestFret = 99;
    for (let s = 0; s < openMidi.length; s++) {
      const fret = pitch - openMidi[s];
      if (fret >= 0 && fret <= 15 && fret < bestFret) {
        bestFret = fret;
        bestS = s;
      }
    }
    return (bestS >= 0) ? { string: bestS, fret: bestFret } : null;
  }

  _extractAccStringEvents(rightHand, winLo, winHi) {
    return this._groupBackendStringEvents(rightHand, winLo, winHi);
  }

  // Group backend right-hand events by strum_id (sweeps) / same time
  // (multi-finger plucks) / solo (single-string pick).
  _groupBackendStringEvents(rightHand, winLo, winHi) {
    // Filter to render window first to avoid grouping the entire song.
    const win = [];
    for (const e of rightHand) {
      if (e == null || typeof e.time !== "number") continue;
      if (e.string == null) continue;       // skip non-string events defensively
      if (e.time > winHi) break;            // backend events are time-sorted
      const eEnd = e.time + (e.duration || 0);
      if (eEnd < winLo) continue;
      win.push(e);
    }

    // Group: events sharing strum_id collapse into ONE strum sweep.
    // Multi-finger plucks (multiple events at the same time, no strum_id)
    // collapse into ONE pluck. Solo events render as pick.
    const groups = new Map();
    for (const e of win) {
      const key = e.strum_id
        ? `s|${e.strum_id}`
        : `t|${e.time.toFixed(4)}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(e);
    }

    const out = [];
    for (const evs of groups.values()) {
      if (evs[0].strum_id) {
        const t0 = Math.min(...evs.map(e => e.time));
        const dur = Math.max(...evs.map(e => e.duration || 0)) || 0.1;
        const strings = evs.map(e => e.string);
        out.push({
          time: t0,
          dur,
          type: "strum",
          dir: evs[0].strum_dir || "down",
          strings,
          chordIdx: 0,
        });
      } else if (evs.length > 1) {
        const e0 = evs[0];
        out.push({
          time: e0.time,
          dur: e0.duration || 0.1,
          type: "pluck",
          strings: evs.map(e => e.string),
          fingers: evs.map(e => e.finger || "i"),
          chordIdx: 0,
        });
      } else {
        const e0 = evs[0];
        out.push({
          time: e0.time,
          dur: e0.duration || 0.1,
          type: "pick",
          string: e0.string,
          finger: e0.finger || null,
          chordIdx: 0,
        });
      }
    }
    // Caller does the final sort across the merged acc + melody layers.
    return out;
  }

  // ---- Right-Hand Waterfall Renderer ----

  _drawRhWaterfall(currentTime) {
    const $ = this._b.$;
    const cfg = this._config;
    const canvas = $(cfg.selectors.waterfallCanvas);
    if (!canvas) return;
    const chords = this._b.getDisplayChords();
    if (!chords || chords.length === 0) return;

    const numStrings = cfg.numStrings;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (w === 0 || h === 0) return;

    if (Math.abs(canvas.width / dpr - w) > 2 || Math.abs(canvas.height / dpr - h) > 2) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const accData = this._b.getAccData();
    const lookAhead = 4.0;
    // v6: read string-family events from backend instead of generating
    // locally. Cold-start window (accData not yet loaded, or loaded for the
    // wrong instrument) → render an empty waterfall; _loadAccompaniment
    // runs on tab switch so the strip fills in within ~1 RTT.
    const rhEvents = (accData && accData.instrument === this._config.id)
      ? this._extractStringRhEvents(accData, currentTime, lookAhead)
      : [];
    const pxPerSec = h / lookAhead;
    const padL = Math.round(w * 0.1);
    // Match drawVerticalFretboard's right-pad floor — finger-circle radius
    // can reach ~22px, and w*0.05 leaves the high-e string's circle clipped
    // for any RH-panel narrower than ~440px (split-screen reality).
    const padR = Math.max(Math.round(w * 0.05), 22);
    const stringSpacing = (w - padL - padR) / Math.max(numStrings - 1, 1);
    function strX(s) { return padL + s * stringSpacing; }

    // String lines (full height) — theme-aware via document data-theme so
    // light themes render visible black-on-cream instead of invisible white.
    const _rhIsLight = (function() {
      try {
        const t = document.documentElement.getAttribute("data-theme");
        return t === "light" || t === "sakura" || t === "sunny" || t === "sky";
      } catch (_) { return false; }
    })();
    const _rhInk = (a) => _rhIsLight
      ? `rgba(0,0,0,${Math.min(1, a * 1.4).toFixed(3)})`
      : `rgba(255,255,255,${a.toFixed(3)})`;
    for (let s = 0; s < numStrings; s++) {
      const x = strX(s);
      ctx.strokeStyle = _rhInk(0.40);
      ctx.lineWidth = s === 0 ? 1.5 : 1;
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    }

    // Beat grid — chord-based
    const _gridChords = this._b.getDisplayChords();
    if (_gridChords && _gridChords.length > 0) {
      for (let ci = 0; ci < _gridChords.length; ci++) {
        const gc = _gridChords[ci];
        const gcEnd = (ci + 1 < _gridChords.length) ? _gridChords[ci + 1].time : gc.time + 4;
        const gcDur = gcEnd - gc.time;
        for (let b = 0; b < 4; b++) {
          const bt = gc.time + (b / 4) * gcDur;
          if (bt < currentTime - 0.1 || bt > currentTime + lookAhead) continue;
          const y = h - (bt - currentTime) * pxPerSec;
          if (y < 0 || y > h) continue;
          const isBar = (b === 0);
          ctx.strokeStyle = isBar ? _rhInk(0.30) : _rhInk(0.10);
          ctx.lineWidth = isBar ? 1 : 0.5;
          ctx.beginPath(); ctx.moveTo(padL - 8, y); ctx.lineTo(w - padR + 8, y); ctx.stroke();
        }
      }
    }

    // Draw RH events
    const STRUM_CLR = "rgb(0,151,167)";
    const PICK_CLR  = "rgb(0,172,193)";
    const STRUM_UP_CLR = "rgb(38,166,154)";
    // RGB tuples mirror the CSS strings above so spark particles
    // can take the same color as the event that fired them.
    const STRUM_RGB = [0, 151, 167];
    const PICK_RGB  = [0, 172, 193];
    const STRUM_UP_RGB = [38, 166, 154];
    function _hex2rgb(hex) {
      const h = (hex || "").replace("#", "");
      if (h.length !== 6) return PICK_RGB;
      return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
    }
    // Re-arm set for particle spawn idempotency (same time+event = single burst).
    // Bound size so a long song can't grow it unbounded.
    if (!this._sparkedKeys) this._sparkedKeys = new Set();
    if (this._sparkedKeys.size > 2000) this._sparkedKeys.clear();
    const spawn = this._b.spawnWaterfallParticles;
    const sparkVelP = 0.6; // strings have no velocity data — mid-range burst

    for (const ev of rhEvents) {
      const yBot = h - (ev.time - currentTime) * pxPerSec;
      const yTop = yBot - ev.dur * pxPerSec;
      if (yTop > h || yBot < 0) continue;
      const cT = Math.max(0, yTop);
      const cB = Math.min(h, yBot);

      if (ev.type === "strum") {
        const xs = ev.strings.map(s => strX(s));
        const minX = Math.min(...xs) - stringSpacing * 0.3;
        const maxX = Math.max(...xs) + stringSpacing * 0.3;
        const clr = ev.dir === "up" ? STRUM_UP_CLR : STRUM_CLR;
        ctx.fillStyle = clr;
        const r = 4;
        ctx.beginPath();
        ctx.roundRect(minX, cT, maxX - minX, cB - cT, r);
        ctx.fill();

        const isActive = (cB >= h - 30 && cT <= h - 10);
        const arrowSize = isActive ? 20 : 14;
        const arrowY = (cT + cB) / 2;
        ctx.save();
        if (isActive) { ctx.shadowColor = "#fff"; ctx.shadowBlur = 12; }
        ctx.fillStyle = isActive ? "#fff" : "rgba(255,255,255,0.7)";
        ctx.font = `bold ${arrowSize}px sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(ev.dir === "down" ? "▶" : "◀", (minX + maxX) / 2, arrowY);
        ctx.restore();

        // Spawn a spark burst at each string when the strum crosses the bottom.
        if (cB >= h - 4 && cT <= h && spawn) {
          const key = `s|${ev.time.toFixed(3)}|${ev.dir}`;
          if (!this._sparkedKeys.has(key)) {
            this._sparkedKeys.add(key);
            const rgb = ev.dir === "up" ? STRUM_UP_RGB : STRUM_RGB;
            const span = (maxX - minX) / Math.max(xs.length - 1, 1);
            for (const sx of xs) spawn(sx, h - 2, Math.max(span, 12), rgb[0], rgb[1], rgb[2], sparkVelP);
          }
        }
      } else if (ev.type === "pick") {
        const x = strX(ev.string);
        const cy = (cT + cB) / 2;
        const r = Math.min(stringSpacing * 0.4, 18);
        const isActive = (cB >= h - 30 && cT <= h - 10);
        const fClr = (ev.finger && FINGER_COLORS[ev.finger]) || PICK_CLR;
        ctx.save();
        if (isActive) { ctx.shadowColor = fClr; ctx.shadowBlur = 14; }
        ctx.fillStyle = fClr;
        ctx.beginPath();
        ctx.arc(x, cy, r, 0, Math.PI * 2);
        ctx.fill();
        if (ev.finger) {
          ctx.fillStyle = ev.finger === "a" ? "#333" : "#fff";
          ctx.font = `bold ${Math.round(r * 1.2)}px sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(ev.finger, x, cy);
        }
        ctx.restore();
        // Contact glow
        if (cB >= h - 4 && cT <= h) {
          ctx.save();
          ctx.fillStyle = fClr;
          ctx.shadowColor = fClr;
          ctx.shadowBlur = 18;
          ctx.beginPath();
          ctx.arc(x, h, r * 0.7, 0, Math.PI * 2);
          ctx.fill();
          ctx.fillStyle = "rgba(255,255,255,0.7)";
          ctx.shadowColor = "#fff";
          ctx.shadowBlur = 12;
          ctx.beginPath();
          ctx.arc(x, h, r * 0.35, 0, Math.PI * 2);
          ctx.fill();
          ctx.restore();

          if (spawn) {
            const key = `p|${ev.time.toFixed(3)}|${ev.string}`;
            if (!this._sparkedKeys.has(key)) {
              this._sparkedKeys.add(key);
              const rgb = ev.finger ? _hex2rgb(FINGER_COLORS[ev.finger]) : PICK_RGB;
              spawn(x, h - 2, r * 2, rgb[0], rgb[1], rgb[2], sparkVelP);
            }
          }
        }
      } else if (ev.type === "pluck") {
        const cy = (cT + cB) / 2;
        const r = Math.min(stringSpacing * 0.4, 18);
        const isActive = (cB >= h - 30 && cT <= h - 10);
        for (let si = 0; si < ev.strings.length; si++) {
          const x = strX(ev.strings[si]);
          const fg = ev.fingers[si] || "i";
          const fClr = FINGER_COLORS[fg] || PICK_CLR;
          ctx.save();
          if (isActive) { ctx.shadowColor = fClr; ctx.shadowBlur = 14; }
          ctx.fillStyle = fClr;
          ctx.beginPath();
          ctx.arc(x, cy, r, 0, Math.PI * 2);
          ctx.fill();
          ctx.fillStyle = fg === "a" ? "#333" : "#fff";
          ctx.font = `bold ${Math.round(r * 1.2)}px sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(fg, x, cy);
          ctx.restore();
          // Contact glow
          if (cB >= h - 4 && cT <= h) {
            ctx.save();
            ctx.fillStyle = fClr;
            ctx.shadowColor = fClr;
            ctx.shadowBlur = 18;
            ctx.beginPath();
            ctx.arc(x, h, r * 0.7, 0, Math.PI * 2);
            ctx.fill();
            ctx.fillStyle = "rgba(255,255,255,0.7)";
            ctx.shadowColor = "#fff";
            ctx.shadowBlur = 12;
            ctx.beginPath();
            ctx.arc(x, h, r * 0.35, 0, Math.PI * 2);
            ctx.fill();
            ctx.restore();

            if (spawn) {
              const key = `pl|${ev.time.toFixed(3)}|${ev.strings[si]}`;
              if (!this._sparkedKeys.has(key)) {
                this._sparkedKeys.add(key);
                const rgb = fg ? _hex2rgb(FINGER_COLORS[fg]) : PICK_RGB;
                spawn(x, h - 2, r * 2, rgb[0], rgb[1], rgb[2], sparkVelP);
              }
            }
          }
        }
      }
    }

    // Firework spark particles (shared via bridge — same effect as piano)
    if (this._b.drawWaterfallParticles) this._b.drawWaterfallParticles(ctx);

    // Now line at bottom
    ctx.strokeStyle = "rgba(0,188,212,0.5)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(padL - 8, h);
    ctx.lineTo(w - padR + 8, h);
    ctx.stroke();
  }
}

window.StringInstrument = StringInstrument;
