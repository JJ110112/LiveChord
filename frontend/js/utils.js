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

function formatTime(sec) {
  if (sec == null) return "";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// ---- 移調工具函式 ----

const NOTE_NAMES_SHARP = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];
const NOTE_NAMES_FLAT  = ["C","Db","D","Eb","E","F","Gb","G","Ab","A","Bb","B"];

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
  const m = note.toUpperCase().match(/^([A-G][b#]?)$/);
  if (!m) return note;
  const normalized = semitoneToNote(noteToSemitone(m[0]), preferFlat);
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
  const flat = m[1].includes("b") || chord.includes("b");
  let r = semitoneToNote(noteToSemitone(m[1])+semi, flat) + (m[2]||"");
  if (m[3]) r += "/" + semitoneToNote(noteToSemitone(m[3])+semi, flat);
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

