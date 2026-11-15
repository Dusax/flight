// Login por senha única + sessão em cookie assinado (HMAC). Sem banco de dados.
//
// Variáveis de ambiente (Vercel -> Settings -> Environment Variables):
//   APP_PASSWORD    senha de acesso (mín. 12 caracteres)
//   SESSION_SECRET  segredo aleatório para assinar a sessão (mín. 32 caracteres)
//   GH_TOKEN        token fine-grained do GitHub (fica só no servidor)
//   CODE_REPO       dono/repositório do código (default: o repositório ligado à Vercel)
//   DATA_REPO       dono/repositório PRIVADO dos dados (default: <CODE_REPO>-data)
import crypto from "node:crypto";
import { send, clientIp } from "./http.js";

export const SESSION_TTL_S = 7 * 24 * 3600;
const REPO_RE = /^[\w.-]+\/[\w.-]+$/;

export function loadConfig(env = process.env) {
  const missing = [], problems = [];
  const password = env.APP_PASSWORD || "", secret = env.SESSION_SECRET || "", ghToken = env.GH_TOKEN || "";
  if (!password) missing.push("APP_PASSWORD");
  else if (password.length < 12) problems.push("APP_PASSWORD precisa ter pelo menos 12 caracteres");
  if (!secret) missing.push("SESSION_SECRET");
  else if (secret.length < 32) problems.push("SESSION_SECRET precisa ter pelo menos 32 caracteres");
  if (!ghToken) missing.push("GH_TOKEN");
  const codeRepo = env.CODE_REPO || (env.VERCEL_GIT_REPO_OWNER && env.VERCEL_GIT_REPO_SLUG ? `${env.VERCEL_GIT_REPO_OWNER}/${env.VERCEL_GIT_REPO_SLUG}` : "");
  if (!codeRepo) missing.push("CODE_REPO");
  else if (!REPO_RE.test(codeRepo)) problems.push("CODE_REPO inválido (use dono/repositório)");
  const dataRepo = env.DATA_REPO || (codeRepo ? `${codeRepo}-data` : "");
  if (dataRepo && !REPO_RE.test(dataRepo)) problems.push("DATA_REPO inválido (use dono/repositório)");
  if (codeRepo && dataRepo && codeRepo.toLowerCase() === dataRepo.toLowerCase()) problems.push("DATA_REPO precisa ser outro repositório (privado)");
  return { password, secret, ghToken, codeRepo, dataRepo, missing, problems, ok: !missing.length && !problems.length };
}

export function configError(cfg) {
  return { error: "Servidor não configurado.", missing: cfg.missing, problems: cfg.problems };
}

/** Comparação em tempo constante (compara os hashes, que têm sempre o mesmo tamanho). */
export function safeEqual(a, b) {
  const ha = crypto.createHash("sha256").update(String(a)).digest();
  const hb = crypto.createHash("sha256").update(String(b)).digest();
  return crypto.timingSafeEqual(ha, hb);
}

// A chave de assinatura depende também da senha: trocar a senha derruba todas as sessões.
const sessionKey = (cfg) => crypto.createHash("sha256").update(`${cfg.secret}\0${cfg.password}`).digest();
const sign = (cfg, data) => crypto.createHmac("sha256", sessionKey(cfg)).update(data).digest("base64url");

export function makeSession(cfg, now = Date.now()) {
  const exp = Math.floor(now / 1000) + SESSION_TTL_S;
  const body = `v1.${exp}.${crypto.randomBytes(12).toString("base64url")}`;
  return `${body}.${sign(cfg, body)}`;
}

export function verifySession(cfg, token, now = Date.now()) {
  if (typeof token !== "string") return false;
  const parts = token.split(".");
  if (parts.length !== 4 || parts[0] !== "v1") return false;
  const body = parts.slice(0, 3).join(".");
  const expected = sign(cfg, body), given = parts[3];
  if (expected.length !== given.length || !crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(given))) return false;
  const exp = Number(parts[1]);
  return Number.isFinite(exp) && exp * 1000 > now;
}

export function parseCookies(header) {
  const out = {};
  for (const part of String(header || "").split(";")) {
    const i = part.indexOf("=");
    if (i > 0) out[part.slice(0, i).trim()] = part.slice(i + 1).trim();
  }
  return out;
}

export const isSecure = (req) => String(req.headers["x-forwarded-proto"] || "").split(",")[0].trim() === "https";
// "__Host-" obriga Secure, Path=/ e proíbe Domain: o cookie não vaza para subdomínios.
const cookieName = (secure) => (secure ? "__Host-fs" : "fs");

export function setSessionCookie(req, res, value, maxAge = SESSION_TTL_S) {
  const secure = isSecure(req);
  res.setHeader("Set-Cookie", `${cookieName(secure)}=${value}; HttpOnly; SameSite=Strict; Path=/; Max-Age=${maxAge}${secure ? "; Secure" : ""}`);
}

export function clearSessionCookie(req, res) {
  setSessionCookie(req, res, "", 0);
}

export function isAuthenticated(req, cfg) {
  const c = parseCookies(req.headers.cookie);
  return verifySession(cfg, c[cookieName(isSecure(req))]);
}

/** Proteção contra CSRF para requisições que alteram algo: mesma origem e corpo JSON. */
export function checkWrite(req) {
  const site = req.headers["sec-fetch-site"];
  if (site && site !== "same-origin") return { status: 403, error: "Origem não permitida." };
  const origin = req.headers.origin;
  if (origin) {
    let host = "";
    try { host = new URL(origin).host; } catch { /* inválida */ }
    if (host !== req.headers.host) return { status: 403, error: "Origem não permitida." };
  }
  if (!String(req.headers["content-type"] || "").toLowerCase().startsWith("application/json")) {
    return { status: 415, error: "Use application/json." };
  }
  return null;
}

/** Exige sessão válida. Devolve a config, ou responde 503/401 e devolve null. */
export function requireAuth(req, res) {
  const cfg = loadConfig();
  if (!cfg.ok) { send(res, 503, configError(cfg)); return null; }
  if (!isAuthenticated(req, cfg)) { send(res, 401, { ok: false, error: "Faça login." }); return null; }
  return cfg;
}

// Limite de tentativas de login por IP. Cada instância serverless tem a sua memória, então
// isto é um freio, não um bloqueio perfeito (a senha longa é a defesa principal).
const attempts = new Map();
const MAX_FAILS = 5, WINDOW_MS = 15 * 60 * 1000;
export const loginLimiter = {
  blockedFor(req, now = Date.now()) {
    const a = attempts.get(clientIp(req));
    if (!a || now > a.reset) return 0;
    return a.count >= MAX_FAILS ? Math.ceil((a.reset - now) / 1000) : 0;
  },
  fail(req, now = Date.now()) {
    const ip = clientIp(req), a = attempts.get(ip);
    if (!a || now > a.reset) attempts.set(ip, { count: 1, reset: now + WINDOW_MS });
    else a.count++;
    if (attempts.size > 500) for (const [k, v] of attempts) if (now > v.reset) attempts.delete(k);
  },
  clear(req) { attempts.delete(clientIp(req)); },
  _reset() { attempts.clear(); },
};
