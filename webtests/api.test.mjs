import { test, beforeEach, afterEach, before, after } from "node:test";
import assert from "node:assert/strict";
import { createServer, listen } from "./server.mjs";
import { makeFakeGithub } from "./fake-github.mjs";
import { loginLimiter, makeSession, verifySession, loadConfig } from "../api/_lib/auth.js";
import { _clearBranchCache } from "../api/_lib/github.js";
import { normalizeWatch, normalizeWatches, countCombos } from "../api/_lib/validate.js";

const GH_TOKEN = "github_pat_SEGREDO_DO_SERVIDOR_1234567890";
const PASSWORD = "uma-senha-bem-longa-123";
const SECRET = "x".repeat(48);
const ENV = { APP_PASSWORD: PASSWORD, SESSION_SECRET: SECRET, GH_TOKEN, CODE_REPO: "Dusax/flight", DATA_REPO: "Dusax/flight-data" };

let server, base, gh;
const savedEnv = { ...process.env };
const setEnv = (e) => { for (const k of Object.keys(ENV)) delete process.env[k]; Object.assign(process.env, e); };

before(async () => { server = createServer(); base = await listen(server); });
after(() => server.close());
beforeEach(() => {
  setEnv(ENV); loginLimiter._reset(); _clearBranchCache();
  gh = makeFakeGithub({ token: GH_TOKEN }); gh.install();
});
afterEach(() => { gh.uninstall(); });

const J = { "content-type": "application/json" };
let ipN = 0;
const nextIp = () => `10.0.0.${++ipN}`;
async function call(path, { method = "GET", body, cookie, headers = {}, ip } = {}) {
  const r = await fetch(base + path, {
    method, redirect: "manual",
    headers: { ...(body !== undefined ? J : {}), ...(cookie ? { cookie } : {}), "x-forwarded-for": ip || nextIp(), ...headers },
    body: body !== undefined ? (typeof body === "string" ? body : JSON.stringify(body)) : undefined,
  });
  const text = await r.text();
  let json = null; try { json = JSON.parse(text); } catch { /* não é JSON */ }
  return { status: r.status, headers: r.headers, text, json };
}
async function login(extra = {}) {
  const r = await call("/api/login", { method: "POST", body: { password: PASSWORD }, ...extra });
  assert.equal(r.status, 200, r.text);
  return r.headers.get("set-cookie").split(";")[0];
}

// ---------- configuração ----------
test("sem configuração o servidor diz o que falta (só nomes, nunca valores)", async () => {
  setEnv({});
  const r = await call("/api/me");
  assert.equal(r.status, 503);
  assert.deepEqual(r.json.missing.sort(), ["APP_PASSWORD", "CODE_REPO", "GH_TOKEN", "SESSION_SECRET"]);
});
test("senha ou segredo fracos são recusados", async () => {
  setEnv({ ...ENV, APP_PASSWORD: "curta", SESSION_SECRET: "curto" });
  const r = await call("/api/login", { method: "POST", body: { password: "curta" } });
  assert.equal(r.status, 503);
  assert.ok(r.json.problems.some((p) => p.includes("APP_PASSWORD")) && r.json.problems.some((p) => p.includes("SESSION_SECRET")));
});
test("DATA_REPO não pode ser o mesmo do código; default é <código>-data", () => {
  assert.ok(loadConfig({ ...ENV, DATA_REPO: "dusax/flight" }).problems.some((p) => p.includes("outro repositório")));
  assert.equal(loadConfig({ ...ENV, DATA_REPO: "" }).dataRepo, "Dusax/flight-data");
});

