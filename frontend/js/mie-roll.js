/* LiveChord MIE — piano roll (plan §11 Phase 2, stage 2 of the 2026-09-09 sequence).
 *
 * A take is four minutes of a thousand notes across seven lanes and six
 * instruments. The event list can show you the last twenty of them, which is
 * the wrong shape for the questions actually being asked: is this lane sitting
 * on top of my playing, does that answer come back before or after the chord
 * moves, why is the strings part always up there.
 *
 * So: time on X, pitch on Y, a keyboard down the left edge as the legend, one
 * colour per lane. Canvas 2D, no library — this repo already draws a MIDI
 * waterfall the same way in player.js, and a take is a few thousand
 * rectangles, which 2D handles without breathing hard.
 *
 * Two modes:
 *   LIVE    — events arrive from the engine's WebSocket and the view follows
 *             the playhead. Off by default: the panel is an instrument during a
 *             performance and nothing here is allowed to cost it a take.
 *   REVIEW  — a session JSONL is loaded and can be scrubbed, looped and played
 *             back at quarter speed to look for collisions and late entries.
 *
 * Reading a log needs `dur_ms` on `gen` and an `off` for every note, which the
 * engine only started recording on 2026-09-09. Older logs draw with the notes
 * they can pair and say how many they could not.
 */
