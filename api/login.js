import { send, methodNotAllowed, readJson, sleep } from "./_lib/http.js";
import { loadConfig, configError, safeEqual, makeSession, setSessionCookie, checkWrite, loginLimiter } from "./_lib/auth.js";

export default async function handler(req, res) {
  if (req.method !== "POST") return methodNotAllowed(res, ["POST"]);
  const cfg = loadConfig();
  if (!cfg.ok) return send(res, 503, configError(cfg));
  const bad = checkWrite(req);
  if (bad) return send(res, bad.status, { error: bad.error });
  const wait = loginLimiter.blockedFor(req);
  if (wait) return send(res, 429, { error: "Muitas tentativas. Aguarde alguns minutos." }, { "Retry-After": String(wait) });
  let body;
  try { body = await readJson(req, 4096); } catch (e) { return send(res, e.status || 400, { error: e.message }); }
  const pw = typeof body?.password === "string" ? body.password : "";
  if (!pw || !safeEqual(pw, cfg.password)) {
    loginLimiter.fail(req);
    await sleep(800); // freio contra tentativa em massa
    return send(res, 401, { error: "Senha incorreta." });
  }
  loginLimiter.clear(req);
  setSessionCookie(req, res, makeSession(cfg));
  send(res, 200, { ok: true });
}
