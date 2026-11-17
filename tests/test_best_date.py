"""Melhor data: menor preço; em empate, o voo mais curto; empate total mantém todas as datas empatadas."""
from datetime import date, timedelta

from flights import db
from flights.models import Offer, ScrapeResult, SearchSpec

D = date(2030, 5, 1)


def offer(price, dur, flight="LA 1"):
    return Offer(price=price, currency="BRL", airline="LATAM", operated_by=None, origin="GRU", destination="LIS", dep_time="2030-05-01T22:10",
                 arr_time="2030-05-02T12:00", duration_min=dur, stops=0, flight_numbers=flight, layover_airports=None, layovers=[],
                 section="Top", position=1)


def dashboard(tmp_path, per_date):
    """per_date: lista de ofertas por data de ida (uma coleta por data)."""
    conn = db.connect(tmp_path / "t.db")
    spec = SearchSpec("GRU", "LIS", D)
    wid = db.add_watch(conn, spec, flex_days=len(per_date) // 2)
    for i, offers in enumerate(per_date):
        db.save_search(conn, spec.shift(i - len(per_date) // 2), ScrapeResult(offers, "u", "typical"), watch_id=wid)
    return db.build_dashboard(conn, today=date(2030, 1, 1))["watches"][0]["combos"]


def test_best_duration_e_a_do_voo_mais_barato_e_no_empate_a_menor(tmp_path):
    combos = dashboard(tmp_path, [
        [offer(3000, 700, "A"), offer(3000, 520, "B"), offer(3500, 400, "C")],   # empate no preço: vale o mais rápido (520), não o 400 (mais caro)
        [offer(3000, 600)],
        [offer(3200, 300)],
    ])
    assert [c["best_duration_min"] for c in combos] == [520, 600, 300]
    assert [c["min_price"] for c in combos] == [3000, 3000, 3200]


def test_sem_ofertas_guardadas_nao_quebra(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    spec = SearchSpec("GRU", "LIS", D)
    wid = db.add_watch(conn, spec)
    db.save_search(conn, spec, ScrapeResult([offer(3000, 500)], "u", "typical"), watch_id=wid, max_offers=1)
    assert db.build_dashboard(conn, today=date(2030, 1, 1))["watches"][0]["combos"][0]["best_duration_min"] == 500
