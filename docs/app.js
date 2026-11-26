/* NEPSE Quant Lab dashboard — reads data/*.json produced by the bot. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const rs = (n, d = 0) => n == null || isNaN(n) ? "—" : "Rs " + Number(n).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d });
const num = (n, d = 0) => n == null || isNaN(n) ? "—" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d });
const pct = (n, d = 2) => n == null || isNaN(n) ? "—" : (n > 0 ? "+" : "") + Number(n).toFixed(d) + "%";
const cls = n => n > 0 ? "up" : n < 0 ? "down" : "";
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const KIND = {
  data: ["DATA", "📡"], exec: ["BROKER", "🏦"], think: ["THINK", "🤔"], learn: ["LEARN", "🧠"], risk: ["RISK", "🛡️"],
  plan: ["PLAN", "📝"], hold: ["HOLD", "✋"], lock: ["T+2", "🔒"], buy: ["BUY", "🟢"], sell: ["SELL", "🔴"],
  warn: ["ALERT", "⚠️"], summary: ["RESULT", "📊"],
};
const FILTERS = { all: "All", trades: "Trades", decisions: "Decisions", brain: "Brain", alerts: "Alerts" };
const FGROUP = { trades: ["buy", "sell"], decisions: ["plan", "hold", "lock", "exec"], brain: ["think", "learn", "risk"], alerts: ["warn", "lock"] };
/* Section plates: real numbered combinations from Sanzo Wada's "A Dictionary of Color Combinations" (1933). */
const PLATES = {
  holdings: [126, [["Ivory Buff", "#ebd3a2"], ["Yellow Ocher", "#e2b540"], ["Deep Lyons Blue", "#1c4286"]]],
  brain: [139, [["Salvia Blue", "#97acc8"], ["Deep Indigo", "#051230"], ["Neutral Gray", "#b6bfc1"]]],
  nepal: [325, [["Eugenia Red", "#da525d"], ["Naples Yellow", "#fbe6a0"], ["Yellow Ocher", "#e2b540"], ["Deep Slate Green", "#112f2c"]]],
  log: [145, [["Brown", "#7c4226"], ["Citron Yellow", "#b2b73e"], ["Dull Violet Black", "#1e0e3f"]]],
  trades: [232, [["Carmine", "#cc1236"], ["Pinkish Cinnamon", "#eeb480"], ["Deep Indigo", "#051230"]]],
  costs: [211, [["Apricot Orange", "#f68c50"], ["Olive Yellow", "#a6a159"], ["Deep Indigo", "#051230"]]],
  how: [155, [["Jasper Red", "#eb5324"], ["Benzol Green", "#00978d"], ["Deep Indigo", "#051230"]]],
  quick: [216, [["Jasper Red", "#eb5324"], ["Green", "#489b6e"], ["Black", "#111314"]]],
  charts: [182, [["Raw Sienna", "#bb7125"], ["Vandyke Brown", "#4b3317"], ["Deep Indigo", "#051230"]]],
  robust: [247, [["Raw Sienna", "#bb7125"], ["Apricot Yellow", "#ffdd00"], ["Benzol Green", "#00978d"], ["Deep Lyons Blue", "#1c4286"]]],
};
function plates() {
  $$(".plate[data-plate]").forEach(el => {
    const [n, cs] = PLATES[el.dataset.plate] || [];
    if (!n) return;
    el.innerHTML = `<span>Plate № ${n}</span><span class="sw">${cs.map(([nm, h]) => `<i style="background:${h}" title="${nm} ${h}"></i>`).join("")}</span><span class="nm">${cs.map(c => c[0]).join(" · ")}</span>`;
  });
}

let RES = null, ROB = null, QT = null, TC = null, th = 15, qh = 5, ALL = {}, pf = "500k";
let META, DATA = {}, mode = "live", charts = {}, logFilter = "all", tradeFilter = "all", range = 0;

async function load() {
  const get = f => fetch(`data/${f}?t=${Date.now()}`).then(r => r.json());
  META = await get("meta.json");
  const pfs = META.portfolios || [{ id: "500k", name: "Rs 5 lakh", files: ["live.json", "backtest.json"] }];
  await Promise.all(pfs.map(async p => {
    const [live, backtest] = await Promise.all(p.files.map(get));
    ALL[p.id] = { live, backtest, meta: p };
  }));
  try { const q = new URLSearchParams(location.search).get("pf") || localStorage.getItem("pf"); if (q && ALL[q]) pf = q; } catch (e) { }
  if (!ALL[pf]) pf = pfs[0].id;
  DATA = ALL[pf];
  $("#pfSeg").innerHTML = pfs.map(p => `<button data-pf="${p.id}" role="tab">${esc(p.name)}</button>`).join("");
  $("#updated").textContent = `Last bot run: ${META.updated} · latest market data: ${META.latest_market_date}`;
  $("#runlog").textContent = (META.run_log || []).slice(1).join(" · ");
  try { RES = await get("research.json"); } catch (e) { RES = null; }
  try { ROB = await get("robustness.json"); } catch (e) { ROB = null; }
  try { QT = await get("shortterm.json"); } catch (e) { QT = null; }
  try { TC = await get("technical.json"); } catch (e) { TC = null; }
  $("#modelTag").textContent = META.model ? "· MODEL " + META.model.toUpperCase() : "";
  render();
  if (RES) research();
  if (ROB) robustness();
  if (QT) quick();
  if (TC) chartPicks();
}

function render() {
  DATA = ALL[pf];
  const S = DATA[mode];
  const c = DATA.meta.config || {};
  $$("#pfSeg button").forEach(b => b.classList.toggle("on", b.dataset.pf === pf));
  $("#pfNote").textContent = c.max_positions ? `Up to ${c.max_positions} stocks of about ${rs(S.start_capital * Math.min(c.max_weight, 0.98 / c.max_positions), 0)} each` : "";
  $("#growthTitle").textContent = `Growth of ${rs(S.start_capital)}`;
  $$(".mode button").forEach(b => b.classList.toggle("on", b.dataset.mode === mode));
  const B = $("#modeBanner");
  if (mode === "live") {
    B.className = "banner live";
    B.innerHTML = `<b>🟢 Live paper trading</b> since <b>${S.inception}</b>, with real NEPSE prices and virtual money (${rs(S.start_capital)}). The bot runs by itself after every trading day; this page updates automatically. Day <b>${S.equity.length}</b> of the experiment.`;
  } else {
    B.className = "banner bt";
    B.innerHTML = `<b>⟲ Historical simulation.</b> What the <i>exact same bot</i> would have done since <b>${S.inception}</b>, deciding each day with only the data available at that time. Covers the COVID shutdown, the 2020–21 mania, the 2022–23 crash and the September 2025 shutdown. The model was designed on data up to <b>${META.split}</b>; everything after is an unseen <b>hold-out test</b>. This is one of four tested start dates; see <a href="#robust">Robustness</a> for the range and the caveats.`;
  }
  kpis(S); story(S); record(S); equityChart(S); holdings(S); orders(S); brain(S); weightChart(S); terminal(S); trades(S); fees(S);
}

