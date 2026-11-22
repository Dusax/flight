// Pedidos de coleta feitos pelo painel ("Buscar agora"). O GitHub guarda só UMA execução pendente: se duas forem
// disparadas enquanto uma coleta já está rodando, a mais antiga é cancelada e os ids dela se perderiam. Por isso os
// ids também ficam acumulados em requests.json no repositório de dados; o job `plan` do workflow lê e esvazia o arquivo.
import { gh } from "./github.js";

const MAX_IDS = 200;
const PATH = (cfg) => `/repos/${cfg.dataRepo}/contents/requests.json`;

export function normalizeRequests(raw) {
  const ids = Array.isArray(raw?.ids) ? raw.ids.filter((n) => Number.isInteger(n) && n > 0 && n <= 1_000_000) : [];
  return { ids: [...new Set(ids)].sort((a, b) => a - b).slice(0, MAX_IDS), all: raw?.all === true };
}

/** Soma os ids pedidos (ou "todas", se a lista vier vazia) ao requests.json. Devolve true se gravou. Nunca lança. */
export async function queueRequest(cfg, ids) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const r = await gh(cfg, PATH(cfg));
    let cur = { ids: [], all: false }, sha;
    if (r.ok) {
      try {
        const j = r.json(); sha = j.sha;
        cur = normalizeRequests(JSON.parse(Buffer.from(String(j.content || ""), "base64").toString("utf8") || "{}"));
      } catch { cur = { ids: [], all: false }; }
    } else if (r.status !== 404) return false;
    const next = ids.length ? normalizeRequests({ ids: [...cur.ids, ...ids], all: cur.all }) : { ids: cur.ids, all: true };
    const put = await gh(cfg, PATH(cfg), {
      method: "PUT",
      body: { message: "pedido de coleta pelo painel", content: Buffer.from(JSON.stringify(next) + "\n").toString("base64"), ...(sha ? { sha } : {}) },
    });
    if (put.ok) return true;
    if (put.status !== 409 && put.status !== 422) return false;   // conflito de sha: lê de novo e tenta outra vez
  }
  return false;
}