// ---------- login e sessão ----------
test("senha errada: 401, sem cookie", async () => {
  const r = await call("/api/login", { method: "POST", body: { password: "errada-errada-123" } });
  assert.equal(r.status, 401);
  assert.equal(r.headers.get("set-cookie"), null);
});
test("login correto: cookie HttpOnly + SameSite=Strict (e Secure/__Host- em https)", async () => {
  const plain = await call("/api/login", { method: "POST", body: { password: PASSWORD } });
  const c = plain.headers.get("set-cookie");
  assert.match(c, /^fs=v1\./); assert.match(c, /HttpOnly/); assert.match(c, /SameSite=Strict/); assert.match(c, /Path=\//); assert.doesNotMatch(c, /Secure/);
  const https = await call("/api/login", { method: "POST", body: { password: PASSWORD }, headers: { "x-forwarded-proto": "https" } });
  const cs = https.headers.get("set-cookie");
  assert.match(cs, /^__Host-fs=v1\./); assert.match(cs, /; Secure/); assert.doesNotMatch(cs, /Domain=/);
});
test("depois de 5 erros o IP é bloqueado, mesmo com a senha certa", async () => {
  const ip = "9.9.9.9";
  for (let i = 0; i < 5; i++) assert.equal((await call("/api/login", { method: "POST", body: { password: "errada-errada-123" }, ip })).status, 401);
  const r = await call("/api/login", { method: "POST", body: { password: PASSWORD }, ip });
  assert.equal(r.status, 429); assert.ok(Number(r.headers.get("retry-after")) > 0);
  assert.equal((await call("/api/login", { method: "POST", body: { password: PASSWORD }, ip: "9.9.9.10" })).status, 200); // outro IP segue livre
});
test("cookie adulterado, expirado ou de outra senha não vale", async () => {
  const cfg = loadConfig();
  const good = makeSession(cfg);
  assert.ok(verifySession(cfg, good));
  assert.ok(!verifySession(cfg, good.slice(0, -2) + "xx"));
  assert.ok(!verifySession(cfg, good.replace(/^v1\.\d+/, "v1.99999999999")));
  assert.ok(!verifySession(cfg, makeSession(cfg, Date.now() - 8 * 24 * 3600 * 1000)));        // expirada
  assert.ok(!verifySession({ ...cfg, password: "outra-senha-bem-longa-9" }, good));            // trocar a senha derruba as sessões
  assert.ok(!verifySession(cfg, "lixo")); assert.ok(!verifySession(cfg, undefined));
  const r = await call("/api/me", { cookie: `fs=${good.slice(0, -3)}abc` });
  assert.equal(r.status, 401);
});
test("logout limpa o cookie", async () => {
  const cookie = await login();
  const r = await call("/api/logout", { method: "POST", body: {}, cookie });
  assert.equal(r.status, 200); assert.match(r.headers.get("set-cookie"), /Max-Age=0/);
});
test("/api/me devolve os repositórios (nunca o token)", async () => {
  const r = await call("/api/me", { cookie: await login() });
  assert.deepEqual(r.json, { ok: true, codeRepo: "Dusax/flight", dataRepo: "Dusax/flight-data" });
});

// ---------- toda rota de dados exige login ----------
for (const [method, path, body] of [["GET", "/api/me"], ["GET", "/api/dashboard"], ["GET", "/api/watches"], ["PUT", "/api/watches", { list: [] }], ["GET", "/api/favorites"], ["PUT", "/api/favorites", { list: [] }], ["POST", "/api/dispatch", { ids: [] }], ["GET", "/api/run-status"]]) {
  test(`sem sessão: ${method} ${path} -> 401 e nada vai ao GitHub`, async () => {
    const r = await call(path, { method, body });
    assert.equal(r.status, 401);
    assert.equal(gh.calls.length, 0);
  });
}

// ---------- CSRF ----------
test("requisição de outra origem é recusada", async () => {
  const cookie = await login();
  assert.equal((await call("/api/dispatch", { method: "POST", body: { ids: [] }, cookie, headers: { origin: "https://evil.example.com" } })).status, 403);
  assert.equal((await call("/api/dispatch", { method: "POST", body: { ids: [] }, cookie, headers: { "sec-fetch-site": "cross-site" } })).status, 403);
  assert.equal((await call("/api/login", { method: "POST", body: { password: PASSWORD }, headers: { origin: "https://evil.example.com" } })).status, 403);
  assert.equal((await call("/api/dispatch", { method: "POST", body: "ids=1", cookie, headers: { "content-type": "application/x-www-form-urlencoded" } })).status, 415);
  assert.equal(gh.dispatches.length, 0);
});

// ---------- dados ----------
test("dashboard: repassa o JSON do repo privado; inexistente = vazio", async () => {
  const cookie = await login();
  assert.deepEqual((await call("/api/dashboard", { cookie })).json, { generated_at: null, watches: [] });
  gh.putFile("dashboard.json", JSON.stringify({ generated_at: "2026-11-10T00:00:00Z", watches: [{ id: 1 }] }));
  const r = await call("/api/dashboard", { cookie });
  assert.equal(r.json.watches[0].id, 1);
  assert.ok(gh.calls.every((c) => c.auth === `Bearer ${GH_TOKEN}` && c.path.toLowerCase().startsWith("/repos/dusax/flight-data")));
});

const watch = (over = {}) => ({ id: 1, label: "Lisboa", origin: "gru", destination: "lis", depart_date: "2027-02-10", return_date: "2027-02-20", legs: null, flex_days: 1, adults: 2, cabin: "business", max_stops: 1, currency: "BRL", every_hours: 12, compare_separate: true, booking_top: 3, active: true, ...over });

test("watches: lista vazia -> cria o arquivo; depois usa o sha; conflito vira 409", async () => {
  const cookie = await login();
  const g = await call("/api/watches", { cookie });
  assert.deepEqual(g.json, { sha: null, list: [] });
  const put1 = await call("/api/watches", { method: "PUT", body: { list: [watch()], message: "criada" }, cookie });
  assert.equal(put1.status, 200, put1.text);
  assert.equal(put1.json.list[0].origin, "GRU");            // normalizado
  const saved = JSON.parse(gh.files.get("watches.json").content);
  assert.equal(saved[0].destination, "LIS");
  const g2 = await call("/api/watches", { cookie });
  assert.equal(g2.json.sha, put1.json.sha);
  const put2 = await call("/api/watches", { method: "PUT", body: { list: [], sha: put1.json.sha }, cookie });
  assert.equal(put2.status, 200);
  const stale = await call("/api/watches", { method: "PUT", body: { list: [watch()], sha: put1.json.sha }, cookie });
  assert.equal(stale.status, 409); assert.equal(stale.json.conflict, true);
  const noSha = await call("/api/watches", { method: "PUT", body: { list: [watch()] }, cookie });
  assert.equal(noSha.status, 409); // arquivo existe e não mandou sha
});
test("watches: campos desconhecidos são descartados e o que é inválido é recusado", async () => {
  const cookie = await login();
  const ok = await call("/api/watches", { method: "PUT", body: { list: [watch({ evil: "<script>", __proto__x: 1 })] }, cookie });
  assert.equal(ok.status, 200);
  assert.ok(!("evil" in JSON.parse(gh.files.get("watches.json").content)[0]));
  for (const [over, msg] of [[{ origin: "SAOP" }, "IATA"], [{ destination: "GRU", origin: "GRU" }, "iguais"], [{ depart_date: "2027-13-40" }, "ida inválida"],
    [{ return_date: "2027-02-01" }, "antes da ida"], [{ adults: 99 }, "adultos"], [{ cabin: "vip" }, "classe"], [{ flex_days: 30 }, "flexíveis"],
    [{ every_hours: 0 }, "frequência"], [{ booking_top: 50 }, "detalhar"], [{ label: "x".repeat(61) }, "60"], [{ id: "1" }, "id"]]) {
    const r = await call("/api/watches", { method: "PUT", body: { list: [watch(over)], sha: undefined }, cookie });
    assert.equal(r.status, 400, JSON.stringify(over)); assert.match(r.json.error, new RegExp(msg), JSON.stringify(over));
  }
  assert.equal((await call("/api/watches", { method: "PUT", body: { list: [watch(), watch()] }, cookie })).status, 400); // id repetido
  assert.equal((await call("/api/watches", { method: "PUT", body: { list: "x" }, cookie })).status, 400);
  assert.equal((await call("/api/watches", { method: "PUT", body: { list: [], sha: "../../x" }, cookie })).status, 400);
});
test("normalizeWatch: multi-destino válido e inválido", () => {
  const legs = [{ origin: "GRU", destination: "LIS", date: "2027-02-10" }, { origin: "lis", destination: "mad", date: "2027-02-17" }, { origin: "MAD", destination: "GRU", date: "2027-02-24" }];
  const n = normalizeWatch({ ...watch({ return_date: null }), legs });
  assert.equal(n.legs[1].origin, "LIS");
  assert.throws(() => normalizeWatch({ ...watch({ return_date: null }), legs: legs.slice(0, 1) }), /2 a 5/);
  assert.throws(() => normalizeWatch({ ...watch({ return_date: null }), legs: [legs[0], { ...legs[1], date: "2027-02-01" }] }), /antes do trecho anterior/);
  assert.throws(() => normalizeWatch({ ...watch({ return_date: null }), legs: [...legs, ...legs, ...legs] }), /2 a 5/);
  assert.throws(() => normalizeWatch({ ...watch({ return_date: null, origin: "FCO" }), legs }), /1º trecho/);
  assert.throws(() => normalizeWatches(Array.from({ length: 61 }, (_, i) => watch({ id: i + 1 }))), /no máximo/);
});

test("dispatch: usa a branch padrão do repo do código e valida os ids", async () => {
  const cookie = await login();
  assert.equal((await call("/api/dispatch", { method: "POST", body: { ids: [1, 2] }, cookie })).status, 200);
  assert.deepEqual(gh.dispatches[0], { ref: "ccr-main", inputs: { ids: "1,2" } });
  assert.equal((await call("/api/dispatch", { method: "POST", body: {}, cookie })).status, 200);
  assert.deepEqual(gh.dispatches[1].inputs, { ids: "" });
  for (const ids of [["1; rm -rf"], [-1], [1.5], "1,2", Array(61).fill(1)]) assert.equal((await call("/api/dispatch", { method: "POST", body: { ids }, cookie })).status, 400, JSON.stringify(ids));
  assert.equal(gh.dispatches.length, 2);
});
test("run-status devolve só os campos usados", async () => {
  const cookie = await login();
  assert.deepEqual((await call("/api/run-status", { cookie })).json, { run: null });
  gh.run = { status: "completed", conclusion: "success", updated_at: "2026-11-10T10:00:00Z", html_url: "https://github.com/x", actor: { login: "dusax" }, head_commit: { message: "segredo" } };
  const r = await call("/api/run-status", { cookie });
  assert.deepEqual(Object.keys(r.json.run).sort(), ["conclusion", "html_url", "status", "updated_at"]);
});

test("token sem acesso ao repo de dados vira erro claro, não 'nenhuma busca'", async () => {
  const cookie = await login();
  gh.dataRepo = "dusax/outro-repo"; // o repo configurado deixa de existir para o token
  for (const p of ["/api/dashboard", "/api/watches"]) {
    const r = await call(p, { cookie });
    assert.equal(r.status, 502, p); assert.match(r.json.error, /repositório de dados|DATA_REPO/i);
  }
});

// ---------- o token nunca sai do servidor ----------
test("erros do GitHub não vazam detalhes nem o token", async () => {
  const cookie = await login();
  for (const status of [401, 403, 404, 500]) {
    gh.failWith = status;
    for (const [m, p, b] of [["GET", "/api/dashboard"], ["GET", "/api/watches"], ["PUT", "/api/watches", { list: [] }], ["GET", "/api/favorites"], ["PUT", "/api/favorites", { list: [] }], ["POST", "/api/dispatch", { ids: [] }], ["GET", "/api/run-status"]]) {
      const r = await call(p, { method: m, body: b, cookie });
      assert.ok(r.status >= 400, `${m} ${p} ${status}`);
      assert.ok(!r.text.includes("SEGREDO-INTERNO") && !r.text.includes("ghp_vazou") && !r.text.includes(GH_TOKEN), `${m} ${p}: ${r.text}`);
    }
  }
});
test("o token do GitHub não aparece em nenhuma resposta nem cabeçalho", async () => {
  const cookie = await login();
  gh.putFile("dashboard.json", "{}");
  const seen = [];
  for (const [m, p, b] of [["GET", "/api/me"], ["GET", "/api/dashboard"], ["GET", "/api/watches"], ["PUT", "/api/watches", { list: [watch()] }], ["POST", "/api/dispatch", { ids: [1] }], ["GET", "/api/run-status"], ["POST", "/api/logout", {}]]) {
    const r = await call(p, { method: m, body: b, cookie });
    seen.push(r.text, ...[...r.headers.values()]);
  }
  assert.ok(!seen.join("\n").includes(GH_TOKEN) && !seen.join("\n").includes(PASSWORD) && !seen.join("\n").includes(SECRET));
});
test("o servidor só fala com os repositórios configurados", async () => {
  const cookie = await login();
  await call("/api/dashboard", { cookie }); await call("/api/watches", { cookie }); await call("/api/dispatch", { method: "POST", body: { ids: [] }, cookie }); await call("/api/run-status", { cookie });
  assert.ok(gh.calls.every((c) => /^\/repos\/dusax\/flight(-data)?(\/|$)/i.test(c.path)));
});
test("respostas da API não são cacheáveis; a página tem CSP sem conexões externas", async () => {
  const cookie = await login();
  assert.equal((await call("/api/me", { cookie })).headers.get("cache-control"), "no-store");
  const page = await fetch(base + "/");
  const csp = page.headers.get("content-security-policy");
  assert.match(csp, /connect-src 'self'(;|$)/); assert.doesNotMatch(csp, /github/);
  assert.match(csp, /style-src 'self'(;|$)/); assert.doesNotMatch(csp, /style-src[^;]*unsafe-inline/);          // nenhum estilo embutido
  assert.match(csp, /script-src 'self'(;|$)/); assert.doesNotMatch(csp, /script-src[^;]*unsafe-inline/);   // nenhum script embutido roda
  assert.equal(page.headers.get("x-robots-tag"), "noindex, nofollow");
  const html = await page.text();
  assert.ok(!/<script(?![^>]*\bsrc=)[^>]*>/.test(html), "sem <script> embutido");
  assert.ok(!/ on[a-z]+\s*=/.test(html), "sem manipuladores de evento inline");
  const css = await (await fetch(base + "/style.css")).text();
  assert.ok(css.length > 1000 && !/<style/.test(html), "estilos vêm de style.css");
  const app = await (await fetch(base + "/app.js")).text();
  assert.ok(!/github_pat_|api\.github\.com/.test(html + app), "a página não deve mais conhecer o GitHub");
  assert.ok(!/\sstyle\s*=\s*["']/.test(html + app), "sem atributo style= (a CSP bloquearia)");
});

test("normalizeWatch: período de datas (ida, volta e por trecho) com os dois extremos", () => {
  const one = watch({ flex_days: 0, return_date: null }); // 2027-02-10, só ida
  const w = normalizeWatch({ ...watch({ flex_days: 0 }), depart_to: "2027-02-12", return_to: "2027-02-22" }); // ida 10-12, volta 20-22
  assert.equal(w.depart_to, "2027-02-12"); assert.equal(w.return_to, "2027-02-22"); assert.equal(w.flex_days, 0);
  assert.equal(countCombos(["2027-02-10", "2027-02-20"], ["2027-02-12", "2027-02-22"]), 9); // 3 idas x 3 voltas
  assert.equal(normalizeWatch({ ...one, depart_to: "2027-02-10" }).depart_to, null); // fim = início: sem período
  const legs = [{ origin: "GRU", destination: "ROM", date: "2027-03-10", date_to: "2027-03-12" }, { origin: "ROM", destination: "VCE", date: "2027-03-15", date_to: "2027-03-16" }];
  const m = normalizeWatch({ ...one, origin: "gru", destination: "rom", depart_date: "2027-03-10", legs });
  assert.deepEqual(m.legs.map((l) => l.date_to), ["2027-03-12", "2027-03-16"]); assert.equal(m.depart_to, "2027-03-12");
  assert.equal(countCombos(m.legs.map((l) => l.date), m.legs.map((l) => l.date_to)), 6);
});

test("normalizeWatch: recusa período inválido, grande demais ou junto com ± dias", () => {
  const one = watch({ flex_days: 0, return_date: null });
  assert.throws(() => normalizeWatch({ ...one, depart_to: "2027-02-01" }), /antes do início/);
  assert.throws(() => normalizeWatch({ ...one, depart_to: "2027-06-30" }), /no máximo 60 dias/);
  assert.throws(() => normalizeWatch({ ...one, depart_to: "2027-02-15", flex_days: 2 }), /não os dois/);
  // período grande não é recusado: cada coleta consulta só `budget` combinações (o resto fica para as próximas)
  assert.equal(normalizeWatch({ ...one, return_date: "2027-03-01", depart_to: "2027-03-10", return_to: "2027-04-15" }).budget, 40);
  const big = { ...one, origin: "gru", destination: "rom", depart_date: "2027-03-01", depart_to: "2027-03-30",
    legs: [1, 2, 3].map((i) => ({ origin: i === 1 ? "GRU" : i === 2 ? "ROM" : "VCE", destination: i === 1 ? "ROM" : i === 2 ? "VCE" : "GRU", date: `2027-03-0${i}`, date_to: `2027-03-${20 + i}` })) };
  assert.throws(() => normalizeWatch(big), /mais de 2000 combinações/);   // 3 trechos de ~20 dias cada: espaço enorme
  assert.throws(() => normalizeWatch({ ...one, budget: 5 }), /consultas por coleta/);
  assert.throws(() => normalizeWatch({ ...one, budget: 500 }), /consultas por coleta/);
  assert.equal(normalizeWatch({ ...one, budget: 60 }).budget, 60);
  assert.throws(() => normalizeWatch({ ...one, depart_to: "15/02/2027" }), /inválido/);
});

// ---------- favoritos ----------
const fav = (over = {}) => ({ id: "1|2027-02-10,2027-02-20|LA 8084|2027-02-10T22:10", watch_id: 1, saved_at: "2026-11-16T12:00:00.000Z", price: 4321.5, currency: "BRL",
  dates: ["2027-02-10", "2027-02-20"], trip: { origin: "gru", destination: "lis", legs: null, adults: 2 },
  offer: { airline: "LATAM", operated_by: null, flight_numbers: "LA 8084", origin: "GRU", destination: "LIS", dep_time: "2027-02-10T22:10", arr_time: "2027-02-11T12:00",
    duration_min: 590, stops: 0, layover_airports: null, cabin: "Economy", aircraft: "Boeing 787", overnight: true, often_delayed: false, co2_kg: 600, co2_diff_pct: -5 }, ...over });

test("favoritos: cria o arquivo, usa o sha, descarta campos desconhecidos e recusa conflito", async () => {
  const cookie = await login();
  assert.deepEqual((await call("/api/favorites", { cookie })).json, { sha: null, list: [] });
  const evil = fav(); evil.evil = "<script>"; evil.offer.extra = 1;
  const put1 = await call("/api/favorites", { method: "PUT", body: { list: [evil], message: "favoritou" }, cookie });
  assert.equal(put1.status, 200, put1.text);
  const saved = JSON.parse(gh.files.get("favorites.json").content);
  assert.equal(saved[0].trip.origin, "GRU"); assert.ok(!("evil" in saved[0]) && !("extra" in saved[0].offer));
  assert.equal((await call("/api/favorites", { cookie })).json.sha, put1.json.sha);
  const stale = await call("/api/favorites", { method: "PUT", body: { list: [], sha: "0".repeat(40) }, cookie });
  assert.equal(stale.status, 409);
  assert.equal((await call("/api/favorites", { method: "PUT", body: { list: [], sha: put1.json.sha }, cookie })).status, 200);
  assert.ok(gh.calls.every((c) => c.path.toLowerCase().startsWith("/repos/dusax/flight-data")));
});

test("favoritos: dados inválidos são recusados", async () => {
  const cookie = await login();
  const bad = [[{ id: "" }, "id"], [{ watch_id: 0 }, "viagem"], [{ saved_at: "ontem" }, "gravação"], [{ price: -1 }, "preço"], [{ price: "9" }, "preço"],
    [{ dates: [] }, "datas"], [{ dates: ["2027-13-40"] }, "datas"], [{ trip: null }, "viagem do favorito"], [{ trip: { origin: "SAOP", destination: "LIS" } }, "IATA"],
    [{ offer: null }, "voo"], [{ offer: { ...fav().offer, stops: 99 } }, "escalas"], [{ offer: { ...fav().offer, airline: "x".repeat(81) } }, "companhia"],
    [{ offer: { ...fav().offer, dep_time: "amanhã" } }, "saída"], [{ currency: "REAIS" }, "moeda"]];
  for (const [over, msg] of bad) {
    const r = await call("/api/favorites", { method: "PUT", body: { list: [fav(over)] }, cookie });
    assert.equal(r.status, 400, JSON.stringify(over)); assert.match(r.json.error, new RegExp(msg), r.json.error);
  }
  assert.equal((await call("/api/favorites", { method: "PUT", body: { list: [fav(), fav()] }, cookie })).status, 400); // repetido
  assert.equal((await call("/api/favorites", { method: "PUT", body: { list: Array.from({ length: 201 }, (_, i) => fav({ id: `f${i}` })) }, cookie })).status, 400);
  const ok = await call("/api/favorites", { method: "PUT", body: { list: [fav({ trip: { origin: "GRU", destination: "LIS", adults: 1, legs: [{ origin: "GRU", destination: "ROM" }, { origin: "ROM", destination: "LIS" }] }, dates: ["2027-02-10", "2027-02-15"] })] }, cookie });
  assert.equal(ok.status, 200, ok.text);
});