(function () {
  "use strict";

  // One colour per LANE, not per algorithm: a scene names its own edges
  // (`phrase_modx`, `iridium_to_wavestate`) and a hardcoded legend goes stale
  // the moment a scene changes. Lanes are the engine's own vocabulary.
  const LANE_HUE = {
    human: 145,       // green: the one you played
    shadow: 275,      // violet
    echo: 190,        // cyan
    echo2: 205,
    follow: 25,       // orange
    phrase: 55,       // yellow
    sustain: 330,     // pink
    pad: 300,
    texture: 240,
  };
  const LANE_LABEL = {
    human: "你", shadow: "影子", echo: "回音", echo2: "回音2", follow: "跟隨",
    phrase: "樂句", sustain: "延續", pad: "襯底", texture: "織體",
  };

  function hueFor(lane) {
    if (LANE_HUE[lane] !== undefined) return LANE_HUE[lane];
    let h = 0;                                  // stable colour for a lane we do not know
    for (let i = 0; i < lane.length; i++) h = (h * 31 + lane.charCodeAt(i)) % 360;
    return h;
  }

  const BLACK = { 1: 1, 3: 1, 6: 1, 8: 1, 10: 1 };
  const isBlack = (n) => !!BLACK[((n % 12) + 12) % 12];
  const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const noteName = (n) => NOTE_NAMES[((n % 12) + 12) % 12] + (Math.floor(n / 12) - 1);

  // ---------------------------------------------------------------- model
  /** Turn a session's events into note rectangles.
   *
   * `gen` gives the pitch that actually sounded and the length planned at send
   * time; the matching `off` gives the truth. They are paired oldest-first per
   * (channel, pitch) because that is how the engine releases them - and one in
   * a few hundred has no `off` at all, because two overlapping notes of the
   * same pitch on one channel share a single note_off. Those fall back to the
   * planned length, which is what `dur_ms` is for.
   */
  function buildNotes(rows) {
    const notes = [];
    const open = new Map();                     // "src|ch|note" -> [note, ...]
    let unpaired = 0, noLength = 0;
    const key = (src, ch, n) => src + "|" + ch + "|" + n;

    for (const r of rows) {
      const t = r.t;
      if (r.type === "human") {
        const o = { t, ch: r.ch, note: r.note, vel: r.vel || 64, lane: "human",
                    edge: "human", dur: null, planned: null, human: true };
        notes.push(o);
        const k = key("h", r.ch, r.note);
        if (!open.has(k)) open.set(k, []);
        open.get(k).push(o);
      } else if (r.type === "human_off") {
        const q = open.get(key("h", r.ch, r.note));
        const o = q && q.shift();
        if (o) o.dur = (r.held_ms !== undefined ? r.held_ms / 1000 : Math.max(0.05, t - o.t));
      } else if (r.type === "gen") {
        const o = { t, ch: r.ch, note: r.note, vel: r.vel || 64, lane: r.lane || "gen",
                    edge: r.edge || "", hop: r.hop || 1,
                    dur: null,
                    planned: r.dur_ms !== undefined ? r.dur_ms / 1000 : null,
                    follow: !!r.follow, human: false };
        if (r.dur_ms === undefined) noLength++;
        notes.push(o);
        const k = key("g", r.ch, r.note);
        if (!open.has(k)) open.set(k, []);
        open.get(k).push(o);
      } else if (r.type === "off") {
        const q = open.get(key("g", r.ch, r.note));
        const o = q && q.shift();
        if (o) o.dur = (r.held_ms !== undefined ? r.held_ms / 1000 : Math.max(0.03, t - o.t));
      }
    }
    for (const n of notes) {
      if (n.dur === null) {
        unpaired++;
        // a note we never saw stop: its own planned length, else a short stub
        // so it is visible rather than invisible
        n.dur = n.planned !== null ? n.planned : 0.25;
        n.openEnded = true;
      }
    }
    return { notes, unpaired, noLength };
  }

  function spanOf(notes) {
    let lo = Infinity, hi = -Infinity, t0 = Infinity, t1 = -Infinity;
    for (const n of notes) {
      if (n.note < lo) lo = n.note;
      if (n.note > hi) hi = n.note;
      if (n.t < t0) t0 = n.t;
      if (n.t + n.dur > t1) t1 = n.t + n.dur;
    }
    if (!notes.length) return { lo: 48, hi: 84, t0: 0, t1: 10 };
    return { lo: Math.max(0, lo - 2), hi: Math.min(127, hi + 2), t0: Math.max(0, t0 - 0.5), t1: t1 + 0.5 };
  }

  /** JSONL -> rows. Blank lines and a half-written last line are normal in a
   *  log still being appended to; anything else that will not parse is counted,
   *  so the panel can say what it could not use instead of drawing nothing.
   *  Splits on CR as well as LF - the engine writes CRLF on Windows, which a
   *  file picked off disk keeps and the fetch path happened to hide.
   */
  function parseLog(text) {
    const rows = [];
    let bad = 0;
    for (const line of String(text).split(/\r?\n/)) {
      const t = line.trim().replace(/^﻿/, "");
      if (!t) continue;
      try { rows.push(JSON.parse(t)); } catch (e) { bad++; }
    }
    rows._bad = bad;
    return rows;
  }

  // ------------------------------------------------------------ the view
  function create(root, opts) {
    opts = opts || {};
    const el = {
      canvas: root.querySelector(".mr-canvas"),
      lanes: root.querySelector(".mr-lanes"),
      seek: root.querySelector(".mr-seek"),
      play: root.querySelector(".mr-play"),
      speed: root.querySelector(".mr-speed"),
      loop: root.querySelector(".mr-loop"),
      zoom: root.querySelector(".mr-zoom"),
      file: root.querySelector(".mr-file"),
      logs: root.querySelector(".mr-logs"),
      live: root.querySelector(".mr-live"),
      sound: root.querySelector(".mr-sound"),
      time: root.querySelector(".mr-time"),
      note: root.querySelector(".mr-note"),
      title: root.querySelector(".mr-title"),
    };
    const ctx = el.canvas.getContext("2d");

    const st = {
      notes: [],
      span: { lo: 48, hi: 84, t0: 0, t1: 10 },
      hidden: new Set(),                 // lanes the player has switched off
      view: 0,                           // left edge of the window, seconds
      secondsPerScreen: 20,
      playhead: 0,
      playing: false,
      speed: 1,
      loop: null,                        // [a, b] in seconds
      live: false,
      liveT: 0,
      hover: null,
      raf: 0,
      lastFrame: 0,
      sound: false,                      // off until asked: this makes noise
    };

    // ------------------------------------------------------------ drawing
    const KEY_W = 44;                    // the keyboard legend down the left
    function size() {
      const dpr = window.devicePixelRatio || 1;
      const r = el.canvas.getBoundingClientRect();
      const w = Math.max(320, Math.floor(r.width)), h = Math.max(160, Math.floor(r.height));
      if (el.canvas.width !== w * dpr || el.canvas.height !== h * dpr) {
        el.canvas.width = w * dpr; el.canvas.height = h * dpr;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return { w, h };
    }

    const ink = () => {
      const t = document.documentElement.getAttribute("data-theme");
      return /^(light|sakura|sunny|sky)$/.test(t || "") ? "0,0,0" : "255,255,255";
    };

    function draw() {
      const { w, h } = size();
      const rgb = ink();
      const plotW = w - KEY_W;
      const { lo, hi } = st.span;
      const rows = Math.max(1, hi - lo + 1);
      const rowH = h / rows;
      const yOf = (n) => (hi - n) * rowH;
      const xOf = (t) => KEY_W + ((t - st.view) / st.secondsPerScreen) * plotW;

      ctx.clearRect(0, 0, w, h);

      // pitch lanes: the black keys shaded, so the eye can find an octave
      for (let n = lo; n <= hi; n++) {
        if (isBlack(n)) {
          ctx.fillStyle = `rgba(${rgb},.05)`;
          ctx.fillRect(KEY_W, yOf(n), plotW, rowH);
        }
        if (n % 12 === 0) {
          ctx.fillStyle = `rgba(${rgb},.16)`;
          ctx.fillRect(KEY_W, yOf(n) + rowH - 1, plotW, 1);
        }
      }

      // a second grid, and a heavier line every five
      const step = st.secondsPerScreen > 60 ? 10 : st.secondsPerScreen > 24 ? 5 : 1;
      const first = Math.floor(st.view / step) * step;
      ctx.font = "10px ui-monospace, monospace";
      for (let t = first; t < st.view + st.secondsPerScreen; t += step) {
        const x = xOf(t);
        if (x < KEY_W) continue;
        ctx.fillStyle = `rgba(${rgb},${t % (step * 5) === 0 ? .18 : .07})`;
        ctx.fillRect(x, 0, 1, h);
        if (t % (step * 5) === 0) {
          ctx.fillStyle = `rgba(${rgb},.45)`;
          ctx.fillText(`${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`, x + 3, 11);
        }
      }

      // the notes
      const vEnd = st.view + st.secondsPerScreen;
      for (const n of st.notes) {
        if (st.hidden.has(n.lane)) continue;
        if (n.t > vEnd || n.t + n.dur < st.view) continue;
        if (n.note < lo || n.note > hi) continue;
        const x = xOf(n.t);
        const wid = Math.max(2, (n.dur / st.secondsPerScreen) * plotW);
        const y = yOf(n.note);
        const hgt = Math.max(2, rowH - 1);
        const hue = hueFor(n.lane);
        const light = n.human ? 62 : 42 + Math.round((n.vel / 127) * 26);
        const sat = n.human ? 70 : 78;
        ctx.fillStyle = `hsl(${hue} ${sat}% ${light}%)`;
        ctx.globalAlpha = n.human ? 1 : 0.55 + (n.vel / 127) * 0.45;
        ctx.fillRect(Math.max(KEY_W, x), y, Math.min(wid, w - Math.max(KEY_W, x)), hgt);
        ctx.globalAlpha = 1;
        // a note we never saw stop is drawn from its PLANNED length, so it is
        // marked - otherwise the picture would assert something it does not know
        if (n.openEnded && wid > 6) {
          ctx.fillStyle = `rgba(${rgb},.5)`;
          ctx.fillRect(Math.max(KEY_W, x) + Math.min(wid, w) - 2, y, 2, hgt);
        }
        if (n.human && hgt > 3) {              // the human line gets an outline
          ctx.strokeStyle = `hsl(${hue} 80% 78%)`;
          ctx.lineWidth = 1;
          ctx.strokeRect(Math.max(KEY_W, x) + .5, y + .5, Math.min(wid, w) - 1, hgt - 1);
        }
      }

      // the keyboard legend
      ctx.fillStyle = `rgba(${rgb},.06)`;
      ctx.fillRect(0, 0, KEY_W, h);
      for (let n = lo; n <= hi; n++) {
        const y = yOf(n);
        ctx.fillStyle = isBlack(n) ? `rgba(${rgb},.55)` : `rgba(${rgb},.14)`;
        ctx.fillRect(0, y, isBlack(n) ? KEY_W * 0.62 : KEY_W - 2, Math.max(1, rowH - 1));
        if (n % 12 === 0 && rowH > 6) {
          ctx.fillStyle = `rgba(${rgb},.75)`;
          ctx.font = "9px ui-monospace, monospace";
          ctx.fillText(noteName(n), KEY_W - 22, y + rowH - 1);
        }
      }

      // loop region, then the playhead over everything
      if (st.loop) {
        const a = xOf(st.loop[0]), b = xOf(st.loop[1]);
        ctx.fillStyle = "rgba(92,107,192,.18)";
        ctx.fillRect(Math.max(KEY_W, a), 0, Math.max(1, b - Math.max(KEY_W, a)), h);
      }
      const px = xOf(st.playhead);
      if (px >= KEY_W) {
        ctx.fillStyle = "#ff5252";
        ctx.fillRect(px, 0, 1.5, h);
      }
    }

    function schedule() {
      if (st.raf) return;
      st.raf = requestAnimationFrame(() => { st.raf = 0; draw(); });
    }

    // ------------------------------------------------------------ playback
    /** Move the playhead by `dt` seconds of wall clock. Separated from the
     *  animation loop so the transport can be reasoned about - and tested -
     *  without a visible document: `requestAnimationFrame` does not run at all
     *  on a hidden tab, which is correct for an animation and useless for
     *  checking that a loop region actually wraps.
     */
    function advance(dt) {
      const before = st.playhead;
      st.playhead += dt * st.speed;
      if (st.loop && st.playhead > st.loop[1]) {
        st.playhead = st.loop[0];
        if (st.playing) { stopSound(); startSound(); }   // the loop wrapped
      } else if (st.sound && st.playing && st.soundSentAt !== null &&
                 st.soundSentAt !== undefined &&
                 before - st.soundSentAt > SOUND_WINDOW_S * 0.6) {
        startSound();                                     // top the window up
      }
      if (st.playhead >= st.span.t1) { st.playhead = st.span.t1; setPlaying(false); }
      follow();
      syncSeek();
    }

    function tick(now) {
      if (!st.playing) return;
      // the first frame after a start - or after the tab was hidden and the
      // pending frame finally arrives - must not jump by however long that was
      const dt = st.lastFrame ? (now - st.lastFrame) / 1000 : 0;
      st.lastFrame = now;
      advance(dt);
      draw();
      if (st.playing) requestAnimationFrame(tick);
    }

    function follow() {
      const margin = st.secondsPerScreen * 0.15;
      if (st.playhead < st.view + margin) st.view = st.playhead - margin;
      if (st.playhead > st.view + st.secondsPerScreen - margin)
        st.view = st.playhead - st.secondsPerScreen + margin;
      st.view = Math.max(st.span.t0 - 1, st.view);
    }

    function setPlaying(on) {
      const was = st.playing;
      st.playing = on;
      st.lastFrame = 0;
      el.play.textContent = on ? "❚❚" : "▶";
      el.play.classList.toggle("is-on", on);
      if (on !== was) { if (on) startSound(); else stopSound(); }
      if (on) requestAnimationFrame(tick);
    }

    // ---------------------------------------------------------- 回放送音
    // The notes go to the ENGINE, once, and its scheduler plays them: sending
    // them one at a time as the playhead crosses each would put a browser
    // animation frame and a WebSocket in the middle of the timing, and this
    // project measures its jitter in single milliseconds.
    //
    // Bounded: only what is about to be heard. A whole four-minute take is a
    // few thousand notes, and re-sending the lot on every scrub would be rude
    // to the engine thread for no gain.
    const SOUND_WINDOW_S = 45;

    function startSound() {
      if (!st.sound || !opts.send) return;
      const from = st.playhead;
      const until = st.loop ? Math.min(st.loop[1], from + SOUND_WINDOW_S) : from + SOUND_WINDOW_S;
      // What you can SEE is what you hear, including your own part: leaving the
      // 「你」 chip on plays your keyboard back too, which is the only way to
      // hear whether an answer sat well against what it was answering. Turn the
      // chip off and it goes quiet like any other lane.
      const withHuman = !st.hidden.has("human");
      const notes = [];
      for (const n of st.notes) {
        if (st.hidden.has(n.lane)) continue;
        if (n.t + n.dur < from || n.t > until) continue;
        notes.push({ t: Math.max(0, n.t - from), ch: n.ch, note: n.note,
                     vel: n.vel, dur: n.dur });
      }
      opts.send({ type: "play_take", notes, speed: st.speed, human: withHuman });
      st.soundSentAt = from;
    }

    function stopSound() {
      if (!opts.send) return;
      opts.send({ type: "play_stop" });
      st.soundSentAt = null;
    }

    el.sound.addEventListener("click", () => {
      st.sound = !st.sound;
      el.sound.classList.toggle("is-on", st.sound);
      el.sound.textContent = st.sound ? "🔊 送出 MIDI" : "🔇 靜音回放";
      if (!st.sound) stopSound();
      else if (st.playing) startSound();
    });

    function syncSeek() {
      const { t0, t1 } = st.span;
      el.seek.min = t0; el.seek.max = t1;
      if (document.activeElement !== el.seek) el.seek.value = st.playhead;
      el.time.textContent = fmt(st.playhead) + " / " + fmt(t1);
    }
    const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

    // -------------------------------------------------------------- lanes
    function renderLanes() {
      const counts = new Map();
      for (const n of st.notes) counts.set(n.lane, (counts.get(n.lane) || 0) + 1);
      el.lanes.innerHTML = "";
      // sorted by how much of the take each one is, so the loud ones lead
      [...counts.entries()].sort((a, b) => b[1] - a[1]).forEach(([lane, n]) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "mr-lane" + (st.hidden.has(lane) ? "" : " on");
        b.dataset.lane = lane;
        b.style.setProperty("--h", hueFor(lane));
        b.innerHTML = `<i></i>${LANE_LABEL[lane] || lane}<span>${n}</span>`;
        b.title = `${lane}：${n} 個音（點一下單獨隱藏，按住 Alt 只留這一條）`;
        el.lanes.appendChild(b);
      });
    }

    function paintLanes() {
      el.lanes.querySelectorAll(".mr-lane").forEach((b) => {
        b.classList.toggle("on", !st.hidden.has(b.dataset.lane));
      });
    }

    el.lanes.addEventListener("click", (ev) => {
      const b = ev.target.closest(".mr-lane");
      if (!b) return;
      const lane = b.dataset.lane;
      if (ev.altKey) {
        const all = new Set(st.notes.map((n) => n.lane));
        st.hidden = new Set([...all].filter((k) => k !== lane));
      } else if (st.hidden.has(lane)) st.hidden.delete(lane);
      else st.hidden.add(lane);
      // repaint the classes, do NOT rebuild the list: replacing the button
      // under the finger that just pressed it loses every rapid second click
      paintLanes();
      draw();
      if (st.playing && st.sound) { stopSound(); startSound(); }
    });

    // ------------------------------------------------------------ pointer
    el.canvas.addEventListener("pointermove", (ev) => {
      const r = el.canvas.getBoundingClientRect();
      const x = ev.clientX - r.left, y = ev.clientY - r.top;
      if (x < KEY_W) { el.note.textContent = ""; return; }
      const plotW = r.width - KEY_W;
      const t = st.view + ((x - KEY_W) / plotW) * st.secondsPerScreen;
      const rows = st.span.hi - st.span.lo + 1;
      const pitch = Math.round(st.span.hi - (y / r.height) * rows);
      const hit = st.notes.find((n) => !st.hidden.has(n.lane) && n.note === pitch &&
                                       t >= n.t && t <= n.t + n.dur);
      el.note.textContent = hit
        ? `${noteName(hit.note)} · ${LANE_LABEL[hit.lane] || hit.lane}` +
          `${hit.edge && hit.edge !== "human" ? " · " + hit.edge : ""}` +
          ` · CH${hit.ch} · v${hit.vel} · ${Math.round(hit.dur * 1000)}ms` +
          `${hit.openEnded ? "（長度為排程值）" : ""}`
        : `${noteName(pitch)} · ${fmt(t)}`;
    });
    el.canvas.addEventListener("pointerleave", () => { el.note.textContent = ""; });

    el.canvas.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      const r = el.canvas.getBoundingClientRect();
      const x = Math.max(KEY_W, ev.clientX - r.left);
      const at = st.view + ((x - KEY_W) / (r.width - KEY_W)) * st.secondsPerScreen;
      const f = ev.deltaY > 0 ? 1.18 : 1 / 1.18;
      st.secondsPerScreen = Math.min(600, Math.max(1, st.secondsPerScreen * f));
      st.view = at - ((x - KEY_W) / (r.width - KEY_W)) * st.secondsPerScreen;
      el.zoom.value = st.secondsPerScreen;
      draw();
    }, { passive: false });

    // click to move the playhead; drag with shift to set a loop
    let dragFrom = null;
    el.canvas.addEventListener("pointerdown", (ev) => {
      const r = el.canvas.getBoundingClientRect();
      const x = ev.clientX - r.left;
      if (x < KEY_W) return;
      const t = st.view + ((x - KEY_W) / (r.width - KEY_W)) * st.secondsPerScreen;
      if (ev.shiftKey) { dragFrom = t; st.loop = [t, t]; }
      else {
        st.playhead = t; st.loop = null; el.loop.classList.remove("is-on");
        if (st.playing) { stopSound(); startSound(); }
      }
      el.canvas.setPointerCapture(ev.pointerId);
      syncSeek(); draw();
    });
    el.canvas.addEventListener("pointermove", (ev) => {
      if (dragFrom === null) return;
      const r = el.canvas.getBoundingClientRect();
      const x = Math.max(KEY_W, ev.clientX - r.left);
      const t = st.view + ((x - KEY_W) / (r.width - KEY_W)) * st.secondsPerScreen;
      st.loop = [Math.min(dragFrom, t), Math.max(dragFrom, t)];
      el.loop.classList.toggle("is-on", st.loop[1] - st.loop[0] > 0.2);
      draw();
    });
    const endDrag = () => {
      if (dragFrom !== null && st.loop && st.loop[1] - st.loop[0] < 0.2) st.loop = null;
      dragFrom = null;
    };
    el.canvas.addEventListener("pointerup", endDrag);
    el.canvas.addEventListener("pointercancel", endDrag);

    // ----------------------------------------------------------- controls
    el.play.addEventListener("click", () => setPlaying(!st.playing));
    el.seek.addEventListener("input", () => {
      st.playhead = Number(el.seek.value); follow(); syncSeek(); draw();
      if (st.playing) { stopSound(); startSound(); }   // the sound has to jump too
    });
    el.speed.addEventListener("change", () => {
      st.speed = Number(el.speed.value);
      if (st.playing) { stopSound(); startSound(); }
    });
    el.zoom.addEventListener("input", () => {
      st.secondsPerScreen = Number(el.zoom.value); draw();
      if (opts.setPref) opts.setPref("zoom", st.secondsPerScreen);
    });
    el.loop.addEventListener("click", () => {
      st.loop = null; el.loop.classList.remove("is-on"); draw();
    });
    el.file.addEventListener("change", () => {
      const f = el.file.files && el.file.files[0];
      if (!f) { el.title.textContent = "沒有選到檔案"; return; }
      el.title.textContent = `讀取 ${f.name}（${Math.round(f.size / 1024)} KB）…`;
      const rd = new FileReader();
      // A blank canvas with no explanation is the worst possible answer: a read
      // error, an empty file, a file that is not a session log and a bug in
      // here all looked exactly the same (2026-09-09).
      rd.onerror = () => {
        el.title.textContent = `讀不到 ${f.name}：${(rd.error && rd.error.name) || "unknown"}`;
      };
      rd.onload = () => {
        try { api.loadEvents(parseLog(String(rd.result)), f.name); }
        catch (e) { el.title.textContent = `${f.name} 解析失敗：${e.message}`; }
      };
      rd.readAsText(f);
      el.file.value = "";
    });
    // The engine's own recordings, listed by the engine. Reviewing the take
    // you just played should be one click, not a file dialog opened with both
    // hands still on the keyboard.
    // Populated when the roll opens, NOT only on focus: filling the list from
    // the focus event is too late - the browser has already drawn the dropdown
    // by then, so the first click showed nothing but the placeholder and the
    // recordings looked as if they were not there (2026-09-09). Focus still
    // refreshes it, for a take recorded since the panel was opened.
    el.logs.addEventListener("focus", () => api.refreshLogs());
    el.logs.addEventListener("change", () => {
      const name = el.logs.value;
      if (!name) return;
      el.title.textContent = "載入中…";
      fetch("/api/log/" + encodeURIComponent(name))
        .then((r) => (r.ok ? r.text() : Promise.reject(new Error(r.status))))
        .then((txt) => api.loadEvents(parseLog(txt), name))
        .catch((e) => { el.title.textContent = `載入失敗：${e.message}`; });
    });

    el.live.addEventListener("click", () => api.setLive(!st.live));

    window.addEventListener("resize", schedule);

    // ---------------------------------------------------------------- api
    const api = {
      loadEvents(rows, name) {
        const built = buildNotes(rows);
        st.notes = built.notes;
        st.span = spanOf(st.notes);
        st.view = st.span.t0;
        st.playhead = st.span.t0;
        // the zoom the player last chose, if they chose one; otherwise a quarter
        // of the take, which is a readable default for a first look
        const saved = opts.pref && opts.pref("zoom");
        st.secondsPerScreen = saved || Math.min(120, Math.max(8, (st.span.t1 - st.span.t0) / 4));
        el.zoom.min = 1; el.zoom.max = 600; el.zoom.value = st.secondsPerScreen;
        st.hidden.clear();
        setPlaying(false);
        renderLanes();
        syncSeek();
        draw();
        const gen = st.notes.filter((n) => !n.human).length;
        if (!st.notes.length) {
          // Say WHY there is nothing. An empty canvas that explains itself is a
          // different thing from one that just sits there.
          const bad = (rows && rows._bad) || 0;
          const n = (rows && rows.length) || 0;
          el.title.textContent = !n
            ? `${name || "這個檔案"} 是空的，或不是一份 session log（讀到 0 行）`
            : `${name || "這個檔案"} 讀到 ${n} 行，裡面沒有任何音` +
              (bad ? `，另有 ${bad} 行看不懂` : "") +
              " —— 選到的可能不是 data/logs/mie/ 下的 session 檔";
          return built;
        }
        let msg = `${name || "log"}：${st.notes.length - gen} 個你彈的、${gen} 個引擎的`;
        if (rows && rows._bad) msg += `　（${rows._bad} 行看不懂，略過）`;
        if (built.noLength) {
          // an old log, recorded before `gen` carried a length
          msg += `　⚠ ${built.noLength} 個音沒有長度（2026-09-09 之前的 log），畫成短棒`;
        } else if (built.unpaired) {
          msg += `　${built.unpaired} 個音沒有對應的釋放，用排程長度`;
        }
        el.title.textContent = msg;
        return built;
      },
      pushEvent(e) {
        if (!st.live) return;
        if (e.type === "human" || e.type === "human_off" || e.type === "gen" || e.type === "off") {
          st.liveRows.push(e);
          st.liveDirty = true;
        }
      },
      setLive(on) {
        if (on) { setPlaying(false); }      // stopPlaying stops the sound too
        st.live = on;
        el.live.classList.toggle("is-on", on);
        el.live.textContent = on ? "● 即時" : "○ 即時";
        if (on) {
          st.liveRows = [];
          st.liveDirty = false;
          setPlaying(false);
          st.liveTimer = setInterval(() => {
            if (!st.liveDirty) return;
            st.liveDirty = false;
            const built = buildNotes(st.liveRows);
            st.notes = built.notes;
            const s = spanOf(st.notes);
            // the pitch window may only GROW while live, or the picture jumps
            // every time a lane happens to fall silent
            st.span = { lo: Math.min(st.span.lo, s.lo), hi: Math.max(st.span.hi, s.hi),
                        t0: 0, t1: Math.max(s.t1, st.playhead) };
            st.playhead = s.t1;
            follow();
            renderLanes();
            syncSeek();
            draw();
          }, 250);
        } else if (st.liveTimer) {
          clearInterval(st.liveTimer);
          st.liveTimer = 0;
        }
      },
      /** The engine just closed a segment: list it, and put it on screen. */
      showSaved(name) {
        api.refreshLogs();
        el.logs.value = name;
        el.logs.dispatchEvent(new Event("change"));
      },
      /** The engine cannot make a sound right now: say so on the button.
       *
       * Without this the roll went on scrolling with the speaker lit after a
       * PANIC, and every press was silently refused - the picture moving while
       * nothing comes out is exactly the state this panel exists to prevent.
       */
      engineState(s) {
        const dead = !!(s && (s.panicked || s.bypass));
        if (dead === st.engineDead) return;
        st.engineDead = dead;
        el.sound.disabled = dead;
        if (dead) {
          if (st.sound) { st.sound = false; el.sound.classList.remove("is-on"); }
          el.sound.textContent = s.panicked ? "🔇 PANIC 中" : "🔇 BYPASS 中";
          el.sound.title = s.panicked
            ? "引擎在 PANIC 狀態，回放不會發聲。按 RESUME 之後才能開"
            : "目前是 BYPASS，引擎不發聲，回放也不會";
        } else {
          el.sound.textContent = "🔇 靜音回放";
          el.sound.title = "播放時把這一段真的送回樂器。預設關著——按下去會發出聲音";
        }
      },
      refreshLogs() {
        fetch("/api/logs").then((r) => r.json()).then((list) => {
          const cur = el.logs.value;
          el.logs.innerHTML = '<option value="">載入錄音…</option>';
          list.forEach((f) => {
            const o = document.createElement("option");
            o.value = f.name;
            // the timestamp is the useful half of the filename
            const m = /^session-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/.exec(f.name);
            const when = m ? `${m[2]}/${m[3]} ${m[4]}:${m[5]}:${m[6]}` : f.name;
            // the note count, not the byte count: a session that ran for six
            // minutes without anyone playing is a big file full of snapshots,
            // and looks from its size like the take you were after
            o.textContent = f.human === undefined
              ? `${when}  ${Math.round(f.bytes / 1024)} KB`
              : (f.human ? `${when}  ${f.human} 音 / 引擎 ${f.gen}`
                         : `${when}  （沒有彈奏）`);
            if (f.human === 0) o.style.opacity = "0.55";
            el.logs.appendChild(o);
          });
          el.logs.value = cur;
        }).catch(() => { /* served from somewhere that is not the engine */ });
      },
      redraw: schedule,
      _advance: advance,          // for QA: drive the transport without a clock
      _setPlaying: setPlaying,
      _state: st,
    };
    st.liveRows = [];
    // the zoom the player last chose, applied before anything is loaded so the
    // panel opens looking the way they left it rather than at a default
    const savedZoom = opts.pref && opts.pref("zoom");
    if (savedZoom) { st.secondsPerScreen = savedZoom; el.zoom.value = savedZoom; }
    api.setLive(false);
    api.refreshLogs();
    draw();
    return api;
  }

  window.MieRoll = { create, buildNotes, hueFor, noteName };
})();
