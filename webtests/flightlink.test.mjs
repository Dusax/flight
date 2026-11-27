import { test } from "node:test";
import assert from "node:assert/strict";
import { flightLink, buildUrl, segmentsOf } from "../docs/flightlink.js";

// Voos e rotas inventados; o formato do tfs foi conferido contra URLs de compra reais do Google (41 de 42 idênticas fora
// o código de cidade, que o Google troca por um id interno).
const raw = (t) => Uint8Array.from(Buffer.from(t.replace(/-/g, "+").replace(/_/g, "/"), "base64"));
function parse(b) {
  const out = []; let i = 0;
  const rv = () => { let v = 0n, s = 0n; for (;;) { const x = b[i++]; v |= BigInt(x & 127) << s; s += 7n; if (x < 128) return v; } };
  while (i < b.length) {
    const k = Number(rv()), f = k >> 3;
    if ((k & 7) === 0) out.push([f, rv()]); else { const l = Number(rv()); out.push([f, b.slice(i, i + l)]); i += l; }
  }
  return out;
}
const txt = (b) => Buffer.from(b).toString();
const tfsOf = (u) => new URL(u).searchParams.get("tfs");
const legsOf = (u) => parse(raw(tfsOf(u))).filter(([f]) => f === 3).map(([, v]) => {
  const m = parse(v);
  return { date: txt(m.find(([f]) => f === 2)[1]),
    segs: m.filter(([f]) => f === 4).map(([, sv]) => Object.fromEntries(parse(sv).map(([f, x]) => [f, txt(x)]))),
    from: txt(parse(m.find(([f]) => f === 13)[1]).find(([f]) => f === 2)[1]), to: txt(parse(m.find(([f]) => f === 14)[1]).find(([f]) => f === 2)[1]) };
});
const top = (u) => Object.fromEntries(parse(raw(tfsOf(u))).filter(([f]) => f !== 3).map(([f, v]) => [f, typeof v === "bigint" ? v : txt(v)]));

const seg = (flight, from, to, plus, arrPlus = plus) => ({ flight, dep: { code: from, plus_days: plus }, arr: { code: to, plus_days: arrPlus } });
const W = { origin: "GRU", destination: "LIS", adults: 2, cabin: "business", currency: "EUR", max_stops: null };
const combo = (extra = {}) => ({ depart_date: "2027-03-10", legs: [{ origin: "GRU", destination: "LIS", date: "2027-03-10" }], ...extra });

test("voo direto com segmentos: tela de compra com o voo escolhido", () => {
  const o = { flight_numbers: "TP 82", dep_time: "2027-03-10T22:30", arr_time: "2027-03-11T11:55", segments: [seg("TP 82", "GRU", "LIS", 0, 1)] };
  const r = flightLink(W, combo(), o);
  assert.equal(r.exact, true);
  assert.match(r.url, /^https:\/\/www\.google\.com\/travel\/flights\/booking\?/);
  const [leg] = legsOf(r.url);
  assert.deepEqual(leg.segs, [{ 1: "GRU", 2: "2027-03-10", 3: "LIS", 5: "TP", 6: "82" }]);
  assert.deepEqual([leg.date, leg.from, leg.to], ["2027-03-10", "GRU", "LIS"]);
  const t = top(r.url);
  assert.equal(t[2], 2n);          // só ida
  assert.equal(t[19], 2n);
  assert.equal(t[9], 3n);          // executiva
  const u = new URL(r.url);
  assert.equal(u.searchParams.get("curr"), "EUR");
  assert.equal(u.searchParams.get("gl"), "BR");
  assert.equal(u.searchParams.get("hl"), "en");
  assert.equal(parse(raw(tfsOf(r.url))).filter(([f]) => f === 8).length, 2);   // 2 adultos
  assert.ok(r.search && r.search.startsWith("https://www.google.com/travel/flights?"));
});

test("conexão que vira o dia: a data de cada segmento vem de dep.plus_days", () => {
  const o = { flight_numbers: "LA 1, LA 2", dep_time: "2027-03-10T20:00", segments: [seg("LA 1", "GRU", "LIM", 0), seg("LA 2", "LIM", "CUZ", 1)] };
  const [leg] = legsOf(flightLink(W, combo(), o).url);
  assert.deepEqual(leg.segs.map((s) => [s[1], s[2], s[3]]), [["GRU", "2027-03-10", "LIM"], ["LIM", "2027-03-11", "CUZ"]]);
});

test("busca por cidade (RIO): o trecho escolhido usa os aeroportos do próprio voo", () => {
  const w = { ...W, origin: "REC", destination: "RIO" };
  const c = { depart_date: "2027-03-10", legs: [{ origin: "REC", destination: "RIO", date: "2027-03-10" }] };
  const o = { flight_numbers: "G3 1", dep_time: "2027-03-10T08:00", segments: [seg("G3 1", "REC", "GIG", 0)] };
  const [leg] = legsOf(flightLink(w, c, o).url);
  assert.deepEqual([leg.from, leg.to], ["REC", "GIG"]);
});

