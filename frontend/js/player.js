/** LiveChord 播放頁 */

(function () {
  const $ = (sel) => document.querySelector(sel);

  const params = new URLSearchParams(window.location.search);
  const trackPath = params.get("path");
  if (!trackPath) { window.location.href = "/"; return; }

  // ---- state ----
  let isFavorite = false;
  let chordData = null;
  let displayMode = "piano";
  let chordCache = {};
  let siblingTracks = [];
  let currentIndex = -1;
  let activeChordIdx = -1;
  let chordElements = [];
  let _ribbonPositions = [];
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
  const chordDisplayOverview = $("#chordDisplayOverview");
  const chordDisplayDiagrams = $("#chordDisplayDiagrams");
  const ribbonTrack = $("#ribbonTrack");
  const tabOverview = $("#tabOverview");
  const tabDiagrams = $("#tabDiagrams");
  const toast = $("#toast");
  const detectOverlay = $("#detectOverlay");
  const detectMsg = $("#detectMsg");
  const detectDetail = $("#detectDetail");
  const bigChordBox = $("#currentChordBig");
  const bigChordName = $("#bigChordName");
  const bigChordJianpu = $("#bigChordJianpu");

  let activeTab = localStorage.getItem("livechord_tab") || "overview";
  let ribbonElements = [];
  const pxPerSec = 100;

  if (tabOverview && tabDiagrams) {
    tabOverview.addEventListener("click", () => {
      activeTab = "overview";
      localStorage.setItem("livechord_tab", "overview");
      tabOverview.classList.add("active");
      tabDiagrams.classList.remove("active");
      chordDisplayOverview.style.display = "";
      chordDisplayDiagrams.style.display = "none";
      // Overview 顯示大和弦
      if (hasChords) bigChordBox.style.display = "";
      if (activeChordIdx >= 0 && activeChordIdx < chordElements.length) {
        chordElements[activeChordIdx].scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
      }
      // 重新觸發高亮更新
      activeChordIdx = -1;
      updateActiveChord(audio.currentTime || -1);
    });

    tabDiagrams.addEventListener("click", () => {
      activeTab = "diagrams";
      localStorage.setItem("livechord_tab", "diagrams");
      tabDiagrams.classList.add("active");
      tabOverview.classList.remove("active");
      chordDisplayOverview.style.display = "none";
      chordDisplayDiagrams.style.display = "block";
      // Diagrams 隱藏大和弦（避免重複）
      bigChordBox.style.display = "none";
      const t = audio.currentTime || 0;
      if (ribbonTrack) {
        ribbonTrack.style.transform = `translateX(${-t * pxPerSec}px)`;
      }
    });

    // 還原上次 tab
    if (activeTab === "diagrams") tabDiagrams.click();
  }
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
    } catch (err) {
      console.error("loadChords error:", err);
    }

    hasChords = false;
    chordDisplayOverview.innerHTML = `<div class="empty" style="padding:20px"><div class="msg" style="color:var(--text-dim)">尚無和弦譜 — 請按「偵測」按鈕手動偵測</div></div>`;
    if (ribbonTrack) ribbonTrack.innerHTML = "";
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
      chordCache[name] = { jianpu: "", notes: [], diagram_guitar: null, diagram_ukulele: null };
      try {
        const info = await API.chordInfo(name);
        chordCache[name].jianpu = info.jianpu || "";
        chordCache[name].notes = info.notes || [];
      } catch {}
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
    chordDisplayOverview.innerHTML = "";
    if (ribbonTrack) ribbonTrack.innerHTML = "";
    chordElements = [];
    ribbonElements = [];
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
    chordDisplayOverview.innerHTML = "";
    if (ribbonTrack) ribbonTrack.innerHTML = "";
    chordElements = [];
    ribbonElements = [];
    _ribbonPositions = [];
    activeChordIdx = -1;

    // Pre-calculate ribbon positions: no overlap, min width 120px
    let curLeft = 0;
    for (let i = 0; i < chords.length; i++) {
      const timeLeft = chords[i].time * pxPerSec;
      const left = Math.max(timeLeft, curLeft);
      const nextStart = i + 1 < chords.length ? chords[i+1].time : chords[i].time + 4;
      const naturalW = (nextStart - chords[i].time) * pxPerSec;
      const w = Math.max(naturalW, 120);
      _ribbonPositions.push({ left, width: w, time: chords[i].time });
      curLeft = left + w;
    }

    for (let i = 0; i < chords.length; i++) {
      const el = _createChordEl(chords[i], i);
      chordDisplayOverview.appendChild(el);
      chordElements.push(el);

      if (ribbonTrack) {
        const rEl = _createRibbonEl(chords[i], i, chords);
        ribbonTrack.appendChild(rEl);
        ribbonElements.push(rEl);
      }
    }
  }

  async function _loadMissingCache(names) {
    const unique = [...new Set(names)];
    await Promise.all(unique.map(async (name) => {
      if (chordCache[name]) return;
      chordCache[name] = { jianpu: "", notes: [], diagram_guitar: null, diagram_ukulele: null };
      try {
        const info = await API.chordInfo(name);
        chordCache[name].jianpu = info.jianpu || "";
        chordCache[name].notes = info.notes || [];
      } catch {}
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

  function _createRibbonEl(chord, idx, allChords) {
    const cache = chordCache[chord.chord] || {};
    const div = document.createElement("div");
    div.className = "ribbon-item";
    div.dataset.idx = idx;
    
    // Use pre-calculated positions (no overlap)
    const pos = _ribbonPositions[idx] || { left: chord.time * pxPerSec, width: 120 };
    div.style.left = `${pos.left}px`;
    div.style.width = `${pos.width}px`;

    div.addEventListener("click", () => {
      audio.currentTime = chord.time;
      updateActiveChord(chord.time);
    });

    const nameEl = document.createElement("div");
    nameEl.className = "chord-name";
    nameEl.textContent = chord.chord;
    div.appendChild(nameEl);

    if (displayMode === "piano") {
      const jp = document.createElement("div");
      jp.className = "chord-jianpu";
      jp.innerHTML = ChordRender.jianpuToHtml(cache.jianpu || "");
      div.appendChild(jp);
      const pianoCanvas = document.createElement("canvas");
      ChordRender.drawPiano(pianoCanvas, cache.notes || [], 1);
      div.appendChild(pianoCanvas);
    } else {
      const key = displayMode === "guitar" ? "diagram_guitar" : "diagram_ukulele";
      const diag = cache[key];
      if (diag) {
        const canvas = document.createElement("canvas");
        ChordRender.drawDiagram(canvas, diag, 1);
        div.appendChild(canvas);
      } else {
        const span = document.createElement("div");
        span.style.cssText = "font-size:12px;color:#666;margin-top:8px";
        span.textContent = "無圖";
        div.appendChild(span);
      }
    }
    return div;
  }

  function _fillChordEl(div, chord, cache) {
    div.innerHTML = "";

    const nameEl = document.createElement("div");
    nameEl.className = "chord-name";
    nameEl.textContent = chord.chord;
    div.appendChild(nameEl);

    if (displayMode === "piano") {
      const jp = document.createElement("div");
      jp.className = "chord-jianpu";
      jp.innerHTML = ChordRender.jianpuToHtml(cache.jianpu || "");
      div.appendChild(jp);
      const pianoCanvas = document.createElement("canvas");
      pianoCanvas.style.marginTop = "4px";
      ChordRender.drawPiano(pianoCanvas, cache.notes || [], 1);
      div.appendChild(pianoCanvas);
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

    if (activeTab === "diagrams" && ribbonTrack && _ribbonPositions.length > 0) {
      // Interpolate scroll from adjusted positions
      let scrollX = currentTime * pxPerSec;
      for (let i = _ribbonPositions.length - 1; i >= 0; i--) {
        const p = _ribbonPositions[i];
        if (currentTime >= p.time) {
          const frac = p.width > 0 ? (currentTime - p.time) * pxPerSec / ((i + 1 < _ribbonPositions.length ? _ribbonPositions[i+1].time - p.time : 4) * pxPerSec) : 0;
          scrollX = p.left + Math.min(frac, 1) * p.width;
          break;
        }
      }
      ribbonTrack.style.transform = `translateX(${-scrollX}px)`;
    }

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
      if (ribbonElements[activeChordIdx]) {
        ribbonElements[activeChordIdx].classList.remove("active");
      }
    }

    activeChordIdx = newIdx;

    if (activeChordIdx >= 0 && activeChordIdx < chordElements.length) {
      const el = chordElements[activeChordIdx];
      el.classList.add("active");
      if (activeTab === "overview") {
        el.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
      }

      if (ribbonElements[activeChordIdx]) {
        ribbonElements[activeChordIdx].classList.add("active");
      }

      // 更新大字顯示 — Diagrams 模式隱藏（避免重複）
      const chord = displayedChords[activeChordIdx];
      const cache = chordCache[chord.chord] || {};

      if (activeTab === "diagrams") {
        bigChordBox.style.display = "none";
      } else {
        bigChordName.textContent = chord.chord;
        bigChordBox.style.display = "";

        bigChordJianpu.innerHTML = "";
        bigChordDiagram.innerHTML = "";

        if (displayMode === "piano") {
          bigChordJianpu.innerHTML = ChordRender.jianpuToHtml(cache.jianpu || "");
          const pianoCanvas = document.createElement("canvas");
          pianoCanvas.style.marginTop = "8px";
          ChordRender.drawPiano(pianoCanvas, cache.notes || [], 1.8);
          bigChordDiagram.appendChild(pianoCanvas);
        } else {
          bigChordJianpu.innerHTML = ChordRender.jianpuToHtml(cache.jianpu || "");
          const key = displayMode === "guitar" ? "diagram_guitar" : "diagram_ukulele";
          const diag = cache[key];
          if (diag) {
            const canvas = document.createElement("canvas");
            canvas.style.marginTop = "8px";
            ChordRender.drawDiagram(canvas, diag, 2);
            bigChordDiagram.appendChild(canvas);
          }
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

  btnPlay.addEventListener("click", () => {
    if (audio.paused) audio.play();
    else audio.pause();
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
    if (activeChordIdx >= 0 && activeChordIdx < chordElements.length) {
      chordElements[activeChordIdx].classList.remove("active");
      if (ribbonElements[activeChordIdx]) ribbonElements[activeChordIdx].classList.remove("active");
    }
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

  // 還原上次音量
  const savedVol = localStorage.getItem("livechord_volume");
  if (savedVol !== null) {
    const v = parseFloat(savedVol);
    audio.volume = v;
    volumeSlider.value = v;
  }

  volumeSlider.addEventListener("input", () => {
    audio.volume = parseFloat(volumeSlider.value);
    localStorage.setItem("livechord_volume", volumeSlider.value);
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
      // 先搜尋 MIDI 檔案
      detectOverlay.style.display = "";
      detectMsg.textContent = "搜尋 MIDI 檔案…";
      detectDetail.textContent = "";

      try {
        const midiResult = await API.midiSearch(trackPath);

        if (midiResult.results && midiResult.results.length > 0) {
          // 有 MIDI → 讓使用者選擇
          const midiList = midiResult.results.map(m => m.name).join("\n");
          detectOverlay.style.display = "none";

          const useMidi = confirm(
            `找到 ${midiResult.results.length} 個 MIDI 檔案：\n\n${midiList}\n\n使用 MIDI 匯入？（取消則用 AI 偵測）`
          );

          if (useMidi) {
            detectOverlay.style.display = "";
            detectMsg.textContent = "MIDI 匯入中…";
            detectDetail.textContent = midiResult.results[0].name;
            const result = await API.midiImport(trackPath, midiResult.results[0].path);
            showToast(`MIDI 匯入完成！${result.chord_count} 個和弦，Key: ${result.key}`, 3000);
            chordCache = {};
            await loadChords(trackPath);
            updateActiveChord(audio.currentTime || -1);
            detectOverlay.style.display = "none";
            return;
          }
        }

        // 無 MIDI 或使用者選擇 AI 偵測
        detectOverlay.style.display = "";
        detectMsg.textContent = "AI 偵測和弦中…";
        detectDetail.textContent = "分析音訊中，請稍候";
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
  // 簡譜/吉他/烏克麗麗模式切換（只限 #modeSwitch 內的按鈕）
  const capoGroup = $("#capoGroup");

  function _updateCapoVisibility() {
    if (capoGroup) {
      capoGroup.style.display = (displayMode === "guitar" || displayMode === "ukulele") ? "" : "none";
    }
  }

  document.querySelectorAll("#modeSwitch .mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#modeSwitch .mode-btn").forEach((b) => {
        b.style.background = "transparent";
        b.style.color = "var(--text-dim)";
        b.classList.remove("active");
      });
      btn.style.background = "var(--accent)";
      btn.style.color = "#fff";
      btn.classList.add("active");
      displayMode = btn.dataset.mode;
      _updateCapoVisibility();
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
