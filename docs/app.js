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
  favs: [],               // voos favoritos (favorites.json)
  favSha: null,
  favsLoaded: false,
  favSort: "recent",
  lastRemoved: null,      // último favorito removido (para "Desfazer")
  sel: {},                // combinação de datas selecionada por busca
  datesAll: {},           // por busca: mostrar todas as datas na aba Datas (padrão: as mais baratas)
  open: new Set(),        // cards com a tabela de voos aberta
  sort: {},               // ordenação da tabela de voos por busca
  sortSummary: "price",
  run: null,              // última execução do workflow
  polling: null,
  editingId: null,
  view: { type: "list" },  // tela atual (vem do #hash)
  flt: {},                // filtros da lista de voos, por viagem
  openDet: new Set(),     // <details> abertos (sobrevivem a novas renderizações)
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
  state.authed = false; state.me = null; state.watchesLoaded = false; state.sha = null; state.favs = []; state.favsLoaded = false; state.favSha = null; stopPolling();
  if (was) { toast("Sua sessão expirou. Entre de novo."); render(); openLogin(); }
}

const fetchDashboard = () => api("/api/dashboard");
const fetchWatchesFile = () => api("/api/watches");
async function saveWatches(list, message) {
  const j = await api("/api/watches", { method: "PUT", body: JSON.stringify({ list, sha: state.sha, message }) });
  state.sha = j.sha;
  state.watches = j.list; // já normalizada pelo servidor
}
const fetchFavsFile = () => api("/api/favorites");
async function saveFavs(list, message) {
  const j = await api("/api/favorites", { method: "PUT", body: JSON.stringify({ list, sha: state.favSha, message }) });
  state.favSha = j.sha;
  state.favs = j.list; // já normalizada pelo servidor
}
async function reloadFavs() {
  if (!canWrite()) return;
  const f = await fetchFavsFile(); state.favSha = f.sha; state.favs = f.list; state.favsLoaded = true; render();
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
/* ---------- rótulos e traduções ---------- */
const isMulti = (w) => !!(w.legs && w.legs.length);
const routeCodes = (w) => (isMulti(w) ? [w.legs[0].origin, ...w.legs.map((l) => l.destination)] : [w.origin, w.destination]);
const routeText = (w) => routeCodes(w).join(" → ");
const routeHtml = (w) => routeCodes(w).map(esc).join('<span class="arrow">→</span>');
const dateSpan = (c) => { const end = c.end_date || c.return_date; return `${fmtDayShort(c.depart_date)}${end && end !== c.depart_date ? ` → ${fmtDayShort(end)}` : ""}`; };
const VERDICT = { low: "baixo", typical: "normal", high: "alto" };
const ptBag = (s) => s.replace(/^(\d+) free carry-on$/, "$1 bagagem de mão grátis")
  .replace(/^(\d+) carry-on bag available for a fee$/, "$1 bagagem de mão paga").replace(/^(\d+) carry-on bag: (.+)$/, "$1 bagagem de mão: $2")
  .replace(/^1st checked bag available for a fee$/, "1ª despachada paga").replace(/^1st checked bag: (.+)$/, "1ª despachada: $1")
  .replace(/^1st checked bag is free$/i, "1ª despachada grátis");
const googleUrl = (u) => (typeof u === "string" && /^https:\/\/www\.google\.com\//.test(u) ? u : null); // só links do Google
// compatível com dashboard.json antigo (sem `legs` nas combinações)
const firstLeg = (w, c) => (c.legs && c.legs[0]) || { origin: w.origin, destination: w.destination };
const typicalOf = (c) => c.offers?.find((o) => o.booking && o.booking.typical_low != null)?.booking || null;

/* ---------- cidades (a lista completa de aeroportos vem de airports.js) ---------- */
const cityOf = (c) => Airports.cityOf(c);

/* ---------- peças visuais ---------- */
const icon = (n, cls = "") => `<svg class="ico ${cls}" aria-hidden="true"><use href="#i-${n}"/></svg>`;
const fmtTime = (iso) => (iso ? iso.slice(11, 16) : "–");
const dayDiff = (a, b) => (a && b ? Math.round((isoToDate(b) - isoToDate(a)) / 864e5) : 0);
const hashStr = (s) => [...String(s)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);
const initials = (name) => {
  const w = String(name || "?").split(/\s+(?:and|e)\s+/i)[0].split(/\s+/).filter(Boolean);
  return (w.length > 1 ? w[0][0] + w[1][0] : (w[0] || "?").slice(0, 2)).toUpperCase();
};
const avatar = (name) => `<span class="av av-${hashStr(name) % 12}" aria-hidden="true">${esc(initials(name))}</span>`;
const weekday = (iso) => isoToDate(iso).toLocaleDateString("pt-BR", { weekday: "short" }).replace(".", "");
const pct = (a, b) => (b ? Math.round(((a - b) / b) * 1000) / 10 : 0);
const pctTxt = (p) => `${Math.abs(p).toFixed(1).replace(".", ",")}%`;

/** Classe extra para valores muito longos (o preço encolhe em vez de empurrar o layout). */
const bigCls = (txt) => (String(txt).length > 11 ? " xl" : "");

function levelChip(l) {
  const m = { low: ["good", "down", "Preço baixo"], typical: ["", "equal", "Preço normal"], high: ["bad", "up", "Preço alto"] }[l];
  return m ? `<span class="chip ${m[0]}" title="Avaliação do Google para esta busca">${icon(m[1])}${m[2]}</span>` : "";
}
/** Variação em relação a outro valor: seta + valor + porcentagem. */
function deltaHtml(cur, prev, cur_, label = "vs. coleta anterior") {
  if (prev == null || cur == null) return "";
  const d = cur - prev;
  if (Math.abs(d) < 0.5) return `<span class="flat">${icon("equal")} igual ${label.replace("vs. ", "à ")}</span>`;
  return `<span class="${d < 0 ? "down" : "up"}">${icon(d < 0 ? "down" : "up")}${money(Math.abs(d), cur_)} (${pctTxt(pct(cur, prev))})</span><span class="muted">${esc(label)}</span>`;
}
function spark(history, w = 128, h = 44) {
  const pts = (history || []).map(([t, p]) => [new Date(t).getTime(), p]).sort((a, b) => a[0] - b[0]);
  if (pts.length < 2) return "";
  const lo = Math.min(...pts.map((p) => p[1])), hi = Math.max(...pts.map((p) => p[1]));
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
  const X = (t) => 4 + ((t - t0) / (t1 - t0 || 1)) * (w - 8);
  const Y = (v) => h - 6 - (hi === lo ? 0.5 : (v - lo) / (hi - lo)) * (h - 12);
  const line = pts.map(([t, v], i) => `${i ? "L" : "M"}${X(t).toFixed(1)},${Y(v).toFixed(1)}`).join("");
  const area = `${line}L${X(t1).toFixed(1)},${h}L${X(t0).toFixed(1)},${h}Z`;
  const first = pts[0][1], last = pts[pts.length - 1][1];
  return `<svg class="spark ${last < first ? "dn" : last > first ? "upn" : ""}" viewBox="0 0 ${w} ${h}" role="img" aria-label="Preço de ${esc(money(first))} para ${esc(money(last))} nas últimas coletas">
    <path class="a" d="${area}"/><path class="l" d="${line}"/><circle class="p" cx="${X(t1).toFixed(1)}" cy="${Y(last).toFixed(1)}" r="3.5"/></svg>`;
}
function routeViz(w, lg) {
  const codes = routeCodes(w), n = codes.length;
  // muitos trechos: fonte menor, seta simples e quebra de linha (6 códigos não cabem numa linha em cartão nenhum)
  const many = n > 4;
  return `<div class="route ${lg ? "lg" : ""} ${n > 3 ? "dense" : ""} ${many ? "many" : ""}">${codes.map((c, i) => `${i ? (many ? `<span class="link sm" aria-hidden="true">›</span>` : `<span class="link">${icon("plane")}</span>`) : ""}<span class="code">${esc(c)}</span>`).join("")}</div>`;
}
const cityLine = (w) => routeCodes(w).map(cityOf).join(" → ");
const kindLabel = (w) => (isMulti(w) ? "Multi-destino" : w.return_date ? "Ida e volta" : "Só ida");
const spanText = (a, b) => {
  if (!b || b === a) return fmtDayShort(a);
  const A = isoToDate(a), B = isoToDate(b);
  return A.getMonth() === B.getMonth() ? `${A.getDate()} a ${fmtDayShort(b)}` : `${fmtDayShort(a)} a ${fmtDayShort(b)}`; // "1 a 3 de jun"
};
function dateChipText(w) {
  if (isMulti(w)) { const last = w.legs[w.legs.length - 1]; return `${fmtDayShort(w.legs[0].date)} → ${fmtDay(last.date_to || last.date)}`; }
  if (w.return_date) return `${fmtDayShort(w.depart_date)} → ${fmtDay(w.return_to || w.return_date)}`;
  return w.depart_to ? `${fmtDayShort(w.depart_date)} a ${fmtDay(w.depart_to)}` : fmtDay(w.depart_date);
}
/* datas por extenso, com os períodos: "Ida 5 a 10 dez · Volta 20 a 25 dez" */
const datesDetail = (w) => (isMulti(w) ? w.legs.map((l) => `${l.origin}→${l.destination} ${spanText(l.date, l.date_to)}`).join(" · ")
  : w.return_date ? `Ida ${spanText(w.depart_date, w.depart_to)} · Volta ${spanText(w.return_date, w.return_to)}`
  : hasRange(w) ? `Ida ${spanText(w.depart_date, w.depart_to)}` : dateChipText(w));
function metaChips(w) {
  const out = [...(w.once ? [`<span class="chip warn">${icon("bolt")}Pontual</span>`] : []), `<span class="chip brand">${esc(kindLabel(w))}</span>`, `<span class="chip">${icon("cal")}${esc(dateChipText(w))}</span>`];
  if (w.flex_days) out.push(`<span class="chip">± ${w.flex_days} dia${w.flex_days > 1 ? "s" : ""}</span>`);
  if (hasRange(w)) out.push(`<span class="chip">${icon("cal")}Período de datas</span>`);
  if (w.coverage && w.coverage.seen < w.coverage.space) out.push(`<span class="chip" title="Cada coleta consulta até ${w.budget} combinações; as demais entram nas próximas.">Vistas ${w.coverage.seen} de ${w.coverage.space} datas</span>`);
  out.push(`<span class="chip">${icon("user")}${w.adults} adulto${w.adults > 1 ? "s" : ""}</span>`, `<span class="chip">${esc(CABINS[w.cabin] || w.cabin)}</span>`);
  if (w.max_stops != null) out.push(`<span class="chip">${w.max_stops === 0 ? "Só direto" : `Até ${w.max_stops} escala${w.max_stops > 1 ? "s" : ""}`}</span>`);
  return out.join("");
}
function nextText(w) {
  if (w.once) return "Consulta pontual: não se repete";
  if (!w.active) return "Coleta pausada";
  if (w.expired) return "Viagem já passou";
  if (!w.every_hours) return "Só quando você pedir";
  if (!w.next_run_at) return "";
  return new Date(w.next_run_at) <= new Date() ? "Próxima coleta: na próxima rodada" : `Próxima coleta ${fmtStamp(w.next_run_at)}`;
}
const inHours = (ts) => {
  const s = (new Date(ts) - Date.now()) / 1000;
  if (s <= 60) return "agora";
  if (s < 3600) return `em ${Math.round(s / 60)} min`;
  if (s < 86400) return `em ${Math.round(s / 3600)} h`;
  return `em ${Math.round(s / 86400)} d`;
};
const hasRange = (w) => !!(w.depart_to || w.return_to || (w.legs || []).some((l) => l.date_to));
const failed = (w) => !!w.last_status && !["ok", "empty"].includes(w.last_status);
const partial = (w) => w.last_total > 1 && w.last_failed < w.last_total && (w.combos || []).length > 0; // parte deu certo e já está no painel
const failTitle = (w) => (partial(w)
  ? `${w.last_failed} de ${w.last_total} consultas falharam; as outras deram certo e estão aqui`
  : `${w.last_status === "blocked" ? "Bloqueada pelo Google" : "Falhou"}${w.last_total > 1 ? ` (${w.last_failed} de ${w.last_total} consultas)` : ""}`);
const failCause = (w) => (w.last_status === "blocked"
  ? "O Google pediu captcha por causa de muitas consultas seguidas do mesmo endereço. Não é defeito do sistema: a próxima coleta agendada tenta de novo. Evite disparar várias buscas em sequência."
  : /something went wrong/i.test(w.last_error || "")
    ? "O Google Voos devolveu uma página de erro passageira (comum em coletas automáticas). O sistema já tentou 3 vezes; a próxima coleta repete sozinha."
    : (w.last_error || "O erro não trouxe detalhe.").replace(/[\s.]*$/, ". ") + "Se repetir em toda coleta, o Google pode ter mudado a página.");
const totalLabel = (w) => (isMulti(w) ? "Total da viagem inteira" : w.return_date ? "Total ida e volta" : "A partir de");

/* ---------- renderização ---------- */
function render() {
  const ws = merged();
  const d = state.dashboard;
  $("#updated").textContent = d?.generated_at ? `Dados de ${fmtStamp(d.generated_at)} · ${ago(d.generated_at)}` : "Sem coletas ainda";
  $("#btn-all").disabled = !canWrite() || !ws.length;
  $("#btn-new").disabled = !canWrite(); $("#btn-once").disabled = !canWrite();
  $("#btn-favs").disabled = !canWrite(); $("#btn-favs").classList.toggle("on", state.view.type === "favs");
  $("#btn-favs").setAttribute("aria-pressed", state.view.type === "favs"); $("#btn-favs").title = state.view.type === "favs" ? "Voltar para as viagens" : "Voos favoritos";
  $("#fav-count").hidden = !state.favs.length; $("#fav-count").textContent = state.favs.length;
  $("#auth-label").textContent = state.authed ? "Sair" : "Entrar";
  $("#btn-auth use").setAttribute("href", state.authed ? "#i-out" : "#i-in");
  renderBanners();

  const v = state.view;
  if (v.type === "favs" && canWrite()) return renderFavs(ws);
  if (!ws.length) {
    $("#view").innerHTML = `<div class="panel empty">${icon("plane")}<h2>${canWrite() ? "Nenhuma viagem ainda" : "Entre para ver as suas viagens"}</h2>
      <p class="muted">${canWrite() ? "Cadastre a primeira e o painel passa a acompanhar os preços para você." : "Os preços ficam guardados no seu repositório privado."}</p>
      ${canWrite() ? `<button class="btn primary" data-act="new">${icon("plus")}Nova viagem</button> <button class="btn" data-act="once">${icon("bolt")}Consulta pontual</button>` : ""}</div>`;
    return;
  }
  if (v.type === "trip") {
    const w = ws.find((x) => x.id === v.id);
    if (w) return renderTrip(w, v);
    if (state.watchesLoaded || state.dashboard) { state.view = { type: "list" }; if (location.hash !== "#/") history.replaceState(null, "", "#/"); }
  }
  renderList(ws);
}

function renderBanners() {
  const b = [];
  if (state.config) {
    const miss = [...(state.config.missing || []).map((m) => `falta <code>${esc(m)}</code>`), ...(state.config.problems || []).map(esc)].join("; ");
    b.push(`<div class="banner err">${icon("alert")}<div><b>Servidor não configurado.</b> ${miss}. Defina as variáveis de ambiente na Vercel e faça um novo deploy (veja o README).</div></div>`);
  } else if (!canWrite()) b.push(`<div class="banner">${icon("lock")}<div><b>Você não está logado.</b> <a href="#" data-open="login">Entrar</a> para ver os preços e cadastrar viagens.</div></div>`);
  if (state.loadError) b.push(`<div class="banner err">${icon("alert")}<div><b>Não consegui ler os dados.</b> ${esc(state.loadError)}</div></div>`);
  const run = state.run;
  if (run) {
    const live = run.status !== "completed", ok = run.conclusion === "success";
    if (live) b.push(`<div class="banner">${icon("refresh")}<div><b>Coleta em andamento…</b> Leva alguns minutos; a página atualiza sozinha. <a target="_blank" rel="noopener noreferrer" href="${esc(run.html_url)}">Ver execução</a></div></div>`);
    else if (!ok) b.push(`<div class="banner err">${icon("alert")}<div><b>A última coleta falhou</b> (${esc(run.conclusion)}) ${ago(run.updated_at)}. <a target="_blank" rel="noopener noreferrer" href="${esc(run.html_url)}">Ver detalhes</a>${(() => { const bad = (state.dashboard?.watches || []).filter(failed); const w0 = bad.find((w) => w.last_status === "blocked") || bad[0];
        return w0 ? `<div class="muted small">${esc(failCause(w0))}${bad.length > 1 ? ` (${bad.length} viagens afetadas)` : ""}</div>` : `<div class="muted small">O motivo de cada falha aparece no cartão da viagem. O log do GitHub é discreto de propósito (repositório público).</div>`; })()}</div></div>`);
  }
  $("#banners").innerHTML = b.join("");
}

/* ---- lista ---- */
function kpisHtml(ws) {
  const active = ws.filter((w) => w.active && !w.expired);
  const paused = ws.length - active.length;
  let drop = null;
  for (const w of ws) {
    const c = bestCombo(w); if (!c || c.prev_price == null) continue;
    const dd = c.min_price - c.prev_price;
    if (dd < -0.5 && (!drop || dd < drop.dd)) drop = { w, c, dd };
  }
  const last = ws.map((w) => w.last_run_at).filter(Boolean).sort().pop();
  const nexts = active.filter((w) => w.every_hours && w.next_run_at).map((w) => w.next_run_at).sort();
  const bad = ws.filter(failed).length;
  const cur = ws[0]?.currency;
  return `<div class="kpis">
    <div class="panel kpi"><div class="kpi-k">${icon("plane")}Viagens monitoradas</div><div class="kpi-v num">${ws.length}</div>
      <div class="kpi-s">${active.length} ativa${active.length === 1 ? "" : "s"}${paused ? ` · ${paused} pausada${paused > 1 ? "s" : ""} ou já passada${paused > 1 ? "s" : ""}` : ""}</div></div>
    <div class="panel kpi"><div class="kpi-k">${icon("down")}Maior queda de preço</div>
      <div class="kpi-v num ${drop ? "down" : ""}">${drop ? `−${money(-drop.dd, cur)}` : "—"}</div>
      <div class="kpi-s">${drop ? `${esc(routeText(drop.w))} · desde a coleta anterior` : "Nenhuma queda desde a última coleta"}</div></div>
    <div class="panel kpi"><div class="kpi-k">${icon("clock")}Última coleta</div><div class="kpi-v">${last ? esc(ago(last)) : "—"}</div>
      <div class="kpi-s">${last ? esc(fmtStamp(last)) : "Ainda não coletou"}${bad ? ` · <span class="up">${bad} com erro</span>` : last ? " · tudo certo" : ""}</div></div>
    <div class="panel kpi"><div class="kpi-k">${icon("bell")}Próxima coleta</div><div class="kpi-v">${nexts.length ? esc(inHours(nexts[0])) : "—"}</div>
      <div class="kpi-s">${nexts.length ? `${nexts.length} viagem${nexts.length > 1 ? "s" : ""} agendada${nexts.length > 1 ? "s" : ""}` : "Nada agendado"}</div></div>
  </div>`;
}

function tripCard(w) {
  const c = bestCombo(w), rw = canWrite();
  const chips = [];
  if (c) chips.push(levelChip(c.price_level));
  if (!w.active) chips.push(`<span class="chip warn">${icon("pause")}Pausada</span>`);
  if (w.expired) chips.push(`<span class="chip warn">Já passou</span>`);
  if (failed(w)) chips.push(partial(w) ? `<span class="chip warn">${icon("alert")}${w.last_failed} de ${w.last_total} falharam</span>` : `<span class="chip bad">${icon("alert")}Coleta falhou</span>`);
  let body;
  if (!c) {
    body = `<div class="tcard-empty">${w.pending ? (w.once ? "Consultando o Google Voos… leva de 1 a 5 minutos; a página atualiza sozinha. Se não chegar, use “Buscar agora”." : "Aguardando a primeira coleta. Use “Buscar agora” para acelerar.") : w.last_status === "empty" ? "O Google não retornou voos para essas datas." : "Ainda sem resultados."}</div>`;
  } else {
    body = `<div class="price-row"><div><div class="price-k">${esc(totalLabel(w))}</div><div class="price num${bigCls(money(c.min_price, w.currency))}">${money(c.min_price, w.currency)}</div>
        <div class="price-d">${deltaHtml(c.min_price, c.prev_price, w.currency, "vs. anterior")}</div></div>${spark(c.history)}</div>
      <div class="chips">${w.combos.length > 1 ? `<span class="chip good">${icon("cal")}Melhor data: ${esc(dateSpan(c))}</span>` : ""}${metaChips(w)}</div>
      <div class="facts">
        <div><div class="fact-k">${icon("clock")}Mais rápida</div><div class="fact-v num">${hm(c.fastest_min)}</div></div>
        <div><div class="fact-k">${icon("plane")}Direto</div><div class="fact-v num">${c.min_nonstop == null ? "—" : money(c.min_nonstop, w.currency)}</div></div>
        <div><div class="fact-k">${icon("tag")}Voos</div><div class="fact-v num">${c.offers_count}</div></div>
      </div>`;
  }
  if (!c) body += `<div class="chips">${metaChips(w)}</div>`;
  return `<article class="panel tcard ${w.active ? "" : "paused"}" data-open-trip="${w.id}" tabindex="0" role="link" aria-label="Abrir ${esc(w.label || routeText(w))}">
    <div class="tcard-top"><div><div class="tcard-name">${esc(w.label || kindLabel(w))}</div><div class="tcard-sub">${esc(cityLine(w))}</div></div><div class="chips">${chips.join("")}</div></div>
    ${routeViz(w)}
    ${body}
    ${failed(w) ? `<div class="tcard-fail${partial(w) ? " soft" : ""}" title="${esc(failCause(w))}">${icon("alert")}<span><b>${esc(failTitle(w))}.</b> ${esc(failCause(w))}</span></div>` : ""}
    <div class="tcard-foot"><span>${c ? `Coletado ${esc(ago(c.scraped_at))}` : esc(nextText(w))}</span>
      <span class="btns"><button class="btn sm" data-act="run" data-id="${w.id}" ${rw ? "" : "disabled"}>${icon("refresh")}Buscar agora</button></span></div>
  </article>`;
}

function renderList(ws) {
  const rank = (a, b) => (b.active && !b.expired) - (a.active && !a.expired) || a.id - b.id;
  const mon = ws.filter((w) => !w.once).sort(rank), once = ws.filter((w) => w.once).sort((a, b) => b.id - a.id);
  $("#view").innerHTML = `${mon.length ? kpisHtml(mon) : ""}
    ${mon.length || !once.length ? `<div class="section-title">Minhas viagens <span class="count">${mon.length}</span></div>
      ${mon.length ? `<div class="trips">${mon.map(tripCard).join("")}</div>` : `<div class="panel empty">${icon("plane")}<h2>Nenhuma viagem monitorada</h2><p class="muted">Cadastre uma viagem para o painel acompanhar os preços, ou use uma consulta pontual.</p></div>`}` : ""}
    ${once.length ? `<div class="section-title">Consultas pontuais <span class="count">${once.length}</span>
        <button class="btn sm ghost" data-act="clear-once" ${canWrite() ? "" : "disabled"}>${icon("trash")}Limpar todas</button></div>
      <p class="muted small">Buscas feitas uma vez, sem repetição automática. Abra para ver os voos ou use “Monitorar daqui pra frente” para acompanhar o preço.</p>
      <div class="trips">${once.map(tripCard).join("")}</div>` : ""}`;
}

/* ---- detalhe da viagem ---- */
const TABS = [["resumo", "Resumo"], ["voos", "Voos"], ["datas", "Datas"], ["historico", "Histórico"], ["comparar", "Comparação"], ["busca", "Configuração"]];

function renderTrip(w, v) {
  const c = w.combos.length ? w.combos[comboIndex(w)] : null;
  const rw = canWrite();
  const hasDates = w.combos.length > 1;
  const hasCmp = w.combos.some((x) => x.separate);
  const tabs = TABS.filter(([k]) => (k !== "datas" || hasDates) && (k !== "comparar" || hasCmp) && (c || k === "resumo" || k === "busca"));
  const tab = tabs.some(([k]) => k === v.tab) ? v.tab : "resumo";
  const count = { voos: c?.offers.length };
  const pane = { resumo: paneResumo, voos: paneVoos, datas: paneDatas, historico: paneHistorico, comparar: paneComparar, busca: paneBusca }[tab](w, c);
  $("#view").innerHTML = `<button class="btn ghost crumb" data-nav="/">${icon("left")}Todas as viagens</button>
    <section class="panel trip-head">
      <div class="trip-head-top">
        <div>${routeViz(w, true)}<div class="trip-city">${esc(cityLine(w))}${w.label ? ` · <b>${esc(w.label)}</b>` : ""}</div></div>
        <div class="trip-actions">
          <button class="btn primary" data-act="run" data-id="${w.id}" ${rw ? "" : "disabled"}>${icon("refresh")}Buscar agora</button>
          <button class="btn" data-act="edit" data-id="${w.id}" ${rw ? "" : "disabled"}>${icon("edit")}Editar</button>
          ${w.once ? `<button class="btn" data-act="promote" data-id="${w.id}" ${rw ? "" : "disabled"}>${icon("bell")}Monitorar daqui pra frente</button>`
            : `<button class="btn" data-act="toggle" data-id="${w.id}" ${rw ? "" : "disabled"}>${icon(w.active ? "pause" : "play")}${w.active ? "Pausar" : "Retomar"}</button>`}
          <button class="btn danger" data-act="remove" data-id="${w.id}" ${rw ? "" : "disabled"}>${icon("trash")}Remover</button>
        </div>
      </div>
      <div class="chips">${metaChips(w)}${failed(w) ? (partial(w) ? `<span class="chip warn">${icon("alert")}Coleta parcial</span>` : `<span class="chip bad">${icon("alert")}Última coleta falhou</span>`) : ""}${w.active ? "" : `<span class="chip warn">${icon("pause")}Pausada</span>`}</div>
      <div class="muted small">${c ? `Resultado de ${esc(fmtStamp(c.scraped_at))} (${esc(ago(c.scraped_at))})` : ""}${c && nextText(w) ? " · " : ""}${esc(nextText(w))}</div>
      ${failed(w) ? `<div class="banner ${partial(w) ? "" : "err"} nomargin">${icon("alert")}<div><b>${partial(w) ? `Coleta parcial: ${w.last_failed} de ${w.last_total} consultas falharam. As ${w.last_total - w.last_failed} que deram certo estão mostradas aqui.` : `A última coleta ${w.last_status === "blocked" ? "foi bloqueada pelo Google" : "falhou"}${w.last_total > 1 ? ` em ${w.last_failed} de ${w.last_total} consultas` : ""}.`}</b> <div class="muted small">${esc(failCause(w))}</div></div></div>` : ""}
    </section>
    <nav class="tabs" role="tablist" aria-label="Seções da viagem">${tabs.map(([k, t]) => `<button class="tab" role="tab" id="tab-${k}" aria-selected="${k === tab}" data-tab="${k}">${t}${count[k] ? ` <span class="chip">${count[k]}</span>` : ""}</button>`).join("")}</nav>
    <div role="tabpanel" aria-labelledby="tab-${tab}">${pane}</div>`;
  restoreDetails();
  if (tab === "resumo" || tab === "historico") drawChart(w, c);
}

const noData = (w) => `<div class="panel empty">${icon("clock")}<h2>Sem resultados ainda</h2><p class="muted">${w.pending ? "A primeira coleta ainda não aconteceu." : w.last_status === "empty" ? "O Google não retornou voos para essas datas." : "Ainda não há coleta para mostrar."}
  Use “Buscar agora” e volte em alguns minutos.</p></div>`;

function itinHtml(o) {
  const next = dayDiff(o.dep_time, o.arr_time);
  const lay = o.layover_airports ? ` · ${esc(o.layover_airports.replace(/,/g, " · "))}` : "";
  return `<div class="itin"><div><div class="itin-t num">${fmtTime(o.dep_time)}</div><div class="itin-c">${esc(o.origin)} · ${esc(cityOf(o.origin))}</div></div>
    <div class="itin-d"><b>${hm(o.duration_min)}</b><div class="flline ${o.stops ? "" : "direct"}">${"<i></i>".repeat(o.stops || 0)}</div><div>${stopsTxt(o.stops)}${lay}</div></div>
    <div class="ta-r"><div class="itin-t num">${fmtTime(o.arr_time)}${next > 0 ? `<span class="nextday">+${next}</span>` : ""}</div><div class="itin-c">${esc(o.destination)} · ${esc(cityOf(o.destination))}</div></div></div>`;
}

function rangeHtml(price, t, cur) {
  if (!t || t.typical_low == null || t.typical_high == null || t.typical_high <= t.typical_low) return "";
  const r = Math.max(0, Math.min(1, (price - t.typical_low) / (t.typical_high - t.typical_low)));
  return `<div class="range"><div class="price-k">Faixa de preço típica desta rota${t.verdict ? ` · este voo está <b>${esc(VERDICT[t.verdict] || t.verdict)}</b>` : ""}</div>
    <div class="range-bar"><span class="range-pin p${Math.round(r * 10)}">${esc(money(price, cur))}</span></div>
    <div class="range-ends"><span>${money(t.typical_low, cur)}</span><span>${money(t.typical_high, cur)}</span></div></div>`;
}

function paneResumo(w, c) {
  if (!c) return noData(w) + configPanel(w);
  const offers = c.offers;
  const best = offers[0];
  const direct = offers.filter((o) => o.stops === 0).sort((a, b) => a.price - b.price)[0];
  const fast = [...offers].sort((a, b) => (a.duration_min ?? 1e9) - (b.duration_min ?? 1e9))[0];
  const typ = typicalOf(c);
  const per = w.adults > 1 ? `<span class="muted">${money(c.min_price / w.adults, w.currency)} por adulto</span>` : "";
  const since = c.first_at && c.first_at !== c.scraped_at && c.first_price != null ? deltaHtml(c.min_price, c.first_price, w.currency, "desde a 1ª coleta") : "";
  const topDates = w.combos.length > 1 ? [...w.combos].map((x, i) => [x, i]).sort((a, b) => a[0].min_price - b[0].min_price).slice(0, 4) : [];
  return `<div class="hero">
    <section class="panel best">
      <div class="best-top"><div><div class="price-k">${esc(totalLabel(w))}${w.combos.length > 1 ? ` · ${esc(dateSpan(c))}` : ""}</div>
        <div class="price lg num${bigCls(money(c.min_price, w.currency))}">${money(c.min_price, w.currency)}</div><div class="price-d">${deltaHtml(c.min_price, c.prev_price, w.currency)} ${per}</div></div>
        <div class="chips">${levelChip(c.price_level)}</div></div>
      ${best ? `${itinHtml(best)}
        <div class="fl-air">${avatar(best.airline)}<div><b>${esc(best.airline)}</b><span class="muted small">${esc(best.flight_numbers || "")}${best.operated_by && best.operated_by !== best.airline ? ` · operado por ${esc(best.operated_by)}` : ""}</span></div></div>` : ""}
      ${rangeHtml(c.min_price, typ, w.currency)}
    </section>
    <div class="stats">
      <div class="panel stat"><div class="stat-k">${icon("plane")}Mais barata direta</div><div class="stat-v num">${direct ? money(direct.price, w.currency) : "—"}</div>
        <div class="stat-s">${direct ? `${esc(direct.airline)} · ${hm(direct.duration_min)}${direct.price > c.min_price ? ` · +${money(direct.price - c.min_price, w.currency)}` : " · é a mais barata"}` : "Sem voo direto nesta data"}</div></div>
      <div class="panel stat"><div class="stat-k">${icon("bolt")}Viagem mais rápida</div><div class="stat-v num">${hm(c.fastest_min)}</div>
        <div class="stat-s">${fast ? `${esc(fast.airline)} · ${money(fast.price, w.currency)}` : ""}</div></div>
      <div class="panel stat"><div class="stat-k">${icon("tag")}Desde a 1ª coleta</div><div class="stat-v num">${c.first_price != null ? money(c.first_price, w.currency) : "—"}</div>
        <div class="stat-s price-d">${since || "Só há uma coleta até agora"}</div></div>
    </div>
  </div>
  ${topDates.length ? `<div class="section-title">Melhores datas <button class="btn ghost sm" data-tab="datas">Ver todas${icon("chev")}</button></div><div class="dates">${topDates.map(([x, i]) => dateTile(w, x, i, c, w.combos)).join("")}</div>` : ""}
  <div class="section-title">Preço ao longo do tempo</div>
  <section class="panel panel-pad">${chartScaffold(w)}</section>
  <div class="section-title">Sobre esta busca</div>${configPanel(w)}`;
}

/* ---- voos ---- */
function fltState(w) { return (state.flt[w.id] ||= { sort: "price", direct: false, airline: "", limit: 12 }); }
const AMEN = { "Wi-Fi for a fee": "Wi-Fi pago", "Free Wi-Fi": "Wi-Fi grátis", "In-seat USB outlet": "tomada USB", "In-seat power & USB outlets": "tomada e USB",
  "In-seat power outlet": "tomada", "On-demand video": "vídeo sob demanda", "Stream media to your device": "streaming no seu aparelho" };
const LEG = { "Above average": "acima da média", "Average": "média", "Below average": "abaixo da média" };

function paneVoos(w, c) {
  if (!c.offers.length) return `<div class="panel empty"><p class="muted">Esta data é de um período grande: a lista de voos só fica guardada para as ${DETAILED_COMBOS} combinações mais baratas, para o painel continuar leve. Os preços dela aparecem na aba Datas e no histórico.</p></div>`;
  const f = fltState(w);
  const all = c.offers;
  const cheapest = Math.min(...all.map((o) => o.price)), fastest = Math.min(...all.map((o) => o.duration_min ?? 1e9));
  const airlines = [...new Set(all.map((o) => o.airline))].sort();
  let list = all.filter((o) => (!f.direct || o.stops === 0) && (!f.airline || o.airline === f.airline));
  list = [...list].sort((a, b) => ((a[f.sort] ?? "") > (b[f.sort] ?? "") ? 1 : (a[f.sort] ?? "") < (b[f.sort] ?? "") ? -1 : 0) || a.price - b.price);
  const shown = list.slice(0, f.limit);
  const note = isMulti(w) || w.return_date ? `<p class="muted small">Estes são os voos do 1º trecho (${esc(firstLeg(w, c).origin)} → ${esc(firstLeg(w, c).destination)}). O preço é o total da viagem inteira com esse voo: o Google só mostra os demais trechos depois de escolher o voo no site.</p>` : "";
  return `${note}<div class="toolbar">
      <label>Ordenar <select data-flt="sort"><option value="price" ${f.sort === "price" ? "selected" : ""}>Menor preço</option><option value="duration_min" ${f.sort === "duration_min" ? "selected" : ""}>Mais rápido</option><option value="dep_time" ${f.sort === "dep_time" ? "selected" : ""}>Horário de saída</option></select></label>
      <label>Companhia <select data-flt="airline"><option value="">Todas</option>${airlines.map((a) => `<option ${f.airline === a ? "selected" : ""}>${esc(a)}</option>`).join("")}</select></label>
      <label><input type="checkbox" data-flt="direct" ${f.direct ? "checked" : ""}> Só diretos</label>
      <span class="spacer"></span><span class="muted small">${list.length} voo${list.length === 1 ? "" : "s"} listado${list.length === 1 ? "" : "s"}${c.offers_count > all.length ? ` · ${c.offers_count} encontrados no total` : ""}</span></div>
    <div class="flights">${shown.length ? shown.map((o, i) => flightRow(w, o, { cheapest: o.price === cheapest, fastest: o.duration_min === fastest, key: `${w.id}-${c.depart_date}-${o.flight_numbers}-${o.dep_time}`, favId: canWrite() ? registerFav(w, c, o) : null })).join("") : `<div class="panel empty"><p class="muted">Nenhum voo com esses filtros.</p></div>`}</div>
    ${list.length > f.limit ? `<button class="btn more-btn" data-more="${w.id}">Mostrar mais ${Math.min(12, list.length - f.limit)} voos</button>` : ""}`;
}

function flightRow(w, o, { cheapest, fastest, key, favId }) {
  const next = dayDiff(o.dep_time, o.arr_time);
  const badges = [];
  if (cheapest) badges.push(`<span class="chip good">${icon("tag")}Mais barato</span>`);
  if (fastest) badges.push(`<span class="chip brand">${icon("bolt")}Mais rápido</span>`);
  if (o.stops === 0) badges.push(`<span class="chip">Direto</span>`);
  if (o.overnight) badges.push(`<span class="chip warn">Pernoite</span>`);
  if (o.often_delayed) badges.push(`<span class="chip warn">${icon("alert")}Costuma atrasar</span>`);
  if (o.cabin) badges.push(`<span class="chip">${esc(o.cabin)}</span>`);
  if (o.co2_kg != null && o.co2_diff_pct) badges.push(`<span class="chip ${o.co2_diff_pct < 0 ? "good" : ""}">CO₂ ${o.co2_diff_pct > 0 ? "+" : ""}${o.co2_diff_pct}%</span>`);
  if (o.booking?.bags?.length) badges.push(`<span class="chip">${icon("bag")}${esc(ptBag(o.booking.bags[0]))}</span>`);
  const detail = flightDetail(w, o);
  return `<article class="panel fl"><div class="fl-main">
    <div class="fl-air">${avatar(o.airline)}<div><b>${esc(o.airline)}</b><span>${esc(o.flight_numbers || "")}${o.operated_by && o.operated_by !== o.airline ? ` · op. ${esc(o.operated_by)}` : ""}</span></div></div>
    <div class="fl-times"><div><div class="fl-t num">${fmtTime(o.dep_time)}</div><div class="fl-c">${esc(o.origin)}</div></div>
      <div class="fl-mid"><b>${hm(o.duration_min)}</b><div class="flline ${o.stops ? "" : "direct"}">${"<i></i>".repeat(o.stops || 0)}</div><div>${stopsTxt(o.stops)}${o.layover_airports ? ` · ${esc(o.layover_airports.replace(/,/g, " · "))}` : ""}</div></div>
      <div class="ta-r"><div class="fl-t num">${fmtTime(o.arr_time)}${next > 0 ? `<span class="nextday">+${next}</span>` : ""}</div><div class="fl-c">${esc(o.destination)}</div></div></div>
    <div class="fl-price"><div class="price num${bigCls(money(o.price, w.currency))}">${money(o.price, w.currency)}</div>${w.adults > 1 ? `<div class="muted small">${money(o.price / w.adults, w.currency)} por adulto</div>` : ""}</div>
  </div>
  <div class="fl-badges">${badges.join("")}${favId ? starBtn(favId) : ""}</div>${detail ? `<details data-k="${esc(key)}"><summary>${icon("chev")}Detalhes do voo</summary><div class="fl-detail">${detail}</div></details>` : ""}</article>`;
}

function flightDetail(w, o) {
  const pt = (p) => (p ? `${esc(p.code)} ${esc(p.time)}${p.plus_days ? ` (+${p.plus_days})` : ""}` : "");
  const segs = (o.segments || []).map((s) => {
    const pills = [s.aircraft, s.cabin, s.legroom_in ? `assento ${LEG[s.legroom] || s.legroom} · ${s.legroom_in} pol` : null,
      ...(s.amenities || []).map((a) => AMEN[a] || a), s.plane_and_crew ? `avião e tripulação: ${s.plane_and_crew}` : null,
      s.delay_note ? "⚠ costuma atrasar 30+ min" : null, s.co2_kg != null ? `${s.co2_kg} kg CO₂` : null].filter(Boolean);
    const lay = s.layover_after ? `<div class="lay">Conexão de ${hm(s.layover_after.duration_min)} em ${esc(s.layover_after.city)} (${esc(s.layover_after.code)})${s.layover_after.note ? ` · ${esc(s.layover_after.note)}` : ""}</div>` : "";
    return `<div class="seg"><div class="seg-t num">${esc(s.flight || "")}</div><div><div class="seg-m">${pt(s.dep)} → ${pt(s.arr)}${s.duration_min != null ? ` · ${hm(s.duration_min)}` : ""}${s.overnight ? " · pernoite" : ""}</div>
      <div class="pills">${pills.map((p) => `<span class="pill">${esc(p)}</span>`).join("")}</div></div>${lay}</div>`;
  }).join("");
  const b = o.booking;
  let bk = "";
  if (b) {
    const sellers = (b.sellers || []).map((s) => `${esc(s.seller)}${s.type ? ` (${s.type === "Airline" ? "companhia" : esc(s.type)})` : ""}: <b>${money(s.price, w.currency)}</b>`).join(" · ");
    const link = googleUrl(b.url) ? `<a href="${esc(b.url)}" target="_blank" rel="noopener noreferrer">${icon("ext")}Abrir este voo no Google Voos</a>` : "";
    bk = `<div class="bk">
      <div class="bk-row">${icon("bag")}<b>Bagagem</b> ${b.bags && b.bags.length ? b.bags.map((x) => esc(ptBag(x))).join(" · ") : "sem informação"}</div>
      ${sellers ? `<div class="bk-row">${icon("tag")}<b>Vendedor</b> ${sellers}</div>` : ""}
      ${b.typical_low != null ? `<div class="bk-row">${icon("down")}<b>Faixa típica</b> ${money(b.typical_low, w.currency)}–${money(b.typical_high, w.currency)}${b.verdict ? ` · este voo está ${esc(VERDICT[b.verdict] || b.verdict)}` : ""}</div>` : ""}
      ${b.bag_policy ? `<div class="bk-row muted small">${esc(b.bag_policy)}</div>` : ""}${link ? `<div class="bk-row">${link}</div>` : ""}</div>`;
  }
  return segs + bk;
}

/* ---- datas ---- */
function dateTile(w, x, i, sel, combos) {
  const lo = Math.min(...combos.map((q) => q.min_price)), hi = Math.max(...combos.map((q) => q.min_price));
  const r = hi === lo ? 0 : (x.min_price - lo) / (hi - lo);
  const cls = x.min_price === lo ? "min" : r > 0.9 ? "t3" : r > 0.66 ? "t2" : r > 0.33 ? "t1" : "";
  const end = x.end_date || x.return_date;
  return `<button class="dtile ${cls}" data-combo="${w.id}:${i}" aria-pressed="${x === sel}" title="Ver os voos desta data">
    <span class="dw">${esc(weekday(x.depart_date))}</span><span class="dd">${esc(fmtDayShort(x.depart_date))}${end && end !== x.depart_date ? ` → ${esc(fmtDayShort(end))}` : ""}</span>
    <span class="dp num">${money(x.min_price, w.currency)}</span><span class="dx">${x.min_price === lo ? "Menor preço" : `+${money(x.min_price - lo, w.currency)}`}</span></button>`;
}
const DATES_TOP = 24, DETAILED_COMBOS = 20; // espelha DETAILED_COMBOS de flights/db.py
function paneDatas(w, c) {
  const byDate = (a, b) => a[0].depart_date.localeCompare(b[0].depart_date) || (a[0].end_date || "").localeCompare(b[0].end_date || "");
  const all = w.combos.map((x, i) => [x, i]);
  const lo = Math.min(...w.combos.map((x) => x.min_price));
  const best = w.combos.find((x) => x.min_price === lo);
  const many = all.length > DATES_TOP, showAll = !many || state.datesAll?.[w.id];
  const shown = showAll ? [...all].sort(byDate) : [...all].sort((a, b) => a[0].min_price - b[0].min_price).slice(0, DATES_TOP).sort(byDate);
  const cov = w.coverage;
  const covNote = cov && cov.space > (w.budget || 40) ? `<div class="banner">${icon("cal")}<div><b>Período grande: ${cov.seen} de ${cov.space} combinações já consultadas.</b>
      <div class="muted small">Cada coleta consulta até ${esc(w.budget)} (as mais baratas e as datas vizinhas a elas primeiro, depois uma amostra espalhada do resto). ${cov.seen < cov.space ? `O período inteiro fica visto em cerca de ${cov.runs_left} coleta${cov.runs_left > 1 ? "s" : ""}; use “Buscar agora” para adiantar.` : "O período inteiro já foi visto; as coletas agora renovam as mais baratas e as mais antigas."}</div></div></div>` : "";
  return `${covNote}<p class="muted">Menor preço em cada data comparada. Clique numa data para ver os voos dela. A mais barata é <b>${esc(dateSpan(best))}</b> (${money(lo, w.currency)}).</p>
    <div class="dates">${shown.map(([x, i]) => dateTile(w, x, i, c, w.combos)).join("")}</div>
    ${many ? `<button class="btn more-btn" data-dates-all="${w.id}">${showAll ? `Mostrar só as ${DATES_TOP} mais baratas` : `Mostrar todas as ${all.length} datas`}</button>` : ""}`;
}

/* ---- histórico ---- */
const chartScaffold = (w) => `<div class="chart-box" id="chart-${w.id}"></div>`;
function paneHistorico(w, c) {
  const pts = [...c.history].sort((a, b) => new Date(a[0]) - new Date(b[0]));
  const rows = [...pts].reverse().slice(0, 40).map((q, i, arr) => {
    const prev = arr[i + 1], d = prev ? q[1] - prev[1] : null;
    return `<tr><td>${esc(fmtStamp(q[0]))}</td><td class="r num"><b>${money(q[1], w.currency)}</b></td><td class="r num">${d == null ? "–" : d === 0 ? `<span class="flat">igual</span>` : `<span class="${d < 0 ? "down" : "up"}">${d < 0 ? "−" : "+"}${money(Math.abs(d), w.currency)}</span>`}</td></tr>`;
  }).join("");
  const lo = Math.min(...pts.map((q) => q[1])), hi = Math.max(...pts.map((q) => q[1]));
  return `<p class="muted">${w.combos.length > 1 ? `Data selecionada: <b>${esc(dateSpan(c))}</b>. ` : ""}Menor preço encontrado em cada coleta${pts.length > 1 ? `: mínimo de ${money(lo, w.currency)} e máximo de ${money(hi, w.currency)} no período.` : "."}</p>
    <section class="panel panel-pad">${chartScaffold(w)}</section>
    <div class="section-title">Coletas <span class="count">${pts.length}</span></div>
    <section class="panel tablewrap"><table><thead><tr><th>Quando</th><th class="r">Menor preço</th><th class="r">Variação</th></tr></thead><tbody>${rows}</tbody></table></section>`;
}

function drawChart(w, c) {
  const box = document.getElementById(`chart-${w.id}`); if (!box || !c) return;
  const pts = c.history.map(([t, p]) => ({ t: new Date(t).getTime(), p, ts: t })).sort((a, b) => a.t - b.t);
  if (pts.length < 2) { box.innerHTML = `<div class="empty-chart">O gráfico aparece a partir da 2ª coleta.${pts[0] ? ` Menor preço até agora: <b>${money(pts[0].p, w.currency)}</b>.` : ""}</div>`; return; }
  const W = Math.max(320, box.clientWidth || 640), H = 210, m = { l: 62, r: 70, t: 14, b: 28 };
  const lo = Math.min(...pts.map((q) => q.p)), hi = Math.max(...pts.map((q) => q.p));
  const pad = (hi - lo) * 0.18 || hi * 0.05 || 1;
  const y0 = Math.max(0, lo - pad), y1 = hi + pad, t0 = pts[0].t, t1 = pts[pts.length - 1].t;
  const X = (t) => m.l + ((t - t0) / (t1 - t0 || 1)) * (W - m.l - m.r);
  const Y = (p) => m.t + (1 - (p - y0) / (y1 - y0)) * (H - m.t - m.b);
  const ticks = [0, 1, 2, 3].map((i) => y0 + ((y1 - y0) * i) / 3);
  const line = pts.map((q, i) => `${i ? "L" : "M"}${X(q.t).toFixed(1)},${Y(q.p).toFixed(1)}`).join("");
  const area = `${line}L${X(t1).toFixed(1)},${H - m.b}L${X(t0).toFixed(1)},${H - m.b}Z`;
  const last = pts[pts.length - 1];
  const dayLbl = (t) => new Date(t).toLocaleDateString("pt-BR", { day: "numeric", month: "short" }).replace(/\./g, "");
  box.innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Menor preço por coleta, de ${esc(money(pts[0].p, w.currency))} a ${esc(money(last.p, w.currency))}" tabindex="0">
    ${ticks.map((v) => `<line class="grid" x1="${m.l}" x2="${W - m.r}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"/><text x="${m.l - 8}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${esc(money(v, w.currency))}</text>`).join("")}
    <line class="axis" x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}"/>
    <text x="${m.l}" y="${H - 8}" text-anchor="start">${esc(dayLbl(t0))}</text><text x="${W - m.r}" y="${H - 8}" text-anchor="end">${esc(dayLbl(t1))}</text>
    <path class="area" d="${area}"/><path class="line" d="${line}"/>
    <circle class="pt" cx="${X(last.t).toFixed(1)}" cy="${Y(last.p).toFixed(1)}" r="5"/>
    <text class="end" x="${(X(last.t) + 10).toFixed(1)}" y="${(Y(last.p) + 4).toFixed(1)}">${esc(money(last.p, w.currency))}</text>
    <g class="hov" hidden><line class="cross" y1="${m.t}" y2="${H - m.b}"/><circle class="pt" r="5"/></g>
    <rect x="${m.l}" y="0" width="${W - m.l - m.r}" height="${H}" fill="transparent"/></svg><div class="tip" hidden></div>`;
  const svg = box.querySelector("svg"), tip = box.querySelector(".tip"), hov = svg.querySelector(".hov");
  const show = (clientX) => {
    const r = svg.getBoundingClientRect(); const x = ((clientX - r.left) / r.width) * W;
    let best = 0; pts.forEach((q, i) => { if (Math.abs(X(q.t) - x) < Math.abs(X(pts[best].t) - x)) best = i; });
    const q = pts[best], prev = pts[best - 1], px = X(q.t), py = Y(q.p);
    hov.removeAttribute("hidden"); hov.querySelector("line").setAttribute("x1", px); hov.querySelector("line").setAttribute("x2", px);
    hov.querySelector("circle").setAttribute("cx", px); hov.querySelector("circle").setAttribute("cy", py);
    const dd = prev ? q.p - prev.p : null;
    tip.innerHTML = `<b class="num">${esc(money(q.p, w.currency))}</b>${esc(fmtStamp(q.ts))}${dd ? `<br><span class="${dd < 0 ? "down" : "up"}">${dd < 0 ? "−" : "+"}${esc(money(Math.abs(dd), w.currency))}</span>` : ""}`;
    tip.hidden = false; tip.style.left = `${Math.min(Math.max((px / W) * r.width, 64), r.width - 64)}px`; tip.style.top = `${(py / H) * r.height - 12}px`;
  };
  const hide = () => { hov.setAttribute("hidden", ""); tip.hidden = true; };
  svg.addEventListener("pointermove", (e) => show(e.clientX)); svg.addEventListener("pointerleave", hide); svg.addEventListener("pointerdown", (e) => show(e.clientX));
}
let rzT; window.addEventListener("resize", () => { clearTimeout(rzT); rzT = setTimeout(() => { const v = state.view; if (v.type !== "trip") return; const w = merged().find((x) => x.id === v.id); if (w && w.combos.length && ["resumo", "historico"].includes(v.tab || "resumo")) drawChart(w, w.combos[comboIndex(w)]); }, 150); });

/* ---- comparação ---- */
function paneComparar(w, c) {
  const sp = c.separate;
  if (!sp) return `<div class="panel empty"><p class="muted">Sem comparação para a data selecionada. Escolha outra em “Datas”.</p></div>`;
  const rows = sp.legs.map((l, i) => `<tr><td>${i + 1}. <b>${esc(l.origin)} → ${esc(l.destination)}</b> <span class="muted">${esc(cityOf(l.origin))} → ${esc(cityOf(l.destination))}</span></td><td>${esc(fmtDayShort(l.date))}</td>
    <td class="r num"><b>${money(l.min_price, w.currency)}</b></td><td class="r num">${money(l.min_nonstop, w.currency)}</td><td class="r num">${hm(l.fastest_min)}</td></tr>`).join("");
  let verdict, kind = "";
  if (sp.total == null) verdict = "Ainda faltam trechos coletados para comparar.";
  else if (sp.vs_trip < -0.5) { verdict = `Comprar separado sai <span class="down">${money(-sp.vs_trip, w.currency)} mais barato</span> que o pacote.`; kind = "ok"; }
  else if (sp.vs_trip > 0.5) { verdict = `O pacote sai <span class="down">${money(sp.vs_trip, w.currency)} mais barato</span> que comprar separado.`; kind = "ok"; }
  else verdict = "Pacote e separado custam o mesmo.";
  const legs = sp.legs.map((l, i) => `<div class="section-title">Trecho ${i + 1}: ${esc(l.origin)} → ${esc(l.destination)} · ${esc(fmtDay(l.date))} <span class="count">só ida</span></div>
    <div class="flights">${l.offers.length ? l.offers.slice(0, 5).map((o) => flightRow(w, o, { cheapest: o.price === l.min_price, fastest: o.duration_min === l.fastest_min, key: `${w.id}-L${i}-${o.flight_numbers}-${o.dep_time}` })).join("") : `<p class="muted">Sem coleta deste trecho ainda.</p>`}</div>`).join("");
  return `<section class="panel best stack"><div class="price-k">Pacote (${isMulti(w) ? "multi-destino" : "ida e volta"}) × trechos comprados separado</div><div class="verdict">${verdict}</div>
      <div class="row"><div><div class="price-k">Pacote</div><div class="price num${bigCls(money(c.min_price, w.currency))}">${money(c.min_price, w.currency)}</div></div><div><div class="price-k">Soma dos trechos</div><div class="price num${bigCls(money(sp.total, w.currency))}">${money(sp.total, w.currency)}</div></div></div>
      <p class="muted small nomargin">Passagens separadas não têm proteção se um voo atrasar e você perder o seguinte, e a bagagem precisa ser despachada de novo em cada conexão.</p></section>
    <section class="panel tablewrap stack"><table><thead><tr><th>Trecho</th><th>Data</th><th class="r">Mais barato</th><th class="r">Direto</th><th class="r">Mais rápido</th></tr></thead><tbody>${rows}</tbody>
      <tfoot><tr><td colspan="2">Soma dos trechos separados</td><td class="r num">${money(sp.total, w.currency)}</td><td class="r num">${money(sp.total_nonstop, w.currency)}</td><td></td></tr></tfoot></table></section>${legs}`;
}

/* ---- configuração ---- */
function configPanel(w) {
  const kv = (k, v) => `<div><div class="kv-k">${k}</div><div class="kv-v">${v}</div></div>`;
  const every = w.once ? "não: consulta pontual (roda só quando você pede)" : w.every_hours ? (EVERY[w.every_hours] || `a cada ${w.every_hours} h`) : "só quando você pedir";
  return `<section class="panel panel-pad"><div class="kv">
    ${kv("Tipo", esc(kindLabel(w)))}${kv("Trajeto", esc(isMulti(w) ? cityLine(w) : `${cityOf(w.origin)} → ${cityOf(w.destination)}`))}
    ${kv("Datas", esc(datesDetail(w)))}
    ${hasRange(w) ? kv("Período de datas", `todas as combinações dentro dos períodos, dias inicial e final incluídos${w.n_queries ? ` (${w.n_queries} consulta${w.n_queries > 1 ? "s" : ""} por coleta)` : ""}${w.coverage && w.coverage.space > w.budget ? `. Já consultadas: ${w.coverage.seen} de ${w.coverage.space}; cada coleta faz até ${w.budget} (as mais baratas e vizinhas primeiro, depois o resto)` : ""}`)
      : kv("Datas flexíveis", w.flex_days ? `± ${w.flex_days} dia${w.flex_days > 1 ? "s" : ""}` : "só a data escolhida")}
    ${kv("Passageiros", `${w.adults} adulto${w.adults > 1 ? "s" : ""}`)}${kv("Classe", esc(CABINS[w.cabin] || w.cabin))}
    ${kv("Escalas", w.max_stops == null ? "qualquer" : w.max_stops === 0 ? "só direto" : `até ${w.max_stops}`)}
    ${kv("Coleta automática", esc(every))}${kv("Detalhar compra", w.booking_top ? `dos ${w.booking_top} mais baratos (só ida)` : "não")}
    ${kv("Comparar trechos separados", w.compare_separate ? "sim" : "não")}${kv("Situação", w.active ? (w.expired ? "viagem já passou" : "ativa") : "pausada")}
    ${kv("Última coleta", w.last_run_at ? esc(fmtStamp(w.last_run_at)) : "ainda não")}${kv("Próxima coleta", esc(nextText(w).replace("Próxima coleta ", "") || "—"))}</div></section>`;
}
const paneBusca = (w) => configPanel(w);

/* ---- <details> abertos sobrevivem a novas renderizações ---- */
document.addEventListener("toggle", (e) => {
  const d = e.target; if (!(d instanceof HTMLDetailsElement) || !d.dataset.k) return;
  d.open ? state.openDet.add(d.dataset.k) : state.openDet.delete(d.dataset.k);
}, true);
function restoreDetails() { document.querySelectorAll("details[data-k]").forEach((d) => { if (state.openDet.has(d.dataset.k)) d.open = true; }); }

/* ---------- ações ---------- */
async function guard(fn, okMsg) {
  try { await fn(); if (okMsg) toast(okMsg); }
  catch (e) {
    if (e.status === 401) return; // onLoggedOut já avisou e abriu o login
    let m = e.message;
    if (e.status === 409) m = "Conflito ao salvar (o arquivo mudou). Recarreguei os dados: tente de novo.";
    toast(esc(m), 9000);
    if (e.status === 409) { await reloadWatches().catch(() => {}); await reloadFavs().catch(() => {}); }
  }
}
async function reloadWatches() {
  if (!canWrite()) return;
  const f = await fetchWatchesFile(); state.sha = f.sha; state.watches = f.list; state.watchesLoaded = true; render();
}
const nextId = () => Math.max(0, ...state.watches.map((w) => +w.id || 0)) + 1;

/* ---------- voos favoritos ---------- */
const favCtx = new Map(); // id do favorito -> { w, c, o } do que está na tela (para gravar ao clicar na estrela)
const comboDates = (c) => (c.legs && c.legs.length ? c.legs.map((l) => l.date) : [c.depart_date, ...(c.return_date ? [c.return_date] : [])]);
const favIdOf = (w, c, o) => `${w.id}|${comboDates(c).join(",")}|${o.flight_numbers || ""}|${o.dep_time || ""}`;
function registerFav(w, c, o) { const id = favIdOf(w, c, o); favCtx.set(id, { w, c, o }); return id; }
const isFav = (id) => state.favs.some((f) => f.id === id);
const starBtn = (id) => `<button type="button" class="star" data-fav="${esc(id)}" aria-pressed="${isFav(id)}" title="${isFav(id) ? "Remover dos favoritos" : "Salvar nos favoritos"}" aria-label="${isFav(id) ? "Remover dos favoritos" : "Salvar nos favoritos"}">${icon("star")}<span>${isFav(id) ? "Favorito" : "Favoritar"}</span></button>`;
const OFFER_KEEP = ["airline", "operated_by", "flight_numbers", "origin", "destination", "dep_time", "arr_time", "duration_min", "stops", "layover_airports",
  "cabin", "aircraft", "overnight", "often_delayed", "co2_kg", "co2_diff_pct"];
function buildFav({ w, c, o }) {
  const offer = {}; for (const k of OFFER_KEEP) offer[k] = o[k] ?? null; offer.airline = o.airline || "";
  return { id: favIdOf(w, c, o), watch_id: w.id, saved_at: new Date().toISOString(), price: o.price, currency: w.currency || "BRL", dates: comboDates(c),
    trip: { origin: w.origin, destination: w.destination, legs: isMulti(w) ? w.legs.map((l) => ({ origin: l.origin, destination: l.destination })) : null, adults: w.adults || 1 }, offer };
}
async function toggleFav(id) {
  const cur = state.favs.find((f) => f.id === id);
  if (cur) {
    await saveFavs(state.favs.filter((f) => f.id !== id), "voo removido dos favoritos");
    state.lastRemoved = cur; render();
    toast(`Removido dos favoritos. <a href="#" data-undo-fav>Desfazer</a>`, 8000);
  } else {
    const ctx = favCtx.get(id); if (!ctx) return;
    if (state.favs.length >= 200) return toast("Limite de 200 favoritos. Remova algum para salvar outro.", 7000);
    await saveFavs([buildFav(ctx), ...state.favs], "voo salvo nos favoritos");
    render(); toast(`Salvo nos favoritos. <a href="#/favoritos">Ver favoritos</a>`, 6000);
  }
}
/** O que já se sabe do favorito agora: preço atual, histórico e se apareceu na última coleta. */
function favInfo(f, ws) {
  const srv = state.dashboard?.favorites?.[f.id] || null;
  const w = ws.find((x) => x.id === f.watch_id) || null;
  let live = null;
  if (w) {
    const c = w.combos.find((x) => comboDates(x).join(",") === f.dates.join(","));
    const o = c?.offers?.find((x) => x.flight_numbers === f.offer.flight_numbers && x.dep_time === f.offer.dep_time);
    if (c) live = { c, o: o || null };
  }
  const current = live?.o ? live.o.price : srv?.current ?? null;
  const seen = live ? !!live.o : srv ? srv.seen_latest : null;           // null = ainda sem coleta depois de salvar
  const searchedAt = live?.c.scraped_at || srv?.searched_at || null;
  const lastSeen = live?.o ? live.c.scraped_at : srv?.last_seen_at || null;
  return { w, current, seen, searchedAt, lastSeen, history: srv?.history || [], combo: live?.c || null };
}
const favDatesText = (f) => {
  if (f.trip.legs) return f.trip.legs.map((l, i) => `${l.origin}→${l.destination} ${fmtDayShort(f.dates[i])}`).join(" · ");
  return f.dates.length > 1 ? `Ida ${fmtDayShort(f.dates[0])} · Volta ${fmtDayShort(f.dates[1])}` : `Ida ${fmtDayShort(f.dates[0])}`;
};
const FAV_SORTS = { recent: "Mais recentes", price: "Menor preço", date: "Data da viagem" };
function favCard(f, ws) {
  const i = favInfo(f, ws), cur = f.currency, trip = { origin: f.trip.origin, destination: f.trip.destination, legs: f.trip.legs };
  const price = i.current ?? f.price, past = f.dates[0] < todayIso();
  const chips = [];
  if (past) chips.push(`<span class="chip warn">Já passou</span>`);
  if (!i.w) chips.push(`<span class="chip warn">${icon("alert")}Viagem removida: sem novas coletas</span>`);
  else if (i.seen === true) chips.push(`<span class="chip good">${icon("check")}Visto na última coleta (${esc(ago(i.searchedAt))})</span>`);
  else if (i.seen === false) chips.push(`<span class="chip warn" title="O voo pode ter esgotado ou saído dos mais baratos que o painel guarda.">${icon("alert")}Não apareceu na última coleta${i.lastSeen ? ` · visto ${esc(ago(i.lastSeen))}` : ""}</span>`);
  else chips.push(`<span class="chip">${icon("clock")}Aguardando a próxima coleta</span>`);
  const pkg = f.dates.length > 1 ? " (total da viagem inteira)" : "";
  const delta = i.current != null ? deltaHtml(i.current, f.price, cur, "vs. quando salvou") : "";
  const o = { ...f.offer, price };
  return `<section class="panel fav${past ? " past" : ""}" data-fav-card="${esc(f.id)}">
    <div class="fav-head"><div class="fav-t"><div class="fav-route">${esc(cityLine(trip))}</div>
        <div class="muted small">${esc(favDatesText(f))} · salvo em ${esc(fmtStamp(f.saved_at))}</div></div>
      ${spark(i.history)}
      <div class="fav-acts">${i.w ? `<button class="btn sm" data-favopen="${esc(f.id)}">Ver na viagem${icon("chev")}</button>` : ""}</div>
      <div class="chips fav-chips">${chips.join("")}</div>
      <div class="fav-price"><span class="muted small">Preço quando salvou${pkg}:</span> <b class="num">${money(f.price, cur)}</b>${delta ? ` <span class="price-d">${delta}</span>` : ""}</div></div>
    ${flightRow({ currency: cur, adults: f.trip.adults }, o, { key: `fav-${f.id}`, favId: f.id })}</section>`;
}
function renderFavs(ws) {
  const sorted = [...state.favs];
  const infos = new Map(sorted.map((f) => [f.id, favInfo(f, ws)]));
  const by = { recent: (a, b) => b.saved_at.localeCompare(a.saved_at), price: (a, b) => (infos.get(a.id).current ?? a.price) - (infos.get(b.id).current ?? b.price),
    date: (a, b) => a.dates[0].localeCompare(b.dates[0]) || a.saved_at.localeCompare(b.saved_at) };
  sorted.sort(by[state.favSort] || by.recent);
  $("#view").innerHTML = `<button class="btn ghost crumb" data-nav="/">${icon("left")}Todas as viagens</button>
    <div class="section-title">Voos favoritos <span class="count">${state.favs.length}</span>
      ${state.favs.length > 1 ? `<label class="fav-sort muted small">Ordenar <select data-favsort>${Object.entries(FAV_SORTS).map(([k, t]) => `<option value="${k}" ${state.favSort === k ? "selected" : ""}>${t}</option>`).join("")}</select></label>` : ""}</div>
    ${sorted.length ? `<p class="muted small">Seus voos marcados com a estrela. O preço é atualizado a cada coleta e comparado com o que estava quando você salvou.</p>
      <div class="favs">${sorted.map((f) => favCard(f, ws)).join("")}</div>`
    : `<div class="panel empty">${icon("star")}<h2>Nenhum favorito ainda</h2>
      <p class="muted">Na aba <b>Voos</b> de uma viagem, toque em <b>Favoritar</b> no voo que você gostou. Ele fica guardado aqui, com o preço de quando você salvou e a variação nas próximas coletas.</p>
      <button class="btn primary" data-nav="/">${icon("left")}Ver minhas viagens</button></div>`}`;
}

/* ---------- navegação (hash) ---------- */
function parseHash() {
  const m = location.hash.match(/^#\/viagem\/(\d+)(?:\/(\w+))?/);
  if (/^#\/favoritos/.test(location.hash)) return { type: "favs" };
  return m ? { type: "trip", id: +m[1], tab: m[2] || "resumo" } : { type: "list" };
}
const go = (path) => { location.hash = "#" + path; };
window.addEventListener("hashchange", () => {
  const prev = state.view; state.view = parseHash(); render();
  if (prev.type !== state.view.type || prev.id !== state.view.id) window.scrollTo(0, 0);
});

document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-act],[data-tab],[data-combo],[data-nav],[data-open-trip],[data-open],[data-more],[data-fav],[data-favopen],[data-undo-fav],[data-dates-all]"); if (!t) return;
  if (t.dataset.datesAll) { const k = +t.dataset.datesAll; state.datesAll[k] = !state.datesAll[k]; return render(); }
  if (t.dataset.fav) { e.preventDefault(); return void guard(() => toggleFav(t.dataset.fav)); }
  if (t.dataset.favopen) {
    const f = state.favs.find((x) => x.id === t.dataset.favopen), w = f && merged().find((x) => x.id === f.watch_id); if (!w) return;
    const i = w.combos.findIndex((c) => comboDates(c).join(",") === f.dates.join(",")); if (i >= 0) state.sel[w.id] = i;
    fltState(w).airline = ""; fltState(w).direct = false; fltState(w).sort = "price";
    return go(`/viagem/${w.id}/voos`);
  }
  if (t.hasAttribute("data-undo-fav")) {
    e.preventDefault(); const f = state.lastRemoved; if (!f) return; state.lastRemoved = null; t.closest(".toast")?.remove();
    return void guard(async () => { await saveFavs([f, ...state.favs.filter((x) => x.id !== f.id)], "voo restaurado nos favoritos"); render(); }, "Favorito restaurado.");
  }
  if (t.dataset.open === "login") { e.preventDefault(); openLogin(); return; }
  if (t.dataset.nav === "/favoritos" && state.view.type === "favs") return go("/"); // o botão Favoritos alterna
  if (t.dataset.nav) { if (t.tagName === "A") e.preventDefault(); return go(t.dataset.nav); }
  if (t.dataset.tab) return go(`/viagem/${state.view.id}/${t.dataset.tab}`);
  if (t.dataset.combo) { const [id, i] = t.dataset.combo.split(":").map(Number); state.sel[id] = i; return go(`/viagem/${id}/voos`); }
  if (t.dataset.more) { fltState({ id: +t.dataset.more }).limit += 12; return render(); }
  if (t.dataset.openTrip) return go(`/viagem/${t.dataset.openTrip}/resumo`);
  const act = t.dataset.act, id = +t.dataset.id;
  if (act === "new") return openWatchDialog(null);
  if (act === "once") return openWatchDialog(null, "once");
  if (act === "promote") return openWatchDialog(state.watches.find((w) => w.id === id), "promote");
  if (act === "clear-once") {
    const n = state.watches.filter((w) => w.once).length;
    if (n && confirm(`Remover ${n === 1 ? "a consulta pontual" : `as ${n} consultas pontuais`}? Os resultados somem do painel (os favoritos continuam).`)) guard(async () => {
      await saveWatches(state.watches.filter((w) => !w.once), "consultas pontuais removidas"); render();
    }, "Consultas pontuais removidas.");
    return;
  }
  if (act === "run") guard(() => dispatch([id]));
  if (act === "edit") openWatchDialog(state.watches.find((w) => w.id === id));
  if (act === "toggle") guard(async () => {
    const list = state.watches.map((w) => (w.id === id ? { ...w, active: !w.active } : w));
    await saveWatches(list, `busca ${id}: ${list.find((w) => w.id === id).active ? "retomada" : "pausada"}`); render();
  });
  if (act === "remove") {
    const w = state.watches.find((x) => x.id === id);
    if (confirm(`Remover a viagem ${routeText(w)}? O histórico dela some do painel.`)) guard(async () => {
      await saveWatches(state.watches.filter((x) => x.id !== id), `busca ${id} removida`); go("/"); render();
    }, "Viagem removida.");
  }
});
document.addEventListener("change", (e) => {
  const fs = e.target.closest("[data-favsort]"); if (fs) { state.favSort = fs.value; return render(); }
  const t = e.target.closest("[data-flt]"); if (!t || state.view.type !== "trip") return;
  const f = fltState({ id: state.view.id });
  const k = t.dataset.flt; f[k] = t.type === "checkbox" ? t.checked : t.value; f.limit = 12; render();
});
document.addEventListener("keydown", (e) => {
  if ((e.key === "Enter" || e.key === " ") && e.target.matches?.("[data-open-trip]")) { e.preventDefault(); e.target.click(); }
});
$("#btn-all").onclick = () => guard(() => dispatch([]));
$("#btn-new").onclick = () => openWatchDialog(null);
$("#btn-once").onclick = () => openWatchDialog(null, "once");
$("#btn-auth").onclick = async () => {
  if (!state.authed) return openLogin();
  try { await api("/api/logout", { method: "POST", body: "{}" }); } catch {}
  state.authed = false; state.me = null; state.watchesLoaded = false; state.sha = null; state.dashboard = null; state.watches = []; state.favs = []; state.favsLoaded = false; state.favSha = null; stopPolling();
  render(); toast("Você saiu.");
};

/* formulário de busca */
const dlgW = $("#dlg-watch"), formW = $("#form-watch");
const MAX_LEGS = 5;
/* ---------- campo de aeroporto com sugestões ---------- */
let acSeq = 0;
const pickerHtml = (cls, aria, ph) => { const id = `ac${++acSeq}`;
  return `<div class="ac-field"><div class="ac"><input class="${cls}" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="${id}" aria-label="${aria}" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="${ph}" required><ul class="ac-list" id="${id}" role="listbox" hidden></ul></div><small class="ac-hint"></small></div>`; };

function pickerSet(input, iata) {
  const hint = input.closest(".ac-field").querySelector(".ac-hint");
  input.value = iata || "";
  const d = iata ? Airports.describe(iata) : null;
  hint.textContent = d ? `${d.city}${d.country ? ` · ${d.country}` : ""}${d.name && !d.metro ? ` — ${d.name}` : ""}` : (iata ? cityOf(iata) : "");
  hint.classList.toggle("ok", !!d);
  hint.title = hint.textContent;   // o texto pode ser cortado em 2 linhas: o nome completo fica no balão
}
/** Valor final do campo: o código escolhido, ou o que dá para entender do texto digitado (null se nada). */
function pickerValue(input) {
  const code = Airports.resolve(input.value);
  if (code) { pickerSet(input, code); return code; }
  return /^[A-Za-z]{3}$/.test(input.value.trim()) ? input.value.trim().toUpperCase() : null;
}
function attachPicker(input) {
  if (input.dataset.ac) return;
  input.dataset.ac = "1";
  const list = input.closest(".ac").querySelector(".ac-list"), hint = input.closest(".ac-field").querySelector(".ac-hint");
  let items = [], act = -1;
  const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); act = -1; };
  const paint = () => {
    [...list.children].forEach((li, i) => li.setAttribute("aria-selected", i === act ? "true" : "false"));
    if (act >= 0 && list.children[act]) { input.setAttribute("aria-activedescendant", list.children[act].id); list.children[act].scrollIntoView({ block: "nearest" }); }
  };
  const render = () => {
    const q = input.value.trim();
    if (!q) return close();
    items = Airports.search(q, 8);
    list.innerHTML = items.length
      ? items.map((a, i) => `<li role="option" id="${list.id}-${i}" data-i="${i}" aria-selected="false"><b>${esc(a.iata)}</b><span class="ac-city">${esc(a.city)}</span><span class="ac-meta">${esc(a.name)}${a.country ? ` · ${esc(a.country)}` : ""}</span></li>`).join("")
      : `<li class="ac-none" role="option" aria-disabled="true">Nenhum aeroporto encontrado para “${esc(q)}”</li>`;
    act = items.length ? 0 : -1; list.hidden = false; input.setAttribute("aria-expanded", "true"); paint();
  };
  const choose = (a) => { pickerSet(input, a.iata); close(); input.dispatchEvent(new CustomEvent("airport-selected", { bubbles: true, detail: { iata: a.iata } })); };
  input.addEventListener("input", () => { hint.textContent = ""; hint.classList.remove("ok"); render(); });
  input.addEventListener("keydown", (e) => {
    if (list.hidden) { if (e.key === "ArrowDown" && input.value.trim()) { render(); e.preventDefault(); } return; }
    if (e.key === "ArrowDown") { act = Math.min(items.length - 1, act + 1); paint(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { act = Math.max(0, act - 1); paint(); e.preventDefault(); }
    else if (e.key === "Enter") { if (items[act]) choose(items[act]); e.preventDefault(); }
    else if (e.key === "Escape") { close(); e.preventDefault(); e.stopPropagation(); }   // Esc fecha só a lista, não o formulário
    else if (e.key === "Tab" && items[act]) choose(items[act]);
  });
  list.addEventListener("mousedown", (e) => { const li = e.target.closest("li[data-i]"); if (li) { e.preventDefault(); choose(items[+li.dataset.i]); } });
  input.addEventListener("blur", () => {
    setTimeout(close, 120);
    const t = input.value.trim();  // código digitado direto (ex.: "gru"): mostra a cidade
    if (/^[A-Za-z]{3}$/.test(t) && Airports.describe(t)) pickerSet(input, t.toUpperCase());
  });
  input.addEventListener("focus", () => { if (input.value.trim() && !hint.textContent) render(); });
}
const initPickers = (root) => root.querySelectorAll(".ac input").forEach(attachPicker);

const legRows = () => [...document.querySelectorAll("#legs .leg-row")];
function legRow(v = {}) {
  const row = document.createElement("div"); row.className = "leg-row";
  row.innerHTML = `${pickerHtml("leg-o", "Origem do trecho", "De onde? (cidade ou código)")}${pickerHtml("leg-d", "Destino do trecho", "Para onde? (cidade ou código)")}
    <label class="leg-d8 leg-datewrap"><span class="mini">Data</span><input class="leg-date" type="date" aria-label="Data do trecho" min="${todayIso()}"></label>
    <label class="leg-d8 leg-towrap" hidden><span class="mini">Até</span><input class="leg-to" type="date" aria-label="Data final do período do trecho" min="${todayIso()}"></label>
    <button type="button" class="btn sm leg-x" aria-label="Remover trecho">✕</button>`;
  initPickers(row);
  if (v.origin) pickerSet(row.querySelector(".leg-o"), v.origin);
  if (v.destination) pickerSet(row.querySelector(".leg-d"), v.destination);
  row.querySelector(".leg-date").value = v.date || "";
  row.querySelector(".leg-to").value = v.date_to || "";
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
  const prevDest = (last && pickerValue(last.querySelector(".leg-d"))) || "";
  const row = legRow({ origin: prevDest });
  if (prevDest) row.querySelector(".leg-o").dataset.auto = "1";
  $("#legs").appendChild(row); refreshLegs();
};
$("#legs").addEventListener("click", (e) => { const x = e.target.closest(".leg-x"); if (x && !x.disabled) { x.closest(".leg-row").remove(); refreshLegs(); } });
$("#legs").addEventListener("input", (e) => { if (e.target.classList.contains("leg-o")) e.target.dataset.auto = "0"; });
$("#legs").addEventListener("airport-selected", (e) => { // o destino de um trecho vira a origem do próximo (se você não mexeu nela)
  if (!e.target.classList.contains("leg-d")) return;
  const next = e.target.closest(".leg-row").nextElementSibling?.querySelector(".leg-o");
  if (next && (!next.value || next.dataset.auto === "1")) { pickerSet(next, e.detail.iata); next.dataset.auto = "1"; }
});
initPickers($("#simple-fields"));

/* ---- período de datas: contagem de consultas (espelha api/_lib/validate.js e flights/models.py) ---- */
const MAX_COMBOS = 2000, MAX_RANGE_DAYS = 60;
const addDays = (iso, n) => new Date(Date.parse(`${iso}T00:00:00Z`) + n * 86400000).toISOString().slice(0, 10);
const daysBetween = (a, b) => Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86400000);
function countCombos(starts, ends) {
  const spans = starts.map((s, i) => Array.from({ length: daysBetween(s, ends[i] || s) + 1 }, (_, k) => addDays(s, k)));
  let n = 0;
  const walk = (i, prev) => {
    if (i === spans.length) { n += 1; return n <= MAX_COMBOS; }
    for (const d of spans[i]) { if (prev && d < prev) continue; if (!walk(i + 1, d)) return false; }
    return true;
  };
  walk(0, null);
  return n;
}
const useRange = () => formW.use_range.checked;
/* datas do formulário no formato [{start, end}], uma por trecho; null se faltar data */
function formDates() {
  const kind = formW.trip.value, rg = useRange(), v = (el) => el.value || "";
  let items;
  if (kind === "multi") items = legRows().map((r) => ({ start: v(r.querySelector(".leg-date")), end: rg ? v(r.querySelector(".leg-to")) : "" }));
  else {
    items = [{ start: v(formW.depart_date), end: rg ? v(formW.depart_to) : "" }];
    if (kind === "round") items.push({ start: v(formW.return_date), end: rg ? v(formW.return_to) : "" });
  }
  return items;
}
function planInfo() {
  const box = $("#plan-info"), items = formDates();
  const set = (msg, bad) => { box.hidden = !msg; box.className = `plan-info${bad ? " bad" : ""}`; box.textContent = msg || ""; return !bad; };
  if (!useRange()) {
    const fx = +formW.flex_days.value || 0;
    return set(fx ? `Vai consultar ${2 * fx + 1} datas (± ${fx} dia${fx > 1 ? "s" : ""}) a cada coleta.` : "", false);
  }
  if (items.some((i) => !i.start)) return set("", false);
  for (const [n, i] of items.entries()) {
    const nome = formW.trip.value === "multi" ? `Trecho ${n + 1}` : n ? "Volta" : "Ida";
    if (i.end && i.end < i.start) return set(`${nome}: o fim do período é antes do início.`, true);
    if (i.end && daysBetween(i.start, i.end) + 1 > MAX_RANGE_DAYS) return set(`${nome}: o período pode ter no máximo ${MAX_RANGE_DAYS} dias.`, true);
  }
  const ends = items.map((i) => (i.end && i.end !== i.start ? i.end : null));
  if (!ends.some(Boolean)) return set("Preencha o fim do período em pelo menos um trecho (ou desmarque “Pesquisar um período”).", false);
  const starts = items.map((i) => i.start), n = countCombos(starts, ends), budget = +formW.budget.value || 40;
  const sep = formW.compare_separate.checked && formW.trip.value !== "oneway";
  if (n > MAX_COMBOS) return set(`O período gera mais de ${MAX_COMBOS} combinações de datas. Reduza os períodos (por exemplo, menos dias no trecho que mais varia).`, true);
  if (n * (sep ? 2 : 1) <= budget) return set(`Vai consultar as ${n} combinaç${n > 1 ? "ões" : "ão"} de datas a cada coleta${sep ? " (mais os trechos separados)" : ""}.`, false);
  const runs = Math.max(1, Math.ceil(n / Math.max(1, budget - Math.max(3, Math.floor(budget / 4)))));
  return set(`O período tem ${n} combinações. Para não sobrecarregar o Google, cada coleta consulta até ${budget}: começa por uma amostra espalhada e, nas seguintes, foca ao redor das mais baratas e cobre o resto (tudo visto em cerca de ${runs} coleta${runs > 1 ? "s" : ""}).`, false);
}
["input", "change"].forEach((ev) => formW.addEventListener(ev, (e) => { if (e.target.matches("[type=date], [name=flex_days], [name=use_range], [name=compare_separate], [name=trip], [name=budget]")) planInfo(); }));
$("#legs").addEventListener("input", planInfo);

function syncTrip() {
  const kind = formW.trip.value, multi = kind === "multi", round = kind === "round", rg = useRange();
  formW.querySelectorAll(".lbl").forEach((l) => { l.textContent = rg ? l.dataset.range : l.dataset.plain; });
  $("#range-row").hidden = multi || !rg; $("#return-to-wrap").hidden = !round;
  formW.depart_to.disabled = multi || !rg; formW.return_to.disabled = !round || !rg;
  $("#legs").classList.toggle("ranged", rg);
  legRows().forEach((r) => { r.querySelector(".leg-towrap").hidden = !rg; });
  $("#flex-wrap").hidden = rg; formW.flex_days.disabled = rg; $("#budget-wrap").hidden = !rg; formW.budget.disabled = !rg;
  planInfo();
  $("#simple-fields").hidden = multi; $("#legs-wrap").hidden = !multi; $("#return-wrap").hidden = !round;
  $("#cmp-wrap").hidden = kind === "oneway";
  for (const n of ["origin", "destination", "depart_date"]) { formW[n].disabled = multi; formW[n].required = !multi; }
  formW.return_date.disabled = !round; formW.return_date.required = round;
  legRows().forEach((r) => r.querySelectorAll("input").forEach((i) => { i.disabled = !multi; i.required = multi && !i.classList.contains("leg-to"); }));
}
formW.querySelectorAll("[name=trip]").forEach((r) => r.addEventListener("change", syncTrip));
formW.use_range.addEventListener("change", () => {
  if (useRange()) { // sugere um fim de período a partir do que já está preenchido
    const fill = (a, b) => { if (a.value && !b.value) b.value = addDays(a.value, 6); };
    if (formW.trip.value !== "multi") { fill(formW.depart_date, formW.depart_to); fill(formW.return_date, formW.return_to); }
  }
  syncTrip();
});

/* mode: "once" (consulta pontual), "promote" (pontual -> monitoramento) ou nada (viagem monitorada) */
function openWatchDialog(w, mode) {
  state.editingId = w ? w.id : null;
  state.dlgOnce = mode === "once" || (!!w?.once && mode !== "promote");
  formW.reset(); $("#watch-err").textContent = "";
  $("#dlg-watch-title").textContent = state.dlgOnce ? (w ? `Editar consulta pontual ${routeText(w)}` : "Consulta pontual")
    : mode === "promote" ? `Monitorar ${routeText(w)}` : w ? `Editar viagem ${routeText(w)}` : "Nova viagem";
  for (const id of ["#name-wrap", "#every-wrap", "#now-wrap"]) $(id).hidden = state.dlgOnce;
  $("#once-note").hidden = !state.dlgOnce;
  $("#watch-save").textContent = state.dlgOnce ? "Consultar agora" : "Salvar";
  formW.depart_date.min = todayIso(); formW.return_date.min = todayIso(); formW.depart_to.min = todayIso(); formW.return_to.min = todayIso();
  pickerSet(formW.origin, ""); pickerSet(formW.destination, "");
  setLegs(w && isMulti(w) ? w.legs : null);
  if (w) {
    formW.label.value = w.label || ""; formW.trip.value = isMulti(w) ? "multi" : w.return_date ? "round" : "oneway";
    if (!isMulti(w)) { pickerSet(formW.origin, w.origin); pickerSet(formW.destination, w.destination); formW.depart_date.value = w.depart_date; formW.return_date.value = w.return_date || ""; formW.depart_to.value = w.depart_to || ""; formW.return_to.value = w.return_to || ""; }
    formW.use_range.checked = hasRange(w); formW.budget.value = String(w.budget || 40);
    formW.adults.value = w.adults; formW.cabin.value = w.cabin; formW.max_stops.value = w.max_stops ?? "";
    formW.flex_days.value = String(w.flex_days || 0); formW.every_hours.value = w.every_hours ? String(w.every_hours) : (mode === "promote" ? "24" : "");
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
    id: state.editingId ?? nextId(), label: state.dlgOnce ? null : (f.get("label") || "").trim() || null,
    origin: "", destination: "", depart_date: "", return_date: null, legs: null,
    flex_days: useRange() ? 0 : +f.get("flex_days"), budget: useRange() ? +f.get("budget") : 40, adults: +f.get("adults"), cabin: f.get("cabin"),
    max_stops: f.get("max_stops") === "" ? null : +f.get("max_stops"), currency: "BRL",
    every_hours: !state.dlgOnce && f.get("every_hours") ? +f.get("every_hours") : null, once: state.dlgOnce,
    compare_separate: kind !== "oneway" && formW.compare_separate.checked, booking_top: +f.get("booking_top"),
    active: state.editingId != null ? (state.watches.find((w) => w.id === state.editingId)?.active ?? true) : true,
  };
  if (kind === "multi") {
    const legs = legRows().map((r) => ({ origin: pickerValue(r.querySelector(".leg-o")), destination: pickerValue(r.querySelector(".leg-d")), date: r.querySelector(".leg-date").value,
      date_to: useRange() ? r.querySelector(".leg-to").value || null : null }));
    if (legs.length < 2) return fail("Multi-destino precisa de pelo menos 2 trechos.");
    for (const [i, l] of legs.entries()) {
      if (!iata(l.origin)) return fail(`Trecho ${i + 1}: escolha o aeroporto de origem na lista (digite a cidade ou o código).`);
      if (!iata(l.destination)) return fail(`Trecho ${i + 1}: escolha o aeroporto de destino na lista (digite a cidade ou o código).`);
      if (l.origin === l.destination) return fail(`Trecho ${i + 1}: origem e destino não podem ser iguais.`);
      if (i && l.date < legs[i - 1].date) return fail(`Trecho ${i + 1}: a data não pode ser antes do trecho anterior.`);
    }
    Object.assign(item, { legs, origin: legs[0].origin, destination: legs[0].destination, depart_date: legs[0].date });
  } else {
    Object.assign(item, { origin: pickerValue(formW.origin), destination: pickerValue(formW.destination),
      depart_date: f.get("depart_date"), return_date: kind === "round" ? f.get("return_date") : null,
      depart_to: useRange() ? f.get("depart_to") || null : null, return_to: useRange() && kind === "round" ? f.get("return_to") || null : null });
    if (!iata(item.origin)) return fail("Escolha o aeroporto de origem na lista (digite a cidade ou o código).");
    if (!iata(item.destination)) return fail("Escolha o aeroporto de destino na lista (digite a cidade ou o código).");
    if (item.origin === item.destination) return fail("Origem e destino não podem ser iguais.");
    if (item.return_date && item.return_date < item.depart_date) return fail("A volta não pode ser antes da ida.");
  }
  if (useRange()) {
    const ends = kind === "multi" ? item.legs.map((l) => l.date_to) : [item.depart_to, item.return_to];
    if (!ends.some((x) => x && x !== "")) return fail("Informe o fim do período em pelo menos um trecho, ou desmarque “Pesquisar um período de datas”.");
    if (!planInfo()) return fail($("#plan-info").textContent);
  }
  err.textContent = "";
  const runNow = state.dlgOnce || f.get("run_now") === "on";
  $("#watch-save").disabled = true;
  await guard(async () => {
    const list = state.editingId != null ? state.watches.map((w) => (w.id === item.id ? item : w)) : [...state.watches, item];
    await saveWatches(list, `${item.once ? "consulta pontual" : "busca"} ${item.id}: ${routeText(item)} ${state.editingId != null ? "editada" : "criada"}`);
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
  if (canWrite() && !state.favsLoaded) { try { await reloadFavs(); } catch (e) { if (e.status !== 401) toast(`Não consegui ler os favoritos: ${esc(e.message)}`, 8000); } }
  if (canWrite() && !state.watchesLoaded) { try { await reloadWatches(); } catch (e) { if (e.status !== 401) toast(`Não consegui ler as buscas cadastradas: ${esc(e.message)}`, 8000); } }
  render();
}
(async function init() {
  state.view = parseHash();
  Airports.load().then(() => render());   // a lista completa de aeroportos (cidades por extenso em qualquer viagem)
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
