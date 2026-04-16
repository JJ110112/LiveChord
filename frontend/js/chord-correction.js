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
    // Shared
    backup,
    revert,
    hasBackup,
    get activeMode() { return _activeMode; },
  };
})();
