/* Spoken chord callouts (player Tools → 唸和弦).
 *
 * Practice without looking at the screen: the player says each chord a
 * little before it starts, either by name ("降B 小七，低音 F") or by degree
 * in the key ("六級小"), so the ear learns the progression.
 *
 *  - spokenText(chord, opts) — pure, no DOM; turns a display chord name into
 *    words a text-to-speech voice reads correctly. A raw "Bbm7/F" handed to
 *    a voice comes out as letter soup.
 *  - createSpeaker() — thin wrapper over the browser's speechSynthesis:
 *    picks a voice for the language, keeps only the newest callout (a late
 *    callout is worse than none) and unlocks iOS on the first tap.
 *
 * Degrees count from the same 1 the 首調 jianpu uses (minor keys use the
 * relative major's 1, so Am in A minor is 六級小), so the spoken numbers
 * match the numbers on the cards.
 */
(function (root) {
  "use strict";

  const PC = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };

  function pitchClass(note) {
    const m = /^([A-G])([#b]?)/.exec(note || "");
    if (!m) return null;
    return (PC[m[1]] + (m[2] === "#" ? 1 : m[2] === "b" ? -1 : 0) + 12) % 12;
  }

  // Tonic pitch class the degrees count from: the key's root, or the
  // relative major's root for a minor key (matches player.js _jianpuTonic).
  function tonicOf(key) {
    const m = /^([A-G][b#]?)(m(?!aj))?/.exec(key || "C");
    if (!m) return 0;
    const r = pitchClass(m[1]);
    return m[2] ? (r + 3) % 12 : r;
  }

  // Chord qualities, longest spelling first so "maj7" wins over "m".
  // zh = 中文精簡 (short, for fast changes), zhFull = 中文對照 (one word per
  // symbol, matches the chart), en = English; *Abbr = the short form used on
  // chords too quick for the full words (快和弦縮寫). Triads say 小三 / 減三 / 增三
  // because a bare 小 is too short to catch over the music.
  const QUALITIES = [
    ["m7b5", { zh: "半減七", zhFull: "小七降五", en: "half diminished", enFull: "minor seven flat five", enAbbr: "half dim" }],
    ["maj13", { zh: "大十三", en: "major thirteen" }],
    ["maj9", { zh: "大九", en: "major nine" }],
    ["maj7", { zh: "大七", en: "major seven" }],
    ["mMaj7", { zh: "小大七", en: "minor major seven" }],
    ["dim7", { zh: "減七", en: "diminished seven", enAbbr: "dim seven" }],
    ["hdim7", { zh: "半減七", zhFull: "小七降五", en: "half diminished", enFull: "minor seven flat five", enAbbr: "half dim" }],
    ["7sus4", { zh: "七掛四", zhFull: "屬七掛四", en: "seven sus four" }],
    ["sus4", { zh: "掛四", en: "sus four" }],
    ["sus2", { zh: "掛二", en: "sus two" }],
    ["add9", { zh: "加九", en: "add nine" }],
    ["m13", { zh: "小十三", en: "minor thirteen" }],
    ["m11", { zh: "小十一", en: "minor eleven" }],
    ["m9", { zh: "小九", en: "minor nine" }],
    ["m7", { zh: "小七", en: "minor seven" }],
    ["m6", { zh: "小六", en: "minor six" }],
    ["M7", { zh: "大七", en: "major seven" }],
    ["dim", { zh: "減三", en: "diminished", enAbbr: "dim" }],
    ["aug", { zh: "增三", en: "augmented", enAbbr: "aug" }],
    ["13", { zh: "十三", zhFull: "屬十三", en: "thirteen", enFull: "dominant thirteen" }],
    ["11", { zh: "十一", zhFull: "屬十一", en: "eleven", enFull: "dominant eleven" }],
    ["9", { zh: "九", zhFull: "屬九", en: "nine", enFull: "dominant nine" }],
    ["7", { zh: "七", zhFull: "屬七", en: "seven", enFull: "dominant seven" }],
    ["6", { zh: "六", en: "six" }],
    ["5", { zh: "五度", en: "five" }],
    ["m", { zh: "小三", en: "minor" }],
    ["°", { zh: "減三", en: "diminished", enAbbr: "dim" }],
    ["ø", { zh: "半減七", zhFull: "小七降五", en: "half diminished", enFull: "minor seven flat five", enAbbr: "half dim" }],
    ["+", { zh: "增三", en: "augmented", enAbbr: "aug" }],
  ];

  const ZH_NUM = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
    "十一", "十二", "十三"];
  const EN_NUM = ["", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "eleven", "twelve", "thirteen"];

  // Leftover tensions after the quality ("b9", "#11", "add13").
  function tensionWords(rest, lang) {
    const out = [];
    const re = /(add|b|#)?(\d{1,2})/g;
    let m;
    while ((m = re.exec(rest))) {
      const n = parseInt(m[2], 10);
      if (!(n > 0 && n < ZH_NUM.length)) continue;
      const pre = m[1] || "";
      if (lang === "zh") out.push((pre === "b" ? "降" : pre === "#" ? "升" : pre === "add" ? "加" : "") + ZH_NUM[n]);
      else out.push((pre === "b" ? "flat " : pre === "#" ? "sharp " : pre === "add" ? "add " : "") + EN_NUM[n]);
    }
    return out.join(lang === "zh" ? "" : " ");
  }

  function qualityWords(suffix, lang, style) {
    let s = suffix || "";
    let words = "";
    const alt = style === "full" ? "Full" : style === "abbr" ? "Abbr" : null;
    for (const [k, v] of QUALITIES) {
      if (s.startsWith(k)) { words = (alt && v[lang + alt]) || v[lang]; s = s.slice(k.length); break; }
    }
    const t = tensionWords(s.replace(/[()]/g, ""), lang);
    return [words, t].filter(Boolean).join(lang === "zh" ? "" : " ");
  }

  function noteWords(note, lang) {
    const m = /^([A-G])([#b]?)/.exec(note || "");
    if (!m) return "";
    if (lang === "zh") return (m[2] === "#" ? "升 " : m[2] === "b" ? "降 " : "") + m[1];
    return m[1] + (m[2] === "#" ? " sharp" : m[2] === "b" ? " flat" : "");
  }

  // Scale degree of a pitch class from the tonic, spelt the way the jianpu
  // cards spell it (b3, #4, b6, b7).
  const DEG = [[1, ""], [1, "#"], [2, ""], [3, "b"], [3, ""], [4, ""], [4, "#"],
    [5, ""], [6, "b"], [6, ""], [7, "b"], [7, ""]];
  function degreeWords(pc, tonic, lang) {
    const [n, acc] = DEG[((pc - tonic) % 12 + 12) % 12];
    if (lang === "zh") return (acc === "#" ? "升" : acc === "b" ? "降" : "") + ZH_NUM[n];
    return (acc === "#" ? "sharp " : acc === "b" ? "flat " : "") + EN_NUM[n];
  }

  /**
   * @param {string} chord display chord name, e.g. "Bbm7/F"
   * @param {{lang?: "zh"|"en", style?: "short"|"full"|"abbr", mode?: "name"|"degree", key?: string}} opts
   * @returns {string} words to speak; "" for no-chord / unparseable
   */
  function spokenText(chord, opts) {
    const o = opts || {};
    const lang = o.lang === "en" ? "en" : "zh";
    const m = /^([A-G][b#]?)(.*?)(?:\/([A-G][b#]?))?$/.exec((chord || "").trim());
    if (!m) return "";
    const q = qualityWords(m[2], lang, o.style);
    let head;
    if (o.mode === "degree") {
      const tonic = tonicOf(o.key);
      const deg = degreeWords(pitchClass(m[1]), tonic, lang);
      head = lang === "zh" ? `${deg}級${q}` : [deg, q].filter(Boolean).join(" ");
    } else {
      head = [noteWords(m[1], lang), q].filter(Boolean).join(" ");
    }
    if (!m[3]) return head;
    const bass = o.mode === "degree"
      ? degreeWords(pitchClass(m[3]), tonicOf(o.key), lang)
      : noteWords(m[3], lang);
    return lang === "zh" ? `${head}，低音${bass}` : `${head} over ${bass}`;
  }

  // Rough spoken length at rate 1.0: ~0.18 s per English syllable, ~0.22 s
  // per Chinese character, ~0.25 s per letter name inside Chinese speech.
  // Only used to decide whether a callout fits; it need not be exact.
  function estimateSeconds(text, lang) {
    if (!text) return 0;
    if (lang === "en") {
      const syl = text.split(/\s+/).filter(Boolean)
        .reduce((n, w) => n + Math.max(1, (w.match(/[aeiouy]+/gi) || []).length), 0);
      return 0.1 + syl * 0.18;
    }
    const cjk = (text.match(/[一-鿿]/g) || []).length;
    const latin = (text.match(/[A-Za-z]/g) || []).length;
    return 0.1 + cjk * 0.22 + latin * 0.25;
  }

  /**
   * What to say for one chord, how fast, and how early.
   * @param {object} o
   *   chord, lang, style ("short"|"full"), mode, key — as spokenText
   *   durSec      wall-clock seconds until the chord after it starts
   *   prevDurSec  wall-clock length of the chord before (caps the lead)
   *   baseRate    user's rate (with auto on: the slowest it may go)
   *   baseLeadSec user's 提前唸 (used when auto is off)
   *   auto        依曲速自動: fit rate + lead to the chord length
   *   shorten     快和弦縮寫: abbreviate when the full words do not fit
   * @returns {{text: string, rate: number, leadSec: number}}
   */
  function planCallout(o) {
    const opts = { lang: o.lang, mode: o.mode, key: o.key };
    const fit = 0.75 * Math.max(0.3, o.durSec || 0);   // leave room before the next callout
    let text = spokenText(o.chord, { ...opts, style: o.style });
    if (o.shorten && estimateSeconds(text, o.lang) / o.baseRate > fit) {
      text = spokenText(o.chord, { ...opts, style: "abbr" });
    }
    let rate = o.baseRate;
    let leadSec = o.baseLeadSec;
    if (o.auto) {
      rate = Math.min(1.6, Math.max(o.baseRate, estimateSeconds(text, o.lang) / fit));
      // Start earlier for longer callouts so the root word lands near the
      // change, but never more than half of the chord still sounding.
      leadSec = Math.min(1.0, Math.max(0.2, 0.15 + 0.35 * estimateSeconds(text, o.lang) / rate));
      if (o.prevDurSec > 0) leadSec = Math.min(leadSec, Math.max(0.15, 0.5 * o.prevDurSec));
    }
    return { text, rate, leadSec };
  }

  function createSpeaker() {
    const synth = typeof window !== "undefined" ? window.speechSynthesis : null;
    let unlocked = false;
    function pickVoice(lang) {
      const voices = synth.getVoices() || [];
      const want = lang === "en" ? ["en-US", "en"] : ["zh-TW", "zh-HK", "zh"];
      for (const w of want) {
        const v = voices.find((x) => (x.lang || "").replace("_", "-").startsWith(w));
        if (v) return v;
      }
      return null;
    }
    return {
      supported: !!synth,
      // iOS only lets speech start inside a tap: speak a silent utterance
      // from the first user gesture so later timed callouts are allowed.
      unlock() {
        if (!synth || unlocked) return;
        unlocked = true;
        try {
          const u = new SpeechSynthesisUtterance(" ");
          u.volume = 0;
          synth.speak(u);
        } catch (_) {}
      },
      // hooks.volume = voice level 0–1 (player's 音樂 ↔ 語音 balance);
      // hooks.onstart / onend are optional (onend also fires on cut-off).
      speak(text, lang, rate, hooks) {
        if (!synth || !text) return;
        const h = hooks || {};
        try {
          synth.cancel();        // only the newest callout matters
          if (synth.paused) synth.resume();
          const u = new SpeechSynthesisUtterance(text);
          const v = pickVoice(lang);
          if (v) u.voice = v;
          u.lang = v ? v.lang : (lang === "en" ? "en-US" : "zh-TW");
          u.rate = rate || 1.0;
          u.volume = h.volume != null ? Math.max(0, Math.min(1, h.volume)) : 1;
          if (h.onstart) u.onstart = h.onstart;
          if (h.onend) { u.onend = h.onend; u.onerror = h.onend; }
          synth.speak(u);
        } catch (_) { if (h.onend) h.onend(); }
      },
      cancel() { try { if (synth) synth.cancel(); } catch (_) {} },
    };
  }

  const api = { spokenText, planCallout, estimateSeconds, createSpeaker, tonicOf };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ChordSpeech = api;
})(typeof self !== "undefined" ? self : this);
