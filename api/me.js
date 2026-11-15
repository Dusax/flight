import { send, methodNotAllowed } from "./_lib/http.js";
import { requireAuth } from "./_lib/auth.js";

export default function handler(req, res) {
  if (req.method !== "GET") return methodNotAllowed(res, ["GET"]);
  const cfg = requireAuth(req, res);
  if (!cfg) return;
  send(res, 200, { ok: true, codeRepo: cfg.codeRepo, dataRepo: cfg.dataRepo });
}
