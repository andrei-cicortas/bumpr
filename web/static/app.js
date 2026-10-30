"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const fmtUsd = (x) => `$${(x || 0).toFixed(4)}`;
const fmtTok = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n || 0));
const domain = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };

const STEPS = [
  ["setup", "Setup"], ["baseline", "Baseline"], ["bump", "Bump"], ["break", "Break"],
  ["research", "Read docs"], ["fix", "Think & rewrite"], ["verify", "Verify"], ["report", "Pull request"],
];
const ULTRA = /ultra/i, NANO = /nano|lightning/i;

// Static build (GitHub Pages): no backend, recordings are plain JSON files next to the page.
const STATIC = document.documentElement.dataset.static === "1";
const API = {
  config: () => (STATIC ? Promise.resolve({ live: false, static: true }) : fetch("/api/config").then((r) => r.json())),
  samples: () => fetch(STATIC ? "data/samples.json" : "/api/samples").then((r) => r.json()),
  replay: (name) => fetch(STATIC ? `data/replays/${name}.json` : `/api/replays/${name}`).then((r) => r.json()),
};

let config = {};
let samples = [];
let session = null; // {token, replayTimer, source}

// ---------------------------------------------------------------------------------------------
async function boot() {
  [config, samples] = await Promise.all([API.config(), API.samples()]);
  renderSamples();
  $("#cta").addEventListener("click", () => startReplay("pandas-sales-report"));
  $("#glossBtn").addEventListener("click", () => { $("#gloss").hidden = !$("#gloss").hidden; });
  $("#devBtn").addEventListener("click", () => setMode("dev"));
  document.querySelectorAll(".modes button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  setMode(mode);
  $("#livenote").textContent = config.static
    ? "This hosted page replays real, unedited event logs of runs against Nemotron on Nebius Token Factory. Use “Run it yourself” for live runs."
    : config.live
      ? "Live runs use real Nemotron calls on Nebius Token Factory; recordings replay a real run's event log."
      : "Live runs are switched off on this deployment; recordings replay a real run's event log, unedited.";
  const params = new URLSearchParams(location.search);
  if (params.get("replay")) startReplay(params.get("replay"));
}

function renderSamples() {
  $("#samples").innerHTML = samples.map((s) => {
    const r = s.replay;
    const rec = r ? `<div class="rec">recorded: <span class="${r.success ? "ok" : ""}">${r.success ? "green" : "red"}</span> · ${r.tests} tests · ${fmtTime(r.elapsed)} · ${fmtUsd(r.cost)}</div>` : "";
    return `<div class="card" data-name="${s.name}">
      <h3>${esc(s.title)}</h3>
      <div class="ver">${esc(s.from)} <b>→</b> ${esc(s.to)}</div>
      <p>${esc(s.blurb)}</p>${rec}
      <div class="btns">
        <button class="ghost" data-act="replay" ${r ? "" : "disabled"}>▶ Watch</button>
        ${config.static
          ? `<button class="primary" data-act="local">Run it yourself</button>`
          : `<button class="primary" data-act="live" ${config.live ? "" : "disabled title='Live runs are disabled here'"}>Run live</button>`}
      </div></div>`;
  }).join("");
  $("#samples").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const name = btn.closest(".card").dataset.name;
    if (btn.dataset.act === "replay") startReplay(name);
    else if (btn.dataset.act === "local") $("#localDialog").showModal();
    else askLive(name);
  });
}

// ---------------------------------------------------------------------------------------------
function resetView(name) {
  if (session?.replayTimer) clearTimeout(session.replayTimer);
  if (session?.source) session.source.close();
  session = { token: Symbol(), speed: mode === "simple" ? 2 : 4, skip: false };
  $("#guide").innerHTML = "";
  document.querySelectorAll(".card").forEach((c) => c.classList.toggle("active", c.dataset.name === name));
  $("#empty").hidden = true;
  $("#runview").hidden = false;
  $("#feed").innerHTML = "";
  $("#history").innerHTML = "";
  $("#result").hidden = true;
  const s = samples.find((x) => x.name === name);
  $("#runtitle").innerHTML = `${esc(s.from)} <span class="arrow">→</span> ${esc(s.to)}`;
  $("#runsub").textContent = `${s.title} · samples/${name}`;
  $("#stepper").innerHTML = STEPS.map(([k, label]) => `<li data-step="${k}">${label}</li>`).join("");
  setStatus("running", "running");
  state = freshState();
  updateMeter(null);
  updateStats();
  $("#clock").textContent = "0:00";

  for (const id of ["#sumBroken", "#sumNow"]) $(id).textContent = "-";
  $("#sumLines").textContent = "0";
  $("#nowline").className = "nowline dev";
  $("#resultPlain").hidden = true;
  $("#nowline").textContent = "Starting…";
  document.querySelectorAll("#controls button").forEach((b) => b.classList.toggle("on", b.dataset.speed === "4"));
  window.scrollTo({ top: 0 });
}