function countUp(el, to, fmt) {
  const from = 0, t0 = performance.now(), dur = 900;
  const step = t => { const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3); el.textContent = fmt(from + (to - from) * e); if (k < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}

function kpis(S) {
  const M = S.metrics || {}, eq = M.equity ?? S.start_capital, last = S.equity.at(-1) || {};
  if (M.xirr_pct != null) {
    $("#kXirr").innerHTML = `<span class="${cls(M.xirr_pct)}">${pct(M.xirr_pct, 1)}</span><small class="muted"> /yr</small>`;
    $("#kXirrSub").textContent = M.nepse_xirr_pct != null ? `NEPSE same dates: ${pct(M.nepse_xirr_pct, 1)}/yr` : "";
  } else {
    $("#kXirr").textContent = "—";
    const left = (M.xirr_min_days || 60) - (M.days || 0);
    $("#kXirrSub").textContent = `Shown after ${M.xirr_min_days || 60} days (${Math.max(left, 0)} to go)`;
  }
  countUp($("#kEquity"), eq, v => rs(v));
  const prev = S.equity.at(-2);
  const dch = prev ? (eq / prev.equity - 1) * 100 : 0;
  $("#kEquitySub").innerHTML = `Today: <span class="${cls(dch)}">${pct(dch)}</span> · started with ${rs(S.start_capital)}`;
  const r = M.return_pct ?? 0;
  $("#kRet").innerHTML = `<span class="${cls(r)}">${pct(r)}</span>`;
  $("#kRetSub").textContent = `${rs(eq - S.start_capital)} ${eq >= S.start_capital ? "profit" : "loss"}`;
  const a = M.nepse_return_pct == null ? null : r - M.nepse_return_pct;
  $("#kAlpha").innerHTML = `<span class="${cls(a)}">${a == null ? "—" : pct(a)}</span>`;
  $("#kAlphaSub").textContent = M.nepse_return_pct == null ? "" : `NEPSE did ${pct(M.nepse_return_pct)}`;
  const inv = last.invested || 0, tot = last.equity || S.start_capital;
  $("#kCash").textContent = `${Math.round((S.cash / tot) * 100)}% / ${Math.round((inv / tot) * 100)}%`;
  setTimeout(() => $("#kCashBar").style.width = `${(inv / tot) * 100}%`, 50);
  $("#kDD").innerHTML = `<span class="${M.max_drawdown_pct < 0 ? "down" : ""}">${M.max_drawdown_pct ? pct(M.max_drawdown_pct) : "0%"}</span>`;
  $("#kDDSub").textContent = M.sharpe != null ? `Sharpe ratio ${M.sharpe}` : "Needs more days";
  $("#kWin").textContent = M.win_rate_pct != null ? `${M.win_rate_pct}%` : "—";
  $("#kWinSub").textContent = M.trades_closed ? `${M.trades_closed} completed trades` : "No completed trades yet";
}

function story(S) {
  const day = S.days.at(-1);
  if (!day) return;
  $("#storyDate").textContent = fmtDate(day.date);
  const pick = day.log.filter(l => ["buy", "sell", "plan", "warn", "lock", "summary", "learn"].includes(l.k));
  const lines = (pick.length ? pick : day.log).slice(0, 9);
  const more = day.log.length - lines.length;
  $("#story").innerHTML = lines.map(l => `<div class="line"><span class="ic">${KIND[l.k]?.[1] || "•"}</span><span>${esc(l.m)}</span></div>`).join("")
    + (more > 0 ? `<a class="muted" href="#log">+ ${more} more steps in the activity log →</a>` : "");
  const b = S.brain || {}, hv = Object.values(b.health || {});
  const okN = hv.filter(v => v === "ok").length;
  $("#mood").textContent = hv.length ? `${okN} of ${hv.length}` : "—";
  $("#gross").textContent = b.next_review != null ? (b.next_review ? `in ${b.next_review} day${b.next_review === 1 ? "" : "s"}` : "today") : "—";
  $("#moodNeedle").style.left = hv.length ? `${8 + 84 * okN / hv.length}%` : "50%";
}

function fmtDate(d) {
  return new Date(d + "T00:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

function baseChartOpts(extra = {}) {
  const ink = css("--ink2"), line = css("--line");
  return {
    responsive: true, maintainAspectRatio: false, animation: { duration: 700 },
    interaction: { mode: "index", intersect: false },
    plugins: { legend: { labels: { color: ink, boxWidth: 12, usePointStyle: true } }, tooltip: { backgroundColor: css("--ink"), titleColor: css("--bg"), bodyColor: css("--bg"), padding: 10 } },
    scales: {
      x: { ticks: { color: css("--muted"), maxTicksLimit: 7, maxRotation: 0 }, grid: { display: false } },
      y: { ticks: { color: css("--muted") }, grid: { color: line } },
    }, ...extra,
  };
}

function equityChart(S) {
  let eq = S.equity;
  if (range && eq.length) { const cut = new Date(new Date(eq.at(-1).date) - range * 864e5).toISOString().slice(0, 10); eq = eq.filter(e => e.date >= cut); }
  const ewBase = eq[0]?.ew;
  const base = eq[0]?.nepse, startEq = eq[0]?.equity;
  const labels = eq.map(e => e.date);
  const bot = eq.map(e => e.equity);
  const nep = eq.map(e => base && e.nepse ? (e.nepse / base) * startEq : null);
  const ew = eq.map(e => ewBase && e.ew ? (e.ew / ewBase) * startEq : null);
  charts.eq?.destroy();
  const ctx = $("#eqChart").getContext("2d");
  const g = ctx.createLinearGradient(0, 0, 0, 300);
  g.addColorStop(0, css("--c1") + "4d"); g.addColorStop(1, css("--c1") + "00");
  charts.eq = new Chart(ctx, {
    type: "line",
    data: {
      labels, datasets: [
        { label: "Bot portfolio", data: bot, borderColor: css("--c1"), backgroundColor: g, fill: true, borderWidth: 2.4, pointRadius: eq.length < 3 ? 5 : 0, tension: .25 },
        { label: "NEPSE index (same Rs start)", data: nep, borderColor: css("--c2"), borderDash: [5, 4], borderWidth: 1.6, pointRadius: eq.length < 3 ? 4 : 0, tension: .25 },
        { label: "Average liquid stock (no costs)", data: ew, borderColor: css("--c3"), borderDash: [2, 3], borderWidth: 1.4, pointRadius: 0, tension: .25 },
      ],
    },
    options: baseChartOpts({ scales: { x: { ticks: { color: css("--muted"), maxTicksLimit: 7, maxRotation: 0 }, grid: { display: false } }, y: { ticks: { color: css("--muted"), callback: v => "Rs " + (v / 100000).toFixed(2) + "L" }, grid: { color: css("--line") } } },
      plugins: { legend: { labels: { color: css("--ink2"), boxWidth: 12, usePointStyle: true } }, tooltip: { backgroundColor: css("--ink"), titleColor: css("--bg"), bodyColor: css("--bg"), padding: 10, callbacks: { label: c => `${c.dataset.label}: ${rs(c.parsed.y)}` } } } }),
  });
}

function spark(h) {
  if (!h || h.length < 2) return "";
  const W = 240, H = 40, mn = Math.min(...h), mx = Math.max(...h), sx = W / (h.length - 1);
  const y = v => H - 3 - ((v - mn) / ((mx - mn) || 1)) * (H - 6);
  const d = h.map((v, i) => `${i ? "L" : "M"}${(i * sx).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const c = h.at(-1) >= h[0] ? css("--green") : css("--red");
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:40px;margin-top:10px"><path d="${d}" fill="none" stroke="${c}" stroke-width="2"/></svg>`;
}

function holdings(S) {
  const el = $("#holds");
  if (!S.positions.length) {
    el.innerHTML = `<div class="empty">No shares owned yet. ${S.pending.length ? `The bot has <b>${S.pending.length} buy orders</b> waiting for the next trading session ↓` : "The bot is waiting for a strong enough opportunity."}</div>`;
    return;
  }
  const sig = META.signals;
  el.innerHTML = S.positions.sort((a, b) => b.value - a.value).map(p => {
    const lock = p.settled ? `<span class="badge ok">✅ Settled, can sell</span>` : `<span class="badge lock">🔒 Settling (day ${p.days_held}/2)</span>`;
    const z = p.signals || {};
    const bars = Object.keys(sig).map(k => { const v = z[k] || 0; return `<i title="${sig[k].name}: ${v}" class="${v < 0 ? "n" : ""}" style="height:${Math.min(100, Math.abs(v) * 33)}%"></i>`; }).join("");
    return `<div class="hold" style="--c:${p.pnl >= 0 ? css("--green") : css("--red")}">
      <div class="h1"><span class="sym">${p.sym}</span><span class="pnl ${cls(p.pnl)}">${pct(p.pnl_pct)}</span></div>
      <div class="sec">${esc(p.sector)} · held ${p.days_held} trading day${p.days_held === 1 ? "" : "s"}</div>
      <div class="row"><span>Shares</span><b>${num(p.qty)}</b></div>
      <div class="row"><span>Bought at → now</span><b>Rs ${num(p.entry_price, 1)} → ${num(p.last, 1)}</b></div>
      <div class="row"><span>Value</span><b>${rs(p.value)}</b></div>
      <div class="row"><span>Profit / loss</span><b class="${cls(p.pnl)}">${rs(p.pnl)}</b></div>
      ${spark(p.hist)}
      <div class="row" style="margin-top:8px">${lock}${p.review_in != null ? `<span class="muted" title="Each holding is re-checked every 60 trading days">review in ${p.review_in}d</span>` : ""}</div>
      <div class="why"><b>Why bought:</b> ${esc(p.reason)}<div class="spark" title="Signal strengths at purchase">${bars}</div></div>
    </div>`;
  }).join("");
}

function orders(S) {
  const el = $("#orders");
  if (!S.pending.length) { el.innerHTML = `<div class="empty">No orders queued. The bot sees no reason to trade at the next session.</div>`; return; }
  el.innerHTML = S.pending.map(o => `<div class="order"><span class="side ${o.side}">${o.side}</span><b>${o.sym}</b><span>${num(o.qty)} shares</span><span class="r">${esc(o.reason)}</span></div>`).join("");
}

function brain(S) {
  const b = S.brain; if (!b) return;
  const sig = META.signals;
  const hl = b.health || {};
  const mx = Math.max(...Object.values(b.weights).map(Math.abs), 0.01);
  $("#weights").innerHTML = Object.entries(b.weights).sort((x, y) => Math.abs(y[1]) - Math.abs(x[1])).map(([k, w]) => {
    if (!sig[k]) return "";
    const wd = Math.abs(w) / mx * 50, pos = w >= 0;
    const badge = hl[k] ? `<span class="hb ${hl[k]}" title="Over the last year this signal has ${hl[k] === "ok" ? "pointed the right way" : "pointed the wrong way"}">${hl[k] === "ok" ? "WORKING" : "STRUGGLING"}</span>` : "";
    return `<div class="wrow"><div class="nm">${sig[k].name}${badge}<small>${sig[k].desc}</small></div>
      <div class="wbar" title="Weight ${(w * 100).toFixed(0)}%"><span style="background:${pos ? css("--green") : css("--red")};${pos ? `left:50%;width:${wd}%` : `right:50%;width:${wd}%`}"></span></div>
      <div class="pc ${pos ? "up" : "down"}">${(w * 100).toFixed(0)}%</div></div>`;
  }).join("") + `<p class="hint">Green bar = the bot prefers stocks scoring <b>high</b> on this. Red bar = it prefers stocks scoring <b>low</b> (the signal marks stocks to avoid).</p>`;
  $("#uniN").textContent = b.universe;
  const keys = Object.keys(sig);
  const color = v => { const a = Math.min(1, Math.abs(v) / 2.5); const c = v >= 0 ? css("--heat-pos") : css("--heat-neg"); return `background:color-mix(in srgb, ${c} ${Math.round(a * 85)}%, ${css("--heat-mid")});color:${a > .5 ? "#f5ecc2" : css("--ink")}`; };
  $("#heat").innerHTML = `<thead><tr><th>#</th><th>Stock</th><th>Sector</th><th>Price</th>${keys.map(k => `<th title="${sig[k].desc}">${sig[k].name}</th>`).join("")}<th>Score</th></tr></thead><tbody>` +
    b.top.map((t, i) => `<tr><td>${i + 1}</td><td><b>${t.sym}</b>${t.veto ? `<span class="veto" title="Never-buy rule: ${esc(t.veto)}">🚫</span>` : ""}</td><td class="muted">${esc(t.sector)}</td><td>${num(t.close, 1)}</td>${keys.map(k => `<td class="z" style="${color(t.z[k])}">${t.z[k].toFixed(1)}</td>`).join("")}<td class="s up">${t.alpha.toFixed(2)}</td></tr>`).join("") + "</tbody>";
}

function weightChart(S) {
  let wh = S.health_history || [], note = "";
  if (wh.length < 30) { wh = DATA.backtest.health_history || []; note = "From the historical simulation"; }
  $("#multNote").textContent = note;
  const sig = META.signals, keys = Object.keys(sig);
  Object.values(charts.mult || {}).forEach(c => c.destroy());
  charts.mult = {};
  const val = (w, k) => (w[k] ?? 0) * 100 * Math.sign(sig[k].prior || 1);   // >0 = working as expected
  const lim = Math.max(8, ...wh.flatMap(w => keys.map(k => Math.abs(val(w, k))))) * 1.05;
  $("#mults").innerHTML = keys.map(k => {
    const last = wh.length ? val(wh.at(-1), k) : 0;
    return `<div class="mult"><div class="mh"><b title="${esc(sig[k].desc)}">${sig[k].name}</b><span class="${cls(last)}">${last >= 0 ? "working" : "struggling"}</span></div><div class="mc"><canvas id="m_${k}" aria-label="${sig[k].name} health over time"></canvas></div></div>`;
  }).join("");
  const tip = { backgroundColor: css("--ink"), titleColor: css("--bg"), bodyColor: css("--bg"), padding: 8, displayColors: false };
  keys.forEach(k => {
    charts.mult[k] = new Chart($("#m_" + k), {
      type: "line",
      data: { labels: wh.map(w => w.date), datasets: [
        { data: wh.map(w => val(w, k)), borderColor: css("--ink2"), borderWidth: 1.5, pointRadius: 0, pointHoverRadius: 4, tension: .3,
          fill: { target: { value: 0 }, above: css("--green") + "40", below: css("--red") + "40" } },
        { data: wh.map(() => 0), borderColor: css("--muted") + "66", borderWidth: 1, pointRadius: 0, pointHoverRadius: 0 }] },
      options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: false }, tooltip: { ...tip, filter: c => c.datasetIndex < 1,
          callbacks: { label: c => `Predictive strength: ${c.parsed.y.toFixed(1)} (${c.parsed.y >= 0 ? "working" : "against the model"})` } } },
        scales: { x: { display: false }, y: { display: false, min: -lim, max: lim } } },
    });
  });
}

let typing;
function terminal(S, keepDay) {
  const sel = $("#daySel");
  const days = [...S.days].reverse();
  if (!keepDay) {
    sel.innerHTML = days.map((d, i) => `<option value="${d.date}">${i === 0 ? "▶ " : ""}${fmtDate(d.date)}</option>`).join("") + `<option value="__all">All shown days</option>`;
  }
  $("#filters").innerHTML = Object.entries(FILTERS).map(([k, v]) => `<button data-f="${k}" class="${k === logFilter ? "on" : ""}">${v}</button>`).join("");
  const pickDays = sel.value === "__all" ? days : days.filter(d => d.date === sel.value);
  const ok = l => logFilter === "all" || FGROUP[logFilter].includes(l.k);
  const out = [];
  pickDays.forEach(d => {
    out.push(`<div class="day">━━ ${fmtDate(d.date)} ━━</div>`);
    d.log.forEach((l, i) => { if (ok(l)) out.push(`<div class="ln ${l.k}"><span class="ts">#${String(i + 1).padStart(2, "0")}</span><span class="tag">${KIND[l.k]?.[0] || l.k}</span><span>${esc(l.m)}</span></div>`); });
  });
  const T = $("#term");
  clearInterval(typing);
  T.innerHTML = "";
  const first = !keepDay && sel.selectedIndex === 0;
  if (!first) { T.innerHTML = out.join("") || `<div class="muted">Nothing for this filter.</div>`; return; }
  let n = 0; // animate today's log line by line
  typing = setInterval(() => {
    if (n >= out.length) { clearInterval(typing); T.insertAdjacentHTML("beforeend", `<div class="ln"><span class="tag">IDLE</span><span>waiting for the next market close <span class="cursor"></span></span></div>`); return; }
    T.insertAdjacentHTML("beforeend", out[n++]); T.scrollTop = T.scrollHeight;
  }, 110);
}

function trades(S) {
  const list = [...S.trades].reverse().filter(t => tradeFilter === "all" || (tradeFilter === "REJECTED" ? t.status === "REJECTED" : t.side === tradeFilter && t.status === "FILLED"));
  $("#tradeCount").textContent = `${list.length} record${list.length === 1 ? "" : "s"}`;
  if (!list.length) { $("#tradeTable").innerHTML = `<tr><td class="muted" style="padding:20px">No trades yet. The first orders execute at the next trading session.</td></tr>`; return; }
  $("#tradeTable").innerHTML = `<thead><tr><th>Date</th><th>Side</th><th>Stock</th><th>Qty</th><th>Price</th><th>Amount</th><th>Fees</th><th>Tax</th><th>Net P/L</th><th>Status</th><th>Why</th></tr></thead><tbody>` +
    list.slice(0, 300).map(t => t.status === "REJECTED"
      ? `<tr><td>${t.date}</td><td><span class="side ${t.side}">${t.side}</span></td><td><b>${t.sym}</b>${t.veto ? `<span class="veto" title="Never-buy rule: ${esc(t.veto)}">🚫</span>` : ""}</td><td colspan="6" class="muted">—</td><td class="st rej">✕ ${esc(t.why)}</td><td class="rs">${esc(t.reason)}</td></tr>`
      : `<tr><td>${t.date}</td><td><span class="side ${t.side}">${t.side}</span></td><td><b>${t.sym}</b>${t.veto ? `<span class="veto" title="Never-buy rule: ${esc(t.veto)}">🚫</span>` : ""}</td><td>${num(t.qty)}</td><td>${num(t.price, 2)}</td><td>${rs(t.amount)}</td><td>${rs(t.fees)}</td><td>${t.cgt ? rs(t.cgt) : "—"}</td><td class="${cls(t.pnl)}"><b>${t.pnl == null ? "—" : rs(t.pnl)}</b></td><td class="st up">✓ filled</td><td class="rs">${esc(t.reason)}</td></tr>`).join("") + "</tbody>";
}

function fees(S) {
  const f = { ...S.fees, small: (S.fees.sebon || 0) + (S.fees.dp || 0) }, items = [
    ["commission", "Broker commission", "0.24–0.36% of every buy and sell, set by SEBON", css("--c2")],
    ["cgt", "Capital gains tax", "7.5% of profit on shares held under a year (5% if longer)", css("--c1")],
    ["slippage", "Slippage", "Paying worse than the day's average price: grows with order size and how thinly the stock trades", css("--c3")],
    ["small", "Small fees", "SEBON 0.015% per trade + DP charge Rs 25 per stock sold", css("--c4")],
  ];
  const tot = items.reduce((a, [k]) => a + (f[k] || 0), 0);
  charts.fee?.destroy();
  charts.fee = new Chart($("#feeChart"), {
    type: "doughnut",
    data: { labels: items.map(i => i[1]), datasets: [{ data: items.map(([k]) => f[k] || 0), backgroundColor: items.map(i => i[3]), borderColor: css("--card"), borderWidth: 2, hoverOffset: 6 }] },
    options: { responsive: true, maintainAspectRatio: false, cutout: "66%", plugins: { legend: { position: "bottom", labels: { color: css("--ink2"), boxWidth: 10, usePointStyle: true } }, tooltip: { backgroundColor: css("--ink"), titleColor: css("--bg"), bodyColor: css("--bg"), callbacks: { label: c => `${c.label}: ${rs(c.parsed)}` } } } },
  });
  $("#feeList").innerHTML = items.map(([k, n, d, c]) => `<div class="fee"><div><span class="chip" style="background:${c}"></span>${n}<small>${d}</small></div><b>${rs(f[k] || 0)}</b></div>`).join("") +
    `<div class="fee"><div><b>Total cost of trading</b><small>${tot ? ((tot / S.start_capital) * 100).toFixed(2) + "% of starting capital" : "Nothing spent yet"}</small></div><b>${rs(tot)}</b></div>`;
}

function record(S) {
  const M = S.metrics || {};
  const seg = (t, x, note) => x ? `<div class="sp"><small>${t}</small><div class="big ${cls(x.return_pct)}">${pct(x.return_pct, 1)}</div>
    <div class="vs">NEPSE ${pct(x.nepse_return_pct, 1)} · avg stock ${pct(x.ew_return_pct, 1)}</div>
    ${x.xirr_pct != null ? `<div class="vs">XIRR <b class="${cls(x.xirr_pct)}">${pct(x.xirr_pct, 1)}/yr</b> (NEPSE ${pct(x.nepse_xirr_pct, 1)}/yr)</div>` : ""}
    <div class="vs">Worst drop ${pct(x.max_drawdown_pct, 1)} (NEPSE ${pct(x.nepse_drawdown_pct, 1)})</div>${note ? `<div class="vs muted">${note}</div>` : ""}</div>` : "";
  $("#split").innerHTML = seg(`Whole period · ${M.from || "—"} → ${M.to || "—"}`, M)
    + seg(`Design period (model built on this) · until ${META.split}`, M.design, "In-sample: flatters the model")
    + seg(`Hold-out test (never seen in design) · after ${META.split}`, M.holdout, "The honest number");
  $("#recordNote").textContent = mode === "live" ? "Live experiment" : "Same rules, fees and taxes as the live bot";
  const ys = M.years || [];
  const mxv = Math.max(1, ...ys.flatMap(y => [Math.abs(y.bot), Math.abs(y.nepse ?? 0)]));
  const bar = (v, c) => v == null ? "" : `<span class="bar2" style="width:${Math.abs(v) / mxv * 60}px;background:${c}"></span>`;
  $("#yearTable").innerHTML = ys.length ? `<thead><tr><th>Year</th><th>Bot</th><th>NEPSE</th><th>Avg stock*</th><th>Bot vs NEPSE</th><th>Worst drop</th></tr></thead><tbody>` +
    ys.map(y => { const d = y.nepse == null ? null : y.bot - y.nepse; return `<tr><td><b>${y.year}</b></td><td class="${cls(y.bot)}"><b>${pct(y.bot, 1)}</b>${bar(y.bot, y.bot >= 0 ? css("--green") : css("--red"))}</td><td class="${cls(y.nepse)}">${pct(y.nepse, 1)}</td><td class="muted">${pct(y.ew, 1)}</td><td class="${cls(d)}">${d == null ? "—" : (d >= 0 ? "✓ " : "✗ ") + pct(d, 1)}</td><td class="down">${pct(y.dd, 1)}</td></tr>`; }).join("") +
    `</tbody>` : "";
  $("#models").innerHTML = (META.model_history || []).map(v => `<div class="model"><b>Model ${v.version}</b> (${v.date})<br>${esc(v.summary)}<br>Same 7-year test → design <b class="${cls(v.design_pct)}">${pct(v.design_pct, 1)}</b>, hold-out <b class="${cls(v.holdout_pct)}">${pct(v.holdout_pct, 1)}</b></div>`).join("")
    + `<div class="model muted">* "Avg stock" = equal amounts in every liquid stock, rebalanced daily with zero costs. No real investor can get this exactly; it shows how hard a pure stock picker's job is.</div>`;
}

const FN = { pressure: "Closing pressure", calm60: "Low volatility (60d)", calm: "Low volatility (20d)", maxret: "No lottery spikes", upcirc: "Circuit chasing", high52: "Near 52-week high", reversal: "Short-term bounce", rev20: "1-month reversal", momentum: "3-month momentum", mom120: "6-month momentum", mom250: "12-month momentum", meanrev: "Rubber band", volume: "Volume confirmation", quality: "Trend quality", sector: "Sector rotation (1m)", sector60: "Sector rotation (3m)", turntrend: "Turnover trend (short)", size: "Crowded trading (turnover)", surge: "Turnover surge" };

function research() {
  const R = RES, st = R.stocks.h20, s5 = R.stocks.h5, mk = R.market, cb = R.circuits;
  const y0 = R.period[0].slice(0, 4), y1 = R.period[1].slice(0, 4);
  $("#rsYears").textContent = `${y1 - y0 + 1} years (${y0}–${y1})`;
  $("#rsCut").textContent = R.design_end;
  const yrsNeg = Object.values(s5.pressure.ic_by_year).filter(v => v < 0).length, yrsAll = Object.keys(s5.pressure.ic_by_year).length;
  const calmY = Object.entries(st.calm60.ic_by_year), calmPos = calmY.filter(([, v]) => v > 0).length;
  const t50 = mk.trend.sma50;
  const neg = o => Object.values(o).filter(v => v < 0).length, cnt = o => Object.keys(o).length;
  const sz = st.size, sens = Object.fromEntries(((ROB || {}).sensitivity || []).map(r => [r.label, r]));
  const base = sens["Chosen settings"], stp = sens["With 15% stop-loss + 22% trailing"], bear = sens["With bear-market cash filter"], lrn = sens["Weights half re-learned each day"];
  const F = [
    ["🤫", "The crowd's favourites lag; quiet stocks lead", `The most heavily traded stocks did <b>worse</b> than quiet ones in ${neg(sz.ic_by_year)} of ${cnt(sz.ic_by_year)} years. It is the strongest pattern in the data, and one a small account can use because big funds cannot buy thinly traded stocks.`, "use", "Used: biggest weight"],
    ["🎯", "Late-day pushes unwind", `Stocks that close above the day's average price did <b>worse</b> over the next week in ${yrsNeg} of ${yrsAll} years. Someone pushing the closing price up rarely lasts.`, "use", "Used: signal + never-buy rule"],
    ["🚦", "Circuits: don't chase, don't catch knives", `After hitting the −10% lower circuit, stocks fell another ${Math.abs(cb.after_lower_next5).toFixed(1)}% over the next week on average. After the +10% upper circuit: ${pct(cb.after_upper_next5, 1)} the following week, so chasing doesn't pay.`, "use", "Used: never-buy rules + exit"],
    ["🛑", "Stop-losses destroyed value", stp && base ? `Adding a 15% stop-loss cut the design-period return from ${pct(base.avg, 0)} to ${pct(stp.avg, 0)} and doubled the number of trades (${stp.trades} vs ${base.trades}). Stocks sold by the stop did not keep falling; the stop just locked in the loss.` : `Stocks sold by a stop-loss did not keep falling afterwards, so the stop only locked in the loss and paid another round of fees.`, "no", "Removed in v3"],
    ["🧭", "Market timing failed", `Being in stocks only when NEPSE is above its 50-day average returned ${pct(t50.design.overlay_ret, 0)} vs ${pct(t50.design.buyhold_ret, 0)} buy-and-hold in design, and ${pct(t50.holdout.overlay_ret, 0)} vs ${pct(t50.holdout.buyhold_ret, 0)} in the hold-out.${bear && base ? ` A bear-market cash filter also failed to shrink the worst drop (${pct(bear.dd, 0)} vs ${pct(base.dd, 0)}).` : ""}`, "no", "Removed in v3"],
    ["🧠", "Simple beat clever", lrn && base ? `Letting the bot re-learn its signal weights every day gave ${pct(lrn.avg, 0)} with a wide spread (${pct(lrn.min, 0)} to ${pct(lrn.max, 0)}). Fixed research weights gave ${pct(base.avg, 0)} (${pct(base.min, 0)} to ${pct(base.max, 0)}). The extra cleverness was mostly noise.` : `Fixed research weights were more stable than re-learning them every day.`, "use", "Used: fixed weights + health monitor"],
    ["📉", "Momentum doesn't work on NEPSE", `The classic "buy what went up for 3–12 months" signal had no reliable edge here (12-month IC ${st.mom250.ic_design[0].toFixed(3)} design, ${st.mom250.ic_holdout[0].toFixed(3)} hold-out). Global textbook rules don't automatically apply to Nepal.`, "no", "Not used"],
    ["💸", "Fees decide everything", `A NEPSE round-trip costs about 0.9% plus 7.5% tax on profit. v2 gave up roughly half its gross return to costs. v3 reviews each holding only every 60 trading days and keeps it while it stays in the better half.`, "use", "Used: slow, staggered reviews"],
  ];
  const fc = { use: "--green", no: "--red", info: "--blue" };
  $("#findings").innerHTML = F.map(([i, t, d, c, tag]) => `<div class="finding" style="--fc:var(${fc[c]})"><span class="fi">${i}</span><b>${t}</b><p>${d}</p><span class="tag2 ${c}">${tag}</span></div>`).join("");

  const keys = Object.keys(st).sort((a, b) => (st[a].ic_design[0] + st[a].ic_holdout[0]) - (st[b].ic_design[0] + st[b].ic_holdout[0]));
  charts.ic?.destroy();
  charts.ic = new Chart($("#icChart"), {
    type: "bar",
    data: { labels: keys.map(k => FN[k] || k), datasets: [
      { label: `Design (to ${R.design_end})`, data: keys.map(k => st[k].ic_design?.[0]), backgroundColor: css("--c2"), borderRadius: 1 },
      { label: "Hold-out (unseen)", data: keys.map(k => st[k].ic_holdout?.[0]), backgroundColor: css("--c1"), borderRadius: 1 }] },
    options: baseChartOpts({ indexAxis: "y", scales: { x: { ticks: { color: css("--muted") }, grid: { color: css("--line") } }, y: { ticks: { color: css("--ink2"), font: { size: 11 } }, grid: { display: false } } } }),
  });
  const years = [...new Set(keys.flatMap(k => Object.keys(st[k].ic_by_year)))].sort();
  const hc = v => { const a = Math.min(1, Math.abs(v) / 0.2); const c = v >= 0 ? css("--heat-pos") : css("--heat-neg"); return `background:color-mix(in srgb, ${c} ${Math.round(a * 85)}%, ${css("--heat-mid")});color:${a > .5 ? "#f5ecc2" : css("--ink")}`; };
  $("#icYears").innerHTML = `<thead><tr><th>Signal</th>${years.map(y => `<th>${y}</th>`).join("")}</tr></thead><tbody>` +
    [...keys].reverse().map(k => `<tr><td><b>${FN[k] || k}</b></td>${years.map(y => { const v = st[k].ic_by_year[y]; return v == null ? "<td></td>" : `<td class="z" style="${hc(v)}">${(v * 100).toFixed(0)}</td>`; }).join("")}</tr>`).join("") + "</tbody>";

  const T = mk.trend, tk = Object.keys(T);
  charts.tr?.destroy();
  charts.tr = new Chart($("#trendChart"), {
    type: "bar",
    data: { labels: tk.map(k => k.replace("sma", "") + "-day avg"), datasets: [
      { label: "Design: above", data: tk.map(k => T[k].design.above), backgroundColor: css("--green") },
      { label: "Design: below", data: tk.map(k => T[k].design.below), backgroundColor: css("--red") },
      { label: "Hold-out: above", data: tk.map(k => T[k].holdout.above), backgroundColor: css("--green") + "77" },
      { label: "Hold-out: below", data: tk.map(k => T[k].holdout.below), backgroundColor: css("--red") + "77" }] },
    options: baseChartOpts({ plugins: { legend: { position: "bottom", labels: { color: css("--ink2"), boxWidth: 10 } }, tooltip: { callbacks: { label: c => `${c.dataset.label}: ${c.parsed.y.toFixed(2)}%` } } } }),
  });
  const q = ["Weakest", "Weak", "Middle", "Strong", "Strongest"];
  charts.br?.destroy();
  charts.br = new Chart($("#breadthChart"), {
    type: "bar",
    data: { labels: q, datasets: [
      { label: "Design", data: mk.breadth_q.design, backgroundColor: css("--c2"), borderRadius: 1 },
      { label: "Hold-out", data: mk.breadth_q.holdout, backgroundColor: css("--c1"), borderRadius: 1 }] },
    options: baseChartOpts({ plugins: { legend: { labels: { color: css("--ink2"), boxWidth: 10 } }, tooltip: { callbacks: { label: c => `${c.dataset.label}: ${c.parsed.y.toFixed(2)}% next 20 days` } } } }),
  });
  const mn = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  charts.mo?.destroy();
  charts.mo = new Chart($("#monthChart"), {
    type: "bar",
    data: { labels: mn, datasets: [{ label: "Avg NEPSE change", data: mn.map((_, i) => mk.month[i + 1]), backgroundColor: mn.map((_, i) => mk.month[i + 1] >= 0 ? css("--green") : css("--red")) }] },
    options: baseChartOpts({ plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${c.parsed.y.toFixed(2)}% (${mk.month_n[c.dataIndex + 1]} years)` } } } }),
  });
  const yy = Object.keys(mk.nepse_years);
  charts.yr?.destroy();
  charts.yr = new Chart($("#yearChart"), {
    type: "bar",
    data: { labels: yy, datasets: [{ label: "NEPSE", data: yy.map(y => mk.nepse_years[y]), backgroundColor: yy.map(y => mk.nepse_years[y] >= 0 ? css("--green") : css("--red")) }] },
    options: baseChartOpts({ plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${c.parsed.y.toFixed(1)}%` } } } }),
  });
}

