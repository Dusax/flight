"""O pipeline em paralelo (plan -> um `run --shard-out` por busca -> merge -> publish) dá o mesmo resultado da execução em série."""
import json
import shutil
from datetime import date, timedelta

from flights import cli, db
from flights.models import Offer, ScrapeResult

SOON = date.today() + timedelta(days=45)


def fake_offer(spec, price):
    d = spec.depart_date.isoformat()
    return Offer(price=price, currency="BRL", airline="LATAM", operated_by=None, origin=spec.origin, destination=spec.destination,
                 dep_time=f"{d}T10:00", arr_time=f"{d}T20:00", duration_min=600, stops=0, flight_numbers="LA 1", layover_airports=None,
                 layovers=[], section="Top", position=1, aircraft="A320", segments=[{"flight": "LA 1"}], detail_at="2030-01-01T00:00:00Z")


class Fake:
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def search(self, spec, **kw):   # preço determinístico por rota e data
        price = 1000 + (spec.depart_date - SOON).days * 10 + ord(spec.destination[0])
        return ScrapeResult([fake_offer(spec, price), fake_offer(spec, price + 500)], "https://www.google.com/x", "typical",
                            typical_low=900.0, typical_high=1800.0)


WATCHES = [
    {"id": 1, "origin": "GRU", "destination": "LIS", "depart_date": SOON.isoformat(), "depart_to": (SOON + timedelta(days=3)).isoformat(), "every_hours": 6, "budget": 40},
    {"id": 2, "origin": "REC", "destination": "CUZ", "depart_date": (SOON + timedelta(days=5)).isoformat(), "every_hours": 6},
    {"id": 3, "origin": "GRU", "destination": "MAD", "depart_date": (SOON + timedelta(days=9)).isoformat(), "every_hours": 6},
]


def fingerprint(path):
    conn = db.connect(path)
    return sorted(tuple(r) for r in conn.execute(
        """SELECT s.watch_id, s.depart_date, s.status, s.min_price, s.typical_low, o.price, o.flight_numbers, o.aircraft, o.detail_at
           FROM searches s LEFT JOIN offers o ON o.search_id = s.id"""))


def test_paralelo_e_serie_dao_o_mesmo_banco(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "make_scraper", lambda a: Fake())
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    monkeypatch.setenv("FLIGHTS_RUN_ID", "321")
    (tmp_path / "watches.json").write_text(json.dumps(WATCHES))
    base = tmp_path / "base.db"
    assert cli.main(["--db", str(base), "--quiet", "sync-watches", str(tmp_path / "watches.json")]) == 0

    # --- em série (como antes): um banco, todas as buscas
    serial = tmp_path / "serial.db"
    shutil.copy(base, serial)
    assert cli.main(["--db", str(serial), "--quiet", "run", "--due", "--max-offers", "30"]) == 0

    # --- em paralelo: plan -> um banco por busca com --shard-out -> merge no principal
    plan_out = tmp_path / "gh_out.txt"; plan_out.write_text("")
    monkeypatch.setenv("GITHUB_OUTPUT", str(plan_out))
    plan_db = tmp_path / "plan.db"; shutil.copy(base, plan_db)
    assert cli.main(["--db", str(plan_db), "--quiet", "plan", "--event", "schedule"]) == 0
    vals = dict(l.split("=", 1) for l in plan_out.read_text().splitlines() if "=" in l)
    ids = [m["id"] for m in json.loads(vals["matrix"])]
    assert vals["any"] == "true" and ids == [1, 2, 3]
    shards = []
    for i in ids:
        work = tmp_path / f"work{i}.db"; shutil.copy(base, work)
        shard = tmp_path / "shards" / f"321-{i}.json.gz"
        assert cli.main(["--db", str(work), "--quiet", "run", str(i), "--max-offers", "30", "--shard-out", str(shard)]) == 0
        shards.append(str(shard))
    parallel = tmp_path / "parallel.db"; shutil.copy(base, parallel)
    assert cli.main(["--db", str(parallel), "--quiet", "merge", *shards]) == 0
    assert cli.main(["--db", str(parallel), "--quiet", "merge", *shards]) == 0        # reaplicar (re-execução do job) não duplica
    cli.QUIET = False

    assert fingerprint(parallel) == fingerprint(serial) and len(fingerprint(serial)) >= 10
    assert cli.main(["--db", str(parallel), "summary", "--run-tag", "321", "--expect-any"]) == 0

    # --- o painel gerado é o mesmo
    out_a, out_b = tmp_path / "pa", tmp_path / "pb"
    assert cli.main(["--db", str(serial), "publish", "--out", str(out_a)]) == 0
    assert cli.main(["--db", str(parallel), "publish", "--out", str(out_b)]) == 0
    a, b = (json.loads((o / "dashboard.json").read_text()) for o in (out_a, out_b))
    for d in (a, b):
        d.pop("generated_at")
        for w in d["watches"]:
            w.pop("last_run_at"); w.pop("next_run_at"); w.pop("last_status", None)
            for c in w["combos"]:
                c.pop("scraped_at"); c.pop("first_at"); c["history"] = [[None, p] for _, p in c["history"]]
                c.pop("series", None)
                for o in c["offers"]:
                    o.pop("booking_at", None)
    assert a == b


def test_segunda_coleta_reaproveita_o_detalhe_do_banco(tmp_path, monkeypatch):
    """A 2ª execução vê o detalhe da 1ª em db.known_details e não precisa lê-lo de novo (TTL 24 h)."""
    monkeypatch.setattr(cli, "make_scraper", lambda a: Fake())
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    (tmp_path / "watches.json").write_text(json.dumps(WATCHES[1:2]))
    dbp = tmp_path / "d.db"
    assert cli.main(["--db", str(dbp), "--quiet", "sync-watches", str(tmp_path / "watches.json")]) == 0
    seen = []
    class Spy(Fake):
        def search(self, spec, **kw):
            seen.append(kw.get("known"))
            return super().search(spec, **kw)
    monkeypatch.setattr(cli, "make_scraper", lambda a: Spy())
    assert cli.main(["--db", str(dbp), "--quiet", "run", "2"]) == 0
    assert cli.main(["--db", str(dbp), "--quiet", "run", "2"]) == 0
    cli.QUIET = False
    assert seen[0] == {} and "LA 1|%sT10:00" % WATCHES[1]["depart_date"] in seen[1] and seen[1]["__insight__"]["typical_low"] == 900.0
