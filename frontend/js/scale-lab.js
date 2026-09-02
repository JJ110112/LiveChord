// scale-lab.js v1
// Piano + guitar scale reference — shared by the homepage "Scale Lab" section
// (index.html #secScaleLab) and the player key-badge popup (player.html #chordKey).
//
// Exposes window.ScaleLab = {
//   KEYS, CATEGORIES, SCALES, getScale, buildScale, spellScale,
//   drawPianoScale, drawGuitarScale, buildGuitarTab, playScale,
//   renderInto, openModal, scaleIdForKey
// }
//
// Everything here is client-side and self-contained: no API calls, no
// Tone.js (the player page doesn't load Tone) — the preview uses a bare
// AudioContext oscillator so it works on both pages.
//
// References used to curate the catalogue (see 參考資料 footer in index.html):
//   - https://www.pianote.com/blog/piano-scales/
//   - https://audiblegenius.com/blog/exotic-piano-scales
//   - https://www.musicca.com/scale-finder
(function () {
  "use strict";

  // ---- pitch / spelling helpers ----
  const LETTERS = ["C", "D", "E", "F", "G", "A", "B"];
  const LETTER_PC = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
  const NAMES_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const NAMES_FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];
  // Project-wide display spelling (utils.js NOTE_NAMES_DISPLAY): flats except F#.
  const NAMES_DISPLAY = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];
  const SHARP_ROOTS = new Set(["G", "D", "A", "E", "B", "F#", "C#"]);

  // 12 roots in circle-of-fifths order (same order as progression-library.js).
  const KEYS = [
    { name: "C", pc: 0 }, { name: "G", pc: 7 }, { name: "D", pc: 2 }, { name: "A", pc: 9 },
    { name: "E", pc: 4 }, { name: "B", pc: 11 }, { name: "F#", pc: 6 }, { name: "F", pc: 5 },
    { name: "Bb", pc: 10 }, { name: "Eb", pc: 3 }, { name: "Ab", pc: 8 }, { name: "Db", pc: 1 },
  ];

  function mod12(v) { return ((v % 12) + 12) % 12; }

  function noteToPc(n) {
    if (typeof window.noteToSemitone === "function") return window.noteToSemitone(n);
    const m = /^([A-Ga-g])([b#]*)/.exec(String(n || "C"));
    if (!m) return 0;
    let s = LETTER_PC[m[1].toUpperCase()];
    for (const c of m[2]) s += c === "#" ? 1 : -1;
    return mod12(s);
  }

  function pcToDisplay(pc) { return NAMES_DISPLAY[mod12(pc)]; }

  function keyNameForPc(pc) {
    const k = KEYS.find((x) => x.pc === mod12(pc));
    return k ? k.name : pcToDisplay(pc);
  }

  // Degree label for an interval from the root (used in chips + piano keys).
  // Default labels use b5 / b6 (blues, locrian, diminished…); scales that
  // conventionally spell the tritone as #4 (lydian family) or use #5 / #2
  // (whole tone, altered) carry their own `degrees` override below.
  const DEGREE_LABEL = ["1", "b2", "2", "b3", "3", "4", "b5", "5", "b6", "6", "b7", "7"];
  // Letter offset from the root for each interval — used to spell
  // non-heptatonic scales by degree (b3 keeps the 3rd letter, b5 the 5th…).
  const DEGREE_LETTER = [0, 1, 1, 2, 2, 3, 4, 4, 5, 5, 6, 6];
  const ENHARMONIC_ROOT = { Db: "C#", "C#": "Db", Eb: "D#", "D#": "Eb", Gb: "F#", "F#": "Gb", Ab: "G#", "G#": "Ab", Bb: "A#", "A#": "Bb" };
  const WEIRD_NOTES = new Set(["Fb", "Cb", "E#", "B#"]);

  /**
   * Spell a scale from a root name + semitone intervals.
   * - 7-note scales: one letter per degree (D major → D E F# G A B C#). If a
   *   degree would need a double accidental we fall back to the enharmonic
   *   display name for that note (keeps things readable, e.g. altered scales).
   * - other sizes: sharps for sharp-side roots, flats otherwise.
   */
  function spellScale(rootName, intervals, degrees) {
    const rootPc = noteToPc(rootName);
    const preferSharp = SHARP_ROOTS.has(rootName) || (rootName.length > 1 && rootName[1] === "#");
    const fallbackNames = preferSharp ? NAMES_SHARP : NAMES_FLAT;

    // Spell with a given root letter; returns { names, bad } where bad counts
    // double accidentals (already replaced by enharmonics) and Fb/Cb/E#/B#.
    const attempt = (letterRoot, letterOffsets) => {
      const startIdx = LETTERS.indexOf(letterRoot);
      let bad = 0;
      const names = intervals.map((iv, i) => {
        const letter = LETTERS[(startIdx + letterOffsets[i]) % 7];
        const pc = mod12(rootPc + iv);
        let acc = pc - LETTER_PC[letter];
        if (acc > 6) acc -= 12;
        if (acc < -6) acc += 12;
        if (Math.abs(acc) > 1) { bad++; return fallbackNames[pc]; }
        const name = letter + (acc === 1 ? "#" : acc === -1 ? "b" : "");
        if (WEIRD_NOTES.has(name)) bad++;
        return name;
      });
      return { names, bad };
    };

    const rootLetter = /^[A-G]/i.test(rootName) ? rootName[0].toUpperCase() : "C";
    // Letter offsets: explicit degree labels win (altered = 1 b2 #2 3 b5 #5 b7
    // reuses letters), else one letter per degree for 7-note scales, else by
    // interval (b3 keeps the 3rd letter, b5 the 5th …).
    let offsets;
    if (Array.isArray(degrees) && degrees.length === intervals.length) {
      offsets = degrees.map((d) => Math.max(0, (parseInt(String(d).replace(/[^0-9]/g, ""), 10) || 1) - 1));
    } else if (intervals.length === 7) {
      offsets = intervals.map((_, i) => i);
    } else {
      offsets = intervals.map((iv) => DEGREE_LETTER[mod12(iv)]);
    }
    let best = attempt(rootLetter, offsets);
    // Try the enharmonic root (Db → C#) when the spelling gets ugly.
    const alt = ENHARMONIC_ROOT[rootName];
    if (best.bad >= 2 && alt) {
      const altTry = attempt(alt[0], offsets);
      if (altTry.bad < best.bad) best = altTry;
    }
    if (best.bad >= 2) return intervals.map((iv) => NAMES_DISPLAY[mod12(rootPc + iv)]);
    return best.names;
  }

  // ---- scale catalogue ----
  // intervals: semitones above the root (ascending, root = 0).
  // mood: short 音色特性 tags. desc: 介紹. use: 適用曲風 / 常見用法. tip: 練習提示.
  const CATEGORIES = [
    { id: "basic", name: "基礎音階", en: "Essential" },
    { id: "modes", name: "教會調式", en: "Modes" },
    { id: "jazz", name: "爵士 / 藍調", en: "Jazz & Blues" },
    { id: "exotic", name: "異國 / 世界", en: "Exotic & World" },
    { id: "symmetric", name: "對稱音階", en: "Symmetric" },
  ];

  const SCALES = [
    // ---- basic ----
    {
      id: "major", cat: "basic", name: "大調", en: "Major (Ionian)",
      intervals: [0, 2, 4, 5, 7, 9, 11], formula: "全 全 半 全 全 全 半",
      mood: ["明亮", "開朗", "穩定"],
      desc: "所有音階的起點。七個音之間的全音／半音排列（全全半全全全半）給了它明確的中心感，主音 (1) 聽起來像「家」。流行、古典、民謠絕大多數旋律都建立在大調上。",
      use: "流行 / 古典 / 民謠 / 兒歌 / 讚美詩",
      tip: "鋼琴先用 C 大調（全白鍵）建立指法 1-2-3-1-2-3-4-5；吉他練 2 個八度的把位型，注意每條弦上都有 2–3 個音。",
      chords: "I  ii  iii  IV  V  vi  vii°",
    },
    {
      id: "minor", cat: "basic", name: "自然小調", en: "Natural Minor (Aeolian)",
      intervals: [0, 2, 3, 5, 7, 8, 10], formula: "全 半 全 全 半 全 全",
      mood: ["憂鬱", "內斂", "抒情"],
      desc: "與其關係大調共用同一組音，只是把中心移到第六級。降三 (b3)、降六 (b6)、降七 (b7) 讓聲音變得柔和憂傷。A 小調 = C 大調的音，從 A 開始。",
      use: "抒情流行 / 搖滾 / 民謠 / 電影配樂",
      tip: "從關係大調往下數三個半音就是它的主音（C 大調 → A 小調）。彈的時候多停在 1 和 5，感受「回家」的方向感。",
      chords: "i  ii°  III  iv  v  VI  VII",
    },
    {
      id: "harmonic_minor", cat: "basic", name: "和聲小調", en: "Harmonic Minor",
      intervals: [0, 2, 3, 5, 7, 8, 11], formula: "全 半 全 全 半 增二 半",
      mood: ["戲劇", "古典", "東方色彩"],
      desc: "把自然小調的第七級升高半音，製造出強烈回到主音的導音。b6 到 7 之間的增二度是它的招牌聲響，帶點古典與中東的氣質。",
      use: "古典 / 新古典金屬 / 佛朗明哥 / 電影配樂",
      tip: "重點練 b6→7→1 這三個音的跨指。搭配 V7 → i 的和弦進行（例：E7 → Am）最能突顯它的張力。",
      chords: "i  ii°  III+  iv  V  VI  vii°",
    },
    {
      id: "melodic_minor", cat: "basic", name: "旋律小調", en: "Melodic Minor",
      intervals: [0, 2, 3, 5, 7, 9, 11], formula: "全 半 全 全 全 全 半",
      mood: ["流暢", "優雅", "爵士感"],
      desc: "自然小調升高第六和第七級（上行），旋律線更平滑。古典上行用此、下行回到自然小調；爵士則上下行都用同一組音，稱為「爵士小調」。",
      use: "古典 / 爵士即興 / 電影配樂",
      tip: "把它想成「大調但把 3 降半音」最好記。爵士玩家常在 m(maj7) 和弦上使用。",
      chords: "i  ii  III+  IV  V  vi°  vii°",
    },
    {
      id: "major_pentatonic", cat: "basic", name: "大調五聲音階", en: "Major Pentatonic",
      intervals: [0, 2, 4, 7, 9], formula: "1 2 3 5 6",
      mood: ["乾淨", "開闊", "鄉村"],
      desc: "拿掉大調中的 4 和 7（兩個容易產生半音摩擦的音），剩下五個音幾乎怎麼彈都好聽。中國、蘇格蘭、非洲民謠都大量使用。",
      use: "鄉村 / 民謠 / 流行 / 中國風 / 廣告配樂",
      tip: "鋼琴上 Gb 大調五聲音階剛好是五個黑鍵，最適合初學者體驗即興。吉他對應「小調五聲第 2 把位型」。",
      chords: "I  IV  V（搭配大三和弦）",
    },
    {
      id: "minor_pentatonic", cat: "basic", name: "小調五聲音階", en: "Minor Pentatonic",
      intervals: [0, 3, 5, 7, 10], formula: "1 b3 4 5 b7",
      mood: ["搖滾", "粗獷", "直接"],
      desc: "吉他手的第一個即興音階。自然小調拿掉 2 和 b6，剩下的五個音在幾乎所有搖滾、藍調、放克伴奏上都能用，是 solo 的萬用鑰匙。",
      use: "搖滾 / 藍調 / 放克 / 流行 solo",
      tip: "吉他上背熟第 1 把位「盒子型」（根音在第 6 弦），再往上下延伸另外四個把位。",
      chords: "i  iv  v  bVII（小三和弦 / 屬七都合）",
    },
    {
      id: "blues", cat: "jazz", name: "藍調音階", en: "Blues Scale",
      intervals: [0, 3, 5, 6, 7, 10], formula: "1 b3 4 b5 5 b7",
      mood: ["藍調", "沙啞", "張力"],
      desc: "小調五聲音階加上「藍調音」b5，這個經過音在 4 與 5 之間製造微妙的緊繃感。放在屬七和弦上就是最經典的藍調味道。",
      use: "藍調 / 搖滾 / 爵士 / 靈魂樂",
      tip: "b5 通常不停留，當作滑向 4 或 5 的裝飾音。吉他上用推弦（bend）表現最道地。",
      chords: "I7  IV7  V7（12 小節藍調）",
    },
    {
      id: "major_blues", cat: "jazz", name: "大調藍調音階", en: "Major Blues Scale",
      intervals: [0, 2, 3, 4, 7, 9], formula: "1 2 b3 3 5 6",
      mood: ["歡快", "搖擺", "鄉村藍調"],
      desc: "大調五聲音階加上 b3 作為經過音，明亮中帶一點藍調的「壞」。爵士鋼琴、鄉村、Boogie-woogie 大量使用。",
      use: "爵士 / 鄉村 / Boogie-woogie / Gospel",
      tip: "b3 → 3 是它的靈魂，鋼琴上可用滑音（黑鍵滑到白鍵）表現。與小調藍調音階在同一把位交替使用效果很好。",
      chords: "I7  IV7  V7",
    },
    // ---- modes ----
    {
      id: "dorian", cat: "modes", name: "多利安調式", en: "Dorian",
      intervals: [0, 2, 3, 5, 7, 9, 10], formula: "全 半 全 全 全 半 全",
      mood: ["爵士", "放克", "微亮的小調"],
      desc: "自然小調把 b6 升為 6，憂鬱裡帶一絲光亮。Miles Davis〈So What〉、Santana〈Oye Como Va〉都是多利安經典。",
      use: "爵士 / 放克 / 拉丁 / 民謠",
      tip: "用 im7 → IV7 的兩個和弦循環（Dm7 → G7）練即興，強調第 6 級的特色音。",
      chords: "i  ii  bIII  IV  v  vi°  bVII",
    },
    {
      id: "phrygian", cat: "modes", name: "弗里吉亞調式", en: "Phrygian",
      intervals: [0, 1, 3, 5, 7, 8, 10], formula: "半 全 全 全 半 全 全",
      mood: ["黑暗", "西班牙", "神秘"],
      desc: "自然小調把 2 降半音，開頭的半音 (b2) 帶來強烈的緊張與異國感。佛朗明哥、金屬樂常用。",
      use: "佛朗明哥 / 金屬 / 電影配樂",
      tip: "特色音是 b2，多在 1 與 b2 之間來回。和弦上用 i → bII（Em → F）最能表現。",
      chords: "i  bII  bIII  iv  v°  bVI  bvii",
    },
    {
      id: "lydian", cat: "modes", name: "利底亞調式", en: "Lydian",
      intervals: [0, 2, 4, 6, 7, 9, 11], degrees: ["1","2","3","#4","5","6","7"], formula: "全 全 全 半 全 全 半",
      mood: ["夢幻", "飄浮", "電影感"],
      desc: "大調把 4 升為 #4，消除了 4→3 的下拉力，聲音變得漂浮、開闊。《辛普森家庭》主題、John Williams 的配樂都愛用。",
      use: "電影配樂 / 動畫 / 前衛搖滾 / 夢幻流行",
      tip: "在 Imaj7 上停留並反覆強調 #4（C 上的 F#）。搭配 I → II（C → D）兩個大三和弦最有利底亞味。",
      chords: "I  II  iii  #iv°  V  vi  vii",
    },
    {
      id: "mixolydian", cat: "modes", name: "混合利底亞調式", en: "Mixolydian",
      intervals: [0, 2, 4, 5, 7, 9, 10], formula: "全 全 半 全 全 半 全",
      mood: ["搖滾", "藍調", "率性"],
      desc: "大調把 7 降半音，是屬七和弦的「原生」音階。聽起來像大調但沒有導音的拉力，更放鬆、更搖滾。",
      use: "搖滾 / 藍調 / 鄉村 / 凱爾特民謠",
      tip: "在 I7 和弦上（例：G7）即興並強調 b7。I → bVII → IV（G → F → C）是它的招牌進行。",
      chords: "I  ii  iii°  IV  v  vi  bVII",
    },
    {
      id: "locrian", cat: "modes", name: "洛克里安調式", en: "Locrian",
      intervals: [0, 1, 3, 5, 6, 8, 10], degrees: ["1","b2","b3","4","b5","b6","b7"], formula: "半 全 全 半 全 全 全",
      mood: ["不安", "無解", "極暗"],
      desc: "唯一主和弦是減三和弦的調式，b2 與 b5 讓它幾乎沒有「回家」的感覺。單獨使用少見，但在 m7b5 和弦上是標準選擇。",
      use: "爵士（m7b5 和弦）/ 金屬 / 實驗音樂",
      tip: "爵士上遇到 ii–V–i 小調進行的 iiø7（例：Bm7b5）就用它。不必追求穩定感，重點在色彩。",
      chords: "i°  bII  biii  iv  bV  bVI  bvii",
    },
    // ---- jazz ----
    {
      id: "bebop_dominant", cat: "jazz", name: "Bebop 屬音階", en: "Bebop Dominant",
      intervals: [0, 2, 4, 5, 7, 9, 10, 11], formula: "1 2 3 4 5 6 b7 7",
      mood: ["搖擺", "流暢", "爵士線條"],
      desc: "混合利底亞加上經過音 7，讓八個音剛好對齊八分音符——從根音起跑，和弦音自然落在正拍。Charlie Parker 時代的必修課。",
      use: "Bebop / 搖擺爵士 / 屬七和弦即興",
      tip: "用八分音符從 1 上行下行，確認 1、3、5、b7 落在正拍。在 ii–V–I 的 V7 上使用。",
      chords: "V7  V9  V13",
    },
    {
      id: "bebop_major", cat: "jazz", name: "Bebop 大音階", en: "Bebop Major",
      intervals: [0, 2, 4, 5, 7, 8, 9, 11], formula: "1 2 3 4 5 b6 6 7",
      mood: ["搖擺", "溫暖", "古典爵士"],
      desc: "大調加上經過音 b6，同樣讓和弦音落在正拍。Barry Harris 系統的核心，適合在 Imaj7 / I6 上編織長線條。",
      use: "搖擺爵士 / 大調和弦即興",
      tip: "從 5 開始下行（5 b6 6 7 1 ...）最能感受和弦音對齊正拍的效果。",
      chords: "Imaj7  I6  I6/9",
    },
    {
      id: "altered", cat: "jazz", name: "變化音階", en: "Altered (Super Locrian)",
      intervals: [0, 1, 3, 4, 6, 8, 10], degrees: ["1","b2","#2","3","b5","#5","b7"], formula: "1 b2 #2 3 b5 #5 b7",
      mood: ["緊張", "現代爵士", "解決前的張力"],
      desc: "旋律小調的第七個調式，包含屬七和弦所有的變化音（b9 #9 b5 #5）。專門用在要回到主和弦前的 V7alt，張力拉滿再釋放。",
      use: "現代爵士 / 融合 / V7alt 和弦",
      tip: "記法：往上半音的旋律小調（G7alt → Ab 旋律小調）。只在 V7 上用，回到 I 時換回大調。",
      chords: "V7alt  V7#9  V7b13",
    },
    {
      id: "lydian_dominant", cat: "jazz", name: "利底亞屬音階", en: "Lydian Dominant",
      intervals: [0, 2, 4, 6, 7, 9, 10], degrees: ["1","2","3","#4","5","6","b7"], formula: "1 2 3 #4 5 6 b7",
      mood: ["飄浮", "俏皮", "融合爵士"],
      desc: "混合利底亞加上 #4，是旋律小調的第四調式。用在不會解決到 I 的屬七和弦上（例如 bVII7、II7 或三全音代理），〈辛普森〉主題也用它。",
      use: "爵士 / 融合 / 波薩諾瓦 / 動畫配樂",
      tip: "記法：往下四度的旋律小調（C7#11 → G 旋律小調）。特色音 #4 要大方彈出來。",
      chords: "V7#11  bII7（三全音代理）",
    },
    {
      id: "half_whole_dim", cat: "symmetric", name: "半全減音階", en: "Half-Whole Diminished",
      intervals: [0, 1, 3, 4, 6, 7, 9, 10], degrees: ["1","b2","b3","3","#4","5","6","b7"], formula: "半 全 半 全 半 全 半 全",
      mood: ["緊張", "對稱", "爵士屬和弦"],
      desc: "半音與全音交替的八音音階，每隔小三度就重複，所以一種指型可以移動到四個根音。在屬七和弦上提供 b9、#9、#11、13 的色彩。",
      use: "爵士屬七和弦 / 電影驚悚配樂",
      tip: "從屬七和弦根音起「半全」排列。C7 用的音階 = Eb7 = Gb7 = A7 用的音階，一次學四個。",
      chords: "V7b9  V13b9",
    },
    {
      id: "whole_half_dim", cat: "symmetric", name: "全半減音階", en: "Whole-Half Diminished",
      intervals: [0, 2, 3, 5, 6, 8, 9, 11], formula: "全 半 全 半 全 半 全 半",
      mood: ["懸疑", "旋轉", "減和弦"],
      desc: "全音與半音交替的八音音階，是減七和弦的原生音階。對稱結構讓它聽起來像不斷旋轉、沒有明確中心。",
      use: "減七和弦即興 / 古典（李斯特、巴爾托克）/ 配樂",
      tip: "在 dim7 和弦上使用。與半全減音階是同一組音的不同起點——從 dim7 的根音起就是全半。",
      chords: "i°7  vii°7",
    },
    {
      id: "whole_tone", cat: "symmetric", name: "全音音階", en: "Whole Tone",
      intervals: [0, 2, 4, 6, 8, 10], degrees: ["1","2","3","#4","#5","b7"], formula: "全 全 全 全 全 全",
      mood: ["夢境", "模糊", "無重力"],
      desc: "六個音全部相隔全音，沒有半音、沒有完全五度，聽起來像失去重力。德布西的印象派作品、卡通的「夢境轉場」都是它。",
      use: "印象派 / 增和弦 / 夢境轉場 / 爵士 V7#5",
      tip: "全世界只有兩組全音音階（C 起與 Db 起）。在 aug 或 7#5 和弦上使用。",
      chords: "I+  V7#5  V9#5",
    },
    {
      id: "chromatic", cat: "symmetric", name: "半音音階", en: "Chromatic",
      intervals: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11], formula: "全部半音",
      mood: ["流動", "中性", "技巧"],
      desc: "十二個半音全部包含，沒有調性中心。實戰中用來連接和弦音、製造滑行效果，也是最好的手指獨立性練習。",
      use: "經過音 / 技巧練習 / 現代音樂",
      tip: "鋼琴指法：白鍵到黑鍵用 1→3，連續白鍵（E-F、B-C）用 1→2。吉他每弦四指各按一格。",
      chords: "任何和弦（作為經過音）",
    },
    // ---- exotic ----
    {
      id: "harmonic_major", cat: "exotic", name: "和聲大調", en: "Harmonic Major",
      intervals: [0, 2, 4, 5, 7, 8, 11], formula: "全 全 半 全 半 增二 半",
      mood: ["懷舊", "微酸", "電影感"],
      desc: "大調把 6 降半音，明亮裡藏著一點哀愁。b6 到 7 的增二度帶來古典與東歐的色彩，是〈Bohemian Rhapsody〉等作品裡借用小調 iv 和弦的來源。",
      use: "電影配樂 / 前衛搖滾 / 爵士（借用和弦）",
      tip: "在 I → iv → I（C → Fm → C）的進行上使用，b6 是關鍵。",
      chords: "I  ii°  iii  iv  V  bVI+  vii°",
    },
    {
      id: "hungarian_minor", cat: "exotic", name: "匈牙利小調", en: "Hungarian Minor (Gypsy)",
      intervals: [0, 2, 3, 6, 7, 8, 11], degrees: ["1","2","b3","#4","5","b6","7"], formula: "全 半 增二 半 半 增二 半",
      mood: ["吉普賽", "熱情", "戲劇性"],
      desc: "和聲小調把 4 升為 #4，形成兩個增二度，是東歐、吉普賽音樂與李斯特《匈牙利狂想曲》的靈魂。",
      use: "吉普賽爵士 / 東歐民謠 / 古典 / 金屬",
      tip: "兩個增二度（b3→#4、b6→7）分開練，再連成一氣。與和聲小調對照，感受 #4 帶來的轉折。",
      chords: "i  #iv°  V  bVI",
    },
    {
      id: "phrygian_dominant", cat: "exotic", name: "弗里吉亞屬音階", en: "Phrygian Dominant (Spanish)",
      intervals: [0, 1, 4, 5, 7, 8, 10], formula: "半 增二 半 全 半 全 全",
      mood: ["西班牙", "中東", "熱烈"],
      desc: "和聲小調的第五調式，b2 與大三度並存。佛朗明哥、猶太克萊茲默、中東音樂和金屬 riff 的共同語言，也稱「西班牙吉普賽音階」。",
      use: "佛朗明哥 / 中東 / 金屬 / 電影配樂",
      tip: "在屬七和弦上停留（例：E7 → Am 的 E7），強調 b2 → 1 的解決。吉他上搭配拉斯蓋多掃弦。",
      chords: "I  bII  iii°  iv  v°  bVI+  bvii",
    },
    {
      id: "double_harmonic", cat: "exotic", name: "雙和聲大調", en: "Double Harmonic (Byzantine)",
      intervals: [0, 1, 4, 5, 7, 8, 11], formula: "半 增二 半 全 半 增二 半",
      mood: ["阿拉伯", "拜占庭", "神秘"],
      desc: "大調把 2 和 6 都降半音，形成兩個增二度，是最典型的「中東」聲響（Dick Dale〈Misirlou〉）。也叫阿拉伯音階或拜占庭音階。",
      use: "中東 / 衝浪搖滾 / 電影配樂 / 金屬",
      tip: "彈的時候每個增二度都放慢，讓聽者聽到跳躍。搭配 I 與 bII 大三和弦交替。",
      chords: "I  bII  iii  iv  V  bVI+  vii",
    },
    {
      id: "neapolitan_minor", cat: "exotic", name: "拿坡里小調", en: "Neapolitan Minor",
      intervals: [0, 1, 3, 5, 7, 8, 11], formula: "半 全 全 全 半 增二 半",
      mood: ["古典", "陰鬱", "戲劇"],
      desc: "和聲小調把 2 降半音，同時擁有弗里吉亞的 b2 和和聲小調的導音 7。名字來自拿坡里六和弦 (bII)。",
      use: "古典 / 電影配樂 / 前衛金屬",
      tip: "把 bII → V → i（Bb → E7 → Am）的進行當骨架練即興。",
      chords: "i  bII  III+  iv  V  VI  vii°",
    },
    {
      id: "persian", cat: "exotic", name: "波斯音階", en: "Persian",
      intervals: [0, 1, 4, 5, 6, 8, 11], formula: "半 增二 半 半 全 增二 半",
      mood: ["異國", "緊繃", "沙漠"],
      desc: "雙和聲大調把 5 降半音，b5 讓它比拜占庭音階更緊繃、更不穩定。適合營造遙遠與神秘的氣氛。",
      use: "中東風配樂 / 遊戲音樂 / 實驗",
      tip: "以 1 為錨點來回，b2 與 b5 當作裝飾音。不要試圖讓它「穩定」，色彩才是重點。",
      chords: "I  bII  iv°",
    },
    {
      id: "hirajoshi", cat: "exotic", name: "平調子", en: "Hirajoshi (Japanese)",
      intervals: [0, 2, 3, 7, 8], formula: "1 2 b3 5 b6",
      mood: ["日本", "禪意", "空靈"],
      desc: "日本箏的調弦音階之一，五個音裡有兩個半音，簡約又深刻。用在小調上就有立刻的日式氛圍。",
      use: "日本傳統 / 動畫配樂 / 環境音樂",
      tip: "讓音之間留白，模仿箏的餘韻。與小調五聲音階對照：把 4 換成 b3 旁的 2，b7 換成 b6。",
      chords: "i  bVI（開放和弦、留白）",
    },
    {
      id: "in_sen", cat: "exotic", name: "陰旋", en: "In Sen (Japanese)",
      intervals: [0, 1, 5, 7, 10], formula: "1 b2 4 5 b7",
      mood: ["日本", "幽暗", "懸浮"],
      desc: "同樣來自日本箏的調弦，開頭的 b2 讓它比平調子更幽暗。尺八獨奏常聽到這種聲響。",
      use: "日本傳統 / 電影配樂 / 冥想音樂",
      tip: "b2 → 1 的下行是靈魂，用慢速、有呼吸感的方式彈奏。",
      chords: "i  sus4  bII",
    },
    {
      id: "egyptian", cat: "exotic", name: "埃及音階", en: "Egyptian (Suspended Pentatonic)",
      intervals: [0, 2, 5, 7, 10], formula: "1 2 4 5 b7",
      mood: ["古老", "懸浮", "開放"],
      desc: "沒有三度音的五聲音階（大調五聲的第二調式），因此不分大小調，帶著古老、開放的感覺。",
      use: "世界音樂 / 電影配樂 / 環境音樂",
      tip: "在 sus2 / sus4 和弦上使用最能表現「沒有大小調」的特質。",
      chords: "Isus2  Isus4  bVII",
    },
    {
      id: "enigmatic", cat: "exotic", name: "謎之音階", en: "Enigmatic",
      intervals: [0, 1, 4, 6, 8, 10, 11], degrees: ["1","b2","3","#4","#5","#6","7"], formula: "半 增二 全 全 全 半 半",
      mood: ["詭異", "不確定", "實驗"],
      desc: "威爾第為一場作曲挑戰設計的人造音階，前半像弗里吉亞、後半像全音音階，連續的全音接兩個半音讓它難以預測。",
      use: "實驗音樂 / 電影驚悚 / 金屬（Joe Satriani〈The Enigmatic〉）",
      tip: "把它當作色彩實驗：在 I 大三和弦上慢慢上行，聽每個音帶來的驚訝感。",
      chords: "I  bII  V+",
    },
  ];

  function getScale(id) {
    return SCALES.find((s) => s.id === id) || SCALES[0];
  }

  /**
   * Build the concrete scale for a root + scale id.
   * @returns {{ root, rootPc, scale, pcs:number[], names:string[], degrees:string[] }}
   */
  function buildScale(rootName, scaleId) {
    const scale = getScale(scaleId);
    const rootPc = noteToPc(rootName);
    const names = spellScale(rootName, scale.intervals, scale.degrees);
    return {
      root: rootName,
      rootPc,
      scale,
      pcs: scale.intervals.map((iv) => mod12(rootPc + iv)),
      names,
      degrees: Array.isArray(scale.degrees) ? scale.degrees.slice() : scale.intervals.map((iv) => DEGREE_LABEL[mod12(iv)]),
    };
  }

  /** Map a player key string ("Am", "F#", "Bb") + optional mode to a scale id. */
  function scaleIdForKey(key, mode) {
    const m = /^([A-Ga-g][b#]?)(m)?/.exec(String(key || "").trim());
    const isMinor = !!(m && m[2]);
    const modeMap = {
      Major: "major", Ionian: "major", Minor: "minor", Aeolian: "minor",
      Dorian: "dorian", Phrygian: "phrygian", Lydian: "lydian", Mixolydian: "mixolydian",
      Locrian: "locrian", Blues: "blues", "Harmonic Minor": "harmonic_minor", "Melodic Minor": "melodic_minor",
    };
    if (mode && modeMap[mode]) return modeMap[mode];
    return isMinor ? "minor" : "major";
  }

  function rootFromKey(key) {
    const m = /^([A-Ga-g][b#]?)/.exec(String(key || "").trim());
    if (!m) return "C";
    return keyNameForPc(noteToPc(m[1]));
  }

  // ---- theme-aware ink ----
  const LIGHT_BG_THEMES = new Set(["light", "sakura", "sunny", "sky"]);
  function isLightBg() {
    const t = document.documentElement.getAttribute("data-theme") || "";
    return LIGHT_BG_THEMES.has(t);
  }
  function cssVar(name, fallback) {
    try {
      const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v || fallback;
    } catch { return fallback; }
  }
  function accentColor() { return cssVar("--accent", "#2196F3"); }
  const TONE_COLOR = "#26c6da"; // scale tone (non-root)

  function setupCanvas(canvas, cssW, cssH) {
    const dpr = Math.max(1, Math.min(3, window.devicePixelRatio || 1));
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    canvas.style.width = cssW + "px";
    canvas.style.height = cssH + "px";
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    return ctx;
  }

  // ---- piano renderer ----
  /**
   * Draw a 2-octave keyboard (C..B, C..B) with scale tones highlighted.
   * @param {HTMLCanvasElement} canvas
   * @param {object} built - result of buildScale()
   * @param {object} opts - { width?: number, octaves?: number, labelMode?: "name"|"degree" }
   */
  function drawPianoScale(canvas, built, opts = {}) {
    if (!canvas || !built) return;
    const octaves = opts.octaves || 2;
    const cssW = Math.max(240, Math.floor(opts.width || canvas.clientWidth || canvas.parentElement?.clientWidth || 560));
    const whiteCount = 7 * octaves;
    const whiteW = cssW / whiteCount;
    const whiteH = Math.min(150, Math.max(90, whiteW * 4.4));
    const blackW = whiteW * 0.6;
    const blackH = whiteH * 0.62;
    const cssH = Math.round(whiteH + 4);
    const ctx = setupCanvas(canvas, cssW, cssH);

    const light = isLightBg();
    const accent = accentColor();
    const inScale = new Map(); // pc -> index in scale
    built.pcs.forEach((pc, i) => inScale.set(pc, i));
    const labelMode = opts.labelMode || "name";
    const labelFor = (pc) => {
      const idx = inScale.get(pc);
      if (idx == null) return "";
      return labelMode === "degree" ? built.degrees[idx] : built.names[idx];
    };

    const WHITE_PCS = [0, 2, 4, 5, 7, 9, 11];
    const BLACK_AFTER = { 0: 1, 2: 3, 5: 6, 7: 8, 9: 10 }; // white pc -> black pc to its right

    // white keys
    for (let w = 0; w < whiteCount; w++) {
      const pc = WHITE_PCS[w % 7];
      const x = w * whiteW;
      const on = inScale.has(pc);
      const isRoot = pc === built.rootPc;
      ctx.fillStyle = on ? (isRoot ? accent : TONE_COLOR) : (light ? "#fbfbf7" : "#f2f2ee");
      ctx.fillRect(x + 0.5, 0.5, whiteW - 1, whiteH);
      ctx.strokeStyle = light ? "#8a8a80" : "#3a3f48";
      ctx.lineWidth = 1;
      ctx.strokeRect(x + 0.5, 0.5, whiteW - 1, whiteH);
      if (on) {
        ctx.fillStyle = "#fff";
        ctx.font = `${isRoot ? 700 : 600} ${Math.round(Math.min(15, whiteW * 0.42))}px system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "alphabetic";
        ctx.fillText(labelFor(pc), x + whiteW / 2, whiteH - 10);
        if (labelMode === "name") {
          ctx.font = `500 ${Math.round(Math.min(11, whiteW * 0.3))}px system-ui, sans-serif`;
          ctx.fillStyle = "rgba(255,255,255,0.85)";
          ctx.fillText(built.degrees[inScale.get(pc)], x + whiteW / 2, whiteH - 26);
        }
      } else if (pc === 0) {
        // octave anchor label like the 88-key view: C inside the key
        ctx.fillStyle = light ? "#666" : "#8a8f99";
        ctx.font = `500 ${Math.round(Math.min(11, whiteW * 0.3))}px system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.fillText("C", x + whiteW / 2, whiteH - 10);
      }
    }
    // black keys
    for (let w = 0; w < whiteCount; w++) {
      const pcWhite = WHITE_PCS[w % 7];
      const pcBlack = BLACK_AFTER[pcWhite];
      if (pcBlack == null) continue;
      const x = (w + 1) * whiteW - blackW / 2;
      const on = inScale.has(pcBlack);
      const isRoot = pcBlack === built.rootPc;
      ctx.fillStyle = on ? (isRoot ? accent : TONE_COLOR) : (light ? "#2b2b2b" : "#111");
      ctx.fillRect(x, 0.5, blackW, blackH);
      ctx.strokeStyle = light ? "#555" : "#000";
      ctx.strokeRect(x + 0.5, 0.5, blackW - 1, blackH);
      if (on) {
        ctx.fillStyle = "#fff";
        ctx.font = `${isRoot ? 700 : 600} ${Math.round(Math.min(12, blackW * 0.5))}px system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.fillText(labelFor(pcBlack), x + blackW / 2, blackH - 8);
      }
    }
  }

  // ---- guitar renderer ----
  const GUITAR_OPEN = [40, 45, 50, 55, 59, 64]; // E2 A2 D3 G3 B3 E4 (low → high)
  const GUITAR_LABELS = ["E", "A", "D", "G", "B", "e"];
  const FRET_MARKERS = [3, 5, 7, 9, 12];

  /**
   * Draw a horizontal 12-fret fretboard, high e on top (tab orientation),
   * with every scale tone shown as a dot (root = accent).
   * @param {object} opts - { width?: number, frets?: number, labelMode?: "name"|"degree" }
   */
  function drawGuitarScale(canvas, built, opts = {}) {
    if (!canvas || !built) return;
    const numFrets = opts.frets || 12;
    // Narrow hosts (phone portrait) get a 520px board that scrolls inside
    // .sl-stage (overflow-x:auto) instead of squeezing 13 frets into 300px.
    const cssW = Math.max(520, Math.floor(opts.width || canvas.clientWidth || canvas.parentElement?.clientWidth || 640));
    const leftPad = 62;   // room for an open-string dot (x≈16) + string label (right edge ≈ leftPad-8)
    const rightPad = 12;
    const topPad = 22;
    const stringGap = Math.max(20, Math.min(30, (cssW - leftPad) / 24));
    const fretW = (cssW - leftPad - rightPad) / numFrets;
    const cssH = Math.round(topPad + stringGap * 5 + 30);
    const ctx = setupCanvas(canvas, cssW, cssH);

    const light = isLightBg();
    const accent = accentColor();
    const ink = light ? "#2a2a2a" : "#d7dbe3";
    const dim = light ? "#8a8a80" : "#5c6270";
    const inScale = new Map();
    built.pcs.forEach((pc, i) => inScale.set(pc, i));
    const labelMode = opts.labelMode || "name";

    // fretboard background
    ctx.fillStyle = light ? "rgba(0,0,0,0.04)" : "rgba(255,255,255,0.04)";
    ctx.fillRect(leftPad, topPad - 4, cssW - leftPad - rightPad, stringGap * 5 + 8);

    // frets
    for (let f = 0; f <= numFrets; f++) {
      const x = leftPad + f * fretW;
      ctx.strokeStyle = f === 0 ? ink : dim;
      ctx.lineWidth = f === 0 ? 4 : 1.2;
      ctx.beginPath();
      ctx.moveTo(x, topPad - 4);
      ctx.lineTo(x, topPad + stringGap * 5 + 4);
      ctx.stroke();
    }
    // fret markers + numbers
    ctx.font = "500 11px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "alphabetic";
    for (let f = 1; f <= numFrets; f++) {
      const cx = leftPad + (f - 0.5) * fretW;
      if (FRET_MARKERS.includes(f)) {
        ctx.fillStyle = light ? "rgba(0,0,0,0.12)" : "rgba(255,255,255,0.14)";
        if (f === 12) {
          ctx.beginPath(); ctx.arc(cx, topPad + stringGap * 1.5, 4, 0, Math.PI * 2); ctx.fill();
          ctx.beginPath(); ctx.arc(cx, topPad + stringGap * 3.5, 4, 0, Math.PI * 2); ctx.fill();
        } else {
          ctx.beginPath(); ctx.arc(cx, topPad + stringGap * 2.5, 4, 0, Math.PI * 2); ctx.fill();
        }
      }
      ctx.fillStyle = dim;
      ctx.fillText(String(f), cx, cssH - 8);
    }
    // strings (high e on top → row 0 = string index 5)
    for (let row = 0; row < 6; row++) {
      const sIdx = 5 - row;
      const y = topPad + row * stringGap;
      ctx.strokeStyle = ink;
      ctx.lineWidth = 0.8 + (5 - sIdx) * 0.35;
      ctx.beginPath();
      ctx.moveTo(leftPad, y);
      ctx.lineTo(cssW - rightPad, y);
      ctx.stroke();
      ctx.fillStyle = dim;
      ctx.font = "600 11px system-ui, sans-serif";
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      ctx.fillText(GUITAR_LABELS[sIdx], leftPad - 8, y);
    }
    // dots
    const r = Math.max(7, Math.min(11, fretW * 0.36));
    for (let row = 0; row < 6; row++) {
      const sIdx = 5 - row;
      const y = topPad + row * stringGap;
      for (let f = 0; f <= numFrets; f++) {
        const pc = mod12(GUITAR_OPEN[sIdx] + f);
        const idx = inScale.get(pc);
        if (idx == null) continue;
        const isRoot = pc === built.rootPc;
        // open-string tones: hollow dot at the far left, before the string label
        const cx = f === 0 ? 16 : leftPad + (f - 0.5) * fretW;
        ctx.beginPath();
        ctx.arc(cx, y, r, 0, Math.PI * 2);
        if (f === 0) {
          ctx.fillStyle = isRoot ? accent : (light ? "#ffffff" : "#1c2028");
          ctx.fill();
          ctx.lineWidth = 2;
          ctx.strokeStyle = isRoot ? accent : TONE_COLOR;
          ctx.stroke();
        } else {
          ctx.fillStyle = isRoot ? accent : TONE_COLOR;
          ctx.fill();
        }
        const label = labelMode === "degree" ? built.degrees[idx] : built.names[idx];
        ctx.fillStyle = f === 0 && !isRoot ? (light ? "#1a1a1a" : "#e6e9ef") : "#fff";
        ctx.font = `${isRoot ? 700 : 600} ${Math.round(r * 1.05)}px system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(label, cx, y + 0.5);
      }
    }
  }

  /**
   * Build a one-position ascending tab (2 octaves where the window allows).
   * Root sits on the 6th string; the window spans 5 frets around it.
   * @returns {{ text: string, positions: Array<{string:number, fret:number, midi:number}> }}
   */
  function buildGuitarTab(built) {
    const rootFret6 = mod12(built.rootPc - mod12(GUITAR_OPEN[0]));
    // Prefer a low position: root on 6th string at fret 0..11; if the root is
    // at fret 0 (E), start the window at 0. Otherwise window = [rootFret-1, rootFret+3].
    const lo = Math.max(0, rootFret6 - 1);
    const hi = lo + 4;
    const inScale = new Set(built.pcs);
    const rootMidi = GUITAR_OPEN[0] + rootFret6;
    const positions = [];
    let lastMidi = -1;
    for (let s = 0; s < 6; s++) {
      for (let f = lo; f <= hi; f++) {
        const midi = GUITAR_OPEN[s] + f;
        if (midi < rootMidi) continue;
        if (!inScale.has(mod12(midi))) continue;
        if (midi <= lastMidi) continue;
        positions.push({ string: s, fret: f, midi });
        lastMidi = midi;
      }
    }
    // Stop after reaching the root two octaves up if present.
    const twoOct = rootMidi + 24;
    const cut = positions.findIndex((p) => p.midi > twoOct);
    const seq = cut >= 0 ? positions.slice(0, cut) : positions;

    const cols = seq.map((p) => String(p.fret));
    const colW = Math.max(2, ...cols.map((c) => c.length)) + 1;
    const lines = [];
    for (let row = 0; row < 6; row++) {
      const sIdx = 5 - row;
      let line = GUITAR_LABELS[sIdx].padEnd(2, " ") + "|";
      seq.forEach((p) => {
        const cell = p.string === sIdx ? String(p.fret) : "";
        line += cell.padStart(colW, "-").padEnd(colW + 1, "-");
      });
      lines.push(line + "|");
    }
    return { text: lines.join("\n"), positions: seq };
  }

  // ---- audio preview (bare WebAudio, no Tone dependency) ----
  let _ac = null;
  let _playToken = 0;
  function _ctx() {
    if (!_ac) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      _ac = new AC();
    }
    if (_ac.state === "suspended") { try { _ac.resume(); } catch {} }
    return _ac;
  }

  /**
   * Play the scale ascending then descending from the root (octave 4).
   * @param {object} built - buildScale() result
   * @param {object} opts - { bpm?: number, onStep?: (pcIndex|null) => void }
   * @returns {() => void} stop function
   */
  function playScale(built, opts = {}) {
    const ac = _ctx();
    if (!ac || !built) return () => {};
    const token = ++_playToken;
    const bpm = Math.max(60, Math.min(240, opts.bpm || 150));
    const step = 60 / bpm;
    const rootMidi = 60 + mod12(built.rootPc);
    const ups = built.pcs.map((pc, i) => ({ midi: rootMidi + built.scale.intervals[i], idx: i }));
    ups.push({ midi: rootMidi + 12, idx: 0 });
    const downs = ups.slice(0, -1).reverse();
    const seq = ups.concat(downs);
    const master = ac.createGain();
    master.gain.value = 0.25;
    master.connect(ac.destination);
    const t0 = ac.currentTime + 0.05;
    const timers = [];
    seq.forEach((n, i) => {
      const t = t0 + i * step;
      const osc = ac.createOscillator();
      osc.type = "triangle";
      osc.frequency.value = 440 * Math.pow(2, (n.midi - 69) / 12);
      const g = ac.createGain();
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(1, t + 0.012);
      g.gain.exponentialRampToValueAtTime(0.35, t + step * 0.6);
      g.gain.exponentialRampToValueAtTime(0.0001, t + step * 0.95);
      osc.connect(g).connect(master);
      osc.start(t);
      osc.stop(t + step);
      if (typeof opts.onStep === "function") {
        timers.push(setTimeout(() => { if (token === _playToken) opts.onStep(n.idx); }, Math.max(0, (t - ac.currentTime) * 1000)));
      }
    });
    const endMs = (t0 - ac.currentTime + seq.length * step) * 1000;
    timers.push(setTimeout(() => {
      if (token === _playToken && typeof opts.onStep === "function") opts.onStep(null);
      try { master.disconnect(); } catch {}
    }, endMs + 50));
    return () => {
      if (token !== _playToken) return;
      _playToken++;
      timers.forEach(clearTimeout);
      try { master.gain.setTargetAtTime(0.0001, ac.currentTime, 0.02); } catch {}
      setTimeout(() => { try { master.disconnect(); } catch {} }, 120);
      if (typeof opts.onStep === "function") opts.onStep(null);
    };
  }

  // ---- shared view (used by homepage section + player modal) ----
  const UI = {
    "zh-TW": {
      key: "調 Key", category: "類別", scale: "音階", instrument: "樂器", piano: "鋼琴", guitar: "吉他",
      play: "試聽", stop: "停止", label_name: "音名", label_degree: "級數", notes: "組成音", formula: "音程公式",
      mood: "音色特性", use: "適用曲風", tip: "練習提示", chords: "常見和弦", tab: "TAB 把位（根音在第 6 弦）",
      relative: "關係調", title: "音階", close: "關閉", colon: "：",
    },
    en: {
      key: "Key", category: "Category", scale: "Scale", instrument: "Instrument", piano: "Piano", guitar: "Guitar",
      play: "Play", stop: "Stop", label_name: "Names", label_degree: "Degrees", notes: "Notes", formula: "Formula",
      mood: "Character", use: "Genres", tip: "Practice tip", chords: "Common chords", tab: "TAB position (root on 6th string)",
      relative: "Relative", title: "Scale", close: "Close", colon: ": ",
    },
  };
  function L(k) {
    const lang = (window.LiveChordI18n && typeof window.LiveChordI18n.getLang === "function") ? window.LiveChordI18n.getLang() : "zh-TW";
    const d = UI[lang] || UI["zh-TW"];
    return d[k] || UI["zh-TW"][k] || k;
  }
  function isEn() {
    return (window.LiveChordI18n && typeof window.LiveChordI18n.getLang === "function") ? window.LiveChordI18n.getLang() === "en" : false;
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function relativeKeyLine(built) {
    const s = built.scale;
    const c = L("colon");
    if (s.id === "major") return `${L("relative")}${c}${keyNameForPc(built.rootPc + 9)}m`;
    if (s.id === "minor") return `${L("relative")}${c}${keyNameForPc(built.rootPc + 3)}`;
    if (s.id === "major_pentatonic") return `${L("relative")}${c}${keyNameForPc(built.rootPc + 9)} ${isEn() ? "minor pentatonic" : "小調五聲"}`;
    if (s.id === "minor_pentatonic") return `${L("relative")}${c}${keyNameForPc(built.rootPc + 3)} ${isEn() ? "major pentatonic" : "大調五聲"}`;
    return "";
  }

  /**
   * Render a complete scale explorer into a container element.
   * @param {HTMLElement} host
   * @param {object} opts - { root, scaleId, instrument, compact, persistKey, onChange }
   * @returns {{ setState(patch), getState(), destroy() }}
   */
  function renderInto(host, opts = {}) {
    const state = {
      root: KEYS.some((k) => k.name === opts.root) ? opts.root : "C",
      scaleId: getScale(opts.scaleId || "major").id,
      instrument: opts.instrument === "guitar" ? "guitar" : "piano",
      labelMode: opts.labelMode === "degree" ? "degree" : "name",
      cat: null,
      compact: !!opts.compact,
      persistKey: opts.persistKey || null,
    };
    state.cat = getScale(state.scaleId).cat;
    if (state.persistKey) {
      try {
        const saved = JSON.parse(localStorage.getItem(state.persistKey) || "null");
        if (saved && typeof saved === "object") {
          if (!opts.root && KEYS.some((k) => k.name === saved.root)) state.root = saved.root;
          if (!opts.scaleId && SCALES.some((s) => s.id === saved.scaleId)) { state.scaleId = saved.scaleId; state.cat = getScale(saved.scaleId).cat; }
          if (saved.instrument === "guitar" || saved.instrument === "piano") state.instrument = saved.instrument;
          if (saved.labelMode === "degree" || saved.labelMode === "name") state.labelMode = saved.labelMode;
        }
      } catch {}
    }

    let stopFn = null;
    let activeIdx = null;
    host.classList.add("sl-host");
    host.innerHTML = `
      <div class="sl-controls">
        <label class="sl-field"><span>${L("key")}</span><select class="sl-key"></select></label>
        <label class="sl-field"><span>${L("category")}</span><select class="sl-cat"></select></label>
        <label class="sl-field sl-field-scale"><span>${L("scale")}</span><select class="sl-scale"></select></label>
        <div class="sl-field"><span>${L("instrument")}</span>
          <div class="sl-seg" role="group">
            <button type="button" class="sl-seg-btn" data-inst="piano">🎹 ${L("piano")}</button>
            <button type="button" class="sl-seg-btn" data-inst="guitar">🎸 ${L("guitar")}</button>
          </div>
        </div>
        <div class="sl-field"><span>${L("label_name")} / ${L("label_degree")}</span>
          <div class="sl-seg" role="group">
            <button type="button" class="sl-seg-btn" data-label="name">${L("label_name")}</button>
            <button type="button" class="sl-seg-btn" data-label="degree">${L("label_degree")}</button>
          </div>
        </div>
        <div class="sl-field sl-field-play"><span>&nbsp;</span>
          <button type="button" class="sl-play proglib-btn proglib-btn-accent">▶ ${L("play")}</button>
        </div>
      </div>
      <div class="sl-keychips" aria-label="12 keys"></div>
      <div class="sl-headline">
        <div class="sl-name"></div>
        <div class="sl-sub"></div>
      </div>
      <div class="sl-notes"></div>
      <div class="sl-stage">
        <canvas class="sl-canvas"></canvas>
        <pre class="sl-tab" hidden></pre>
      </div>
      <div class="sl-info">
        <div class="sl-mood"></div>
        <p class="sl-desc"></p>
        <div class="sl-meta"></div>
      </div>
    `;

    const q = (sel) => host.querySelector(sel);
    const refs = {
      key: q(".sl-key"), cat: q(".sl-cat"), scale: q(".sl-scale"),
      instBtns: host.querySelectorAll("[data-inst]"), labelBtns: host.querySelectorAll("[data-label]"),
      play: q(".sl-play"), chips: q(".sl-keychips"), name: q(".sl-name"), sub: q(".sl-sub"),
      notes: q(".sl-notes"), canvas: q(".sl-canvas"), tab: q(".sl-tab"),
      mood: q(".sl-mood"), desc: q(".sl-desc"), meta: q(".sl-meta"),
    };

    refs.key.innerHTML = KEYS.map((k) => `<option value="${k.name}">${k.name}</option>`).join("");
    refs.cat.innerHTML = CATEGORIES.map((c) => `<option value="${c.id}">${isEn() ? c.en : c.name}</option>`).join("");
    refs.chips.innerHTML = KEYS.map((k) => `<button type="button" class="sl-chip" data-root="${k.name}">${k.name}</button>`).join("");

    function fillScales() {
      const list = SCALES.filter((s) => s.cat === state.cat);
      refs.scale.innerHTML = list.map((s) => `<option value="${s.id}">${isEn() ? s.en : `${s.name} · ${s.en}`}</option>`).join("");
      if (!list.some((s) => s.id === state.scaleId)) state.scaleId = list[0].id;
      refs.scale.value = state.scaleId;
    }

    function persist() {
      if (!state.persistKey) return;
      try {
        localStorage.setItem(state.persistKey, JSON.stringify({
          root: state.root, scaleId: state.scaleId, instrument: state.instrument, labelMode: state.labelMode,
        }));
      } catch {}
    }

    function drawStage() {
      const built = buildScale(state.root, state.scaleId);
      const w = Math.floor(host.clientWidth || 600) - 2;
      if (state.instrument === "piano") {
        refs.tab.hidden = true;
        drawPianoScale(refs.canvas, built, { width: w, labelMode: state.labelMode, activeIdx });
      } else {
        drawGuitarScale(refs.canvas, built, { width: w, labelMode: state.labelMode, activeIdx });
        refs.tab.hidden = false;
        refs.tab.textContent = `${L("tab")}\n${buildGuitarTab(built).text}`;
      }
    }

    function render() {
      const built = buildScale(state.root, state.scaleId);
      const s = built.scale;
      refs.key.value = state.root;
      refs.cat.value = state.cat;
      fillScales();
      refs.instBtns.forEach((b) => b.classList.toggle("is-active", b.dataset.inst === state.instrument));
      refs.labelBtns.forEach((b) => b.classList.toggle("is-active", b.dataset.label === state.labelMode));
      refs.chips.querySelectorAll(".sl-chip").forEach((c) => c.classList.toggle("is-active", c.dataset.root === state.root));

      refs.name.innerHTML = `<b>${escapeHtml(state.root)} ${escapeHtml(isEn() ? s.en : s.name)}</b><span class="sl-en">${escapeHtml(isEn() ? s.name : s.en)}</span>`;
      const rel = relativeKeyLine(built);
      refs.sub.textContent = `${L("formula")}${L("colon")}${s.formula}${rel ? `　·　${rel}` : ""}`;
      refs.notes.innerHTML = built.names.map((n, i) => `
        <span class="sl-note ${i === 0 ? "is-root" : ""} ${activeIdx === i ? "is-playing" : ""}" data-idx="${i}">
          <b>${escapeHtml(n)}</b><small>${escapeHtml(built.degrees[i])}</small>
        </span>`).join("");
      refs.mood.innerHTML = (s.mood || []).map((m) => `<span class="sl-mood-tag">${escapeHtml(m)}</span>`).join("");
      refs.desc.textContent = s.desc || "";
      refs.meta.innerHTML = `
        <div><b>${L("use")}</b>${escapeHtml(s.use || "")}</div>
        <div><b>${L("chords")}</b>${escapeHtml(s.chords || "")}</div>
        <div><b>${L("tip")}</b>${escapeHtml(s.tip || "")}</div>`;
      drawStage();
      persist();
      if (typeof opts.onChange === "function") opts.onChange({ ...state });
    }

    function stopPlay() {
      if (stopFn) { const f = stopFn; stopFn = null; f(); }
      activeIdx = null;
      refs.play.textContent = `▶ ${L("play")}`;
      refs.play.classList.remove("is-active");
      refs.notes.querySelectorAll(".sl-note").forEach((n) => n.classList.remove("is-playing"));
    }

    function togglePlay() {
      if (stopFn) { stopPlay(); return; }
      const built = buildScale(state.root, state.scaleId);
      refs.play.textContent = `■ ${L("stop")}`;
      refs.play.classList.add("is-active");
      stopFn = playScale(built, {
        onStep: (idx) => {
          activeIdx = idx;
          refs.notes.querySelectorAll(".sl-note").forEach((n) => n.classList.toggle("is-playing", Number(n.dataset.idx) === idx));
          if (idx == null) { stopFn = null; stopPlay(); }
        },
      });
    }

    refs.key.addEventListener("change", () => { state.root = refs.key.value; render(); });
    refs.cat.addEventListener("change", () => { state.cat = refs.cat.value; state.scaleId = null; render(); });
    refs.scale.addEventListener("change", () => { state.scaleId = refs.scale.value; render(); });
    refs.instBtns.forEach((b) => b.addEventListener("click", () => { state.instrument = b.dataset.inst; render(); }));
    refs.labelBtns.forEach((b) => b.addEventListener("click", () => { state.labelMode = b.dataset.label; render(); }));
    refs.chips.addEventListener("click", (e) => {
      const chip = e.target.closest(".sl-chip");
      if (!chip) return;
      state.root = chip.dataset.root;
      render();
    });
    refs.play.addEventListener("click", togglePlay);

    const onResize = () => drawStage();
    window.addEventListener("resize", onResize);
    const themeObs = new MutationObserver(() => drawStage());
    try { themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] }); } catch {}

    render();

    return {
      setState(patch) {
        if (!patch) return;
        if (patch.root && KEYS.some((k) => k.name === patch.root)) state.root = patch.root;
        if (patch.scaleId && SCALES.some((s) => s.id === patch.scaleId)) { state.scaleId = patch.scaleId; state.cat = getScale(patch.scaleId).cat; }
        if (patch.instrument === "guitar" || patch.instrument === "piano") state.instrument = patch.instrument;
        render();
      },
      getState() { return { ...state }; },
      redraw: drawStage,
      destroy() {
        stopPlay();
        window.removeEventListener("resize", onResize);
        try { themeObs.disconnect(); } catch {}
        host.innerHTML = "";
      },
    };
  }

  // ---- player modal (UX_CONVENTION §12 Type B: .lc-modal-backdrop + .lc-modal) ----
  let _modal = null;
  function closeModal() {
    if (!_modal) return;
    const m = _modal; _modal = null;
    try { m.view.destroy(); } catch {}
    document.removeEventListener("keydown", m.onKey);
    m.backdrop.remove();
  }

  /**
   * Open the scale popup for a key (player). Remembers the last instrument.
   * @param {object} opts - { key: "Am" | "F#", mode?: "Dorian", instrument?: "piano"|"guitar" }
   */
  function openModal(opts = {}) {
    closeModal();
    const root = rootFromKey(opts.key || "C");
    const scaleId = opts.scaleId || scaleIdForKey(opts.key, opts.mode);
    const backdrop = document.createElement("div");
    backdrop.className = "scale-modal lc-modal-backdrop";
    const card = document.createElement("div");
    card.className = "lc-modal";
    card.innerHTML = `
      <button class="lc-close" aria-label="${L("close")}">&times;</button>
      <div class="lc-title">🎼 ${L("title")}${L("colon")}${escapeHtml(root)} ${escapeHtml(isEn() ? getScale(scaleId).en : getScale(scaleId).name)}</div>
      <div class="sl-modal-body"></div>`;
    backdrop.appendChild(card);
    document.body.appendChild(backdrop);
    const body = card.querySelector(".sl-modal-body");
    const view = renderInto(body, {
      root, scaleId, instrument: opts.instrument, compact: true, persistKey: "livechord_scalelab_player",
      onChange: (st) => {
        const s = getScale(st.scaleId);
        card.querySelector(".lc-title").textContent = `🎼 ${L("title")}${L("colon")}${st.root} ${isEn() ? s.en : s.name}`;
      },
    });
    // the persisted key/scale from a previous popup must not override the song's key
    view.setState({ root, scaleId });
    const onKey = (e) => { if (e.key === "Escape") closeModal(); };
    document.addEventListener("keydown", onKey);
    backdrop.addEventListener("click", (e) => { if (e.target === backdrop) closeModal(); });
    card.querySelector(".lc-close").addEventListener("click", closeModal);
    _modal = { backdrop, view, onKey };
    // canvas width depends on the card's final layout
    requestAnimationFrame(() => { if (_modal) _modal.view.redraw(); });
    return view;
  }

  // ---- homepage section bootstrap ----
  function initHomeSection() {
    const ROOT = document.getElementById("secScaleLab");
    if (!ROOT) return;
    const body = document.getElementById("scalelabBody");
    const host = document.getElementById("scalelabHost");
    const collapseBtn = document.getElementById("scalelabCollapseBtn");
    const SETTINGS_KEY = "livechord_scalelab_settings";
    let collapsed = false;
    try {
      const s = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "null");
      if (s && typeof s.collapsed === "boolean") collapsed = s.collapsed;
    } catch {}
    let view = null;
    function syncCollapse() {
      ROOT.classList.toggle("is-collapsed", collapsed);
      if (body) body.setAttribute("aria-hidden", collapsed ? "true" : "false");
      if (collapseBtn) {
        collapseBtn.classList.toggle("is-active", collapsed);
        collapseBtn.textContent = collapsed ? "展開" : "收折";
        collapseBtn.setAttribute("aria-expanded", collapsed ? "false" : "true");
      }
      if (!collapsed && !view && host) view = renderInto(host, { persistKey: "livechord_scalelab" });
      else if (!collapsed && view) view.redraw();
      try { localStorage.setItem(SETTINGS_KEY, JSON.stringify({ collapsed })); } catch {}
    }
    if (collapseBtn) collapseBtn.addEventListener("click", () => { collapsed = !collapsed; syncCollapse(); });
    syncCollapse();
  }

  window.ScaleLab = {
    KEYS, CATEGORIES, SCALES, getScale, buildScale, spellScale, scaleIdForKey, rootFromKey,
    drawPianoScale, drawGuitarScale, buildGuitarTab, playScale, renderInto, openModal, closeModal,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initHomeSection);
  else initHomeSection();
})();
