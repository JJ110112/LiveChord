// frontend/js/midi-exporter.js
window.MidiExporter = (function() {
  function writeVLQ(value) {
    let buffer = [value & 0x7F];
    while ((value >>= 7)) {
      buffer.push((value & 0x7F) | 0x80);
    }
    return buffer.reverse();
  }

  function createTrack(events, channel) {
    let trackData = [];
    let lastTick = 0;

    // Convert to absolute ticks first (1 sec = 960 ticks @ 120bpm)
    const midiEvents = [];
    for (const e of events) {
      if (!e.pitch || isNaN(e.time)) continue;
      const startTick =     Math.round(e.time * 960);
      const endTick =   Math.round((e.time + (e.duration || 0.5)) * 960);
      const vel =       Math.round((e.velocity || 0.8) * 127);
      midiEvents.push({ tick: startTick, type: 0x90 | channel, pitch: e.pitch, vel: vel });
      midiEvents.push({ tick: endTick,   type: 0x80 | channel, pitch: e.pitch, vel: 0 });
    }

    midiEvents.sort((a, b) => a.tick - b.tick);

    for (const me of midiEvents) {
      const delta = Math.max(0, me.tick - lastTick);
      trackData.push(...writeVLQ(delta));
      trackData.push(me.type, me.pitch, me.vel);
      lastTick = me.tick;
    }

    // End of track: delta 0, FF 2F 00
    trackData.push(0x00, 0xFF, 0x2F, 0x00);
    
    // MTrk header
    const dataLen = trackData.length;
    const header = [
      0x4D, 0x54, 0x72, 0x6B, // MTrk
      (dataLen >> 24) & 0xFF,
      (dataLen >> 16) & 0xFF,
      (dataLen >> 8) & 0xFF,
      dataLen & 0xFF
    ];
    
    return header.concat(trackData);
  }

  function exportMidi(accData, title) {
    if (!accData) return;
    
    const leftTrack = createTrack(accData.left_hand || [], 0);  // Ch 1
    const rightTrack = createTrack(accData.right_hand || [], 1); // Ch 2
    
    // Format 1, 2 tracks, 480 ticks per quarter
    const mthd = [
      0x4D, 0x54, 0x68, 0x64, // MThd
      0x00, 0x00, 0x00, 0x06, // Length
      0x00, 0x01,             // Format 1
      0x00, 0x02,             // 2 tracks
      0x01, 0xE0              // 480 ticks
    ];
    
    const midiFile = mthd.concat(leftTrack, rightTrack);
    const u8 = new Uint8Array(midiFile);
    const blob = new Blob([u8], { type: 'audio/midi' });
    const url = URL.createObjectURL(blob);
    
    const a = document.createElement('a');
    a.style.display = 'none';
    a.href = url;
    a.download = (title || 'AI_Accompaniment') + '.mid';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => {
        URL.revokeObjectURL(url);
    }, 1000);
  }

  return { exportMidi };
})();
