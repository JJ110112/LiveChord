/**
 * LiveChord interactive tutorial tour.
 *
 * Shared by the homepage and player. The page scripts only call
 * LiveChordTutorial.maybeStart(page); this module owns positioning,
 * persistence, replay, and cross-page resume.
 */
(function () {
  "use strict";

  const VERSION = 1;
  const DONE_KEY = "livechord_tutorial_v1_done";
  const DISMISSED_KEY = "livechord_tutorial_v1_dismissed_at";
  const RESUME_KEY = "livechord_tutorial_resume";
  const FORCE_KEY = "livechord_tutorial_force";
  const DEMO_HASH = "9d19747b402b";

  const FALLBACK = {
    "tutorial.menu": "Tutorial",
    "tutorial.next": "Next",
    "tutorial.back": "Back",
    "tutorial.skip": "Skip",
    "tutorial.done": "Done",
    "tutorial.open_demo_continue": "Open demo and continue",
    "tutorial.progress": "{current}/{total}",
    "tutorial.home_upload.title": "Pick local audio",
    "tutorial.home_upload.body": "Choose a local audio file here. LiveChord analyzes it and builds chords, beats, and practice views.",
    "tutorial.home_demo.title": "Try a sample first",
    "tutorial.home_demo.body": "Tap 사랑의 빈도 (Love Frequency) if you want to see the player before uploading anything.",
    "tutorial.player_chords.title": "Chord timeline",
    "tutorial.player_chords.body": "The left panel follows the song and shows chord names, chord tones, tabs, or number notation as the music moves.",
    "tutorial.player_instrument.title": "Instrument practice view",
    "tutorial.player_instrument.body": "The right panel shows the current instrument view: waterfall and keys for piano, or hand/fingering areas for guitar, ukulele, accordion, and arranger keyboard.",
    "tutorial.player_instrument.piano": "The piano view pairs the waterfall with the keyboard so you can see what is coming and where it lands.",
    "tutorial.player_instrument.guitar": "The guitar view separates left-hand chord shapes from right-hand strum or picking patterns.",
    "tutorial.player_instrument.ukulele": "The ukulele view separates left-hand chord shapes from right-hand rhythm and picking cues.",
    "tutorial.player_instrument.accordion": "The accordion view shows left-hand bass buttons and the right-hand keyboard waterfall.",
    "tutorial.player_instrument.arranger": "The arranger keyboard view combines the waterfall with the keyboard split for practice.",
    "tutorial.player_playback.title": "Playback",
    "tutorial.player_playback.body": "Use these controls to play, pause, or restart. The chord timeline and practice view stay synced to the audio.",
    "tutorial.player_switch.title": "Switch instruments",
    "tutorial.player_switch.body": "Switch between piano, guitar, ukulele, accordion, and arranger keyboard. The right panel changes to match your instrument.",
    "tutorial.player_teaching.title": "Practice settings",
    "tutorial.player_teaching.body": "Choose left hand, right hand, both hands, fingering, chord tones, and AI accompaniment. Start simple, then add complexity.",
    "tutorial.player_tools.title": "Practice helpers",
    "tutorial.player_tools.body": "Speed, loop, and transpose are the everyday practice tools: slow down, repeat a small phrase, then return to tempo."
  };

  const HOME_STEPS = [
    {
      id: "home-upload",
      page: "home",
      target: () => document.querySelector("#betaBrowseLocalBtn"),
      placement: "right",
      titleKey: "tutorial.home_upload.title",
      bodyKey: "tutorial.home_upload.body"
    },
    {
      id: "home-demo-love-frequency",
      page: "home",
      target: () => document.querySelector(`.demo-card[data-hash="${DEMO_HASH}"]`),
      placement: "top",
      titleKey: "tutorial.home_demo.title",
      bodyKey: "tutorial.home_demo.body",
      action: "open-demo"
    }
  ];

  const PLAYER_STEPS = [
    {
      id: "player-chords",
      page: "player",
      target: () => document.querySelector("#chordRibbonPanel"),
      placement: "right",
      titleKey: "tutorial.player_chords.title",
      bodyKey: "tutorial.player_chords.body"
    },
    {
      id: "player-instrument",
      page: "player",
      target: _activeInstrumentTarget,
      placement: "left",
      titleKey: "tutorial.player_instrument.title",
      body: _activeInstrumentBody
    },
    {
      id: "player-playback",
      page: "player",
      target: () => document.querySelector("#tbPlayback"),
      placement: "top",
      titleKey: "tutorial.player_playback.title",
      bodyKey: "tutorial.player_playback.body"
    },
    {
      id: "player-instrument-switch",
      page: "player",
      target: () => document.querySelector("#tbInstrument"),
      placement: "top",
      titleKey: "tutorial.player_switch.title",
      bodyKey: "tutorial.player_switch.body"
    },
    {
      id: "player-teaching",
      page: "player",
      target: () => document.querySelector("#tbTeaching"),
      placement: "top",
      titleKey: "tutorial.player_teaching.title",
      bodyKey: "tutorial.player_teaching.body"
    },
    {
      id: "player-practice-tools",
      page: "player",
      targets: ["#tbSpeed", "#tbLoop", "#tbTranspose"],
      placement: "top",
      titleKey: "tutorial.player_tools.title",
      bodyKey: "tutorial.player_tools.body"
    }
  ];

  let _root = null;
  let _spotlight = null;
  let _popover = null;
  let _active = false;
  let _page = "";
  let _source = "auto";
  let _steps = [];
  let _index = 0;
  let _lastFocus = null;
  let _autoStarted = {};
  let _resizeTimer = 0;

  function _t(key, vars, fallback) {
    const fb = fallback || FALLBACK[key] || key;
    const out = window.LiveChordI18n && window.LiveChordI18n.t
      ? window.LiveChordI18n.t(key, vars)
      : fb;
    if (out === key) return _interpolate(fb, vars);
    return out;
  }

  function _interpolate(s, vars) {
    if (!vars) return s;
    return String(s).replace(/\{(\w+)\}/g, (m, k) =>
      Object.prototype.hasOwnProperty.call(vars, k) ? String(vars[k]) : m
    );
  }

  function _getStorage(storage, key) {
    try { return storage.getItem(key); } catch (_) { return null; }
  }

  function _setStorage(storage, key, val) {
    try { storage.setItem(key, val); } catch (_) {}
  }

  function _removeStorage(storage, key) {
    try { storage.removeItem(key); } catch (_) {}
  }

  function _isDone() {
    return _getStorage(localStorage, DONE_KEY) === "true";
  }

  function _emit(name, payload) {
    const data = Object.assign({ version: VERSION }, payload || {});
    try {
      if (window.LiveChordAnalytics) window.LiveChordAnalytics.track(name, data);
    } catch (_) {}
    try {
      if (window.API && API.trackEvent) API.trackEvent(name, data);
    } catch (_) {}
  }

  function _activeInstrumentTarget() {
    const ids = [
      "chordDisplayPiano",
      "chordDisplayGuitar",
      "chordDisplayUkulele",
      "chordDisplayAccordion",
      "chordDisplayArranger"
    ];
    for (const id of ids) {
      const el = document.getElementById(id);
      if (_isUsableElement(el)) return el;
    }
    return document.querySelector("#instrumentPanel");
  }

  function _activeInstrumentBody() {
    const active = _activeInstrumentTarget();
    const id = active && active.id;
    if (id === "chordDisplayPiano") return _t("tutorial.player_instrument.piano");
    if (id === "chordDisplayGuitar") return _t("tutorial.player_instrument.guitar");
    if (id === "chordDisplayUkulele") return _t("tutorial.player_instrument.ukulele");
    if (id === "chordDisplayAccordion") return _t("tutorial.player_instrument.accordion");
    if (id === "chordDisplayArranger") return _t("tutorial.player_instrument.arranger");
    return _t("tutorial.player_instrument.body");
  }

  function _isUsableElement(el) {
    if (!el || !el.getClientRects || el.getClientRects().length === 0) return false;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 1 && rect.height > 1;
  }

  function _resolveTarget(step) {
    if (step.targets && step.targets.length) {
      const elements = step.targets
        .map(sel => document.querySelector(sel))
        .filter(_isUsableElement);
      if (!elements.length) return null;
      const rect = _unionRects(elements.map(el => el.getBoundingClientRect()));
      return { element: elements[0], rect };
    }
    const element = typeof step.target === "function" ? step.target() : document.querySelector(step.target);
    if (!_isUsableElement(element)) return null;
    return { element, rect: element.getBoundingClientRect() };
  }

  function _unionRects(rects) {
    const left = Math.min(...rects.map(r => r.left));
    const top = Math.min(...rects.map(r => r.top));
    const right = Math.max(...rects.map(r => r.right));
    const bottom = Math.max(...rects.map(r => r.bottom));
    return { left, top, right, bottom, width: right - left, height: bottom - top };
  }

  function _stepsFor(page) {
    return page === "player" ? PLAYER_STEPS : HOME_STEPS;
  }

  function _firstAvailableIndex(from, dir) {
    if (!_steps.length) return -1;
    let i = Math.max(0, Math.min(from, _steps.length - 1));
    while (i >= 0 && i < _steps.length) {
      const step = _steps[i];
      if (_resolveTarget(step)) return i;
      _emit("tutorial_target_missing", {
        page: _page,
        source: _source,
        step_id: step.id,
        step_index: i + 1
      });
      i += dir || 1;
    }
    return -1;
  }

  function _hasAvailableAfter(idx) {
    for (let i = idx + 1; i < _steps.length; i++) {
      if (_resolveTarget(_steps[i])) return true;
    }
    return false;
  }

  function _hasAvailableBefore(idx) {
    for (let i = idx - 1; i >= 0; i--) {
      if (_resolveTarget(_steps[i])) return true;
    }
    return false;
  }

  function _ensureRoot() {
    if (_root) return;
    _root = document.createElement("div");
    _root.id = "lcTutorialRoot";
    _root.className = "lc-tutorial-root";
    _root.hidden = true;

    _spotlight = document.createElement("div");
    _spotlight.className = "lc-tutorial-spotlight";

    _popover = document.createElement("div");
    _popover.className = "lc-tutorial-popover lc-panel";
    _popover.setAttribute("role", "dialog");
    _popover.setAttribute("aria-modal", "false");

    _root.appendChild(_spotlight);
    _root.appendChild(_popover);
    document.body.appendChild(_root);

    _popover.addEventListener("click", (e) => {
      const action = e.target && e.target.closest("[data-tutorial-action]");
      if (!action) return;
      e.preventDefault();
      _handleAction(action.getAttribute("data-tutorial-action"));
    });
  }

  function _renderStep(step, target) {
    _ensureRoot();
    const isLast = !_hasAvailableAfter(_index);
    const canBack = _hasAvailableBefore(_index);
    const primaryKey = step.action === "open-demo"
      ? "tutorial.open_demo_continue"
      : (isLast ? "tutorial.done" : "tutorial.next");
    const progress = _t("tutorial.progress", {
      current: _index + 1,
      total: _steps.length
    });
    const body = typeof step.body === "function" ? step.body() : _t(step.bodyKey);

    _popover.innerHTML = "";
    const kicker = document.createElement("div");
    kicker.className = "lc-tutorial-kicker";
    kicker.textContent = progress;

    const title = document.createElement("div");
    title.className = "lc-title";
    title.id = "lcTutorialTitle";
    title.textContent = _t(step.titleKey);

    const content = document.createElement("p");
    content.className = "lc-tutorial-body";
    content.textContent = body;

    const actions = document.createElement("div");
    actions.className = "lc-tutorial-actions";
    actions.innerHTML = `
      <button class="tb-popup-btn lc-tutorial-skip" type="button" data-tutorial-action="skip"></button>
      <button class="tb-popup-btn lc-tutorial-back" type="button" data-tutorial-action="back"></button>
      <button class="tb-popup-btn active lc-tutorial-primary" type="button" data-tutorial-action="primary"></button>
    `;
    actions.querySelector(".lc-tutorial-skip").textContent = _t("tutorial.skip");
    const back = actions.querySelector(".lc-tutorial-back");
    back.textContent = _t("tutorial.back");
    back.hidden = !canBack;
    actions.querySelector(".lc-tutorial-primary").textContent = _t(primaryKey);

    _popover.appendChild(kicker);
    _popover.appendChild(title);
    _popover.appendChild(content);
    _popover.appendChild(actions);
    _popover.setAttribute("aria-labelledby", "lcTutorialTitle");
    _root.hidden = false;
    _position(target.rect, step.placement);

    const primary = _popover.querySelector(".lc-tutorial-primary");
    if (primary) primary.focus({ preventScroll: true });
  }

  function _position(rect, placement) {
    const pad = 6;
    const spot = {
      top: Math.max(4, rect.top - pad),
      left: Math.max(4, rect.left - pad),
      width: rect.width + pad * 2,
      height: rect.height + pad * 2
    };
    spot.width = Math.min(window.innerWidth - spot.left - 4, spot.width);
    spot.height = Math.min(window.innerHeight - spot.top - 4, spot.height);
    Object.assign(_spotlight.style, {
      top: `${spot.top}px`,
      left: `${spot.left}px`,
      width: `${spot.width}px`,
      height: `${spot.height}px`
    });

    const mobileBottom = window.matchMedia("(max-width: 640px) and (orientation: portrait)").matches;
    _popover.classList.toggle("is-mobile", mobileBottom);
    _popover.style.visibility = "hidden";
    _popover.style.top = "0";
    _popover.style.left = "0";
    _popover.style.right = "";
    _popover.style.bottom = "";

    if (mobileBottom) {
      _popover.style.visibility = "";
      _popover.style.top = "";
      _popover.style.left = "";
      _popover.style.right = "12px";
      _popover.style.bottom = "calc(12px + env(safe-area-inset-bottom))";
      return;
    }

    const pop = _popover.getBoundingClientRect();
    const margin = 8;
    const gap = 12;
    const preferred = placement || "bottom";
    const order = [preferred, _opposite(preferred), "right", "left", "bottom", "top"]
      .filter((v, i, arr) => arr.indexOf(v) === i);
    let picked = null;
    for (const pos of order) {
      const next = _coordsFor(pos, rect, pop, gap);
      if (_fits(next, pop, margin)) {
        picked = next;
        break;
      }
    }
    if (!picked) picked = _coordsFor(preferred, rect, pop, gap);
    picked.left = Math.max(margin, Math.min(picked.left, window.innerWidth - pop.width - margin));
    picked.top = Math.max(margin, Math.min(picked.top, window.innerHeight - pop.height - margin));
    _popover.style.left = `${picked.left}px`;
    _popover.style.top = `${picked.top}px`;
    _popover.style.visibility = "";
  }

  function _opposite(pos) {
    return { top: "bottom", bottom: "top", left: "right", right: "left" }[pos] || "top";
  }

  function _coordsFor(pos, rect, pop, gap) {
    if (pos === "right") {
      return { left: rect.right + gap, top: rect.top + rect.height / 2 - pop.height / 2 };
    }
    if (pos === "left") {
      return { left: rect.left - pop.width - gap, top: rect.top + rect.height / 2 - pop.height / 2 };
    }
    if (pos === "top") {
      return { left: rect.left + rect.width / 2 - pop.width / 2, top: rect.top - pop.height - gap };
    }
    return { left: rect.left + rect.width / 2 - pop.width / 2, top: rect.bottom + gap };
  }

  function _fits(pos, pop, margin) {
    return pos.left >= margin
      && pos.top >= margin
      && pos.left + pop.width <= window.innerWidth - margin
      && pos.top + pop.height <= window.innerHeight - margin;
  }

  function _delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  async function _scrollTargetIntoView(target) {
    const el = target && target.element;
    if (!el) return;
    // Keep coach-mark geometry deterministic. Smooth scroll can still be in
    // flight when the fixed spotlight is measured, leaving the ring behind.
    const behavior = "auto";
    const scroller = el.closest && el.closest(".horizontal-scroll");
    if (scroller) {
      const left = el.offsetLeft - (scroller.clientWidth - el.clientWidth) / 2;
      try { scroller.scrollTo({ left: Math.max(0, left), behavior }); } catch (_) { scroller.scrollLeft = Math.max(0, left); }
    }
    try { el.scrollIntoView({ block: "center", inline: "center", behavior }); } catch (_) {}
    await _delay(40);
  }

  function _blockingOverlayVisible() {
    const ids = ["detectOverlay", "localAudioPrompt", "instrumentPickerPanel", "bugReportDialog"];
    return ids.some(id => {
      const el = document.getElementById(id);
      return _isUsableElement(el);
    });
  }

  async function _waitForQuiet(maxMs) {
    const start = Date.now();
    while (_blockingOverlayVisible() && Date.now() - start < maxMs) {
      await _delay(250);
    }
  }

  function _closeFloatingChrome() {
    document.querySelectorAll(".tb-item.open").forEach(item => item.classList.remove("open"));
    const nav = document.querySelector(".header-nav.mobile-open");
    if (nav) nav.classList.remove("mobile-open");
    const gear = document.getElementById("btnHeaderSettings");
    if (gear) gear.setAttribute("aria-expanded", "false");
  }

  async function _showCurrent(dir) {
    if (!_active) return;
    _closeFloatingChrome();
    const next = _firstAvailableIndex(_index, dir || 1);
    if (next < 0) {
      close({ silent: true });
      return;
    }
    _index = next;
    const step = _steps[_index];
    let target = _resolveTarget(step);
    await _scrollTargetIntoView(target);
    target = _resolveTarget(step);
    if (!target) {
      _emit("tutorial_target_missing", {
        page: _page,
        source: _source,
        step_id: step.id,
        step_index: _index + 1
      });
      _index += dir || 1;
      _showCurrent(dir || 1);
      return;
    }
    _renderStep(step, target);
    _emit("tutorial_step_view", {
      page: _page,
      source: _source,
      step_id: step.id,
      step_index: _index + 1
    });
  }

  function _handleAction(action) {
    if (!_active) return;
    const step = _steps[_index];
    if (action === "skip") {
      close({ dismissed: true });
      return;
    }
    if (action === "back") {
      _index -= 1;
      _showCurrent(-1);
      return;
    }
    if (action !== "primary") return;

    _emit("tutorial_step_next", {
      page: _page,
      source: _source,
      step_id: step.id,
      step_index: _index + 1
    });

    if (step.action === "open-demo") {
      _setStorage(sessionStorage, RESUME_KEY, "player-core");
      _setStorage(sessionStorage, FORCE_KEY, "1");
      _setStorage(sessionStorage, "livechord_from_demo", "1");
      window.location.href = `/player?hash=${encodeURIComponent(DEMO_HASH)}`;
      return;
    }

    if (!_hasAvailableAfter(_index)) {
      close({ completed: true });
      return;
    }
    _index += 1;
    _showCurrent(1);
  }

  async function start(page, opts) {
    opts = opts || {};
    const force = !!opts.force;
    if (_active) close({ silent: true });
    if (!force && _isDone()) return;

    _page = page === "player" ? "player" : "home";
    _source = opts.source || (force ? "gear" : "auto");
    _steps = _stepsFor(_page);
    _index = 0;
    if (opts.fromStep) {
      const found = _steps.findIndex(step => step.id === opts.fromStep);
      if (found >= 0) _index = found;
    }
    _active = true;
    _lastFocus = document.activeElement;
    _ensureRoot();
    _root.hidden = false;
    document.addEventListener("keydown", _onKeydown, true);
    await _waitForQuiet(8000);
    _emit("tutorial_start", { page: _page, source: _source });
    _showCurrent(1);
  }

  function maybeStart(page) {
    const normalized = page === "player" ? "player" : "home";
    const resume = normalized === "player" && _getStorage(sessionStorage, RESUME_KEY);
    const force = _getStorage(sessionStorage, FORCE_KEY) === "1";
    if (!resume && !force && _isDone()) return;
    if (_autoStarted[normalized] && !resume && !force) return;
    _autoStarted[normalized] = true;
    if (resume) _removeStorage(sessionStorage, RESUME_KEY);
    if (force) _removeStorage(sessionStorage, FORCE_KEY);
    const fromStep = resume ? "player-chords" : null;
    const source = resume ? "home-demo" : (force ? "gear" : "auto");
    setTimeout(() => start(normalized, { force: !!(resume || force), fromStep, source }), 180);
  }

  function close(opts) {
    opts = opts || {};
    if (!_active && !opts.silent) return;
    const completed = !!opts.completed;
    const dismissed = !!opts.dismissed;
    const page = _page;
    const step = _steps[_index];
    _active = false;
    if (_root) _root.hidden = true;
    document.removeEventListener("keydown", _onKeydown, true);
    if (completed || dismissed) {
      _setStorage(localStorage, DONE_KEY, "true");
      if (dismissed) _setStorage(localStorage, DISMISSED_KEY, new Date().toISOString());
      _removeStorage(sessionStorage, RESUME_KEY);
      _removeStorage(sessionStorage, FORCE_KEY);
    }
    if (completed) _emit("tutorial_complete", { page, source: _source });
    if (dismissed) {
      _emit("tutorial_skip", {
        page,
        source: _source,
        step_id: step && step.id,
        step_index: _index + 1
      });
    }
    if (_lastFocus && typeof _lastFocus.focus === "function") {
      try { _lastFocus.focus({ preventScroll: true }); } catch (_) {}
    }
  }

  function _onKeydown(e) {
    if (!_active) return;
    if (e.key === "Escape") {
      e.preventDefault();
      close({ dismissed: true });
    }
  }

  function refreshPosition() {
    if (!_active) return;
    const step = _steps[_index];
    const target = step && _resolveTarget(step);
    if (target) _position(target.rect, step.placement);
  }

  function _bindEntrypoints() {
    const home = document.getElementById("btnStartTutorialHome");
    if (home && home.dataset.tutorialBound !== "1") {
      home.dataset.tutorialBound = "1";
      home.addEventListener("click", (e) => {
        e.preventDefault();
        start("home", { force: true, source: "gear" });
      });
    }
    const player = document.getElementById("btnStartTutorialPlayer");
    if (player && player.dataset.tutorialBound !== "1") {
      player.dataset.tutorialBound = "1";
      player.addEventListener("click", (e) => {
        e.preventDefault();
        start("player", { force: true, source: "gear" });
      });
    }
  }

  window.addEventListener("resize", () => {
    clearTimeout(_resizeTimer);
    _resizeTimer = setTimeout(refreshPosition, 100);
  });
  window.addEventListener("orientationchange", () => setTimeout(refreshPosition, 180));
  document.addEventListener("livechord:langchange", () => { if (_active) _showCurrent(0); });
  document.addEventListener("livechord:i18nready", () => { _bindEntrypoints(); if (_active) _showCurrent(0); });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _bindEntrypoints);
  } else {
    _bindEntrypoints();
  }

  window.LiveChordTutorial = { maybeStart, start, close, refreshPosition };
})();
