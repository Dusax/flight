from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .models import Leg, ScrapeResult, SearchSpec, legs_from_json, parse_date, parse_iata, plan_specs

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id           INTEGER PRIMARY KEY,
    label        TEXT,
    origin       TEXT NOT NULL,
    destination  TEXT NOT NULL,
    depart_date  TEXT NOT NULL,
    return_date  TEXT,
    flex_days    INTEGER NOT NULL DEFAULT 0,
    adults       INTEGER NOT NULL DEFAULT 1,
    cabin        TEXT NOT NULL DEFAULT 'economy',
    max_stops    INTEGER,
    currency     TEXT NOT NULL DEFAULT 'BRL',
    every_hours  INTEGER,                 -- frequência da coleta agendada; NULL = só manual
    legs_json    TEXT,                    -- multi-destino: [{origin,destination,date}, ...]
    compare_separate INTEGER NOT NULL DEFAULT 0,  -- também busca cada trecho como só ida
    booking_top  INTEGER NOT NULL DEFAULT 0,      -- abre a tela de compra dos N voos mais baratos (só ida)
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL
);

-- Uma linha por coleta (uma consulta ao Google Voos).
CREATE TABLE IF NOT EXISTS searches (
    id           INTEGER PRIMARY KEY,
    watch_id     INTEGER REFERENCES watches(id) ON DELETE SET NULL,
    scraped_at   TEXT NOT NULL,           -- UTC, ISO 8601
    origin       TEXT NOT NULL,
    destination  TEXT NOT NULL,
    depart_date  TEXT NOT NULL,
    return_date  TEXT,
    trip_type    TEXT NOT NULL,           -- one_way | round_trip | multi_city
    legs_json    TEXT,                    -- multi-destino: todos os trechos
    leg_index    INTEGER,                 -- preenchido quando a busca é "um trecho como só ida" (comparação)
    adults       INTEGER NOT NULL,
    cabin        TEXT NOT NULL,
    max_stops    INTEGER,
    currency     TEXT NOT NULL,
    status       TEXT NOT NULL,           -- ok | empty | error | blocked
    error        TEXT,
    offers_count INTEGER NOT NULL DEFAULT 0,
    min_price    REAL,
    min_nonstop  REAL,                    -- menor preço entre os voos diretos
    fastest_min  INTEGER,                 -- menor duração (min) entre os voos coletados
    price_level  TEXT,                    -- low | typical | high (avaliação do Google)
    url          TEXT
);

-- Uma linha por voo encontrado em cada coleta.
-- price = total para TODOS os passageiros (ida e volta, se a busca for ida e volta).
CREATE TABLE IF NOT EXISTS offers (
    id               INTEGER PRIMARY KEY,
    search_id        INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    position         INTEGER NOT NULL,
    section          TEXT,
    price            REAL NOT NULL,
    currency         TEXT NOT NULL,
    airline          TEXT,
    operated_by      TEXT,
    flight_numbers   TEXT,
    origin           TEXT,
    destination      TEXT,
    dep_time         TEXT,               -- horário local, ISO
    arr_time         TEXT,
    duration_min     INTEGER,
    stops            INTEGER,
    layover_airports TEXT,
    layovers_json    TEXT,
    -- detalhes do card expandido (NULL quando o voo não foi detalhado)
    cabin            TEXT,
    aircraft         TEXT,
    co2_kg           INTEGER,
    co2_diff_pct     INTEGER,            -- vs. média da rota; negativo = emite menos
    overnight        INTEGER,
    often_delayed    INTEGER,
    segments_json    TEXT,               -- um item por voo da viagem: aeronave, assentos, comodidades, conexão...
    booking_json     TEXT                -- tela de compra: bagagem, vendedores, faixa típica de preço
);

