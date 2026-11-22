"""Horários de coleta em cron (fuso de Brasília). Os vetores em schedule_cases.json são os mesmos do teste em Node."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from flights import schedule as S

CASES = json.loads((Path(__file__).parent / "schedule_cases.json").read_text())


def local(txt):
    return datetime.fromisoformat(txt).replace(tzinfo=S.TZ)


@pytest.mark.parametrize("case", CASES["valid"], ids=lambda c: c["expr"])
def test_vetores_compartilhados(case):
    s = S.parse(case["expr"])
    for when, expected in case["matches"]:
        assert S.matches(s, local(when)) is expected, (case["expr"], when)
    if "next" in case:
        t, got = local(case["next"]["after"]), []
        for _ in case["next"]["expect"]:
            t = S.next_slot(s, t)
            got.append(t.astimezone(S.TZ).strftime("%Y-%m-%dT%H:%M"))
        assert got == case["next"]["expect"]


@pytest.mark.parametrize("expr", CASES["invalid"])
def test_expressoes_invalidas(expr):
    with pytest.raises(ValueError):
        S.parse(expr)


def utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def test_venceu_no_horario_e_nao_repete_na_rodada_seguinte():
    s = S.parse("0 22 * * *")            # 22h de Brasília = 01h UTC do dia seguinte
    assert S.due(s, utc(2026, 11, 22, 1, 7), utc(2026, 11, 21, 1, 7))                 # tick das 22h07, última coleta ontem
    assert not S.due(s, utc(2026, 11, 22, 1, 7), utc(2026, 11, 22, 1, 2))             # coleta manual às 22h02 já atendeu o horário
    assert not S.due(s, utc(2026, 11, 22, 2, 7), utc(2026, 11, 22, 1, 7))             # tick de 23h07: o horário das 22h já foi atendido
    assert not S.due(s, utc(2026, 11, 21, 15, 7), utc(2026, 11, 20, 1, 7))            # 12h07: fora do horário


def test_recupera_rodada_que_o_github_pulou_ate_3_horas():
    s = S.parse("0 22 * * *")
    last = utc(2026, 11, 21, 1, 7)                                                    # coletou ontem às 22h
    assert S.due(s, utc(2026, 11, 22, 2, 7), last)        # tick das 22h07 pulado: às 23h07 ainda coleta
    assert S.due(s, utc(2026, 11, 22, 3, 7), last)        # 00h07 (2 h depois)
    assert S.due(s, utc(2026, 11, 22, 4, 7), last)        # 01h07: o horário das 22h foi há 3 horas cheias, ainda dentro da folga
    assert not S.due(s, utc(2026, 11, 22, 5, 7), last)    # 02h07: passou da folga, espera o próximo dia


def test_nunca_coletou_so_coleta_nos_horarios():
    s = S.parse("0 22 * * *")
    assert S.due(s, utc(2026, 11, 22, 1, 7), None)
    assert not S.due(s, utc(2026, 11, 21, 15, 7), None)


def test_hora_em_hora_coleta_a_cada_tick():
    s = S.parse("0 * * * *")
    assert S.due(s, utc(2026, 11, 21, 15, 7), utc(2026, 11, 21, 14, 7))
    assert not S.due(s, utc(2026, 11, 21, 15, 7), utc(2026, 11, 21, 15, 7))


# ---------- integração com o banco ----------
from datetime import date

from flights import cli, db
from flights.models import Offer, ScrapeResult, SearchSpec


def item(i, **kw):
    soon = (date.today() + timedelta(days=60)).isoformat()
    return {"id": i, "origin": "GRU", "destination": "LIS", "depart_date": soon, "return_date": None, "flex_days": 0, **kw}


def put_search(conn, wid, when_utc):
    spec = SearchSpec("GRU", "LIS", date.today() + timedelta(days=60))
    o = Offer(price=1000, currency="BRL", airline="X", operated_by=None, origin="GRU", destination="LIS", dep_time="2030-01-01T10:00",
              arr_time="2030-01-01T20:00", duration_min=600, stops=0, flight_numbers="XX 1", layover_airports=None, layovers=[],
              section="Top", position=1)
    sid = db.save_search(conn, spec, ScrapeResult([o], "u", "typical"), watch_id=wid)
    conn.execute("UPDATE searches SET scraped_at = ? WHERE id = ?", (when_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), sid))
    conn.commit()


def watch(conn, wid):
    return next(r for r in db.list_watches(conn) if r["id"] == wid)


def test_sync_grava_cron_zera_every_e_recusa_invalido(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    n, warnings = db.sync_watches(conn, [item(1, schedule="0 22,0,2 * * 1-5", every_hours=6), item(2, schedule="0 99 * * *"),
                                         item(3, every_hours=12), item(4, once=True, schedule="0 22 * * *", every_hours=3)])
    assert n == 3 and len(warnings) == 1 and "hora" in warnings[0]
    w1, w3, w4 = watch(conn, 1), watch(conn, 3), watch(conn, 4)
    assert w1["schedule"] == "0 22,0,2 * * 1-5" and w1["every_hours"] is None
    assert w3["schedule"] is None and w3["every_hours"] == 12                       # "a cada N horas" continua igual
    assert w4["schedule"] is None and w4["every_hours"] is None and w4["once"] == 1  # pontual nunca é agendada
    out = {w["id"]: w for w in db.build_dashboard(conn)["watches"]}
    assert out[1]["schedule"] == "0 22,0,2 * * 1-5" and out[1]["next_run_at"]


def test_is_due_pelo_banco_respeita_horario_e_coleta_anterior(tmp_path):
    conn = db.connect(tmp_path / "d.db")
    db.sync_watches(conn, [item(1, schedule="0 22 * * *")])
    w = watch(conn, 1)
    assert db.is_due(conn, w, utc(2026, 11, 22, 1, 7))                         # 22h07 de Brasília, nunca coletou
    put_search(conn, 1, utc(2026, 11, 22, 1, 9))                               # coletou às 22h09
    assert not db.is_due(conn, w, utc(2026, 11, 22, 2, 7))                     # tick das 23h07: já atendido
    assert not db.is_due(conn, w, utc(2026, 11, 22, 15, 7))                    # 12h07: fora do horário
    assert db.is_due(conn, w, utc(2026, 11, 23, 1, 7))                         # dia seguinte, 22h07
    assert db.next_run_at(w, "2026-11-22T01:09:00Z", utc(2026, 11, 22, 15, 7)) == "2026-11-23T01:00:00Z"   # próximo: 22h de amanhã
    assert db.next_run_at(w, "2026-11-21T01:09:00Z", utc(2026, 11, 22, 1, 7)) == "2026-11-22T01:00:00Z"   # pendente: já chegou


def test_pausada_e_sem_cron_nao_mudam(tmp_path):
    conn = db.connect(tmp_path / "p.db")
    db.sync_watches(conn, [item(1, schedule="0 * * * *", active=False), item(2, every_hours=24), item(3)])
    assert not db.is_due(conn, watch(conn, 1), utc(2026, 11, 22, 1, 7))        # pausada
    assert db.is_due(conn, watch(conn, 2), datetime.now(timezone.utc) + timedelta(minutes=1))   # a cada 24 h, nunca coletou: vence
    assert not db.is_due(conn, watch(conn, 3), utc(2026, 11, 22, 1, 7))        # só manual


def test_cli_due_codigo_de_saida(tmp_path, capsys):
    dbp = str(tmp_path / "c.db")
    conn = db.connect(dbp)
    db.sync_watches(conn, [item(1, schedule="0 3 * * *")])
    conn.close()
    s = S.parse("0 3 * * *")   # só coleta às 3h: vence apenas se houver um horário nas últimas 3 horas cheias
    expected = 0 if S.latest_slot(s, datetime.now(timezone.utc)) else cli.EXIT_NONE_DUE
    assert cli.main(["--db", dbp, "due"]) == expected
    assert "busca(s) vencida(s)" in capsys.readouterr().out


def test_banco_antigo_sem_coluna_schedule_e_migrado(tmp_path):
    import sqlite3
    c = sqlite3.connect(tmp_path / "old.db")
    c.executescript("CREATE TABLE watches (id INTEGER PRIMARY KEY, label TEXT, origin TEXT NOT NULL, destination TEXT NOT NULL,"
                    "depart_date TEXT NOT NULL, return_date TEXT, flex_days INTEGER NOT NULL DEFAULT 0, adults INTEGER NOT NULL DEFAULT 1,"
                    "cabin TEXT NOT NULL DEFAULT 'economy', max_stops INTEGER, currency TEXT NOT NULL DEFAULT 'BRL', every_hours INTEGER,"
                    "legs_json TEXT, compare_separate INTEGER NOT NULL DEFAULT 0, booking_top INTEGER NOT NULL DEFAULT 0,"
                    "active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);"
                    "INSERT INTO watches (origin,destination,depart_date,every_hours,created_at) VALUES ('GRU','LIS','2030-05-01',24,'x');")
    c.commit(); c.close()
    conn = db.connect(tmp_path / "old.db")
    w = watch(conn, 1)
    assert w["schedule"] is None and w["once"] == 0 and w["every_hours"] == 24


def test_watch_add_com_cron_pela_linha_de_comando(tmp_path, capsys):
    dbp = str(tmp_path / "w.db")
    soon = (date.today() + timedelta(days=60)).isoformat()
    assert cli.main(["--db", dbp, "watch", "add", "GRU", "LIS", soon, "--schedule", "0 22-5/2 * * *", "--every", "6"]) == 0
    conn = db.connect(dbp)
    w = watch(conn, 1)
    assert w["schedule"] == "0 22-5/2 * * *" and w["every_hours"] is None


def test_due_nenhuma_vencida_nao_se_confunde_com_crash(tmp_path, monkeypatch, capsys):
    """Código 10 = nenhuma vencida; um erro dentro do `due` devolve outro código (o workflow trata como erro, não como 'nada')."""
    dbp = str(tmp_path / "n.db")
    db.connect(dbp).close()
    assert cli.main(["--db", dbp, "due"]) == cli.EXIT_NONE_DUE == 10
    def boom(*a, **k):
        raise RuntimeError("quebrou")
    monkeypatch.setattr(db, "is_due", boom)
    conn = db.connect(dbp)
    db.sync_watches(conn, [item(1, every_hours=6)])
    conn.close()
    with pytest.raises(RuntimeError):
        cli.main(["--db", dbp, "due"])          # o python sai com 1 (traceback): diferente de 10


def test_orcamento_de_tempo_invalido_nao_derruba(capsys):
    assert cli.parse_time_budget(None, None) is None
    assert cli.parse_time_budget(None, "38") == 38.0
    assert cli.parse_time_budget("12.5", "38") == 12.5          # a linha de comando manda
    assert cli.parse_time_budget(None, "38min") is None         # inválido: aviso, sem exceção
    assert cli.parse_time_budget("abc", "30") == 30.0           # CLI inválido cai para o ambiente
    assert cli.parse_time_budget(0, "-5") is None
    out = capsys.readouterr().out
    assert "FLIGHTS_TIME_BUDGET" in out and "--time-budget" in out