function robustness() {
  const R = ROB;
  $$(".rbCut").forEach(e => e.textContent = R.split);
  const st = R.starts || [];
  const avg = k => st.reduce((a, r) => a + r[k], 0) / (st.length || 1);
  $("#rbStarts").innerHTML = `<thead><tr><th>Started on</th><th>Design</th><th>NEPSE</th><th>Hold-out</th><th>NEPSE</th><th>Avg stock</th><th>Worst drop</th></tr></thead><tbody>` +
    st.map(r => `<tr><td><b>${r.start}</b></td><td class="${cls(r.design)}">${pct(r.design, 0)}</td><td class="muted">${pct(r.design_nepse, 0)}</td><td class="${cls(r.holdout)}"><b>${pct(r.holdout, 1)}</b></td><td class="muted">${pct(r.holdout_nepse, 1)}</td><td class="muted">${pct(r.holdout_ew, 1)}</td><td class="down">${pct(r.holdout_dd, 0)}</td></tr>`).join("") +
    (R.fresh ? `<tr><td><b>Fresh Rs 5 lakh on ${R.fresh.start}</b></td><td class="muted">—</td><td class="muted">—</td><td class="${cls(R.fresh.holdout)}"><b>${pct(R.fresh.holdout, 1)}</b></td><td class="muted">${pct(R.fresh.holdout_nepse, 1)}</td><td class="muted">${pct(R.fresh.holdout_ew, 1)}</td><td class="down">${pct(R.fresh.holdout_dd, 0)}</td></tr>` : "") +
    (st.length ? `<tr class="chosen"><td><b>Average</b></td><td>${pct(avg("design"), 0)}</td><td class="muted">${pct(avg("design_nepse"), 0)}</td><td><b>${pct(avg("holdout"), 1)}</b></td><td class="muted">${pct(avg("holdout_nepse"), 1)}</td><td class="muted">${pct(avg("holdout_ew"), 1)}</td><td class="down">${pct(avg("holdout_dd"), 0)}</td></tr>` : "") + "</tbody>";
  const eh = st.map(r => r.edge_holdout).filter(Boolean), ea = st.map(r => r.edge_all).filter(Boolean);
  const m = (a, k) => a.reduce((x, r) => x + r[k], 0) / (a.length || 1);
  $("#rbEdge").innerHTML = eh.length ? `Monthly edge over NEPSE: <b>${pct(m(ea, "monthly_pct"), 2)}</b> over the whole period (t = ${m(ea, "t").toFixed(1)}), but only <b>${pct(m(eh, "monthly_pct"), 2)}</b> in the hold-out (t = ${m(eh, "t").toFixed(1)}; above 2 would be convincing). In the hold-out the bot beat NEPSE in ${m(eh, "win_months_pct").toFixed(0)}% of months.` : "";
  const S = R.sensitivity || [], mxv = Math.max(...S.map(r => r.avg), 1);
  $("#rbSens").innerHTML = `<thead><tr><th>Setting</th><th>Design return</th><th>Range</th><th>Worst drop</th><th>Trades</th></tr></thead><tbody>` +
    S.map((r, i) => `<tr class="${i === 0 ? "chosen" : ""}"><td><b>${esc(r.label)}</b></td><td>${pct(r.avg, 0)}<span class="bar2" style="width:${Math.max(0, r.avg) / mxv * 70}px;background:${r.avg >= R.design_nepse ? css("--c1") : css("--red")}"></span></td><td class="muted">${pct(r.min, 0)} to ${pct(r.max, 0)}</td><td class="down">${pct(r.dd, 0)}</td><td class="muted">${r.trades}</td></tr>`).join("") +
    `<tr><td class="muted">NEPSE index, same period</td><td class="muted">${pct(R.design_nepse, 0)}</td><td></td><td></td><td></td></tr></tbody>`;
  const lo = Math.min(...S.map(r => r.avg)), hi = Math.max(...S.map(r => r.avg));
  $("#rbSensNote").textContent = `Every variation returned between ${pct(lo, 0)} and ${pct(hi, 0)} in the design period, against ${pct(R.design_nepse, 0)} for NEPSE. No single setting is doing the work.`;
  const hAvg = avg("holdout"), hN = avg("holdout_nepse"), hE = avg("holdout_ew"), fr = R.fresh;
  $("#rbVerdict").innerHTML = `<h3>The honest verdict</h3><ul>
    <li><b>What held up:</b> v3 is far better than the earlier versions in the design period, stays good when any setting is changed, and survives doubled trading costs. On unseen data, portfolios that were already running made ${pct(hAvg, 1)} on average against ${pct(hN, 1)} for NEPSE and ${pct(hE, 1)} for the average stock.</li>
    <li><b>What did not:</b> ${fr ? `a <b>fresh</b> Rs 5 lakh portfolio started on ${fr.start} made only <b>${pct(fr.holdout, 1)}</b> against ${pct(fr.holdout_nepse, 1)} for NEPSE. ` : ""}The huge design-period lead (${pct(avg("design"), 0)} vs ${pct(avg("design_nepse"), 0)}) did not carry over: in the hold-out the monthly edge is statistically zero, and in 2025–26 the big, heavily traded banks led while quiet stocks lagged. There is <b>no proof yet that this beats NEPSE going forward</b>.</li>
    <li><b>What to expect:</b> returns close to the average NEPSE stock, ahead of the index in broad rallies (2021, 2024) and behind it when a few large stocks lead (2025–26). The worst historical drop was about ${pct(S.length ? S[0].dd : null, 0)}.</li>
    <li><b>Known gaps:</b> cash dividends are not counted (a small understatement), merged stocks are settled at their last price, and thinly traded stocks can be harder to sell in a panic than any model assumes. The strategy also stops scaling at roughly Rs 20 lakh, because quiet stocks cannot absorb bigger orders.</li>
    <li><b>Why keep running it live:</b> a backtest is an argument; the live tab is the evidence. Every trading day adds to it.</li></ul>`;
}

