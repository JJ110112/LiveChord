/** LiveChord 播放頁 */

(function () {
  const $ = (sel) => document.querySelector(sel);

  const params = new URLSearchParams(window.location.search);
  const trackPath = params.get("path");
  if (!trackPath) { window.location.href = "/"; return; }

  // ---- state ----
  let isFavorite = false;
  let chordData = null;
  let displayMode = "jianpu";
  let chordCache = {};
  let siblingTracks = [];
  let currentIndex = -1;
  let activeChordIdx = -1;
  let chordElements = [];
  let transpose = 0;
  let capo = 0;

  // ---- DOM ----
  const audio = $("#audio");
  const cover = $("#cover");
  const songTitle = $("#songTitle");
  const songArtist = $("#songArtist");
  const songAlbum = $("#songAlbum");
  const songMeta = $("#songMeta");
  const btnPlay = $("#btnPlay");
  const btnFav = $("#btnFav");
  const progressBar = $("#progressBar");
  const progress = $("#progress");
  const timeCurrent = $("#timeCurrent");
  const timeDuration = $("#timeDuration");
  const volumeSlider = $("#volumeSlider");
  const chordDisplay = $("#chordDisplay");
  const toast = $("#toast");
  const detectOverlay = $("#detectOverlay");
  const detectMsg = $("#detectMsg");
  const detectDetail = $("#detectDetail");
  const bigChordBox = $("#currentChordBig");
  const bigChordName = $("#bigChordName");
  const bigChordJianpu = $("#bigChordJianpu");
  const bigChordDiagram = $("#bigChordDiagram");

  function showToast(msg, ms = 2000) {
    toast.textContent = msg;
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), ms);
  }

  // ===========================================================================
  // 一、載入 track：播放時若無和弦譜，自動偵測
  // ===========================================================================

  async function loadTrack(path) {
    audio.src = API.trackStreamUrl(path);
    cover.src = API.trackCoverUrl(path);
    cover.style.display = "";

    try {
      const info = await API.trackInfo(path);
      songTitle.textContent = info.title || path.split("/").pop().replace(/\.flac$/i, "");
      songArtist.textContent = info.artist || "";
      songAlbum.textContent = info.album || "";
      const meta = [];
      if (info.sample_rate) meta.push(`${info.sample_rate / 1000}kHz`);
      if (info.bits_per_sample) meta.push(`${info.bits_per_sample}bit`);
      if (info.channels) meta.push(info.channels === 2 ? "Stereo" : `${info.channels}ch`);
      songMeta.textContent = meta.join(" / ");
      document.title = `${info.title || "LiveChord"} — LiveChord`;
    } catch {
      songTitle.textContent = path.split("/").pop().replace(/\.flac$/i, "");
    }

    API.addRecent(path).catch(() => {});

    try {
      const favData = await API.getFavorites();
      isFavorite = favData.favorites.some((f) => f.path === path);
      updateFavButton();
    } catch {}

    await loadChords(path);
    loadSiblings(path);
  }

  async function loadSiblings(path) {
    const parts = path.split("/");
    parts.pop();
    const dir = parts.join("/");
    try {
      const data = await API.browse(dir);
      siblingTracks = data.entries.filter((e) => !e.is_dir && e.name.toLowerCase().endsWith(".flac"));
      currentIndex = siblingTracks.findIndex((t) => t.path === path);
    } catch {}
  }

  // ---- chord loading (自動偵測整合) ----

  let hasChords = false;

  async function loadChords(path) {
    try {
      chordData = await API.getChords(path);
      if (chordData.exists && chordData.chords && chordData.chords.length > 0) {
        hasChords = true;
        if (chordData.key) {
          const keyInfo = $("#chordKey");
          if (keyInfo) keyInfo.textContent = `Key: ${chordData.key}`;
        }
        if (chordData.capo) {
          capo = chordData.capo;
          const capoSel = $("#capoSelect");
          if (capoSel) capoSel.value = capo;
        }
        await preloadChordInfo(chordData.chords);
        buildChordDOM();
        bigChordBox.style.display = "";
        return;
      }
    } catch {}

    hasChords = false;
    chordDisplay.innerHTML = `<div class="empty" style="padding:20px"><div class="msg" style="color:var(--text-dim)">尚無和弦譜 — 按播放將自動偵測</div></div>`;
    bigChordBox.style.display = "none";
  }

  /** 播放時自動偵測（顯示 overlay） */
  async function autoDetectAndPlay() {
    detectOverlay.style.display = "";
    detectMsg.textContent = "正在偵測和弦…";
    detectDetail.textContent = "首次播放需要分析音訊，大約 30 秒~1 分鐘";

    try {
      const result = await API.detectChords(trackPath);
      detectMsg.textContent = `偵測完成！${result.chord_count} 個和弦`;
      detectDetail.textContent = `調性: ${result.key}`;

      chordCache = {};
      await loadChords(trackPath);
    } catch (err) {
      detectMsg.textContent = "偵測失敗";
      detectDetail.textContent = err.message;
      await new Promise((r) => setTimeout(r, 2000));
    } finally {
      detectOverlay.style.display = "none";
    }

    // 偵測完成後開始播放
    audio.play();
  }

  // ---- preload chord info ----

  async function preloadChordInfo(chords) {
    const names = [...new Set(chords.map((c) => c.chord))];
    await Promise.all(names.map(async (name) => {
      if (chordCache[name]) return;
      chordCache[name] = { jianpu: "", diagram_guitar: null, diagram_ukulele: null };
      try { const info = await API.chordInfo(name); chordCache[name].jianpu = info.jianpu || ""; } catch {}
      try { chordCache[name].diagram_guitar = await API.chordDiagram("guitar", name); } catch {}
      try { chordCache[name].diagram_ukulele = await API.chordDiagram("ukulele", name); } catch {}
    }));
  }

  // ===========================================================================
  // 三、和弦高亮改進：當前和弦置中放大 + 時間軸連動
  // ===========================================================================

  function _displayChords() {
    if (!chordData || !chordData.chords) return [];
    const shift = transpose - capo;
    if (shift === 0) return chordData.chords;
    return chordData.chords.map((c) => ({ ...c, chord: transposeChord(c.chord, shift) }));
  }

  function buildChordDOM() {
    chordDisplay.innerHTML = "";
    chordElements = [];
    activeChordIdx = -1;

    const chords = _displayChords();
    if (!chords || chords.length === 0) return;

    const uncached = chords.filter((c) => !chordCache[c.chord]).map((c) => c.chord);
    if (uncached.length > 0) {
      _buildDOMFromChords(chords);
      _loadMissingCache(uncached).then(() => {
        _buildDOMFromChords(chords);
        updateActiveChord(audio.currentTime || -1);
      });
      return;
    }
    _buildDOMFromChords(chords);
  }

  function _buildDOMFromChords(chords) {
    chordDisplay.innerHTML = "";
    chordElements = [];
    activeChordIdx = -1;
    for (let i = 0; i < chords.length; i++) {
      const el = _createChordEl(chords[i], i);
      chordDisplay.appendChild(el);
      chordElements.push(el);
    }
  }

  async function _loadMissingCache(names) {
    const unique = [...new Set(names)];
    await Promise.all(unique.map(async (name) => {
      if (chordCache[name]) return;
      chordCache[name] = { jianpu: "", diagram_guitar: null, diagram_ukulele: null };
      try { const info = await API.chordInfo(name); chordCache[name].jianpu = info.jianpu || ""; } catch {}
      try { chordCache[name].diagram_guitar = await API.chordDiagram("guitar", name); } catch {}
      try { chordCache[name].diagram_ukulele = await API.chordDiagram("ukulele", name); } catch {}
    }));
  }

  function _createChordEl(chord, idx) {
    const cache = chordCache[chord.chord] || {};
    const div = document.createElement("div");
    div.className = "chord-item";
    div.dataset.idx = idx;
    div.dataset.time = chord.time;
    if (chord.end != null) div.dataset.end = chord.end;

    div.addEventListener("click", () => {
      audio.currentTime = chord.time;
      // 立即更新高亮（不等 seeked 事件）
      updateActiveChord(chord.time);
    });

    _fillChordEl(div, chord, cache);
    return div;
  }

  function _fillChordEl(div, chord, cache) {
    div.innerHTML = "";

    const nameEl = document.createElement("div");
    nameEl.className = "chord-name";
    nameEl.textContent = chord.chord;
    div.appendChild(nameEl);

    if (displayMode === "jianpu") {
      const jp = document.createElement("div");
      jp.className = "chord-jianpu";
      jp.innerHTML = ChordRender.jianpuToHtml(cache.jianpu || "");
      div.appendChild(jp);
    } else {
      const key = displayMode === "guitar" ? "diagram_guitar" : "diagram_ukulele";
      const diagramData = cache[key];
      if (diagramData) {
        const canvas = document.createElement("canvas");
        canvas.style.marginTop = "4px";
        ChordRender.drawDiagram(canvas, diagramData, 1);
        div.appendChild(canvas);
      } else {
        const span = document.createElement("div");
        span.style.cssText = "font-size:11px;color:#666;margin-top:4px";
        span.textContent = "無圖";
        div.appendChild(span);
      }
    }

    const timeEl = document.createElement("div");
    timeEl.className = "chord-time";
    timeEl.textContent = formatTime(chord.time);
    div.appendChild(timeEl);
  }

  /** 更新高亮：timeline 和弦 + 大字顯示 */
  function updateActiveChord(currentTime) {
    if (!chordData || !chordData.chords || chordElements.length === 0) return;

    const displayedChords = _displayChords();

    // 找出當前和弦：持續顯示直到下一個和弦出現
    // 邏輯：找最後一個 time <= currentTime 的和弦
    // 如果在 end 之前 → 完全高亮；在 end 之後但下個和弦之前 → 仍顯示（持續）
    let newIdx = -1;
    for (let i = displayedChords.length - 1; i >= 0; i--) {
      if (currentTime >= displayedChords[i].time) {
        newIdx = i;
        break;
      }
    }

    if (newIdx === activeChordIdx) return;

    // 移除舊高亮
    if (activeChordIdx >= 0 && activeChordIdx < chordElements.length) {
      chordElements[activeChordIdx].classList.remove("active");
    }

    activeChordIdx = newIdx;

    if (activeChordIdx >= 0 && activeChordIdx < chordElements.length) {
      const el = chordElements[activeChordIdx];
      el.classList.add("active");
      el.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });

      // 更新大字顯示
      const chord = displayedChords[activeChordIdx];
      const cache = chordCache[chord.chord] || {};
      bigChordName.textContent = chord.chord;
      bigChordBox.style.display = "";

      // 根據 displayMode 決定大字區顯示內容
      bigChordJianpu.innerHTML = "";
      bigChordDiagram.innerHTML = "";

      if (displayMode === "jianpu") {
        bigChordJianpu.innerHTML = ChordRender.jianpuToHtml(cache.jianpu || "");
      } else {
        // 簡譜仍顯示在副標
        bigChordJianpu.innerHTML = ChordRender.jianpuToHtml(cache.jianpu || "");
        // 放大和弦圖
        const key = displayMode === "guitar" ? "diagram_guitar" : "diagram_ukulele";
        const diag = cache[key];
        if (diag) {
          const canvas = document.createElement("canvas");
          canvas.style.marginTop = "8px";
          ChordRender.drawDiagram(canvas, diag, 2);
          bigChordDiagram.appendChild(canvas);
        }
      }
    } else {
      bigChordName.textContent = "—";
      bigChordJianpu.innerHTML = "";
      bigChordDiagram.innerHTML = "";
    }
  }

  // ===========================================================================
  // 播放控制（攔截播放按鈕，無和弦時先偵測）
  // ===========================================================================

  btnPlay.addEventListener("click", async () => {
    if (!audio.paused) {
      audio.pause();
      return;
    }
    // 有和弦 → 直接播放
    if (hasChords) {
      audio.play();
      return;
    }
    // 無和弦 → 自動偵測後播放
    await autoDetectAndPlay();
  });

  audio.addEventListener("play", () => { btnPlay.innerHTML = "&#x23F8;"; });
  audio.addEventListener("pause", () => { btnPlay.innerHTML = "&#x25B6;"; });
  audio.addEventListener("loadedmetadata", () => {
    timeDuration.textContent = formatTime(audio.duration);
  });

  // requestAnimationFrame 同步
  let rafId = null;

  function tickSync() {
    if (!audio.paused) {
      const t = audio.currentTime;
      const d = audio.duration || 1;
      progress.style.width = (t / d * 100) + "%";
      timeCurrent.textContent = formatTime(t);
      updateActiveChord(t);
      rafId = requestAnimationFrame(tickSync);
    }
  }

  audio.addEventListener("play", () => {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = requestAnimationFrame(tickSync);
  });

  audio.addEventListener("pause", () => {
    if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
    const t = audio.currentTime;
    progress.style.width = (t / (audio.duration || 1) * 100) + "%";
    timeCurrent.textContent = formatTime(t);
    updateActiveChord(t);
  });

  audio.addEventListener("seeked", () => {
    const t = audio.currentTime;
    progress.style.width = (t / (audio.duration || 1) * 100) + "%";
    timeCurrent.textContent = formatTime(t);
    updateActiveChord(t);
  });

  audio.addEventListener("ended", () => {
    btnPlay.innerHTML = "&#x25B6;";
    progress.style.width = "0%";
    if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
    if (activeChordIdx >= 0 && activeChordIdx < chordElements.length)
      chordElements[activeChordIdx].classList.remove("active");
    activeChordIdx = -1;
    bigChordName.textContent = "—";
    bigChordJianpu.innerHTML = "";
    bigChordDiagram.innerHTML = "";
  });

  // progress bar seek (三、時間軸點擊連動)
  progressBar.addEventListener("click", (e) => {
    const rect = progressBar.getBoundingClientRect();
    const pct = (e.clientX - rect.left) / rect.width;
    audio.currentTime = pct * (audio.duration || 0);
  });

  volumeSlider.addEventListener("input", () => {
    audio.volume = parseFloat(volumeSlider.value);
  });

  // prev / next
  $("#btnPrev").addEventListener("click", () => {
    if (siblingTracks.length === 0 || currentIndex <= 0) return;
    window.location.href = `/player?path=${encodeURIComponent(siblingTracks[currentIndex - 1].path)}`;
  });
  $("#btnNext").addEventListener("click", () => {
    if (siblingTracks.length === 0 || currentIndex >= siblingTracks.length - 1) return;
    window.location.href = `/player?path=${encodeURIComponent(siblingTracks[currentIndex + 1].path)}`;
  });

  // ---- edit link ----
  const btnEdit = $("#btnEdit");
  if (btnEdit) btnEdit.href = `/editor?path=${encodeURIComponent(trackPath)}`;

  // ---- manual detect button ----
  const btnDetect = $("#btnDetect");
  if (btnDetect) {
    btnDetect.addEventListener("click", async () => {
      detectOverlay.style.display = "";
      detectMsg.textContent = "正在重新偵測和弦…";
      detectDetail.textContent = "分析音訊中，請稍候";
      try {
        const result = await API.detectChords(trackPath);
        showToast(`偵測完成！${result.chord_count} 個和弦，調性: ${result.key}`, 3000);
        chordCache = {};
        await loadChords(trackPath);
        updateActiveChord(audio.currentTime || -1);
      } catch (err) {
        showToast("偵測失敗: " + err.message, 4000);
      } finally {
        detectOverlay.style.display = "none";
      }
    });
  }

  // ---- favorite ----
  btnFav.addEventListener("click", async () => {
    try {
      if (isFavorite) {
        await API.removeFavorite(trackPath);
        isFavorite = false;
        showToast("已取消收藏");
      } else {
        await API.addFavorite(trackPath);
        isFavorite = true;
        showToast("已加入最愛");
      }
      updateFavButton();
    } catch (err) {
      showToast("操作失敗: " + err.message);
    }
  });

  function updateFavButton() {
    btnFav.innerHTML = isFavorite ? "&#x2764;" : "&#x2661;";
    btnFav.classList.toggle("active", isFavorite);
  }

  // ---- mode switch ----
  document.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".mode-btn").forEach((b) => {
        b.style.background = "transparent";
        b.style.color = "var(--text-dim)";
        b.classList.remove("active");
      });
      btn.style.background = "var(--accent)";
      btn.style.color = "#fff";
      btn.classList.add("active");
      displayMode = btn.dataset.mode;
      buildChordDOM();
      updateActiveChord(audio.currentTime || -1);
    });
  });

  // ---- Transpose / Capo ----
  const transposeUpBtn = $("#btnTransposeUp");
  const transposeDnBtn = $("#btnTransposeDown");
  const transposeVal = $("#transposeValue");
  const capoSelect = $("#capoSelect");

  if (transposeUpBtn) transposeUpBtn.addEventListener("click", () => {
    transpose = Math.min(transpose + 1, 11);
    transposeVal.textContent = transpose > 0 ? `+${transpose}` : transpose;
    buildChordDOM(); updateActiveChord(audio.currentTime || -1);
  });
  if (transposeDnBtn) transposeDnBtn.addEventListener("click", () => {
    transpose = Math.max(transpose - 1, -11);
    transposeVal.textContent = transpose > 0 ? `+${transpose}` : transpose;
    buildChordDOM(); updateActiveChord(audio.currentTime || -1);
  });
  if (capoSelect) capoSelect.addEventListener("change", () => {
    capo = parseInt(capoSelect.value) || 0;
    buildChordDOM(); updateActiveChord(audio.currentTime || -1);
  });

  // ---- init ----
  loadTrack(trackPath);
})();

// ---- 移調工具函式 (全域) ----
const NOTE_NAMES_SHARP = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];
const NOTE_NAMES_FLAT  = ["C","Db","D","Eb","E","F","Gb","G","Ab","A","Bb","B"];

function noteToSemitone(n) {
  const m = {C:0,D:2,E:4,F:5,G:7,A:9,B:11};
  let s = m[n[0].toUpperCase()] || 0;
  if (n.length > 1) { if (n[1]==="#") s++; else if (n[1]==="b") s--; }
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
