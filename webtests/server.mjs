// Servidor local que imita a Vercel: serve docs/ e monta api/*.js, com os cabeçalhos do vercel.json.
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const vercel = JSON.parse(fs.readFileSync(path.join(root, "vercel.json"), "utf8"));
const staticHeaders = Object.fromEntries(vercel.headers.flatMap((r) => r.headers).map((h) => [h.key, h.value]));
const MIME = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".txt": "text/plain; charset=utf-8", ".json": "application/json" };

export function createServer() {
  return http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    try {
      if (url.pathname.startsWith("/api/")) {
        const name = url.pathname.slice(5);
        if (!/^[a-z-]+$/.test(name) || !fs.existsSync(path.join(root, "api", `${name}.js`))) { res.statusCode = 404; return res.end("not found"); }
        const mod = await import(path.join(root, "api", `${name}.js`));
        return await mod.default(req, res);
      }
      const file = path.join(root, "docs", url.pathname === "/" ? "index.html" : url.pathname.slice(1));
      if (!file.startsWith(path.join(root, "docs")) || !fs.existsSync(file)) { res.statusCode = 404; return res.end("not found"); }
      for (const [k, v] of Object.entries(staticHeaders)) res.setHeader(k, v);
      res.setHeader("Content-Type", MIME[path.extname(file)] || "application/octet-stream");
      res.end(fs.readFileSync(file));
    } catch (e) {
      res.statusCode = 500; res.end(String(e.stack || e));
    }
  });
}

export function listen(server) {
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(`http://127.0.0.1:${server.address().port}`)));
}