let state = freshState();
function freshState() {
  return { calls: [], searches: 0, pages: 0, passages: 0, total: 0, thinking: {}, step: null, runs: 0, done: null };
}

function setStatus(cls, text) {
  const el = $("#status");
  el.className = `status ${cls}`;
  el.textContent = text;
}

// ---------------------------------------------------------------------------------------------
async function startReplay(name) {
  resetView(name);
  session.replaying = true;
  $("#controls").hidden = false;
  const data = await API.replay(name);
  const token = session.token;
  const events = data.events;
  let i = 0;
  const tick = () => {
    if (session.token !== token) return;
    if (i >= events.length) return;
    const ev = events[i++];
    handle(ev);
    const next = () => {
      if (session.token !== token || i >= events.length) return;
      const gap = Math.max(0, events[i].t - ev.t);
      const wait = session.skip ? 0 : Math.min(gap, 2.5) * 1000 / session.speed;
      session.replayTimer = setTimeout(tick, wait);
    };
    // In the simple view the replay pauses at each milestone until the viewer presses Continue.
    if (session.paused) session.resume = next; else next();
  };
  tick();
}

function resume() {
  if (!session?.paused) return;
  session.paused = false;
  const go = session.resume;
  session.resume = null;
  if (go) go();
}

// ---- view mode + guide card -------------------------------------------------------------------
let mode = "simple";
try { mode = localStorage.getItem("bumpr-mode") || "simple"; } catch { /* storage unavailable */ }

function setMode(m) {
  mode = m;
  document.body.classList.toggle("simple", m === "simple");
  document.querySelectorAll(".modes button").forEach((b) => b.classList.toggle("on", b.dataset.mode === m));
  try { localStorage.setItem("bumpr-mode", m); } catch { /* storage unavailable */ }
  if (m !== "simple") resume();
}

function activity(text) {
  if (session?.paused) return;
  const g = $("#guide");
  g.className = "guide g-activity";
  g.innerHTML = `<div class="spinner"></div><div class="g-body"><div class="g-text">${text}</div></div>`;
}

function milestone(icon, html, extra = "") {
  const g = $("#guide");
  const pause = session?.replaying && mode === "simple" && !session.skip;
  g.className = "guide";
  g.innerHTML = `<div class="g-icon">${icon}</div><div class="g-body"><div class="g-text">${html}</div>${extra}` +
    (pause ? `<div class="g-actions"><button class="primary" id="gNext">Continue ▶</button>` +
      `<button class="linkbtn" id="gSkip">Skip to the result</button></div>` : "") + `</div>`;
  if (pause) {
    session.paused = true;
    $("#gNext").onclick = resume;
    $("#gSkip").onclick = () => { session.skip = true; resume(); };
    $("#gNext").focus({ preventScroll: true });
  }
}

function miniDiff(diff, max = 12) {
  const lines = diff.split("\n").filter((l) => /^[+-]/.test(l) && !/^(\+\+\+|---)/.test(l)).slice(0, max);
  return `<pre class="diff g-mini">${diffHtml(lines.join("\n"))}</pre>`;
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("#controls button");
  if (!b || !session) return;
  document.querySelectorAll("#controls button").forEach((x) => x.classList.remove("on"));
  b.classList.add("on");
  if (b.dataset.speed === "skip") { session.skip = true; } else { session.speed = Number(b.dataset.speed); session.skip = false; }
});

