/* LiveChord MIE panel (plan §9, Phase 1: top bar + meters + edge list + event stream).
 * Talks to the engine process over ws://127.0.0.1:8810/ws. Static page can be served by
 * the engine itself (http://127.0.0.1:8810/mie) or by the LiveChord backend (/mie). */
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const WS_URL = "ws://127.0.0.1:8810/ws";
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
    const TEX = { quiet: "靜", sustained: "持續", chord: "和弦", arpeggio: "琶音", melody: "旋律" };
    $("#mieTexture").textContent = TEX[st.texture] || st.texture || "—";
    $("#mieHands").textContent = (st.lh && st.lh.length) ? `雙手 ${st.lh.length}+${st.rh.length}` : "";
    $("#mieBpm").textContent = st.bpm;
    $("#mieClock").textContent = st.clock + (st.pulse_conf ? ` ${Math.round(st.pulse_conf * 100)}%` : "");
    $("#mieBpm").parentElement.title = st.pulse_bpm
      ? `脈動推估 ${st.pulse_bpm} BPM，信心 ${Math.round(st.pulse_conf * 100)}%（自由速度的演奏本來就沒有明確脈動）`
      : "尚未從演奏推估出脈動";
    $("#mieBeat").textContent = `${st.beat}/${st.beats_per_bar}`;
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
      NUM("跟和弦移調", "follow_chord", 0, 1, 1, "1 = 重播時整句依和弦根音平行移調（Am→Dm 就整句 +5）"),
    ],
    shadow: [NUM("延遲(ms)", "delay_ms", 0, 500, 10, null),
             NUM("最長持續(s)", "max_hold_s", 0.5, 30, 0.5, null)],
    silence: [NUM("等待(s)", "after_s", 0.5, 20, 0.5, "安靜多久才進來"),
              NUM("聲部", "voices", 1, 6, 1, null),
              NUM("力度", "vel", 1, 127, 1, null),
              NUM("持續(s)", "hold_s", 1, 60, 1, null),
              NUM("釋放(拍)", "release_beats", 0, 8, 0.5, "你再彈之後多久淡出"),
              NUM("低", "low", 21, 108, 1, null), NUM("高", "high", 21, 108, 1, null)],
    sustain: [NUM("等待(s)", "after_s", 0.5, 20, 0.5, "按住多久才開始"),
              NUM("最短(小節)", "every_bars_min", 0.25, 8, 0.25, null),
              NUM("最長(小節)", "every_bars_max", 0.25, 8, 0.25, null),
              NUM("聲部", "voices", 1, 6, 1, null),
              NUM("力度", "vel", 1, 127, 1, null),
              NUM("持續(拍)", "hold_beats", 1, 32, 1, null),
              NUM("釋放(拍)", "release_beats", 0, 8, 0.5, null),
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
      send({ type: "set", path: `edge.${e.id}.${spec.x.key}`, value: x });
      send({ type: "set", path: `edge.${e.id}.${spec.y.key}`, value: y });
    };
    el.addEventListener("pointerdown", (ev) => {
      el.setPointerCapture(ev.pointerId); el.classList.add("is-live"); move(ev);
    });
    el.addEventListener("pointermove", (ev) => { if (el.hasPointerCapture(ev.pointerId)) move(ev); });
    const end = (ev) => {
      if (!el.classList.contains("is-live")) return;
      el.classList.remove("is-live");
      last = 0; move(ev);                          // make sure the last position lands
    };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
    el.render();
    return el;
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
      const partner = spec.key === "low" ? "high" : (spec.key === "high" ? "low" : null);
      if (partner) {
        const other = wrap.parentElement.querySelector(`.mie-fn[data-key="${partner}"]`);
        if (other) {
          const o = Number(other.value);
          if (spec.key === "low" && v > o - 12) pushPartner(wrap, partner, v + 12);
          if (spec.key === "high" && v < o + 12) pushPartner(wrap, partner, v - 12);
        }
      }
      sl.value = num.value = v;
      set(v);
    };
    sl.addEventListener("input", () => commit(sl.value));
    num.addEventListener("change", () => commit(num.value));
    return wrap;
  }

  function pushPartner(wrap, key, value) {
    const body = wrap.parentElement;
    const num = body.querySelector(`.mie-fn[data-key="${key}"]`);
    const sl = body.querySelector(`.mie-fs[data-key="${key}"]`);
    if (!num || !sl) return;
    const v = Math.max(Number(sl.min), Math.min(Number(sl.max), Math.round(value)));
    if (Number(num.value) === v) return;
    num.value = sl.value = v;
    num.dispatchEvent(new Event("change"));
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
        specs.forEach((spec) => body.appendChild(edgeField(e, spec)));
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
        if (document.activeElement === inp) return;
        const v = e[inp.dataset.key];
        if (v === undefined) return;
        inp.value = inp.tagName === "SELECT" ? String(v === true ? CHOICES[inp.dataset.key][0] : v) : v;
      });
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
      case "off": cls = "off"; txt = `  off ch${e.ch} ${nn(e.note)} (${e.why})`; break;
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
