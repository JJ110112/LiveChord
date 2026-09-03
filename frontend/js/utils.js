/**
 * LiveChord 共用工具函式
 * 所有頁面共享的 helper — 在各頁面 IIFE 之前載入
 */

// ---- Search marquee placeholder ----
// Beta-only search scope is "user's own uploads + YT analyses" (NAS library
// results are skipped in beta non-admin to avoid album-vs-MV duration
// mismatch). The marquee copy shifts to match:
//   • Fresh beta user (no history yet) → "請輸入 YouTube URL..."
//   • User has at least one analyzed song → "請輸入歌曲、專輯、藝人或YouTube URL..."
//   • Personal / admin → same long copy (they still get full library search)
function _applySearchMarqueeText(text) {
  document.querySelectorAll("#searchInput").forEach((el) => { el.placeholder = text; });
  document.querySelectorAll(".search-marquee-text").forEach((el) => { el.textContent = text; });
}

// Localized strings come from common.search_placeholder* (long/short
// variants × generic/public). Public mode (livechord.org) uses the
// upload-focused copy — Plan B 2026-05-04 disables YT extraction there
// so the placeholder must not promise it. English fallbacks below match
// the EN-default deployment; they only show if i18n.js is delayed.
function _marqueeStrings(isPublic) {
  const t = (window.LiveChordI18n && window.LiveChordI18n.t) || null;
  const SHORT_FB        = "Paste a YouTube URL...";
  const LONG_FB         = "Search a song...";
  const SHORT_FB_PUB    = "Search a song...";
  const LONG_FB_PUB     = "Search a song...";
  const shortKey = isPublic ? "common.search_placeholder_short_public" : "common.search_placeholder_short";
  const longKey  = isPublic ? "common.search_placeholder_public"        : "common.search_placeholder";
  const shortFb  = isPublic ? SHORT_FB_PUB : SHORT_FB;
  const longFb   = isPublic ? LONG_FB_PUB  : LONG_FB;
  const short = t ? t(shortKey) : shortFb;
  const long  = t ? t(longKey)  : longFb;
  // t() returns the key itself for missing strings — fall back to English then.
  return {
    short: short === shortKey ? shortFb : short,
    long:  long  === longKey  ? longFb  : long,
  };
}

async function updateSearchMarqueeText() {
  // Public mode (livechord.org) gets upload-focused copy — YT extraction
  // is disabled there so we can't promise YouTube URLs work. Detect via
  // /api/config/public; cache the promise so we don't re-fetch per call.
  if (!window._lcIsPublicModePromise) {
    window._lcIsPublicModePromise = fetch("/api/config/public")
      .then(r => r.json())
      .then(cfg => cfg.deployment_mode === "public")
      .catch(() => false);
  }
  const isPublic = await window._lcIsPublicModePromise;
  const { short, long } = _marqueeStrings(isPublic);
  const isBeta = location.port === "8801" || location.hostname.endsWith("livechord.org");

  // Personal/admin: flip to long text immediately (no async fetch needed),
  // avoids flashing the short beta copy.
  if (!isBeta) {
    _applySearchMarqueeText(long);
    return;
  }
  // Beta: short is the default (HTML already has it) — only upgrade to long
  // when the user has at least one analyzed song in their history.
  _applySearchMarqueeText(short);
  try {
    const r = await fetch("/api/process/my-history?limit=1");
    if (!r.ok) return;
    const d = await r.json();
    if (Array.isArray(d.history) && d.history.length > 0) {
      _applySearchMarqueeText(long);
    }
  } catch {}
}

// Auto-run once DOM is ready — both homepage and player-topbar search boxes
// pick up the right copy without each page having to opt in.
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", updateSearchMarqueeText);
} else {
  updateSearchMarqueeText();
}
// Re-render on language switch and once the dictionary first loads, so the
// marquee tracks the picker without a page reload.
document.addEventListener("livechord:langchange", updateSearchMarqueeText);
document.addEventListener("livechord:i18nready",  updateSearchMarqueeText);

// ---- DOM helpers ----

function showToast(msg, ms = 2000) {
  const t = document.getElementById("toast");
  if (!t) return;
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), ms);
}

function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str || "";
  return d.innerHTML;
}

// ---- 時間格式 ----

