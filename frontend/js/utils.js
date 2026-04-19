/**
 * LiveChord 共用工具函式
 * 所有頁面共享的 helper — 在各頁面 IIFE 之前載入
 */

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

