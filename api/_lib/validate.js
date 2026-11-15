// Valida e normaliza a lista de buscas antes de gravá-la no repositório de dados.
// Só passam os campos conhecidos, com tipo e limite verificados: o servidor nunca grava
// no GitHub algo que o coletor Python não entenda.

const IATA = /^[A-Z]{3}$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const CABINS = new Set(["economy", "premium", "business", "first"]);
export const MAX_WATCHES = 60;

const isDate = (v) => typeof v === "string" && ISO_DATE.test(v) && !Number.isNaN(Date.parse(v)) && new Date(v).toISOString().slice(0, 10) === v;
const int = (v, lo, hi) => Number.isInteger(v) && v >= lo && v <= hi;

function leg(l, i) {
  if (!l || typeof l !== "object") throw new Error(`trecho ${i + 1} inválido`);
  const o = { origin: String(l.origin || "").toUpperCase(), destination: String(l.destination || "").toUpperCase(), date: l.date };
  if (!IATA.test(o.origin) || !IATA.test(o.destination)) throw new Error(`trecho ${i + 1}: use códigos IATA de 3 letras`);
  if (o.origin === o.destination) throw new Error(`trecho ${i + 1}: origem e destino iguais`);
  if (!isDate(o.date)) throw new Error(`trecho ${i + 1}: data inválida`);
  return o;
}

export function normalizeWatch(w) {
  if (!w || typeof w !== "object" || Array.isArray(w)) throw new Error("item inválido");
  if (!int(w.id, 1, 1_000_000)) throw new Error("id inválido");
  const out = { id: w.id };
  if (w.label != null && w.label !== "") {
    if (typeof w.label !== "string" || w.label.length > 60) throw new Error("nome com mais de 60 caracteres");
    out.label = w.label.trim();
  } else out.label = null;
  out.origin = String(w.origin || "").toUpperCase();
  out.destination = String(w.destination || "").toUpperCase();
  if (!IATA.test(out.origin) || !IATA.test(out.destination)) throw new Error("use códigos IATA de 3 letras");
  if (out.origin === out.destination) throw new Error("origem e destino iguais");
  if (!isDate(w.depart_date)) throw new Error("data de ida inválida");
  out.depart_date = w.depart_date;
  if (w.return_date != null && w.return_date !== "") {
    if (!isDate(w.return_date)) throw new Error("data de volta inválida");
    if (w.return_date < w.depart_date) throw new Error("a volta não pode ser antes da ida");
    out.return_date = w.return_date;
  } else out.return_date = null;
  if (w.legs != null) {
    if (!Array.isArray(w.legs) || w.legs.length < 2 || w.legs.length > 5) throw new Error("multi-destino precisa de 2 a 5 trechos");
    out.legs = w.legs.map(leg);
    out.legs.forEach((l, i) => { if (i && l.date < out.legs[i - 1].date) throw new Error(`trecho ${i + 1}: data antes do trecho anterior`); });
    if (out.return_date) throw new Error("multi-destino não usa data de volta");
    // o 1º trecho tem de bater com os campos de topo
    const f = out.legs[0];
    if (f.origin !== out.origin || f.destination !== out.destination || f.date !== out.depart_date) throw new Error("o 1º trecho difere de origem/destino/ida");
  } else out.legs = null;
  if (!int(w.flex_days ?? 0, 0, 7)) throw new Error("datas flexíveis: 0 a 7");
  out.flex_days = w.flex_days ?? 0;
  if (!int(w.adults ?? 1, 1, 9)) throw new Error("adultos: 1 a 9");
  out.adults = w.adults ?? 1;
  if (!CABINS.has(w.cabin ?? "economy")) throw new Error("classe inválida");
  out.cabin = w.cabin ?? "economy";
  if (w.max_stops != null && !int(w.max_stops, 0, 3)) throw new Error("escalas: 0 a 3");
  out.max_stops = w.max_stops ?? null;
  const cur = String(w.currency || "BRL").toUpperCase();
  if (!IATA.test(cur)) throw new Error("moeda inválida");
  out.currency = cur;
  if (w.every_hours != null && !int(w.every_hours, 1, 24 * 30)) throw new Error("frequência: 1 a 720 horas");
  out.every_hours = w.every_hours ?? null;
  out.compare_separate = w.compare_separate === true;
  if (!int(w.booking_top ?? 0, 0, 10)) throw new Error("detalhar compra: 0 a 10");
  out.booking_top = w.booking_top ?? 0;
  out.active = w.active !== false;
  return out;
}

export function normalizeWatches(list) {
  if (!Array.isArray(list)) throw new Error("a lista de buscas deve ser um array");
  if (list.length > MAX_WATCHES) throw new Error(`no máximo ${MAX_WATCHES} buscas`);
  const seen = new Set();
  return list.map((w, i) => {
    let n;
    try { n = normalizeWatch(w); } catch (e) { throw new Error(`busca ${i + 1}: ${e.message}`); }
    if (seen.has(n.id)) throw new Error(`id ${n.id} repetido`);
    seen.add(n.id);
    return n;
  });
}
