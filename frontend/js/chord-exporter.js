/**
 * Chord chart export — PDF (via window.print) + MIDI (chord-block).
 * Companion to midi-exporter.js (which is for AI-accompaniment events).
 * This module exports what the user SEES in the player ribbon — chord
 * names grouped by bar, with section headers, key/BPM metadata.
 */
window.ChordExporter = (function () {
  function _t(k, v) {
    return (window.LiveChordI18n && window.LiveChordI18n.t)
      ? window.LiveChordI18n.t(k, v) : k;
  }

  // ---- MIDI byte helpers (duplicated from midi-exporter.js intentionally;
  //      chord-block event shape differs from per-note events) ----
  function writeVLQ(value) {
    let buffer = [value & 0x7F];
    while ((value >>= 7)) {
      buffer.push((value & 0x7F) | 0x80);
    }
    return buffer.reverse();
  }

  const TPQ = 480;

  function _safeBpm(bpm) {
    const v = Number(bpm);
    return Number.isFinite(v) && v > 0 ? v : 120;
  }

  function _secondsToTicks(seconds, bpm) {
    return Math.max(0, Math.round(Number(seconds || 0) * _safeBpm(bpm) * TPQ / 60));
  }

  function _tempoMeta(bpm) {
    const mpqn = Math.max(1, Math.round(60000000 / _safeBpm(bpm)));
    return [0xFF, 0x51, 0x03, (mpqn >> 16) & 0xFF, (mpqn >> 8) & 0xFF, mpqn & 0xFF];
  }

  function _chordEnd(chords, index, bpm) {
    const c = chords[index] || {};
    const explicit = Number(c.end);
    if (Number.isFinite(explicit) && explicit > Number(c.time || 0)) return explicit;
    const next = chords[index + 1];
    const nextTime = Number(next && next.time);
    if (Number.isFinite(nextTime) && nextTime > Number(c.time || 0)) return nextTime;
    return Number(c.time || 0) + (60 / _safeBpm(bpm)) * 4;
  }

  // Resolve displayed chords honoring transpose + capo (mirrors
  // player.js `_displayChords()` but is self-contained).
  function _displayChords(chords, transpose, capo) {
    const shift = (transpose || 0) - (capo || 0);
    if (shift === 0) return chords.slice();
    return chords.map(c => ({ ...c, chord: transposeChord(c.chord, shift) }));
  }

  function _displayKey(key, transpose, capo) {
    const shift = (transpose || 0) - (capo || 0);
    if (shift === 0 || !key) return key || '';
    return transposeChord(key, shift);
  }

  // beats_per_bar resolution: explicit field → ChordCorrection helper →
  // fallback 4. Mirrors the auto-split panel's logic.
  function _resolveBpb(chordData) {
    const bpm = (chordData && chordData.bpm) || 120;
    const secPerBeat = 60 / bpm;
    if (window.ChordCorrection && window.ChordCorrection.inferBeatsPerBar) {
      const v = window.ChordCorrection.inferBeatsPerBar(chordData, secPerBeat);
      if (v && v >= 2 && v <= 16) return v;
    }
    if (chordData.beats_per_bar && chordData.beats_per_bar >= 2) {
      return chordData.beats_per_bar;
    }
    return 4;
  }

  // Find the bar index a given time belongs to. Uses downbeats[] when
  // present, else synthesizes a bar grid from beats[] every Nth beat,
  // else falls back to fixed BPM math.
  function _barIndexAt(t, chordData, bpb) {
    const dbs = (chordData && Array.isArray(chordData.downbeats)) ? chordData.downbeats : [];
    if (dbs.length > 0) {
      let idx = 0;
      for (let i = 0; i < dbs.length; i++) {
        if (dbs[i] <= t + 0.05) idx = i; else break;
      }
      return idx;
    }
    const bts = (chordData && Array.isArray(chordData.beats)) ? chordData.beats : [];
    if (bts.length > 0) {
      let beatIdx = 0;
      for (let i = 0; i < bts.length; i++) {
        if (bts[i] <= t + 0.05) beatIdx = i; else break;
      }
      return Math.floor(beatIdx / bpb);
    }
    const bpm = (chordData && chordData.bpm) || 120;
    const barDur = (60 / bpm) * bpb;
    return Math.floor(t / barDur);
  }

  // Group chords into bars: returns [{barIdx, chords: [name, ...]}].
  // Consecutive chords sharing the same bar coalesce into one bar entry.
  function _groupByBar(chords, chordData, bpb) {
    const bars = [];
    let curBar = -1, cur = null;
    for (const c of chords) {
      const bi = _barIndexAt(c.time, chordData, bpb);
      if (bi !== curBar || !cur) {
        cur = { barIdx: bi, chords: [c.chord], firstTime: c.time };
        bars.push(cur);
        curBar = bi;
      } else {
        // Same bar — append unless identical to previous (collapse repeats
        // within a bar to keep the chart compact).
        if (cur.chords[cur.chords.length - 1] !== c.chord) {
          cur.chords.push(c.chord);
        }
      }
    }
    return bars;
  }

  // Section label numbering — mirrors player.js:1666-1715. Returns a
  // map from section-start-time → numbered label (e.g. "Verse 1").
  function _buildSectionLabels(sectionData) {
    const labels = new Map();
    if (!sectionData || !Array.isArray(sectionData.sections)) return labels;
    const secs = sectionData.sections;
    const typeCounts = {};
    for (const s of secs) typeCounts[s.type] = (typeCounts[s.type] || 0) + 1;
    const typeOcc = {};
    const noNum = new Set(['intro', 'outro', 'dialogue']);
    for (const s of secs) {
      typeOcc[s.type] = (typeOcc[s.type] || 0) + 1;
      const total = typeCounts[s.type] || 1;
      const count = typeOcc[s.type];
      const numStr = (total > 1 && !noNum.has(s.type)) ? ` ${count}` : '';
      const baseType = (s.type || '').replace(/\d+|'/g, '');
      const enType = baseType.charAt(0).toUpperCase() + baseType.slice(1).replace('_', ' ');
      const modeTag = s.mode && s.mode !== 'Major' && s.mode !== 'Minor' ? ` ${s.mode}` : '';
      labels.set(s.start, `${enType}${numStr}${modeTag}`);
    }
    return labels;
  }

  // Compute uniform column width for monospace bar layout: longest
  // chord name in the song + 2 spaces, clamped to [6, 12].
  function _columnWidth(chords) {
    let maxLen = 1;
    for (const c of chords) {
      if (c.chord && c.chord.length > maxLen) maxLen = c.chord.length;
    }
    return Math.min(12, Math.max(6, maxLen + 2));
  }

  // Pad a chord name to fixed width, right-padded with spaces.
  function _pad(s, w) {
    const v = String(s == null ? '' : s);
    if (v.length >= w) return v + ' ';   // overflow: leave one space gap
    return v + ' '.repeat(w - v.length);
  }

  // Build one printed bar's textual contents (the part between `| ... |`).
  // Multiple chords inside a bar share the bar's slot, each padded to colW.
  function _renderBarText(bar, colW) {
    if (!bar.chords.length) return _pad('', colW);
    return bar.chords.map(n => _pad(n, colW)).join('');
  }

  // Slice a flat bar list into row groups of `barsPerRow` bars each.
  function _chunkRows(bars, barsPerRow) {
    const rows = [];
    for (let i = 0; i < bars.length; i += barsPerRow) {
      rows.push(bars.slice(i, i + barsPerRow));
    }
    return rows;
  }

  // -------------------------------------------------------------
  // PDF export — builds a chord chart as a clean standalone HTML page
  // opened in a new window, then calls print() on that window.
  //
  // Why new-window instead of in-page #chordPrintSheet + @media print:
  // mobile WebKit / Android Chrome have unreliable @media print
  // handling — display:none on body children, position:sticky
  // descendants, and viewport-vh calculations all break the snapshot
  // engine in mobile print mode, producing blank previews. Opening a
  // brand-new document sidesteps every one of those quirks.
  // -------------------------------------------------------------
  function _escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function _buildChartBlocks(opts) {
    const { chordData, sectionData, useCurrentKey, transpose, capo } = opts;
    const chords = useCurrentKey
      ? _displayChords(chordData.chords, transpose, capo)
      : chordData.chords.slice();
    const bpb = _resolveBpb(chordData);
    const bars = _groupByBar(chords, chordData, bpb);
    const colW = _columnWidth(chords);
    const barsPerRow = (bpb === 2) ? 4 : 2;
    const labels = _buildSectionLabels(sectionData);

    const blocks = [];
    let cur = { label: null, bars: [] };
    blocks.push(cur);
    const sortedStarts = Array.from(labels.keys()).sort((a, b) => a - b);
    let nextStartIdx = 0;

    if (sortedStarts.length > 0 && bars.length > 0) {
      let firstSec = null;
      const firstT = bars[0].firstTime;
      for (let i = 0; i < sortedStarts.length; i++) {
        if (sortedStarts[i] <= firstT + 0.05) firstSec = sortedStarts[i];
        else break;
      }
      if (firstSec != null) {
        cur.label = labels.get(firstSec);
        while (nextStartIdx < sortedStarts.length && sortedStarts[nextStartIdx] <= firstT + 0.05) {
          nextStartIdx++;
        }
      } else {
        cur.label = 'Intro';
      }
    } else if (bars.length > 0) {
      cur.label = 'Intro';
    }

    for (const b of bars) {
      while (nextStartIdx < sortedStarts.length && b.firstTime >= sortedStarts[nextStartIdx] - 0.05) {
        const newLabel = labels.get(sortedStarts[nextStartIdx]);
        nextStartIdx++;
        if (cur.bars.length === 0) {
          cur.label = newLabel;
        } else {
          cur = { label: newLabel, bars: [] };
          blocks.push(cur);
        }
      }
      cur.bars.push(b);
    }

    return { blocks, colW, barsPerRow, bpb };
  }

  function _buildPrintHtml(opts) {
    const { chordData, useCurrentKey, transpose, capo, title } = opts;
    const { blocks, colW, barsPerRow, bpb } = _buildChartBlocks(opts);
    const displayKey = useCurrentKey
      ? _displayKey(chordData.key, transpose, capo)
      : (chordData.key || '');
    const origKey = chordData.key || '';
    const bpm = (chordData && chordData.bpm) ? Math.round(chordData.bpm) : null;
    const ts = chordData && chordData.time_signature
      ? chordData.time_signature
      : `${bpb}/4`;

    const titleText = title || _t('player.export.untitled');
    const metaParts = [];
    if (displayKey) {
      let k = `Key: ${displayKey}`;
      if (capo > 0) k += ` (capo ${capo})`;
      if (useCurrentKey && displayKey !== origKey) k += ` (original: ${origKey})`;
      metaParts.push(k);
    }
    if (bpm) metaParts.push(`BPM: ${bpm}`);
    metaParts.push(`Time: ${ts}`);
    const metaText = metaParts.join('   |   ');

    let bodyHtml = `<h1>${_escapeHtml(titleText)}</h1>`;
    bodyHtml += `<div class="cps-meta">${_escapeHtml(metaText)}</div>`;
    for (const block of blocks) {
      if (block.bars.length === 0) continue;
      bodyHtml += `<div class="cps-section">`;
      if (block.label) bodyHtml += `<h3>[${_escapeHtml(block.label)}]</h3>`;
      const rows = _chunkRows(block.bars, barsPerRow);
      for (const row of rows) {
        let txt = '| ';
        for (const b of row) txt += _renderBarText(b, colW) + '| ';
        bodyHtml += `<div class="cps-line">${_escapeHtml(txt.trimEnd())}</div>`;
      }
      bodyHtml += `</div>`;
    }
    const d = new Date();
    const dateStr = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    bodyHtml += `<div class="cps-footer">Generated by livechord.org · ${dateStr}</div>`;

    // All styles inline — the popup window has no stylesheet of its own.
    const css = `
      * { box-sizing: border-box; margin: 0; padding: 0; }
      html, body { background: #fff; color: #000; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
      body { padding: 18mm 14mm; font-family: 'Courier New', Consolas, monospace; line-height: 1.6; }
      h1 { font-size: 22pt; margin: 0 0 4mm; font-family: Georgia, "Noto Sans TC", serif; page-break-after: avoid; break-after: avoid; }
      .cps-meta { font-size: 11pt; color: #333; margin-bottom: 6mm; font-family: Georgia, "Noto Sans TC", serif; }
      h3 { font-size: 13pt; margin: 5mm 0 2mm; font-family: Georgia, "Noto Sans TC", serif; page-break-after: avoid; break-after: avoid; }
      .cps-section { page-break-inside: avoid; break-inside: avoid; margin-bottom: 4mm; }
      .cps-line { font-size: 12pt; white-space: pre; }
      .cps-footer { margin-top: 8mm; font-size: 9pt; color: #666; font-family: Georgia, "Noto Sans TC", serif; }
      .cps-controls { position: fixed; top: 12px; right: 12px; display: flex; gap: 8px; }
      .cps-controls button { font-family: system-ui, sans-serif; font-size: 14px; padding: 8px 14px; border: 1px solid #888; background: #f5f5f5; border-radius: 6px; cursor: pointer; }
      @media print { .cps-controls { display: none !important; } body { padding: 0; } @page { margin: 1.5cm; } }
    `;
    const lang = (document.documentElement.lang || 'en');
    const printBtnLabel = _t('player.export.download');
    return `<!DOCTYPE html>
<html lang="${_escapeHtml(lang)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${_escapeHtml(titleText)} — LiveChord</title>
<style>${css}</style>
</head>
<body>
<div class="cps-controls">
  <button id="cpsPrintAgain" type="button">${_escapeHtml(printBtnLabel)}</button>
  <button id="cpsClose" type="button">✕</button>
</div>
${bodyHtml}
<script>
  // Auto-print after the page is fully laid out. Mobile WebKit needs a
  // small delay so font rendering + layout settle before the snapshot.
  function _doPrint() { try { window.print(); } catch (e) {} }
  document.getElementById('cpsPrintAgain').addEventListener('click', _doPrint);
  document.getElementById('cpsClose').addEventListener('click', function(){ window.close(); });
  if (document.readyState === 'complete') {
    setTimeout(_doPrint, 250);
  } else {
    window.addEventListener('load', function(){ setTimeout(_doPrint, 250); });
  }
</` + `script>
</body>
</html>`;
  }

  function _exportPdf(opts, modalEls) {
    let html;
    try {
      html = _buildPrintHtml(opts);
    } catch (e) {
      _toast(_t('toast.export.failed', { err: e.message || 'render failed' }));
      return;
    }
    // Close the modal first so the new tab focus isn't fighting the backdrop.
    if (modalEls && modalEls.backdrop && modalEls.backdrop.parentNode) {
      modalEls.backdrop.parentNode.removeChild(modalEls.backdrop);
    }
    // Open the chart in a new window/tab and write the doc into it.
    // Important: must be invoked synchronously inside the click handler chain
    // so iOS Safari / mobile Chrome don't treat it as a blocked popup.
    const w = window.open('', '_blank');
    if (!w) {
      _toast(_t('toast.export.popup_blocked'), 5000);
      return;
    }
    w.document.open();
    w.document.write(html);
    w.document.close();
  }

  // -------------------------------------------------------------
  // MIDI export — chord-block per chord event, Format 0 SMF
  // -------------------------------------------------------------
  function _safeFilename(title) {
    const cleaned = (title || '').replace(/[<>:"/\\|?*]+/g, '_').trim();
    return cleaned || 'livechord_chords';
  }

  async function _ensureChordNotes(uniqueNames, chordCache) {
    const missing = uniqueNames.filter(n => !chordCache[n] || !Array.isArray(chordCache[n].notes) || chordCache[n].notes.length === 0);
    if (missing.length === 0) return { ok: true, names: uniqueNames };
    if (!window.API || !window.API.chordInfo) {
      return { ok: false, badName: missing[0], reason: 'API.chordInfo unavailable' };
    }
    const results = await Promise.allSettled(missing.map(n => window.API.chordInfo(n)));
    for (let i = 0; i < results.length; i++) {
      const name = missing[i];
      const r = results[i];
      if (r.status === 'fulfilled' && r.value && Array.isArray(r.value.notes) && r.value.notes.length > 0) {
        chordCache[name] = chordCache[name] || {};
        chordCache[name].notes = r.value.notes;
        if (r.value.jianpu != null) chordCache[name].jianpu = r.value.jianpu;
      } else {
        return { ok: false, badName: name, reason: 'no notes returned' };
      }
    }
    return { ok: true };
  }

  // chordCache notes are pitch-class NAMES (e.g. "Bb", "D", "F"), not MIDI
  // numbers. Voice the chord as a closed block starting at root in C3-B3
  // range (MIDI 48-59), each subsequent tone the lowest semitone above
  // the previous so the whole voicing fits within an octave-and-a-bit.
  function _voiceChordToMidi(noteNames) {
    if (!Array.isArray(noteNames) || noteNames.length === 0) return [];
    const semis = noteNames.map(n => {
      if (typeof n === 'number') return ((n % 12) + 12) % 12;
      if (typeof window.noteToSemitone === 'function') return window.noteToSemitone(n);
      return 0;
    });
    const rootMidi = 48 + semis[0];   // C3..B3 region
    const out = [rootMidi];
    let prev = rootMidi;
    for (let i = 1; i < semis.length; i++) {
      let m = 48 + semis[i];
      while (m <= prev) m += 12;
      out.push(m);
      prev = m;
    }
    return out;
  }

  function _buildChordBlockTrack(chords, chordCache, bpm) {
    // Build absolute-tick event list.
    const events = [];
    events.push({ tick: 0, order: 0, bytes: _tempoMeta(bpm) });
    // Program change to acoustic piano on ch 0.
    events.push({ tick: 0, order: 1, bytes: [0xC0, 0x00] });
    for (let i = 0; i < chords.length; i++) {
      const c = chords[i];
      const rawNotes = (chordCache[c.chord] && Array.isArray(chordCache[c.chord].notes)) ? chordCache[c.chord].notes : [];
      const midiNotes = _voiceChordToMidi(rawNotes);
      if (midiNotes.length === 0) continue;
      const onTick = _secondsToTicks(c.time, bpm);
      const offTick = Math.max(onTick + 1, _secondsToTicks(_chordEnd(chords, i, bpm), bpm));
      const vel = 96;
      for (const p of midiNotes) {
        if (typeof p !== 'number' || p < 0 || p > 127) continue;
        events.push({ tick: onTick, order: 2, bytes: [0x90, p, vel] });
        events.push({ tick: offTick, order: 1, bytes: [0x80, p, 0] });
      }
    }
    events.sort((a, b) => a.tick - b.tick || a.order - b.order);

    // Emit delta-time + bytes.
    let trackData = [];
    let lastTick = 0;
    for (const ev of events) {
      const delta = Math.max(0, ev.tick - lastTick);
      trackData.push(...writeVLQ(delta));
      trackData.push(...ev.bytes);
      lastTick = ev.tick;
    }
    // End of track.
    trackData.push(0x00, 0xFF, 0x2F, 0x00);

    const dataLen = trackData.length;
    const header = [
      0x4D, 0x54, 0x72, 0x6B,                                    // "MTrk"
      (dataLen >> 24) & 0xFF, (dataLen >> 16) & 0xFF,
      (dataLen >> 8) & 0xFF,  dataLen & 0xFF
    ];
    return header.concat(trackData);
  }

  async function _exportMidi(opts, modalEls) {
    const { chordData, chordCache, useCurrentKey, transpose, capo, title } = opts;
    const chords = useCurrentKey
      ? _displayChords(chordData.chords, transpose, capo)
      : chordData.chords.slice();

    // Disable the Download button and show preparing-state label (critical
    // review §2 — guard against double-click during async fetch).
    const btn = modalEls && modalEls.dlBtn;
    const origLabel = btn ? btn.textContent : '';
    if (btn) {
      btn.disabled = true;
      btn.textContent = _t('toast.export.preparing_notes');
    }
    const restoreBtn = () => {
      if (btn) { btn.disabled = false; btn.textContent = origLabel; }
    };

    const unique = Array.from(new Set(chords.map(c => c.chord).filter(Boolean)));
    let pre;
    try {
      pre = await _ensureChordNotes(unique, chordCache);
    } catch (e) {
      restoreBtn();
      _toast(_t('toast.export.midi_no_notes', { name: '(network)' }));
      return;
    }
    if (!pre.ok) {
      restoreBtn();
      _toast(_t('toast.export.midi_no_notes', { name: pre.badName || '(unknown)' }));
      return;
    }

    const track = _buildChordBlockTrack(chords, chordCache, chordData.bpm);
    const mthd = [
      0x4D, 0x54, 0x68, 0x64,   // "MThd"
      0x00, 0x00, 0x00, 0x06,   // header length 6
      0x00, 0x00,               // Format 0
      0x00, 0x01,               // 1 track
      (TPQ >> 8) & 0xFF, TPQ & 0xFF
    ];
    const bytes = new Uint8Array(mthd.concat(track));
    const blob = new Blob([bytes], { type: 'audio/midi' });
    const url = URL.createObjectURL(blob);
    const fname = `${_safeFilename(title)}_chords.mid`;
    const a = document.createElement('a');
    a.style.display = 'none';
    a.href = url;
    a.download = fname;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);

    restoreBtn();
    if (modalEls && modalEls.backdrop && modalEls.backdrop.parentNode) {
      modalEls.backdrop.parentNode.removeChild(modalEls.backdrop);
    }
  }

  // -------------------------------------------------------------
  // Modal UI
  // -------------------------------------------------------------
  function _toast(msg, dur) {
    if (typeof window.showToast === 'function') {
      window.showToast(msg, dur || 3500);
    } else {
      console.log('[ChordExporter]', msg);
    }
  }

  function _buildOptionRow(format, labelKey, descKey, checked) {
    const lbl = document.createElement('label');
    lbl.className = 'ce-fmt-card';
    lbl.style.cssText = 'display:flex; align-items:flex-start; gap:10px; padding:10px 12px; border:1px solid var(--lc-popup-border-color, rgba(255,255,255,0.12)); border-radius:8px; cursor:pointer; margin-bottom:8px;';
    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = 'ce-format';
    radio.value = format;
    radio.checked = !!checked;
    radio.style.cssText = 'margin-top:3px;';
    const body = document.createElement('div');
    body.style.cssText = 'flex:1;';
    const lblText = document.createElement('div');
    lblText.style.cssText = 'font-weight:600; margin-bottom:2px;';
    lblText.textContent = _t(labelKey);
    const descText = document.createElement('div');
    descText.style.cssText = 'font-size:12px; opacity:0.75;';
    descText.textContent = _t(descKey);
    body.appendChild(lblText);
    body.appendChild(descText);
    lbl.appendChild(radio);
    lbl.appendChild(body);
    return { row: lbl, radio };
  }

  function openModal(opts) {
    if (!opts || !opts.chordData || !Array.isArray(opts.chordData.chords) || opts.chordData.chords.length === 0) {
      _toast(_t('toast.error.no_chord_data'), 2500);
      return;
    }

    // Tear down any previous modal first.
    document.querySelectorAll('.chord-export-modal-backdrop').forEach(el => el.remove());

    const backdrop = document.createElement('div');
    backdrop.className = 'lc-modal-backdrop chord-export-modal-backdrop';

    const modal = document.createElement('div');
    modal.className = 'lc-modal';
    modal.style.cssText = 'min-width:340px; max-width:min(480px, 92vw);';

    const titleEl = document.createElement('div');
    titleEl.style.cssText = 'font-size:16px; font-weight:600; margin-bottom:14px;';
    titleEl.textContent = _t('player.export.modal_title');
    modal.appendChild(titleEl);

    const pdfOpt = _buildOptionRow('pdf', 'player.export.format_pdf', 'player.export.format_pdf_desc', true);
    const midiOpt = _buildOptionRow('midi', 'player.export.format_midi', 'player.export.format_midi_desc', false);
    modal.appendChild(pdfOpt.row);
    modal.appendChild(midiOpt.row);

    // Transpose checkbox — only visible when transpose ≠ 0 or capo ≠ 0.
    const transpose = opts.transpose || 0;
    const capo = opts.capo || 0;
    let useCurrentBox = null;
    if (transpose !== 0 || capo !== 0) {
      const curKey = _displayKey(opts.chordData.key, transpose, capo);
      const origKey = opts.chordData.key || '';
      const wrap = document.createElement('label');
      wrap.style.cssText = 'display:flex; align-items:center; gap:8px; margin:10px 0 4px; font-size:13px;';
      useCurrentBox = document.createElement('input');
      useCurrentBox.type = 'checkbox';
      useCurrentBox.checked = true;
      const span = document.createElement('span');
      span.textContent = _t('player.export.use_current_key', { current: curKey, original: origKey });
      wrap.appendChild(useCurrentBox);
      wrap.appendChild(span);
      modal.appendChild(wrap);
    }

    const actions = document.createElement('div');
    actions.className = 'lc-modal-actions';
    actions.style.cssText = 'margin-top:14px; display:flex; gap:8px; justify-content:flex-end;';
    const cancelBtn = document.createElement('button');
    cancelBtn.textContent = _t('player.export.cancel');
    cancelBtn.style.cssText = 'padding:8px 14px; border-radius:6px; border:1px solid currentColor; background:transparent; color:inherit; cursor:pointer;';
    const dlBtn = document.createElement('button');
    dlBtn.textContent = _t('player.export.download');
    dlBtn.style.cssText = 'padding:8px 14px; border-radius:6px; border:none; background:var(--accent, #4c8bff); color:#fff; font-weight:600; cursor:pointer;';
    actions.appendChild(cancelBtn);
    actions.appendChild(dlBtn);
    modal.appendChild(actions);

    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);

    const close = () => {
      if (backdrop.parentNode) backdrop.parentNode.removeChild(backdrop);
      document.removeEventListener('keydown', escListener);
    };
    const escListener = (e) => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', escListener);
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
    cancelBtn.addEventListener('click', close);

    dlBtn.addEventListener('click', () => {
      const format = pdfOpt.radio.checked ? 'pdf' : (midiOpt.radio.checked ? 'midi' : 'pdf');
      const useCurrentKey = useCurrentBox ? useCurrentBox.checked : true;
      const callOpts = {
        chordData: opts.chordData,
        sectionData: opts.sectionData,
        chordCache: opts.chordCache || {},
        transpose, capo,
        title: opts.title,
        useCurrentKey,
      };
      const modalEls = { backdrop, dlBtn };
      if (format === 'pdf') {
        _exportPdf(callOpts, modalEls);
      } else {
        _exportMidi(callOpts, modalEls).catch(e => {
          dlBtn.disabled = false;
          dlBtn.textContent = _t('player.export.download');
          _toast(_t('toast.export.failed', { err: (e && e.message) || 'unknown' }));
        });
      }
    });
  }

  return { openModal };
})();
