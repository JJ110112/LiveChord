/* LiveChord MIE panel (plan §9, Phase 1: top bar + meters + edge list + event stream).
 * Talks to the engine over the WebSocket on whatever port served this page
 * (falling back to 8810 when the LiveChord backend on 8800 served it). Served by
 * the engine itself (http://127.0.0.1:8810/mie) or by the LiveChord backend (/mie). */
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  // Whoever served this page is the engine, unless the page came from the
  // LiveChord backend on 8800 - which serves /mie as a convenience and has no
  // engine of its own. Hardcoding 8810 meant `--port` silently did not work:
  // the engine listened where you asked and the panel talked to 8810 anyway.
  const WS_URL = (location.port && location.port !== "8800")
    ? `ws://${location.host}/ws`
    : "ws://127.0.0.1:8810/ws";
  const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const nn = (n) => `${NOTE_NAMES[n % 12]}${Math.floor(n / 12) - 1}`;

  let ws = null, connected = false, lastSnap = null, reconnectTimer = null;
  let escArm = 0;
  const streamMax = 60;
  const edgeEls = new Map();
  const instEls = new Map();

  function send(obj) {
    if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
  }

  // ---------------------------------------------------------------- connect
  function connect() {
    try { ws = new WebSocket(WS_URL); } catch (e) { scheduleReconnect(); return; }
    ws.onopen = () => { connected = true; $("#mieConn").classList.add("on"); loadScenes(); };
    ws.onclose = () => { connected = false; $("#mieConn").classList.remove("on"); scheduleReconnect(); };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
    ws.onmessage = (m) => {
      let msg; try { msg = JSON.parse(m.data); } catch (e) { return; }
      if (msg.type === "hello") { renderSnapshot(msg.snapshot, true); }
      else if (msg.type === "state") { renderSnapshot(msg, false); }
      else if (msg.type === "event") { msg.events.forEach(pushEvent); }
    };
  }
  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => { reconnectTimer = null; connect(); }, 1500);
  }
  async function loadScenes() {
    try {
      const r = await fetch("/api/scenes"); if (!r.ok) return;
      const list = await r.json();
      const sel = $("#mieSceneSel"); sel.innerHTML = "";
      list.forEach((s) => { const o = document.createElement("option"); o.value = s.id; o.textContent = `${s.id} ${s.name}`; sel.appendChild(o); });
      if (lastSnap) sel.value = lastSnap.scene.id;
    } catch (e) { /* served from the NUC: no scene list, keep the current one */ }
  }

  // ---------------------------------------------------------------- render
  function pct(el, v) { el.style.width = `${Math.max(0, Math.min(100, v * 100))}%`; }
  function renderSnapshot(s, first) {
    lastSnap = s;
    const st = s.state;
    $("#mieModeSel").value = s.mode;
    const modeEl = $("#mieMode");
    modeEl.classList.toggle("bypass", !!s.bypass);
    modeEl.classList.toggle("live", !s.bypass);
    $("#miePanic").classList.toggle("armed", !!s.panicked);
    // A grey BYPASS pill was not loud enough: after a `ws_lost` PANIC the player
    // played 40 seconds into silence without noticing (2026-09-07 20:44).
    const halt = $("#mieHalt");
    halt.hidden = !(s.panicked || s.bypass);
    if (!halt.hidden) {
      $("#mieHaltWhy").textContent = s.panicked
        ? "PANIC 之後不會自己恢復（面板重新整理過也會觸發）。按 RESUME 才會再發聲。"
        : "目前是 BYPASS，直通照常，但引擎不會發聲。";
    }
    $("#mieKey").textContent = st.key + (st.key_source === "player" ? " ▶" : st.key_source === "inferred" ? " ~" : "");
    $("#mieChord").textContent = st.chord || "—";
    $("#mieTexture").textContent = TEX[st.texture] || st.texture || "—";
    $("#mieHands").textContent = (st.lh && st.lh.length) ? `雙手 ${st.lh.length}+${st.rh.length}` : "";
    $("#mieBpm").textContent = st.bpm;
    $("#mieClock").textContent = st.clock + (st.pulse_conf ? ` ${Math.round(st.pulse_conf * 100)}%` : "");
    $("#mieBpm").parentElement.title = st.pulse_bpm
      ? `脈動推估 ${st.pulse_bpm} BPM，信心 ${Math.round(st.pulse_conf * 100)}%（自由速度的演奏本來就沒有明確脈動）`
      : "尚未從演奏推估出脈動";
    $("#mieBeat").textContent = `${st.beat}/${st.beats_per_bar}`;
    window.__mieSnap = s;   // a console handle: the last thing the engine said
    renderStyles(s.style);
    renderAdvice(s.advice || []);
    if (s.last_control) $("#mieUc4").textContent = `${s.last_control.key} = ${s.last_control.val}`;
    pct($("#mDensity"), st.density / 8); $("#vDensity").textContent = st.density.toFixed(1) + "/s";
    pct($("#mEnergy"), st.energy); $("#vEnergy").textContent = Math.round(st.energy * 100) + "%";
    pct($("#mRestraint"), s.restraint); $("#vRestraint").textContent = s.restraint.toFixed(2);
    pct($("#mSilence"), Math.min(1, st.silence_s / 8));
    const holding = (st.held || []).length + (st.sustained || []).length;
    $("#vSilence").textContent = holding ? `按住 ${holding}` + ((st.pedal || []).length ? " ♪" : "") : st.silence_s.toFixed(1) + " s";
    pct($("#mVel"), st.vel_mean / 127); $("#vVel").textContent = Math.round(st.vel_mean);
    const g = s.scene.global || {};
    syncSlider("gMaster", g.master_gain === undefined ? 1 : g.master_gain, 2);
    syncSlider("gTension", g.tension || 0, 2);
    syncSlider("gDensity", g.density === undefined ? 0.5 : g.density, 2);
    // the slider is continuous; the engine lands on the nearest musical ratio,
    // so show what it actually settled on rather than where the finger is
    syncSlider("gTime", st.time_knob === null || st.time_knob === undefined ? 1 : st.time_knob, 2);
    syncSlider("gProb", g.prob_scale, 2); syncSlider("gChaos", g.chaos, 2); syncSlider("gRestraint", g.restraint, 2);
    if ($("#mieSceneSel").options.length && !$("#mieSceneSel").matches(":focus")) $("#mieSceneSel").value = s.scene.id;
    renderPreset(s.preset);
    const ed = s.edits || {};
    $("#mieSave").classList.toggle("is-unsaved", !!ed.unsaved);
    $("#mieSave").title = ed.unsaved ? `有 ${ed.undo} 項調整還沒寫進 scene 檔` : "把目前所有調整寫回 scene 檔";
    $("#mieUndo").disabled = !ed.undo;
    $("#mieFreeze").classList.toggle("is-frozen", !!s.frozen);
    $("#mieFreeze").textContent = s.frozen ? "解凍" : "凍結";
    $("#mieFreeze").title = s.frozen
      ? "按住中：引擎不再產生新的音，你在這張床上彈。再按一次放開"
      : "按住引擎現在正在響的聲音，讓你在上面繼續彈（空白鍵）";
    document.body.classList.toggle("is-frozen", !!s.frozen);
    renderInstruments(s);
    renderEdges(s);
    renderStats(s);
  }
  function renderPreset(p) {
    if (!p) return;
    const stored = new Set(p.stored || []);
    document.querySelectorAll(".mie-pbtn").forEach((b) => {
      const slot = b.dataset.slot;
      b.classList.toggle("is-on", slot === p.slot);
      // an empty slot is shown faded rather than hidden: you should be able to
      // see that A is free before you decide to put something in it
      b.classList.toggle("is-empty", slot !== "LIVE" && !stored.has(slot));
      b.classList.toggle("is-dirty", slot === p.slot && slot !== "LIVE" && !!p.dirty);
    });
  }

  function syncSlider(id, v, digits) {
    const el = document.getElementById(id);
    if (!el || v === undefined || el.matches(":active")) return;
    el.value = v; document.getElementById(id + "V").textContent = Number(v).toFixed(digits);
  }
  function renderInstruments(s) {
    const box = $("#mieInstruments");
    const st = s.state;
    const activeCh = new Set(st.active_gen.map((a) => a[0]));
    const humanCh = new Set(st.human_chs);
    s.instruments.forEach((inst) => {
      let el = instEls.get(inst.ch);
      if (!el) {
        el = document.createElement("label"); el.className = "mie-inst";
        el.innerHTML = `<input type="checkbox"><span class="dot"></span><span class="ch">CH${inst.ch}</span><span class="nm"></span>`;
        el.querySelector("input").addEventListener("change", (e) => send({ type: "set", path: `inst.${inst.ch}.enabled`, value: e.target.checked }));
        box.appendChild(el); instEls.set(inst.ch, el);
      }
      el.querySelector(".nm").textContent = inst.name;
      const cb = el.querySelector("input"); if (document.activeElement !== cb) cb.checked = inst.enabled;
      el.classList.toggle("active", activeCh.has(inst.ch));
      el.classList.toggle("human", humanCh.has(inst.ch));
      el.title = `${inst.role} · max ${inst.max_voices} voices · ${inst.note_range[0]}–${inst.note_range[1]}`;
    });
  }
  // Which knobs an edge exposes, per algorithm (plan §9.1). Every one of these
  // is a live `set` on the engine, so a lane can be shaped while playing.
  const NUM = (label, key, min, max, step, hint) => ({ label, key, min, max, step, hint });
  // A yes/no setting is a checkbox, not a 0..1 slider. `follow_chord` is stored
  // in the scene as the JSON boolean `true`; the slider could never bind to it,
  // so the box showed EMPTY and every snapshot logged "The specified value
  // 'true' cannot be parsed" - 85 of them in one minute on the QA panel. Worse,
  // dragging it would have written a NUMBER over the boolean.
  const BOOL = (label, key, hint) => ({ label, key, hint, kind: "bool" });
  const COMMON = [
    NUM("機率", "prob", 0, 1, 0.05, "這個手勢被回應的機率"),
    NUM("力度×", "vel_scale", 0, 2, 0.05, "生成音的力度倍率"),
    NUM("移調", "transpose", -24, 24, 1, "半音"),
    NUM("八度", "octave", -3, 3, 1, null),
  ];
  const PARAMS = {
    echo: [
      NUM("回音次數", "repeats", 1, 12, 1, "上限；衰減到 min_vel 以下就停"),
      NUM("間隔(拍)", "delay_beats", 0, 8, 0.25, "每次回音之間隔幾拍"),
      NUM("每趟衰減", "decay", 0.1, 1, 0.05, "第 2 趟起每趟的力度倍率；第 1 趟由「力度×」決定"),
      NUM("最小力度", "min_vel", 1, 64, 1, "低於此值就不再回音"),
      NUM("時值衰減", "dur_decay", 0.3, 1, 0.05, "每次回音變短的比例"),
      NUM("重疊", "max_overlap", 0.5, 4, 0.25, "同音回音允許重疊幾次；1 = 接續不重疊"),
    ],
    follow: [NUM("音程", "interval", -24, 24, 1, "半音，預設 7 = 五度")],
    phrase: [
      NUM("重複次數", "repeats", 1, 8, 1, "整句重播幾次"),
      NUM("句尾間隔(拍)", "phrase_gap_beats", 0.25, 8, 0.25, "多久沒有新音就算一句結束"),
      NUM("句尾間隔(倍)", "phrase_gap_iois", 0.8, 4, 0.1, "相對於你自己的音距：1.5 = 停頓超過你平常音距的 1.5 倍就算一句結束。兩個門檻取大的"),
      NUM("延遲(拍)", "delay_beats", 0, 8, 0.25, "句子結束後多久開始回來"),
      NUM("每趟衰減", "decay", 0.1, 1, 0.05, "第 2 趟起每趟的力度倍率；第 1 趟由「力度×」決定"),
      NUM("時值衰減", "dur_decay", 0.3, 1, 0.05, "每次重播變短的比例；回音變遠也會變短"),
      NUM("最少音數", "min_notes", 1, 8, 1, "太短的不算一句"),
      NUM("最多音數", "max_notes", 2, 16, 1, "只回最後這幾個音"),
      NUM("最小力度", "min_vel", 1, 64, 1, null),
      BOOL("跟和弦移調", "follow_chord", "重播時整句依和弦根音平行移調（Am→Dm 就整句 +5）"),
    ],
    shadow: [NUM("延遲(ms)", "delay_ms", 0, 500, 10, null),
             NUM("最長持續(s)", "max_hold_s", 0.5, 30, 0.5, null)],
    silence: [NUM("等待(s)", "after_s", 0.5, 20, 0.5, "安靜多久才進來"),
              NUM("聲部", "voices", 1, 6, 1, null),
              NUM("力度", "vel", 1, 127, 1, null),
              NUM("持續(s)", "hold_s", 1, 60, 1, null),
              NUM("釋放(拍)", "release_beats", 0, 8, 0.5, "你再彈之後多久淡出"),
              BOOL("跟和弦", "follow_chord", "和弦換了就把這層墊音重新擺到新的和弦上"),
              NUM("低", "low", 21, 108, 1, null), NUM("高", "high", 21, 108, 1, null)],
    sustain: [NUM("等待(s)", "after_s", 0.5, 20, 0.5, "按住多久才開始"),
              NUM("最短(小節)", "every_bars_min", 0.25, 8, 0.25, null),
              NUM("最長(小節)", "every_bars_max", 0.25, 8, 0.25, null),
              NUM("聲部", "voices", 1, 6, 1, null),
              NUM("力度", "vel", 1, 127, 1, null),
              NUM("持續(拍)", "hold_beats", 1, 32, 1, null),
              NUM("釋放(拍)", "release_beats", 0, 8, 0.5, null),
              BOOL("疊在手上面", "above_held", "把這層墊音放在你正按著的音之上（預設開）"),
              NUM("低", "low", 21, 108, 1, null), NUM("高", "high", 21, 108, 1, null)],
  };
  const CHOICES = {
    constraint: ["chord", "function", "scale", "free"],
    align: ["none", "half", "beat", "bar"],
    voice_lead: ["off", "octave", "free"],
    silence_mode: ["sound", "attack"],
    collision: ["octave", "unison", "none"],
  };

  // ------------------------------------------------------------- XY pad
  // A pedal puts one or two knobs on the surface and hides the rest. The panel
  // had it backwards: the first layer carried no control at all, so changing
  // anything meant expanding an edge and facing ten sliders - eight edges read
  // as eighty controls, and the player said they would get lost in it. Each
  // edge now leads with the two settings that decide how it FEELS, on one pad
  // you can move with a single drag. X is its sense of time, Y is how much of
  // it there is. Everything else stays exactly where it was, one click down.
  const PAD = {
    echo:    { x: { key: "delay_beats", min: 0.25, max: 8, step: 0.25, label: "間隔" },
               y: { key: "decay", min: 0.1, max: 0.95, step: 0.05, label: "尾巴" } },
    phrase:  { x: { key: "delay_beats", min: 0.25, max: 8, step: 0.25, label: "延遲" },
               y: { key: "decay", min: 0.1, max: 0.95, step: 0.05, label: "尾巴" } },
    shadow:  { x: { key: "delay_ms", min: 0, max: 500, step: 10, label: "延遲" },
               y: { key: "max_hold_s", min: 0.5, max: 30, step: 0.5, label: "持續" } },
    silence: { x: { key: "after_s", min: 0.5, max: 20, step: 0.5, label: "等待" },
               y: { key: "hold_s", min: 1, max: 60, step: 1, label: "持續" } },
    sustain: { x: { key: "every_bars_min", min: 0.25, max: 8, step: 0.25, label: "間隔" },
               y: { key: "voices", min: 1, max: 6, step: 1, label: "聲部" } },
    follow:  { x: { key: "interval", min: -24, max: 24, step: 1, label: "音程" },
               y: { key: "prob", min: 0, max: 1, step: 0.05, label: "機率" } },
  };

  function padValue(e, ax) {
    const v = e[ax.key];
    return v === undefined ? ax.min : Math.max(ax.min, Math.min(ax.max, Number(v)));
  }

  function makePad(e) {
    const spec = PAD[e.algo];
    if (!spec) return null;
    const el = document.createElement("div");
    el.className = "mie-pad";
    el.title = `橫向 ${spec.x.label}　直向 ${spec.y.label}（拖曳）`;
    el.innerHTML = `<div class="mie-pad-dot"></div><span class="mie-pad-lbl"></span>`;
    const dot = el.querySelector(".mie-pad-dot");
    const lbl = el.querySelector(".mie-pad-lbl");

    el.render = () => {
      const x = padValue(e, spec.x), y = padValue(e, spec.y);
      const fx = (x - spec.x.min) / (spec.x.max - spec.x.min);
      const fy = (y - spec.y.min) / (spec.y.max - spec.y.min);
      dot.style.left = `${fx * 100}%`;
      dot.style.bottom = `${fy * 100}%`;          // up is more, as a fader is
      lbl.textContent = `${spec.x.label} ${round(x, spec.x.step)} · ${spec.y.label} ${round(y, spec.y.step)}`;
    };

    const round = (v, step) => {
      const s = String(step);
      const dp = s.includes(".") ? s.split(".")[1].length : 0;
      return Number(v).toFixed(dp);
    };

    let last = 0;
    let sentX, sentY;                             // what the engine already has
    const move = (ev) => {
      const r = el.getBoundingClientRect();
      const fx = Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width));
      const fy = Math.max(0, Math.min(1, 1 - (ev.clientY - r.top) / r.height));
      const snap = (f, ax) => {
        const raw = ax.min + f * (ax.max - ax.min);
        // round to the step's own precision: Math.round(x/0.05)*0.05 gives
        // 0.8500000000000001, which then goes into the scene file verbatim
        const s = String(ax.step);
        const dp = s.includes(".") ? s.split(".")[1].length : 0;
        return Number((Math.round(raw / ax.step) * ax.step).toFixed(dp));
      };
      const x = snap(fx, spec.x), y = snap(fy, spec.y);
      e[spec.x.key] = x; e[spec.y.key] = y;
      el.render();
      const now = performance.now();
      if (now - last < 33) return;                // ~30 Hz is plenty for a knob
      last = now;
      // Only the axis that actually MOVED. A drag is mostly sideways or mostly
      // vertical, so sending both every frame doubled the traffic and filled
      // the session log with no-op sets - the 22:29 take recorded
      // `every_bars_min: 1` five times in 800 ms while only `voices` was
      // changing, which makes the log unreadable when you are trying to see
      // what the player actually did.
      if (x !== sentX) { sentX = x; send({ type: "set", path: `edge.${e.id}.${spec.x.key}`, value: x }); }
      if (y !== sentY) { sentY = y; send({ type: "set", path: `edge.${e.id}.${spec.y.key}`, value: y }); }
    };
    el.addEventListener("pointerdown", (ev) => {
      el.setPointerCapture(ev.pointerId); el.classList.add("is-live"); move(ev);
    });
    el.addEventListener("pointermove", (ev) => { if (el.hasPointerCapture(ev.pointerId)) move(ev); });
    const end = (ev) => {
      if (!el.classList.contains("is-live")) return;
      el.classList.remove("is-live");
      last = 0; sentX = sentY = undefined;         // make sure the last position lands
      move(ev);
    };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
    el.render();
    return el;
  }

  function edgeBool(e, spec) {
    let cur = e;
    const wrap = document.createElement("label");
    wrap.className = "mie-field mie-field-bool";
    wrap.title = spec.hint || spec.key;
    wrap.innerHTML = `<span class="mie-fl">${spec.label}</span><input type="checkbox">`;
    const cb = wrap.querySelector("input");
    cb.dataset.key = spec.key;
    // undefined means the algorithm's own default, and every one of these
    // defaults to on; a missing key must not read as "off"
    const val = (v) => (v === undefined || v === null ? true : !!v);
    cb.checked = val(e[spec.key]);
    cb.addEventListener("change", () => {
      cur[spec.key] = cb.checked;
      send({ type: "set", path: `edge.${cur.id}.${spec.key}`, value: cb.checked });
    });
    wrap.sync = (fresh) => {
      cur = fresh;
      if (document.activeElement !== cb) cb.checked = val(fresh[spec.key]);
    };
    return wrap;
  }

  function edgeField(e, spec) {
    // A slider, not a number box. On a laptop a slider is the closest thing to
    // the hardware knob this will eventually live on: you can sweep it while
    // listening, which is the only way to find a decay or a register by ear.
    // The number is still shown, and still typeable, because some values (a
    // transpose of exactly 7) are named, not felt.
    const wrap = document.createElement("label");
    wrap.className = "mie-field mie-field-slider";
    wrap.title = spec.hint || spec.key;
    const val = e[spec.key];
    wrap.innerHTML = `<span class="mie-fl">${spec.label}</span>` +
      `<input class="mie-fs" type="range" min="${spec.min}" max="${spec.max}" step="${spec.step}">` +
      `<input class="mie-fn" type="number" min="${spec.min}" max="${spec.max}" step="${spec.step}">`;
    const sl = wrap.querySelector(".mie-fs");
    const num = wrap.querySelector(".mie-fn");
    const set = (v) => send({ type: "set", path: `edge.${e.id}.${spec.key}`, value: Number(v) });
    sl.value = num.value = val === undefined ? spec.min : val;
    sl.dataset.key = num.dataset.key = spec.key;

    // A register is a PAIR. On the 17:21 take the player dragged `high` from 88
    // down onto `low`, which was 55, and the sustain lane spent the next four
    // minutes playing n55 and nothing else - seventeen times, one pitch. A
    // number box makes you type that on purpose; a slider you can sweep into it
    // by accident and never notice. So the two ends push each other and keep an
    // octave between them, which is the least room a lane needs to voice a
    // chord at all.
    const commit = (v) => {
      v = Number(v);
      const p = PAIRS[spec.key];
      if (p) {
        const other = wrap.parentElement.querySelector(`.mie-fn[data-key="${p.partner}"]`);
        if (other) {
          const o = Number(other.value);
          if (p.side === "above" && o < v + p.gap) pushPartner(wrap, p.partner, v + p.gap);
          if (p.side === "below" && o > v - p.gap) pushPartner(wrap, p.partner, v - p.gap);
        }
      }
      sl.value = num.value = v;
      set(v);
    };
    sl.addEventListener("input", () => commit(sl.value));
    num.addEventListener("change", () => commit(num.value));
    return wrap;
  }

  // Settings that only mean anything as a pair, and the least room to keep
  // between them. `every_bars_*` joined the table after the 22:42 take, where
  // the panel let the player set 最長 0.25 under 最短 0.5: the algorithm quietly
  // clamps `hi = max(lo, hi)`, so the slider they were moving did nothing and
  // said nothing.
  const PAIRS = {
    low:  { partner: "high", gap: 12, side: "above" },
    high: { partner: "low",  gap: 12, side: "below" },
    every_bars_min: { partner: "every_bars_max", gap: 0, side: "above" },
    every_bars_max: { partner: "every_bars_min", gap: 0, side: "below" },
  };

  function pushPartner(wrap, key, value) {
    const body = wrap.parentElement;
    const num = body.querySelector(`.mie-fn[data-key="${key}"]`);
    const sl = body.querySelector(`.mie-fs[data-key="${key}"]`);
    if (!num || !sl) return;
    // On the partner's OWN step. Rounding to whole numbers was fine while the
    // only pair was a register in semitones; `every_bars_*` moves in quarters
    // and would have been rounded away.
    const step = Number(sl.step) || 1;
    const snapped = Math.round(value / step) * step;
    const dp = String(step).includes(".") ? String(step).split(".")[1].length : 0;
    const v = Number(Math.max(Number(sl.min), Math.min(Number(sl.max), snapped)).toFixed(dp));
    if (Number(num.value) === v) return;
    num.value = sl.value = v;
    num.dispatchEvent(new Event("change"));
  }

  // ------------------------------------------------- 彈法條件 (when.texture)
  // The classifier has been reading the playing for a while - block chords,
  // arpeggio, a held pad, a single line - and nothing could act on it: no
  // scene named a condition and there was no way to write one. This is the
  // control. Nothing selected means "takes everything", which is every edge
  // that exists today, so turning this on changes nothing until you use it.
  const TEX = { quiet: "靜", sustained: "持續", chord: "和弦", arpeggio: "琶音", melody: "旋律" };
  const TEX_ORDER = ["quiet", "sustained", "chord", "arpeggio", "melody"];

  function edgeWhenTexture(e) {
    const w = e.texture !== undefined ? e.texture : (e.when || {}).texture;
    if (!w) return [];
    return typeof w === "string" ? [w] : w.slice();
  }

  function edgeTextureField(e) {
    // The snapshot hands us a NEW edge object every frame while the DOM node is
    // cached, so the control keeps a reference it can refresh: without this the
    // chips would show whatever the FIRST snapshot said and never move again
    // when the server changed the setting - undo, revert and a preset switch
    // all do exactly that.
    let cur = e;
    const wrap = document.createElement("label");
    wrap.className = "mie-field mie-field-when";
    wrap.title = "只有在你這樣彈的時候，這條線才會說話。全部不選 = 不限";
    wrap.innerHTML = `<span class="mie-fl">只在這樣彈</span><span class="mie-chips"></span>`;
    const chips = wrap.querySelector(".mie-chips");
    TEX_ORDER.forEach((k) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "mie-chip"; b.dataset.tex = k; b.textContent = TEX[k];
      chips.appendChild(b);
    });
    const paint = () => {
      const on = new Set(edgeWhenTexture(cur));
      chips.querySelectorAll(".mie-chip").forEach((b) => b.classList.toggle("on", on.has(b.dataset.tex)));
      wrap.classList.toggle("is-any", on.size === 0);
    };
    chips.addEventListener("click", (ev) => {
      const b = ev.target.closest(".mie-chip");
      if (!b) return;
      const on = new Set(edgeWhenTexture(cur));
      on.has(b.dataset.tex) ? on.delete(b.dataset.tex) : on.add(b.dataset.tex);
      const list = TEX_ORDER.filter((k) => on.has(k));
      cur.when = { texture: list };
      // One source of truth. A scene may carry the `texture:` shorthand, and
      // `wants()` reads THAT first - a `when` written beside it would never be
      // looked at. Clear it in the same breath.
      if (cur.texture !== undefined && cur.texture !== null) {
        cur.texture = null;
        send({ type: "set", path: `edge.${cur.id}.texture`, value: null });
      }
      send({ type: "set", path: `edge.${cur.id}.when`, value: { texture: list } });
      paint();
    });
    wrap.sync = (fresh) => { cur = fresh; paint(); };
    paint();
    return wrap;
  }

  function edgeChoice(e, key) {
    const wrap = document.createElement("label");
    wrap.className = "mie-field";
    wrap.title = key;
    wrap.innerHTML = `<span>${key}</span><select></select>`;
    const sel = wrap.querySelector("select");
    CHOICES[key].forEach((v) => { const o = document.createElement("option"); o.value = v; o.textContent = v; sel.appendChild(o); });
    sel.dataset.key = key;
    sel.value = String(e[key] === undefined || e[key] === true ? CHOICES[key][0] : e[key]);
    sel.addEventListener("change", () => send({ type: "set", path: `edge.${e.id}.${key}`, value: sel.value }));
    return wrap;
  }

  function renderEdges(s) {
    const box = $("#mieEdges");
    const seen = new Set();
    s.edges.forEach((e) => {
      seen.add(e.id);
      let el = edgeEls.get(e.id);
      if (!el) {
        el = document.createElement("div"); el.className = "mie-edge";
        el.innerHTML = `<div class="mie-edge-head">
            <input type="checkbox" title="啟用">
            <div><div class="name"></div><div class="route"></div></div>
            <span class="algo"></span><span class="fires"></span>
            <button class="mie-more" title="參數">▾</button>
          </div><div class="mie-edge-body" hidden></div>`;
        const pad = makePad(e);
        if (pad) {
          el.querySelector(".mie-edge-head").insertBefore(pad, el.querySelector(".algo"));
          el._pad = pad;
        }
        el.querySelector('input[type=checkbox]').addEventListener("change", (ev) => send({ type: "set", path: `edge.${e.id}.enabled`, value: ev.target.checked }));
        const body = el.querySelector(".mie-edge-body");
        el.querySelector(".mie-more").addEventListener("click", () => {
          body.hidden = !body.hidden;
          el.querySelector(".mie-more").textContent = body.hidden ? "▾" : "▴";
        });
        // the algorithm's own version of a knob wins, so vel_scale is not shown twice
        const specs = new Map();
        [...COMMON, ...(PARAMS[e.algo] || [])].forEach((sp) => specs.set(sp.key, sp));
        el._bools = [];
        specs.forEach((spec) => {
          const f = spec.kind === "bool" ? edgeBool(e, spec) : edgeField(e, spec);
          if (spec.kind === "bool") el._bools.push(f);
          body.appendChild(f);
        });
        el._when = edgeTextureField(e);
        body.appendChild(el._when);
        const choices = ["constraint", "align", "voice_lead", "collision"];
        if (e.algo === "silence") choices.push("silence_mode");
        choices.forEach((k) => body.appendChild(edgeChoice(e, k)));
        box.appendChild(el); edgeEls.set(e.id, el);
      }
      const src = e.src === 0 ? "HUMAN" : `CH${e.src}`;
      el.querySelector(".name").textContent = e.id;
      el.querySelector(".route").textContent = `${src} → CH${e.dst} · p ${e.prob} · ${e.constraint}`
        + (e.delay_beats ? ` · ${e.delay_beats} beat` : "") + (e.delay_ms ? ` · ${e.delay_ms} ms` : "");
      el.querySelector(".algo").textContent = e.algo;
      const cb = el.querySelector('input[type=checkbox]'); if (document.activeElement !== cb) cb.checked = e.enabled;
      el.querySelectorAll(".mie-edge-body input, .mie-edge-body select").forEach((inp) => {
        if (document.activeElement === inp || inp.type === "checkbox") return;
        const v = e[inp.dataset.key];
        if (v === undefined) return;
        inp.value = inp.tagName === "SELECT" ? String(v === true ? CHOICES[inp.dataset.key][0] : v) : v;
      });
      (el._bools || []).forEach((f) => f.sync(e));
      // `prob` is on the head row too, where it is the one number you glance at
      const ph = el.querySelector(".mie-edge-head .prob-val");
      if (ph) ph.textContent = Number(e.prob).toFixed(2);
      if (el._pad && !el._pad.classList.contains("is-live")) el._pad.render();
      // A lane whose notes are being thrown away looks identical to a quiet
      // one. Say it on the row, where the control that caused it is.
      const fires = el.querySelector(".fires");
      fires.textContent = e.mute ? "靜音" : (e.drops ? `${e.fires || 0} ⚠${e.drops}` : (e.fires || 0));
      fires.classList.toggle("has-drops", !!e.drops || !!e.mute);
      fires.title = e.mute || (e.drops
        ? `${e.drops} 個音被丟掉了——多半是音域或八度把它推到樂器範圍外`
        : "");
      if (el._when && !el._when.contains(document.activeElement)) el._when.sync(e);
      // A lane whose condition does not match right now is not broken and not
      // idle - it is WAITING, and it should say which playing it is waiting
      // for. Silence you can explain is not the same as silence you cannot.
      const want = edgeWhenTexture(e);
      const waiting = want.length > 0 && s.state.texture && !want.includes(s.state.texture);
      el.classList.toggle("is-waiting", !!waiting);
      if (waiting && !e.mute) {
        fires.textContent = `${e.fires || 0} ⏸`;
        fires.title = `這條線只在「${want.map((k) => TEX[k] || k).join("、")}」時說話，你現在是「${TEX[s.state.texture] || s.state.texture}」`;
      }
      el.classList.toggle("is-mute", !!e.mute);
      el.classList.toggle("hot", e.ago !== null && e.ago !== undefined && e.ago < 2);
      el.classList.toggle("off", !e.enabled);
    });
    edgeEls.forEach((el, id) => { if (!seen.has(id)) { el.remove(); edgeEls.delete(id); } });
  }
  function renderStats(s) {
    const st = s.stats, j = s.jitter || {};
    const rows = [["human", st.human_notes], ["gen sent", st.gen_sent], ["scheduled", st.gen_sched], ["dropped", st.dropped], ["muted", st.muted],
      ["loops", st.loops], ["panics", st.panics], ["pending", s.pending],
      ["jitter p95", j.p95 !== undefined ? `${j.p95} ms` : "—"], ["jitter max", j.max !== undefined ? `${j.max} ms` : "—"]];
    Object.entries(s.drops || {}).forEach(([k, v]) => rows.push([`drop:${k}`, v, "drop"]));
    $("#mieStats").innerHTML = rows.map(([k, v, c]) => `<div class="${c || ""}"><span class="k">${k}</span> ${v}</div>`).join("");
  }

  // ---------------------------------------------------------------- events
  function pushEvent(e) {
    if (roll) roll.pushEvent(e);          // the roll draws from the same stream
    const box = $("#mieStream");
    const el = document.createElement("div");
    let cls = "", txt = "";
    switch (e.type) {
      case "human": cls = "human"; txt = `HUMAN ch${e.ch} ${nn(e.note)} v${e.vel}` + (e.chord ? ` [${e.chord}]` : ""); break;
      case "gen": cls = `gen${Math.min(3, e.hop || 1)}`; txt = `GEN hop${e.hop} ch${e.ch} ${nn(e.note)} v${e.vel} ${e.lane} ← ${e.edge}`; break;
      case "sched": cls = "sched"; txt = `  sched ch${e.ch} ${nn(e.note)} in ${e.in_ms} ms · ${e.dur_ms} ms · ${e.edge}`; break;
      case "drop": cls = "drop"; txt = `DROP ${e.reason} ch${e.ch} ${nn(e.note)} ${e.lane || ""} ${e.edge || ""}`; break;
      case "edge": cls = "edge"; txt = `  edge ${e.edge} p=${e.p}`; break;
      case "resnap": cls = "resnap"; txt = `  resnap ch${e.ch} ${nn(e.frm)} → ${nn(e.to)}`; break;
      case "off": cls = "off"; txt = `  off ch${e.ch} ${nn(e.note)} (${e.why}${e.held_ms !== undefined ? `, ${e.held_ms} ms` : ""})`; break;
      case "human_off": cls = "off"; txt = `  human off ch${e.ch} ${nn(e.note)} (${e.held_ms} ms)`; break;
      case "style": cls = "mode"; txt = e.action === "clear" ? "風格 → 取消" : `風格 → ${e.id}（${e.edges} 條邊）`; break;
      case "panic": cls = "panic"; txt = `PANIC (${e.reason}) ${e.notes} notes released`; break;
      case "loop": cls = "loop"; txt = `LOOP ch${e.ch} ${nn(e.note)} came back on MIE In`; break;
      case "pedal": cls = "edge"; txt = `  pedal ch${e.ch} ${e.val >= 64 ? "down" : "up"}`; break;
      case "control": cls = "ctl"; txt = `UC4 ${e.key} = ${e.val}` + (e.action ? ` → ${e.action}` : ""); break;
      case "set": cls = "set"; txt = `set ${e.path} = ${JSON.stringify(e.value)}`; break;
      case "mode": cls = "mode"; txt = `mode → ${e.mode}`; break;
      case "scene": cls = "mode"; txt = `scene → ${e.id} ${e.name}`; break;
      case "skip": cls = "edge"; txt = `  skip ${e.edge} p=${e.p}`; break;
      default: cls = "edge"; txt = JSON.stringify(e);
    }
    el.className = `mie-ev mie-ev-${cls}`;
    el.innerHTML = `<span class="t">${Number(e.t).toFixed(2)}</span>${txt}`;
    box.appendChild(el);
    while (box.children.length > streamMax) box.removeChild(box.firstChild);
  }

  // ---------------------------------------------------------------- controls
  // EVERY binding below runs exactly once, at load. Putting one inside a render
  // function attaches another copy on every snapshot, and a single click then
  // fires all of them: on the 19:27 take one press of Revert sent 117 reverts,
  // which wrote 24,777 settings, and the panel stopped responding. If a control
  // ever needs binding from a render path, remove the old listener first.
  $("#miePanic").addEventListener("click", () => send({ type: "panic" }));
  $("#mieResume").addEventListener("click", () => send({ type: "resume" }));
  $("#mieFreeze").addEventListener("click", () =>
    send({ type: "freeze", on: !$("#mieFreeze").classList.contains("is-frozen") }));
  $("#mieUndo").addEventListener("click", () => send({ type: "undo" }));
  // `confirm()` blocks this page's JavaScript, and the engine's messages queue
  // up behind it - the dialog looked stuck because the page could not repaint.
  // A two-step button asks the same question without stopping the panel.
  let revertArmed = 0;
  $("#mieRevert").addEventListener("click", () => {
    const b = $("#mieRevert");
    if (Date.now() - revertArmed < 4000) {
      revertArmed = 0; b.classList.remove("is-arming"); b.textContent = "退回檔案";
      send({ type: "revert" });
      return;
    }
    revertArmed = Date.now();
    b.classList.add("is-arming");
    b.textContent = "再按一次確認";
    setTimeout(() => {
      if (!revertArmed) return;
      revertArmed = 0; b.classList.remove("is-arming"); b.textContent = "退回檔案";
    }, 4000);
  });
  // ------------------------------------------------------------ 落差提示
  // It only ever SAYS. Nothing here changes a setting until the player presses
  // the button, and what it presses is an ordinary `set` - so it shows up on
  // the sliders and Ctrl+Z puts it back like anything else.
  let adviceSig = "";
  function renderAdvice(list) {
    const box = $("#mieAdvice");
    const pop = $("#mieAdvicePop");
    box.hidden = !list.length;
    if (!list.length) { pop.hidden = true; adviceSig = ""; return; }
    $(".mie-adv-n").textContent = list.length;
    box.classList.toggle("has-warn", list.some((a) => a.level === "warn"));
    const sig = list.map((a) => a.id + a.text).join("|");
    if (sig === adviceSig) return;               // do not rebuild under the cursor
    adviceSig = sig;
    pop.innerHTML = "";
    list.forEach((a) => {
      const row = document.createElement("div");
      row.className = "mie-adv-row" + (a.level === "warn" ? " is-warn" : "");
      const btns = (a.fix_label ? `<button class="mie-btn mie-adv-fix">${a.fix_label}</button>` : "")
        + (a.alt && a.alt.style ? `<button class="mie-btn mie-adv-alt">切換風格</button>` : "");
      row.innerHTML = `<div class="mie-adv-txt">${a.text}</div>`
        + `<div class="mie-adv-why">${a.why || ""}</div>`
        + `<div class="mie-adv-act">${btns}</div>`;
      const fix = row.querySelector(".mie-adv-fix");
      if (fix) fix.addEventListener("click", () => {
        (a.fix || []).forEach(([path, value]) => send({ type: "set", path, value }));
        row.classList.add("is-done");
        fix.textContent = "已套用（Ctrl+Z 可退回）";
        fix.disabled = true;
      });
      const alt = row.querySelector(".mie-adv-alt");
      if (alt) alt.addEventListener("click", () => {
        send({ type: "style", id: a.alt.style });
        alt.textContent = "已切換"; alt.disabled = true;
      });
      pop.appendChild(row);
    });
  }
  $("#mieAdviceBtn").addEventListener("click", () => {
    const pop = $("#mieAdvicePop");
    pop.hidden = !pop.hidden;
  });
  document.addEventListener("click", (e) => {
    const pop = $("#mieAdvicePop");
    if (pop.hidden) return;
    if (!$("#mieAdvice").contains(e.target instanceof Node ? e.target : null)) pop.hidden = true;
  });

  // ---------------------------------------------------------- 介入風格預設
  // A style is a bundle of settings that already exist - nothing here can do
  // anything the player could not already do by hand, which is why it is safe
  // to reach for mid-set. Choosing one stashes the current settings; 取消
  // puts those back, not the previous style's.
  let styleSig = "";
  function renderStyles(info) {
    const sel = $("#mieStyleSel");
    const list = (info && info.list) || [];
    const sig = list.map((x) => x.id).join(",");
    if (sig !== styleSig) {
      styleSig = sig;
      sel.innerHTML = '<option value="">— 不套用 —</option>';
      list.forEach((x) => {
        const o = document.createElement("option");
        o.value = x.id; o.textContent = x.name; o.title = x.hint || "";
        sel.appendChild(o);
      });
    }
    const cur = (info && info.id) || "";
    if (document.activeElement !== sel && sel.value !== cur) sel.value = cur;
    const hit = list.find((x) => x.id === cur);
    sel.parentElement.title = hit
      ? `${hit.name}：${hit.hint}　（選「不套用」會全部回到你套用之前的設定）`
      : "介入風格：一次把張力、厚度、時間與每個演算法的取音範圍換成一組。取消就全部回到你原本的設定";
    sel.parentElement.classList.toggle("is-on", !!cur);
  }
  $("#mieStyleSel").addEventListener("change", (e) => {
    send({ type: "style", id: e.target.value });
  });

  // ------------------------------------------------------------ 鋼琴捲軸
  // Created lazily: until the player asks for it there is no canvas, no
  // animation frame, and `pushEvent` does nothing extra on the panel's hot
  // path. The three columns above are where the work happens during a take
  // and this must not disturb them.
  let roll = null;
  const rollBox = $("#mieRoll");
  function toggleRoll(on) {
    const show = on === undefined ? rollBox.hidden : on;
    rollBox.hidden = !show;
    $("#mieRollBtn").classList.toggle("is-on", show);
    // If the module failed to load or parse, the panel would open showing a
    // toolbar over a dead canvas and nothing would ever explain it.
    if (show && !roll && !window.MieRoll) {
      const t = rollBox.querySelector(".mr-title");
      if (t) t.textContent = "捲軸的程式沒有載入（mie-roll.js）——硬重新整理一次；如果還是這樣，看 console 的錯誤";
    }
    if (show && !roll && window.MieRoll) {
      roll = window.MieRoll.create(rollBox);
      window.__mieRoll = roll;   // a handle for the console: the roll is the
                                 // one part of this panel worth poking at from
                                 // devtools while a take is being reviewed
    }
    if (roll) { if (show) roll.redraw(); else roll.setLive(false); }
    // it lives below the three columns, which fill the screen - opening
    // something the player then cannot see is the same as not opening it
    if (show) rollBox.scrollIntoView({ behavior: "smooth", block: "end" });
  }
  $("#mieRollBtn").addEventListener("click", () => toggleRoll());
  $("#mieRollClose").addEventListener("click", () => toggleRoll(false));

  document.addEventListener("keydown", (e) => {
    // Space toggles freeze: both hands are usually on the keys, so the one
    // control worth reaching for has to be the easiest key on the laptop.
    // `e.target` is not always an Element - a keydown can land on the document
    // itself, where `matches` does not exist and the guard would throw.
    const onField = e.target instanceof Element && e.target.matches("input, select, textarea");
    if (e.code === "Space" && !onField) {
      e.preventDefault();
      if (e.repeat) return;          // a held key repeats at the OS rate
      send({ type: "freeze", on: !$("#mieFreeze").classList.contains("is-frozen") });
      return;
    }
    if (e.key.toLowerCase() === "r" && !onField && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault(); toggleRoll(); return;
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !e.shiftKey) {
      e.preventDefault(); send({ type: "undo" });
    }
  });
  // Everything tuned on this panel lives only in memory until this is pressed.
  document.querySelectorAll(".mie-pbtn").forEach((b) =>
    b.addEventListener("click", () => send({ type: "preset", slot: b.dataset.slot })));
  document.querySelectorAll(".mie-pstore").forEach((b) =>
    b.addEventListener("click", () => send({ type: "preset_save", slot: b.dataset.store })));
  $("#mieSave").addEventListener("click", () => {
    // Name the scene an empty answer overwrites. "覆寫目前的" is only obvious
    // to whoever wrote it; the player saved into a second file for two sessions
    // and thought the global knobs were not being saved at all.
    const cur = (lastSnap && lastSnap.scene && lastSnap.scene.id) || "";
    const as = prompt(`另存為新 scene 的編號（留空 = 覆寫 ${cur}）`, "");
    if (as === null) return;
    send({ type: "save_scene", as: as.trim() || undefined });
  });
  $("#mieHaltResume").addEventListener("click", () => send({ type: "resume" }));
  $("#mieModeSel").addEventListener("change", (e) => send({ type: "mode", value: e.target.value }));
  $("#mieSceneSel").addEventListener("change", (e) => send({ type: "scene", id: e.target.value }));
  [["gMaster", "master_gain"], ["gTension", "tension"], ["gDensity", "density"],
   ["gTime", "time"], ["gProb", "prob_scale"], ["gChaos", "chaos"],
   ["gRestraint", "restraint"]].forEach(([id, key]) => {
    const el = document.getElementById(id);
    el.addEventListener("input", () => { document.getElementById(id + "V").textContent = Number(el.value).toFixed(2); });
    el.addEventListener("change", () => send({ type: "set", path: `global.${key}`, value: Number(el.value) }));
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      const now = Date.now();
      if (now - escArm < 600) { send({ type: "panic" }); escArm = 0; } else { escArm = now; }
    }
  });

  connect();
})();
