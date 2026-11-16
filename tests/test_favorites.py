"""Voos favoritos: o painel grava favorites.json; o `publish` junta o histórico de preço de cada voo."""
import json
from datetime import date
from pathlib import Path

from flights import cli, db
from flights.models import Offer, ScrapeResult, SearchSpec

D = date(2030, 5, 1)
SPEC = SearchSpec("GRU", "LIS", D, date(2030, 5, 10))


def offer(price, flight="LA 8084", dep="2030-05-01T22:10", **kw):
    base = dict(price=price, currency="BRL", airline="LATAM", operated_by=None, origin="GRU", destination="LIS", dep_time=dep,
                arr_time="2030-05-02T12:00", duration_min=600, stops=0, flight_numbers=flight, layover_airports=None,
                layovers=[], section="Top", position=1)
    base.update(kw)
    return Offer(**base)


def fav(flight="LA 8084", dep="2030-05-01T22:10", dates=("2030-05-01", "2030-05-10"), watch_id=1):
    return {"id": f"{watch_id}|{','.join(dates)}|{flight}|{dep}", "watch_id": watch_id, "dates": list(dates),
            "offer": {"flight_numbers": flight, "dep_time": dep}}


def setup(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    wid = db.add_watch(conn, SPEC)
    return conn, wid


def test_historico_e_preco_atual_do_voo_favorito(tmp_path):
    conn, wid = setup(tmp_path)
    for price in (3000, 2800):
        db.save_search(conn, SPEC, ScrapeResult([offer(price), offer(price + 500, flight="TP 92", dep="2030-05-01T09:00")], "u", "typical"), watch_id=wid)
    out = db.build_favorites(conn, [fav()])
    info = out[fav()["id"]]
    assert [p for _, p in info["history"]] == [3000, 2800] and info["current"] == 2800
    assert info["seen_latest"] is True and info["last_seen_at"] == info["searched_at"]


def test_voo_que_saiu_da_lista_nao_aparece_na_ultima_coleta(tmp_path):
    conn, wid = setup(tmp_path)
    db.save_search(conn, SPEC, ScrapeResult([offer(3000)], "u", "typical"), watch_id=wid)
    db.save_search(conn, SPEC, ScrapeResult([offer(3300, flight="TP 92", dep="2030-05-01T09:00")], "u", "typical"), watch_id=wid)
    info = db.build_favorites(conn, [fav()])[fav()["id"]]
    assert info["current"] == 3000 and info["seen_latest"] is False
    assert len(info["history"]) == 1


def test_viagem_removida_ou_datas_trocadas_nao_quebram(tmp_path):
    conn, wid = setup(tmp_path)
    db.save_search(conn, SPEC, ScrapeResult([offer(3000)], "u", "typical"), watch_id=wid)
    out = db.build_favorites(conn, [fav(watch_id=99), fav(dates=("2030-05-02", "2030-05-10")), fav(dates=("2030-05-01",)),
                                    {"id": "x", "watch_id": "abc", "dates": ["lixo", 1], "offer": None}, "texto", {"sem": "id"}])
    assert all(v["current"] is None and v["history"] == [] for v in out.values()) and len(out) == 4


def test_publish_inclui_favoritos(tmp_path):
    conn, wid = setup(tmp_path)
    db.save_search(conn, SPEC, ScrapeResult([offer(3000)], "u", "typical"), watch_id=wid)
    conn.close()
    out = tmp_path / "pub"
    out.mkdir()
    (out / "favorites.json").write_text(json.dumps([fav()]))
    assert cli.main(["--db", str(tmp_path / "t.db"), "publish", "--out", str(out)]) == 0
    data = json.loads((out / "dashboard.json").read_text())
    assert data["favorites"][fav()["id"]]["current"] == 3000
    (out / "favorites.json").write_text("{quebrado")
    assert cli.main(["--db", str(tmp_path / "t.db"), "publish", "--out", str(out)]) == 0   # arquivo ilegível não derruba a coleta
    assert json.loads((out / "dashboard.json").read_text())["favorites"] == {}
