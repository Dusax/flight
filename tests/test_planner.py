"""Períodos grandes: cada coleta consulta só um orçamento de combinações, escolhidas de forma inteligente."""
from datetime import date, timedelta

import pytest

from flights import cli, db, planner
from flights.models import Offer, ScrapeResult, SearchSpec

D = date(2030, 5, 1)
TODAY = date(2030, 1, 1)


def grid(n=16, m=16):
    return [(D + timedelta(days=i), D + timedelta(days=10 + j)) for i in range(n) for j in range(m)]


STARTS = (D, D + timedelta(days=10))


def ix(c):
    return ((c[0] - D).days, (c[1] - D).days - 10)


def test_primeira_coleta_e_uma_amostra_espalhada():
    o = planner.order(grid(), STARTS, {}, hot=3)
    assert len(o) == 256 and len(set(o)) == 256          # todas, sem repetir
    first = {ix(c) for c in o[:4]}
    assert first == {(0, 0), (0, 8), (8, 0), (8, 8)}            # malha de 8 em 8 primeiro
    assert all(a % 4 == 0 and b % 4 == 0 for a, b in (ix(c) for c in o[4:9]))   # depois a de 4 em 4


def test_depois_concentra_nas_mais_baratas_e_nas_vizinhas():
    combos = grid()
    known = {combos[0]: (900.0, "2030-01-02T00:00:00Z"), combos[5 * 16 + 5]: (2500.0, "2030-01-02T00:00:00Z")}
    o = planner.order(combos, STARTS, known, hot=1)
    assert o[0] == combos[0]                                   # a mais barata vem renovar
    nearest = [ix(c) for c in o[1:5]]
    assert any(abs(a) + abs(b) == 1 for a, b in nearest)       # vizinha da mais barata logo no começo
    assert any(a % 8 == 0 and b % 8 == 0 for a, b in nearest)  # e a amostra espalhada continua alternando


def test_tudo_visto_renova_as_mais_antigas():
    combos = grid(3, 3)
    known = {c: (1000.0 + i, f"2030-01-0{i + 1}T00:00:00Z") for i, c in enumerate(combos)}
    o = planner.order(combos, STARTS, known, hot=2)
    assert o[:2] == combos[:2]                                  # as 2 mais baratas
    assert o[2:] == combos[2:]                                  # depois as mais antigas (a mais velha primeiro)


def test_multi_trecho_tambem_funciona():
    starts = (D, D + timedelta(days=5), D + timedelta(days=9))
    combos = [(D + timedelta(days=a), D + timedelta(days=5 + b), D + timedelta(days=9 + c)) for a in range(4) for b in range(4) for c in range(4)]
    assert sorted(planner.order(combos, starts, {}, hot=3)) == sorted(combos)


def watch_row(tmp_path, compare=False, budget=40, flex=0, **kw):
    """Ida 10 dias (1 a 10/mai) x volta 16 dias (11 a 26/mai): 160 combinações, todas válidas."""
    conn = db.connect(tmp_path / "t.db")
    spec = SearchSpec("GRU", "LIS", D, D + timedelta(days=10))
    ends = (D + timedelta(days=kw.get("dep", 9)), D + timedelta(days=10 + kw.get("ret", 15)))
    wid = db.add_watch(conn, spec, compare_separate=compare, ends=ends, budget=budget)
    return conn, wid, spec


def row_of(conn, wid):
    return next(r for r in db.list_watches(conn) if r["id"] == wid)


def test_periodo_grande_respeita_o_orcamento(tmp_path):
    conn, wid, _ = watch_row(tmp_path, budget=30)
    plan = db.watch_run_plan(conn, row_of(conn, wid), TODAY)
    assert len(db.watch_plan(row_of(conn, wid), TODAY)) == 160 and len(plan) == 30


