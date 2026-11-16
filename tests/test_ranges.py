"""Período de datas por trecho (dias inicial e final incluídos) em vez de só ± dias."""
from datetime import date, timedelta

import pytest

from flights import cli, db
from flights.models import MAX_COMBOS, Leg, SearchSpec, combo_dates, normalize_ends, plan_specs

D = date(2030, 5, 1)
TODAY = date(2030, 1, 1)


def rt():
    return SearchSpec("GRU", "LIS", D, D + timedelta(days=9))


def test_so_ida_cada_dia_do_periodo_inclusive():
    sp = SearchSpec("GRU", "LIS", D)
    specs = plan_specs(sp, ends=(D + timedelta(days=4),), today=TODAY)
    assert [s.depart_date for s in specs] == [D + timedelta(days=i) for i in range(5)]  # 1 a 5, os dois extremos entram


def test_ida_e_volta_combina_os_dois_periodos():
    ends = (D + timedelta(days=2), D + timedelta(days=11))
    specs = plan_specs(rt(), ends=ends, today=TODAY)
    assert len(specs) == 9  # 3 idas x 3 voltas
    assert {(s.depart_date, s.return_date) for s in specs} >= {(D, D + timedelta(days=9)), (D + timedelta(days=2), D + timedelta(days=11))}


def test_volta_nunca_antes_da_ida():
    sp = SearchSpec("GRU", "LIS", D, D + timedelta(days=1))
    combos = combo_dates(sp, (D + timedelta(days=3), D + timedelta(days=3)), TODAY)
    assert all(b >= a for a, b in combos) and len(combos) == 3 + 3 + 2 + 1  # (ida 1..4) x (volta 2..4), sem volta antes da ida


def test_multi_cada_trecho_tem_seu_periodo_e_respeita_a_ordem():
    legs = [Leg("GRU", "LIS", D), Leg("LIS", "MAD", D + timedelta(days=2)), Leg("MAD", "GRU", D + timedelta(days=4))]
    sp = SearchSpec.multi(legs)
    specs = plan_specs(sp, ends=(D + timedelta(days=1), D + timedelta(days=3), None), today=TODAY)
    assert len(specs) == 4
    for s in specs:
        ds = [l.date for l in s.all_legs()]
        assert ds == sorted(ds)
    assert {s.legs[1].date for s in specs} == {D + timedelta(days=2), D + timedelta(days=3)}


def test_trechos_separados_nao_repetem():
    legs = [Leg("GRU", "LIS", D), Leg("LIS", "GRU", D + timedelta(days=5))]
    sp = SearchSpec.multi(legs)
    specs = plan_specs(sp, compare_separate=True, ends=(D + timedelta(days=1), D + timedelta(days=6)), today=TODAY)
    pacotes = [s for s in specs if s.leg_index is None]
    soltos = [s for s in specs if s.leg_index is not None]
    assert len(pacotes) == 4 and len(soltos) == 4  # 2 datas de cada trecho, sem repetir


def test_datas_passadas_ficam_de_fora():
    sp = SearchSpec("GRU", "LIS", D)
    specs = plan_specs(sp, ends=(D + timedelta(days=4),), today=D + timedelta(days=2))
    assert [s.depart_date for s in specs] == [D + timedelta(days=i) for i in range(2, 5)]


def test_fim_antes_do_inicio_ou_periodo_enorme_sao_recusados():
    sp = SearchSpec("GRU", "LIS", D)
    with pytest.raises(ValueError):
        normalize_ends(sp, (D - timedelta(days=1),))
    with pytest.raises(ValueError):
        normalize_ends(sp, (D + timedelta(days=200),))


def test_nao_mistura_com_flex():
    with pytest.raises(ValueError):
        plan_specs(SearchSpec("GRU", "LIS", D), flex=2, ends=(D + timedelta(days=3),), today=TODAY)


def test_sync_grava_e_dashboard_devolve_os_periodos(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    base = {"origin": "GRU", "destination": "LIS", "depart_date": "2030-05-01"}
    items = [
        {"id": 1, **base, "depart_to": "2030-05-05"},
        {"id": 2, **base, "return_date": "2030-05-10", "depart_to": "2030-05-02", "return_to": "2030-05-12"},
        {"id": 3, **base, "legs": [{"origin": "GRU", "destination": "LIS", "date": "2030-05-01", "date_to": "2030-05-02"},
                                    {"origin": "LIS", "destination": "MAD", "date": "2030-05-04"}]},
        {"id": 4, **base, "depart_to": "2030-04-01"},                       # fim antes do início
        {"id": 5, **base, "depart_to": "2030-05-05", "flex_days": 2},         # período + ±dias
        {"id": 6, **base, "return_date": "2030-12-01", "depart_to": "2030-06-30", "return_to": "2030-12-30"},  # combinações demais
    ]
    n, warnings = db.sync_watches(conn, items)
    assert n == 3 and len(warnings) == 3
    out = {w["id"]: w for w in db.build_dashboard(conn, today=TODAY)["watches"]}
    assert out[1]["depart_to"] == "2030-05-05" and out[1]["n_queries"] == 5
    assert out[2]["return_to"] == "2030-05-12" and out[2]["n_queries"] == 6
    assert out[3]["legs"][0]["date_to"] == "2030-05-02" and out[3]["legs"][1]["date_to"] is None
    assert out[1]["expired"] is False


def test_banco_antigo_sem_coluna_e_migrado(tmp_path):
    import sqlite3
    c = sqlite3.connect(tmp_path / "old.db")
    c.executescript("CREATE TABLE watches (id INTEGER PRIMARY KEY, label TEXT, origin TEXT NOT NULL, destination TEXT NOT NULL,"
                    "depart_date TEXT NOT NULL, return_date TEXT, flex_days INTEGER NOT NULL DEFAULT 0, adults INTEGER NOT NULL DEFAULT 1,"
                    "cabin TEXT NOT NULL DEFAULT 'economy', max_stops INTEGER, currency TEXT NOT NULL DEFAULT 'BRL', every_hours INTEGER,"
                    "legs_json TEXT, compare_separate INTEGER NOT NULL DEFAULT 0, booking_top INTEGER NOT NULL DEFAULT 0,"
                    "active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);"
                    "INSERT INTO watches (origin,destination,depart_date,created_at) VALUES ('GRU','LIS','2030-05-01','x');")
    c.commit(); c.close()
    conn = db.connect(tmp_path / "old.db")
    assert db.watch_ends(db.list_watches(conn)[0]) == (None,)


def test_cli_periodo_nos_pontos_do_multi():
    legs, ends = cli.parse_stops_ranges(["GRU@2030-05-01..2030-05-03", "LIS@2030-05-10", "MAD@2030-05-15..16/05/2030", "GRU"])
    assert ends == (date(2030, 5, 3), None, date(2030, 5, 16)) and legs[2].date == date(2030, 5, 15)
    assert cli.parse_stops(["GRU@2030-05-01..2030-05-03", "LIS@2030-05-10", "GRU"])[0].date == D


def test_cli_ida_e_volta_com_periodos():
    args = cli.build_parser().parse_args(["search", "GRU", "LIS", "2030-05-01", "--return", "2030-05-10",
                                           "--depart-to", "2030-05-03", "--return-to", "2030-05-12"])
    spec = cli.spec_from_args(args)
    assert cli.ends_from_args(args, spec) == (date(2030, 5, 3), date(2030, 5, 12))
    assert MAX_COMBOS >= 9

