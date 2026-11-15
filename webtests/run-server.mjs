// Sobe o painel local com um GitHub FALSO por trás (para pré-visualizar e para o teste de navegador).
//   node webtests/run-server.mjs [dashboard.json] [watches.json]     (senha de teste: senha-de-teste-123)
import fs from "node:fs";
import { createServer, listen } from "./server.mjs";
import { makeFakeGithub } from "./fake-github.mjs";

const GH_TOKEN = "github_pat_FALSO_so_para_teste_0123456789";
if (process.env.OMIT_GH_TOKEN === "1") delete process.env.GH_TOKEN; else process.env.GH_TOKEN = GH_TOKEN;
Object.assign(process.env, {
  APP_PASSWORD: "senha-de-teste-123", SESSION_SECRET: "s".repeat(48),
  CODE_REPO: "Dusax/flight", DATA_REPO: "Dusax/flight-data",
});
const gh = makeFakeGithub({ token: GH_TOKEN });
gh.install();
if (process.argv[2]) gh.putFile("dashboard.json", fs.readFileSync(process.argv[2], "utf8"));
if (process.argv[3]) gh.putFile("watches.json", fs.readFileSync(process.argv[3], "utf8"));
const base = await listen(createServer());
console.log(base);
// endpoint de inspeção só para o teste: o que o "GitHub" recebeu
globalThis.__gh = gh;
process.stdin.resume();
process.on("SIGTERM", () => process.exit(0));