const VB = { edge: ["EDGE", "edge"], avoid: ["AVOID", "avoid"], noise: ["NO EDGE", "noise"], "too few": ["TOO FEW", "few"] };
function quick() {
  const Q = QT, H = "h" + qh, S = Q.setups, keys = Object.keys(S);
  const vb = v => `<span class="vb ${VB[v]?.[1] || "noise"}">${VB[v]?.[0] || v}</span>`;
  const nEdge = keys.filter(k => S[k].verdict === "edge").length, nAvoid = keys.filter(k => S[k].verdict === "avoid").length;
  const an = Q.announcement || {}, b10 = an.bonus10 || {};
  const cell = x => x && x.net != null ? `<td class="${cls(x.net)}"><b>${pct(x.net, 1)}</b></td><td class="muted">${x.win.toFixed(0)}%</td><td class="${cls(x.excess)}">${pct(x.excess, 1)}</td>` : `<td colspan="3" class="muted">too few</td>`;
  $("#qVerdict").innerHTML = `<h3>The short answer</h3><ul>
    <li><b>${nEdge ? `${nEdge} setup(s) showed a real edge.` : "No short-term setup made money after costs"}</b> in either period. Of ${keys.length} setups tested, <b>${nAvoid} are reliably worse than the market</b>: chasing upper circuits, volume spikes, late pushes, rallies and breakouts loses money on NEPSE.</li>
    <li><b>The costs are the wall.</b> A round trip costs about ${Q.cost_pct.toFixed(1)}% plus ${Q.cgt_pct.toFixed(1)}% tax on profit. Even the best setup (buying after a weak close) beats the market by only ~0.3% over 5 days.</li>
    <li><b>The one real pattern is invisible to the public.</b> Before a bonus of 10%+ is announced, the stock beats the market by <b>${pct(b10.pre, 1)}</b> over 20 trading days. After the announcement it gives back <b>${pct(b10.post, 1)}</b>. By the time the news is public, the move is over.</li>
    <li><b>What would change this:</b> seeing who is buying before the news (broker floorsheet data). The bot started recording it daily; see the bottom of this section.</li></ul>`;
  $("#qTable").innerHTML = `<thead><tr><th>Setup (seen at the close)</th><th>Design: net</th><th>win</th><th>vs market</th><th>Hold-out: net</th><th>win</th><th>vs market</th><th>Trades</th><th>Verdict</th></tr></thead><tbody>` +
    keys.sort((a, b) => ({ edge: 0, noise: 1, "too few": 2, avoid: 3 }[S[a].verdict] - { edge: 0, noise: 1, "too few": 2, avoid: 3 }[S[b].verdict]) || ((S[b].holdout?.[H]?.excess ?? -99) - (S[a].holdout?.[H]?.excess ?? -99)))
      .map(k => { const r = S[k], d = r.design?.[H], o = r.holdout?.[H];
        return `<tr><td><b>${esc(r.label)}</b><small>${esc(r.desc)}</small></td>${cell(d)}${cell(o)}<td class="muted">${((d?.n || 0) + (o?.n || 0)).toLocaleString("en-IN")}</td><td>${vb(r.verdict)}</td></tr>`; }).join("") + "</tbody>";

  const bc = Q.book_closure || {};
  charts.qp?.destroy();
  if (an.days) {
    charts.qp = new Chart($("#qPath"), {
      type: "line",
      data: { labels: an.days.map(d => d === 0 ? "Day 0" : (d > 0 ? "+" + d : d)), datasets: [
        { label: `Bonus 10%+ announcement (${b10.n})`, data: b10.cum_excess, borderColor: css("--c1"), borderWidth: 2.2, pointRadius: 0, tension: .25 },
        { label: `Any dividend announcement (${an.n})`, data: an.cum_excess, borderColor: css("--c2"), borderWidth: 1.8, borderDash: [5, 4], pointRadius: 0, tension: .25 },
        { label: `Book-closure day (${bc.n})`, data: bc.cum_excess, borderColor: css("--c3"), borderWidth: 1.6, borderDash: [2, 3], pointRadius: 0, tension: .25 }] },
      options: baseChartOpts({ plugins: { legend: { position: "bottom", labels: { color: css("--ink2"), boxWidth: 10, usePointStyle: true } },
        tooltip: { callbacks: { title: c => `Trading day ${c[0].label}`, label: c => `${c.dataset.label}: ${pct(c.parsed.y, 2)} vs market` } } },
        scales: { x: { ticks: { color: css("--muted"), maxTicksLimit: 9, maxRotation: 0 }, grid: { display: false } }, y: { ticks: { color: css("--muted"), callback: v => v + "%" }, grid: { color: css("--line") } } } }),
    });
    $("#qPathNote").innerHTML = `Return vs the average stock, set to 0 the day before the event (day 0). The stock rises <b>before</b> the public announcement and drifts down after it. That pattern suggests informed buying ahead of the news.`;
  }
  const dv = Q.dividends;
  if (dv) {
    const rowD = (g, per, k) => { const x = dv.groups[g]?.[per]?.[k] || {}; return x.net != null ? `<td class="${cls(x.net)}">${pct(x.net, 1)}</td><td class="${cls(x.excess)}">${pct(x.excess, 1)}</td>` : `<td colspan="2" class="muted">few</td>`; };
    $("#qDiv").innerHTML = `<thead><tr><th>Buy the day after the announcement…</th><th>Design net</th><th>vs mkt</th><th>Hold-out net</th><th>vs mkt</th></tr></thead><tbody>` +
      Object.keys(dv.groups).flatMap(g => [["sell_before", "sell 3 days before book closure"], ["hold_through", "hold through, collect bonus/dividend"]].map(([k, lab]) =>
        `<tr><td><b>${esc(g)}</b><small class="muted" style="display:block">${lab}</small></td>${rowD(g, "design", k)}${rowD(g, "holdout", k)}</tr>`)).join("") + "</tbody>";
    $("#qDivNote").innerHTML = `Uses ${dv.n_tradable} tradable announcements from ShareSansar's dividend calendar (median ${dv.median_gap_days} trading days between announcement and book closure). None of these beat the market after costs, so the public calendar is priced in almost immediately.`;
  }
  const T = Q.today || {};
  $("#qToday").textContent = T.date ? `as of ${fmtDate(T.date)}` : "";
  const hist = k => S[k]?.holdout?.[H];
  $("#qRadar").innerHTML = (T.triggers || []).length ? T.triggers.map(t => {
    const h = hist(t.setup);
    return `<div class="qgroup"><div class="qh"><b>${esc(t.label)}</b>${vb(t.verdict)}<span class="muted">${t.count} stock${t.count === 1 ? "" : "s"}${h && h.net != null ? ` · historically ${pct(h.net, 1)} net after ${qh} days, won ${h.win.toFixed(0)}% of the time` : ""}</span></div>
      <div class="chips">${t.stocks.map(x => `<span class="chip2" title="${esc(x.sector)}">${x.sym}<small>${pct(x.chg, 1)}</small></span>`).join("")}</div></div>`;
  }).join("") : `<div class="empty">No setup triggered at the latest close.</div>`;
  const fs = Q.floorsheet || {};
  const N = [
    ["have", "Daily prices, volume, VWAP (2019→)", "The basis of every test on this page. ~1,800 trading days, 475 stocks."],
    ["have", `Dividend & bonus calendar (${dv ? dv.n_announcements.toLocaleString("en-IN") : "—"} announcements)`, "Announcement and book-closure dates from ShareSansar, refreshed weekly. Showed that the public calendar is priced in too fast to trade."],
    ["collecting", `Broker floorsheet (${fs.days || 0} day${fs.days === 1 ? "" : "s"} collected${fs.first ? `, since ${fs.first}` : ""})`, "Who bought and who sold, by broker number. Could detect quiet accumulation before news. Only today's sheet is ever published, so the bot now saves a summary every day. Testable after ~6–12 months."],
    ["missing", "Quarterly earnings (EPS) with release dates", "Stocks often keep drifting after a surprise result. Needs each company's quarterly report and the date it was published; not available in bulk for free."],
    ["missing", "Intraday prices & order book (market depth)", "Short-term edges usually live inside the day. NEPSE's live depth is not archived publicly, so it cannot be backtested."],
    ["missing", "News & AGM / right-share notices with timestamps", "To know exactly when information became public. Scattered across company notices and news sites."],
  ];
  const nc = { have: "--green", collecting: "--amber", missing: "--red" }, nl = { have: "Have", collecting: "Collecting now", missing: "Not available" };
  $("#qNeeds").innerHTML = N.map(([st, t, d]) => `<div class="need" style="--nc:var(${nc[st]})"><span class="st">${nl[st]}</span><b>${t}</b><p>${d}</p></div>`).join("");
}

