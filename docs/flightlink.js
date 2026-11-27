// Link "Ver passagem": monta, no navegador, a URL do Google Voos com o voo já escolhido (tela de compra).
// A URL é só o parâmetro `tfs` (protobuf em base64) da busca; cada trecho carrega, além de data e aeroportos, os segmentos do
// voo escolhido {origem, data, destino, companhia, número}. Espelha flights/tfs.py (que monta a busca sem voo escolhido).
// Funções puras, testadas em Node. Nada passa pelo nosso servidor: o link abre direto o google.com.

const BASE = "https://www.google.com/travel/flights";
const BOOKING = `${BASE}/booking`;
const TFU = "EgQIABABIgA";
const CABIN = { economy: 1, premium: 2, business: 3, first: 4 };
const enc = new TextEncoder();

const varint = (n) => {
  const out = [];
  let v = BigInt(n);
  for (;;) {
    const b = Number(v & 0x7fn);
    v >>= 7n;
    if (v) out.push(b | 0x80); else { out.push(b); return out; }
  }
};
const cat = (...parts) => parts.flat();
const fVarint = (num, v) => cat(varint(num << 3), varint(v));
const fBytes = (num, data) => {
  const b = typeof data === "string" ? [...enc.encode(data)] : data;
  return cat(varint((num << 3) | 2), varint(b.length), b);
};
const b64url = (bytes) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

const isoDay = (s) => (typeof s === "string" && /^\d{4}-\d{2}-\d{2}/.test(s) ? s.slice(0, 10) : null);
function addDays(iso, n) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

const FLIGHT_RE = /^([A-Z0-9]{2})\s*(\d{1,4})$/;

/** Segmentos do voo [{from, to, date, airline, number}] ou null se não der para saber com certeza.
 *  Com `offer.segments` (detalhe lido do Google) a data de cada segmento é a do trecho + dep.plus_days.
 *  Sem eles, só vale se for voo direto ou se a viagem toda cai no mesmo dia (não dá para adivinhar a data das conexões). */
export function segmentsOf(offer, legDate) {
  if (!offer) return null;
  const base = isoDay(offer.dep_time) || isoDay(legDate);
  if (!base) return null;
  if (Array.isArray(offer.segments) && offer.segments.length) {
    const out = [];
    for (const s of offer.segments) {
      const m = FLIGHT_RE.exec(String(s.flight || "").trim());
      if (!m || !s.dep?.code || !s.arr?.code) return null;
      out.push({ from: s.dep.code, to: s.arr.code, date: addDays(base, s.dep.plus_days || 0), airline: m[1], number: m[2] });
    }
    return out;
  }
  const nums = String(offer.flight_numbers || "").split(",").map((x) => x.trim()).filter(Boolean);
  const lay = String(offer.layover_airports || "").split(",").map((x) => x.trim()).filter(Boolean);
  if (!nums.length || nums.length !== lay.length + 1 || !offer.origin || !offer.destination) return null;
  const sameDay = nums.length === 1 || (isoDay(offer.arr_time) && isoDay(offer.arr_time) === base);
  if (!sameDay) return null;
  const pts = [offer.origin, ...lay, offer.destination];
  const out = [];
  for (let i = 0; i < nums.length; i++) {
    const m = FLIGHT_RE.exec(nums[i]);
    if (!m) return null;
    out.push({ from: pts[i], to: pts[i + 1], date: base, airline: m[1], number: m[2] });
  }
  return out;
}

function legMsg(leg, { selected, maxStops }) {
  let msg = fBytes(2, leg.date);
  if (leg.segments?.length) {
    for (const s of leg.segments) {
      msg = cat(msg, fBytes(4, cat(fBytes(1, s.from), fBytes(2, s.date), fBytes(3, s.to), fBytes(5, s.airline), fBytes(6, s.number))));
    }
  } else if (!selected && maxStops != null) {
    msg = cat(msg, fVarint(5, maxStops));
  }
  // com voo escolhido, os aeroportos do trecho são os do próprio voo (a busca por cidade, ex. RIO, vira o aeroporto real)
  const from = leg.segments?.length ? leg.segments[0].from : leg.origin, to = leg.segments?.length ? leg.segments[leg.segments.length - 1].to : leg.destination;
  return cat(msg, fBytes(13, cat(fVarint(1, 1), fBytes(2, from))), fBytes(14, cat(fVarint(1, 1), fBytes(2, to))));
}

