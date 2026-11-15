import { send, methodNotAllowed } from "./_lib/http.js";
import { requireAuth } from "./_lib/auth.js";
import { gh, upstreamError, WORKFLOW_FILE } from "./_lib/github.js";

// Última execução do workflow de coleta (só os campos que o painel usa).
export default async function handler(req, res) {
  if (req.method !== "GET") return methodNotAllowed(res, ["GET"]);
  const cfg = requireAuth(req, res);
  if (!cfg) return;
  const r = await gh(cfg, `/repos/${cfg.codeRepo}/actions/workflows/${WORKFLOW_FILE}/runs?per_page=1`);
  if (!r.ok) return upstreamError(res, r, "ler a última coleta");
  const run = r.json().workflow_runs?.[0];
  send(res, 200, { run: run ? { status: run.status, conclusion: run.conclusion, updated_at: run.updated_at, html_url: run.html_url } : null });
}
