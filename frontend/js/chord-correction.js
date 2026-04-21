/**
 * chord-correction.js — AI-assisted chord correction tools for the player
 *
 * Three features:
 *   1. Beat Tap Correction (節拍校正) — user taps 1/2/3/4, system snaps chords to beat grid
 *   2. Chord Offset Alignment (和弦對齊) — user presses Space at chord changes, system shifts all chords
 *   3. Chord Split (和弦快切) — split long chords + optional section boundary (called from context menu)
 *
 * Exposes window.ChordCorrection for player.js to consume.
 */
(function () {
  "use strict";

  /* ---------- helpers ---------- */
  function median(arr) {
    if (arr.length === 0) return 0;
    const s = [...arr].sort((a, b) => a - b);
    const mid = Math.floor(s.length / 2);
    return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
  }

  function round2(v) { return Math.round(v * 100) / 100; }

  function showToast(msg, ms) {
    if (typeof window.showToast === "function") { window.showToast(msg, ms); return; }
    const el = document.getElementById("toast");
    if (!el) return;
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(el._tid);
    el._tid = setTimeout(() => el.classList.remove("show"), ms || 2000);
  }

  /* ========== Shared State ========== */
  let _originalChords = null;   // backup before first correction
  let _activeMode = null;       // null | "beat-tap" | "chord-align"

  /* ========== Feature 3: Chord Split ========== */

  function generateSplitOptions(totalBeats) {
    const opts = [];
    // Generate pairs where each side >= 1 beat, in whole beats
    for (let l = 1; l < totalBeats; l++) {
      const r = totalBeats - l;
      if (r >= 1) opts.push([l, r]);
    }
    // Filter: keep only options where both sides >= 2 beats to avoid noise
    const filtered = opts.filter(([l, r]) => l >= 2 && r >= 2);
    return filtered.length > 0 ? filtered : opts;
  }

  function splitChord(chordData, chordIdx, leftBeats, rightBeats, secPerBeat) {
    const chord = chordData.chords[chordIdx];
    const splitTime = round2(chord.time + leftBeats * secPerBeat);
    const origEnd = chord.end || chord.time + (leftBeats + rightBeats) * secPerBeat;

    chord.end = splitTime;
    const newChord = { time: splitTime, end: round2(origEnd), chord: chord.chord };
    chordData.chords.splice(chordIdx + 1, 0, newChord);
    return splitTime; // caller may use for section boundary
  }

  // Set a chord's beat count (duration). Cascades subsequent chords by the
  // delta so the timeline stays coherent — shortening leaves no gap, growing
  // creates no overlap. Caller handles backup/rebuild.
  function setBeats(chordData, chordIdx, newBeats, secPerBeat) {
    const chords = chordData.chords;
    const chord = chords[chordIdx];
    const oldEnd = chord.end || (chordIdx < chords.length - 1 ? chords[chordIdx + 1].time : chord.time + 2);
    const newEnd = round2(chord.time + newBeats * secPerBeat);
    const delta = newEnd - oldEnd;
    chord.end = newEnd;
    if (Math.abs(delta) > 0.001) {
      for (let j = chordIdx + 1; j < chords.length; j++) {
        chords[j].time = round2(chords[j].time + delta);
        if (chords[j].end) chords[j].end = round2(chords[j].end + delta);
      }
    }
    return { delta, newEnd };
  }

  // Merge two adjacent chords. The `dissolve` param names which one vanishes
  // — "prev" means the chord AT chordIdx-1 vanishes and this chord extends
  // back to absorb it; "next" means the chord AT chordIdx+1 vanishes and
  // this chord extends forward. Returns true on success, false at song
  // boundaries. Caller handles backup + rebuild.
  function mergeChord(chordData, chordIdx, dissolve /* "prev" | "next" */) {
    const chords = chordData.chords;
    if (chordIdx < 0 || chordIdx >= chords.length) return false;
    if (dissolve === "prev") {
      if (chordIdx === 0) return false;
      const prev = chords[chordIdx - 1];
      const curr = chords[chordIdx];
      // prev absorbs curr: prev's name stays, prev.end becomes curr.end
      prev.end = curr.end || prev.end;
      chords.splice(chordIdx, 1);
    } else if (dissolve === "next") {
      if (chordIdx >= chords.length - 1) return false;
      const curr = chords[chordIdx];
      const next = chords[chordIdx + 1];
      // curr absorbs next: curr's name stays, curr.end becomes next.end
      curr.end = next.end || curr.end;
      chords.splice(chordIdx + 1, 1);
    } else {
      return false;
    }
    return true;
  }

  /* ========== Feature 2: Chord Align ========== */

  let _alignState = null; // { chordData, audio, getActiveIdxFn, rebuildFn, marks[], panelEl, keyHandler }

  function _createPanel(title, instruction) {
    const panel = document.createElement("div");
    panel.className = "correction-panel";
    panel.innerHTML = `
      <div class="correction-title">${title}</div>
      <div class="correction-instructions">${instruction}</div>
      <div class="correction-status"><span class="cc-count">0</span></div>
      <div class="correction-result" style="display:none"></div>
      <div class="correction-actions">
        <button class="correction-btn correction-apply" disabled>&#x2713; &#x5957;&#x7528;</button>
        <button class="correction-btn correction-cancel">&#x2717; &#x53D6;&#x6D88;</button>
      </div>`;
    document.body.appendChild(panel);
    return panel;
  }

  function enterChordAlign(chordData, audio, getActiveIdxFn, rebuildFn) {
    if (_activeMode) { showToast("請先結束目前的修正模式", 2000); return; }
    if (!chordData || !chordData.chords || chordData.chords.length === 0) {
      showToast("尚無和弦資料", 2000); return;
    }
    _activeMode = "chord-align";

    const marks = [];
    const panel = _createPanel(
      "\u23F1 和弦對齊 (Chord Align)",
      "播放音樂，聽到和弦變換時按 <kbd>Space</kbd>"
    );

    const countEl = panel.querySelector(".cc-count");
    const resultEl = panel.querySelector(".correction-result");
    const applyBtn = panel.querySelector(".correction-apply");
    const cancelBtn = panel.querySelector(".correction-cancel");

    function updateUI() {
      countEl.textContent = `${marks.length} 個標記`;
      if (marks.length >= 3) {
        const offsets = marks.map(m => m.audioTime - m.chordTime);
        const med = median(offsets);
        resultEl.style.display = "";
        const sign = med >= 0 ? "+" : "";
        resultEl.textContent = `偏移量: ${sign}${round2(med)}s`;
        applyBtn.disabled = false;
      }
    }

    function keyHandler(e) {
      if (e.key !== " ") return;
      e.preventDefault();
      e.stopImmediatePropagation();
      if (audio.paused) return;

      const idx = getActiveIdxFn();
      if (idx < 0 || idx >= chordData.chords.length) return;

      const chordTime = chordData.chords[idx].time;
      marks.push({ audioTime: audio.currentTime, chordIdx: idx, chordTime });

      // visual flash
      const dots = document.querySelectorAll(".rv-item");
      if (dots[idx]) {
        dots[idx].style.outline = "2px solid #4caf50";
        setTimeout(() => { if (dots[idx]) dots[idx].style.outline = ""; }, 400);
      }
      updateUI();
    }
    document.addEventListener("keydown", keyHandler, { capture: true });

    applyBtn.onclick = () => {
      if (marks.length < 3) { showToast("至少需要 3 個標記", 2000); return; }
      const offsets = marks.map(m => m.audioTime - m.chordTime);
      const offset = median(offsets);

      if (Math.abs(offset) > 3) {
        if (!confirm(`偏移量 ${round2(offset)}s 很大，確定要套用？`)) return;
      }

      backup(chordData);
      chordData.chords.forEach(c => {
        c.time = round2(Math.max(0, c.time + offset));
        if (c.end != null) c.end = round2(Math.max(0, c.end + offset));
      });
      exitChordAlign();
      rebuildFn();
      showToast(`已套用偏移 ${round2(offset)}s`, 2500);
    };

    cancelBtn.onclick = () => { exitChordAlign(); };

    _alignState = { chordData, audio, getActiveIdxFn, rebuildFn, marks, panelEl: panel, keyHandler };
  }

  function exitChordAlign() {
    _activeMode = null;
    if (!_alignState) return;
    document.removeEventListener("keydown", _alignState.keyHandler, { capture: true });
    if (_alignState.panelEl && _alignState.panelEl.parentNode) {
      _alignState.panelEl.remove();
    }
    _alignState = null;
  }

  /* ========== Feature 1: Beat Tap ========== */

  let _beatState = null; // { chordData, audio, rebuildFn, taps[], panelEl, keyHandler }

  function enterBeatTap(chordData, audio, rebuildFn) {
    if (_activeMode) { showToast("請先結束目前的修正模式", 2000); return; }
    if (!chordData || !chordData.chords || chordData.chords.length === 0) {
      showToast("尚無和弦資料", 2000); return;
    }
    _activeMode = "beat-tap";

    const taps = [];
    const panel = _createPanel(
      "\uD83E\uDD41 節拍校正 (Beat Tap)",
      "播放音樂，按 <kbd>1</kbd> <kbd>2</kbd> <kbd>3</kbd> <kbd>4</kbd> 鍵敲擊每一拍"
    );

    const countEl = panel.querySelector(".cc-count");
    const resultEl = panel.querySelector(".correction-result");
    const applyBtn = panel.querySelector(".correction-apply");
    const cancelBtn = panel.querySelector(".correction-cancel");

    function updateUI() {
      countEl.textContent = `${taps.length} 次敲擊`;
      if (taps.length >= 4) {
        const intervals = [];
        for (let i = 1; i < taps.length; i++) {
          intervals.push(taps[i].time - taps[i - 1].time);
        }
        const med = median(intervals);
        const bpm = Math.round(60 / med);
        resultEl.style.display = "";
        resultEl.textContent = `BPM: ${bpm}`;
        if (taps.length >= 8) applyBtn.disabled = false;
      }
    }

    function keyHandler(e) {
      const beatNum = parseInt(e.key);
      if (isNaN(beatNum) || beatNum < 1 || beatNum > 9) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      if (audio.paused) return;

      taps.push({ time: audio.currentTime, beat: beatNum });

      // flash panel
      countEl.style.color = "#ff9800";
      setTimeout(() => { countEl.style.color = ""; }, 150);
      updateUI();
    }
    document.addEventListener("keydown", keyHandler, { capture: true });

    applyBtn.onclick = () => {
      if (taps.length < 8) { showToast("至少需要 8 次敲擊 (2 小節)", 2000); return; }

      // 1. Calculate BPM
      const intervals = [];
      for (let i = 1; i < taps.length; i++) {
        intervals.push(taps[i].time - taps[i - 1].time);
      }
      const rawMedian = median(intervals);
      // filter outliers (> 2x or < 0.5x median)
      const filtered = intervals.filter(v => v > rawMedian * 0.5 && v < rawMedian * 2);
      const secPerBeat = filtered.length > 0 ? median(filtered) : rawMedian;
      const bpm = Math.round(60 / secPerBeat);

      // 2. Calculate phase (where beat 1 falls)
      // For each tap, phase = tap.time - (tap.beat - 1) * secPerBeat
      const phases = taps.map(t => t.time - (t.beat - 1) * secPerBeat);
      // Normalize phases to [0, secPerBeat * timeSignature)
      const tSig = Math.max(...taps.map(t => t.beat)); // auto-detect time signature
      const measureDur = secPerBeat * tSig;
      const normPhases = phases.map(p => ((p % measureDur) + measureDur) % measureDur);
      const phase = median(normPhases);

      // 3. Build beat grid covering the song
      const songDur = audio.duration || 300;
      const gridStart = phase - Math.ceil(phase / secPerBeat) * secPerBeat;

      // 4. Snap each chord to nearest beat
      backup(chordData);
      const chords = chordData.chords;
      let corrections = 0;

      for (let i = 0; i < chords.length; i++) {
        const t = chords[i].time;
        // nearest beat = round((t - gridStart) / secPerBeat) * secPerBeat + gridStart
        const beatIdx = Math.round((t - gridStart) / secPerBeat);
        const snapped = round2(gridStart + beatIdx * secPerBeat);
        if (Math.abs(snapped - t) > 0.02) corrections++;
        const dur = (chords[i].end || t + 2) - t;
        chords[i].time = round2(Math.max(0, snapped));
        // set end to next chord's time (or preserve duration for last)
        if (i < chords.length - 1) {
          // will be updated in next pass
        } else {
          chords[i].end = round2(chords[i].time + dur);
        }
      }

      // 5. Update end times = next chord's start time
      for (let i = 0; i < chords.length - 1; i++) {
        chords[i].end = chords[i + 1].time;
      }

      // 6. Remove duplicates (same time)
      for (let i = chords.length - 1; i > 0; i--) {
        if (Math.abs(chords[i].time - chords[i - 1].time) < 0.02) {
          chords.splice(i, 1);
        }
      }

      // Sort by time
      chords.sort((a, b) => a.time - b.time);

      exitBeatTap();
      rebuildFn();
      showToast(`BPM ${bpm}，修正了 ${corrections} 個和弦`, 2500);
    };

    cancelBtn.onclick = () => { exitBeatTap(); };

    _beatState = { chordData, audio, rebuildFn, taps, panelEl: panel, keyHandler };
  }

  function exitBeatTap() {
    _activeMode = null;
    if (!_beatState) return;
    document.removeEventListener("keydown", _beatState.keyHandler, { capture: true });
    if (_beatState.panelEl && _beatState.panelEl.parentNode) {
      _beatState.panelEl.remove();
    }
    _beatState = null;
  }

  /* ========== Feature 5: Chord Calibrate (1/2/3/4 tap) ========== */

  let _calibrateState = null;

  function enterChordCalibrate(chordData, audio, rebuildFn, options) {
    options = options || {};
    const startChordIdx = Math.max(0, Math.min(options.startChordIdx || 0, (chordData?.chords?.length || 1) - 1));

    if (_activeMode) { showToast("請先結束目前的修正模式", 2000); return; }
    if (!chordData || !chordData.chords || chordData.chords.length === 0) {
      showToast("尚無和弦資料", 2000); return;
    }
    _activeMode = "chord-calibrate";

    const taps = []; // { time, isChordStart }

    // Load keybinds (persisted). Defaults: `0` = chord start, `.` = next beat
    // — both on the numpad / right-hand home position so one hand can operate
    // without looking.
    const savedBinds = (() => {
      try { return JSON.parse(localStorage.getItem("livechord_calibrate_keys") || "{}"); }
      catch { return {}; }
    })();
    let chordStartKey = savedBinds.chordStartKey || "0";
    let nextBeatKey = savedBinds.nextBeatKey || ".";

    function saveKeybinds() {
      try {
        localStorage.setItem("livechord_calibrate_keys",
          JSON.stringify({ chordStartKey, nextBeatKey }));
      } catch {}
    }

    const title = startChordIdx > 0
      ? `\u{1F3AF} 從第 ${startChordIdx + 1} 個和絃開始校正`
      : `\u{1F3AF} 和弦與節拍校正 (Chord + Beat Calibrate)`;
    const panel = _createPanel(title, "");  // instruction set below

    const countEl = panel.querySelector(".cc-count");
    const resultEl = panel.querySelector(".correction-result");
    const applyBtn = panel.querySelector(".correction-apply");
    const cancelBtn = panel.querySelector(".correction-cancel");
    const instructionEl = panel.querySelector(".correction-instructions");

    function refreshInstruction() {
      // Clickable <kbd> labels — click to rebind. No modal, inline capture.
      instructionEl.innerHTML =
        `播放音樂，按 <kbd class="cc-bind" data-role="start">${chordStartKey}</kbd> = 換和弦；` +
        `<kbd class="cc-bind" data-role="next">${nextBeatKey}</kbd> = 同和弦下一拍` +
        `<br><span style="opacity:0.6; font-size:11px;">(點擊按鍵可自訂；手機請用下方 <b>新和弦</b> / <b>下一拍</b> 按鈕)</span>`;
      instructionEl.querySelectorAll(".cc-bind").forEach(kbd => {
        kbd.style.cssText = "cursor:pointer; padding:2px 8px; background:rgba(33,150,243,0.2); border-radius:3px; min-width:20px; display:inline-block; text-align:center;";
        kbd.addEventListener("click", () => _rebindOne(kbd));
      });
    }

    function _rebindOne(kbdEl) {
      const role = kbdEl.dataset.role; // "start" or "next"
      const oldText = kbdEl.textContent;
      kbdEl.textContent = "按任意鍵...";
      kbdEl.style.background = "rgba(255,152,0,0.4)";
      rebindingActive = true;
      const oneshot = (e) => {
        if (["Shift", "Control", "Alt", "Meta", "Tab"].includes(e.key)) return;
        document.removeEventListener("keydown", oneshot, { capture: true });
        rebindingActive = false;
        if (e.key === "Escape") {
          kbdEl.textContent = oldText;
          kbdEl.style.background = "";
          return;
        }
        e.preventDefault();
        e.stopImmediatePropagation();
        const other = role === "start" ? nextBeatKey : chordStartKey;
        if (e.key === other) {
          showToast("兩個按鍵不能相同", 1800);
          kbdEl.textContent = oldText;
          kbdEl.style.background = "";
          return;
        }
        if (role === "start") chordStartKey = e.key;
        else nextBeatKey = e.key;
        saveKeybinds();
        refreshInstruction();
      };
      document.addEventListener("keydown", oneshot, { capture: true });
    }

    // Inject large touch-friendly tap buttons just before the Apply/Cancel row
    const actionsRow = panel.querySelector(".correction-actions");
    const tapRow = document.createElement("div");
    tapRow.className = "correction-tap-row";
    tapRow.style.cssText = "display:flex; gap:8px; margin-bottom:8px;";
    tapRow.innerHTML = `
      <button type="button" class="correction-btn cc-tap-start" style="flex:1; min-height:44px; font-size:15px; background:rgba(33,150,243,0.3); border-color:#2196F3;">▶ 新和弦</button>
      <button type="button" class="correction-btn cc-tap-beat" style="flex:1; min-height:44px; font-size:15px;">· 下一拍</button>
    `;
    actionsRow.parentNode.insertBefore(tapRow, actionsRow);

    refreshInstruction();

    let rebindingActive = false;

    function recordTap(isChordStart) {
      if (audio.paused) { showToast("請先播放音樂", 1200); return; }
      taps.push({ time: audio.currentTime, isChordStart });
      countEl.style.color = "#2196F3";
      setTimeout(() => { countEl.style.color = ""; }, 150);
      updateUI();
    }
    tapRow.querySelector(".cc-tap-start").addEventListener("click", () => recordTap(true));
    tapRow.querySelector(".cc-tap-beat").addEventListener("click", () => recordTap(false));

    function groupsFromTaps() {
      const groups = [];
      for (const t of taps) {
        if (t.isChordStart || groups.length === 0) groups.push([t]);
        else groups[groups.length - 1].push(t);
      }
      return groups;
    }

    function updateUI() {
      const groups = groupsFromTaps();
      countEl.textContent = `${groups.length} 個和絃，${taps.length} 拍`;
      if (taps.length >= 4) {
        const intervals = [];
        for (let i = 1; i < taps.length; i++) intervals.push(taps[i].time - taps[i - 1].time);
        const med = median(intervals);
        if (med > 0) {
          const bpm = Math.round(60 / med);
          resultEl.style.display = "";
          resultEl.textContent = `BPM: ${bpm}`;
        }
      }
      applyBtn.disabled = groups.length < 2;
    }

    function keyHandler(e) {
      // Rebind capture runs via its own listener; stay out of its way.
      if (rebindingActive) return;
      if (e.key !== chordStartKey && e.key !== nextBeatKey) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      recordTap(e.key === chordStartKey);
    }
    document.addEventListener("keydown", keyHandler, { capture: true });

    applyBtn.onclick = () => {
      const groups = groupsFromTaps();
      if (groups.length < 2) { showToast("至少敲 2 個和絃才能校正", 2000); return; }

      // 1. Derive secPerBeat (filtered median of all inter-tap intervals)
      const intervals = [];
      for (let i = 1; i < taps.length; i++) intervals.push(taps[i].time - taps[i - 1].time);
      const rawMed = median(intervals);
      const filtered = intervals.filter(v => v > rawMed * 0.5 && v < rawMed * 2);
      const secPerBeat = (filtered.length > 0 ? median(filtered) : rawMed) || 0.5;

      // 2. Lag compensation ("由後面校正回去") — use the middle groups to
      // measure the user's consistent reaction delay, then shift every tap
      // earlier by that amount. Skips group 0 which is typically late.
      const residuals = [];
      for (let i = 1; i < groups.length; i++) {
        const expected = groups[i - 1][0].time + groups[i - 1].length * secPerBeat;
        residuals.push(groups[i][0].time - expected);
      }
      const lag = residuals.length > 0 ? median(residuals) : 0;

      // 3. Apply segmented-safe to chordData
      backup(chordData);
      const chords = chordData.chords;
      const offset = startChordIdx;
      const N = Math.min(groups.length, chords.length - offset);

      // Left boundary fix — prior chord's end aligns with first new time
      if (offset > 0) {
        const firstNewTime = Math.max(0, groups[0][0].time - lag);
        chords[offset - 1].end = round2(firstNewTime);
      }

      // Rewrite segment
      for (let g = 0; g < N; g++) {
        const newTime = Math.max(0, groups[g][0].time - lag);
        chords[offset + g].time = round2(newTime);
        if (g > 0) chords[offset + g - 1].end = chords[offset + g].time;
      }

      // Right boundary fix
      const lastIdx = offset + N - 1;
      if (offset + N < chords.length) {
        // Un-recalibrated neighbor exists — line up with its unchanged time
        chords[lastIdx].end = chords[offset + N].time;
      } else {
        // Last chord of the song — derive end from the tap count
        const lastGroup = groups[N - 1];
        chords[lastIdx].end = round2(chords[lastIdx].time + lastGroup.length * secPerBeat);
      }

      // GLOBAL sort + dedupe across the whole chord array. Segment-local
      // sort was a bug: if the user tapped at an audio time that didn't
      // match the right-clicked chord's original position (e.g., audio was
      // still in the chorus when they right-clicked a verse chord), the
      // rewritten segment's times end up *outside* the surrounding chords'
      // ranges, producing a time-regression across the whole array. The
      // admin "過完冬季" file had chord[20].time=183 → chord[21].time=66,
      // exactly this failure mode. A global resort restores the invariant.
      chords.sort((a, b) => (a.time || 0) - (b.time || 0));
      const MIN_GAP = 0.2;
      for (let i = chords.length - 1; i > 0; i--) {
        if ((chords[i].time || 0) - (chords[i - 1].time || 0) < MIN_GAP) {
          chords.splice(i - 1, 1);  // drop the earlier of the pair;
                                    // the later one carries the fresher edit
        }
      }
      // Re-align ends so ribbon doesn't paint gaps
      for (let i = 0; i < chords.length - 1; i++) {
        chords[i].end = chords[i + 1].time;
      }

      // Overwrite the song's saved BPM with the tap-derived tempo. Without
      // this, the player reads the stale chordData.bpm (Phase C) and rounds
      // chord durations against the wrong secPerBeat — so a chord the user
      // tapped as 4 beats would display as 3 dots when the real tempo is
      // faster than the saved BPM. Writing `bpm` here makes the dot count
      // always reflect what the user tapped, regardless of the pre-calibration
      // value.
      const newBpm = Math.max(30, Math.min(300, Math.round(60 / secPerBeat)));
      chordData.bpm = newBpm;

      // Toasts
      let summary;
      if (groups.length > chords.length - offset) {
        summary = `敲了 ${groups.length} 組但只剩 ${chords.length - offset} 個和絃，多的已忽略 (BPM=${newBpm})`;
      } else if (groups.length < chords.length - offset) {
        summary = `已校正 ${N} 個，${chords.length - offset - N} 個未動 (BPM=${newBpm}，右鍵下一個可續)`;
      } else {
        summary = `已校正全部 ${N} 個和絃 (BPM=${newBpm})`;
      }

      exitChordCalibrate();
      rebuildFn();
      showToast(summary, 2800);
    };

    cancelBtn.onclick = () => { exitChordCalibrate(); };

    _calibrateState = { chordData, audio, rebuildFn, taps, panelEl: panel, keyHandler };
    updateUI();
  }

  function exitChordCalibrate() {
    _activeMode = null;
    if (!_calibrateState) return;
    document.removeEventListener("keydown", _calibrateState.keyHandler, { capture: true });
    if (_calibrateState.panelEl && _calibrateState.panelEl.parentNode) {
      _calibrateState.panelEl.remove();
    }
    _calibrateState = null;
  }

  /* ========== Backup / Revert ========== */

  function backup(chordData) {
    if (_originalChords) return; // already backed up
    _originalChords = chordData.chords.map(c => ({ ...c }));
  }

  function revert(chordData, rebuildFn) {
    if (!_originalChords) return;
    chordData.chords = _originalChords.map(c => ({ ...c }));
    _originalChords = null;
    rebuildFn();
  }

  function hasBackup() { return !!_originalChords; }

  /* ========== Public API ========== */

  window.ChordCorrection = {
    // Feature 1: Beat Tap
    enterBeatTap,
    exitBeatTap,
    // Feature 2: Chord Align
    enterChordAlign,
    exitChordAlign,
    // Feature 3: Split
    splitChord,
    generateSplitOptions,
    // Feature 4: Beat adjust
    setBeats,
    // Feature 5: Chord Calibrate (1234 tap)
    enterChordCalibrate,
    exitChordCalibrate,
    // Feature 6: Merge adjacent chords
    mergeChord,
    // Shared
    backup,
    revert,
    hasBackup,
    get activeMode() { return _activeMode; },
  };
})();
