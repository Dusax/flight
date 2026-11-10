import base64
import json
from datetime import date, timedelta

import pytest

from flights import cli, db
from flights.models import Leg, Offer, ScrapeResult, SearchSpec, plan_specs
from flights.tfs import build_tfs, build_url

LEGS = [Leg("GRU", "LIS", date(2026, 12, 20)), Leg("LIS", "MAD", date(2026, 12, 27)), Leg("MAD", "GRU", date(2027, 1, 3))]
# Validado ao vivo: o Google Voos aceitou e listou os voos do 1º trecho com o total da viagem.
GOLDEN_MULTI = "GhoSCjIwMjYtMTItMjBqBRIDR1JVcgUSA0xJUxoaEgoyMDI2LTEyLTI3agUSA0xJU3IFEgNNQUQaGhIKMjAyNy0wMS0wM2oFEgNNQURyBRIDR1JVQAFIAZgBAw"


def offer(price, stops=0, dur=600, **kw):
    base = dict(price=price, currency="BRL", airline="LATAM", operated_by=None, origin="GRU", destination="LIS",
                dep_time="2027-02-10T10:00", arr_time="2027-02-10T20:00", duration_min=dur, stops=stops,
                flight_numbers="LA 1", layover_airports=None, layovers=[], section="Top", position=1)
    base.update(kw)
    return Offer(**base)


def test_multi_tfs_matches_what_google_accepted():
    assert build_tfs(SearchSpec.multi(LEGS)) == GOLDEN_MULTI
    raw = base64.urlsafe_b64decode(GOLDEN_MULTI + "==")
    assert raw.endswith(b"\x98\x01\x03")  # tipo de viagem 3 = multi-destino


def test_url_has_country_for_booking_options():
    spec = SearchSpec("GRU", "GIG", date(2026, 12, 5))
    assert "gl=BR" in build_url(spec) and "gl=" not in build_url(spec, None)


def test_multi_spec_properties():
    s = SearchSpec.multi(LEGS, adults=2, cabin="business")
    assert (s.trip_type, s.route(), s.origin, s.destination, s.depart_date, s.end_date) == (
        "multi_city", "GRU→LIS→MAD→GRU", "GRU", "LIS", date(2026, 12, 20), date(2027, 1, 3))
    assert [l.origin for l in s.all_legs()] == ["GRU", "LIS", "MAD"]
    assert SearchSpec("GRU", "LIS", date(2026, 12, 20), date(2026, 12, 30)).route() == "GRU⇄LIS"


def test_multi_validation():
    with pytest.raises(ValueError, match="2 a 5"):
        SearchSpec.multi(LEGS[:1])
    with pytest.raises(ValueError, match="2 a 5"):
        SearchSpec.multi([Leg("GRU", "LIS", date(2026, 12, 20 + i)) for i in range(6)])
    with pytest.raises(ValueError, match="ordem"):
        SearchSpec.multi([LEGS[0], Leg("LIS", "MAD", date(2026, 12, 10))])
    with pytest.raises(ValueError, match="iguais"):
        SearchSpec.multi([LEGS[0], Leg("LIS", "LIS", date(2026, 12, 27))])
    SearchSpec.multi([LEGS[0], Leg("LIS", "MAD", date(2026, 12, 20))])  # mesmo dia vale
    SearchSpec.multi([LEGS[0], Leg("LIS", "MAD", date(2026, 12, 27)), Leg("FCO", "GRU", date(2027, 1, 3))])  # sem encadear: vale


def test_shift_moves_every_leg_and_return():
    s = SearchSpec.multi(LEGS).shift(2)
    assert [l.date for l in s.legs] == [date(2026, 12, 22), date(2026, 12, 29), date(2027, 1, 5)]
    assert s.depart_date == s.legs[0].date
    r = SearchSpec("GRU", "LIS", date(2026, 12, 20), date(2026, 12, 30)).shift(-1)
    assert (r.depart_date, r.return_date) == (date(2026, 12, 19), date(2026, 12, 29))


