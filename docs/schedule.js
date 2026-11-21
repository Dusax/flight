// Horários de coleta em cron, no fuso de Brasília. Espelha flights/schedule.py (os dois passam pelos mesmos vetores
// de tests/schedule_cases.json). Usado pelo painel (prévia, formulário) e pelo servidor (validação).
// A coleta roda de hora em hora: a unidade é a hora cheia e o campo de minutos é aceito, mas ignorado.

export const TZ = "America/Sao_Paulo";
export const CATCHUP_H = 3;            // rodada perdida (o GitHub atrasa/pula) ainda é coletada até 3 horas cheias depois
const HOUR = 3600e3;
const DOW_NAMES = { SUN: 0, MON: 1, TUE: 2, WED: 3, THU: 4, FRI: 5, SAT: 6 };
const MONTH_NAMES = Object.fromEntries("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(" ").map((n, i) => [n, i + 1]));
const WD = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };

let fmt = null;
function localParts(ms) {
  fmt ||= new Intl.DateTimeFormat("en-US", { timeZone: TZ, hourCycle: "h23", month: "numeric", day: "numeric", hour: "numeric", weekday: "short" });
  const p = {};
  for (const x of fmt.formatToParts(new Date(ms))) p[x.type] = x.value;
  return { month: +p.month, day: +p.day, hour: +p.hour % 24, dow: WD[p.weekday] };
}

function field(text, lo, hi, what, names, wrap) {
  const out = new Set();
  for (const part of text.split(",")) {
    if (!part) throw new Error(`${what}: campo vazio`);
    const [rng, stepS] = part.split("/");
    let step = 1;
    if (stepS !== undefined) {
      if (!/^\d+$/.test(stepS) || +stepS < 1) throw new Error(`${what}: passo inválido em '${part}'`);
      step = +stepS;
    }
    const val = (tok) => {
      const t = tok.toUpperCase();
      if (names && t in names) return names[t];
      if (!/^\d+$/.test(tok)) throw new Error(`${what}: valor inválido '${tok}'`);
      const v = +tok;
      if (v < lo || v > hi) throw new Error(`${what}: ${v} fora de ${lo}-${hi}`);
      return v;
    };
    let a, b;
    if (rng === "*") { a = lo; b = hi; }
    else if (rng.includes("-")) { const [x, y] = rng.split("-"); a = val(x); b = val(y); }
    else { a = val(rng); b = stepS !== undefined ? hi : a; }
    let vals = [];
    if (a <= b) for (let v = a; v <= b; v++) vals.push(v);
    else if (wrap) { for (let v = a; v <= hi; v++) vals.push(v); for (let v = lo; v <= b; v++) vals.push(v); }   // 22-5 = 22,23,0..5
    else throw new Error(`${what}: faixa ${a}-${b} ao contrário`);
    vals.filter((_, i) => i % step === 0).forEach((v) => out.add(v));
  }
  return [out, text.startsWith("*")];
}

export function parse(expr) {
  if (typeof expr !== "string") throw new Error("horários: expressão inválida");
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5 || parts[0] === "") throw new Error("horários: use 5 campos (minuto hora dia-do-mês mês dia-da-semana), ex.: 0 22-23,0-5 * * *");
  field(parts[0], 0, 59, "minuto");
  const [hours] = field(parts[1], 0, 23, "hora", null, true);
  const [dom, domAny] = field(parts[2], 1, 31, "dia do mês");
  const [months] = field(parts[3], 1, 12, "mês", MONTH_NAMES);
  const [dow0, dowAny] = field(parts[4], 0, 7, "dia da semana", DOW_NAMES);
  const dow = new Set([...dow0].map((d) => (d === 7 ? 0 : d)));
  return { expr: parts.join(" "), parts, hours, dom, months, dow, domAny, dowAny };
}

export function matches(s, ms) {
  const t = localParts(ms);
  if (!s.hours.has(t.hour) || !s.months.has(t.month)) return false;
  const dowOk = s.dow.has(t.dow), domOk = s.dom.has(t.day);
  if (s.domAny && s.dowAny) return true;
  if (s.domAny) return dowOk;
  if (s.dowAny) return domOk;
  return domOk || dowOk;
}
const floorHour = (ms) => Math.floor(ms / HOUR) * HOUR;