function askLive(name) {
  if (!config.needs_code) return startLive(name, "");
  const dlg = $("#codeDialog");
  $("#codeInput").value = "";
  dlg.returnValue = "";
  dlg.showModal();
  dlg.addEventListener("close", function onClose() {
    dlg.removeEventListener("close", onClose);
    if (dlg.returnValue === "ok") startLive(name, $("#codeInput").value.trim());
  });
}

async function startLive(name, code) {
  resetView(name);
  $("#controls").hidden = true;
  const res = await fetch("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sample: name, code }) });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    handle({ t: 0, type: "error", message: body.detail || `HTTP ${res.status}` });
    setStatus("red", "not started");
    return;
  }
  const src = new EventSource(`/api/runs/${body.id}/events`);
  session.source = src;
  src.onmessage = (m) => handle(JSON.parse(m.data));
  src.addEventListener("end", () => src.close());
  src.onerror = () => { if (state.done) src.close(); };
}

// ---------------------------------------------------------------------------------------------
function handle(ev) {
  $("#clock").textContent = fmtTime(ev.t || 0);
  const fn = HANDLERS[ev.type];
  if (fn) fn(ev);
}

const feed = () => $("#feed");
function add(html, cls = "") {
  const div = document.createElement("div");
  div.className = `ev ${cls}`;
  div.innerHTML = html;
  feed().appendChild(div);
  if (!session?.skip) div.scrollIntoView({ block: "nearest", behavior: "smooth" });
  return div;
}
function head(dot, who, text, meta = "") {
  return `<div class="ev-head"><span class="dot ${dot}"></span><span class="who">${who}</span><span>${text}</span><span class="meta">${meta}</span></div>`;
}