test("ida e volta: ida com o voo escolhido, volta só com data e aeroportos", () => {
  const c = { depart_date: "2027-03-10", return_date: "2027-03-20", legs: [
    { origin: "GRU", destination: "LIS", date: "2027-03-10" }, { origin: "LIS", destination: "GRU", date: "2027-03-20" }] };
  const o = { flight_numbers: "TP 82", dep_time: "2027-03-10T22:30", segments: [seg("TP 82", "GRU", "LIS", 0)] };
  const r = flightLink(W, c, o);
  assert.equal(r.exact, true);
  const legs = legsOf(r.url);
  assert.equal(legs.length, 2);
  assert.equal(legs[0].segs.length, 1);
  assert.deepEqual([legs[1].date, legs[1].from, legs[1].to, legs[1].segs.length], ["2027-03-20", "LIS", "GRU", 0]);
  assert.equal(top(r.url)[19], 1n);
});

test("sem segments: direto vira segmento único; conexão no mesmo dia também; virando o dia cai na busca", () => {
  const direct = { flight_numbers: "TP 82", dep_time: "2027-03-10T09:00", arr_time: "2027-03-10T21:00", origin: "GRU", destination: "LIS", stops: 0 };
  assert.equal(flightLink(W, combo(), direct).exact, true);
  const sameDay = { flight_numbers: "AA 1, AA 2", dep_time: "2027-03-10T06:00", arr_time: "2027-03-10T20:00", origin: "GRU", destination: "LIS", layover_airports: "MAD" };
  const [leg] = legsOf(flightLink(W, combo(), sameDay).url);
  assert.deepEqual(leg.segs.map((s) => [s[1], s[3], s[6]]), [["GRU", "MAD", "1"], ["MAD", "LIS", "2"]]);
  const overnight = { ...sameDay, arr_time: "2027-03-11T08:00" };
  const r = flightLink(W, combo(), overnight);
  assert.equal(r.exact, false);
  assert.match(r.url, /\/travel\/flights\?/);      // busca da data, não a tela de compra
  assert.equal(legsOf(r.url)[0].segs.length, 0);
});

test("multi-destino fica na busca com todos os trechos", () => {
  const w = { ...W, legs: [{ origin: "GRU", destination: "LIS" }, { origin: "LIS", destination: "MAD" }] };
  const c = { depart_date: "2027-03-10", legs: [{ origin: "GRU", destination: "LIS", date: "2027-03-10" }, { origin: "LIS", destination: "MAD", date: "2027-03-14" }] };
  const r = flightLink(w, c, { flight_numbers: "TP 82", dep_time: "2027-03-10T22:30", segments: [seg("TP 82", "GRU", "LIS", 0)] });
  assert.equal(r.exact, false);
  assert.equal(legsOf(r.url).length, 2);
  assert.equal(top(r.url)[19], 3n);
});

test("busca respeita máximo de escalas; link capturado do Google tem prioridade; outros domínios são ignorados", () => {
  const r = flightLink({ ...W, max_stops: 0 }, combo(), { flight_numbers: "XX", dep_time: "2027-03-10T09:00", arr_time: "2027-03-11T09:00", origin: "GRU", destination: "LIS", layover_airports: "MAD" });
  assert.deepEqual(parse(parse(raw(tfsOf(r.url))).find(([f]) => f === 3)[1]).find(([f]) => f === 5), [5, 0n]);
  const cap = "https://www.google.com/travel/flights/booking?tfs=abc";
  assert.equal(flightLink(W, combo(), { booking: { url: cap } }).url, cap);
  const evil = flightLink(W, combo(), { booking: { url: "https://evil.example/x" }, flight_numbers: "TP 82", dep_time: "2027-03-10T09:00", arr_time: "2027-03-10T21:00", origin: "GRU", destination: "LIS" });
  assert.match(evil.url, /^https:\/\/www\.google\.com\//);
});

test("dados insuficientes: sem datas ou sem voo, não monta nada quebrado", () => {
  assert.equal(flightLink(W, null, {}), null);
  assert.equal(flightLink({ adults: 1 }, { depart_date: "2027-03-10" }, {}), null);
  assert.equal(segmentsOf(null, "2027-03-10"), null);
  assert.equal(segmentsOf({ segments: [{ flight: "??", dep: { code: "GRU" }, arr: { code: "LIS" } }], dep_time: "2027-03-10T09:00" }, "2027-03-10"), null);
});

test("combinação antiga (sem legs): usa as datas da combinação", () => {
  const r = flightLink(W, { depart_date: "2027-03-10", return_date: "2027-03-20" }, { flight_numbers: "TP 82", dep_time: "2027-03-10T09:00", arr_time: "2027-03-10T21:00", origin: "GRU", destination: "LIS" });
  assert.equal(legsOf(r.url).length, 2);
});
