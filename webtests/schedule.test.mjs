import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as S from "../docs/schedule.js";

const CASES = JSON.parse(readFileSync(new URL("../tests/schedule_cases.json", import.meta.url)));
// "2026-11-21T22:00" (hora de Brasília, UTC-3 o ano todo desde 2019) -> instante
const local = (txt) => Date.parse(`${txt}:00-03:00`);
const fmtLocal = (ms) => new Date(ms - 3 * 3600e3).toISOString().slice(0, 16);

for (const c of CASES.valid) {
  test(`vetores compartilhados: ${c.expr}`, () => {
    const s = S.parse(c.expr);
    for (const [when, expected] of c.matches) assert.equal(S.matches(s, local(when)), expected, `${c.expr} @ ${when}`);
    if (c.next) {
      let t = local(c.next.after); const got = [];
      for (let i = 0; i < c.next.expect.length; i++) { t = S.nextSlot(s, t); got.push(fmtLocal(t)); }
      assert.deepEqual(got, c.next.expect);
    }
  });
}
for (const bad of CASES.invalid) test(`expressão inválida: ${JSON.stringify(bad)}`, () => assert.throws(() => S.parse(bad)));

test("due: horário atendido, folga de 3 horas cheias e nunca coletou", () => {
  const s = S.parse("0 22 * * *"), u = (d, h, m) => Date.UTC(2026, 10, d, h, m);   // 22h Brasília = 01h UTC
  assert.equal(S.due(s, u(22, 1, 7), u(21, 1, 7)), true);
  assert.equal(S.due(s, u(22, 1, 7), u(22, 1, 2)), false);     // coleta manual às 22h02 já atendeu
  assert.equal(S.due(s, u(22, 2, 7), u(22, 1, 7)), false);
  assert.equal(S.due(s, u(21, 15, 7), u(20, 1, 7)), false);
  assert.equal(S.due(s, u(22, 4, 7), u(21, 1, 7)), true);      // 01h07: ainda na folga
  assert.equal(S.due(s, u(22, 5, 7), u(21, 1, 7)), false);     // 02h07: fora
  assert.equal(S.due(s, u(22, 1, 7), null), true);
});

test("compile / decompile ida e volta", () => {
  const cases = [
    [{ from: 22, to: 6, every: 2, days: [1, 2, 3, 4, 5] }, "0 0,2,4,6,22 * * 1-5"],
    [{ from: 22, to: 6, every: 1, days: [0, 1, 2, 3, 4, 5, 6] }, "0 0-6,22,23 * * *"],
    [{ from: null, to: null, every: 6, days: [0, 1, 2, 3, 4, 5, 6] }, "0 */6 * * *"],
    [{ from: null, to: null, every: 1, days: [6, 0] }, "0 * * * 0,6"],
    [{ from: 9, to: 18, every: 3, days: [1, 3, 5] }, "0 9,12,15,18 * * 1,3,5"],
    [{ from: 6, to: 6, every: 1, days: [0, 1, 2, 3, 4, 5, 6] }, "0 6 * * *"],
  ];
  for (const [simple, cron] of cases) {
    assert.equal(S.compile(simple), cron);
    const d = S.decompile(cron);
    assert.ok(d, cron);
    assert.equal(S.compile(d), cron, `ida e volta de ${cron}`);
    assert.deepEqual([...S.parse(cron).hours].sort((a, b) => a - b), [...S.parse(S.compile(simple)).hours].sort((a, b) => a - b));
  }
});

test("decompile: o que não cabe no modo simples vira null (modo avançado)", () => {
  for (const x of ["0 6,22 * * *", "30 22 * * *", "0 22 15 * *", "0 22 * 12 *", "0 6-8,22-23 * * *", "lixo"]) assert.equal(S.decompile(x), null, x);
});

test("describe", () => {
  assert.equal(S.describe("0 0,2,4,6,22 * * 1-5"), "Seg a sex, das 22h às 6h, de 2 em 2 h");
  assert.equal(S.describe("0 * * * *"), "Todos os dias, o dia todo, toda hora");
  assert.equal(S.describe("0 6 * * 0,6"), "Fins de semana, às 6h");
  assert.equal(S.describe("0 6,22 * * *"), "Cron: 0 6,22 * * *");
});

test("nextSlots devolve instantes crescentes no fuso de Brasília", () => {
  const out = S.nextSlots("0 22 * * *", 3, local("2026-11-21T10:00"));
  assert.deepEqual(out.map(fmtLocal), ["2026-11-21T22:00", "2026-11-22T22:00", "2026-11-23T22:00"]);
});
