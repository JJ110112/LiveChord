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
    $("#mieKey").textContent = st.key + (st.key_source === "player" ? " ▶" : st.key_source === "inferred" ? " ~" : "");
    $("#mieChord").textContent = st.chord || "—";
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
    syncSlider("gProb", g.prob_scale, 2); syncSlider("gChaos", g.chaos, 2); syncSlider("gRestraint", g.restraint, 2);
    if ($("#mieSceneSel").options.length && !$("#mieSceneSel").matches(":focus")) $("#mieSceneSel").value = s.scene.id;
    renderInstruments(s);
    renderEdges(s);
    renderStats(s);
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
      NUM("衰減", "vel_scale", 0.1, 1, 0.02, "每次回音的力度倍率，越大尾巴越長"),
      NUM("最小力度", "min_vel", 1, 64, 1, "低於此值就不再回音"),
      NUM("時值衰減", "dur_decay", 0.3, 1, 0.05, "每次回音變短的比例"),
      NUM("重疊", "max_overlap", 0.5, 4, 0.25, "同音回音允許重疊幾次；1 = 接續不重疊"),
    ],
    follow: [NUM("音程", "interval", -24, 24, 1, "半音，預設 7 = 五度")],
    phrase: [
      NUM("重複次數", "repeats", 1, 8, 1, "整句重播幾次"),
      NUM("句尾間隔(拍)", "phrase_gap_beats", 0.25, 8, 0.25, "多久沒有新音就算一句結束"),
      NUM("延遲(拍)", "delay_beats", 0, 8, 0.25, "句子結束後多久開始回來"),
      NUM("衰減", "vel_scale", 0.1, 1, 0.02, "每次重播的力度倍率"),
      NUM("時值衰減", "dur_decay", 0.3, 1, 0.05, "每次重播變短的比例；回音變遠也會變短"),
      NUM("最少音數", "min_notes", 1, 8, 1, "太短的不算一句"),
      NUM("最多音數", "max_notes", 2, 16, 1, "只回最後這幾個音"),
      NUM("最小力度", "min_vel", 1, 64, 1, null),
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

  function edgeField(e, spec) {
    const wrap = document.createElement("label");
    wrap.className = "mie-field";
    wrap.title = spec.hint || spec.key;
    const val = e[spec.key];
    wrap.innerHTML = `<span>${spec.label}</span><input type="number" min="${spec.min}" max="${spec.max}" step="${spec.step}">`;
    const inp = wrap.querySelector("input");
    inp.value = val === undefined ? "" : val;
    inp.dataset.key = spec.key;
    inp.addEventListener("change", () => send({ type: "set", path: `edge.${e.id}.${spec.key}`, value: Number(inp.value) }));
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
      el.querySelector(".fires").textContent = e.fires || 0;
      el.classList.toggle("hot", e.ago !== null && e.ago !== undefined && e.ago < 2);
      el.classList.toggle("off", !e.enabled);
    });
    edgeEls.forEach((el, id) => { if (!seen.has(id)) { el.remove(); edgeEls.delete(id); } });
  }
  function renderStats(s) {
    const st = s.stats, j = s.jitter || {};
    const rows = [["human", st.human_notes], ["gen sent", st.gen_sent], ["scheduled", st.gen_sched], ["dropped", st.dropped],
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
  $("#miePanic").addEventListener("click", () => send({ type: "panic" }));
  $("#mieResume").addEventListener("click", () => send({ type: "resume" }));
  $("#mieModeSel").addEventListener("change", (e) => send({ type: "mode", value: e.target.value }));
  $("#mieSceneSel").addEventListener("change", (e) => send({ type: "scene", id: e.target.value }));
  [["gProb", "prob_scale"], ["gChaos", "chaos"], ["gRestraint", "restraint"]].forEach(([id, key]) => {
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
