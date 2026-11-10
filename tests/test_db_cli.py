import csv
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from flights import cli, db
from flights.models import Offer, ScrapeResult, SearchSpec, parse_date, parse_iata
from flights.scraper import BlockedError, ScrapeError


def offer(price, stops=0, **kw):
    base = dict(price=price, currency="BRL", airline="LATAM", operated_by=None, origin="GRU", destination="LIS",
                dep_time="2026-12-20T10:00", arr_time="2026-12-20T20:00", duration_min=600, stops=stops,
                flight_numbers="LA 1", layover_airports=None, layovers=[], section="Top", position=1)
    base.update(kw)
    return Offer(**base)


SPEC = SearchSpec("GRU", "LIS", date(2026, 12, 20), date(2026, 12, 30), adults=2)


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "sub" / "t.db")


def test_save_and_history(conn):
    r1 = ScrapeResult([offer(1000), offer(800, stops=1, flight_numbers="X 1")], "http://u", "typical")
    r2 = ScrapeResult([offer(900)], "http://u", "low")
    s1 = db.save_search(conn, SPEC, r1)
    db.save_search(conn, SPEC, r2)
    db.save_search(conn, SPEC, error="boom")  # falhas ficam registradas mas não entram no histórico
    rows = db.history(conn, "GRU", "LIS")
    assert [r["min_price"] for r in rows] == [800, 900]
    assert rows[0]["min_nonstop"] == 1000 and rows[0]["price_level"] == "typical"
    assert [o["price"] for o in db.search_offers(conn, s1)] == [800, 1000]
    assert [r["status"] for r in db.recent_searches(conn)] == ["error", "ok", "ok"]


def test_empty_result_status(conn):
    sid = db.save_search(conn, SPEC, ScrapeResult([], "u"))
    assert conn.execute("SELECT status, min_price FROM searches WHERE id=?", (sid,)).fetchone()[:] == ("empty", None)


def test_layovers_serialized_as_json(conn):
    o = offer(500, stops=1, layovers=[{"n": 1, "duration_min": 80, "airport": "Madrid", "city": "Madrid"}])
    sid = db.save_search(conn, SPEC, ScrapeResult([o], "u"))
    row = db.search_offers(conn, sid)[0]
    assert json.loads(row["layovers_json"])[0]["duration_min"] == 80


def test_watch_crud_and_roundtrip_to_spec(conn):
    wid = db.add_watch(conn, SPEC, flex_days=2, label="x")
    assert db.watch_to_spec(db.list_watches(conn)[0]) == SPEC
    assert db.set_watch_active(conn, wid, False)
    assert db.list_watches(conn, only_active=True) == []
    assert db.remove_watch(conn, wid) and not db.remove_watch(conn, wid)


def test_inputs():
    assert parse_date("20/12/2026") == date(2026, 12, 20) == parse_date("2026-12-20")
    assert parse_iata(" gru ") == "GRU"
    for bad in ("2026-13-01", "ontem"):
        with pytest.raises(ValueError):
            parse_date(bad)
    with pytest.raises(ValueError):
        parse_iata("SAOP")
    with pytest.raises(ValueError):
        SearchSpec("GRU", "LIS", date(2026, 12, 20), date(2026, 12, 10))


def test_flex_skips_past_dates():
    soon = date.today() + timedelta(days=1)
    specs = cli.expand_flex(SearchSpec("GRU", "LIS", soon, soon + timedelta(days=7)), 3)
    assert [s.depart_date for s in specs] == [soon + timedelta(days=d) for d in range(-1, 4)]  # hoje ainda vale; antes de hoje não
    assert all((s.return_date - s.depart_date).days == 7 for s in specs)


class FakeScraper:
    def __init__(self, behaviour):
        self.behaviour, self.calls = behaviour, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def search(self, spec, **kw):
        self.calls.append(spec)
        b = self.behaviour(len(self.calls))
        if isinstance(b, Exception):
            raise b
        return b


def run_cli(monkeypatch, tmp_path, fake, *argv):
    monkeypatch.setattr(cli, "make_scraper", lambda args: fake)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    return cli.main(["--db", str(tmp_path / "t.db"), *argv])


def test_cli_search_run_history_export(monkeypatch, tmp_path, capsys):
    fake = FakeScraper(lambda n: ScrapeResult([offer(1000 - n * 50)], "http://u", "typical"))
    soon = (date.today() + timedelta(days=30)).isoformat()
    assert run_cli(monkeypatch, tmp_path, fake, "watch", "add", "GRU", "LIS", soon, "--nights", "7", "--flex", "1") == 0
    assert run_cli(monkeypatch, tmp_path, fake, "run") == 0
    assert len(fake.calls) == 3  # ±1 dia
    assert run_cli(monkeypatch, tmp_path, fake, "history", "GRU", "LIS") == 0
    assert run_cli(monkeypatch, tmp_path, fake, "search", "GRU", "GIG", soon) == 0
    out = tmp_path / "out" / "e.csv"
    assert run_cli(monkeypatch, tmp_path, fake, "export", "--out", str(out)) == 0
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert len(rows) == 4 and "price_per_adult" in rows[0]
    capsys.readouterr()


def test_cli_blocked_stops_run_and_is_logged(monkeypatch, tmp_path, capsys):
    fake = FakeScraper(lambda n: BlockedError("captcha") if n == 2 else ScrapeResult([offer(1)], "u"))
    soon = (date.today() + timedelta(days=30)).isoformat()
    assert run_cli(monkeypatch, tmp_path, fake, "search", "GRU", "LIS", soon, "--flex", "2") == 1
    assert len(fake.calls) == 2  # parou no bloqueio
    conn = db.connect(tmp_path / "t.db")
    assert [r["status"] for r in db.recent_searches(conn)] == ["blocked", "ok"]


def test_cli_failure_is_recorded_and_run_continues(monkeypatch, tmp_path, capsys):
    fake = FakeScraper(lambda n: ScrapeError("falhou") if n == 1 else ScrapeResult([offer(1)], "u"))
    soon = (date.today() + timedelta(days=30)).isoformat()
    assert run_cli(monkeypatch, tmp_path, fake, "search", "GRU", "LIS", soon, "--flex", "1") == 1
    assert len(fake.calls) == 3
