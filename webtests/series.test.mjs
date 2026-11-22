import { test } from "node:test";
import assert from "node:assert/strict";
import { flightKey, flightPoints, flightStats, tableRows, otherDates } from "../docs/series.js";

const combo = {
  series: { t: ["2030-05-01T10:00:00Z", "2030-05-02T10:00:00Z", "2030-05-03T10:00:00Z", "2030-05-04T10:00:00Z"], min: [3000, 3400, 2800, 2900],
    f: { "LA 8084|2030-05-01T22:10": [3000, null, 2800, 3100], "TP 92|2030-05-01T09:00": [null, 3400, null, null] } },
};

test("flightKey: número + horário de saída", () => {
  assert.equal(flightKey({ flight_numbers: "LA 8084", dep_time: "2030-05-01T22:10" }), "LA 8084|2030-05-01T22:10");
  assert.equal(flightKey({ flight_numbers: "LA 8084" }), "LA 8084|");
});

test("flightPoints alinha as coletas e preserva as lacunas", () => {
  const pts = flightPoints(combo, "LA 8084|2030-05-01T22:10");
  assert.deepEqual(pts.map((q) => q.p), [3000, null, 2800, 3100]);
  assert.deepEqual(pts.map((q) => q.min), [3000, 3400, 2800, 2900]);
  assert.equal(pts[0].t, Date.parse("2030-05-01T10:00:00Z"));
  assert.deepEqual(flightPoints(combo, "XX 1|x"), []); assert.deepEqual(flightPoints({}, "a"), []);
});

test("flightStats: atual, menor/maior, variações, aparições", () => {
  const s = flightStats(flightPoints(combo, "LA 8084|2030-05-01T22:10"));
  assert.equal(s.current, 3100); assert.equal(s.first, 3000); assert.equal(s.min, 2800); assert.equal(s.minAt, "2030-05-03T10:00:00Z");
  assert.equal(s.max, 3100); assert.equal(s.sinceFirst, 100); assert.equal(s.sincePrev, 300);   // vs. a coleta anterior em que apareceu (2800)
  assert.equal(s.seen, 3); assert.equal(s.total, 4); assert.equal(s.inLatest, true);
});

test("flightStats: não apareceu na última coleta; um só ponto; nenhum ponto", () => {
  const gone = flightStats(flightPoints(combo, "TP 92|2030-05-01T09:00"));
  assert.equal(gone.inLatest, false); assert.equal(gone.current, 3400); assert.equal(gone.lastSeenAt, "2030-05-02T10:00:00Z"); assert.equal(gone.sinceFirst, null); assert.equal(gone.sincePrev, null);
  assert.equal(flightStats([{ p: null }, { p: null }]), null); assert.equal(flightStats([]), null);
});

test("tableRows: mais nova primeiro, variação contra a última vez que apareceu, ausência marcada", () => {
  const rows = tableRows(flightPoints(combo, "LA 8084|2030-05-01T22:10"));
  assert.deepEqual(rows.map((r) => r.p), [3100, 2800, null, 3000]);
  assert.deepEqual(rows.map((r) => r.delta), [300, -200, null, null]);
  assert.deepEqual(rows.map((r) => r.absent), [false, false, true, false]);
});

test("otherDates: mesmo número e horário em outras datas, ordenado, marcando a atual", () => {
  const combos = [
    { depart_date: "2030-05-02", end_date: "2030-05-02", offers: [{ flight_numbers: "LA 8084", dep_time: "2030-05-02T22:10", price: 3200 }, { flight_numbers: "XX 1", dep_time: "2030-05-02T08:00", price: 1 }] },
    { depart_date: "2030-05-01", end_date: "2030-05-01", offers: [{ flight_numbers: "LA 8084", dep_time: "2030-05-01T22:10", price: 3100 }] },
    { depart_date: "2030-05-03", end_date: "2030-05-03", offers: [{ flight_numbers: "LA 8084", dep_time: "2030-05-03T06:00", price: 2000 }] },   // horário diferente: outro voo
    { depart_date: "2030-05-04", end_date: "2030-05-04", offers: [], offers_trimmed: true },
  ];
  const out = otherDates(combos, { flight_numbers: "LA 8084", dep_time: "2030-05-01T22:10" });
  assert.deepEqual(out.map((x) => [x.depart_date, x.price, x.here]), [["2030-05-01", 3100, true], ["2030-05-02", 3200, false]]);
  assert.deepEqual(otherDates(combos, {}), []);
});
