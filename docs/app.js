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
};
function plates() {
  $$(".plate[data-plate]").forEach(el => {
    const [n, cs] = PLATES[el.dataset.plate] || [];
    if (!n) return;
    el.innerHTML = `<span>Plate № ${n}</span><span class="sw">${cs.map(([nm, h]) => `<i style="background:${h}" title="${nm} ${h}"></i>`).join("")}</span><span class="nm">${cs.map(c => c[0]).join(" · ")}</span>`;
  });
}

let RES = null;
let META, DATA = {}, mode = "live", charts = {}, logFilter = "all", tradeFilter = "all", range = 0;

async function load() {
  const get = f => fetch(`data/${f}?t=${Date.now()}`).then(r => r.json());
  [META, DATA.live, DATA.backtest] = await Promise.all([get("meta.json"), get("live.json"), get("backtest.json")]);
  $("#updated").textContent = `Last bot run: ${META.updated} · latest market data: ${META.latest_market_date}`;
  $("#runlog").textContent = (META.run_log || []).slice(1).join(" · ");
  try { RES = await get("research.json"); } catch (e) { RES = null; }
  render();
  if (RES) research();
}

function render() {
  const S = DATA[mode];
  $$(".mode button").forEach(b => b.classList.toggle("on", b.dataset.mode === mode));
  const B = $("#modeBanner");
  if (mode === "live") {
    B.className = "banner live";
    B.innerHTML = `<b>🟢 Live paper trading</b> since <b>${S.inception}</b>, with real NEPSE prices and virtual money (${rs(S.start_capital)}). The bot runs by itself after every trading day; this page updates automatically. Day <b>${S.equity.length}</b> of the experiment.`;
  } else {
    B.className = "banner bt";
    B.innerHTML = `<b>⟲ Historical simulation.</b> What the <i>exact same bot</i> would have done since <b>${S.inception}</b>, deciding each day with only the data available at that time. Covers the COVID shutdown, the 2020–21 mania, the 2022–23 crash and the September 2025 shutdown. The model was designed on data up to <b>${META.split}</b>; everything after is an unseen <b>hold-out test</b>.`;
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
  const b = S.brain || {};
  $("#mood").textContent = b.regime || "—";
  $("#gross").textContent = b.gross ? Math.round(b.gross * 100) + "%" : "—";
  $("#moodNeedle").style.left = ({ Bearish: "15%", Neutral: "50%", Bullish: "85%" }[b.regime] || "50%");
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
      <div class="row" style="margin-top:8px">${lock}</div>
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
  const pri = b.priors || {};
  const mx = Math.max(...Object.values(b.weights).map(Math.abs), ...Object.values(pri).map(Math.abs), 0.01);
  $("#weights").innerHTML = Object.entries(b.weights).sort((x, y) => Math.abs(y[1]) - Math.abs(x[1])).map(([k, w]) => {
    const wd = Math.abs(w) / mx * 50, pos = w >= 0;
    return `<div class="wrow"><div class="nm">${sig[k].name}<small>${sig[k].desc}</small></div>
      <div class="wbar" title="Now ${(w * 100).toFixed(0)}% · research starting point ${((pri[k] || 0) * 100).toFixed(0)}%"><span style="background:${pos ? css("--green") : css("--red")};${pos ? `left:50%;width:${wd}%` : `right:50%;width:${wd}%`}"></span>${k in pri ? `<i class="prior" style="left:${50 + (pri[k] / mx) * 50}%"></i>` : ""}</div>
      <div class="pc ${pos ? "up" : "down"}">${(w * 100).toFixed(0)}%</div></div>`;
  }).join("") + `<p class="hint">Green = the bot buys stocks scoring high on this. Red = it learned this signal works <b>in reverse</b> on NEPSE, so high scores count against a stock. ▼ = where 8 years of research put it; the bar = today's learned trust.</p>`;
  $("#uniN").textContent = b.universe;
  const keys = Object.keys(sig);
  const color = v => { const a = Math.min(1, Math.abs(v) / 2.5); const c = v >= 0 ? css("--heat-pos") : css("--heat-neg"); return `background:color-mix(in srgb, ${c} ${Math.round(a * 85)}%, ${css("--heat-mid")});color:${a > .5 ? "#f5ecc2" : css("--ink")}`; };
  $("#heat").innerHTML = `<thead><tr><th>#</th><th>Stock</th><th>Sector</th><th>Price</th>${keys.map(k => `<th title="${sig[k].desc}">${sig[k].name}</th>`).join("")}<th>Score</th></tr></thead><tbody>` +
    b.top.map((t, i) => `<tr><td>${i + 1}</td><td><b>${t.sym}</b>${t.veto ? `<span class="veto" title="Never-buy rule: ${esc(t.veto)}">🚫</span>` : ""}</td><td class="muted">${esc(t.sector)}</td><td>${num(t.close, 1)}</td>${keys.map(k => `<td class="z" style="${color(t.z[k])}">${t.z[k].toFixed(1)}</td>`).join("")}<td class="s up">${t.alpha.toFixed(2)}</td></tr>`).join("") + "</tbody>";
}

function weightChart(S) {
  let wh = S.weight_history || [], note = "";
  if (wh.length < 5) { wh = DATA.backtest.weight_history || []; note = "From the historical simulation"; }
  $("#multNote").textContent = note;
  const sig = META.signals, keys = Object.keys(sig), pri = (S.brain || DATA.backtest.brain || {}).priors || {};
  Object.values(charts.mult || {}).forEach(c => c.destroy());
  charts.mult = {};
  const lim = Math.max(10, ...wh.flatMap(w => keys.map(k => Math.abs(w[k] * 100)))) * 1.05;
  $("#mults").innerHTML = keys.map(k => {
    const last = (wh.at(-1)?.[k] ?? 0) * 100;
    return `<div class="mult"><div class="mh"><b title="${esc(sig[k].desc)}">${sig[k].name}</b><span class="${cls(last)}">${last >= 0 ? "+" : ""}${last.toFixed(0)}%</span></div><div class="mc"><canvas id="m_${k}" aria-label="${sig[k].name} trust over time"></canvas></div></div>`;
  }).join("");
  const tip = { backgroundColor: css("--ink"), titleColor: css("--bg"), bodyColor: css("--bg"), padding: 8, displayColors: false };
  keys.forEach(k => {
    charts.mult[k] = new Chart($("#m_" + k), {
      type: "line",
      data: { labels: wh.map(w => w.date), datasets: [
        { data: wh.map(w => w[k] * 100), borderColor: css("--ink2"), borderWidth: 1.5, pointRadius: 0, pointHoverRadius: 4, tension: .3,
          fill: { target: { value: 0 }, above: css("--green") + "40", below: css("--red") + "40" } },
        { data: wh.map(() => (pri[k] ?? 0) * 100), borderColor: css("--amber"), borderDash: [3, 3], borderWidth: 1, pointRadius: 0, pointHoverRadius: 0 },
        { data: wh.map(() => 0), borderColor: css("--muted") + "66", borderWidth: 1, pointRadius: 0, pointHoverRadius: 0 }] },
      options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: false }, tooltip: { ...tip, filter: c => c.datasetIndex < 2,
          callbacks: { label: c => c.datasetIndex ? `Research start: ${c.parsed.y.toFixed(0)}%` : `Trust: ${c.parsed.y.toFixed(0)}%` } } },
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
    ["slippage", "Slippage", "Paying slightly worse than the day's average price (simulated 0.25%)", css("--c3")],
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
    <div class="vs">Worst drop ${pct(x.max_drawdown_pct, 1)} (NEPSE ${pct(x.nepse_drawdown_pct, 1)})${x.cagr_pct != null ? ` · ${x.cagr_pct}%/yr` : ""}</div>${note ? `<div class="vs muted">${note}</div>` : ""}</div>` : "";
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

const FN = { pressure: "Closing pressure", calm60: "Low volatility (60d)", calm: "Low volatility (20d)", maxret: "No lottery spikes", upcirc: "Circuit chasing", high52: "Near 52-week high", reversal: "Short-term bounce", rev20: "1-month reversal", momentum: "3-month momentum", mom120: "6-month momentum", mom250: "12-month momentum", meanrev: "Rubber band", volume: "Volume confirmation", quality: "Trend quality", sector: "Sector rotation (1m)", sector60: "Sector rotation (3m)", turntrend: "Turnover trend", size: "Company size" };

function research() {
  const R = RES, st = R.stocks.h20, s5 = R.stocks.h5, mk = R.market, cb = R.circuits;
  const y0 = R.period[0].slice(0, 4), y1 = R.period[1].slice(0, 4);
  $("#rsYears").textContent = `${y1 - y0 + 1} years (${y0}–${y1})`;
  $("#rsCut").textContent = R.design_end;
  const yrsNeg = Object.values(s5.pressure.ic_by_year).filter(v => v < 0).length, yrsAll = Object.keys(s5.pressure.ic_by_year).length;
  const calmY = Object.entries(st.calm60.ic_by_year), calmPos = calmY.filter(([, v]) => v > 0).length;
  const t50 = mk.trend.sma50;
  const F = [
    ["🎯", "Closing pressure works in reverse", `Stocks that close above the day's average price did <b>worse</b> over the next week in ${yrsNeg} of ${yrsAll} years. Late-day pushes tend to unwind. The most reliable pattern found.`, "use", "Used: strongest signal (counts against)"],
    ["🐢", "Boring beats exciting… usually", `Calm stocks without lottery-like spikes did better in ${calmPos} of ${calmY.length} years, but badly lagged in the 2020–21 mania. That's why the bot's trust in it adapts.`, "use", "Used: weight adapts"],
    ["🚦", "Circuits: don't chase, don't catch knives", `After hitting the −10% lower circuit, stocks fell another ${Math.abs(cb.after_lower_next5).toFixed(1)}% over the next week on average. After the +10% upper circuit: ${pct(cb.after_upper_next5, 1)} the following week, so chasing doesn't pay.`, "use", "Used: never-buy rules"],
    ["📉", "Momentum doesn't work on NEPSE", `The classic "buy what went up for 3–12 months" signal had no reliable edge here (12-month IC ${st.mom250.ic_design[0].toFixed(3)} design, ${st.mom250.ic_holdout[0].toFixed(3)} hold-out). Global textbook rules don't automatically apply to Nepal.`, "no", "Starts at zero weight"],
    ["🧭", "Simple market timing failed", `Being in stocks only when NEPSE is above its 50-day average returned ${pct(t50.design.overlay_ret, 0)} vs ${pct(t50.design.buyhold_ret, 0)} buy-and-hold in design, and ${pct(t50.holdout.overlay_ret, 0)} vs ${pct(t50.holdout.buyhold_ret, 0)} in the hold-out.`, "no", "Replaced by a bear-market-only filter"],
    ["💸", "Fees decide everything", `A NEPSE round-trip costs ~0.85% plus 7.5% tax on profit. Signals that change every week can't beat that. Reviewing every 40 days instead of 10–20 turned a losing model into a profitable one.`, "use", "Used: 40-day review cycle"],
    ["🪔", "Festivals & months: mostly noise", `NEPSE rose ${mk.holidays.pre5_avg}% on average in the week before long holidays (vs ${mk.holidays.any5_avg}% for any week). Monthly patterns swing wildly with only ${y1 - y0} years of data.`, "no", "Not used"],
    ["🌊", "Weekly trends persist slightly", `NEPSE's weekly returns are positively correlated (${mk.autocorr.weekly}): good weeks tend to follow good weeks, a sign of a retail-driven market. Too weak to trade alone after costs.`, "info", "Monitored"],
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

/* ---- events */
$$(".mode button").forEach(b => b.onclick = () => { mode = b.dataset.mode; render(); });
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
};
try { const t = localStorage.getItem("theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) { }
setThemeIcon();
plates();
if (window.Chart) { Chart.defaults.font.family = '"IBM Plex Sans", system-ui, sans-serif'; Chart.defaults.font.size = 11.5; }
load().catch(e => { $("#modeBanner").className = "banner"; $("#modeBanner").textContent = "Could not load bot data: " + e; });
