/** LiveChord API 呼叫封裝 */

const API = {
  async get(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },

  async post(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },

  async del(url) {
    const res = await fetch(url, { method: "DELETE" });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },

  // 音樂庫
  browse: (path = "") => API.get(`/api/browse?path=${encodeURIComponent(path)}`),
  search: (q) => API.get(`/api/search?q=${encodeURIComponent(q)}`),
  trackInfo: (path) => API.get(`/api/track/info?path=${encodeURIComponent(path)}`),
  trackStreamUrl: (path) => `/api/track/stream?path=${encodeURIComponent(path)}`,
  trackCoverUrl: (path) => `/api/track/cover?path=${encodeURIComponent(path)}`,
  libraryScan: (mode = "incremental") => API.post(`/api/library/scan?mode=${mode}`),
  libraryScanStatus: () => API.get("/api/library/scan/status"),
  libraryStats: () => API.get("/api/library/stats"),

  // 和弦
  chordInfo: (name) => API.get(`/api/chord/info/${encodeURIComponent(name)}`),
  chordDiagram: (inst, name) => API.get(`/api/chord/diagram/${inst}/${encodeURIComponent(name)}`),
  getChords: (path) => API.get(`/api/chords?path=${encodeURIComponent(path)}`),
  saveChords: (data) => API.post("/api/chords", data),
  detectChords: (path) => API.post(`/api/chords/detect?path=${encodeURIComponent(path)}`),
  midiSearch: (path) => API.get(`/api/chords/midi-search?path=${encodeURIComponent(path)}`),
  midiImport: (path, midiPath) => API.post(`/api/chords/midi-import?path=${encodeURIComponent(path)}&midi_path=${encodeURIComponent(midiPath)}`),

  // 使用者
  getFavorites: () => API.get("/api/favorites"),
  addFavorite: (path) => API.post("/api/favorites", { path }),
  removeFavorite: (path) => API.del(`/api/favorites?path=${encodeURIComponent(path)}`),
  getRecent: () => API.get("/api/recent"),
  addRecent: (path) => API.post("/api/recent", { path }),
};
