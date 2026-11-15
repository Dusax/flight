import { send, methodNotAllowed } from "./_lib/http.js";
import { clearSessionCookie, checkWrite } from "./_lib/auth.js";

export default function handler(req, res) {
  if (req.method !== "POST") return methodNotAllowed(res, ["POST"]);
  const bad = checkWrite(req);
  if (bad) return send(res, bad.status, { error: bad.error });
  clearSessionCookie(req, res);
  send(res, 200, { ok: true });
}