function chartPicks() {
  const T = TC, R = T.horizons["h" + th] || {}, R15 = T.horizons.h15 || {};
  const e = R15.expert || {}, ed = e.design || {}, eh = e.holdout || {}, hi = R15.high52?.holdout || {};
  $("#tVerdict").innerHTML = `<h3>How often were chart picks right? (3-week hold, after costs)</h3><ul>
    <li><b>2019 to mid-2024:</b> the weekly top-5 picks went up <b>${ed.up_pct}%</b> of the time; a random liquid stock went up ${ed.base_up_pct}%. They beat NEPSE ${ed.beat_pct}% of the time (random: ${ed.base_beat_pct}%).</li>
    <li><b>Mid-2024 to today (unseen data):</b> the picks went up only <b>${eh.up_pct}%</b> of the time vs ${eh.base_up_pct}% for random stocks, and beat NEPSE ${eh.beat_pct}% of the time (random: ${eh.base_beat_pct}%). <b>Worse than picking at random.</b></li>
    <li><b>None of the 11 classic chart signals beat random picks reliably.</b> The closest was "near 52-week high" (up ${hi.up_pct}% vs ${hi.base_up_pct}% random in the hold-out), but that gap is within luck.</li>
    <li><b>Why:</b> on NEPSE, stocks that look strongest on a chart are usually the most crowded, and the crowd's favourites tend to lag (see <a href="#brain">the main model</a>). Costs of ~1.4% per round trip also mean most 3-week trades lose money.</li>
    <li><b>So treat the picks below as an experiment.</b> The live scoreboard will show whether they do better from here.</li></ul>`;
  const P = T.picks || {};
  $("#tDate").textContent = P.date ? `from the close of ${fmtDate(P.date)}` : "";
  $("#tPicks").innerHTML = (P.list || []).length ? P.list.map((p, i) => `<div class="tp ${i < T.top_n ? "top" : ""}">
      <span class="pts" title="Bullish checklist points">${"●".repeat(p.points)}${"○".repeat(8 - p.points)} ${p.points}/8</span>
      <div class="s">${p.sym}</div><div class="sec">${esc(p.sector)}${i < T.top_n ? " · top 5" : ""}</div>
      <div class="lv"><span>Close</span><b>${num(p.close, 1)}</b><span>Stop</span><b class="down">${num(p.stop, 1)}</b>
        <span>Target</span><b class="up">${num(p.target, 1)}</b><span>RSI · ADX</span><b>${p.rsi} · ${p.adx}</b>
        <span>3m vs NEPSE</span><b class="${cls(p.rs60)}">${pct(p.rs60, 1)}</b><span>50 / 200-day</span><b>${num(p.sma50, 0)} / ${num(p.sma200, 0)}</b></div>
      <div class="tg">${p.tags.map(t => `<span>${esc(t)}</span>`).join("")}</div></div>`).join("")
    : `<div class="empty">No stock scores 6 or more on the checklist today.</div>`;
  const L = T.live || {};
  const k = (lab, v, sub) => `<div class="sp"><small>${lab}</small><div class="big">${v}</div><div class="vs">${sub}</div></div>`;
  $("#tLiveKpi").innerHTML = k("Predictions judged", L.judged || 0, `${L.total_weeks || 0} week(s) logged`)
    + k("Went up (after costs)", L.judged ? `${L.up} of ${L.judged}` : "—", L.judged ? `${(L.up / L.judged * 100).toFixed(0)}% · backtest: ~${eh.up_pct}%` : "first results after 3 weeks")
    + k("Beat NEPSE", L.judged ? `${L.beat} of ${L.judged}` : "—", L.judged ? `${(L.beat / L.judged * 100).toFixed(0)}%` : "")
    + k("Average result", L.avg_net != null ? pct(L.avg_net, 1) : "—", "per pick, after costs and tax");
  $("#tLiveNote").textContent = L.judged ? "" : "The first week's picks are judged 3 weeks after they were made";
  $("#tLive").innerHTML = (L.weeks || []).length ? `<thead><tr><th>Picked at close of</th><th>Picks and results</th><th>NEPSE</th><th>Status</th></tr></thead><tbody>` +
    [...L.weeks].reverse().map(w => `<tr><td><b>${w.signal}</b></td><td>${w.picks.map(p => p.net == null ? `<span class="res o">${p.sym}</span>`
      : `<span class="res ${p.net > 0 ? "w" : "l"}" title="Before costs ${pct(p.gross, 1)}">${p.sym} ${pct(p.net, 1)}</span>`).join("")}</td>
      <td class="${cls(w.nepse)}">${w.nepse != null ? pct(w.nepse, 1) : "—"}</td><td class="muted">${esc(w.status || "")}</td></tr>`).join("") + "</tbody>" : "";
  const vb2 = v => `<span class="vb ${v === "edge" ? "edge" : v === "worse than random" ? "avoid" : "noise"}">${v.toUpperCase()}</span>`;
  const c2 = (x, k1, k2, unit) => x && x[k1] != null ? `<td><b class="${x[k1] > x[k2] ? "up" : "down"}">${x[k1]}${unit}</b> <small class="muted">vs ${x[k2]}${unit}</small></td>` : `<td class="muted">—</td>`;
  const order = ["expert", "expert_quiet", ...Object.keys(R).filter(k => !k.startsWith("expert"))];
  $("#tTable").innerHTML = `<thead><tr><th>Signal</th><th>Went up: design</th><th>Went up: hold-out</th><th>Beat NEPSE: hold-out</th><th>Avg result: hold-out</th><th>Trades</th><th>Verdict</th></tr></thead><tbody>` +
    order.filter(k => R[k]).map(k => { const r = R[k], d = r.design, o = r.holdout;
      return `<tr><td><b>${esc(r.label)}</b><small>${esc(r.desc)}</small></td>${c2(d, "up_pct", "base_up_pct", "%")}${c2(o, "up_pct", "base_up_pct", "%")}${c2(o, "beat_pct", "base_beat_pct", "%")}${c2(o, "avg_net", "base_net", "%")}<td class="muted">${((d?.n || 0) + (o?.n || 0)).toLocaleString("en-IN")}</td><td>${vb2(r.verdict)}</td></tr>`; }).join("") + "</tbody>";
}