export function latestSlot(s, nowMs, lookback = CATCHUP_H) {
  const base = floorHour(nowMs);
  for (let k = 0; k <= lookback; k++) if (matches(s, base - k * HOUR)) return base - k * HOUR;
  return null;
}
export function nextSlot(s, afterMs, horizonDays = 400) {
  let t = floorHour(afterMs) + HOUR;
  for (let i = 0; i < horizonDays * 24; i++, t += HOUR) if (matches(s, t)) return t;
  return null;
}
export function due(s, nowMs, lastMs) {
  const slot = latestSlot(s, nowMs);
  return slot !== null && (lastMs == null || lastMs < slot);
}
export function nextSlots(expr, n = 5, fromMs = Date.now()) {
  const s = parse(expr), out = [];
  let t = fromMs;
  for (let i = 0; i < n; i++) { t = nextSlot(s, t); if (t === null) break; out.push(t); }
  return out;
}

/* ---------- formulário simples <-> cron ---------- */
const EVERY_OPTIONS = [1, 2, 3, 4, 6, 12];
function genHours({ from, to, every }) {
  const out = [];
  if (from == null) { for (let h = 0; h < 24; h += every) out.push(h); return out; }
  const span = (to - from + 24) % 24;
  for (let k = 0; k * every <= span; k++) out.push((from + k * every) % 24);
  return out;
}
function compress(sorted) {           // 0,1,2,3,4,5,22,23 -> "0-5,22,23" (só junta sequências de 3 ou mais)
  const parts = []; let i = 0;
  while (i < sorted.length) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++;
    if (j - i >= 2) parts.push(`${sorted[i]}-${sorted[j]}`);
    else for (let k = i; k <= j; k++) parts.push(String(sorted[k]));
    i = j + 1;
  }
  return parts.join(",");
}
/** { from, to, every, days } -> cron. from/to = hora (0-23) ou null para o dia todo; days = 0 (dom) a 6 (sáb). */
export function compile({ from = null, to = null, every = 1, days = [0, 1, 2, 3, 4, 5, 6] }) {
  const hours = [...new Set(genHours({ from, to, every }))].sort((a, b) => a - b);
  const h = hours.length === 24 ? "*" : from == null ? `*/${every}` : compress(hours);
  const d = [...new Set(days)].sort((a, b) => a - b);
  return `0 ${h} * * ${d.length === 7 ? "*" : compress(d)}`;
}
const sameSet = (a, b) => a.size === b.length && b.every((x) => a.has(x));
/** cron -> { from, to, every, days } se couber no formulário simples; senão null (modo avançado). */
export function decompile(expr) {
  let s;
  try { s = parse(expr); } catch { return null; }
  if (s.parts[0] !== "0" || !s.domAny || s.parts[3] !== "*") return null;
  const days = s.dowAny ? [0, 1, 2, 3, 4, 5, 6] : [...s.dow].sort((a, b) => a - b);
  for (const every of EVERY_OPTIONS) if (sameSet(s.hours, genHours({ from: null, every }))) return { from: null, to: null, every, days };
  const H = [...s.hours].sort((a, b) => a - b);
  for (const every of EVERY_OPTIONS) for (const from of H) for (const to of H) if (sameSet(s.hours, genHours({ from, to, every }))) return { from, to, every, days };
  return null;
}

const DAY = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"];
export function describe(expr) {
  const d = decompile(expr);
  if (!d) return `Cron: ${expr}`;
  const k = d.days.join();
  const days = d.days.length === 7 ? "Todos os dias" : k === "1,2,3,4,5" ? "Seg a sex" : k === "0,6" ? "Fins de semana"
    : d.days.length === 1 ? `Só ${DAY[d.days[0]]}` : d.days.map((x) => DAY[x]).join(", ");
  const when = d.from == null ? "o dia todo" : d.from === d.to ? `às ${d.from}h` : `das ${d.from}h às ${d.to}h`;
  const rep = d.every === 1 ? "toda hora" : `de ${d.every} em ${d.every} h`;
  return d.from === d.to && d.from != null ? `${days}, ${when}` : `${days}, ${when}, ${rep}`;
}

if (typeof window !== "undefined") window.Schedule = { TZ, parse, matches, latestSlot, nextSlot, nextSlots, due, compile, decompile, describe, EVERY_OPTIONS };
