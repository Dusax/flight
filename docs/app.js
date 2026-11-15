"use strict";
/* ---------- utilidades ---------- */
const $ = (s, r = document) => r.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch {} },
};
const CABINS = { economy: "Econômica", premium: "Premium", business: "Executiva", first: "Primeira" };
const EVERY = { 1: "a cada 1 h", 3: "a cada 3 h", 6: "a cada 6 h", 12: "a cada 12 h", 24: "todo dia", 48: "a cada 2 dias", 168: "toda semana" };
const WORKFLOW = "scrape.yml";

const money = (v, cur) => v == null ? "–" : new Intl.NumberFormat("pt-BR", { style: "currency", currency: cur || "BRL", maximumFractionDigits: 0 }).format(v);
const isoToDate = (iso) => { const [y, m, d] = iso.slice(0, 10).split("-").map(Number); return new Date(y, m - 1, d); };
const fmtDay = (iso) => isoToDate(iso).toLocaleDateString("pt-BR", { day: "numeric", month: "short", year: "numeric" }).replace(/\./g, "");
const fmtDayShort = (iso) => isoToDate(iso).toLocaleDateString("pt-BR", { day: "numeric", month: "short" }).replace(/\./g, "");
const fmtStamp = (ts) => new Date(ts).toLocaleString("pt-BR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).replace(/\./g, "");
const hm = (m) => m == null ? "–" : `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}`;
const stopsTxt = (n) => n === 0 ? "Direto" : n === 1 ? "1 escala" : `${n} escalas`;
const ago = (ts) => {
  const s = Math.max(0, (Date.now() - new Date(ts)) / 1000);
  if (s < 90) return "agora há pouco";
  if (s < 3600) return `há ${Math.round(s / 60)} min`;
  if (s < 86400) return `há ${Math.round(s / 3600)} h`;
  return `há ${Math.round(s / 86400)} d`;
};
const todayIso = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
function toast(html, ms = 6000) {
  const el = document.createElement("div"); el.className = "toast"; el.innerHTML = html;
  $("#toasts").appendChild(el); setTimeout(() => el.remove(), ms);
}

/* ---------- estado e API do servidor ---------- */
const state = {
  authed: false,          // há sessão válida?
  me: null,               // { codeRepo, dataRepo } informado pelo servidor
  config: null,           // servidor mal configurado: { missing, problems }
  loadError: null,        // erro ao ler os dados (ex.: o token do servidor sem acesso ao repo de dados)
  dashboard: null,        // dashboard.json (resultados)
  watches: [],            // lista editável (watches.json)
  sha: null,              // sha atual do watches.json no repo de dados (null = ainda não existe)
  watchesLoaded: false,   // já li a lista de buscas?
  sel: {},                // combinação de datas selecionada por busca
  open: new Set(),        // cards com a tabela de voos aberta
  sort: {},               // ordenação da tabela de voos por busca
  sortSummary: "price",
  run: null,              // última execução do workflow
  polling: null,
  editingId: null,
};
const canWrite = () => state.authed;

// Todas as chamadas vão para o próprio servidor (/api/...). O token do GitHub vive lá;
// o navegador só tem um cookie de sessão HttpOnly (o JavaScript nem consegue lê-lo).
async function api(path, opts = {}) {
  let r;
  try {
    r = await fetch(path, { credentials: "same-origin", ...opts, headers: { ...(opts.body ? { "Content-Type": "application/json" } : {}), ...opts.headers } });
  } catch { throw Object.assign(new Error("Sem conexão com o servidor."), { status: 0 }); }
  const text = await r.text(); let j = null; try { j = text ? JSON.parse(text) : null; } catch {}
  if (r.status === 401 && path !== "/api/login") { onLoggedOut(); throw Object.assign(new Error(j?.error || "Faça login."), { status: 401 }); }
  if (!r.ok) throw Object.assign(new Error(j?.error || `Erro ${r.status}`), { status: r.status, data: j });
  return j;
}
function onLoggedOut() {
  const was = state.authed;
  state.authed = false; state.me = null; state.watchesLoaded = false; state.sha = null; stopPolling();
  if (was) { toast("Sua sessão expirou. Entre de novo."); render(); openLogin(); }
}

const fetchDashboard = () => api("/api/dashboard");
const fetchWatchesFile = () => api("/api/watches");
async function saveWatches(list, message) {
  const j = await api("/api/watches", { method: "PUT", body: JSON.stringify({ list, sha: state.sha, message }) });
  state.sha = j.sha;
  state.watches = j.list; // já normalizada pelo servidor
}
async function dispatch(ids) {
  await api("/api/dispatch", { method: "POST", body: JSON.stringify({ ids }) });
  toast(`Coleta disparada. Leva de 1 a 5 minutos; a página atualiza sozinha. <a target="_blank" rel="noopener noreferrer" href="https://github.com/${esc(state.me?.codeRepo || "")}/actions/workflows/${WORKFLOW}">Ver execução</a>`, 9000);
  startPolling();
}
async function loadRun() {
  try { state.run = (await api("/api/run-status")).run; } catch { state.run = null; }
}
function startPolling() {
  stopPolling();
  const before = state.dashboard?.generated_at; const t0 = Date.now();
  state.polling = setInterval(async () => {
    await loadRun();
    try {
      const d = await fetchDashboard();
      if (d.generated_at !== before) { state.dashboard = d; stopPolling(); toast("Dados atualizados."); }
    } catch {}
    render();
    if (Date.now() - t0 > 12 * 60 * 1000) stopPolling();
  }, 15000);
}
function stopPolling() { clearInterval(state.polling); state.polling = null; }

/* ---------- dados derivados ---------- */
function merged() {
  const byId = new Map((state.dashboard?.watches || []).map((w) => [w.id, w]));
  const base = canWrite() && state.watchesLoaded ? state.watches : (state.dashboard?.watches || []);
  return base.map((w) => {
    const d = byId.get(w.id) || {};
    return { ...d, ...w, combos: d.combos || [], pending: !byId.has(w.id) };
  });
}
function bestCombo(w) { return w.combos.reduce((b, c) => (!b || c.min_price < b.min_price ? c : b), null); }
function comboIndex(w) {
  const i = state.sel[w.id];
  if (i != null && w.combos[i]) return i;
  return Math.max(0, w.combos.indexOf(bestCombo(w)));
}
function delta(cur, prev, currency) {
  if (prev == null || cur == null) return "";
  const d = cur - prev;
  if (Math.abs(d) < 0.5) return `<span class="flat">= igual à coleta anterior</span>`;
  const cls = d < 0 ? "down" : "up";
  return `<span class="${cls}">${d < 0 ? "▼" : "▲"} ${money(Math.abs(d), currency)}</span> <span>vs. coleta anterior</span>`;
}
function levelBadge(l) {
  if (!l) return `<span class="badge">–</span>`;
  const map = { low: ["▼", "Preço baixo", "low"], typical: ["●", "Preço normal", ""], high: ["▲", "Preço alto", "high"] };
  const [ico, txt, cls] = map[l] || ["●", l, ""];
  return `<span class="badge ${cls}"><span class="dot" aria-hidden="true">${ico}</span>${txt}</span>`;
}

/* ---------- rótulos e traduções ---------- */
const isMulti = (w) => !!(w.legs && w.legs.length);
const routeCodes = (w) => (isMulti(w) ? [w.legs[0].origin, ...w.legs.map((l) => l.destination)] : [w.origin, w.destination]);
const routeText = (w) => routeCodes(w).join(" → ");
const routeHtml = (w) => routeCodes(w).map(esc).join('<span class="arrow">→</span>');
const dateSpan = (c) => { const end = c.end_date || c.return_date; return `${fmtDayShort(c.depart_date)}${end && end !== c.depart_date ? ` → ${fmtDayShort(end)}` : ""}`; };
const PT_AMEN = { "Wi-Fi for a fee": "Wi-Fi pago", "Free Wi-Fi": "Wi-Fi grátis", "In-seat USB outlet": "tomada USB",
  "In-seat power & USB outlets": "tomada e USB", "In-seat power outlet": "tomada", "On-demand video": "vídeo sob demanda",
  "Stream media to your device": "streaming no seu aparelho" };
const PT_LEG = { "Above average": "acima da média", "Average": "média", "Below average": "abaixo da média" };
const VERDICT = { low: "baixo", typical: "normal", high: "alto" };
const ptBag = (s) => s.replace(/^(\d+) free carry-on$/, "$1 bagagem de mão grátis")
  .replace(/^(\d+) carry-on bag available for a fee$/, "$1 bagagem de mão paga").replace(/^(\d+) carry-on bag: (.+)$/, "$1 bagagem de mão: $2")
  .replace(/^1st checked bag available for a fee$/, "1ª despachada paga").replace(/^1st checked bag: (.+)$/, "1ª despachada: $1")
  .replace(/^1st checked bag is free$/i, "1ª despachada grátis");
const googleUrl = (u) => (typeof u === "string" && /^https:\/\/www\.google\.com\//.test(u) ? u : null); // só links do Google
// compatível com dashboard.json antigo (sem `legs` nas combinações)
const firstLeg = (w, c) => (c.legs && c.legs[0]) || { origin: w.origin, destination: w.destination };
const typicalOf = (c) => c.offers?.find((o) => o.booking && o.booking.typical_low != null)?.booking || null;

/* ---------- renderização ---------- */
function render() {
  const ws = merged();
  const d = state.dashboard;
  $("#updated").textContent = d?.generated_at ? `Dados de ${fmtStamp(d.generated_at)} (${ago(d.generated_at)})` : "Sem coletas ainda";
  $("#btn-all").disabled = !canWrite() || !ws.length;
  $("#btn-new").disabled = !canWrite();
  $("#btn-auth").textContent = state.authed ? "Sair" : "Entrar";

  // avisos
  const b = [];
  if (state.config) {
    const miss = [...(state.config.missing || []).map((m) => `falta <code>${esc(m)}</code>`), ...(state.config.problems || []).map(esc)].join("; ");
    b.push(`<div class="banner err"><span class="ico">✗</span><div><b>Servidor não configurado.</b> ${miss}. Defina as variáveis de ambiente na Vercel e faça um novo deploy (veja o README).</div></div>`);
  } else if (!canWrite()) b.push(`<div class="banner"><span class="ico">🔒</span><div><b>Você não está logado.</b> <a href="#" data-open="login">Entrar</a> para ver os preços e cadastrar buscas.</div></div>`);
  if (state.loadError) b.push(`<div class="banner err"><span class="ico">✗</span><div><b>Não consegui ler os dados.</b> ${esc(state.loadError)}</div></div>`);
  const run = state.run;
  if (run) {
    const live = run.status !== "completed";
    const ok = run.conclusion === "success";
    const txt = live ? "Coleta em andamento…" : ok ? `Última coleta concluída ${ago(run.updated_at)}` : `Última coleta falhou (${esc(run.conclusion)}) ${ago(run.updated_at)}`;
    if (live || !ok) b.push(`<div class="banner ${!live && !ok ? "err" : ""}"><span class="ico">${live ? "…" : "✗"}</span><div>${txt} · <a target="_blank" rel="noopener" href="${esc(run.html_url)}">ver detalhes</a>${!live && !ok ? "<br><span class='muted small'>Pode ser bloqueio do Google (captcha) ou mudança no layout. O log mostra o motivo.</span>" : ""}</div></div>`);
  }
  $("#banners").innerHTML = b.join("");

  if (!ws.length) {
    $("#summary").innerHTML = "";
    $("#cards").innerHTML = `<div class="panel empty"><h2>Nenhuma busca cadastrada</h2><p class="muted">${canWrite() ? "Clique em “+ Nova busca” para começar." : "Entre para ver e cadastrar as suas buscas."}</p></div>`;
    return;
  }
  renderSummary(ws);
  const openIds = new Set([...document.querySelectorAll("details.offers[open]")].map((x) => x.dataset.id));
  $("#cards").innerHTML = ws.map(cardHtml).join("");
  document.querySelectorAll("details.offers").forEach((el) => { if (openIds.has(el.dataset.id) || state.open.has(+el.dataset.id)) el.open = true; });
  ws.forEach((w) => drawWatchChart(w));
}

function renderSummary(ws) {
  const rows = ws.map((w) => {
    const c = w.combos.length ? w.combos[comboIndex(w)] : null;
    const best = bestCombo(w);
    return { w, c: best, price: best?.min_price ?? Infinity, nonstop: best?.min_nonstop ?? Infinity, fast: best?.fastest_min ?? Infinity };
  });
  const key = state.sortSummary;
  rows.sort((a, b) => a[key] - b[key]);
  const cheapest = rows.find((r) => r.c)?.w.id;
  const th = (k, t, cls = "") => `<th class="${cls}" aria-sort="${state.sortSummary === k ? "ascending" : "none"}"><button data-sortsum="${k}">${t}${state.sortSummary === k ? " ↑" : ""}</button></th>`;
  $("#summary").innerHTML = `<div class="panel summary"><table>
    <caption class="sr">Resumo das buscas ordenado por ${key}</caption>
    <thead><tr><th>Busca</th><th>Datas</th>${th("price", "Mais barata", "r")}${th("nonstop", "Direto", "r")}${th("fast", "Mais rápida", "r")}<th>Google</th></tr></thead>
    <tbody>${rows.map(({ w, c }) => `<tr data-go="${w.id}" class="${w.id === cheapest ? "best" : ""}" tabindex="0">
      <td><b>${esc(routeText(w))}</b>${w.label ? ` <span class="muted">${esc(w.label)}</span>` : ""}</td>
      <td>${c ? dateSpan(c) : (w.pending ? "<span class='muted'>aguardando 1ª coleta</span>" : "<span class='muted'>sem resultado</span>")}</td>
      <td class="r num"><b>${c ? money(c.min_price, w.currency) : "–"}</b></td>
      <td class="r num">${c ? money(c.min_nonstop, w.currency) : "–"}</td>
      <td class="r num">${c ? hm(c.fastest_min) : "–"}</td>
      <td>${c ? levelBadge(c.price_level) : ""}</td></tr>`).join("")}</tbody></table></div>`;
}

function metaLine(w) {
  const parts = [];
  if (isMulti(w)) parts.push(`${w.legs.length} trechos: ` + w.legs.map((l) => `${l.origin}→${l.destination} ${fmtDayShort(l.date)}`).join(" · "));
  else { parts.push(`ida ${fmtDay(w.depart_date)}`); parts.push(w.return_date ? `volta ${fmtDay(w.return_date)}` : "só ida"); }
  parts.push(`${w.adults} adulto${w.adults > 1 ? "s" : ""}`, (CABINS[w.cabin] || w.cabin).toLowerCase());
  if (w.max_stops != null) parts.push(w.max_stops === 0 ? "só direto" : `até ${w.max_stops} escala${w.max_stops > 1 ? "s" : ""}`);
  if (w.flex_days) parts.push(`± ${w.flex_days} dia${w.flex_days > 1 ? "s" : ""}`);
  return parts.join(" · ");
}

function cardHtml(w) {
  const rw = canWrite();
  const best = bestCombo(w);
  const idx = comboIndex(w);
  const c = w.combos[idx];
  const chips = [];
  chips.push(`<span class="chip">${isMulti(w) ? "Multi-destino" : w.return_date ? "Ida e volta" : "Só ida"}</span>`);
  if (w.compare_separate) chips.push(`<span class="chip">Compara trechos separados</span>`);
  chips.push(`<span class="chip">${w.every_hours ? `🕒 ${EVERY[w.every_hours] || `a cada ${w.every_hours} h`}` : "Só manual"}</span>`);
  if (!w.active) chips.push(`<span class="chip">⏸ Pausada</span>`);
  if (w.expired) chips.push(`<span class="chip">Viagem já passou</span>`);
  if (w.next_run_at && w.active && !w.expired) chips.push(`<span class="chip">Próxima: ${new Date(w.next_run_at) <= new Date() ? "na próxima rodada" : fmtStamp(w.next_run_at)}</span>`);
  if (w.last_run_at) chips.push(`<span class="chip">Última coleta ${ago(w.last_run_at)}</span>`);
  const warn = w.last_status && w.last_status !== "ok" && w.last_status !== "empty"
    ? `<div class="banner err" style="margin:12px 0 0"><span class="ico">✗</span><div>A última tentativa ${w.last_status === "blocked" ? "foi bloqueada pelo Google" : "falhou"}. <span class="muted small">${esc(w.last_error || "")}</span></div></div>` : "";

  let body;
  if (!c) {
    body = `<p class="muted" style="margin:16px 0 4px">${w.pending ? "Aguardando a primeira coleta. Clique em “Buscar agora”." : w.last_status === "empty" ? "O Google não retornou voos para essas datas." : "Ainda sem resultados."}</p>`;
  } else {
    const per = w.adults > 1 ? `<div class="d">${money(c.min_price / w.adults, w.currency)} por adulto</div>` : "";
    const sinceFirst = c.first_at && c.first_at !== c.scraped_at && c.first_price != null ? `<div class="d">${delta(c.min_price, c.first_price, w.currency).replace("vs. coleta anterior", "vs. 1ª coleta")}</div>` : "";
    body = `
    <div class="metrics">
      <div class="metric"><div class="k">Mais barata</div><div class="v num">${money(c.min_price, w.currency)}</div><div class="d">${delta(c.min_price, c.prev_price, w.currency) || (w.adults > 1 ? "" : "&nbsp;")}</div>${per}${sinceFirst}</div>
      <div class="metric"><div class="k">Mais barata direta</div><div class="v num">${money(c.min_nonstop, w.currency)}</div><div class="d">${c.min_nonstop == null ? "Sem voo direto" : c.min_nonstop === c.min_price ? "É a mais barata" : `+${money(c.min_nonstop - c.min_price, w.currency)} sobre a mais barata`}</div></div>
      <div class="metric"><div class="k">Viagem mais rápida</div><div class="v num">${hm(c.fastest_min)}</div><div class="d">${(() => { const f = c.offers.find((o) => o.duration_min === c.fastest_min); return f ? `${esc(f.airline)} · ${money(f.price, w.currency)}` : ""; })()}</div></div>
      <div class="metric"><div class="k">Avaliação do Google</div><div class="v" style="font-size:18px;padding-top:6px">${levelBadge(c.price_level)}</div><div class="d">${c.offers_count} voos encontrados${(() => { const t = typicalOf(c); return t ? `<br>Faixa típica ${money(t.typical_low, w.currency)}–${money(t.typical_high, w.currency)}` : ""; })()}</div></div>
    </div>
    ${w.combos.length > 1 ? `<div class="chart-title">Datas comparadas (${w.combos.length}) — toque para ver o histórico e os voos de cada uma</div>
      <div class="combos">${w.combos.map((x, i) => `<button class="combo ${x === best ? "cheapest" : ""}" data-combo="${w.id}:${i}" aria-pressed="${i === idx}"><b class="num">${money(x.min_price, w.currency)}</b>${dateSpan(x)}</button>`).join("")}</div>` : ""}
    <div class="chart-title">Menor preço ao longo do tempo · ${dateSpan(c)} · total para ${w.adults} adulto${w.adults > 1 ? "s" : ""}${isMulti(w) ? ", viagem completa" : c.return_date ? ", ida e volta" : ""}</div>
    <div class="chart-box" id="chart-${w.id}"></div>
    <details class="sec"><summary>Histórico em tabela</summary><div class="tablewrap" id="hist-${w.id}"></div></details>
    <details class="sec offers" data-id="${w.id}"><summary>${isMulti(w) || c.return_date ? "Voos do 1º trecho" : "Voos encontrados"} (${c.offers.length}${c.offers_count > c.offers.length ? ` mais baratos de ${c.offers_count}` : ""})</summary>
      ${isMulti(w) || c.return_date ? `<p class="muted small" style="margin:4px 0">Lista os voos de ${esc(firstLeg(w, c).origin)} → ${esc(firstLeg(w, c).destination)}. O preço é o total da viagem inteira com esse voo (o Google só mostra os demais trechos depois de escolher).</p>` : ""}
      ${offersTable(w, c.offers, { id: w.id, sortable: true })}</details>
    ${separateHtml(w, c)}`;
  }
  return `<article class="panel card ${w.active ? "" : "paused"}" id="card-${w.id}">
    <div class="card-head"><div>
      <div class="route">${routeHtml(w)}${w.label ? `<span class="label">${esc(w.label)}</span>` : ""}</div>
      <div class="meta">${esc(metaLine(w))}</div>
      <div class="chips">${chips.join("")}</div></div></div>
    ${warn}${body}
    <div class="card-foot"><span class="muted small">${c ? `Resultado de ${fmtStamp(c.scraped_at)}` : ""}</span>
      <div class="btns">
        <button class="btn sm primary" data-act="run" data-id="${w.id}" ${rw ? "" : "disabled"}>Buscar agora</button>
        <button class="btn sm" data-act="edit" data-id="${w.id}" ${rw ? "" : "disabled"}>Editar</button>
        <button class="btn sm" data-act="toggle" data-id="${w.id}" ${rw ? "" : "disabled"}>${w.active ? "Pausar" : "Retomar"}</button>
        <button class="btn sm danger" data-act="remove" data-id="${w.id}" ${rw ? "" : "disabled"}>Remover</button>
      </div></div>
  </article>`;
}

function offerDetail(w, o) {
  const tags = [];
  if (o.cabin) tags.push(esc(o.cabin));
  if (o.aircraft) tags.push(esc(o.aircraft));
  if (o.co2_kg != null) tags.push(`${o.co2_kg} kg CO₂${o.co2_diff_pct ? ` (${o.co2_diff_pct > 0 ? "+" : ""}${o.co2_diff_pct}%)` : ""}`);
  if (o.overnight) tags.push("pernoite");
  if (o.often_delayed) tags.push("⚠ costuma atrasar 30+ min");
  const b = o.booking;
  if (b && b.bags && b.bags.length) tags.push(b.bags.map((x) => esc(ptBag(x))).join(" · "));
  if (!tags.length && !(o.segments && o.segments.length) && !b) return "";
  const pt = (p) => (p ? `${esc(p.code)} ${esc(p.time)}${p.plus_days ? ` (+${p.plus_days})` : ""}` : "");
  const segs = (o.segments || []).map((s) => {
    const bits = [`<b>${esc(s.flight || "")}</b>`, s.aircraft && esc(s.aircraft), s.cabin && esc(s.cabin), `${pt(s.dep)} → ${pt(s.arr)}`,
      s.duration_min != null && hm(s.duration_min), s.overnight && "pernoite"].filter(Boolean);
    const extra = [];
    if (s.legroom_in) extra.push(`espaço entre assentos: ${esc(PT_LEG[s.legroom] || s.legroom || "")} (${s.legroom_in} pol)`);
    if (s.amenities && s.amenities.length) extra.push(s.amenities.map((a) => esc(PT_AMEN[a] || a)).join(", "));
    if (s.plane_and_crew) extra.push(`avião e tripulação: ${esc(s.plane_and_crew)}`);
    if (s.delay_note) extra.push("⚠ costuma atrasar 30+ min");
    if (s.co2_kg != null) extra.push(`${s.co2_kg} kg CO₂`);
    const lay = s.layover_after ? `<div class="lay">Conexão de ${hm(s.layover_after.duration_min)} em ${esc(s.layover_after.city)} (${esc(s.layover_after.code)})${s.layover_after.note ? ` · ${esc(s.layover_after.note)}` : ""}</div>` : "";
    return `<div class="sg">${bits.join(" · ")}<div class="muted small">${extra.join(" · ")}</div></div>${lay}`;
  }).join("");
  let bk = "";
  if (b) {
    const sellers = (b.sellers || []).map((s) => `${esc(s.seller)}${s.type ? ` (${s.type === "Airline" ? "companhia" : esc(s.type)})` : ""}: <b>${money(s.price, w.currency)}</b>`).join(" · ");
    const typ = b.typical_low != null ? `Faixa típica desta rota: ${money(b.typical_low, w.currency)}–${money(b.typical_high, w.currency)}${b.verdict ? ` · este voo está <b>${VERDICT[b.verdict] || esc(b.verdict)}</b>` : ""}` : "";
    const link = googleUrl(b.url) ? `<a href="${esc(b.url)}" target="_blank" rel="noopener noreferrer">Abrir este voo no Google Voos</a>` : "";
    bk = `<div class="bk"><b>Compra</b> · ${b.bags && b.bags.length ? b.bags.map((x) => esc(ptBag(x))).join(" · ") : "bagagem: sem informação"}${sellers ? `<br>${sellers}` : ""}${typ ? `<br>${typ}` : ""}${b.bag_policy ? `<br><span class="muted">${esc(b.bag_policy)}</span>` : ""}${link ? `<br>${link}` : ""}</div>`;
  }
  return `<tr class="dtl"><td colspan="8"><details><summary>Detalhes · ${tags.join(" · ")}</summary><div class="dbody">${segs}${bk}</div></details></td></tr>`;
}

function offersTable(w, offers, { id, sortable }) {
  const s = (sortable && state.sort[id]) || { key: "price", dir: 1 };
  const rows = [...offers].sort((a, b) => ((a[s.key] ?? Infinity) - (b[s.key] ?? Infinity)) * s.dir || a.price - b.price);
  const th = (k, t, cls = "") => (sortable
    ? `<th class="${cls}" aria-sort="${s.key === k ? (s.dir > 0 ? "ascending" : "descending") : "none"}"><button data-sortoff="${id}:${k}">${t}${s.key === k ? (s.dir > 0 ? " ↑" : " ↓") : ""}</button></th>`
    : `<th class="${cls}">${t}</th>`);
  const dayDiff = (a, b) => (b && a ? Math.round((isoToDate(b) - isoToDate(a)) / 864e5) : 0);
  const dt = (iso) => `${fmtDayShort(iso)} ${iso.slice(11, 16)}`;
  return `<div class="tablewrap"><table><thead><tr>${th("price", "Preço", "r")}<th>Companhia</th><th>Voo</th>${th("dep_time", "Saída")}<th>Chegada</th>${th("duration_min", "Duração", "r")}${th("stops", "Escalas")}<th>Conexões</th></tr></thead><tbody>
    ${rows.map((o) => { const dd = dayDiff(o.dep_time, o.arr_time); return `<tr>
      <td class="r num"><b>${money(o.price, w.currency)}</b></td>
      <td>${esc(o.airline)}${o.operated_by && o.operated_by !== o.airline ? `<div class="muted small">op. ${esc(o.operated_by)}</div>` : ""}</td>
      <td class="num">${esc(o.flight_numbers || "–")}</td>
      <td class="num">${o.dep_time ? dt(o.dep_time) : "–"}</td>
      <td class="num">${o.arr_time ? dt(o.arr_time) : "–"}${dd > 0 ? ` <span class="next-day">+${dd}</span>` : ""}</td>
      <td class="r num">${hm(o.duration_min)}</td><td>${stopsTxt(o.stops)}</td><td>${esc((o.layover_airports || "").replace(/,/g, " · ") || "–")}</td></tr>${offerDetail(w, o)}`; }).join("")}
    </tbody></table></div>`;
}

function separateHtml(w, c) {
  const sp = c.separate; if (!sp) return "";
  const rows = sp.legs.map((l, i) => `<tr><td>${i + 1}. <b>${esc(l.origin)} → ${esc(l.destination)}</b></td><td>${fmtDayShort(l.date)}</td>
    <td class="r num"><b>${money(l.min_price, w.currency)}</b></td><td class="r num">${money(l.min_nonstop, w.currency)}</td><td class="r num">${hm(l.fastest_min)}</td></tr>`).join("");
  let verdict;
  if (sp.total == null) verdict = "Ainda faltam trechos coletados para comparar.";
  else if (sp.vs_trip < -0.5) verdict = `Comprar separado sai <b class="down">${money(-sp.vs_trip, w.currency)} mais barato</b> que o pacote.`;
  else if (sp.vs_trip > 0.5) verdict = `O pacote sai <b class="down">${money(sp.vs_trip, w.currency)} mais barato</b> que comprar separado.`;
  else verdict = "Pacote e separado custam o mesmo.";
  const legsDetail = sp.legs.map((l, i) => `<div class="chart-title" style="margin-top:10px">Trecho ${i + 1}: ${esc(l.origin)} → ${esc(l.destination)} · ${fmtDay(l.date)} (só ida${l.price_level ? ` · Google: preço ${VERDICT[l.price_level] || esc(l.price_level)}` : ""})</div>
    ${l.offers.length ? offersTable(w, l.offers, { id: `${w.id}-${i}`, sortable: false }) : '<p class="muted small">Sem coleta deste trecho ainda.</p>'}`).join("");
  return `<div class="sepbox"><div class="chart-title" style="margin:0">Pacote vs. trechos comprados separado (só ida)</div>
    <div class="tablewrap" style="margin-top:8px"><table><thead><tr><th>Trecho</th><th>Data</th><th class="r">Mais barato</th><th class="r">Direto</th><th class="r">Mais rápido</th></tr></thead><tbody>${rows}</tbody>
    <tfoot><tr><td colspan="2">Soma dos trechos separados</td><td class="r num">${money(sp.total, w.currency)}</td><td class="r num">${money(sp.total_nonstop, w.currency)}</td><td></td></tr>
    <tr><td colspan="2">Pacote (${isMulti(w) ? "multi-destino" : "ida e volta"})</td><td class="r num">${money(c.min_price, w.currency)}</td><td></td><td></td></tr></tfoot></table></div>
    <div class="verdict">${verdict}</div>
    <div class="muted small">Passagens separadas não têm proteção se um voo atrasar e você perder o seguinte, e a bagagem precisa ser despachada de novo em cada conexão.</div>
    <details class="sec"><summary>Voos de cada trecho (só ida)</summary>${legsDetail}</details></div>`;
}

/* ---------- gráfico de linha (SVG) ---------- */
function drawWatchChart(w) {
  const box = document.getElementById(`chart-${w.id}`); if (!box) return;
  const c = w.combos[comboIndex(w)]; if (!c) return;
  const pts = c.history.map(([t, p]) => ({ t: new Date(t).getTime(), p, ts: t })).sort((a, b) => a.t - b.t);
  const hist = document.getElementById(`hist-${w.id}`);
  if (hist) hist.innerHTML = `<table><thead><tr><th>Coleta</th><th class="r">Menor preço</th><th class="r">Variação</th></tr></thead><tbody>${[...pts].reverse().slice(0, 30).map((q, i, arr) => {
    const prev = arr[i + 1]; const d = prev ? q.p - prev.p : null;
    return `<tr><td>${fmtStamp(q.ts)}</td><td class="r num">${money(q.p, w.currency)}</td><td class="r num">${d == null ? "–" : d === 0 ? "=" : `${d < 0 ? "▼" : "▲"} ${money(Math.abs(d), w.currency)}`}</td></tr>`; }).join("")}</tbody></table>`;
  if (pts.length < 2) { box.innerHTML = `<div class="empty-chart">O gráfico aparece a partir da 2ª coleta. Menor preço até agora: <b>${money(pts[0].p, w.currency)}</b>.</div>`; return; }

  const W = Math.max(320, box.clientWidth || 640), H = 190;
  const m = { l: 56, r: 64, t: 12, b: 26 };
  const lo = Math.min(...pts.map((q) => q.p)), hi = Math.max(...pts.map((q) => q.p));
  const pad = (hi - lo) * 0.15 || hi * 0.05 || 1;
  const y0 = Math.max(0, lo - pad), y1 = hi + pad;
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t;
  const X = (t) => m.l + ((t - t0) / (t1 - t0 || 1)) * (W - m.l - m.r);
  const Y = (p) => m.t + (1 - (p - y0) / (y1 - y0)) * (H - m.t - m.b);
  const ticks = [0, 1, 2, 3].map((i) => y0 + ((y1 - y0) * i) / 3);
  const path = pts.map((q, i) => `${i ? "L" : "M"}${X(q.t).toFixed(1)},${Y(q.p).toFixed(1)}`).join("");
  const last = pts[pts.length - 1];
  const dayLbl = (t) => new Date(t).toLocaleDateString("pt-BR", { day: "numeric", month: "short" }).replace(/\./g, "");
  box.innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Menor preço por coleta, de ${money(pts[0].p, w.currency)} a ${money(last.p, w.currency)}" tabindex="0">
    ${ticks.map((v) => `<line class="grid" x1="${m.l}" x2="${W - m.r}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"/><text x="${m.l - 8}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${money(v, w.currency)}</text>`).join("")}
    <line class="axis" x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}"/>
    <text x="${m.l}" y="${H - 8}" text-anchor="start">${dayLbl(t0)}</text><text x="${W - m.r}" y="${H - 8}" text-anchor="end">${dayLbl(t1)}</text>
    <path class="line" d="${path}"/>
    <circle class="pt" cx="${X(last.t).toFixed(1)}" cy="${Y(last.p).toFixed(1)}" r="5"/>
    <text class="end" x="${(X(last.t) + 10).toFixed(1)}" y="${(Y(last.p) + 4).toFixed(1)}">${money(last.p, w.currency)}</text>
    <g class="hov" style="display:none"><line class="cross" y1="${m.t}" y2="${H - m.b}"/><circle class="pt" r="5"/></g>
    <rect x="${m.l}" y="0" width="${W - m.l - m.r}" height="${H}" fill="transparent" class="hit"/>
  </svg><div class="tip" style="display:none"></div>`;
  const svg = box.querySelector("svg"), tip = box.querySelector(".tip"), hov = svg.querySelector(".hov");
  const show = (clientX) => {
    const r = svg.getBoundingClientRect(); const x = ((clientX - r.left) / r.width) * W;
    let best = 0; pts.forEach((q, i) => { if (Math.abs(X(q.t) - x) < Math.abs(X(pts[best].t) - x)) best = i; });
    const q = pts[best], prev = pts[best - 1], px = X(q.t), py = Y(q.p);
    hov.style.display = ""; hov.querySelector("line").setAttribute("x1", px); hov.querySelector("line").setAttribute("x2", px);
    hov.querySelector("circle").setAttribute("cx", px); hov.querySelector("circle").setAttribute("cy", py);
    const d = prev ? q.p - prev.p : null;
    tip.innerHTML = `<b class="num">${money(q.p, w.currency)}</b>${fmtStamp(q.ts)}${d ? `<br><span class="${d < 0 ? "down" : "up"}">${d < 0 ? "▼" : "▲"} ${money(Math.abs(d), w.currency)}</span>` : ""}`;
    tip.style.display = ""; tip.style.left = `${Math.min(Math.max((px / W) * r.width, 60), r.width - 60)}px`; tip.style.top = `${(py / H) * r.height - 12}px`;
  };
  const hide = () => { hov.style.display = "none"; tip.style.display = "none"; };
  svg.addEventListener("pointermove", (e) => show(e.clientX));
  svg.addEventListener("pointerleave", hide);
  svg.addEventListener("pointerdown", (e) => show(e.clientX));
}
let rzT; window.addEventListener("resize", () => { clearTimeout(rzT); rzT = setTimeout(() => merged().forEach(drawWatchChart), 150); });

/* ---------- ações ---------- */
async function guard(fn, okMsg) {
  try { await fn(); if (okMsg) toast(okMsg); }
  catch (e) {
    if (e.status === 401) return; // onLoggedOut já avisou e abriu o login
    let m = e.message;
    if (e.status === 409) m = "Conflito ao salvar (o arquivo mudou). Recarreguei os dados: tente de novo.";
    toast(esc(m), 9000);
    if (e.status === 409) await reloadWatches().catch(() => {});
  }
}
async function reloadWatches() {
  if (!canWrite()) return;
  const f = await fetchWatchesFile(); state.sha = f.sha; state.watches = f.list; state.watchesLoaded = true; render();
}
const nextId = () => Math.max(0, ...state.watches.map((w) => +w.id || 0)) + 1;

document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-act],[data-combo],[data-sortsum],[data-sortoff],[data-go],[data-open]"); if (!t) return;
  if (t.dataset.open === "login") { e.preventDefault(); openLogin(); return; }
  if (t.dataset.sortsum) { state.sortSummary = t.dataset.sortsum; render(); return; }
  if (t.dataset.sortoff) {
    const [id, key] = t.dataset.sortoff.split(":"); const cur = state.sort[id] || { key: "price", dir: 1 };
    state.sort[id] = { key, dir: cur.key === key ? -cur.dir : 1 }; state.open.add(+id); render(); return;
  }
  if (t.dataset.combo) { const [id, i] = t.dataset.combo.split(":").map(Number); state.sel[id] = i; render(); return; }
  if (t.dataset.go) { document.getElementById(`card-${t.dataset.go}`)?.scrollIntoView({ behavior: "smooth", block: "start" }); return; }
  const id = +t.dataset.id, act = t.dataset.act;
  if (act === "run") guard(() => dispatch([id]));
  if (act === "edit") openWatchDialog(state.watches.find((w) => w.id === id));
  if (act === "toggle") guard(async () => {
    const list = state.watches.map((w) => (w.id === id ? { ...w, active: !w.active } : w));
    await saveWatches(list, `busca ${id}: ${list.find((w) => w.id === id).active ? "retomada" : "pausada"}`); render();
  });
  if (act === "remove") {
    const w = state.watches.find((x) => x.id === id);
    if (confirm(`Remover a busca ${routeText(w)}? O histórico dela some do painel.`)) guard(async () => {
      await saveWatches(state.watches.filter((x) => x.id !== id), `busca ${id} removida`); render();
    }, "Busca removida.");
  }
});
document.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.matches?.("tr[data-go]")) e.target.click(); });
$("#btn-all").onclick = () => guard(() => dispatch([]));
$("#btn-new").onclick = () => openWatchDialog(null);
$("#btn-auth").onclick = async () => {
  if (!state.authed) return openLogin();
  try { await api("/api/logout", { method: "POST", body: "{}" }); } catch {}
  state.authed = false; state.me = null; state.watchesLoaded = false; state.sha = null; state.dashboard = null; state.watches = []; stopPolling();
  render(); toast("Você saiu.");
};

/* formulário de busca */
const dlgW = $("#dlg-watch"), formW = $("#form-watch");
const MAX_LEGS = 5;
const legRows = () => [...document.querySelectorAll("#legs .leg-row")];
function legRow(v = {}) {
  const row = document.createElement("div"); row.className = "leg-row";
  row.innerHTML = `<input class="leg-o" maxlength="3" minlength="3" pattern="[A-Za-z]{3}" placeholder="GRU" aria-label="Origem do trecho" style="text-transform:uppercase">
    <input class="leg-d" maxlength="3" minlength="3" pattern="[A-Za-z]{3}" placeholder="LIS" aria-label="Destino do trecho" style="text-transform:uppercase">
    <input class="leg-date" type="date" aria-label="Data do trecho" min="${todayIso()}">
    <button type="button" class="btn sm leg-x" aria-label="Remover trecho">✕</button>`;
  row.querySelector(".leg-o").value = v.origin || ""; row.querySelector(".leg-d").value = v.destination || ""; row.querySelector(".leg-date").value = v.date || "";
  return row;
}
function refreshLegs() {
  const rows = legRows();
  rows.forEach((r) => { r.querySelector(".leg-x").disabled = rows.length <= 2; });
  $("#leg-add").hidden = rows.length >= MAX_LEGS;
  syncTrip();
}
function setLegs(legs) {
  $("#legs").innerHTML = "";
  (legs && legs.length ? legs : [{}, {}]).forEach((l) => $("#legs").appendChild(legRow(l)));
  refreshLegs();
}
$("#leg-add").onclick = () => {
  const rows = legRows(); if (rows.length >= MAX_LEGS) return;
  const last = rows[rows.length - 1];
  const prevDest = last?.querySelector(".leg-d").value.toUpperCase() || "";
  const row = legRow({ origin: prevDest });
  if (prevDest) row.querySelector(".leg-o").dataset.auto = "1";
  $("#legs").appendChild(row); refreshLegs();
};
$("#legs").addEventListener("click", (e) => { const x = e.target.closest(".leg-x"); if (x && !x.disabled) { x.closest(".leg-row").remove(); refreshLegs(); } });
$("#legs").addEventListener("input", (e) => {
  const row = e.target.closest(".leg-row"); if (!row) return;
  if (e.target.classList.contains("leg-o")) e.target.dataset.auto = "0";
  if (e.target.classList.contains("leg-d")) { // o destino de um trecho vira a origem do próximo (se você não mexeu nela)
    const next = row.nextElementSibling?.querySelector(".leg-o");
    if (next && (!next.value || next.dataset.auto === "1")) { next.value = e.target.value; next.dataset.auto = "1"; }
  }
});

function syncTrip() {
  const kind = formW.trip.value, multi = kind === "multi", round = kind === "round";
  $("#simple-fields").hidden = multi; $("#legs-wrap").hidden = !multi; $("#return-wrap").hidden = !round;
  $("#cmp-wrap").hidden = kind === "oneway";
  for (const n of ["origin", "destination", "depart_date"]) { formW[n].disabled = multi; formW[n].required = !multi; }
  formW.return_date.disabled = !round; formW.return_date.required = round;
  legRows().forEach((r) => r.querySelectorAll("input").forEach((i) => { i.disabled = !multi; i.required = multi; }));
}
formW.querySelectorAll("[name=trip]").forEach((r) => r.addEventListener("change", syncTrip));

function openWatchDialog(w) {
  state.editingId = w ? w.id : null;
  formW.reset(); $("#watch-err").textContent = "";
  $("#dlg-watch-title").textContent = w ? `Editar busca ${routeText(w)}` : "Nova busca";
  formW.depart_date.min = todayIso(); formW.return_date.min = todayIso();
  setLegs(w && isMulti(w) ? w.legs : null);
  if (w) {
    formW.label.value = w.label || ""; formW.trip.value = isMulti(w) ? "multi" : w.return_date ? "round" : "oneway";
    if (!isMulti(w)) { formW.origin.value = w.origin; formW.destination.value = w.destination; formW.depart_date.value = w.depart_date; formW.return_date.value = w.return_date || ""; }
    formW.adults.value = w.adults; formW.cabin.value = w.cabin; formW.max_stops.value = w.max_stops ?? "";
    formW.flex_days.value = String(w.flex_days || 0); formW.every_hours.value = w.every_hours ? String(w.every_hours) : "";
    formW.compare_separate.checked = !!w.compare_separate; formW.booking_top.value = String(w.booking_top ?? 0);
  }
  syncTrip(); dlgW.showModal();
}
$("#watch-cancel").onclick = () => dlgW.close();
formW.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(formW); const err = $("#watch-err"); const kind = f.get("trip");
  const fail = (m) => { err.textContent = m; };
  const iata = (v) => /^[A-Z]{3}$/.test(v);
  const item = {
    id: state.editingId ?? nextId(), label: (f.get("label") || "").trim() || null,
    origin: "", destination: "", depart_date: "", return_date: null, legs: null,
    flex_days: +f.get("flex_days"), adults: +f.get("adults"), cabin: f.get("cabin"),
    max_stops: f.get("max_stops") === "" ? null : +f.get("max_stops"), currency: "BRL",
    every_hours: f.get("every_hours") ? +f.get("every_hours") : null,
    compare_separate: kind !== "oneway" && formW.compare_separate.checked, booking_top: +f.get("booking_top"),
    active: state.editingId != null ? (state.watches.find((w) => w.id === state.editingId)?.active ?? true) : true,
  };
  if (kind === "multi") {
    const legs = legRows().map((r) => ({ origin: r.querySelector(".leg-o").value.toUpperCase().trim(), destination: r.querySelector(".leg-d").value.toUpperCase().trim(), date: r.querySelector(".leg-date").value }));
    if (legs.length < 2) return fail("Multi-destino precisa de pelo menos 2 trechos.");
    for (const [i, l] of legs.entries()) {
      if (!iata(l.origin) || !iata(l.destination)) return fail(`Trecho ${i + 1}: use o código IATA de 3 letras (ex.: GRU, LIS).`);
      if (l.origin === l.destination) return fail(`Trecho ${i + 1}: origem e destino não podem ser iguais.`);
      if (i && l.date < legs[i - 1].date) return fail(`Trecho ${i + 1}: a data não pode ser antes do trecho anterior.`);
    }
    Object.assign(item, { legs, origin: legs[0].origin, destination: legs[0].destination, depart_date: legs[0].date });
  } else {
    Object.assign(item, { origin: (f.get("origin") || "").toUpperCase(), destination: (f.get("destination") || "").toUpperCase(),
      depart_date: f.get("depart_date"), return_date: kind === "round" ? f.get("return_date") : null });
    if (!iata(item.origin) || !iata(item.destination)) return fail("Use o código IATA de 3 letras (ex.: GRU, LIS).");
    if (item.origin === item.destination) return fail("Origem e destino não podem ser iguais.");
    if (item.return_date && item.return_date < item.depart_date) return fail("A volta não pode ser antes da ida.");
  }
  err.textContent = "";
  const runNow = f.get("run_now") === "on";
  $("#watch-save").disabled = true;
  await guard(async () => {
    const list = state.editingId != null ? state.watches.map((w) => (w.id === item.id ? item : w)) : [...state.watches, item];
    await saveWatches(list, `busca ${item.id}: ${routeText(item)} ${state.editingId != null ? "editada" : "criada"}`);
    dlgW.close(); render();
    toast("Busca salva.");
    if (runNow) await dispatch([item.id]);
  });
  $("#watch-save").disabled = false;
});

/* login */
const dlgL = $("#dlg-login"), formL = $("#form-login");
function openLogin(msg) {
  if (state.config) return; // servidor mal configurado: o aviso já explica
  formL.password.value = ""; $("#login-err").textContent = msg || "";
  if (!dlgL.open) dlgL.showModal();
  setTimeout(() => formL.password.focus(), 0);
}
async function afterLogin() {
  state.me = await api("/api/me"); state.authed = true; state.loadError = null;
  await load(); await loadRun(); render();
  if (state.run && state.run.status !== "completed") startPolling();
}
formL.addEventListener("submit", async (e) => {
  e.preventDefault(); const err = $("#login-err"); err.textContent = ""; $("#login-go").disabled = true;
  try {
    await api("/api/login", { method: "POST", body: JSON.stringify({ password: formL.password.value }) });
    formL.password.value = ""; await afterLogin(); dlgL.close();
  } catch (ex) {
    err.textContent = ex.status === 401 ? "Senha incorreta." : ex.status === 429 ? "Muitas tentativas. Aguarde alguns minutos."
      : ex.status === 503 ? "Servidor não configurado (veja o aviso na página)." : ex.message;
    if (ex.status === 503) { state.config = ex.data; dlgL.close(); render(); }
  } finally { $("#login-go").disabled = false; }
});

/* ---------- inicialização ---------- */
async function load() {
  try { state.dashboard = await fetchDashboard(); state.loadError = null; }
  catch (e) {
    if (e.status === 401) return;
    state.dashboard = state.dashboard || { generated_at: null, watches: [] };
    state.loadError = e.message;
  }
  if (canWrite() && !state.watchesLoaded) { try { await reloadWatches(); } catch (e) { if (e.status !== 401) toast(`Não consegui ler as buscas cadastradas: ${esc(e.message)}`, 8000); } }
  render();
}
(async function init() {
  // versões antigas guardavam o token do GitHub neste navegador: apaga, ele não é mais usado
  for (const k of ["flights.token", "flights.repo", "flights.datarepo"]) store.set(k, null);
  try { await afterLogin(); }
  catch (e) {
    if (e.status === 503) state.config = e.data;
    render();
    if (e.status === 401 || e.status === 0) openLogin();
  }
  setInterval(() => { if (!document.hidden) render(); }, 60000); // atualiza os "há X min"
})();