def test_plan_specs_modes():
    today = date(2026, 1, 1)
    one = SearchSpec("GRU", "GIG", date(2026, 12, 5))
    assert len(plan_specs(one, 0, True, today)) == 1  # só ida não tem o que separar
    rt = SearchSpec("GRU", "LIS", date(2026, 12, 20), date(2026, 12, 30))
    plan = plan_specs(rt, 1, True, today)
    assert len(plan) == 3 * 3  # 3 datas × (pacote + 2 trechos)
    legs = [p for p in plan if p.leg_index is not None]
    assert {(p.origin, p.destination, p.leg_index) for p in legs} == {("GRU", "LIS", 0), ("LIS", "GRU", 1)}
    assert all(p.trip_type == "one_way" for p in legs)
    multi = SearchSpec.multi(LEGS)
    assert len(plan_specs(multi, 0, True, today)) == 1 + 3
    assert len(plan_specs(multi, 0, False, today)) == 1


def test_plan_skips_past_trips():
    today = date(2026, 12, 21)
    rt = SearchSpec("GRU", "LIS", date(2026, 12, 20), date(2026, 12, 30))
    assert [p.depart_date for p in plan_specs(rt, 1, False, today)] == [date(2026, 12, 21)]
    assert plan_specs(rt, 0, False, today) == []


# ---------- banco e painel ----------

@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "m.db")


def multi_item(**kw):
    base = {"id": 1, "label": "Europa", "origin": "GRU", "destination": "LIS", "depart_date": "2027-02-10",
            "return_date": None, "legs": [{"origin": "GRU", "destination": "LIS", "date": "2027-02-10"},
                                          {"origin": "LIS", "destination": "MAD", "date": "2027-02-17"},
                                          {"origin": "MAD", "destination": "GRU", "date": "2027-02-24"}],
            "flex_days": 0, "adults": 1, "cabin": "economy", "max_stops": None, "currency": "BRL",
            "every_hours": 24, "compare_separate": True, "booking_top": 2, "active": True}
    base.update(kw)
    return base


def test_sync_multi_watch_roundtrip(conn):
    assert db.sync_watches(conn, [multi_item()]) == (1, [])
    w = db.list_watches(conn)[0]
    assert (w["compare_separate"], w["booking_top"], w["return_date"]) == (1, 2, None)
    spec = db.watch_to_spec(w)
    assert spec.trip_type == "multi_city" and spec.route() == "GRU→LIS→MAD→GRU"
    assert db.row_route(w) == "GRU→LIS→MAD→GRU"


def test_sync_rejects_bad_multi_and_booking_top(conn):
    bad = multi_item(legs=[{"origin": "GRU", "destination": "LIS", "date": "2027-02-10"}])
    n, warns = db.sync_watches(conn, [bad, multi_item(id=2, booking_top=99)])
    assert n == 0 and len(warns) == 2


