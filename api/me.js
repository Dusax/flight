import { send, methodNotAllowed } from "./_lib/http.js";
import { requireAuth } from "./_lib/auth.js";

export default function handler(req, res) {
  if (req.method !== "GET") return methodNotAllowed(res, ["GET"]);
  const cfg = requireAuth(req, res);
  if (!cfg) return;
  // `build` muda a cada deploy: o painel compara com o da primeira carga e avisa quando há versão nova.
  // `version` é o commit curto (para o usuário conferir qual versão está no ar).
  const sha = process.env.VERCEL_GIT_COMMIT_SHA;
  send(res, 200, { ok: true, codeRepo: cfg.codeRepo, dataRepo: cfg.dataRepo,
    version: sha ? sha.slice(0, 7) : "dev", build: process.env.VERCEL_DEPLOYMENT_ID || sha || "dev" });
}
