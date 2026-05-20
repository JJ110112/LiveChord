// frontend/js/midi-exporter.js
window.MidiExporter = (function() {
  const TPQ = 480;

  function writeVLQ(value) {
    let buffer = [value & 0x7F];
    while ((value >>= 7)) {
      buffer.push((value & 0x7F) | 0x80);
    }
    return buffer.reverse();
  }

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

  function _eventTime(e) {
    const t = Number(e && (e.time != null ? e.time : e.start));
    return Number.isFinite(t) ? t : 0;
  }

  function _eventDuration(e) {
    const d = Number(e && e.duration);
    if (Number.isFinite(d) && d > 0) return d;
    const start = Number(e && (e.time != null ? e.time : e.start));
    const end = Number(e && e.end);
    if (Number.isFinite(start) && Number.isFinite(end) && end > start) return end - start;
    return 0.5;
  }

  function _eventPitches(e) {
    const raw = Array.isArray(e && e.pitches)
      ? e.pitches
      : [e && (e.pitch != null ? e.pitch : e.midi)];
    return raw
      .map(p => Math.round(Number(p)))
      .filter(p => Number.isFinite(p) && p >= 0 && p <= 127);
  }

  function _eventVelocity(e) {
    const raw = Number(e && e.velocity);
    const scaled = Number.isFinite(raw) ? (raw <= 1 ? raw * 127 : raw) : 102;
    return Math.min(127, Math.max(1, Math.round(scaled)));
  }

  function createTrack(events, channel, bpm, includeTempo) {
    let trackData = [];
    let lastTick = 0;
    const midiEvents = [];

    if (includeTempo) {
      midiEvents.push({ tick: 0, order: 0, bytes: _tempoMeta(bpm) });
    }
    midiEvents.push({ tick: 0, order: 1, bytes: [0xC0 | channel, 0x00] });

    for (const e of events || []) {
      const pitches = _eventPitches(e);
      if (!pitches.length) continue;
      const start = _eventTime(e);
      const duration = _eventDuration(e); // canonical readable duration; gate_ratio is playback-only.
      const startTick = _secondsToTicks(start, bpm);
      const endTick = Math.max(startTick + 1, _secondsToTicks(start + duration, bpm));
      const vel = _eventVelocity(e);
      for (const pitch of pitches) {
        midiEvents.push({ tick: startTick, order: 2, bytes: [0x90 | channel, pitch, vel] });
        midiEvents.push({ tick: endTick, order: 1, bytes: [0x80 | channel, pitch, 0] });
      }
    }

    midiEvents.sort((a, b) => a.tick - b.tick || a.order - b.order);

    for (const me of midiEvents) {
      const delta = Math.max(0, me.tick - lastTick);
      trackData.push(...writeVLQ(delta));
      trackData.push(...me.bytes);
      lastTick = me.tick;
    }

    trackData.push(0x00, 0xFF, 0x2F, 0x00);

    const dataLen = trackData.length;
    const header = [
      0x4D, 0x54, 0x72, 0x6B,
      (dataLen >> 24) & 0xFF,
      (dataLen >> 16) & 0xFF,
      (dataLen >> 8) & 0xFF,
      dataLen & 0xFF
    ];

    return header.concat(trackData);
  }

  function exportMidi(accData, title, style, level, opts) {
    if (!accData) return;
    opts = opts || {};

    const bpm = _safeBpm(opts.bpm || accData.bpm);

    // Caller may supply pre-filtered left/right event arrays (e.g. to match
    // the user's current practice-mode selection). Fall back to accData.
    const leftEvents = (opts.leftEvents != null) ? opts.leftEvents : (accData.left_hand || []);
    const rightEvents = (opts.rightEvents != null) ? opts.rightEvents : (accData.right_hand || []);

    const leftTrack = createTrack(leftEvents, 0, bpm, true);
    const rightTrack = createTrack(rightEvents, 1, bpm, false);

    const mthd = [
      0x4D, 0x54, 0x68, 0x64,
      0x00, 0x00, 0x00, 0x06,
      0x00, 0x01,
      0x00, 0x02,
      (TPQ >> 8) & 0xFF, TPQ & 0xFF
    ];

    const midiFile = mthd.concat(leftTrack, rightTrack);
    const u8 = new Uint8Array(midiFile);

    const safeName = (title || 'AI_Accompaniment').replace(/[<>:"/\\|?*]+/g, '_');
    const sStyle = style || 'Accomp';
    const sLevel = level || 'L1';
    const modeSfx = opts.modeSuffix ? `_${opts.modeSuffix}` : "";
    const fileName = `${safeName}_${sStyle}_${sLevel}${modeSfx}.mid`;

    let binary = '';
    for (let i = 0; i < u8.length; i++) {
      binary += String.fromCharCode(u8[i]);
    }
    const dataUrl = 'data:audio/midi;base64,' + btoa(binary);

    const a = document.createElement('a');
    a.style.display = 'none';
    a.href = dataUrl;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  return { exportMidi };
})();
