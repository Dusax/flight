// Utilidades HTTP que funcionam igual na Vercel e no servidor local de testes.

export function send(res, status, body, headers = {}) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");
  res.setHeader("X-Content-Type-Options", "nosniff");
  for (const [k, v] of Object.entries(headers)) res.setHeader(k, v);
  res.end(JSON.stringify(body));
}

export function methodNotAllowed(res, allowed) {
  send(res, 405, { error: "Método não permitido." }, { Allow: allowed.join(", ") });
}

/** Lê o corpo JSON. Na Vercel `req.body` já vem interpretado; localmente lemos o stream. */
export async function readJson(req, limit = 256 * 1024) {
  let raw = req.body;
  if (raw === undefined) {
    const chunks = [];
    let size = 0;
    for await (const c of req) {
      size += c.length;
      if (size > limit) throw Object.assign(new Error("Corpo grande demais."), { status: 413 });
      chunks.push(c);
    }
    raw = Buffer.concat(chunks).toString("utf8");
  }
  if (Buffer.isBuffer(raw)) raw = raw.toString("utf8");
  if (typeof raw === "string") {
    if (raw.length > limit) throw Object.assign(new Error("Corpo grande demais."), { status: 413 });
    try { return raw ? JSON.parse(raw) : {}; } catch { throw Object.assign(new Error("JSON inválido."), { status: 400 }); }
  }
  return raw ?? {};
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function clientIp(req) {
  const xf = String(req.headers["x-forwarded-for"] || "").split(",")[0].trim();
  return xf || req.socket?.remoteAddress || "?";
}
