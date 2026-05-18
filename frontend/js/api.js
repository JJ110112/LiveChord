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
    if (!res.ok) {
      let detail = "";
      try {
        const data = await res.json();
        detail = data && data.detail ? String(data.detail) : "";
      } catch {}
      const status = `${res.status} ${res.statusText}`;
      throw new Error(detail ? `${status}: ${detail}` : status);
    }
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
  rateChordVersion: (path, version, score) => API.post("/api/chords/rate", { path, version, score }),
  saveChords: (data) => API.post("/api/chords", data),
  detectChords: (path) => API.post(`/api/chords/detect?path=${encodeURIComponent(path)}`),
  chordTracks: (page = 1, limit = 100, query = "", status = "all") => API.get(`/api/chords/tracks?page=${page}&limit=${limit}&query=${encodeURIComponent(query)}&status=${status}`),
  batchDetect: (groupId = "") => API.post(`/api/chords/batch-detect${groupId ? `?group_id=${encodeURIComponent(groupId)}` : ""}`),
  batchDetectStatus: () => API.get("/api/chords/batch-detect/status"),
  libraryGroups: () => API.get("/api/library/groups"),
  tasksStatus: () => API.get("/api/tasks/status"),
  chordVoicings: (inst, name) => API.get(`/api/chord/voicings/${inst}/${encodeURIComponent(name)}`),
  chordAnalysis: (key, name) => API.get(`/api/chord/analysis/${encodeURIComponent(key)}/${encodeURIComponent(name)}`),
  jazzify: (chords, key, level, mode = "rule-based", bpm = null) => API.post("/api/ai/jazzify", { chords, key, level, mode, bpm }),

  // Jam Tracks
  jamTracksStyles: () => API.get("/api/jam_tracks/styles"),
  jamTracksList: (style, limit = 100, offset = 0) =>
    API.get(`/api/jam_tracks?style=${encodeURIComponent(style)}&limit=${limit}&offset=${offset}`),

  // 回饋 (Beta)
  submitRating: (song_hash, rating, comment = "", song_title = "") =>
    API.post("/api/feedback/rating", { song_hash, song_title, rating, comment }),
  getMyRating: (song_hash) => API.get(`/api/feedback/rating?song_hash=${encodeURIComponent(song_hash)}`),
  getRatingSummary: (song_hash) => API.get(`/api/feedback/ratings/summary?song_hash=${encodeURIComponent(song_hash)}`),
  submitBug: (category, description, page_url = "", browser_info = "") =>
    API.post("/api/feedback/bug", { category, description, page_url, browser_info }),
  trackEvent: (event_type, payload = {}) =>
    API.post("/api/analytics/event", { event_type, payload }).catch(() => {}),

  // 新歌處理 (Beta Phase 2)
  processUpload: async (file) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/api/process/upload", { method: "POST", body: form });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },
  processStatus: (jobId) => API.get(`/api/process/status/${jobId}`),
  processResult: (jobId) => API.get(`/api/process/result/${jobId}`),

  // 使用者
  getFavorites: () => API.get("/api/favorites"),
  addFavorite: (path) => API.post("/api/favorites", { path }),
  removeFavorite: (path) => API.del(`/api/favorites?path=${encodeURIComponent(path)}`),
  getRecent: () => API.get("/api/recent"),
  addRecent: (path) => API.post("/api/recent", { path }),
  removeRecent: (path) => API.del(`/api/recent?path=${encodeURIComponent(path)}`),
};