const HANDLERS = {
  phase(ev) {
    const idx = STEPS.findIndex(([k]) => k === ev.phase);
    document.querySelectorAll("#stepper li").forEach((li, i) => {
      li.classList.toggle("done", i < idx);
      li.classList.toggle("now", i === idx);
    });
    const label = STEPS[idx]?.[1] || ev.phase;
    if (PLAIN[ev.phase]) $("#nowline").textContent = PLAIN[ev.phase](ev);
    if (SIMPLE[ev.phase]) activity(SIMPLE[ev.phase](ev));
    const d = document.createElement("div");
    d.className = "divider";
    d.innerHTML = `${esc(label)} <span>${esc(ev.detail)}</span>`;
    feed().appendChild(d);
  },
  versions(ev) {
    state.pkg = ev;
    $("#runtitle").innerHTML = `${esc(ev.package)} ${esc(ev.old)} <span class="arrow">→</span> ${esc(ev.new)}`;

  },
  tests(ev) {
    state.runs += 1;
    if (ev.label === "baseline") state.total = ev.passed;
    updateMeter(ev);
    const score = `${ev.passed} / ${state.total || ev.passed + ev.failed}`;
    $("#sumNow").innerHTML = `<span class="${ev.green ? "good" : "bad"}">${score}</span>`;
    const n = state.total || ev.passed + ev.failed;
    const pkg = state.pkg ? `${state.pkg.package} ${state.pkg.new.split(".")[0]}` : "the new version";
    if (ev.label === "baseline") {
      milestone("✅", `<b>Before the update</b>, the app works: all ${n} automatic checks pass.`);
    } else if (state.broken === undefined) {
      state.broken = ev;
      $("#sumBroken").innerHTML = `<span class="bad">${score}</span>`;
      if (!ev.green) milestone("💥", `After updating to <b>${esc(pkg)}</b>, only ${ev.passed} of ${n} checks pass. <b>The app is broken.</b>`);
    } else if (ev.green && !state.fixed) {
      state.fixed = true;
      milestone("🎉", `<b>Fixed!</b> All ${n} of ${n} checks pass again.`);
    } else if (!ev.green && ev.label !== "final") {
      milestone("🔁", `Better, but not done: ${ev.passed} of ${n} checks pass. Bumpr reads the new error and tries again.`);
    }
    const tag = ev.label === "baseline" ? "baseline" : ev.label === "final" ? "final" : `run ${state.runs}`;
    const h = document.createElement("span");
    h.className = `hrun ${ev.green ? "g" : "r"}`;
    h.textContent = `${tag}: ${ev.passed}/${state.total || ev.passed + ev.failed}`;
    $("#history").appendChild(h);
    if (ev.label === "final") return;
    const pill = ev.green ? `<span class="pill g">${ev.passed} passed</span>` :
      `<span class="pill r">${ev.passed} passed · ${ev.failed} failed · ${ev.errors} errors</span>`;
    const why = ev.label === "baseline" ? `Tests are automatic checks that the app gives the right answers. On the old version all of them pass: this is the behaviour Bumpr must keep.`
      : ev.green ? `Every test passes on the new version.`
      : state.runs === 2 ? `Same code, same tests, new library version: this is what the upgrade broke.`
      : `Progress check after Ultra's rewrite.`;
    add(head(ev.green ? "test" : "bad", "Tests", pill, `${ev.duration ? ev.duration.toFixed(2) + "s" : ""}`) +
      `<div class="explain">${why}</div>`);
  },
  triage(ev) {
    const lines = ev.digest.split("\n");
    add(head("bad", "Error log", "what broke") +
      `<pre class="keys">${esc(lines.slice(0, 8).join("\n"))}</pre>` +
      (lines.length > 8 ? `<details><summary>${lines.length - 8} more lines</summary><pre class="keys">${esc(lines.slice(8).join("\n"))}</pre></details>` : ""));
  },
  search(ev) {
    state.searches += 1;
    updateStats();
    const items = ev.results.map((r) => `<li><span class="dom">${esc(domain(r.url))}</span><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title || r.url)}</a></li>`).join("");
    add(head("tavily", "Tavily", `search: “${esc(ev.query)}”`, `${ev.results.length} results`) +
      `<div class="explain">Searching the web for the library's current documentation, instead of trusting what the model remembers.</div>` +
      `<ul class="links">${items}</ul>`);
  },
  docs(ev) {
    state.pages += 1;
    state.passages += ev.passages;
    if (state.pages === 1 && state.pkg) {
      milestone("📖", `Bumpr found the <b>official ${esc(state.pkg.package)} manual</b> online. A small AI (Nemotron Nano) ` +
        `read all of it and picked out the ${ev.passages} part${ev.passages === 1 ? "" : "s"} this app needs.`);
    }
    updateStats();
    const url = ev.url.replace(/^https?:\/\//, "");
    const p = state.pendingDocs;
    state.pendingDocs = null;
    const meta = `${ev.page_chars.toLocaleString()} chars` + (p ? ` · ${p.latency.toFixed(0)}s · ${fmtUsd(p.cost)}` : "");
    add(head("nano", "Nemotron Nano", `read <a href="${esc(ev.url)}" target="_blank" rel="noopener">${esc(url)}</a>`, meta) +
      `<div class="explain">Nano read the whole page and copied out only the parts about code this project uses.</div>` +
      `<div class="grounded">✓ ${ev.passages} passage${ev.passages === 1 ? "" : "s"} handed to Ultra, each checked word-for-word against the page</div>` +
      (ev.excerpt ? `<details><summary>Passages handed to Ultra</summary><pre>${esc(ev.excerpt)}</pre></details>` : ""));
  },
  thinking(ev) {
    let box = state.thinking[ev.purpose];
    if (!box) {
      box = add(head("ultra", "Nemotron Ultra", "is thinking…", "") +
        `<div class="explain">The model's reasoning, streamed live: it compares the errors and the docs with the code.</div>` +
        `<pre class="think"></pre>`);
      state.thinking[ev.purpose] = box;
    }
    const pre = $("pre", box);
    pre.textContent += ev.delta;
    pre.scrollTop = pre.scrollHeight;
  },
  llm(ev) {
    state.calls.push(ev);
    updateStats();
    const box = state.thinking[ev.purpose];
    if (box) {
      $(".ev-head span:nth-child(3)", box).textContent = `thought for ${ev.latency.toFixed(0)}s`;
      $(".meta", box).textContent = `${fmtTok(ev.prompt_tokens)} in · ${fmtTok(ev.completion_tokens)} out · ${fmtUsd(ev.cost)}`;
      const pre = $("pre", box);
      const det = document.createElement("details");
      det.innerHTML = `<summary>Reasoning (${pre.textContent.length.toLocaleString()} chars)</summary>`;
      det.appendChild(pre);
      box.appendChild(det);
    } else if (ULTRA.test(ev.model)) {
      add(head("ultra", "Nemotron Ultra", esc(ev.purpose), `${fmtTok(ev.prompt_tokens)} in · ${fmtTok(ev.completion_tokens)} out · ${ev.latency.toFixed(1)}s · ${fmtUsd(ev.cost)}`));
    } else if (ev.purpose === "read docs") {
      // The docs card is emitted after Nano's call(s) finish; remember the time and cost for it.
      const p = state.pendingDocs || { latency: 0, cost: 0 };
      state.pendingDocs = { latency: p.latency + ev.latency, cost: p.cost + ev.cost };
    }
  },
  plan(ev) {
    const items = ev.text.split("\n").map((l) => l.trim()).filter((l) => /^\d+[.)]/.test(l))
      .map((l) => `<li>${inline(l.replace(/^\d+[.)]\s*/, ""))}</li>`).join("");
    add(head("ultra", "Nemotron Ultra", `plan · attempt ${ev.attempt}`) +
      `<div class="explain">What Ultra decided to change (old → new) and why.</div>` +
      (items ? `<ol class="plan">${items}</ol>` : `<pre>${esc(ev.text)}</pre>`));
  },
  edit(ev) {
    const lines = ev.diff.split("\n").filter((l) => !/^(\+\+\+|---)/.test(l));
    const adds = lines.filter((l) => l.startsWith("+")).length, dels = lines.filter((l) => l.startsWith("-")).length;
    const n = adds + dels;
    if (!state.edited) {
      state.edited = true;
      milestone("🛠️", `A big AI (Nemotron Ultra) <b>rewrote the code</b>. <span class="bad">Red</span> = old, <span class="good">green</span> = new:`,
        miniDiff(ev.diff));
    }
    add(head("ultra", "Rewrote", `<code>${esc(ev.path)}</code>`, `+${adds} −${dels}`) +
      `<div class="explain">Red lines are the old code, green lines the new code.</div>` +
      `<details ${n <= 40 ? "open" : ""}><summary>diff</summary><pre class="diff">${diffHtml(ev.diff)}</pre></details>`);
  },
  log(ev) { add(head("test", "Bumpr", esc(ev.message))); },
  error(ev) { add(head("bad", "Stopped", esc(ev.message))); },
  done(ev) {
    state.done = ev;
    if (ev.usage) { state.calls = state.calls.length ? state.calls : []; updateStats(ev.usage); }
    document.querySelectorAll("#stepper li").forEach((li) => { li.classList.remove("now"); if (ev.success) li.classList.add("done"); });
    setStatus(ev.success ? "green" : "red", ev.success ? `green · ${ev.final?.passed}/${ev.total} tests` : "failed");
    if (ev.diff) {
      const lines = ev.diff.split("\n").filter((l) => !/^(\+\+\+|---)/.test(l));
      $("#sumLines").textContent = `+${lines.filter((l) => l.startsWith("+")).length} −${lines.filter((l) => l.startsWith("-")).length}`;
      showResult(ev);
    }
    if (ev.success && ev.diff) {
      const files = ev.diff.split("\n").filter((l) => l.startsWith("+++ b/") && !l.includes("requirements")).length;
      const changed = ev.diff.split("\n").filter((l) => !/^(\+\+\+|---)/.test(l));
      const body = Math.max(changed.filter((l) => l.startsWith("+")).length, changed.filter((l) => l.startsWith("-")).length);
      const cost = ev.usage ? ev.usage.total_cost : 0;
      const before = state.broken ? state.broken.passed : 0;
      const pages = state.pages;
      $("#resultPlain").innerHTML =
        `<b>Repaired.</b> Right after the ${esc(ev.package)} ${esc(ev.new)} update, only ${before} of ${ev.total} automatic ` +
        `checks passed: the app was broken. Bumpr read ${pages} page${pages === 1 ? "" : "s"} of the official ${esc(ev.package)} ` +
        `documentation, rewrote ${body} lines of code in ${files} file${files === 1 ? "" : "s"}, and now <b>all ${ev.final.passed} ` +
        `checks pass</b>. It took ${fmtTime(ev.elapsed)} and about ${(cost * 100).toFixed(1)} cents of AI usage. ` +
        `Scroll down for Bumpr's report and every changed line.`;
      $("#resultPlain").hidden = false;
    }
    if (ev.success) {
      const g = $("#guide");
      const cost = ev.usage ? ev.usage.total_cost : 0;
      const before = state.broken ? state.broken.passed : 0;
      g.className = "guide final";
      g.innerHTML = `<div class="g-icon">🎉</div><div class="g-body"><div class="g-text">` +
        `<b>Repaired:</b> from <span class="bad">${before} of ${ev.total}</span> checks passing to ` +
        `<span class="good">${ev.final.passed} of ${ev.total}</span>, in ${fmtTime(ev.elapsed)}, for ${(cost * 100).toFixed(1)} cents.</div>` +
        `<div class="g-actions"><button class="primary" id="gDiff">See every code change</button>` +
        `<button class="ghost" id="gAgain">Watch another example</button>` +
        `<button class="ghost" id="gDev">Show technical details</button></div></div>`;
      $("#gDiff").onclick = () => {
        document.querySelector('.tab[data-tab="diff"]').click();
        $("#result").scrollIntoView({ behavior: "smooth", block: "start" });
      };
      const others = samples.map((s) => s.name).filter((nm) => nm !== document.querySelector(".card.active")?.dataset.name);
      $("#gAgain").onclick = () => startReplay(others[0] || samples[0].name);
      $("#gDev").onclick = () => setMode("dev");
    }
    const nl = $("#nowline");
    nl.classList.toggle("done", !!ev.success);
    nl.textContent = ev.success
      ? `Done in ${fmtTime(ev.elapsed)}: all ${ev.final.passed} tests pass on ${ev.package} ${ev.new}. The pull request and the code changes are below.`
      : "The run stopped before every test passed. The log above shows how far it got.";
  },
};