/* ---- events */
$$(".mode button").forEach(b => b.onclick = () => { mode = b.dataset.mode; render(); });
$("#tH").onclick = e => { const b = e.target.closest("button"); if (!b) return; th = +b.dataset.h; $$("#tH button").forEach(x => x.classList.toggle("on", x === b)); if (TC) chartPicks(); };
$("#qH").onclick = e => { const b = e.target.closest("button"); if (!b) return; qh = +b.dataset.h; $$("#qH button").forEach(x => x.classList.toggle("on", x === b)); if (QT) quick(); };
$("#pfSeg").onclick = e => {
  const b = e.target.closest("button"); if (!b || b.dataset.pf === pf) return;
  pf = b.dataset.pf;
  try { localStorage.setItem("pf", pf); } catch (err) { }
  render();
};
$("#rangeSeg").onclick = e => { const b = e.target.closest("button"); if (!b) return; range = +b.dataset.r; $$("#rangeSeg button").forEach(x => x.classList.toggle("on", x === b)); equityChart(DATA[mode]); };
$("#tradeSeg").onclick = e => { const b = e.target.closest("button"); if (!b) return; tradeFilter = b.dataset.f; $$("#tradeSeg button").forEach(x => x.classList.toggle("on", x === b)); trades(DATA[mode]); };
$("#filters").onclick = e => { const b = e.target.closest("button"); if (!b) return; logFilter = b.dataset.f; terminal(DATA[mode], true); };
$("#daySel").onchange = () => terminal(DATA[mode], true);
function setThemeIcon() {
  const light = document.documentElement.dataset.theme === "light";
  $("#theme").textContent = light ? "☾" : "☀";
  $("#theme").title = light ? "Switch to night" : "Switch to paper";
}
$("#theme").onclick = () => {
  const r = document.documentElement;
  r.dataset.theme = r.dataset.theme === "light" ? "dark" : "light";
  try { localStorage.setItem("theme", r.dataset.theme); } catch (e) { }
  setThemeIcon();
  render();
  if (RES) research();
  if (ROB) robustness();
  if (QT) quick();
  if (TC) chartPicks();
};
try { const t = localStorage.getItem("theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) { }
setThemeIcon();
plates();
if (window.Chart) { Chart.defaults.font.family = '"IBM Plex Sans", system-ui, sans-serif'; Chart.defaults.font.size = 11.5; }
load().catch(e => { $("#modeBanner").className = "banner"; $("#modeBanner").textContent = "Could not load bot data: " + e; });
