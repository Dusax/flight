// Evolução do preço de um voo específico (aba "Voos acompanhados"). Funções puras, testadas em Node.
// Um voo é identificado por número + horário de saída (que inclui o dia), igual a flights/db.py (flight_key).

export const flightKey = (o) => `${o.flight_numbers}|${o.dep_time || ""}`;

/** Série do voo na combinação de datas: [{t, ts, p, min}] uma por coleta; p = null quando o voo não apareceu. */
export function flightPoints(combo, key) {
  const s = combo?.series;
  const arr = s?.f?.[key];
  if (!s || !arr) return [];
  return s.t.map((ts, i) => ({ t: Date.parse(ts), ts, p: arr[i] ?? null, min: s.min?.[i] ?? null }));
}

/** Indicadores do voo: atual, menor/maior (com quando), primeiro, variações e quantas vezes apareceu. */
export function flightStats(points) {
  const seen = points.filter((q) => q.p != null);
  if (!seen.length) return null;
  const pick = (cmp) => seen.reduce((b, q) => (cmp(q.p, b.p) ? q : b), seen[0]);
  const cur = seen[seen.length - 1], first = seen[0], prev = seen.length > 1 ? seen[seen.length - 2] : null;
  const min = pick((a, b) => a < b), max = pick((a, b) => a > b);
  return {
    seen: seen.length, total: points.length,
    current: cur.p, currentAt: cur.ts, first: first.p, firstAt: first.ts,
    min: min.p, minAt: min.ts, max: max.p, maxAt: max.ts,
    sinceFirst: seen.length > 1 ? cur.p - first.p : null,
    sincePrev: prev ? cur.p - prev.p : null, prevAt: prev ? prev.ts : null,
    inLatest: points[points.length - 1].p != null,       // apareceu na última coleta da data?
    lastSeenAt: cur.ts,
  };
}

/** Linhas da tabela de coletas, da mais nova para a mais antiga; variação é contra a coleta anterior em que o voo apareceu. */
export function tableRows(points) {
  let prev = null;
  const rows = points.map((q) => {
    const r = { ts: q.ts, p: q.p, absent: q.p == null, delta: q.p != null && prev != null ? q.p - prev : null, min: q.min };
    if (q.p != null) prev = q.p;
    return r;
  });
  return rows.reverse();
}

/** Mesmo número de voo e mesmo horário do dia em outras datas: [{index, depart_date, price, here}], por data. */
export function otherDates(combos, offer) {
  if (!offer?.flight_numbers) return [];
  const hhmm = (offer.dep_time || "").slice(11, 16);
  const out = [];
  combos.forEach((c, index) => {
    const o = (c.offers || []).find((x) => x.flight_numbers === offer.flight_numbers && (x.dep_time || "").slice(11, 16) === hhmm);
    if (o) out.push({ index, depart_date: c.depart_date, end_date: c.end_date, price: o.price, dep_time: o.dep_time, here: o.dep_time === offer.dep_time });
  });
  return out.sort((a, b) => a.depart_date.localeCompare(b.depart_date));
}

if (typeof window !== "undefined") window.Series = { flightKey, flightPoints, flightStats, tableRows, otherDates };