// Short activity lines for the simple view.
const SIMPLE = {
  setup: () => "Getting the app ready…",
  baseline: () => "Checking that the app works before the update…",
  bump: () => "Installing the update…",
  break: () => "Checking the app after the update…",
  research: (ev) => ev.detail.startsWith("Looking up") ? "New error: looking it up in the official manual…"
    : "Searching the web for the official manual…",
  fix: () => "The AI is rewriting the code…",
  verify: () => "Running every check one last time…",
  report: () => "Writing the report…",
};

// One plain-English sentence per step, shown above the timeline.
const PLAIN = {
  setup: () => "Copying the project into a sandbox with its own Python environment.",
  baseline: () => "Running the project's tests on the old library version. They should all pass.",
  bump: (ev) => `Upgrading the library: ${ev.detail}.`,
  break: () => "Running the same tests on the new version to see what the upgrade broke.",
  research: (ev) => ev.detail.startsWith("Looking up")
    ? `A new error appeared, so Bumpr is looking it up in the official docs before the next attempt.`
    : "Finding the library's official migration guide on the web and reading it.",
  fix: (ev) => `${ev.detail.replace("Nemotron Ultra is thinking", "Nemotron Ultra is planning the fix and rewriting the code")}.`,
  verify: () => "Running the whole test suite one last time.",
  report: () => "Nemotron Nano is writing the pull request that explains every change.",
};

