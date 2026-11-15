import { send, methodNotAllowed } from "./_lib/http.js";
import { requireAuth } from "./_lib/auth.js";
import { gh, upstreamError, fileIsMissing } from "./_lib/github.js";

// Devolve o dashboard.json do repositório privado de dados (ainda inexistente = vazio).
export default async function handler(req, res) {
  if (req.method !== "GET") return methodNotAllowed(res, ["GET"]);
  const cfg = requireAuth(req, res);
  if (!cfg) return;
  const r = await gh(cfg, `/repos/${cfg.dataRepo}/contents/dashboard.json`, { accept: "application/vnd.github.raw+json" });
  if (r.status === 404) {
    const { missing, repoResponse } = await fileIsMissing(cfg, cfg.dataRepo);
    if (!missing) return upstreamError(res, repoResponse, "acessar o repositório de dados");
    return send(res, 200, { generated_at: null, watches: [] }); // o repositório existe; o robô ainda não gerou o arquivo
  }
  if (!r.ok) return upstreamError(res, r, "ler os dados");
  let data;
  try { data = r.json(); } catch { return send(res, 502, { error: "dashboard.json inválido." }); }
  send(res, 200, data);
}