// Notes pasted from AI answers carry LaTeX-ish markup and light Markdown;
// make them readable for display (stored text is untouched).
//   $\text{I} \to \text{ii}$ → I → ii · \flat → ♭ · "### 標題" → bold line ·
//   "* item" / "- item" → bullet · **bold** · blank line → paragraph gap
function formatNoteHtml(raw) {
  let t = String(raw || "")
    .replace(/\$\$([^$]+)\$\$/g, "$1")   // display math first
    .replace(/\$([^$]+)\$/g, "$1")       // inline math; "$a$$b$" is two spans, not display math
    .replace(/\\text\{([^}]*)\}/g, "$1")
    .replace(/\\mathrm\{([^}]*)\}/g, "$1")
    .replace(/\\(?:to|rightarrow|longrightarrow)\b/g, "→")
    .replace(/\\flat/g, "♭").replace(/\\sharp/g, "♯")
    .replace(/\\(?:circ|deg)\b/g, "°")
    .replace(/\^\{([^}]*)\}/g, "$1").replace(/_\{([^}]*)\}/g, "$1")
    .replace(/\\,|\\;|\\ /g, " ")
    .replace(/\\([A-Za-z]+)/g, "$1");
  const inline = (line) => escapeHtml(line)
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  const out = [];
  let prevBlank = false;
  t.split(/\r?\n/).forEach((line) => {
    const s = line.trim();
    if (!s) { prevBlank = true; return; }
    let html;
    let m;
    if ((m = s.match(/^#{1,6}\s+(.*)$/))) html = `<strong class="pl-note-h">${inline(m[1])}</strong>`;
    else if ((m = s.match(/^(\d+[.、]\s*[^：:（(]{0,30}(?:[（(][^）)]{0,30}[）)])?)(.*)$/)) && m[1].length <= 45) {
      // "1. A 主歌（前導與順暢下行）CMaj7 → …": bold the numbered title, keep the rest inline.
      const rest = m[2].replace(/^[：:]\s*/, "");
      html = `<strong class="pl-note-h">${inline(m[1].trim())}</strong>${rest ? "<br>" + inline(rest) : ""}`;
    }
    else if ((m = s.match(/^[*\-•]\s+(.*)$/))) html = `<span class="pl-note-li">• ${inline(m[1])}</span>`;
    else html = inline(s);
    if (prevBlank && out.length) out.push(`<span class="pl-note-gap"></span>`);
    out.push(html);
    prevBlank = false;
  });
  return out.join("<br>");
}

function formatTime(sec, mode) {
  if (sec == null) return "";
  if (mode === "centi") {
    // m:ss.cc — chord-card label precision so users can cite exact chord
    // boundaries when reporting issues. Card labels render once per
    // build, not 100x/sec, so the extra precision is cheap.
    let totalCs = Math.round(sec * 100);
    if (totalCs < 0) totalCs = 0;
    let cs = totalCs % 100;
    let totalS = Math.floor(totalCs / 100);
    let s = totalS % 60;
    let m = Math.floor(totalS / 60);
    return `${m}:${s.toString().padStart(2, "0")}.${cs.toString().padStart(2, "0")}`;
  }
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// ---- 移調工具函式 ----

const NOTE_NAMES_SHARP = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];
const NOTE_NAMES_FLAT  = ["C","Db","D","Eb","E","F","Gb","G","Ab","A","Bb","B"];

// 固定顯示拼法：全用降記號，唯獨 F# 用升記號 —— C Db D Eb E F F# G Ab A Bb B。
// 所有面向使用者的和弦／音名「文字」都應透過此表拼寫（樂譜五線譜除外，由調號決定）。
const NOTE_NAMES_DISPLAY = ["C","Db","D","Eb","E","F","F#","G","Ab","A","Bb","B"];

function semitoneToDisplay(s) {
  s = ((s%12)+12)%12;
  return NOTE_NAMES_DISPLAY[s];
}

function noteToSemitone(n) {
  const m = {C:0,D:2,E:4,F:5,G:7,A:9,B:11};
  let s = m[n[0].toUpperCase()] || 0;
  for (let i = 1; i < n.length; i++) {
    if (n[i] === "#") s++; else if (n[i] === "b") s--;
  }
  return ((s%12)+12)%12;
}

function semitoneToNote(s, flat) {
  s = ((s%12)+12)%12;
  return flat ? NOTE_NAMES_FLAT[s] : NOTE_NAMES_SHARP[s];
}

function normalizeNoteForDisplay(note, preferFlat = true) {
  if (typeof note !== "string") return note || "";
  // 不要先 toUpperCase：那會把降記號 "b" 變成 "B"，使所有 flat 拼法（尤其 Gb）
  // 比對失敗而原封不動 → Gb 無法轉成 F#。改用大小寫不敏感的字母 + 原樣記號比對。
  const m = note.match(/^([A-Ga-g][b#]?)$/);
  if (!m) return note;
  // 固定顯示拼法（NOTE_NAMES_DISPLAY）；preferFlat 保留作向後相容，不再影響 F#。
  const normalized = semitoneToDisplay(noteToSemitone(m[1]));
  return note === note.toLowerCase() ? normalized.toLowerCase() : normalized;
}

function normalizeKeyForDisplay(key) {
  return normalizeChordNameForDisplay(key);
}

function normalizeChordNameForDisplay(chord) {
  if (!chord || typeof chord !== "string") return chord || "";
  const m = chord.match(/^([A-G][b#]?)(.*?)(?:\/([A-G][b#]?))?$/i);
  if (!m) return chord;

  const root = normalizeNoteForDisplay(m[1], true);
  const suffix = m[2] || "";
  const slash = m[3] ? `/${normalizeNoteForDisplay(m[3], true)}` : "";
  return `${root}${suffix}${slash}`;
}

function transposeChord(chord, semi) {
  if (!chord || semi === 0) return chord;
  const m = chord.match(/^([A-G][b#]?)(.*?)(?:\/([A-G][b#]?))?$/);
  if (!m) return chord;
  // 移調結果採固定顯示拼法（見 NOTE_NAMES_DISPLAY），不再依來源升降記號。
  let r = semitoneToDisplay(noteToSemitone(m[1])+semi) + (m[2]||"");
  if (m[3]) r += "/" + semitoneToDisplay(noteToSemitone(m[3])+semi);
  return r;
}

// ---- 簡譜 ----

const JIANPU_NAMES = ["1","#1","2","#2","3","4","#4","5","#5","6","#6","7"];
const JIANPU_FLAT  = ["1","b2","2","b3","3","4","b5","5","b6","6","b7","7"];

function chordToJianpu(chord, key) {
  const m = chord.match(/^([A-G][b#]?)/);
  if (!m) return "";
  const notes = chord.match(/^([A-G][b#]?)(.*?)(?:\/([A-G][b#]?))?$/);
  if (!notes) return "";
  const root = notes[1];
  const keySemi = noteToSemitone(key || "C");
  const useFlat = root.includes("b") || (key && key.includes("b"));
  const interval = ((noteToSemitone(root) - keySemi) % 12 + 12) % 12;
  return useFlat ? JIANPU_FLAT[interval] : JIANPU_NAMES[interval];
}

// ---- IndexedDB 音檔暫存 (跨頁傳遞) ----

const _AUDIO_DB_NAME = "LiveChordAudio";
const _AUDIO_DB_STORE = "blobs";

function _openAudioDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(_AUDIO_DB_NAME, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(_AUDIO_DB_STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function audioDBStore(hash, blob) {
  try {
    const db = await _openAudioDB();
    const tx = db.transaction(_AUDIO_DB_STORE, "readwrite");
    tx.objectStore(_AUDIO_DB_STORE).put({ blob, storedAt: Date.now() }, hash);
    await new Promise((res, rej) => { tx.oncomplete = res; tx.onerror = rej; });
    db.close();
  } catch (e) {
    console.warn("audioDBStore failed:", e);
  }
}

async function audioDBLoad(hash) {
  try {
    const db = await _openAudioDB();
    const tx = db.transaction(_AUDIO_DB_STORE, "readonly");
    const req = tx.objectStore(_AUDIO_DB_STORE).get(hash);
    const result = await new Promise((res, rej) => { req.onsuccess = () => res(req.result); req.onerror = rej; });
    db.close();
    return result ? result.blob : null;
  } catch (e) {
    console.warn("audioDBLoad failed:", e);
    return null;
  }
}

async function audioDBDelete(hash) {
  try {
    const db = await _openAudioDB();
    const tx = db.transaction(_AUDIO_DB_STORE, "readwrite");
    tx.objectStore(_AUDIO_DB_STORE).delete(hash);
    await new Promise((res, rej) => { tx.oncomplete = res; tx.onerror = rej; });
    db.close();
  } catch (e) {}
}

function extractYouTubeId(url) {
  if (!url) return null;
  const m = url.match(/(?:v=|youtu\.be\/|\/shorts\/)([A-Za-z0-9_-]{11})/);
  return m ? m[1] : null;
}