// ---------------------------------------------------------------------------------------------
function updateMeter(ev) {
  const [p, f, e] = $("#bar").children;
  if (!ev) { p.style.width = f.style.width = e.style.width = "0"; $("#meternum").textContent = "-"; return; }
  const total = Math.max(state.total, ev.passed + ev.failed + ev.errors, 1);
  const fails = ev.failed;
  const errs = ev.passed + ev.failed === 0 ? total : ev.errors ? Math.max(total - ev.passed - ev.failed, ev.errors) : 0;
  p.style.width = `${(ev.passed / total) * 100}%`;
  f.style.width = `${(fails / total) * 100}%`;
  e.style.width = `${Math.min(errs, total - ev.passed - fails) / total * 100}%`;
  $("#meternum").innerHTML = `<span style="color:${ev.green ? "var(--pass)" : "var(--fail)"}">${ev.passed}</span> / ${total} passing`;
}

function updateStats(summary) {
  const agg = { ultra: { calls: 0, tok: 0, cost: 0 }, nano: { calls: 0, tok: 0, cost: 0 } };
  let allUltra = 0;
  for (const c of state.calls) {
    const k = ULTRA.test(c.model) ? "ultra" : "nano";
    agg[k].calls += 1;
    agg[k].tok += c.prompt_tokens + c.completion_tokens;
    agg[k].cost += c.cost;
    allUltra += (c.prompt_tokens - (c.cached_tokens || 0)) * 1e-6 + (c.cached_tokens || 0) * 1.2e-7 + c.completion_tokens * 3e-6;
  }
  const maxTok = Math.max(agg.ultra.tok, agg.nano.tok, 1);
  for (const k of ["ultra", "nano"]) {
    const el = $(`.model[data-model=${k}]`);
    $(".mbar span", el).style.width = `${(agg[k].tok / maxTok) * 100}%`;
    $(".calls", el).textContent = `${agg[k].calls} call${agg[k].calls === 1 ? "" : "s"}`;
    $(".toks", el).textContent = `${fmtTok(agg[k].tok)} tok`;
    $(".cost", el).textContent = fmtUsd(agg[k].cost);
  }
  const total = summary ? summary.total_cost : agg.ultra.cost + agg.nano.cost;
  const counterfactual = summary ? summary.all_planner_cost : allUltra;
  $("#tcost").textContent = fmtUsd(total);
  $("#sumCost").textContent = `$${(total || 0).toFixed(3)}`;
  $("#tultra").textContent = fmtUsd(counterfactual);
  const tokAll = agg.ultra.tok + agg.nano.tok;
  $("#saving").textContent = counterfactual > total && tokAll
    ? `Nano processed ${Math.round((agg.nano.tok / tokAll) * 100)}% of all tokens; routing saved ${Math.round((1 - total / counterfactual) * 100)}% vs. Ultra-only.`
    : "";
  $("#nsearch").textContent = state.searches;
  $("#npages").textContent = state.pages;
  $("#npass").textContent = state.passages;
}

