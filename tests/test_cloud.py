import json
from datetime import date, datetime, timedelta, timezone

import pytest

from flights import cli, db
from flights.models import Offer, ScrapeResult, SearchSpec

# Item exatamente como a página (docs/index.html) grava em data/watches.json.
PAGE_ITEM = {"id": 4, "label": "Teste Madri", "origin": "GRU", "destination": "MAD", "depart_date": "2027-02-10",
             "return_date": "2027-02-24", "flex_days": 2, "adults": 2, "cabin": "business", "max_stops": 1,
             "currency": "BRL", "every_hours": 12, "active": True}


def offer(price, stops=0, dur=600, **kw):
    base = dict(price=price, currency="BRL", airline="LATAM", operated_by=None, origin="GRU", destination="LIS",
                dep_time="2027-02-10T10:00", arr_time="2027-02-10T20:00", duration_min=dur, stops=stops,
                flight_numbers="LA 1", layover_airports=None, layovers=[], section="Top", position=1)
    base.update(kw)
    return Offer(**base)


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "c.db")


def test_sync_accepts_page_item_and_mirrors_list(conn):
    n, warns = db.sync_watches(conn, [PAGE_ITEM, {**PAGE_ITEM, "id": 5, "return_date": None, "every_hours": None}])
    assert (n, warns) == (2, [])
    w = db.list_watches(conn)[0]
    assert (w["origin"], w["cabin"], w["flex_days"], w["every_hours"], w["max_stops"]) == ("GRU", "business", 2, 12, 1)
    assert db.watch_to_spec(w).return_date == date(2027, 2, 24)
    # editar mantém o id; sumir do JSON apaga; lista vazia apaga tudo
    db.sync_watches(conn, [{**PAGE_ITEM, "adults": 3, "active": False}])
    rows = db.list_watches(conn)
    assert [(r["id"], r["adults"], r["active"]) for r in rows] == [(4, 3, 0)]
    db.sync_watches(conn, [])
    assert db.list_watches(conn) == []


def test_sync_skips_invalid_items_with_warning(conn):
    n, warns = db.sync_watches(conn, [PAGE_ITEM, {**PAGE_ITEM, "id": 9, "origin": "SAOP"}, {"id": 10}, {**PAGE_ITEM, "id": 11, "flex_days": 30}])
    assert n == 1 and len(warns) == 3
    assert [r["id"] for r in db.list_watches(conn)] == [4]


def test_due_logic(conn):
    db.sync_watches(conn, [PAGE_ITEM, {**PAGE_ITEM, "id": 5, "every_hours": None}, {**PAGE_ITEM, "id": 6, "active": False}])
    w4, w5, w6 = db.list_watches(conn)
    spec = db.watch_to_spec(w4)
    assert db.is_due(conn, w4)            # nunca rodou
    assert not db.is_due(conn, w5)        # sem frequência = só manual
    assert not db.is_due(conn, w6)        # pausada
    sid = db.save_search(conn, spec, ScrapeResult([offer(1)], "u"), watch_id=4)
    now = datetime.now(timezone.utc)
    assert not db.is_due(conn, w4, now)                                   # acabou de coletar
    assert not db.is_due(conn, w4, now + timedelta(hours=11))
    assert db.is_due(conn, w4, now + timedelta(hours=11, minutes=56))     # dentro da tolerância do cron
    assert db.is_due(conn, w4, now + timedelta(hours=13))
    # tentativa que falhou também conta: não fica martelando o Google a cada hora
    conn.execute("DELETE FROM searches WHERE id = ?", (sid,))
    db.save_search(conn, spec, error="boom", watch_id=4)
    assert not db.is_due(conn, w4, datetime.now(timezone.utc))


def test_max_offers_keeps_cheapest_but_aggregates_use_all(conn):
    spec = SearchSpec("GRU", "LIS", date(2027, 2, 10))
    offers = [offer(100 + i * 10, stops=1, dur=900 - i) for i in range(10)] + [offer(900, stops=0, dur=300)]
    sid = db.save_search(conn, spec, ScrapeResult(offers, "u"), max_offers=3)
    s = conn.execute("SELECT * FROM searches WHERE id = ?", (sid,)).fetchone()
    assert (s["offers_count"], s["min_price"], s["min_nonstop"], s["fastest_min"]) == (11, 100, 900, 300)
    assert [o["price"] for o in db.search_offers(conn, sid)] == [100, 110, 120]


