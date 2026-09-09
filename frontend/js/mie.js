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
  // A save the engine refused because the scene file had changed underneath,
  // waiting for a second, deliberate press. Up here because both the snapshot
  // render and the button wiring read it.
  let saveConflict = false;
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
    renderTouched(s.touched || []);
    if (roll) roll.engineState(s);
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
    // ...unless the button is currently carrying a refused save. That warning
    // is the more important thing to say, and this line runs every snapshot -
    // it wiped the conflict tooltip within 200 ms of it being set.
    if (!saveConflict) {
      $("#mieSave").title = ed.unsaved ? `有 ${ed.undo} 項調整還沒寫進 scene 檔` : "把目前所有調整寫回 scene 檔";
    }
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
        el.innerHTML = `<input type="checkbox"><span class="dot"></span><span class="ch">CH${inst.ch}</span><span class="nm"></span>`
          + `<button class="mie-hold" type="button" title="標成適合長音">長</button>`
          + `<select class="mie-swcc" title="這台的表情/呼吸吃哪一個 CC"></select>`;
        const sw = el.querySelector(".mie-swcc");
        SWELL_CCS.forEach(([v, label]) => {
          const o = document.createElement("option"); o.value = v; o.textContent = label; sw.appendChild(o);
        });
        sw.addEventListener("change", (ev) =>
          send({ type: "set", path: `inst.${inst.ch}.swell_cc`, value: Number(ev.target.value) }));
        el.querySelector("input").addEventListener("change", (e) => send({ type: "set", path: `inst.${inst.ch}.enabled`, value: e.target.checked }));
        // Whether a synth can hold a long note is a fact about the PATCH loaded
        // on it, and the player changes patches. It was a line in a JSON file
        // that needed a restart to take effect; now it is where the instrument
        // is. It is written straight back to instruments.json - the rig is not
        // a take and there is no 儲存 for it.
        el.querySelector(".mie-hold").addEventListener("click", (e) => {
          e.preventDefault();
          send({ type: "set", path: `inst.${inst.ch}.sustain_ok`, value: !el._hold });
        });
        box.appendChild(el); instEls.set(inst.ch, el);
      }
      el.querySelector(".nm").textContent = inst.name;
      const cb = el.querySelector("input"); if (document.activeElement !== cb) cb.checked = inst.enabled;
      el.classList.toggle("active", activeCh.has(inst.ch));
      el.classList.toggle("human", humanCh.has(inst.ch));
      el._hold = !!inst.sustain_ok;
      const hb = el.querySelector(".mie-hold");
      hb.classList.toggle("is-on", el._hold);
      // The two numbers are the ones this flag actually chooses between, and
      // they come from the scene rather than from anything hard-coded here.
      const g = (s.scene.global) || {};
      hb.title = el._hold
        ? `已標長音：這台可以抱到 ${g.sustain_dur_s || 30} 秒`
        : `沒標長音：這台上的音最長 ${g.max_dur_s || 8} 秒。點一下改成長音`;
      const sw = el.querySelector(".mie-swcc");
      if (document.activeElement !== sw) sw.value = String(inst.swell_cc || 0);
      sw.classList.toggle("is-on", !!inst.swell_cc);
      sw.title = inst.swell_cc
        ? `呼吸走 CC${inst.swell_cc}。休息值永遠是 127，不會把這台留在小聲的地方`
        : "沒有設 CC：這台不會收到任何呼吸訊息。選一個之後，聲部的「呼吸」才會出去";
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
    // Every lane can be asked to stay under the hands, not just the pad: the
    // follow lane sits a fifth ABOVE the note it answers, so it goes over the
    // player's top exactly when they play near it (measured: 81 % of its notes
    // on the 11:15 take). It is not dropped when it would - it keeps its pitch
    // class and is voiced an octave down, so a fifth above becomes a fourth
    // below.
    NUM("讓開(半音)", "below_player", 0, 24, 1,
        "0 = 不管你彈到哪裡。大於 0 = 永遠比你當下彈的最高音再低這麼多半音，你往上跑它就往下讓"),
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
              BOOL("疊在手上面", "above_held", "把這層墊音放在你正按著的音之上（預設開；設了「讓開」就不看這個）"),
              NUM("低", "low", 21, 108, 1, null), NUM("高", "high", 21, 108, 1, null)],
  };
  const CHOICES = {
    constraint: ["chord", "function", "scale", "free"],
    align: ["none", "half", "beat", "bar"],
    voice_lead: ["off", "octave", "free"],
    silence_mode: ["sound", "attack"],
    collision: ["octave", "unison", "none"],
    // How much air between neighbouring voices. `close` is what every scene was
    // tuned with and stays the default.
    spacing: ["close", "open", "wide"],
    // One voice held under the harmony, read from the KEY - a floor that
    // follows the chord is a bass line, not a pedal.
    pedal: ["off", "tonic", "fifth"],
  };
  const CHOICE_LABEL = {
    spacing: { close: "密集", open: "開放 1-5-9", wide: "很寬" },
    pedal: { off: "不用", tonic: "主音", fifth: "五度" },
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
    el.innerHTML = `<div class="mie-pad-dot"></div>`;
    const dot = el.querySelector(".mie-pad-dot");
    // The readout used to sit INSIDE the pad, bottom-left, where the dot sat on
    // top of it as soon as either value was low - "延遲 60 · 持續 0.5" with a
    // blue circle through the middle of it. It now lives beside the pad, and
    // the row that owns both hands it over.
    let lbl = { textContent: "" };
    el.setLabel = (node) => { lbl = node; el.render(); };

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

  /** 強度: the edge's own volume, on the row rather than one click down.
   *
   *  It is `vel_scale`, the same setting the 力度× slider in the body writes -
   *  the answer to "每個子效果能有自己的獨立音量嗎". Having it out here is the
   *  point: balancing lanes against each other is done by ear, while playing,
   *  and it was the one knob that always needed the panel opened first.
   *
   *  Both sliders stay honest because both are re-read from the engine's own
   *  snapshot; neither writes to the other.
   */
  function makeVel(e, wrap) {
    const sl = wrap.querySelector("input");
    const out = wrap.querySelector(".mie-vel-v");
    const hit = wrap.querySelector(".mie-vel-hit");
    let ed = e, snap = null;
    const show = (v) => {
      sl.value = v; out.textContent = Number(v).toFixed(2);
      const vel = effVel(ed, Number(v), snap);
      // Below 1 the engine mutes the note outright; below about 4 the
      // instrument is technically playing and you cannot hear it. Both look
      // exactly like a lane that is not firing, which is how sustain_strings
      // spent a whole take at velocity 1 without anyone knowing.
      hit.textContent = vel === null ? "" : (vel < 1 ? "→ 靜音" : `→ v${vel}`);
      hit.classList.toggle("is-gone", vel !== null && vel < 4);
      hit.title = vel === null
        ? "這條線回應的是另一條線送出的音，不是你彈的，所以這裡算不出實際力度"
        : "乘完全域音量之後，真正送到樂器的力度。低於 1 引擎會直接靜音"
          + (ed.vel_drift && ed.vel_drift.depth ? "。自走音量還會讓它上下擺動" : "")
          + (ed.algo === "echo" || ed.algo === "phrase" ? "。這是第一趟，後面每趟更輕" : "");
    };
    show(e.vel_scale === undefined ? 1 : e.vel_scale);
    sl.addEventListener("input", () => {
      send({ type: "set", path: `edge.${e.id}.vel_scale`, value: Number(sl.value) });
      show(Number(sl.value));
    });
    return {
      sync(next, s) {
        ed = next; snap = s;
        // never while the finger is on it, or the value fights the drag
        if (document.activeElement === sl) return;
        show(next.vel_scale === undefined ? 1 : next.vel_scale);
      },
    };
  }

  /** What actually reaches the instrument, as a MIDI velocity.
   *
   *  The number on the slider is a MULTIPLIER, and at 全域音量 0.27 a
   *  multiplier of 0.05 is the difference between a lane you can hear and one
   *  that is muted note by note - which is not something anyone should have to
   *  work out in their head mid-take.
   *
   *  Where the velocity starts depends on the algorithm: a pad or a sustained
   *  line carries its own `vel`, everything else answers a note you played and
   *  starts from how hard you played it. `vel_mean` is the engine's own rolling
   *  average of exactly that, so this is the real first note - not a model of
   *  one. Later passes of an echo are quieter still; this is the loudest.
   */
  function effVel(e, scale, s) {
    let base = e.vel;
    if (base === undefined) {
      // A hop-2 edge answers another LANE's notes, not yours, and those are
      // already quiet - iridium_to_wavestate reads CH11, which sends at 4-8.
      // Using your own velocity here would print a number three times too big,
      // and a confident wrong number is worse than no number.
      if (e.src !== 0) return null;
      const m = s && s.state && s.state.vel_mean;
      if (!m) return null;                  // nothing played yet: no honest answer
      base = m;
    }
    const gain = (s && s.scene && s.scene.global && s.scene.global.master_gain);
    const v = base * scale * (gain === undefined ? 1 : gain);
    return Math.min(127, Math.round(v));
  }

  /** 呼吸 depth, on the face of the card - but only for a lane whose instrument
   *  has said which controller it listens to.
   *
   *  It needs both halves, and after the first evening with it the player had
   *  set the instrument half and stopped: the depth was one row among twenty in
   *  the expanded parameter grid, and the log shows not one `edge.*.swell` in
   *  half an hour. A control the feature cannot work without does not belong
   *  three levels down. It stays hidden on lanes where it would do nothing,
   *  rather than offering a slider that sends nowhere.
   *
   *  The full control - beats and shape - stays in the body. This is the one
   *  knob you reach for while listening.
   */
  function makeBreath(e, wrap) {
    const sl = wrap.querySelector("input");
    const top = wrap.querySelector(".mie-breath-top");
    const out = wrap.querySelector(".mie-breath-v");
    const cc = wrap.querySelector(".mie-breath-cc");
    let cur = e;
    const read = (x) => (x && typeof x.swell === "object" && x.swell) || {};
    const show = (v) => { sl.value = v; out.textContent = Number(v).toFixed(2); };
    const push = () => {
      const d = read(cur);
      const v = { depth: Number(sl.value), beats: d.beats || 8, shape: d.shape || "breathe",
                  top: Number(top.value) };
      cur.swell = v;
      send({ type: "set", path: `edge.${cur.id}.swell`, value: v });
    };
    sl.addEventListener("input", () => out.textContent = Number(sl.value).toFixed(2));
    sl.addEventListener("change", push);
    top.addEventListener("change", push);
    return {
      sync(next, insts, sharing) {
        cur = next;
        const inst = insts.find((i) => i.ch === next.dst) || {};
        wrap.hidden = !inst.swell_cc;
        if (wrap.hidden) return;
        const d = read(next);
        const on = d.depth > 0 || (d.top !== undefined && d.top < 1);
        // MIDI has no per-note expression. CC11 belongs to the CHANNEL, so a
        // breath set here rides every OTHER lane on the same instrument as
        // well - which is a surprise worth having before it is heard, not
        // after.
        const others = (sharing.get(next.dst) || 1) - 1;
        const t = d.top === undefined ? 1 : d.top;
        if (document.activeElement !== top) top.value = t;
        // the two numbers the ear actually asks about: how loud at the peak,
        // and how far it falls
        cc.textContent = `CC${inst.swell_cc} 頂${Math.round(127 * t)}`
          + (on && others ? ` ·連帶 ${others}` : "");
        wrap.classList.toggle("is-off", !on);
        wrap.classList.toggle("is-shared", !!on && others > 0);
        wrap.title = others
          ? `CC${inst.swell_cc} 是整個聲道共用的，所以這個呼吸也會帶著 CH${next.dst} 上`
            + `另外 ${others} 條線一起起伏。要它單獨呼吸，就把它搬到一台沒有別人的琴`
          : `這條線響著的時候，把 CH${next.dst} 的 CC${inst.swell_cc} 上下擺。深度 0 = 不動`;
        if (document.activeElement !== sl) show(d.depth || 0);
      },
    };
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

  // ------------------------------------------------- 自走音量 (vel_drift)
  // Every edge already has its own volume - 力度× - and this gives that volume
  // a life of its own. Written as one nested object so there is a single name
  // for the setting, the same way the playing-style condition is written.
  // The three a synth patch is normally wired to, and nothing else: this is a
  // sticky channel-wide controller and a wrong guess quietens a machine that
  // will not give it back on its own.
  const SWELL_CCS = [[0, "—"], [1, "CC1 調變"], [11, "CC11 表情"], [74, "CC74 濾波"]];
  const DRIFT_SHAPES = [["sine", "起伏"], ["triangle", "來回"], ["ramp", "推上去"], ["breathe", "呼吸"]];

  /** 呼吸: a controller sweep across the life of a held note.
   *
   *  Velocity decides how a note STARTS and nothing after that moves, which is
   *  the whole of 「單純的長音持續按著會顯得呆板」. This moves after that. It
   *  needs both halves: the instrument has to say which controller its patch
   *  listens to (左邊樂器清單), and the lane has to say how deep and how slow.
   */
  function edgeSwell(e) {
    let cur = e, inst = null;
    const wrap = document.createElement("label");
    wrap.className = "mie-field mie-field-drift mie-field-swell";
    wrap.innerHTML = '<span class="mie-fl">呼吸</span>'
      + '<span class="mie-drift-row">'
      + '<input class="mie-dd" type="range" min="0" max="1" step="0.05" title="幅度：0 = 不動；1 = 從全開一路呼吸到全關">'
      + '<span class="mie-dv"></span>'
      + '<input class="mie-db" type="number" min="2" max="128" step="2" title="一個呼吸幾拍">'
      + '<select class="mie-ds" title="形狀"></select>'
      + '<input class="mie-dt" type="number" min="0.05" max="1" step="0.05" '
      + 'title="上限：呼吸最大聲的時候到哪裡。這是「太大聲」該調的地方——'
      + 'pad 音色多半不吃力度，強度拉再低也沒用，它聽的是這個 CC">'
      + '</span>';
    const dd = wrap.querySelector(".mie-dd"), dv = wrap.querySelector(".mie-dv");
    const db = wrap.querySelector(".mie-db"), ds = wrap.querySelector(".mie-ds");
    const dt = wrap.querySelector(".mie-dt");
    DRIFT_SHAPES.forEach(([v, label]) => {
      const o = document.createElement("option"); o.value = v; o.textContent = label; ds.appendChild(o);
    });
    const read = (x) => (x && typeof x.swell === "object" && x.swell) || {};
    const paint = () => {
      const d = read(cur);
      dd.value = d.depth || 0;
      dv.textContent = Number(d.depth || 0).toFixed(2);
      db.value = d.beats || 8;
      ds.value = d.shape || "breathe";
      dt.value = d.top === undefined ? 1 : d.top;
      const on = d.depth > 0 || (d.top !== undefined && d.top < 1);
      wrap.classList.toggle("is-off", !on);
      // Depth without a controller sends nothing at all, and a control that
      // silently does nothing is worse than one that is not there.
      const cc = inst && inst.swell_cc;
      wrap.classList.toggle("is-deaf", !!on && !cc);
      wrap.title = cc
        ? `送 CC${cc} 給 CH${cur.dst}，在音響著的時候上下擺。休息值一定是 127——`
          + `這條線一停、按 PANIC、引擎關掉，都會把它還回去`
        : `CH${cur.dst} 還沒說它聽哪一個 CC，所以這裡設什麼都不會送出去。`
          + `到左邊樂器清單把 CH${cur.dst} 的 CC 選起來`;
    };
    const push = () => {
      const v = { depth: Number(dd.value), beats: Number(db.value), shape: ds.value,
                  top: Number(dt.value) };
      cur.swell = v;
      send({ type: "set", path: `edge.${cur.id}.swell`, value: v });
      paint();
    };
    dd.addEventListener("input", () => { dv.textContent = Number(dd.value).toFixed(2); });
    dd.addEventListener("change", push);
    db.addEventListener("change", push);
    ds.addEventListener("change", push);
    dt.addEventListener("change", push);
    wrap.sync = (fresh, insts) => {
      cur = fresh;
      inst = (insts || []).find((i) => i.ch === fresh.dst) || null;
      if (!wrap.contains(document.activeElement)) paint();
    };
    paint();
    return wrap;
  }

  function edgeDrift(e) {
    let cur = e;
    const wrap = document.createElement("label");
    wrap.className = "mie-field mie-field-drift";
    wrap.title = "讓這條線的音量自己慢慢動。幅度 0 = 不動。幅度是「你設的力度× 的上下比例」，"
      + "所以你調的那個值仍然是中心，不會被蓋掉";
    wrap.innerHTML = '<span class="mie-fl">自走音量</span>'
      + '<span class="mie-drift-row">'
      + '<input class="mie-dd" type="range" min="0" max="0.8" step="0.05" title="幅度">'
      + '<span class="mie-dv"></span>'
      + '<input class="mie-db" type="number" min="2" max="128" step="2" title="一個循環幾拍">'
      + '<select class="mie-ds" title="形狀"></select></span>';
    const dd = wrap.querySelector(".mie-dd"), dv = wrap.querySelector(".mie-dv");
    const db = wrap.querySelector(".mie-db"), ds = wrap.querySelector(".mie-ds");
    DRIFT_SHAPES.forEach(([v, label]) => {
      const o = document.createElement("option"); o.value = v; o.textContent = label; ds.appendChild(o);
    });
    const read = (x) => (x && typeof x.vel_drift === "object" && x.vel_drift) || {};
    const paint = () => {
      const d = read(cur);
      dd.value = d.depth || 0;
      dv.textContent = Number(d.depth || 0).toFixed(2);
      db.value = d.beats || 16;
      ds.value = d.shape || "sine";
      wrap.classList.toggle("is-off", !(d.depth > 0));
    };
    const push = () => {
      const v = { depth: Number(dd.value), beats: Number(db.value), shape: ds.value };
      cur.vel_drift = v;
      dv.textContent = v.depth.toFixed(2);
      wrap.classList.toggle("is-off", !(v.depth > 0));
      send({ type: "set", path: `edge.${cur.id}.vel_drift`, value: v });
    };
    dd.addEventListener("input", () => { dv.textContent = Number(dd.value).toFixed(2); });
    dd.addEventListener("change", push);
    db.addEventListener("change", push);
    ds.addEventListener("change", push);
    wrap.sync = (fresh) => { cur = fresh; if (!wrap.contains(document.activeElement)) paint(); };
    paint();
    return wrap;
  }

  const CHOICE_FIELD = {
    spacing: ["聲部間距", "聲部之間至少隔多遠。密集 = 疊在一起（原本的樣子）；"
                        + "開放 = 根音、五度、九度，三度被推到上面變成十度，中頻讓出來給人聲或旋律"],
    pedal: ["持續低音", "一個聲部釘在調的主音或五度，上面的和弦怎麼換它都不動。"
                      + "它不會被輪替掉——會被輪掉的就不是持續低音了"],
  };
  function edgeChoice(e, key) {
    const wrap = document.createElement("label");
    wrap.className = "mie-field";
    const f = CHOICE_FIELD[key];
    wrap.title = f ? f[1] : key;
    wrap.innerHTML = `<span>${f ? f[0] : key}</span><select></select>`;
    const sel = wrap.querySelector("select");
    CHOICES[key].forEach((v) => {
      const o = document.createElement("option");
      o.value = v;
      o.textContent = (CHOICE_LABEL[key] && CHOICE_LABEL[key][v]) || v;
      sel.appendChild(o);
    });
    sel.dataset.key = key;
    sel.value = String(e[key] === undefined || e[key] === true ? CHOICES[key][0] : e[key]);
    sel.addEventListener("change", () => send({ type: "set", path: `edge.${e.id}.${key}`, value: sel.value }));
    return wrap;
  }

  function renderEdges(s) {
    const box = $("#mieEdges");
    const seen = new Set();
    // Which channels currently have anything feeding them. A hop-2 edge answers
    // another LANE's notes, so with its source switched off it cannot fire at
    // all - and it looks exactly like a lane that is simply quiet. On the 16:23
    // take iridium_to_wavestate was soloed for 91 seconds and 166 played notes
    // and produced nothing, because both edges that feed CH11 were off. That is
    // the kind of silence the panel has to explain rather than just display.
    const fed = new Set(s.edges.filter((e) => e.enabled).map((e) => e.dst));
    const load = chLoad(s.edges);
    // how many enabled lanes each instrument is carrying, for the breath's
    // "this rides the others too" note
    const sharing = new Map();
    s.edges.forEach((e) => { if (e.enabled) sharing.set(e.dst, (sharing.get(e.dst) || 0) + 1); });
    s.edges.forEach((e) => {
      seen.add(e.id);
      let el = edgeEls.get(e.id);
      if (!el) {
        el = document.createElement("div"); el.className = "mie-edge";
        // Three lines rather than one long strip: the name and the lane badge
        // said the same thing twice at opposite ends of the row and still
        // collided at three-across, and the pad was a letterbox because it was
        // taking whatever width was left over.
        // The edge id used to sit next to the lane badge and say the same
        // thing twice - 「sustain13 / sustain_event61」. One name, and it is the
        // lane's: that is the name the piano roll paints, the name the legend
        // chips carry, and the name the events use. The id stays in the row's
        // tooltip for when something has to be looked up in a log.
        el.innerHTML = `<div class="mie-edge-head">
            <input type="checkbox" title="啟用">
            <span class="algo"></span>
            <span class="fires" title="這一趟這條線被觸發的次數"></span>
            <button class="mie-more" title="參數">▾</button>
          </div>
          <div class="route"><span class="src"></span> → <select class="dst mie-sel" title="這條線送到哪一台。換過去的時候，它正在舊那台上響的音會先收掉"></select><span class="rest"></span></div>
          <div class="mie-edge-quick">
            <div class="mie-quick-right">
              <label class="mie-vel" title="這條線自己的音量：生成音的力度倍率（全域音量之外，各聲部各自的）">
                <span class="mie-fl">強度</span>
                <input class="mie-fs" type="range" min="0" max="2" step="0.05">
                <span class="mie-fn mie-vel-v">1.00</span>
                <span class="mie-vel-hit"></span>
              </label>
              <label class="mie-vel mie-breath" hidden
                     title="呼吸：這條線響著的時候，把樂器的表情 CC 上下擺。深度 0 = 不動">
                <span class="mie-fl">呼吸</span>
                <input class="mie-fs" type="range" min="0" max="1" step="0.05">
                <span class="mie-fn mie-breath-v">0.00</span>
                <input class="mie-breath-top" type="range" min="0.05" max="1" step="0.05"
                       title="上限：呼吸最大聲的時候到哪裡">
                <span class="mie-vel-hit mie-breath-cc"></span>
              </label>
              <span class="mie-pad-lbl"></span>
            </div>
          </div>
          <div class="mie-edge-body" hidden></div>`;
        const quick = el.querySelector(".mie-edge-quick");
        const pad = makePad(e);
        if (pad) {
          quick.insertBefore(pad, quick.firstChild);
          pad.setLabel(el.querySelector(".mie-pad-lbl"));
          el._pad = pad;
        } else {
          quick.classList.add("no-pad");
        }
        el._vel = makeVel(e, el.querySelector(".mie-vel"));
        el._breath = makeBreath(e, el.querySelector(".mie-breath"));
        el.querySelector('input[type=checkbox]').addEventListener("change", (ev) => send({ type: "set", path: `edge.${e.id}.enabled`, value: ev.target.checked }));
        el.querySelector(".dst").addEventListener("change", (ev) =>
          send({ type: "set", path: `edge.${e.id}.dst`, value: Number(ev.target.value) }));
        const body = el.querySelector(".mie-edge-body");
        el.querySelector(".mie-more").addEventListener("click", () => {
          body.hidden = !body.hidden;
          // an open edge takes the whole width of the list back - its parameter
          // grid needs the room, and at half width every field wrapped
          el.classList.toggle("is-open", !body.hidden);
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
        el._drift = edgeDrift(e);
        body.appendChild(el._drift);
        el._swell = edgeSwell(e);
        body.appendChild(el._swell);
        const choices = ["constraint", "align", "voice_lead", "collision"];
        if (e.algo === "silence") choices.push("silence_mode");
        // Only the two lanes that lay a chord down have a voicing to shape.
        if (e.algo === "silence" || e.algo === "sustain") choices.push("spacing", "pedal");
        choices.forEach((k) => body.appendChild(edgeChoice(e, k)));
        box.appendChild(el); edgeEls.set(e.id, el);
      }
      // The same hue the piano roll paints this lane with, so an orange bar in
      // the picture leads straight to the row that made it. The roll owns the
      // palette; asking it keeps one definition rather than two that drift.
      if (window.MieRoll && window.MieRoll.hueFor) {
        el.style.setProperty("--lane-h", window.MieRoll.hueFor(e.lane || e.algo));
      }
      const src = e.src === 0 ? "HUMAN" : `CH${e.src}`;
      el._vel.sync(e, s);
      el._breath.sync(e, s.instruments || [], sharing);
      el.querySelector(".src").textContent = src;
      // Which instrument a lane speaks through was only ever in the scene file.
      // "PANIC 是因為 wavestate 的音色多變不適合當延音" - the Wavestate is a
      // wave-sequencing box, which is the wrong thing to hold a long note on,
      // and the only way to move the lane was to edit JSON and restart.
      syncDst(el.querySelector(".dst"), e, s.instruments || [], load);
      el.querySelector(".rest").textContent = ` · p ${e.prob} · ${e.constraint}`
        + (e.delay_beats ? ` · ${e.delay_beats} beat` : "") + (e.delay_ms ? ` · ${e.delay_ms} ms` : "");
      const lane = e.lane || e.algo;
      el.querySelector(".algo").textContent = LANE_LABEL[lane] || lane;
      el.querySelector(".algo").title = `${e.id}　${e.algo}　lane: ${lane}`;
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
      // A bare "0" on a row is a number with no noun. It is how many times this
      // line has fired since the engine came up, and it has to say so.
      fires.textContent = e.mute ? "靜音"
        : (e.drops ? `觸發 ${e.fires || 0} ⚠${e.drops}` : `觸發 ${e.fires || 0}`);
      fires.classList.toggle("has-drops", !!e.drops || !!e.mute);
      fires.title = e.mute || (e.drops
        ? `${e.drops} 個音被丟掉了——多半是音域或八度把它推到樂器範圍外`
        : "");
      if (el._when && !el._when.contains(document.activeElement)) el._when.sync(e);
      if (el._drift) el._drift.sync(e);
      if (el._swell) el._swell.sync(e, s.instruments);
      // A lane whose condition does not match right now is not broken and not
      // idle - it is WAITING, and it should say which playing it is waiting
      // for. Silence you can explain is not the same as silence you cannot.
      const want = edgeWhenTexture(e);
      const waiting = want.length > 0 && s.state.texture && !want.includes(s.state.texture);
      el.classList.toggle("is-waiting", !!waiting);
      if (waiting && !e.mute) {
        fires.textContent = `觸發 ${e.fires || 0} ⏸`;
        fires.title = `這條線只在「${want.map((k) => TEX[k] || k).join("、")}」時說話，你現在是「${TEX[s.state.texture] || s.state.texture}」`;
      }
      // stated after the texture branch so a real 靜音 or a drop still wins
      const starved = e.src !== 0 && e.enabled && !fed.has(e.src);
      el.classList.toggle("is-starved", starved);
      if (starved && !e.mute && !e.drops) {
        fires.textContent = "沒有來源";
        fires.title = `這條線回應的是 CH${e.src} 上生成的音，但現在沒有任何開著的邊送到 CH${e.src}。`
                    + `要聽它，先把送到 CH${e.src} 的那條線打開`;
      }
      el.classList.toggle("is-mute", !!e.mute);
      el.classList.toggle("hot", e.ago !== null && e.ago !== undefined && e.ago < 2);
      el.classList.toggle("off", !e.enabled);
    });
    edgeEls.forEach((el, id) => { if (!seen.has(id)) { el.remove(); edgeEls.delete(id); } });
  }
  // Long lanes want an instrument that can hold a note. `sustain_ok` in
  // instruments.json already says which ones can, and the mark is the whole
  // point of the list: choosing by name alone is how a pad ended up on a
  // wave-sequencing synth.
  const LONG_ALGOS = new Set(["sustain", "silence"]);
  /** How crowded each instrument is: lanes pointing at it, and notes it has
   *  actually had to throw away. The drop count is the honest signal - counting
   *  lanes guesses, whereas a `voices` drop is the instrument saying it ran
   *  out. On the 17:32 take CH13 carried an echo with three repeats AND a
   *  two-voice sustain on four voices, wanted six notes at once, and lost
   *  three. Nothing on the panel said so until the notes were already gone. */
  function chLoad(edges) {
    const per = new Map();
    edges.forEach((e) => {
      if (!e.enabled) return;
      const d = per.get(e.dst) || { lanes: 0, drops: 0 };
      d.lanes += 1;
      d.drops += e.drops || 0;
      per.set(e.dst, d);
    });
    return per;
  }
  function syncDst(sel, e, insts, load) {
    const want = insts.map((i) => {
      const d = load.get(i.ch) || {};
      return `${i.ch}|${i.name}|${i.enabled}|${i.sustain_ok}|${d.lanes || 0}|${d.drops || 0}`;
    }).join(",");
    if (sel._want !== want) {                    // rebuild only when something moved
      sel._want = want;
      sel.innerHTML = "";
      insts.forEach((i) => {
        const d = load.get(i.ch) || { lanes: 0, drops: 0 };
        const o = document.createElement("option");
        o.value = i.ch;
        o.textContent = `CH${i.ch} ${i.name}`
          + (i.sustain_ok ? " ·長音" : "")
          // short on purpose: this string also has to survive being truncated
          // inside a 200 px select, and what must survive is ·長音 and the ⚠
          + `（${d.lanes}條/${i.max_voices}聲${d.drops ? ` ⚠${d.drops}` : ""}）`
          + (i.enabled ? "" : "（關）");
        o.title = `${i.role}　${i.max_voices} 聲部　`
          + (i.sustain_ok ? "適合長音" : "沒有標記為適合長音")
          + (d.drops ? `　這台已經因為聲部不夠丟掉 ${d.drops} 個音` : "");
        sel.appendChild(o);
      });
    }
    if (document.activeElement !== sel) sel.value = String(e.dst);
    const here = load.get(e.dst) || { drops: 0 };
    const inst = insts.find((i) => i.ch === e.dst) || {};
    // Two different warnings, one colour. Neither is an error - the player may
    // want exactly this - but both are worth seeing before they are heard.
    const odd = LONG_ALGOS.has(e.algo) && !inst.sustain_ok;
    sel.classList.toggle("is-odd", odd || here.drops > 0);
    sel.title = here.drops > 0
      ? `CH${e.dst} 已經因為聲部不夠丟掉 ${here.drops} 個音（這台只有 ${inst.max_voices} 個聲部，`
        + `${(load.get(e.dst) || {}).lanes} 條線指著它）`
      : (odd ? `這是長音聲部，但 CH${e.dst} 沒有標長音——音會被砍在 8 秒。`
             + `要嘛換一台，要嘛在左邊樂器清單把它的「長」打開`
             : "這條線送到哪一台。換過去的時候，它正在舊那台上響的音會先收掉");
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
    if (e.type === "save_conflict") onSaveConflict(e);
    if (e.type === "log_saved") {
      // Say it on the button that was pressed. The button lives in the roll
      // now, so when the roll is closed - Ctrl+S, mid-take, without looking -
      // nobody sees this; the notice line under the columns says it instead,
      // which is why `log_saved` is in NOTABLE.
      const b = $("#mieLogSave");
      b.disabled = false;
      b.textContent = `已存 ${e.human} 音`;
      b.title = `存成 ${e.path}（到此 ${e.human} 個人類音 / ${e.gen} 個生成音）。新的一段已經在錄了`;
      setTimeout(() => { b.textContent = "存這段"; }, 4000);
      if (roll) roll.showSaved(e.path);
    }
    // The per-note flow - human, gen, sched, edge, off, resnap, skip, pedal -
    // is not here any more. It scrolled a hundred lines a minute and could only
    // be read after the fact, while the roll draws exactly the same events as a
    // picture you can take in mid-phrase. What is left is the handful the
    // player must not miss, said once, on a line that stays put.
    if (!NOTABLE.has(e.type)) return;
    let cls = "", txt = "";
    switch (e.type) {
      case "drop": cls = "drop"; txt = `DROP ${e.reason} ch${e.ch} ${nn(e.note)} ${e.lane || ""} ${e.edge || ""}`; break;
      case "style": cls = "mode"; txt = e.action === "clear" ? "風格 → 取消"
          : `風格 → ${e.id}（${e.edges} 條邊${e.kept ? `，保留你調過的 ${e.kept} 項` : ""}）`; break;
      case "replay": cls = "mode"; txt = e.action === "start"
        ? `回放送出 ${e.notes} 個音${e.human ? "（含你彈的）" : ""}${e.skipped ? `（跳過 ${e.skipped} 個：關掉的樂器）` : ""} ×${e.speed}`
        : (e.action === "refused" ? `回放被拒絕：${e.why === "panicked" ? "引擎在 PANIC 狀態，先按 RESUME" : "目前是 BYPASS"}`
                                  : `回放停止（收掉 ${e.released} 個音）`); break;
      case "touched": cls = "mode"; txt = e.path === "*"
        ? `交還 ${e.n} 個手動設定給風格` : `交還 ${e.path} 給風格`; break;
      case "save_conflict":
        cls = "drop";
        txt = `沒有存：${e.path} 在 ${e.when} 被別的地方改過了。`
            + `重新選一次這個 scene 可以拿到新的內容；`
            + `要用畫面上這一份蓋過去，就再按一次「儲存 ⚠」`;
        break;
      case "log_saved": cls = "mode"; txt = `錄音存成 ${e.path}（到此 ${e.human} 個人類音 / ${e.gen} 個生成音）`; break;
      case "panic": cls = "panic"; txt = `PANIC (${e.reason}) ${e.notes} notes released`; break;
      case "loop": cls = "loop"; txt = `LOOP ch${e.ch} ${nn(e.note)} came back on MIE In`; break;
      case "advice_muted": cls = "mode"; txt = e.on
        ? `不再提醒「${e.id}」（目前靜音 ${e.n} 項；用 --forget 開機可以全部復原）`
        : `恢復提醒「${e.id}」`; break;
      case "all_edges": cls = "mode"; txt = e.on
        ? `全開：${e.n} 條邊打開了（共 ${e.total} 條）`
        : `全部略過：${e.n} 條邊讓開了，聲音已收掉。把想聽的那一條勾回來`; break;
      case "error": cls = "drop"; txt = `出錯：${e.where} — ${e.err}`; break;
      case "mode": cls = "mode"; txt = `mode → ${e.mode}`; break;
      case "scene": cls = "mode"; txt = `scene → ${e.id} ${e.name}`; break;
      default: cls = "mode"; txt = JSON.stringify(e);
    }
    showNotice(cls, txt, e.t);
  }

  // Which events earn a line. Everything else is a note, and notes are the
  // roll's job.
  // `set` and `control` are deliberately NOT here: a set fires on every step of
  // a slider drag, and UC4 already has its own readout in the top bar. A single
  // line that flickers through fifty values is not a thing anyone reads.
  const NOTABLE = new Set(["drop", "panic", "loop", "save_conflict", "log_saved",
                           "style", "scene", "mode", "replay", "touched", "error",
                           "all_edges", "advice_muted"]);
  // A PANIC or a refused save has to survive being looked away from; a style
  // change is news for a moment and then clutter. The loud ones stay until the
  // next thing happens, the rest fade.
  const STICKY = new Set(["panic", "loop", "drop"]);
  let noticeTimer = 0;
  function showNotice(cls, txt, t) {
    const box = $("#mieNotice");
    box.className = `mie-notice mie-ev-${cls}`;
    box.innerHTML = `<span class="t">${Number(t).toFixed(2)}</span>${txt}`;
    box.hidden = false;
    clearTimeout(noticeTimer);
    if (!STICKY.has(cls)) noticeTimer = setTimeout(() => { box.hidden = true; }, 12000);
  }

  // ---------------------------------------------------------------- controls
  // EVERY binding below runs exactly once, at load. Putting one inside a render
  // function attaches another copy on every snapshot, and a single click then
  // fires all of them: on the 19:27 take one press of Revert sent 117 reverts,
  // which wrote 24,777 settings, and the panel stopped responding. If a control
  // ever needs binding from a render path, remove the old listener first.
  $("#miePanic").addEventListener("click", () => send({ type: "panic" }));
  $("#mieResume").addEventListener("click", () => send({ type: "resume" }));
  // Hearing ONE line means silencing the other ten. A checkbox at a time is
  // eleven clicks out and eleven back, which is enough friction that lanes go
  // untested. 全部略過 then tick the one you want.
  $("#mieAllOn").addEventListener("click", () => send({ type: "all_edges", on: true }));
  $("#mieAllOff").addEventListener("click", () => send({ type: "all_edges", on: false }));
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
  // ------------------------------------------------------------ 存這段
  // The engine writes the log continuously - it always did - but the only way
  // to CLOSE a recording was to stop the engine and press q at the console,
  // which is a thing you least want to do in the middle of playing. This ends
  // the segment into its own file and immediately starts the next one.
  $("#mieLogSave").addEventListener("click", () => {
    const b = $("#mieLogSave");
    b.disabled = true; b.textContent = "存…";
    send({ type: "log_save" });
    setTimeout(() => { b.disabled = false; b.textContent = "存這段"; }, 1500);
  });

  // ------------------------------------------------------------ 落差提示
  // It only ever SAYS. Nothing here changes a setting until the player presses
  // the button, and what it presses is an ordinary `set` - so it shows up on
  // the sliders and Ctrl+Z puts it back like anything else.
  let adviceSig = "";
  /** When it first spoke. A reading that only shows what is true THIS INSTANT
   *  can only be read by someone already watching the screen, and nobody
   *  playing is: `tension_gap` was up for fourteen seconds of the 21:05 take.
   *  So the panel says how long ago instead of pretending it is news. */
  function adviceWhen(a) {
    if (!lastSnap || a.first_t === undefined) return "";
    const s = Math.max(0, lastSnap.t - a.first_t);
    const ago = s < 45 ? "剛剛" : s < 5400 ? `${Math.round(s / 60)} 分鐘前` : "很久以前";
    return a.live === false ? `　${ago}（現在已經沒有了）` : `　${ago}`;
  }

  function renderAdvice(list) {
    const box = $("#mieAdvice");
    const pop = $("#mieAdvicePop");
    box.hidden = !list.length;
    if (!list.length) { pop.hidden = true; adviceSig = ""; return; }
    $(".mie-adv-n").textContent = list.length;
    box.classList.toggle("has-warn", list.some((a) => a.level === "warn"));
    // `live` is in the signature: an entry that has stopped being true has to
    // repaint once, to say so.
    const sig = list.map((a) => a.id + a.text + a.live).join("|");
    if (sig === adviceSig) return;               // do not rebuild under the cursor
    adviceSig = sig;
    pop.innerHTML = "";
    list.forEach((a) => {
      const row = document.createElement("div");
      row.className = "mie-adv-row" + (a.level === "warn" ? " is-warn" : "")
        + (a.live === false ? " is-past" : "");
      const btns = (a.fix_label ? `<button class="mie-btn mie-adv-fix">${a.fix_label}</button>` : "")
        + (a.alt && a.alt.style ? `<button class="mie-btn mie-adv-alt">切換風格</button>` : "")
        + `<button class="mie-btn mie-adv-read" title="看過了。它會消失，`
        + `但如果又發生一次還是會再出現">看過了</button>`
        + `<button class="mie-btn mie-adv-mute" title="這件事你已經決定了，不用再提醒。`
        + `記在引擎那邊，重開也不會回來">不用再提</button>`;
      row.innerHTML = `<div class="mie-adv-txt">${a.text}`
        + `<span class="mie-adv-when">${adviceWhen(a)}</span></div>`
        + `<div class="mie-adv-why">${a.why || ""}</div>`
        + `<div class="mie-adv-act">${btns}</div>`;
      row.querySelector(".mie-adv-read").addEventListener("click", () =>
        send({ type: "read_advice", id: a.id }));
      row.querySelector(".mie-adv-mute").addEventListener("click", () =>
        send({ type: "mute_advice", id: a.id, on: true }));
      const fix = row.querySelector(".mie-adv-fix");
      if (fix) fix.addEventListener("click", () => {
        (a.fix || []).forEach(([path, value]) => send({ type: "set", path, value }));
        send({ type: "read_advice", id: a.id });   // acting on it is reading it
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
  const LANE_LABEL = {
    human: "你", shadow: "影子", echo: "回音", echo2: "回音2", follow: "跟隨",
    phrase: "樂句", sustain: "延續", pad: "襯底", texture: "織體",
  };

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

  // ------------------------------------------------------------ 面板記憶
  // What the PANEL looked like, per browser. The scene and the style are the
  // engine's business and are remembered on its side, so a restart comes up on
  // the right graph whether or not this browser has ever been opened.
  //
  // Deliberately NOT remembered: anything that makes a sound. 送出 MIDI comes
  // back off every time - a panel that starts playing into the room because of
  // what someone did yesterday is not a convenience.
  const PREF = "livechord_mie_panel";
  function prefs() {
    try { return JSON.parse(localStorage.getItem(PREF) || "{}") || {}; }
    catch (e) { return {}; }
  }
  function setPref(k, v) {
    try {
      const p = prefs(); p[k] = v;
      localStorage.setItem(PREF, JSON.stringify(p));
    } catch (e) { /* private window, or storage off: the panel still works */ }
  }

  // ------------------------------------------------------------ 鋼琴捲軸
  // Created lazily: until the player asks for it there is no canvas, no
  // animation frame, and `pushEvent` does nothing extra on the panel's hot
  // path. The three columns above are where the work happens during a take
  // and this must not disturb them.
  let roll = null, rollSaveTimer = 0;
  const rollBox = $("#mieRoll");
  function toggleRoll(on) {
    const wasOpen = !rollBox.hidden;
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
      roll = window.MieRoll.create(rollBox, {
        send, setPref, pref: (k) => prefs()[k],
      });
      window.__mieRoll = roll;   // a handle for the console: the roll is the
                                 // one part of this panel worth poking at from
                                 // devtools while a take is being reviewed
    }
    // Opening it starts it drawing. It IS the event stream now, and a roll that
    // opens empty and stays empty until you find the 即時 button reads as broken.
    // Only ever on the way IN: pressing 即時 off and leaving the panel open has
    // to stick, or the button does nothing.
    if (show) {
      // the height the player dragged it to last time. Applied before the
      // canvas is measured, or the roll draws itself at the default size and
      // then jumps.
      const h = prefs().rollH;
      if (h) rollBox.style.height = `${h}px`;
    }
    if (roll) {
      if (show) { if (!wasOpen) roll.setLive(true); roll.redraw(); }
      else roll.setLive(false);
    }
    // it lives below the three columns, which fill the screen - opening
    // something the player then cannot see is the same as not opening it
    if (show) rollBox.scrollIntoView({ behavior: "smooth", block: "end" });
  }
  // The roll is resized by dragging its bottom edge (CSS `resize: vertical`).
  // Remember where it was left, and tell the canvas - it sizes itself to its
  // box once, on redraw, so without this the picture keeps the old height and
  // the new space stays blank.
  let rollH = 0;
  function rememberRollHeight() {
    if (rollBox.hidden) return;
    // offsetHeight, not the observer's contentRect: `box-sizing: border-box` is
    // global here, so the height written back on the next visit is a BORDER
    // box. Saving the content box instead loses the padding and the border
    // every time, and the roll would come back 18 px shorter each session.
    const h = rollBox.offsetHeight;
    if (!h || h === rollH) return;
    rollH = h;
    if (roll) roll.redraw();          // the canvas measures its box as it draws
    clearTimeout(rollSaveTimer);
    rollSaveTimer = setTimeout(() => setPref("rollH", h), 400);
  }
  // Two ways in, because neither covers the other. The observer keeps the
  // picture filling the box WHILE the corner is being dragged; pointerup is
  // what actually ends the drag, and it still arrives when the observer does
  // not - a background tab suspends ResizeObserver along with rAF.
  if (window.ResizeObserver) new ResizeObserver(rememberRollHeight).observe(rollBox);
  rollBox.addEventListener("pointerup", rememberRollHeight);
  $("#mieRollBtn").addEventListener("click", () => { toggleRoll(); setPref("roll", !rollBox.hidden); });
  $("#mieRollClose").addEventListener("click", () => { toggleRoll(false); setPref("roll", false); });
  // Open unless this browser was left with it closed. It replaced the event
  // stream column, so the panel would otherwise come up with nowhere at all to
  // watch what the engine is doing.
  if (prefs().roll !== false) setTimeout(() => toggleRoll(true), 400);   // after the first snapshot

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
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      // The button lives in the roll now. It is still in the DOM when the roll
      // is closed, so clicking it works either way - 存這段 is the one thing
      // you press without looking, mid-take, and it must not depend on a panel
      // being open.
      e.preventDefault(); $("#mieLogSave").click(); return;
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
  // A conflict is not a dead end: the player may still decide their in-memory
  // version is the one they want. It has to be a SECOND, deliberate press -
  // the whole point is that the first one did not silently discard anything.
  function onSaveConflict(e) {
    saveConflict = true;
    const b = $("#mieSave");
    b.classList.add("is-conflict");
    b.textContent = "儲存 ⚠";
    b.title = `${e.path} 在 ${e.when} 被別的地方改過了。再按一次會用你目前的設定覆蓋掉它`;
  }
  $("#mieSave").addEventListener("click", () => {
    // Name the scene an empty answer overwrites. "覆寫目前的" is only obvious
    // to whoever wrote it; the player saved into a second file for two sessions
    // and thought the global knobs were not being saved at all.
    const cur = (lastSnap && lastSnap.scene && lastSnap.scene.id) || "";
    const as = prompt(`另存為新 scene 的編號（留空 = 覆寫 ${cur}）`, "");
    if (as === null) return;
    const id = as.trim() || undefined;
    // The second press is the deliberate one. Save-as never conflicts (it
    // writes a new name), so the flag only arms the overwrite path.
    send({ type: "save_scene", as: id, force: !id && saveConflict });
    if (!id && saveConflict) {
      saveConflict = false;
      const b = $("#mieSave");
      b.classList.remove("is-conflict");
      b.textContent = "儲存";
      b.title = "";
    }
  });
  $("#mieHaltResume").addEventListener("click", () => send({ type: "resume" }));
  $("#mieModeSel").addEventListener("change", (e) => send({ type: "mode", value: e.target.value }));
  $("#mieSceneSel").addEventListener("change", (e) => send({ type: "scene", id: e.target.value }));
  // The globals, and which of them the player now owns. A knob you have moved
  // by hand stays where you left it, the way a pedal does - so the panel has to
  // SHOW which ones those are, or the rule is invisible and a style silently
  // doing nothing to a slider is as confusing as it silently overwriting one.
  const GLOBAL_SLIDERS = [["gMaster", "master_gain"], ["gTension", "tension"],
    ["gDensity", "density"], ["gTime", "time"], ["gProb", "prob_scale"],
    ["gChaos", "chaos"], ["gRestraint", "restraint"]];
  GLOBAL_SLIDERS.forEach(([id, key]) => {
    const el = document.getElementById(id);
    el.addEventListener("input", () => { document.getElementById(id + "V").textContent = Number(el.value).toFixed(2); });
    el.addEventListener("change", () => send({ type: "set", path: `global.${key}`, value: Number(el.value) }));
    // click the label to hand it back, so a style may move it again
    const row = el.parentElement;
    const lbl = row.querySelector(".mie-mlbl");
    if (lbl) {
      lbl.addEventListener("click", () => {
        if (row.classList.contains("is-mine")) send({ type: "release", path: `global.${key}` });
      });
    }
  });

  function renderTouched(list) {
    const mine = new Set(list || []);
    GLOBAL_SLIDERS.forEach(([id, key]) => {
      const row = document.getElementById(id).parentElement;
      const on = mine.has(`global.${key}`);
      row.classList.toggle("is-mine", on);
      row.title = on
        ? "你自己調過這一格，所以風格不會再動它。點左邊的名稱交還給風格"
        : "";
    });
    const n = mine.size;
    const btn = $("#mieRelease");
    btn.hidden = !n;
    btn.textContent = `我調過 ${n}`;
    const NL = String.fromCharCode(10);
    btn.title = n
      ? "這 " + n + " 個設定是你手動調的，切換風格不會蓋掉它們：" + NL
        + [...mine].map((x) => "  " + x).join(NL) + NL
        + "按這裡全部交還給風格"
      : "";
  }
  $("#mieRelease").addEventListener("click", () => send({ type: "release" }));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      const now = Date.now();
      if (now - escArm < 600) { send({ type: "panic" }); escArm = 0; } else { escArm = now; }
    }
  });

  connect();
})();
