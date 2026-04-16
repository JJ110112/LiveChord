/**
 * 琶音/刷弦 Pattern 資料庫 — 供所有弦樂器共用
 * Extracted from player.js for reuse across instruments.
 */

// zone: "bass"=lowest active string, "bass_alt"=2nd lowest, "1"/"2"/"3"=treble strings
// finger: "p"=thumb, "i"=index, "m"=middle, "a"=ring, "ima"=simultaneous pluck
// null step = rest
window.ARPEGGIO_PATTERNS = {
  pima: {
    name: "p-i-m-a", category: "classical", subdiv: 1,
    steps: [
      { finger: "p", zone: "bass" },
      { finger: "i", zone: "3" },
      { finger: "m", zone: "2" },
      { finger: "a", zone: "1" },
    ],
    description: "基本上行琶音",
  },
  pimami: {
    name: "p-i-m-a-m-i", category: "classical", subdiv: 2,
    steps: [
      { finger: "p", zone: "bass" },
      { finger: "i", zone: "3" },
      { finger: "m", zone: "2" },
      { finger: "a", zone: "1" },
      { finger: "m", zone: "2" },
      { finger: "i", zone: "3" },
    ],
    description: "古典滾奏（Romance）",
  },
  pami: {
    name: "p-a-m-i", category: "classical", subdiv: 1,
    steps: [
      { finger: "p", zone: "bass" },
      { finger: "a", zone: "1" },
      { finger: "m", zone: "2" },
      { finger: "i", zone: "3" },
    ],
    description: "下行琶音",
  },
  pima_chord: {
    name: "p + ima", category: "classical", subdiv: 1,
    steps: [
      { finger: "p", zone: "bass" },
      { finger: "ima", zone: "321" },
      null,
      { finger: "ima", zone: "321" },
    ],
    description: "低音+和弦撥（Bossa Nova）",
  },
  travis_basic: {
    name: "Travis Basic", category: "travis", subdiv: 2,
    steps: [
      { finger: "p", zone: "bass" },
      null,
      { finger: "p", zone: "bass_alt" },
      { finger: "m", zone: "2" },
      { finger: "p", zone: "bass" },
      { finger: "i", zone: "3" },
      { finger: "p", zone: "bass_alt" },
      { finger: "m", zone: "2" },
    ],
    description: "交替 Bass（Merle Travis）",
  },
  travis_pinch: {
    name: "Travis Pinch", category: "travis", subdiv: 2,
    steps: [
      { finger: "p", zone: "bass" },
      { finger: "i", zone: "3" },
      { finger: "p", zone: "bass_alt" },
      { finger: "m", zone: "2" },
      { finger: "p", zone: "bass" },
      { finger: "i", zone: "3" },
      { finger: "p", zone: "bass_alt" },
      { finger: "a", zone: "1" },
    ],
    description: "交替 Bass + Fill",
  },
  folk_44: {
    name: "Folk 4/4", category: "folk", subdiv: 2,
    steps: [
      { finger: "p", zone: "bass" },
      { finger: "i", zone: "3" },
      { finger: "m", zone: "2" },
      { finger: "a", zone: "1" },
      { finger: "p", zone: "bass" },
      { finger: "a", zone: "1" },
      { finger: "m", zone: "2" },
      { finger: "i", zone: "3" },
    ],
    description: "民謠指彈（Dust in the Wind）",
  },
  pop_ballad: {
    name: "Pop Ballad", category: "pop", subdiv: 2,
    steps: [
      { finger: "p", zone: "bass" },
      { finger: "i", zone: "3" },
      { finger: "m", zone: "2" },
      { finger: "i", zone: "3" },
      { finger: "a", zone: "1" },
      { finger: "i", zone: "3" },
      { finger: "m", zone: "2" },
      { finger: "i", zone: "3" },
    ],
    description: "流行抒情",
  },
};

// Right-hand finger colors — aligned with left-hand: i=①red, m=②orange, a=③yellow
window.FINGER_COLORS = {
  p: "#00bcd4", // cyan — thumb (左手無對應，獨立色)
  i: "#ef5350", // red — index (= left ① 食指)
  m: "#ff9800", // orange — middle (= left ② 中指)
  a: "#ffeb3b", // yellow — ring (= left ③ 無名指)
};

// Resolve arpeggio zone to actual string index(es)
window.resolveArpZone = function (zone, diagram, numStrings) {
  const active = [];
  if (diagram && diagram.strings) {
    diagram.strings.forEach((f, s) => { if (f >= 0) active.push(s); });
  } else {
    for (let s = 0; s < numStrings; s++) active.push(s);
  }
  switch (zone) {
    case "bass":      return active.length > 0 ? active[0] : 0;
    case "bass_alt":  return active.length > 1 ? active[1] : active[0] || 0;
    case "1":         return numStrings - 1;
    case "2":         return numStrings - 2;
    case "3":         return numStrings - 3;
    case "321":       return [numStrings - 3, numStrings - 2, numStrings - 1];
    default:          return 0;
  }
};
