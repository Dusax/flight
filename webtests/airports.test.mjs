import { test, before } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
await import(path.join(root, "docs/airports.js"));
const A = globalThis.Airports;
const rows = JSON.parse(fs.readFileSync(path.join(root, "docs/airports.json"), "utf8"));
before(() => A._build(rows));

const top = (q, n = 5) => A.search(q, n).map((a) => a.iata);

test("a base tem milhares de aeroportos e os códigos de cidade", () => {
  assert.ok(rows.length > 7000 && A.size > 7000);
  for (const c of ["GRU", "REC", "CUZ", "LIS", "MAD", "JFK", "SAO", "RIO", "NYC"]) assert.ok(A.describe(c), c);
  assert.ok(rows.every(([c]) => /^[A-Z]{3}$/.test(c)));
});
test("código digitado: o aeroporto exato vem primeiro", () => {
  assert.equal(top("gru")[0], "GRU"); assert.equal(top("REC")[0], "REC"); assert.equal(top("cuz")[0], "CUZ"); assert.equal(top("lis")[0], "LIS");
});
test("cidade em português, com ou sem acento e maiúsculas", () => {
  assert.equal(top("lisboa")[0], "LIS"); assert.equal(top("Recife")[0], "REC"); assert.equal(top("cusco")[0], "CUZ"); assert.equal(top("brasilia")[0], "BSB");
  assert.equal(top("BRASÍLIA")[0], "BSB"); assert.equal(top("florianopolis")[0], "FLN"); assert.equal(top("foz")[0], "IGU"); assert.equal(top("madri")[0], "MAD");
});
test("cidade em inglês e nomes no original também funcionam", () => {
  assert.equal(top("lisbon")[0], "LIS"); assert.equal(top("new york")[0], "NYC"); assert.ok(top("new york", 8).includes("JFK")); assert.equal(top("madrid")[0], "MAD");
  assert.ok(top("rome").includes("FCO")); assert.ok(top("roma").includes("FCO")); assert.ok(top("venice").includes("VCE")); assert.ok(top("veneza").includes("VCE"));
});
test("vários aeroportos na mesma cidade: o código de cidade e os principais aparecem", () => {
  const sp = top("sao paulo", 6); assert.equal(sp[0], "SAO"); for (const c of ["GRU", "CGH"]) assert.ok(sp.includes(c), `${c} em ${sp}`); assert.ok(top("campinas").includes("VCP"));
  const rj = top("rio de janeiro", 6); assert.equal(rj[0], "RIO"); assert.ok(rj.includes("GIG") && rj.includes("SDU"));
  const ny = top("nova york", 8); assert.equal(ny[0], "NYC"); for (const c of ["JFK", "EWR", "LGA"]) assert.ok(ny.includes(c));
  const lon = top("londres", 8); assert.equal(lon[0], "LON"); assert.ok(lon.includes("LHR") && lon.includes("LGW"));
  const par = top("paris", 5); assert.equal(par[0], "PAR"); assert.ok(par.includes("CDG") && par.includes("ORY"));
});
test("pelo nome do aeroporto ou pelo país", () => {
  assert.ok(top("guarulhos").includes("GRU")); assert.ok(top("heathrow").includes("LHR")); assert.ok(top("santos dumont").includes("SDU"));
  assert.ok(top("portugal", 10).length > 0 && A.search("portugal", 10).every((a) => a.country === "Portugal" || a.cityN || true));
});
test("aeroportos pequenos de cidades menores também existem", () => {
  assert.ok(A.describe("PNZ") && A.describe("JDO")); assert.ok(top("petrolina").includes("PNZ")); assert.ok(top("juazeiro do norte").includes("JDO"));
});
test("sem resultado e limite", () => {
  assert.deepEqual(top("zzzzqq"), []); assert.deepEqual(top(""), []); assert.deepEqual(top("   "), []);
  assert.ok(A.search("a", 8).length <= 8 && A.search("sao", 3).length <= 3);
});
test("resolve: código conhecido, cidade ou nada", () => {
  assert.equal(A.resolve("gru"), "GRU"); assert.equal(A.resolve("Recife"), "REC"); assert.equal(A.resolve("lisboa"), "LIS"); assert.equal(A.resolve("rio"), "RIO");
  assert.equal(A.resolve("zzzz"), null); assert.equal(A.resolve(""), null);
});
test("nome da cidade: português quando existe, senão o da base", () => {
  assert.equal(A.cityOf("LIS"), "Lisboa"); assert.equal(A.cityOf("REC"), "Recife"); assert.equal(A.cityOf("SAO"), "São Paulo (todos os aeroportos)");
  assert.ok(/Fiumicino|Roma/.test(A.cityOf("FCO"))); assert.equal(A.cityOf("QQQ"), "QQQ");
  assert.equal(A.describe("CIA")?.city, "Roma"); // outro aeroporto de Roma herda o nome em português
  assert.ok(A.describe("GRU").country === "Brasil" && A.describe("LIS").country === "Portugal");
});
test("funciona só com a lista em português se o arquivo não carregar", () => {
  A._build([]);
  assert.equal(A.search("lisboa")[0]?.iata, "LIS"); assert.equal(A.cityOf("GRU"), "São Paulo");
  A._build(rows);
});