function showResult(ev) {
  $("#result").hidden = false;
  $("#tab-pr").innerHTML = `<div class="md">${markdown(ev.report || ev.summary || "")}</div>`;
  $("#tab-diff").innerHTML = `<pre class="diff">${diffHtml(ev.diff)}</pre>`;
  $("#tab-sources").innerHTML = `<ul class="links">${(ev.sources || []).map((u) => `<li><span class="dom">${esc(domain(u))}</span><a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a></li>`).join("")}</ul>`;
  const blob = new Blob([ev.diff], { type: "text/x-diff" });
  $("#download").href = URL.createObjectURL(blob);
  $("#download").download = `bumpr-${ev.package}-${ev.new}.patch`;
  if (!session?.skip && mode !== "simple") $("#result").scrollIntoView({ behavior: "smooth", block: "start" });
  if (mode === "simple") window.scrollTo({ top: 0, behavior: "smooth" });
}

document.addEventListener("click", (e) => {
  const t = e.target.closest(".tab");
  if (!t) return;
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("on", x === t));
  for (const k of ["pr", "diff", "sources"]) $(`#tab-${k}`).hidden = k !== t.dataset.tab;
});

// ---------------------------------------------------------------------------------------------
function diffHtml(diff) {
  return diff.split("\n").map((l) => {
    const e = esc(l);
    if (/^(\+\+\+|---)/.test(l)) return `<span class="file">${e}</span>`;
    if (l.startsWith("@@")) return `<span class="hunk">${e}</span>`;
    if (l.startsWith("+")) return `<span class="add">${e}</span>`;
    if (l.startsWith("-")) return `<span class="del">${e}</span>`;
    return e;
  }).join("\n").replace(/<\/span>\n/g, "</span>");
}

function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\*)/g, "$1<i>$2</i>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|\s)(https?:\/\/[^\s<]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
}

function markdown(md) {
  const out = [];
  let list = false;
  for (const raw of md.split("\n")) {
    const line = raw.trimEnd();
    const li = line.match(/^\s*[-*]\s+(.*)/) || line.match(/^\s*\d+[.)]\s+(.*)/);
    if (li) { if (!list) { out.push("<ul>"); list = true; } out.push(`<li>${inline(li[1])}</li>`); continue; }
    if (list) { out.push("</ul>"); list = false; }
    const h = line.match(/^#{1,4}\s+(.*)/);
    if (h) out.push(`<h2>${inline(h[1])}</h2>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push("</ul>");
  return out.join("");
}

boot();
