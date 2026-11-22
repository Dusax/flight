"""Evolução de preço de cada voo: arrays alinhados às coletas, com lacunas quando o voo não apareceu."""
import json
from datetime import date, timedelta

from flights import cli, db
from flights.models import Offer, ScrapeResult, SearchSpec

D = date(2030, 5, 1)


def offer(price, flight="LA 8084", dep="2030-05-01T22:10", **kw):
    return Offer(price=price, currency="BRL", airline="LATAM", operated_by=None, origin="GRU", destination="LIS", dep_time=dep,
                 arr_time="2030-05-02T12:00", duration_min=600, stops=0, flight_numbers=flight, layover_airports=None, layovers=[],
                 section="Top", position=1, **kw)


def setup(tmp_path, runs, flex=0):
    conn = db.connect(tmp_path / "t.db")
    spec = SearchSpec("GRU", "LIS", D)
    wid = db.add_watch(conn, spec, flex_days=flex)
    for offers in runs:
        db.save_search(conn, spec, ScrapeResult(offers, "u", "typical"), watch_id=wid)
    return conn, wid, spec


def combo0(conn, **kw):
    return db.build_dashboard(conn, today=date(2030, 1, 1), **kw)["watches"][0]["combos"][0]


def test_serie_alinhada_com_lacunas(tmp_path):
    conn, *_ = setup(tmp_path, [
        [offer(3000), offer(3500, "TP 92", "2030-05-01T09:00")],
        [offer(3400, "TP 92", "2030-05-01T09:00")],                     # LA 8084 sumiu desta coleta
        [offer(2800), offer(3300, "TP 92", "2030-05-01T09:00")],
    ])
    c = combo0(conn)
    s = c["series"]
    assert len(s["t"]) == 3 and s["t"] == sorted(s["t"])
    assert s["f"]["LA 8084|2030-05-01T22:10"] == [3000, None, 2800]
    assert s["f"]["TP 92|2030-05-01T09:00"] == [3500, 3400, 3300]
    assert s["min"] == [3000, 3400, 2800]                               # menor preço da data a cada coleta


def test_mesmo_numero_em_datas_diferentes_sao_voos_diferentes(tmp_path):
    conn, *_ = setup(tmp_path, [[offer(3000, dep="2030-05-01T22:10"), offer(3100, dep="2030-05-02T22:10")]])
    keys = set(combo0(conn)["series"]["f"])
    assert keys == {"LA 8084|2030-05-01T22:10", "LA 8084|2030-05-02T22:10"}


def test_so_voos_da_lista_atual_e_sem_numero_ignorado(tmp_path):
    conn, *_ = setup(tmp_path, [
        [offer(3000), offer(3600, "XX 1", "2030-05-01T07:00")],
        [offer(2900), offer(3000, None, "2030-05-01T11:00")],            # voo sem número de voo
    ])
    c = combo0(conn)
    assert set(c["series"]["f"]) == {"LA 8084|2030-05-01T22:10"}         # XX 1 não está mais na lista atual; sem número fica de fora


def test_limite_de_pontos_pega_as_mais_recentes(tmp_path):
    conn, *_ = setup(tmp_path, [[offer(3000 + i)] for i in range(12)])
    s = combo0(conn, series_points=5)["series"]
    assert len(s["t"]) == 5 and s["f"]["LA 8084|2030-05-01T22:10"] == [3007, 3008, 3009, 3010, 3011]


def test_combinacao_sem_voos_guardados_nao_tem_serie(tmp_path):
    conn, wid, spec = setup(tmp_path, [])
    db.save_search(conn, spec, ScrapeResult([], "u", "typical"), watch_id=wid)   # sem voos (status empty)
    assert db.build_dashboard(conn, today=date(2030, 1, 1))["watches"][0]["combos"] == []


def test_publish_reduz_os_pontos_quando_o_json_fica_grande(tmp_path, monkeypatch):
    conn, *_ = setup(tmp_path, [[offer(3000 + i)] for i in range(60)])
    conn.close()
    out = tmp_path / "pub"
    cli.main(["--db", str(tmp_path / "t.db"), "publish", "--out", str(out)])
    assert len(json.loads((out / "dashboard.json").read_text())["watches"][0]["combos"][0]["series"]["t"]) == 60
    monkeypatch.setattr(cli, "MAX_DASHBOARD_BYTES", 5_000)          # força o limite: 90 -> 45
    cli.main(["--db", str(tmp_path / "t.db"), "publish", "--out", str(out)])
    n = len(json.loads((out / "dashboard.json").read_text())["watches"][0]["combos"][0]["series"]["t"])
    assert n in (45, 20)                                              # 90 -> 45 -> 20 até caber (ou o mínimo)
