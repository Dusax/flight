// Cliente mínimo da API do GitHub. O token vem de GH_TOKEN (só no servidor) e os repositórios
// são sempre os de CODE_REPO/DATA_REPO: o navegador não escolhe URL nenhuma.
import { send } from "./http.js";

const API = "https://api.github.com";
export const WORKFLOW_FILE = "scrape.yml";

export async function gh(cfg, path, { method = "GET", body, accept } = {}) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 10_000);
  try {
    const r = await fetch(API + path, {
      method,
      headers: {
        Authorization: `Bearer ${cfg.ghToken}`,
        Accept: accept || "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "flights-painel",
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: ctl.signal,
    });
    const text = await r.text();
    return { status: r.status, ok: r.ok, text, json: () => JSON.parse(text) };
  } catch (e) {
    return { status: 0, ok: false, text: "", json: () => null, error: e.name === "AbortError" ? "timeout" : "rede" };
  } finally {
    clearTimeout(timer);
  }
}

/** Traduz erro do GitHub numa resposta nossa, sem repassar o corpo (pode ter detalhes internos). */
export function upstreamError(res, r, what) {
  if (r.status === 409 || r.status === 422) return send(res, 409, { error: "Conflito: o arquivo mudou. Recarregue e tente de novo.", conflict: true });
  if (r.status === 401 || r.status === 403) return send(res, 502, { error: `O token do servidor não tem acesso para ${what} (confira GH_TOKEN e as permissões).` });
  if (r.status === 404) return send(res, 502, { error: `Não encontrei ${what} (confira CODE_REPO e DATA_REPO).` });
  if (r.status === 0) return send(res, 504, { error: "O GitHub não respondeu a tempo." });
  return send(res, 502, { error: `O GitHub respondeu ${r.status} ao ${what}.` });
}

/** Num 404 de arquivo: o arquivo ainda não existe (ok) ou o repositório/token está errado (erro)? */
export async function fileIsMissing(cfg, repo) {
  const r = await gh(cfg, `/repos/${repo}`);
  return { missing: r.ok, repoResponse: r };
}

const branchCache = new Map();
export async function defaultBranch(cfg, repo) {
  const hit = branchCache.get(repo);
  if (hit && Date.now() - hit.t < 10 * 60_000) return hit.name;
  const r = await gh(cfg, `/repos/${repo}`);
  if (!r.ok) return null;
  const name = r.json().default_branch;
  branchCache.set(repo, { name, t: Date.now() });
  return name;
}
export const _clearBranchCache = () => branchCache.clear();