CREATE INDEX IF NOT EXISTS idx_offers_search   ON offers(search_id);
CREATE INDEX IF NOT EXISTS idx_searches_route  ON searches(origin, destination, depart_date, scraped_at);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn) -> None:
    """Bancos criados por versões anteriores ganham as colunas novas."""
    wanted = {
        "watches": {"every_hours": "INTEGER", "legs_json": "TEXT", "compare_separate": "INTEGER NOT NULL DEFAULT 0",
                    "booking_top": "INTEGER NOT NULL DEFAULT 0"},
        "searches": {"min_nonstop": "REAL", "fastest_min": "INTEGER", "legs_json": "TEXT", "leg_index": "INTEGER"},
        "offers": {"cabin": "TEXT", "aircraft": "TEXT", "co2_kg": "INTEGER", "co2_diff_pct": "INTEGER",
                   "overnight": "INTEGER", "often_delayed": "INTEGER", "segments_json": "TEXT", "booking_json": "TEXT"},
    }
    for table, cols in wanted.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, ddl in cols.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    conn.commit()


# --- watches ---------------------------------------------------------------

def add_watch(conn, spec: SearchSpec, flex_days: int = 0, label: Optional[str] = None,
              every_hours: Optional[int] = None, compare_separate: bool = False, booking_top: int = 0) -> int:
    cur = conn.execute(
        """INSERT INTO watches (label, origin, destination, depart_date, return_date, flex_days,
                                adults, cabin, max_stops, currency, every_hours, legs_json, compare_separate,
                                booking_top, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (label, spec.origin, spec.destination, spec.depart_date.isoformat(),
         spec.return_date.isoformat() if spec.return_date else None, flex_days,
         spec.adults, spec.cabin, spec.max_stops, spec.currency, every_hours, spec.legs_json,
         int(compare_separate), booking_top, utcnow()),
    )
    conn.commit()
    return cur.lastrowid


def list_watches(conn, only_active: bool = False) -> List[sqlite3.Row]:
    q = "SELECT * FROM watches" + (" WHERE active = 1" if only_active else "") + " ORDER BY id"
    return conn.execute(q).fetchall()


def remove_watch(conn, watch_id: int) -> bool:
    cur = conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
    conn.commit()
    return cur.rowcount > 0


def set_watch_active(conn, watch_id: int, active: bool) -> bool:
    cur = conn.execute("UPDATE watches SET active = ? WHERE id = ?", (int(active), watch_id))
    conn.commit()
    return cur.rowcount > 0


def watch_to_spec(row: sqlite3.Row) -> SearchSpec:
    legs = legs_from_json(row["legs_json"])
    return SearchSpec(
        origin=row["origin"], destination=row["destination"],
        depart_date=date.fromisoformat(row["depart_date"]),
        return_date=date.fromisoformat(row["return_date"]) if row["return_date"] else None,
        adults=row["adults"], cabin=row["cabin"], max_stops=row["max_stops"], currency=row["currency"],
        legs=legs,
    )


def row_route(row) -> str:
    """Rota legível de uma linha de `watches` ou `searches` (GRU→LIS, GRU⇄LIS, GRU→LIS→MAD→GRU)."""
    legs = legs_from_json(row["legs_json"])
    if legs:
        return "→".join([legs[0].origin] + [l.destination for l in legs])
    return f"{row['origin']}{'⇄' if row['return_date'] else '→'}{row['destination']}"


# --- coletas ---------------------------------------------------------------

def save_search(
    conn,
    spec: SearchSpec,
    result: Optional[ScrapeResult] = None,
    error: Optional[str] = None,
    status: Optional[str] = None,
    watch_id: Optional[int] = None,
    url: Optional[str] = None,
    max_offers: Optional[int] = None,
) -> int:
    """Grava a coleta. Com `max_offers`, só os N voos mais baratos vão para a tabela
    `offers` (os agregados min_price/min_nonstop/fastest_min usam todos os voos)."""
    all_offers = result.offers if result else []
    offers = sorted(all_offers, key=lambda o: (o.price, o.duration_min or 0))
    if max_offers:
        offers = offers[:max_offers]
    if status is None:
        status = "error" if error else ("ok" if offers else "empty")
    cur = conn.execute(
        """INSERT INTO searches (watch_id, scraped_at, origin, destination, depart_date, return_date, trip_type,
                                 legs_json, leg_index, adults, cabin, max_stops, currency, status, error, offers_count, min_price,
                                 min_nonstop, fastest_min, price_level, url)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (watch_id, utcnow(), spec.origin, spec.destination, spec.depart_date.isoformat(),
         spec.return_date.isoformat() if spec.return_date else None, spec.trip_type,
         spec.legs_json, spec.leg_index, spec.adults, spec.cabin, spec.max_stops, spec.currency, status, error, len(all_offers),
         min((o.price for o in all_offers), default=None),
         min((o.price for o in all_offers if o.stops == 0), default=None),
         min((o.duration_min for o in all_offers if o.duration_min), default=None),
         result.price_level if result else None,
         (result.url if result else url)),
    )
    search_id = cur.lastrowid
    conn.executemany(
        """INSERT INTO offers (search_id, position, section, price, currency, airline, operated_by, flight_numbers,
                               origin, destination, dep_time, arr_time, duration_min, stops, layover_airports,
                               layovers_json, cabin, aircraft, co2_kg, co2_diff_pct, overnight, often_delayed,
                               segments_json, booking_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(search_id, o.position, o.section, o.price, o.currency, o.airline, o.operated_by, o.flight_numbers,
          o.origin, o.destination, o.dep_time, o.arr_time, o.duration_min, o.stops, o.layover_airports,
          json.dumps(o.layovers, ensure_ascii=False) if o.layovers else None,
          o.cabin, o.aircraft, o.co2_kg, o.co2_diff_pct,
          None if o.overnight is None else int(o.overnight), None if o.often_delayed is None else int(o.often_delayed),
          json.dumps(o.segments, ensure_ascii=False, separators=(",", ":")) if o.segments else None,
          json.dumps(o.booking, ensure_ascii=False, separators=(",", ":")) if o.booking else None) for o in offers],
    )
    conn.commit()
    return search_id


def search_offers(conn, search_id: int, limit: Optional[int] = None) -> List[sqlite3.Row]:
    q = "SELECT * FROM offers WHERE search_id = ? ORDER BY price, duration_min"
    return conn.execute(q + (f" LIMIT {int(limit)}" if limit else ""), (search_id,)).fetchall()


def history(conn, origin: Optional[str] = None, destination: Optional[str] = None, depart_date: Optional[str] = None,
            return_date: Optional[str] = None, watch_id: Optional[int] = None) -> List[sqlite3.Row]:
    """Evolução do menor preço, coleta a coleta."""
    q = """SELECT s.id, s.scraped_at, s.depart_date, s.return_date, s.adults, s.cabin, s.currency,
                   s.min_price, s.offers_count, s.price_level,
                   COALESCE(s.min_nonstop, (SELECT MIN(price) FROM offers o WHERE o.search_id = s.id AND o.stops = 0)) AS min_nonstop
            FROM searches s
            WHERE s.status = 'ok'"""
    args: list = []
    if watch_id is not None:
        q += " AND s.watch_id = ?"
        args.append(watch_id)
    else:  # por rota: multi-destino não entra (o preço é de outra coisa: a viagem inteira)
        q += " AND s.origin = ? AND s.destination = ? AND s.trip_type != 'multi_city'"
        args += [origin, destination]
    if depart_date:
        q += " AND s.depart_date = ?"
        args.append(depart_date)
    if return_date:
        q += " AND s.return_date = ?"
        args.append(return_date)
    return conn.execute(q + " ORDER BY s.depart_date, s.scraped_at", args).fetchall()


def recent_searches(conn, limit: int = 20) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM searches ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def export_rows(conn, search_id: Optional[int] = None) -> Iterable[sqlite3.Row]:
    q = """SELECT s.id AS search_id, s.scraped_at, s.trip_type, s.adults, s.cabin, s.price_level,
                  s.depart_date, s.return_date,
                  o.position, o.section, o.price, ROUND(o.price * 1.0 / s.adults, 2) AS price_per_adult, o.currency, o.airline, o.operated_by, o.flight_numbers,
                  o.origin, o.destination, o.dep_time, o.arr_time, o.duration_min, o.stops, o.layover_airports,
                  o.cabin AS offer_cabin, o.aircraft, o.co2_kg, o.co2_diff_pct, o.overnight, o.often_delayed, s.legs_json, s.leg_index,
                  o.booking_json
           FROM offers o JOIN searches s ON s.id = o.search_id"""
    if search_id is not None:
        return conn.execute(q + " WHERE s.id = ? ORDER BY o.price", (search_id,)).fetchall()
    return conn.execute(q + " ORDER BY s.id, o.price").fetchall()


# --- agendamento ---------------------------------------------------------------

DUE_TOLERANCE_S = 300  # o cron do GitHub atrasa/adianta alguns minutos; não perde a rodada por isso


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def last_run_at(conn, watch_id: int) -> Optional[str]:
    row = conn.execute("SELECT MAX(scraped_at) AS t FROM searches WHERE watch_id = ?", (watch_id,)).fetchone()
    return row["t"]


def next_run_at(row: sqlite3.Row, last: Optional[str]) -> Optional[str]:
    if not row["active"] or not row["every_hours"]:
        return None
    if not last:
        return utcnow()
    return (_parse_ts(last) + timedelta(hours=row["every_hours"])).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_due(conn, row: sqlite3.Row, now: Optional[datetime] = None) -> bool:
    """Ativa, com frequência definida, e a última coleta (de qualquer status) é mais antiga que ela."""
    nxt = next_run_at(row, last_run_at(conn, row["id"]))
    if nxt is None:
        return False
    now = now or datetime.now(timezone.utc)
    return _parse_ts(nxt) - timedelta(seconds=DUE_TOLERANCE_S) <= now


# --- sincronização com watches.json (a página edita esse arquivo) ----------------

def _spec_from_item(it: dict) -> SearchSpec:
    common = dict(adults=int(it.get("adults") or 1), cabin=it.get("cabin") or "economy",
                  max_stops=None if it.get("max_stops") in (None, "") else int(it["max_stops"]),
                  currency=(it.get("currency") or "BRL").upper())
    if it.get("legs"):
        legs = [Leg(parse_iata(l["origin"]), parse_iata(l["destination"]), parse_date(l["date"])) for l in it["legs"]]
        return SearchSpec.multi(legs, **common)
    return SearchSpec(origin=parse_iata(it["origin"]), destination=parse_iata(it["destination"]),
                      depart_date=parse_date(it["depart_date"]),
                      return_date=parse_date(it["return_date"]) if it.get("return_date") else None, **common)


def sync_watches(conn, items: List[dict]) -> Tuple[int, List[str]]:
    """Faz a tabela `watches` espelhar a lista do JSON (por id). Devolve (qtd, avisos).

    Cada item é uma busca só de ida, de ida e volta (`return_date`) ou multi-destino (`legs`).
    Itens inválidos são ignorados com aviso; ids que sumiram do JSON são apagados
    (as coletas antigas ficam no banco, sem vínculo).
    """
    warnings, keep = [], []
    for it in items:
        try:
            wid = int(it["id"])
            spec = _spec_from_item(it)
            flex = int(it.get("flex_days") or 0)
            every = int(it["every_hours"]) if it.get("every_hours") else None
            booking_top = int(it.get("booking_top") or 0)
            if not 0 <= flex <= 7:
                raise ValueError("flex_days deve ficar entre 0 e 7")
            if every is not None and every < 1:
                raise ValueError("every_hours deve ser >= 1")
            if not 0 <= booking_top <= 10:
                raise ValueError("booking_top deve ficar entre 0 e 10")
        except (KeyError, ValueError, TypeError) as e:
            warnings.append(f"item ignorado ({it.get('id', '?') if isinstance(it, dict) else '?'}): {e}")
            continue
        vals = (spec.origin, spec.destination, spec.depart_date.isoformat(),
                spec.return_date.isoformat() if spec.return_date else None, flex, spec.adults, spec.cabin,
                spec.max_stops, spec.currency, every, spec.legs_json, int(bool(it.get("compare_separate"))),
                booking_top, int(it.get("active", True)), (it.get("label") or None))
        exists = conn.execute("SELECT 1 FROM watches WHERE id = ?", (wid,)).fetchone()
        if exists:
            conn.execute("""UPDATE watches SET origin=?, destination=?, depart_date=?, return_date=?, flex_days=?,
                            adults=?, cabin=?, max_stops=?, currency=?, every_hours=?, legs_json=?,
                            compare_separate=?, booking_top=?, active=?, label=? WHERE id=?""", vals + (wid,))
        else:
            conn.execute("""INSERT INTO watches (origin, destination, depart_date, return_date, flex_days, adults,
                            cabin, max_stops, currency, every_hours, legs_json, compare_separate, booking_top,
                            active, label, id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         vals + (wid, utcnow()))
        keep.append(wid)
    if keep:
        conn.execute(f"DELETE FROM watches WHERE id NOT IN ({','.join('?' * len(keep))})", keep)
    else:
        conn.execute("DELETE FROM watches")
    conn.commit()
    return len(keep), warnings


# --- JSON do painel ------------------------------------------------------------

HISTORY_POINTS = 240   # pontos de histórico por combinação de datas
OFFERS_PER_COMBO = 30  # voos exibidos por combinação de datas
OFFERS_PER_LEG = 10    # voos exibidos por trecho na comparação "separado"

_OFFER_COLS = """price, airline, operated_by, flight_numbers, origin, destination, dep_time, arr_time, duration_min,
                 stops, layover_airports, cabin, aircraft, co2_kg, co2_diff_pct, overnight, often_delayed,
                 segments_json, booking_json"""


def _offer_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["segments"] = json.loads(d.pop("segments_json")) if d.get("segments_json") else None
    d["booking"] = json.loads(d.pop("booking_json")) if d.get("booking_json") else None
    for k in ("overnight", "often_delayed"):
        d[k] = None if d[k] is None else bool(d[k])
    return d


def _rows_for(conn, watch_id: int, spec: SearchSpec) -> List[sqlite3.Row]:
    """Coletas ok de uma busca exata (datas, trechos, passageiros, classe...), da mais antiga à mais nova."""
    rows = conn.execute(
        """SELECT id, scraped_at, min_price, min_nonstop, fastest_min, price_level, offers_count
           FROM searches
           WHERE watch_id = ? AND status = 'ok' AND depart_date = ?
             AND COALESCE(return_date,'') = COALESCE(?, '') AND COALESCE(legs_json,'') = COALESCE(?, '')
             AND adults = ? AND cabin = ? AND COALESCE(max_stops,-1) = COALESCE(?, -1) AND currency = ?
             AND COALESCE(leg_index,-1) = COALESCE(?, -1)
           ORDER BY scraped_at, id""",
        (watch_id, spec.depart_date.isoformat(), spec.return_date.isoformat() if spec.return_date else None,
         spec.legs_json, spec.adults, spec.cabin, spec.max_stops, spec.currency, spec.leg_index)).fetchall()
    return [r for r in rows if r["min_price"] is not None]


def _offers_for(conn, search_id: int, limit: int) -> List[dict]:
    return [_offer_dict(o) for o in conn.execute(
        f"SELECT {_OFFER_COLS} FROM offers WHERE search_id = ? ORDER BY price, duration_min LIMIT ?",
        (search_id, limit))]


def _separate(conn, watch_id: int, trip: SearchSpec, trip_min: float) -> Optional[dict]:
    """Soma dos voos mais baratos de cada trecho como só ida, para comparar com o pacote."""
    legs, total, total_nonstop, complete, nonstop_complete = [], 0.0, 0.0, True, True
    for leg, leg_spec in zip(trip.all_legs(), trip.leg_specs()):
        rows = _rows_for(conn, watch_id, leg_spec)
        cur = rows[-1] if rows else None
        item = {"index": leg_spec.leg_index, "origin": leg.origin, "destination": leg.destination,
                "date": leg.date.isoformat(), "min_price": None, "min_nonstop": None, "fastest_min": None,
                "scraped_at": None, "price_level": None, "offers": []}
        if cur:
            item.update(min_price=cur["min_price"], min_nonstop=cur["min_nonstop"], fastest_min=cur["fastest_min"],
                        scraped_at=cur["scraped_at"], price_level=cur["price_level"],
                        offers=_offers_for(conn, cur["id"], OFFERS_PER_LEG))
            total += cur["min_price"]
            if cur["min_nonstop"] is None:
                nonstop_complete = False
            else:
                total_nonstop += cur["min_nonstop"]
        else:
            complete = nonstop_complete = False
        legs.append(item)
    if not any(l["min_price"] is not None for l in legs):
        return None
    return {"legs": legs, "total": total if complete else None,
            "total_nonstop": total_nonstop if nonstop_complete else None,
            "vs_trip": (total - trip_min) if complete else None}  # negativo = separado compensa


def build_dashboard(conn, today: Optional[date] = None) -> Dict:
    """Tudo que a página precisa, num JSON só. Só entra o que está em `watches` e bate com a
    configuração atual da busca (se você editar as datas, as coletas antigas deixam de aparecer)."""
    today = today or date.today()
    out_watches = []
    for w in list_watches(conn):
        spec = watch_to_spec(w)
        last = last_run_at(conn, w["id"])
        last_attempt = conn.execute(
            "SELECT status, error, scraped_at FROM searches WHERE watch_id = ? ORDER BY id DESC LIMIT 1",
            (w["id"],)).fetchone()
        compare = bool(w["compare_separate"])
        combos = []
        for trip in plan_specs(spec, w["flex_days"], compare, today):
            if trip.leg_index is not None:
                continue
            rows = _rows_for(conn, w["id"], trip)
            if not rows:
                continue
            cur = rows[-1]
            combos.append({
                "depart_date": trip.depart_date.isoformat(),
                "return_date": trip.return_date.isoformat() if trip.return_date else None,
                "end_date": trip.end_date.isoformat(),
                "legs": [{"origin": l.origin, "destination": l.destination, "date": l.date.isoformat()}
                         for l in trip.all_legs()],
                "scraped_at": cur["scraped_at"], "min_price": cur["min_price"],
                "min_nonstop": cur["min_nonstop"], "fastest_min": cur["fastest_min"],
                "price_level": cur["price_level"], "offers_count": cur["offers_count"],
                "first_price": rows[0]["min_price"], "first_at": rows[0]["scraped_at"],
                "prev_price": rows[-2]["min_price"] if len(rows) > 1 else None,
                "history": [[r["scraped_at"], r["min_price"]] for r in rows[-HISTORY_POINTS:]],
                "offers": _offers_for(conn, cur["id"], OFFERS_PER_COMBO),
                "separate": _separate(conn, w["id"], trip, cur["min_price"]) if compare and len(trip.all_legs()) > 1 else None,
            })
        dep_last = spec.depart_date + timedelta(days=w["flex_days"])  # última data de ida possível
        out_watches.append({
            "id": w["id"], "label": w["label"], "origin": w["origin"], "destination": w["destination"],
            "depart_date": w["depart_date"], "return_date": w["return_date"], "flex_days": w["flex_days"],
            "adults": w["adults"], "cabin": w["cabin"], "max_stops": w["max_stops"], "currency": w["currency"],
            "every_hours": w["every_hours"], "active": bool(w["active"]),
            "trip_type": spec.trip_type, "route": spec.route(),
            "legs": [{"origin": l.origin, "destination": l.destination, "date": l.date.isoformat()} for l in spec.legs] or None,
            "compare_separate": compare, "booking_top": w["booking_top"],
            "expired": dep_last < today,
            "last_run_at": last, "next_run_at": next_run_at(w, last),
            "last_status": last_attempt["status"] if last_attempt else None,
            "last_error": last_attempt["error"] if last_attempt else None,
            "combos": combos,
        })
    return {"generated_at": utcnow(), "watches": out_watches}