def test_old_database_is_migrated(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.executescript("""CREATE TABLE watches (id INTEGER PRIMARY KEY, label TEXT, origin TEXT NOT NULL, destination TEXT NOT NULL,
        depart_date TEXT NOT NULL, return_date TEXT, flex_days INTEGER NOT NULL DEFAULT 0, adults INTEGER NOT NULL DEFAULT 1,
        cabin TEXT NOT NULL DEFAULT 'economy', max_stops INTEGER, currency TEXT NOT NULL DEFAULT 'BRL',
        active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE searches (id INTEGER PRIMARY KEY, watch_id INTEGER, scraped_at TEXT NOT NULL, origin TEXT NOT NULL,
        destination TEXT NOT NULL, depart_date TEXT NOT NULL, return_date TEXT, trip_type TEXT NOT NULL, adults INTEGER NOT NULL,
        cabin TEXT NOT NULL, max_stops INTEGER, currency TEXT NOT NULL, status TEXT NOT NULL, error TEXT,
        offers_count INTEGER NOT NULL DEFAULT 0, min_price REAL, price_level TEXT, url TEXT);""")
    c.commit(); c.close()
    conn = db.connect(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(searches)")} | {r["name"] for r in conn.execute("PRAGMA table_info(watches)")}
    assert {"every_hours", "min_nonstop", "fastest_min"} <= cols


def test_build_dashboard(conn):
    future = date.today() + timedelta(days=60)
    item = {**PAGE_ITEM, "depart_date": future.isoformat(), "return_date": (future + timedelta(days=10)).isoformat(), "flex_days": 1}
    db.sync_watches(conn, [item, {**item, "id": 5, "depart_date": "2020-01-01", "return_date": None, "flex_days": 0}])
    w = db.watch_to_spec(db.list_watches(conn)[0])
    for price in (1000, 900):          # duas coletas da mesma combinação -> histórico
        db.save_search(conn, w, ScrapeResult([offer(price), offer(price + 500, stops=1, dur=400)], "u", "low"), watch_id=4, max_offers=30)
    other = SearchSpec(w.origin, w.destination, w.depart_date + timedelta(days=1), w.return_date + timedelta(days=1), 2, "business", 1)
    db.save_search(conn, other, ScrapeResult([offer(1500)], "u", "high"), watch_id=4)
    db.save_search(conn, w, error="blocked!", status="blocked", watch_id=4)

    d = db.build_dashboard(conn)
    json.dumps(d)  # serializável
    w4, w5 = d["watches"]
    assert w4["id"] == 4 and w4["last_status"] == "blocked" and w4["last_error"] == "blocked!" and not w4["expired"]
    assert w4["next_run_at"] is not None
    c1, c2 = w4["combos"]
    assert (c1["min_price"], c1["prev_price"], c1["first_price"], c1["min_nonstop"], c1["fastest_min"]) == (900, 1000, 1000, 900, 400)
    assert [h[1] for h in c1["history"]] == [1000, 900] and c1["price_level"] == "low"
    assert [o["price"] for o in c1["offers"]] == [900, 1400]
    assert c2["min_price"] == 1500 and c2["prev_price"] is None
    assert w5["expired"] and w5["combos"] == []


def test_cli_sync_and_publish(tmp_path, capsys):
    f = tmp_path / "watches.json"
    f.write_text(json.dumps([PAGE_ITEM]))
    dbp, out = str(tmp_path / "x.db"), str(tmp_path / "pub")
    assert cli.main(["--db", dbp, "sync-watches", str(f)]) == 0
    assert cli.main(["--db", dbp, "publish", "--out", out]) == 0
    data = json.loads((tmp_path / "pub" / "dashboard.json").read_text())
    assert data["watches"][0]["destination"] == "MAD" and data["watches"][0]["combos"] == []
    assert cli.main(["--db", dbp, "sync-watches", str(tmp_path / "nao-existe.json")]) == 0  # arquivo ausente = lista vazia


def test_run_due_skips_when_nothing_due(tmp_path, monkeypatch, capsys):
    f = tmp_path / "w.json"
    f.write_text(json.dumps([{**PAGE_ITEM, "depart_date": (date.today() + timedelta(days=40)).isoformat(),
                              "return_date": None, "flex_days": 0}]))
    dbp = str(tmp_path / "x.db")
    cli.main(["--db", dbp, "sync-watches", str(f)])

    class Fake:
        calls = 0
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def search(self, spec, **kw):
            Fake.calls += 1
            return ScrapeResult([offer(1)], "u")
    monkeypatch.setattr(cli, "make_scraper", lambda a: Fake())
    assert cli.main(["--db", dbp, "run", "--due"]) == 0 and Fake.calls == 1
    assert cli.main(["--db", dbp, "run", "--due"]) == 0 and Fake.calls == 1   # não vence de novo
    assert "Nenhuma busca vencida" in capsys.readouterr().out


def test_quiet_mode_prints_no_route_price_or_error_text(tmp_path, capsys, monkeypatch):
    """Logs do Actions em repo público são públicos: nada que identifique a busca pode sair."""
    f = tmp_path / "w.json"
    soon = date.today() + timedelta(days=40)
    f.write_text(json.dumps([{**PAGE_ITEM, "id": 1, "depart_date": soon.isoformat(), "return_date": None, "flex_days": 1},
                             {**PAGE_ITEM, "id": 2, "origin": "FCO", "destination": "VCE", "depart_date": soon.isoformat(),
                              "return_date": None, "flex_days": 0}]))
    dbp = str(tmp_path / "x.db")
    leak = "Timeout navigating to https://www.google.com/travel/flights?tfs=SEGREDO&q=GRU-MAD"

    class Fake:
        n = 0
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def search(self, spec, **kw):
            Fake.n += 1
            if Fake.n == 2:
                from flights.scraper import ScrapeError
                raise ScrapeError(leak)
            return ScrapeResult([offer(1234, flight_numbers="XX 999")], "https://www.google.com/x", "typical")

    monkeypatch.setattr(cli, "make_scraper", lambda a: Fake())
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    try:
        assert cli.main(["--db", dbp, "--quiet", "sync-watches", str(f)]) == 0
        assert cli.main(["--db", dbp, "--quiet", "run"]) == 1  # uma falha
        out = capsys.readouterr()
        text = out.out + out.err
        for secret in ("GRU", "MAD", "FCO", "VCE", "1234", "1.234", "BRL", "LATAM", "XX 999", "SEGREDO", "tfs", "google.com", "typical"):
            assert secret not in text, f"vazou {secret!r}: {text}"
        assert "ok · 1 voos" in text and "erro" in text and "Concluído: 2 ok, 1 com falha" in text.replace("\n", " ") or "Concluído" in text
        # o detalhe completo do erro fica só no banco (privado) e aparece no painel
        conn = db.connect(dbp)
        assert any(leak in (r["error"] or "") for r in db.recent_searches(conn))
    finally:
        cli.QUIET = False