/** URL do Google Voos. `legs`: [{origin, destination, date, segments?}]; com segmentos em algum trecho vira tela de compra. */
export function buildUrl({ legs, adults = 1, cabin = "economy", currency = "BRL", maxStops = null, country = "BR", multi = false }) {
  const selected = legs.some((l) => l.segments?.length);
  const trip = multi ? 3 : legs.length > 1 ? 1 : 2;   // 1 = ida e volta, 2 = só ida, 3 = multi-destino (igual a tfs.py)
  let msg = [];
  if (selected) msg = cat(fVarint(1, 28), fVarint(2, trip));
  for (const l of legs) msg = cat(msg, fBytes(3, legMsg(l, { selected, maxStops })));
  for (let i = 0; i < Math.max(1, adults); i++) msg = cat(msg, fVarint(8, 1));
  msg = cat(msg, fVarint(9, CABIN[cabin] || 1));
  if (selected) msg = cat(msg, fVarint(14, 1), fBytes(16, fVarint(1, 18446744073709551615n)));
  msg = cat(msg, fVarint(19, trip));
  const p = new URLSearchParams({ hl: "en", curr: currency || "BRL", tfu: TFU, tfs: b64url(msg) });
  if (country) p.set("gl", country.toUpperCase());
  return `${selected ? BOOKING : BASE}?${p.toString()}`;
}

const googleUrl = (u) => (typeof u === "string" && /^https:\/\/www\.google\.com\//.test(u) ? u : null);

/** Link do voo: {url, exact, search?}. exact = abre a tela de compra do voo (search = busca da data, plano B); false = só a busca.
 *  w: viagem ({origin, destination, legs?, adults, cabin, currency, max_stops}); c: combinação de datas
 *  ({depart_date, return_date?, legs?}); o: voo (como no dashboard). */
export function flightLink(w, c, o) {
  const captured = googleUrl(o?.booking?.url);
  if (captured) return { url: captured, exact: true };
  if (!w || !c) return null;
  const common = { adults: w.adults || 1, cabin: w.cabin || "economy", currency: w.currency || "BRL", maxStops: w.max_stops ?? null };
  const multi = !!(w.legs && w.legs.length);
  let legs = null;
  if (c.legs && c.legs.length) {   // dashboard novo: toda combinação traz os trechos {origin, destination, date}
    legs = c.legs.map((l) => ({ origin: l.origin, destination: l.destination, date: isoDay(l.date) }));
  } else if (!multi && w.origin && w.destination && isoDay(c.depart_date)) {
    legs = [{ origin: w.origin, destination: w.destination, date: isoDay(c.depart_date) }];
    if (isoDay(c.return_date)) legs.push({ origin: w.destination, destination: w.origin, date: isoDay(c.return_date) });
  }
  if (!legs || legs.some((l) => !l.origin || !l.destination || !l.date)) return null;
  // voo escolhido só quando dá para saber os segmentos; multi-destino fica na busca (as ofertas valem para um trecho só)
  const segs = multi ? null : segmentsOf(o, legs[0].date);
  const search = buildUrl({ ...common, legs: legs.map((l) => ({ ...l })), multi });   // busca da data, o formato que o coletor já usa
  if (!segs) return { url: search, exact: false };
  legs[0].segments = segs;
  return { url: buildUrl({ ...common, legs, multi }), exact: true, search };
}

if (typeof window !== "undefined") window.FlightLink = { flightLink, buildUrl, segmentsOf };
