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
const SIGCOL = ["#2fd497", "#6e93ff", "#ffb224", "#ff6b6f", "#a08bff", "#3cc8e8", "#ff8fcf", "#c2d94c"];

let META, DATA = {}, mode = "live", charts = {}, logFilter = "all", tradeFilter = "all", range = 0;

async function load() {
  const get = f => fetch(`data/${f}?t=${Date.now()}`).then(r => r.json());
  [META, DATA.live, DATA.backtest] = await Promise.all([get("meta.json"), get("live.json"), get("backtest.json")]);
  $("#updated").textContent = `Last bot run: ${META.updated} · latest market data: ${META.latest_market_date}`;
  $("#runlog").textContent = (META.run_log || []).slice(1).join(" · ");
  render();
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
    B.innerHTML = `<b>⟲ Historical simulation.</b> What the <i>exact same bot</i> would have done since <b>${S.inception}</b>, deciding each day only with data available at that time. Includes the September 2025 market shutdown and crash. Use it to judge the strategy; the live tab is the real test.`;
  }
  kpis(S); story(S); equityChart(S); holdings(S); orders(S); brain(S); weightChart(S); terminal(S); trades(S); fees(S);
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
  if (range) eq = eq.slice(-range);
  const base = eq[0]?.nepse, startEq = eq[0]?.equity;
  const labels = eq.map(e => e.date);
  const bot = eq.map(e => e.equity);
  const nep = eq.map(e => base && e.nepse ? (e.nepse / base) * startEq : null);
  charts.eq?.destroy();
  const ctx = $("#eqChart").getContext("2d");
  const g = ctx.createLinearGradient(0, 0, 0, 300);
  g.addColorStop(0, css("--green") + "55"); g.addColorStop(1, css("--green") + "00");
  charts.eq = new Chart(ctx, {
    type: "line",
    data: {
      labels, datasets: [
        { label: "Bot portfolio", data: bot, borderColor: css("--green"), backgroundColor: g, fill: true, borderWidth: 2.4, pointRadius: eq.length < 3 ? 5 : 0, tension: .25 },
        { label: "NEPSE index (same Rs start)", data: nep, borderColor: css("--muted"), borderDash: [5, 4], borderWidth: 1.6, pointRadius: eq.length < 3 ? 4 : 0, tension: .25 },
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
  const mx = Math.max(...Object.values(b.weights).map(Math.abs), 0.01);
  $("#weights").innerHTML = Object.entries(b.weights).sort((x, y) => Math.abs(y[1]) - Math.abs(x[1])).map(([k, w]) => {
    const wd = Math.abs(w) / mx * 50, pos = w >= 0;
    return `<div class="wrow"><div class="nm">${sig[k].name}<small>${sig[k].desc}</small></div>
      <div class="wbar"><span style="background:${pos ? css("--green") : css("--red")};${pos ? `left:50%;width:${wd}%` : `right:50%;width:${wd}%`}"></span></div>
      <div class="pc ${pos ? "up" : "down"}">${(w * 100).toFixed(0)}%</div></div>`;
  }).join("") + `<p class="hint">Green = the bot buys stocks scoring high on this. Red = it learned this signal works <b>in reverse</b> on NEPSE, so high scores count against a stock.</p>`;
  $("#uniN").textContent = b.universe;
  const keys = Object.keys(sig);
  const color = v => { const a = Math.min(1, Math.abs(v) / 2.5); const c = v >= 0 ? css("--heat-pos") : css("--heat-neg"); return `background:color-mix(in srgb, ${c} ${Math.round(a * 85)}%, ${css("--heat-mid")});color:${a > .55 ? "#fff" : css("--ink")}`; };
  $("#heat").innerHTML = `<thead><tr><th>#</th><th>Stock</th><th>Sector</th><th>Price</th>${keys.map(k => `<th title="${sig[k].desc}">${sig[k].name}</th>`).join("")}<th>Score</th></tr></thead><tbody>` +
    b.top.map((t, i) => `<tr><td>${i + 1}</td><td><b>${t.sym}</b></td><td class="muted">${esc(t.sector)}</td><td>${num(t.close, 1)}</td>${keys.map(k => `<td class="z" style="${color(t.z[k])}">${t.z[k].toFixed(1)}</td>`).join("")}<td class="s up">${t.alpha.toFixed(2)}</td></tr>`).join("") + "</tbody>";
}

function weightChart(S) {
  let wh = S.weight_history || [], note = "";
  if (wh.length < 5) { wh = DATA.backtest.weight_history || []; note = " (from historical simulation)"; }
  const sig = META.signals, keys = Object.keys(sig);
  charts.w?.destroy();
  charts.w = new Chart($("#wChart"), {
    type: "line",
    data: { labels: wh.map(w => w.date), datasets: keys.map((k, i) => ({ label: sig[k].name, data: wh.map(w => w[k] * 100), borderColor: SIGCOL[i], borderWidth: 1.8, pointRadius: 0, tension: .3 })) },
    options: baseChartOpts({ plugins: { legend: { position: "bottom", labels: { color: css("--ink2"), boxWidth: 8, usePointStyle: true, font: { size: 11 } } }, title: { display: !!note, text: "Trust levels over time" + note, color: css("--muted") }, tooltip: { callbacks: { label: c => `${c.dataset.label}: ${c.parsed.y.toFixed(0)}%` } } } }),
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
    if (n >= out.length) { clearInterval(typing); T.insertAdjacentHTML("beforeend", `<div class="ln"><span class="tag">bot@nepse</span><span>waiting for the next market close <span class="cursor"></span></span></div>`); return; }
    T.insertAdjacentHTML("beforeend", out[n++]); T.scrollTop = T.scrollHeight;
  }, 110);
}

function trades(S) {
  const list = [...S.trades].reverse().filter(t => tradeFilter === "all" || (tradeFilter === "REJECTED" ? t.status === "REJECTED" : t.side === tradeFilter && t.status === "FILLED"));
  $("#tradeCount").textContent = `${list.length} record${list.length === 1 ? "" : "s"}`;
  if (!list.length) { $("#tradeTable").innerHTML = `<tr><td class="muted" style="padding:20px">No trades yet. The first orders execute at the next trading session.</td></tr>`; return; }
  $("#tradeTable").innerHTML = `<thead><tr><th>Date</th><th>Side</th><th>Stock</th><th>Qty</th><th>Price</th><th>Amount</th><th>Fees</th><th>Tax</th><th>Net P/L</th><th>Status</th><th>Why</th></tr></thead><tbody>` +
    list.slice(0, 300).map(t => t.status === "REJECTED"
      ? `<tr><td>${t.date}</td><td><span class="side ${t.side}">${t.side}</span></td><td><b>${t.sym}</b></td><td colspan="6" class="muted">—</td><td class="st rej">✕ ${esc(t.why)}</td><td class="rs">${esc(t.reason)}</td></tr>`
      : `<tr><td>${t.date}</td><td><span class="side ${t.side}">${t.side}</span></td><td><b>${t.sym}</b></td><td>${num(t.qty)}</td><td>${num(t.price, 2)}</td><td>${rs(t.amount)}</td><td>${rs(t.fees)}</td><td>${t.cgt ? rs(t.cgt) : "—"}</td><td class="${cls(t.pnl)}"><b>${t.pnl == null ? "—" : rs(t.pnl)}</b></td><td class="st up">✓ filled</td><td class="rs">${esc(t.reason)}</td></tr>`).join("") + "</tbody>";
}

function fees(S) {
  const f = S.fees, items = [
    ["commission", "Broker commission", "0.24–0.36% of every buy and sell, set by SEBON", css("--blue")],
    ["slippage", "Slippage", "Paying slightly worse than the day's average price (simulated 0.25%)", css("--violet")],
    ["cgt", "Capital gains tax", "7.5% of profit on shares held under a year (5% if longer)", css("--red")],
    ["dp", "DP charge", "Rs 25 per stock sold, paid to the depository participant", css("--amber")],
    ["sebon", "SEBON fee", "0.015% regulator fee on every transaction", css("--green")],
  ];
  const tot = items.reduce((a, [k]) => a + (f[k] || 0), 0);
  charts.fee?.destroy();
  charts.fee = new Chart($("#feeChart"), {
    type: "doughnut",
    data: { labels: items.map(i => i[1]), datasets: [{ data: items.map(([k]) => f[k] || 0), backgroundColor: items.map(i => i[3]), borderColor: css("--card"), borderWidth: 3 }] },
    options: { responsive: true, maintainAspectRatio: false, cutout: "68%", plugins: { legend: { position: "bottom", labels: { color: css("--ink2"), boxWidth: 10, usePointStyle: true } }, tooltip: { callbacks: { label: c => `${c.label}: ${rs(c.parsed)}` } } } },
  });
  $("#feeList").innerHTML = items.map(([k, n, d, c]) => `<div class="fee"><div><span style="color:${c}">●</span> ${n}<small>${d}</small></div><b>${rs(f[k] || 0)}</b></div>`).join("") +
    `<div class="fee"><div><b>Total cost of trading</b><small>${tot ? ((tot / S.start_capital) * 100).toFixed(2) + "% of starting capital" : "Nothing spent yet"}</small></div><b>${rs(tot)}</b></div>`;
}

/* ---- events */
$$(".mode button").forEach(b => b.onclick = () => { mode = b.dataset.mode; render(); });
$("#rangeSeg").onclick = e => { const b = e.target.closest("button"); if (!b) return; range = +b.dataset.r; $$("#rangeSeg button").forEach(x => x.classList.toggle("on", x === b)); equityChart(DATA[mode]); };
$("#tradeSeg").onclick = e => { const b = e.target.closest("button"); if (!b) return; tradeFilter = b.dataset.f; $$("#tradeSeg button").forEach(x => x.classList.toggle("on", x === b)); trades(DATA[mode]); };
$("#filters").onclick = e => { const b = e.target.closest("button"); if (!b) return; logFilter = b.dataset.f; terminal(DATA[mode], true); };
$("#daySel").onchange = () => terminal(DATA[mode], true);
$("#theme").onclick = () => {
  const r = document.documentElement, dark = r.dataset.theme ? r.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  r.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("theme", r.dataset.theme); } catch (e) { }
  render();
};
try { const t = localStorage.getItem("theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) { }
load().catch(e => { $("#modeBanner").className = "banner"; $("#modeBanner").textContent = "Could not load bot data: " + e; });
