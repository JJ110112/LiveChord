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

  /* ---------- Transform helpers (calibrate extrapolation) ---------- */

  // Fit an affine transform newTime = a·origTime + b over the calibrated
  // chord pairs. Residual + slope gates decide if affine is accepted;
  // otherwise we fall back to a median-shift (constant offset). This powers
  // the post-calibrate "apply this timing fix to the rest of the song" step.
  function fitTransform(origTimes, newTimes) {
    const n = Math.min(origTimes.length, newTimes.length);
    // Always compute the deltas for the shift fallback.
    const deltas = [];
    for (let i = 0; i < n; i++) deltas.push(newTimes[i] - origTimes[i]);
    const deltaT = n > 0 ? median(deltas) : 0;

    if (n >= 3) {
      let sx = 0, sy = 0, sxx = 0, sxy = 0;
      for (let i = 0; i < n; i++) {
        sx += origTimes[i];
        sy += newTimes[i];
        sxx += origTimes[i] * origTimes[i];
        sxy += origTimes[i] * newTimes[i];
      }
      const denom = n * sxx - sx * sx;
      if (denom > 1e-9) {
        const a = (n * sxy - sx * sy) / denom;
        const b = (sy - a * sx) / n;
        if (Number.isFinite(a) && Number.isFinite(b)) {
          let maxAbsResidual = 0;
          for (let i = 0; i < n; i++) {
            const r = Math.abs(newTimes[i] - (a * origTimes[i] + b));
            if (r > maxAbsResidual) maxAbsResidual = r;
          }
          // Accept affine only when the fit is tight AND the slope is
          // close to 1 — a runaway slope compounds rate errors over the
          // tail of the song and is almost never what the user wants.
          if (maxAbsResidual <= 0.10 && Math.abs(a - 1) <= 0.10) {
            return { mode: "affine", a, b, deltaT, maxResidual: maxAbsResidual };
          }
        }
      }
    }
    return { mode: "shift", a: 1, b: deltaT, deltaT, maxResidual: 0 };
  }

  function applyTransform(t, transform) {
    if (transform.mode === "affine") return transform.a * t + transform.b;
    return t + transform.deltaT;
  }

  // Walk [fromIdx..toIdx] inclusive and rewrite time/end through the
  // transform. Reconcile the seam with chords[fromIdx-1].end so the ribbon
  // doesn't paint a visual gap or overlap at the boundary between the
  // calibrated segment and the extrapolated tail.
  function applyTransformToRange(chordData, fromIdx, toIdx, transform) {
    const chords = chordData.chords;
    if (!chords || fromIdx > toIdx || fromIdx >= chords.length) return 0;
    const last = Math.min(toIdx, chords.length - 1);
    let count = 0;
    for (let j = fromIdx; j <= last; j++) {
      const c = chords[j];
      if (typeof c.time === "number") c.time = round2(applyTransform(c.time, transform));
      if (typeof c.end === "number") c.end = round2(applyTransform(c.end, transform));
      count++;
    }
    if (fromIdx > 0) {
      chords[fromIdx - 1].end = chords[fromIdx].time;
    }
    return count;
  }

  // Step 1 auto-sync: after chord times shift, mutate every accompaniment /
  // melody event whose ORIGINAL time falls inside [rangeStart, rangeEnd) so
  // the piano waterfall stays aligned without a server-side regen. The
  // waterfall/scheduleHand read accData + melodyData on every RAF frame, so
  // in-place mutation is enough — no explicit redraw trigger needed.
  //
  // Accuracy caveats (documented in plan):
  //   • Pure shift (constant offset): exact.
  //   • Affine with residual ≤ 0.1s: events warp with chords, audibly fine.
  //   • User changed a chord's beat count: notes-per-chord stays wrong
  //     (shift can't synthesize missing beats). Escape hatch = 伴奏重生.
  function _applyTransformToTimelines(transform, rangeStart, rangeEnd) {
    const EPS = 1e-6;
    const inRange = (t) => typeof t === "number"
      && t >= rangeStart - EPS
      && t < rangeEnd - EPS;

    const shift = (events) => {
      if (!Array.isArray(events)) return 0;
      let n = 0;
      for (const e of events) {
        if (!e || !inRange(e.time)) continue;
        const newT = applyTransform(e.time, transform);
        const dur = (typeof e.end === "number") ? e.end - e.time : null;
        e.time = round2(newT);
        if (dur !== null) e.end = round2(newT + dur);
        else if (typeof e.duration === "number") {
          // duration is time-invariant for shift, scales by slope for affine
          if (transform.mode === "affine") {
            e.duration = round2(e.duration * transform.a);
          }
        }
        n++;
      }
      return n;
    };

    let total = 0;
    const acc = (typeof window !== "undefined") ? window.accData : null;
    if (acc) {
      total += shift(acc.left_hand);
      total += shift(acc.right_hand);
    }
    const mel = (typeof window !== "undefined") ? window.melodyData : null;
    if (Array.isArray(mel)) total += shift(mel);
    return total;
  }

  // Global sort + MIN_GAP dedupe + end-alignment pass. Cheap and idempotent;
  // safe to call after every mutation as a three-layer defense (calibrate
  // apply → frontend save → backend save) against time-regression bugs.
  function normalizeChords(chordData) {
    const chords = chordData.chords;
    if (!chords || chords.length === 0) return;
    chords.sort((a, b) => (a.time || 0) - (b.time || 0));
    const MIN_GAP = 0.2;
    for (let i = chords.length - 1; i > 0; i--) {
      if ((chords[i].time || 0) - (chords[i - 1].time || 0) < MIN_GAP) {
        chords.splice(i - 1, 1);
      }
    }
    for (let i = 0; i < chords.length - 1; i++) {
      chords[i].end = chords[i + 1].time;
    }
  }

  /* ========== Shared State ========== */
  let _originalChords = null;   // backup before first correction
  let _activeMode = null;       // null | "beat-tap" | "chord-align"
  let _lastCalibration = null;  // {endIdx, mode, a, b, deltaT, bpm, ts}

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

  // Helper — turn the post-tap panel into a range-selector. Hides the live
  // tap controls, injects a radio list with live blast-radius counts, and
  // wires Back/Apply buttons. Commit fires through onCommit(rangeEndIdx).
  function _showRangeSelectorStep(panel, opts) {
    const { chordData, offset, N, transform, newBpm, tapsCount, sections, onBack, onCommit } = opts;
    const total = chordData.chords.length;
    const lastCalibratedIdx = offset + N - 1;

    // Hide the live tap UI bits — we're past the tapping phase now.
    const instructionEl = panel.querySelector(".correction-instructions");
    const tapRow = panel.querySelector(".correction-tap-row");
    const statusEl = panel.querySelector(".correction-status");
    const resultEl = panel.querySelector(".correction-result");
    const actionsRow = panel.querySelector(".correction-actions");
    if (instructionEl) instructionEl.style.display = "none";
    if (tapRow) tapRow.style.display = "none";
    if (statusEl) statusEl.style.display = "none";
    if (resultEl) resultEl.style.display = "none";
    if (actionsRow) actionsRow.style.display = "none";

    // Resolve the phrase-boundary chord index (first chord at or after the
    // next section start that sits past the calibrated segment). null when
    // there is no usable section.
    let phraseEndIdx = null;
    let phraseLabel = "";
    if (Array.isArray(sections) && sections.length > 0 && lastCalibratedIdx < total - 1) {
      const segLastTime = chordData.chords[lastCalibratedIdx].time || 0;
      const nextSec = sections
        .filter(s => typeof s.start === "number" && s.start > segLastTime + 0.05)
        .sort((a, b) => a.start - b.start)[0];
      if (nextSec) {
        for (let j = lastCalibratedIdx + 1; j < total; j++) {
          if ((chordData.chords[j].time || 0) >= nextSec.start - 0.05) {
            phraseEndIdx = j - 1;  // last chord BEFORE the next section
            phraseLabel = nextSec.label || nextSec.type || "下個段落";
            break;
          }
        }
        // If loop fell through, the section boundary is past all chords.
        if (phraseEndIdx === null) phraseEndIdx = total - 1;
      }
    }

    // Human-readable transform-confidence line so the user can sanity-check
    // before committing.
    let confidence;
    if (transform.mode === "affine") {
      const slope = transform.a.toFixed(3);
      const offsetStr = (transform.b >= 0 ? "+" : "") + transform.b.toFixed(2) + " s";
      const nearPure = Math.abs(transform.a - 1) < 0.005;
      confidence = `線性延伸：×${slope} 倍 ${offsetStr} (最大殘差 ${transform.maxResidual.toFixed(2)} s)` +
                   (nearPure ? "（≈ 純位移）" : "");
    } else {
      const dt = transform.deltaT;
      const sign = dt >= 0 ? "+" : "";
      confidence = `位移模式：${sign}${dt.toFixed(2)} s`;
    }

    // Build the range-selector block.
    const block = document.createElement("div");
    block.className = "cc-range-selector";
    const segmentCount = N;
    const endCount = total - 1 - lastCalibratedIdx;
    const phraseCount = phraseEndIdx !== null ? phraseEndIdx - lastCalibratedIdx : 0;
    block.innerHTML = `
      <div class="cc-range-summary">
        已輕敲 ${tapsCount} 個節拍點 (${N} 個和絃)，BPM ${newBpm}
        <br><span class="cc-range-confidence">${confidence}</span>
      </div>
      <div class="cc-range-prompt">將此校正套用到 (請選擇)：</div>
      <label class="cc-range-opt"><input type="radio" name="cc-range" value="segment">
        <span>只校正這 ${segmentCount} 個和絃</span></label>
      <label class="cc-range-opt ${phraseEndIdx === null ? "cc-range-disabled" : ""}">
        <input type="radio" name="cc-range" value="phrase" ${phraseEndIdx === null ? "disabled" : ""}>
        <span>${phraseEndIdx === null
          ? "延伸到下個樂句邊界（沒有下個樂句）"
          : `延伸到下個樂句邊界 — ${_escape(phraseLabel)} 起 (+${phraseCount} 個)`}</span></label>
      <label class="cc-range-opt ${endCount <= 0 ? "cc-range-disabled" : ""}">
        <input type="radio" name="cc-range" value="end" ${endCount <= 0 ? "disabled" : ""}>
        <span>${endCount <= 0
          ? "延伸到結尾（此段已是最後一段）"
          : `延伸到結尾 (+${endCount} 個)`}</span></label>
      <div class="cc-range-actions">
        <button type="button" class="correction-btn cc-range-back">&#x2039; 回上一步</button>
        <button type="button" class="correction-btn correction-apply cc-range-apply" disabled>&#x2713; 套用</button>
      </div>
    `;
    panel.appendChild(block);

    const applyBtn = block.querySelector(".cc-range-apply");
    const backBtn = block.querySelector(".cc-range-back");
    block.querySelectorAll('input[name="cc-range"]').forEach(r => {
      r.addEventListener("change", () => { if (!r.disabled) applyBtn.disabled = false; });
    });

    applyBtn.onclick = () => {
      const chosen = block.querySelector('input[name="cc-range"]:checked');
      if (!chosen) return;
      let rangeEndIdx = lastCalibratedIdx;
      if (chosen.value === "phrase" && phraseEndIdx !== null) rangeEndIdx = phraseEndIdx;
      else if (chosen.value === "end") rangeEndIdx = total - 1;
      block.remove();
      onCommit(rangeEndIdx);
    };

    backBtn.onclick = () => {
      block.remove();
      if (instructionEl) instructionEl.style.display = "";
      if (tapRow) tapRow.style.display = "";
      if (statusEl) statusEl.style.display = "";
      if (resultEl && resultEl.textContent) resultEl.style.display = "";
      if (actionsRow) actionsRow.style.display = "";
      if (typeof onBack === "function") onBack();
    };
  }

  function _escape(s) {
    return String(s || "").replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[c]);
  }

  function _restorePanelFromRangeSelector(panel /*, applyBtn, cancelBtn, tapRow, instructionEl */) {
    // Intentional no-op today: the Back handler inside _showRangeSelectorStep
    // already re-shows the live tap UI. Placeholder kept so the call-site in
    // enterChordCalibrate remains explicit about the restore path.
  }

  function _commitCalibrate(chordData, ctx, rangeEndIdx, rebuildFn) {
    const { groups, N, offset, secPerBeat, transform, newBpm, segOrigTimes, segNewTimes } = ctx;
    backup(chordData);
    const chords = chordData.chords;

    // Capture the timeline window BEFORE any chord mutation so we know which
    // accData / melodyData events to shift. rangeStart = the first calibrated
    // chord's original time; rangeEnd = the original time of the first chord
    // past our touch range (or +Infinity if we're going to song end).
    const waterfallRangeStart = segOrigTimes.length > 0 ? segOrigTimes[0] : 0;
    const boundaryIdx = rangeEndIdx + 1;
    const waterfallRangeEnd = (boundaryIdx < chords.length)
      ? (chords[boundaryIdx].time || Number.POSITIVE_INFINITY)
      : Number.POSITIVE_INFINITY;

    // Left boundary fix — prior chord's end aligns with first new time
    if (offset > 0) {
      chords[offset - 1].end = round2(segNewTimes[0]);
    }
    // Rewrite the calibrated segment in place
    for (let g = 0; g < N; g++) {
      chords[offset + g].time = round2(segNewTimes[g]);
      if (g > 0) chords[offset + g - 1].end = chords[offset + g].time;
    }
    // Right boundary of the segment (set before extrapolation overwrites it)
    const lastIdx = offset + N - 1;
    if (offset + N < chords.length) {
      chords[lastIdx].end = chords[offset + N].time;
    } else {
      const lastGroup = groups[N - 1];
      chords[lastIdx].end = round2(chords[lastIdx].time + lastGroup.length * secPerBeat);
    }

    // Extrapolate the transform onto [offset+N .. rangeEndIdx] if the user
    // picked a range past the segment.
    const extraFrom = offset + N;
    const extraTo = Math.min(rangeEndIdx, chords.length - 1);
    const extrapolated = (extraTo >= extraFrom)
      ? applyTransformToRange(chordData, extraFrom, extraTo, transform)
      : 0;

    // Three-layer defense: global sort + dedupe + end-alignment.
    normalizeChords(chordData);

    // Step 1 waterfall auto-sync — shift accData / melodyData events that
    // lived under the calibrated/extrapolated chord range. Uses the ORIGINAL
    // chord-time window we captured before mutation.
    const waterfallShifted = _applyTransformToTimelines(
      transform, waterfallRangeStart, waterfallRangeEnd
    );

    // Authoritative BPM for dot-grid rendering.
    chordData.bpm = newBpm;

    // Phase 3: bump beat_version so the player's stale-acc detector fires
    // on the next reload (chord times moved → existing accompaniment is
    // off the new grid). Skip if chordData has no beat fields yet (legacy).
    if (typeof chordData.beat_version === "number") {
      chordData.beat_version = (chordData.beat_version | 0) + 1;
    }

    // Stash for the right-click "延伸至此和弦" entry.
    _lastCalibration = {
      endIdx: lastIdx,
      mode: transform.mode,
      a: transform.a,
      b: transform.b,
      deltaT: transform.deltaT,
      bpm: newBpm,
      ts: Date.now(),
    };

    let summary = `已校正 ${N} 個和絃`;
    if (extrapolated > 0) summary += ` + 延伸 ${extrapolated} 個`;
    summary += ` (BPM=${newBpm})`;
    if (waterfallShifted > 0) summary += `｜瀑布流 ${waterfallShifted} 筆已同步`;

    exitChordCalibrate();
    rebuildFn();
    showToast(summary, 2800);
  }

  // Right-click reuse path. Applies the last-stored calibration transform
  // onto [_lastCalibration.endIdx+1 .. targetIdx]. Clears _lastCalibration
  // after success so the entry disappears from the menu until the next
  // calibrate session seeds it again.
  function applyLastCalibrateToIdx(chordData, targetIdx, rebuildFn) {
    if (!_lastCalibration) { showToast("尚無可延伸的校正結果", 1800); return false; }
    const { endIdx, mode, a, b, deltaT, bpm } = _lastCalibration;
    const chords = chordData?.chords;
    if (!chords || targetIdx <= endIdx || endIdx >= chords.length - 1) {
      showToast("無法延伸到此和弦", 1800); return false;
    }
    backup(chordData);
    const transform = { mode, a, b, deltaT };
    const fromIdx = endIdx + 1;
    const toIdx = Math.min(targetIdx, chords.length - 1);

    // Capture the waterfall window BEFORE applyTransformToRange mutates
    // chord times. rangeStart = first untouched chord's original time;
    // rangeEnd = first chord past the extension (or +Infinity if targetIdx
    // is the final chord).
    const waterfallRangeStart = chords[fromIdx].time || 0;
    const boundaryIdx = toIdx + 1;
    const waterfallRangeEnd = (boundaryIdx < chords.length)
      ? (chords[boundaryIdx].time || Number.POSITIVE_INFINITY)
      : Number.POSITIVE_INFINITY;

    const count = applyTransformToRange(chordData, fromIdx, toIdx, transform);
    normalizeChords(chordData);
    const waterfallShifted = _applyTransformToTimelines(
      transform, waterfallRangeStart, waterfallRangeEnd
    );
    if (typeof bpm === "number") chordData.bpm = bpm;
    // Phase 3: bump beat_version so player can detect stale acc cache.
    if (typeof chordData.beat_version === "number") {
      chordData.beat_version = (chordData.beat_version | 0) + 1;
    }
    // Roll the stash forward so a second right-click can continue from the
    // new boundary; but if the user hit song-end, clear it.
    if (targetIdx >= chords.length - 1) {
      _lastCalibration = null;
    } else {
      _lastCalibration = { ..._lastCalibration, endIdx: targetIdx, ts: Date.now() };
    }
    if (typeof rebuildFn === "function") rebuildFn();
    let msg = `已延伸 ${count} 個和絃`;
    if (waterfallShifted > 0) msg += `｜瀑布流 ${waterfallShifted} 筆已同步`;
    showToast(msg, 2000);
    return true;
  }

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

      // 3. Compute (not commit) segment edits so we can fit the transform
      // against the pre-edit vs post-edit chord times.
      const chords = chordData.chords;
      const offset = startChordIdx;
      const N = Math.min(groups.length, chords.length - offset);

      const segOrigTimes = [];
      const segNewTimes = [];
      for (let g = 0; g < N; g++) {
        segOrigTimes.push(chords[offset + g].time || 0);
        segNewTimes.push(Math.max(0, groups[g][0].time - lag));
      }

      const transform = fitTransform(segOrigTimes, segNewTimes);
      const newBpm = Math.max(30, Math.min(300, Math.round(60 / secPerBeat)));

      // 4. Swap the panel into range-selector mode. Commit only fires when
      // the user explicitly picks a range and clicks 套用.
      const commitCtx = {
        groups, N, offset, secPerBeat, transform, newBpm,
        segOrigTimes, segNewTimes,
      };
      _showRangeSelectorStep(panel, {
        chordData,
        offset,
        N,
        transform,
        newBpm,
        tapsCount: taps.length,
        sections: options.sections || null,
        onBack: () => {
          _restorePanelFromRangeSelector(panel, applyBtn, cancelBtn, tapRow, instructionEl);
        },
        onCommit: (rangeEndIdx) => {
          _commitCalibrate(chordData, commitCtx, rangeEndIdx, rebuildFn);
        },
      });
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
    applyLastCalibrateToIdx,
    getLastCalibration() { return _lastCalibration ? { ..._lastCalibration } : null; },
    clearLastCalibration() { _lastCalibration = null; },
    // Feature 6: Merge adjacent chords
    mergeChord,
    // Shared
    backup,
    revert,
    hasBackup,
    get activeMode() { return _activeMode; },
  };
})();
