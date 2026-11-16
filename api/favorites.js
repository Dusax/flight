import { send, methodNotAllowed, readJson } from "./_lib/http.js";
import { requireAuth, checkWrite } from "./_lib/auth.js";
import { gh, upstreamError, fileIsMissing } from "./_lib/github.js";
import { normalizeFavorites } from "./_lib/validate.js";

const PATH = (cfg) => `/repos/${cfg.dataRepo}/contents/favorites.json`;

export default async function handler(req, res) {
  if (req.method !== "GET" && req.method !== "PUT") return methodNotAllowed(res, ["GET", "PUT"]);
  const cfg = requireAuth(req, res);
  if (!cfg) return;

  if (req.method === "GET") {
    const r = await gh(cfg, PATH(cfg));
    if (r.status === 404) {
      const { missing, repoResponse } = await fileIsMissing(cfg, cfg.dataRepo);
      if (!missing) return upstreamError(res, repoResponse, "acessar o repositório de dados");
      return send(res, 200, { sha: null, list: [] }); // ainda não existe: o 1º salvamento cria
    }
    if (!r.ok) return upstreamError(res, r, "ler os favoritos");
    try {
      const j = r.json();
      const list = JSON.parse(Buffer.from(String(j.content || ""), "base64").toString("utf8") || "[]");
      return send(res, 200, { sha: j.sha, list: Array.isArray(list) ? list : [] });
    } catch { return send(res, 502, { error: "favorites.json inválido no repositório de dados." }); }
  }

  const bad = checkWrite(req);
  if (bad) return send(res, bad.status, { error: bad.error });
  let body, list;
  try { body = await readJson(req); } catch (e) { return send(res, e.status || 400, { error: e.message }); }
  try { list = normalizeFavorites(body?.list); } catch (e) { return send(res, 400, { error: e.message }); }
  if (body.sha != null && (typeof body.sha !== "string" || !/^[0-9a-f]{40}$/.test(body.sha))) return send(res, 400, { error: "sha inválido." });
  const message = typeof body.message === "string" && body.message.trim() ? body.message.trim().slice(0, 120) : "favoritos atualizados pelo painel";
  const r = await gh(cfg, PATH(cfg), {
    method: "PUT",
    body: { message, content: Buffer.from(JSON.stringify(list, null, 2) + "\n").toString("base64"), ...(body.sha ? { sha: body.sha } : {}) },
  });
  if (!r.ok) return upstreamError(res, r, "salvar os favoritos");
  send(res, 200, { sha: r.json().content.sha, list });
}
