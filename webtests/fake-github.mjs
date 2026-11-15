// GitHub falso, em memória, para testar as funções do painel sem rede.
import crypto from "node:crypto";

export function makeFakeGithub({ token, codeRepo = "dusax/flight", dataRepo = "dusax/flight-data" } = {}) {
  const state = {
    token, codeRepo, dataRepo,
    files: new Map(), // caminho -> { content, sha } no repo de dados
    dispatches: [], calls: [],
    codeBranch: "ccr-main", run: null, failWith: null,
  };
  const sha = (s) => crypto.createHash("sha1").update(s).digest("hex");
  state.putFile = (path, content) => { state.files.set(path, { content, sha: sha(content) }); };

  const reply = (status, body, raw) => new Response(raw ? String(body) : body == null ? null : JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

  state.handle = async (url, opts = {}) => {
    const u = new URL(url), path = u.pathname, method = (opts.method || "GET").toUpperCase();
    const h = Object.fromEntries(Object.entries(opts.headers || {}).map(([k, v]) => [k.toLowerCase(), v]));
    state.calls.push({ method, path, auth: h.authorization, body: opts.body ? JSON.parse(opts.body) : null });
    if (state.failWith) return reply(state.failWith, { message: "SEGREDO-INTERNO-DO-GITHUB token=ghp_vazou" });
    if (h.authorization !== `Bearer ${state.token}`) return reply(401, { message: "Bad credentials" });
    const m = path.match(/^\/repos\/([^/]+\/[^/]+)(\/.*)?$/);
    if (!m) return reply(404, { message: "Not Found" });
    const repo = m[1].toLowerCase(), rest = m[2] || "";
    if (repo !== state.codeRepo.toLowerCase() && repo !== state.dataRepo.toLowerCase()) return reply(404, { message: "Not Found" });

    if (rest === "") return reply(200, { default_branch: repo === state.codeRepo.toLowerCase() ? state.codeBranch : "main", private: repo !== state.codeRepo.toLowerCase() });

    const c = rest.match(/^\/contents\/(.+)$/);
    if (c && repo === state.dataRepo.toLowerCase()) {
      const f = state.files.get(c[1]);
      if (method === "GET") {
        if (!f) return reply(404, { message: "Not Found" });
        if ((h.accept || "").includes("raw")) return reply(200, f.content, true);
        return reply(200, { sha: f.sha, content: Buffer.from(f.content).toString("base64") });
      }
      if (method === "PUT") {
        const b = JSON.parse(opts.body);
        if (f && !b.sha) return reply(422, { message: "sha wasn't supplied" });
        if (f && b.sha !== f.sha) return reply(409, { message: "does not match" });
        const content = Buffer.from(b.content, "base64").toString("utf8");
        state.putFile(c[1], content);
        return reply(200, { content: { sha: state.files.get(c[1]).sha } });
      }
    }
    if (repo === state.codeRepo.toLowerCase()) {
      if (rest === "/actions/workflows/scrape.yml/dispatches" && method === "POST") {
        state.dispatches.push(JSON.parse(opts.body));
        return new Response(null, { status: 204 });
      }
      if (rest.startsWith("/actions/workflows/scrape.yml/runs")) return reply(200, { workflow_runs: state.run ? [state.run] : [] });
    }
    return reply(404, { message: "Not Found" });
  };

  const real = globalThis.fetch;
  state.install = () => {
    globalThis.fetch = (url, opts) => (String(url).startsWith("https://api.github.com") ? state.handle(String(url), opts) : real(url, opts));
  };
  state.uninstall = () => { globalThis.fetch = real; };
  return state;
}