def test_dashboard_compares_package_vs_separate(conn):
    db.sync_watches(conn, [multi_item()])
    spec = db.watch_to_spec(db.list_watches(conn)[0])
    trip = ScrapeResult([offer(7000, flight_numbers="UX 58"), offer(9000, stops=1)], "u", "typical")
    db.save_search(conn, spec, trip, watch_id=1)
    for ls, price in zip(spec.leg_specs(), (3000, 300, 4500)):
        res = ScrapeResult([offer(price, stops=0, dur=100 + price // 100), offer(price + 50, stops=1)], "u")
        res.offers[0].booking = {"bags": ["1 free carry-on"], "sellers": [{"seller": "X", "price": price}], "typical_low": 1, "typical_high": 2}
        db.save_search(conn, ls, res, watch_id=1)
    d = db.build_dashboard(conn, today=date(2027, 1, 1))
    (w,) = d["watches"]
    assert w["trip_type"] == "multi_city" and w["route"] == "GRU→LIS→MAD→GRU" and w["compare_separate"] and w["booking_top"] == 2
    (c,) = w["combos"]
    assert (c["min_price"], c["end_date"]) == (7000, "2027-02-24") and [l["destination"] for l in c["legs"]] == ["LIS", "MAD", "GRU"]
    sp = c["separate"]
    assert [l["min_price"] for l in sp["legs"]] == [3000, 300, 4500]
    assert (sp["total"], sp["vs_trip"]) == (7800, 800)  # pacote 800 mais barato
    assert sp["total_nonstop"] == 7800
    assert sp["legs"][0]["offers"][0]["booking"]["bags"] == ["1 free carry-on"]
    json.dumps(d)


def test_dashboard_separate_incomplete_has_no_total(conn):
    db.sync_watches(conn, [multi_item()])
    spec = db.watch_to_spec(db.list_watches(conn)[0])
    db.save_search(conn, spec, ScrapeResult([offer(7000)], "u"), watch_id=1)
    db.save_search(conn, spec.leg_specs()[0], ScrapeResult([offer(3000)], "u"), watch_id=1)
    (c,) = db.build_dashboard(conn, today=date(2027, 1, 1))["watches"][0]["combos"]
    assert c["separate"]["total"] is None and c["separate"]["vs_trip"] is None
    assert c["separate"]["legs"][1]["min_price"] is None


def test_editing_a_watch_hides_searches_from_the_old_setup(conn):
    db.sync_watches(conn, [multi_item()])
    old = db.watch_to_spec(db.list_watches(conn)[0])
    db.save_search(conn, old, ScrapeResult([offer(7000)], "u"), watch_id=1)
    assert len(db.build_dashboard(conn, today=date(2027, 1, 1))["watches"][0]["combos"]) == 1
    edited = multi_item(legs=[{"origin": "GRU", "destination": "LIS", "date": "2027-02-11"},
                              {"origin": "LIS", "destination": "GRU", "date": "2027-02-20"}], depart_date="2027-02-11")
    db.sync_watches(conn, [edited])
    assert db.build_dashboard(conn, today=date(2027, 1, 1))["watches"][0]["combos"] == []


def test_details_are_persisted(conn):
    spec = SearchSpec("GRU", "LIS", date(2027, 2, 10))
    o = offer(1000, cabin="Economy", aircraft="Boeing 787", co2_kg=431, co2_diff_pct=-17, overnight=True,
              often_delayed=False, segments=[{"flight": "LA 1", "legroom_in": 32}], booking={"bags": ["x"]})
    sid = db.save_search(conn, spec, ScrapeResult([o], "u"))
    r = conn.execute("SELECT * FROM offers WHERE search_id = ?", (sid,)).fetchone()
    assert (r["cabin"], r["aircraft"], r["co2_kg"], r["co2_diff_pct"], r["overnight"], r["often_delayed"]) == ("Economy", "Boeing 787", 431, -17, 1, 0)
    assert json.loads(r["segments_json"])[0]["legroom_in"] == 32 and json.loads(r["booking_json"]) == {"bags": ["x"]}


def test_history_by_route_excludes_multi_but_watch_history_includes_it(conn):
    db.sync_watches(conn, [multi_item()])
    spec = db.watch_to_spec(db.list_watches(conn)[0])
    db.save_search(conn, spec, ScrapeResult([offer(7000)], "u"), watch_id=1)
    assert db.history(conn, "GRU", "LIS") == []
    assert len(db.history(conn, watch_id=1)) == 1


# ---------- CLI ----------

def test_parse_stops():
    legs = cli.parse_stops(["GRU@2026-12-20", "lis@27/12/2026", "MAD@2027-01-03", "FCO"])
    assert [(l.origin, l.destination, l.date) for l in legs] == [
        ("GRU", "LIS", date(2026, 12, 20)), ("LIS", "MAD", date(2026, 12, 27)), ("MAD", "FCO", date(2027, 1, 3))]  # open jaw
    for bad, msg in ((["GRU@2026-12-20", "LIS"], "pelo menos 2 trechos"), (["GRU", "LIS@2026-12-20", "MAD"], "informe a data"),
                     (["GRU@2026-12-20", "LIS@2026-12-27", "MAD@2027-01-03"], "sem data")):
        with pytest.raises(ValueError, match=msg):
            cli.parse_stops(bad)


class Fake:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def search(self, spec, **kw):
        self.calls.append((spec, kw))
        return ScrapeResult([offer(1000 + len(self.calls))], "u")


def test_cli_multi_compare_passes_booking_and_detail(monkeypatch, tmp_path, capsys):
    fake = Fake()
    monkeypatch.setattr(cli, "make_scraper", lambda a: fake)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    soon = date.today() + timedelta(days=30)
    argv = ["--db", str(tmp_path / "t.db"), "multi", f"GRU@{soon}", f"LIS@{soon + timedelta(days=7)}", "GRU",
            "--compare-separate", "--booking", "2", "--detail", "12"]
    assert cli.main(argv) == 0
    kinds = [(s.trip_type, s.leg_index) for s, _ in fake.calls]
    assert kinds == [("multi_city", None), ("one_way", 0), ("one_way", 1)]
    assert all(kw == {"detail_offers": 12, "booking_top": 2} for _, kw in fake.calls)
    out = capsys.readouterr().out
    assert "trecho 1, só ida" in out


def test_cli_watch_add_multi_and_run_uses_watch_settings(monkeypatch, tmp_path, capsys):
    fake = Fake()
    monkeypatch.setattr(cli, "make_scraper", lambda a: fake)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    dbp = str(tmp_path / "t.db")
    soon = date.today() + timedelta(days=30)
    assert cli.main(["--db", dbp, "watch", "add-multi", f"GRU@{soon}", f"LIS@{soon + timedelta(days=5)}", "GRU",
                     "--every", "12", "--compare-separate", "--booking", "1"]) == 0
    assert cli.main(["--db", dbp, "watch", "list"]) == 0
    assert "GRU→LIS→GRU" in capsys.readouterr().out
    assert cli.main(["--db", dbp, "run"]) == 0
    assert len(fake.calls) == 3 and all(kw["booking_top"] == 1 for _, kw in fake.calls)
    assert cli.main(["--db", dbp, "history", "--watch", "1"]) == 0
    assert cli.main(["--db", dbp, "log"]) == 0


def test_export_has_details_and_booking_summary(tmp_path, capsys):
    import csv
    dbp = tmp_path / "x.db"
    conn = db.connect(dbp)
    o = offer(1000, cabin="Economy", aircraft="Boeing 787", co2_kg=431, overnight=True,
              booking={"bags": ["1 free carry-on", "1st checked bag: R$719"], "url": "https://www.google.com/x",
                       "sellers": [{"seller": "LATAM", "price": 990.0}], "typical_low": 800.0, "typical_high": 1500.0, "verdict": "typical"})
    db.save_search(conn, SearchSpec("GRU", "LIS", date(2027, 2, 10)), ScrapeResult([o, offer(1200)], "u"))
    out = tmp_path / "e.csv"
    assert cli.main(["--db", str(dbp), "export", "--out", str(out)]) == 0
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert "booking_json" not in rows[0] and rows[0]["cabin"] == "economy"  # cabin = da busca; offer_cabin = do voo
    first = rows[0]
    assert (first["offer_cabin"], first["aircraft"], first["co2_kg"], first["seller"], first["seller_price"]) == ("Economy", "Boeing 787", "431", "LATAM", "990.0")
    assert first["bags"] == "1 free carry-on | 1st checked bag: R$719" and first["price_verdict"] == "typical"
    assert rows[1]["bags"] == "" and rows[1]["offer_cabin"] == ""


def test_offers_details_command_prints_segments(tmp_path, capsys):
    dbp = tmp_path / "x.db"
    conn = db.connect(dbp)
    o = offer(1000, cabin="Economy", aircraft="Boeing 787", segments=[{"flight": "LA 8148", "aircraft": "Boeing 787",
             "dep": {"code": "GRU", "time": "10:40 PM", "plus_days": 0}, "arr": {"code": "LIS", "time": "11:20 AM", "plus_days": 1},
             "legroom": "Above average", "legroom_in": 32, "amenities": ["On-demand video"],
             "layover_after": {"duration_min": 80, "city": "Madrid", "code": "MAD", "note": None}}],
              booking={"bags": ["1 free carry-on"], "sellers": [{"seller": "LATAM", "price": 990.0}], "typical_low": 800.0, "typical_high": 1500.0, "verdict": "low"})
    db.save_search(conn, SearchSpec("GRU", "LIS", date(2027, 2, 10)), ScrapeResult([o], "u"))
    assert cli.main(["--db", str(dbp), "offers", "--details"]) == 0
    out = capsys.readouterr().out
    assert "LA 8148" in out and "Boeing 787" in out and "assento Above average (32 pol)" in out
    assert "conexão de 1h20 em Madrid (MAD)" in out and "bagagem [1 free carry-on]" in out and "(este voo: low)" in out
