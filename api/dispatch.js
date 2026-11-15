import { send, methodNotAllowed, readJson } from "./_lib/http.js";
import { requireAuth, checkWrite } from "./_lib/auth.js";
import { gh, upstreamError, defaultBranch, WORKFLOW_FILE } from "./_lib/github.js";

// Dispara a coleta (todas as buscas ativas, ou só as ids informadas).
export default async function handler(req, res) {
  if (req.method !== "POST") return methodNotAllowed(res, ["POST"]);
  const cfg = requireAuth(req, res);
  if (!cfg) return;
  const bad = checkWrite(req);
  if (bad) return send(res, bad.status, { error: bad.error });
  let body;
  try { body = await readJson(req, 4096); } catch (e) { return send(res, e.status || 400, { error: e.message }); }
  const ids = body?.ids ?? [];
  if (!Array.isArray(ids) || ids.length > 60 || !ids.every((n) => Number.isInteger(n) && n > 0 && n <= 1_000_000)) {
    return send(res, 400, { error: "ids inválidos." });
  }
  const ref = await defaultBranch(cfg, cfg.codeRepo);
  if (!ref) return send(res, 502, { error: "Não encontrei o repositório do código (confira CODE_REPO e o token)." });
  const r = await gh(cfg, `/repos/${cfg.codeRepo}/actions/workflows/${WORKFLOW_FILE}/dispatches`, {
    method: "POST", body: { ref, inputs: { ids: ids.join(",") } },
  });
  if (!r.ok) return upstreamError(res, r, "disparar a coleta (o token precisa de Actions: Read and write)");
  send(res, 200, { ok: true });
}
