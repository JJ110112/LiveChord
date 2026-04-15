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
  getChords: (path, version = null) => {
    let url = `/api/chords?path=${encodeURIComponent(path)}`;
    if (version) url += `&version=${encodeURIComponent(version)}`;
    return API.get(url);
  },
  getChordVersions: (path) => API.get(`/api/chords/versions?path=${encodeURIComponent(path)}`),
  saveChords: (data) => API.post("/api/chords", data),
  detectChords: (path) => API.post(`/api/chords/detect?path=${encodeURIComponent(path)}`),
  midiSearch: (path) => API.get(`/api/chords/midi-search?path=${encodeURIComponent(path)}`),
  midiImport: (path, midiPath) => API.post(`/api/chords/midi-import?path=${encodeURIComponent(path)}&midi_path=${encodeURIComponent(midiPath)}`),
  midiUpload: async (path, file) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`/api/chords/midi-upload?path=${encodeURIComponent(path)}`, { method: "POST", body: form });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },
  chordTracks: (page = 1, limit = 100, query = "", status = "all") => API.get(`/api/chords/tracks?page=${page}&limit=${limit}&query=${encodeURIComponent(query)}&status=${status}`),
  batchMidiImport: () => API.post("/api/chords/batch-midi-import"),
  batchDetect: (groupId = "") => API.post(`/api/chords/batch-detect${groupId ? `?group_id=${encodeURIComponent(groupId)}` : ""}`),
  batchDetectStatus: () => API.get("/api/chords/batch-detect/status"),
  libraryGroups: () => API.get("/api/library/groups"),
  tasksStatus: () => API.get("/api/tasks/status"),
  chordVoicings: (inst, name) => API.get(`/api/chord/voicings/${inst}/${encodeURIComponent(name)}`),
  chordAnalysis: (key, name) => API.get(`/api/chord/analysis/${encodeURIComponent(key)}/${encodeURIComponent(name)}`),
  jazzify: (chords, key, level, mode = "rule-based") => API.post("/api/ai/jazzify", { chords, key, level, mode }),

  // Jam Tracks
  jamTracksStyles: () => API.get("/api/jam_tracks/styles"),
  jamTracksList: (style, limit = 100, offset = 0) =>
    API.get(`/api/jam_tracks?style=${encodeURIComponent(style)}&limit=${limit}&offset=${offset}`),

  // 使用者
  getFavorites: () => API.get("/api/favorites"),
  addFavorite: (path) => API.post("/api/favorites", { path }),
  removeFavorite: (path) => API.del(`/api/favorites?path=${encodeURIComponent(path)}`),
  getRecent: () => API.get("/api/recent"),
  addRecent: (path) => API.post("/api/recent", { path }),
};