def test_periodo_que_cabe_no_orcamento_consulta_tudo(tmp_path):
    conn, wid, _ = watch_row(tmp_path, budget=100, dep=5, ret=5)
    assert len(db.watch_run_plan(conn, row_of(conn, wid), TODAY)) == 36


def test_orcamento_conta_tambem_os_trechos_separados(tmp_path):
    conn, wid, _ = watch_row(tmp_path, compare=True, budget=30)
    plan = db.watch_run_plan(conn, row_of(conn, wid), TODAY)
    assert len(plan) <= 30 and any(s.leg_index is not None for s in plan) and any(s.leg_index is None for s in plan)


def test_coletas_seguidas_vao_cobrindo_o_periodo(tmp_path):
    conn, wid, spec = watch_row(tmp_path, budget=40)
    seen = set()
    for run in range(8):
        plan = db.watch_run_plan(conn, row_of(conn, wid), TODAY)
        assert len(plan) == 40
        new = 0
        for i, s in enumerate(plan):
            seen_before = (s.depart_date, s.return_date) in seen
            new += not seen_before
            seen.add((s.depart_date, s.return_date))
            price = 3000 + (s.depart_date - D).days * 10 + (s.return_date - D).days  # mais barato no começo do período
            o = Offer(price=price, currency="BRL", airline="X", operated_by=None, origin="GRU", destination="LIS",
                      dep_time=f"{s.depart_date}T10:00", arr_time=f"{s.depart_date}T20:00", duration_min=600, stops=0,
                      flight_numbers="XX 1", layover_airports=None, layovers=[], section="Top", position=1)
            db.save_search(conn, s, ScrapeResult([o], "u", "typical"), watch_id=wid)
        if run == 0:
            assert new == 40
    assert len(seen) >= 120                                     # em 8 coletas, mais de 3/4 do período já foi visto
    # a mais barata (no começo do período) foi encontrada e é renovada a cada coleta
    plan = db.watch_run_plan(conn, row_of(conn, wid), TODAY)
    assert (D, D + timedelta(days=10)) in {(s.depart_date, s.return_date) for s in plan}
    d = next(w for w in db.build_dashboard(conn, today=TODAY)["watches"] if w["id"] == wid)
    assert d["coverage"]["space"] == 160 and d["coverage"]["seen"] == len(seen) and d["budget"] == 40
    assert sum(1 for c in d["combos"] if c["offers"]) == db.DETAILED_COMBOS    # só as mais baratas levam os voos
    assert all(c["offers_trimmed"] for c in d["combos"] if not c["offers"])


def test_sync_valida_o_orcamento_e_aceita_periodos_grandes(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    base = {"origin": "GRU", "destination": "LIS", "depart_date": "2030-05-01", "return_date": "2030-05-10", "depart_to": "2030-05-30", "return_to": "2030-06-20"}
    n, warnings = db.sync_watches(conn, [{"id": 1, **base, "budget": 25}, {"id": 2, **base, "budget": 5}, {"id": 3, **base, "budget": 500}, {"id": 4, **base}])
    assert n == 2 and len(warnings) == 2
    out = {w["id"]: w for w in db.build_dashboard(conn, today=TODAY)["watches"]}
    assert out[1]["budget"] == 25 and out[4]["budget"] == 40
    assert out[1]["coverage"]["space"] > 500 and out[1]["coverage"]["runs_left"] > 1


def test_tempo_esgotado_deixa_o_resto_para_a_proxima(tmp_path, capsys, monkeypatch):
    conn = db.connect(tmp_path / "d.db")

    class Never:
        def search(self, *a, **k):
            raise AssertionError("não devia consultar")
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    spec = SearchSpec("GRU", "LIS", D)
    ok, failed = cli.run_specs(conn, Never(), [(None, spec, 0), (None, spec, 0)], 0, 5, deadline=0.0)
    assert (ok, failed) == (0, 0) and "2 consulta(s) ficam para a próxima" in capsys.readouterr().out
