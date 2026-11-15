import { test, beforeEach, afterEach, before, after } from "node:test";
import assert from "node:assert/strict";
import { createServer, listen } from "./server.mjs";
import { makeFakeGithub } from "./fake-github.mjs";
import { loginLimiter, makeSession, verifySession, loadConfig } from "../api/_lib/auth.js";
import { _clearBranchCache } from "../api/_lib/github.js";
import { normalizeWatch, normalizeWatches } from "../api/_lib/validate.js";

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
for (const [method, path, body] of [["GET", "/api/me"], ["GET", "/api/dashboard"], ["GET", "/api/watches"], ["PUT", "/api/watches", { list: [] }], ["POST", "/api/dispatch", { ids: [] }], ["GET", "/api/run-status"]]) {
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
    for (const [m, p, b] of [["GET", "/api/dashboard"], ["GET", "/api/watches"], ["PUT", "/api/watches", { list: [] }], ["POST", "/api/dispatch", { ids: [] }], ["GET", "/api/run-status"]]) {
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
