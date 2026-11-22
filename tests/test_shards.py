"""Um job por busca: cada um coleta num banco próprio e exporta um shard; o job final junta tudo no banco principal."""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from flights import cli, db
from flights.models import Offer, ScrapeResult, SearchSpec

D = date.today() + timedelta(days=60)


def offer(price, flight="LA 1", **kw):
    return Offer(price=price, currency="BRL", airline="LATAM", operated_by=None, origin="GRU", destination="LIS",
                 dep_time=f"{D}T10:00", arr_time=f"{D}T20:00", duration_min=600, stops=0, flight_numbers=flight,
                 layover_airports=None, layovers=[], section="Top", position=1, **kw)


def base_db(path, n_watches=2):
    conn = db.connect(path)
    for i in range(n_watches):
        db.add_watch(conn, SearchSpec("GRU", "LIS", D + timedelta(days=i * 10)), every_hours=6)
    conn.close()


def collect(path, watch_id, n=2, run_tag="900"):
    """Simula o job de coleta: coleta no próprio banco e devolve o shard exportado."""
    conn = db.connect(path)
    since = db.max_search_id(conn)
    w = next(r for r in db.list_watches(conn) if r["id"] == watch_id)
    spec = db.watch_to_spec(w)
    for k in range(n):   # uma consulta por data diferente (como numa busca com período)
        db.save_search(conn, spec.shift(k), ScrapeResult([offer(1000 + k, aircraft="A320", segments=[{"f": 1}], detail_at="2030-01-01T00:00:00Z"),
                                                  offer(1500 + k, "TP 9")], f"https://www.google.com/{k}", "typical", typical_low=900.0, typical_high=1800.0),
                       watch_id=watch_id, run_tag=run_tag)
    return db.export_shard(conn, since, run_tag)


def test_export_so_leva_as_coletas_novas_sem_ids_locais(tmp_path):
    base_db(tmp_path / "a.db")
    conn = db.connect(tmp_path / "a.db")
    spec = db.watch_to_spec(db.list_watches(conn)[0])
    db.save_search(conn, spec, ScrapeResult([offer(1)], "u", "typical"), watch_id=1)          # coleta antiga (antes do shard)
    conn.close()
    shard = collect(tmp_path / "a.db", 1, n=2)
    assert len(shard["searches"]) == 2 and all("id" not in s for s in shard["searches"])
    assert all("search_id" not in o and "id" not in o for s in shard["searches"] for o in s["offers"])
    assert len(shard["searches"][0]["offers"]) == 2 and shard["run_tag"] == "900"


def test_importar_refaz_os_ids_e_preserva_o_que_importa(tmp_path):
    base_db(tmp_path / "main.db"); base_db(tmp_path / "job1.db"); base_db(tmp_path / "job2.db")
    s1, s2 = collect(tmp_path / "job1.db", 1, n=3), collect(tmp_path / "job2.db", 2, n=2)
    main = db.connect(tmp_path / "main.db")
    r1, r2 = db.import_shard(main, s1), db.import_shard(main, s2)
    assert (r1["searches"], r1["offers"], r2["searches"], r2["offers"]) == (3, 6, 2, 4)
    assert main.execute("SELECT COUNT(*) FROM searches").fetchone()[0] == 5 and main.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 10
    # cada voo ficou ligado à coleta certa (ids novos, sem colisão entre shards)
    bad = main.execute("SELECT COUNT(*) FROM offers o LEFT JOIN searches s ON s.id = o.search_id WHERE s.id IS NULL").fetchone()[0]
    assert bad == 0
    per_watch = {r[0]: r[1] for r in main.execute("SELECT s.watch_id, COUNT(*) FROM offers o JOIN searches s ON s.id = o.search_id GROUP BY 1")}
    assert per_watch == {1: 6, 2: 4}
    o = main.execute("SELECT aircraft, segments_json, detail_at FROM offers WHERE aircraft IS NOT NULL LIMIT 1").fetchone()
    assert (o[0], o[1], o[2]) == ("A320", '[{"f":1}]', "2030-01-01T00:00:00Z")
    assert tuple(main.execute("SELECT typical_low, run_tag FROM searches LIMIT 1").fetchone()) == (900.0, "900")


def test_reaplicar_o_mesmo_shard_nao_duplica(tmp_path):
    base_db(tmp_path / "main.db"); base_db(tmp_path / "job.db")
    shard = collect(tmp_path / "job.db", 1, n=2)
    main = db.connect(tmp_path / "main.db")
    db.import_shard(main, shard)
    again = db.import_shard(main, shard)
    assert again["searches"] == 0 and again["duplicated"] == 2
    assert main.execute("SELECT COUNT(*) FROM searches").fetchone()[0] == 2 and main.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 4


def test_coleta_de_busca_removida_no_painel_e_ignorada(tmp_path):
    base_db(tmp_path / "main.db"); base_db(tmp_path / "job.db")
    shard = collect(tmp_path / "job.db", 2, n=2)
    main = db.connect(tmp_path / "main.db")
    db.remove_watch(main, 2)                                    # o usuário apagou a busca enquanto a coleta rodava
    r = db.import_shard(main, shard)
    assert (r["searches"], r["removed_watch"]) == (0, 2) and main.execute("SELECT COUNT(*) FROM searches").fetchone()[0] == 0


def test_shard_de_versao_desconhecida_e_recusado(tmp_path):
    main = db.connect(tmp_path / "m.db")
    with pytest.raises(ValueError):
        db.import_shard(main, {"version": 99, "searches": []})


def test_cli_merge_junta_varios_arquivos_e_aguenta_arquivo_ruim(tmp_path, capsys):
    base_db(tmp_path / "main.db"); base_db(tmp_path / "j1.db"); base_db(tmp_path / "j2.db")
    db.write_shard(tmp_path / "s1.json.gz", collect(tmp_path / "j1.db", 1)); db.write_shard(tmp_path / "s2.json.gz", collect(tmp_path / "j2.db", 2))
    (tmp_path / "ruim.json.gz").write_bytes(b"isto nao e gzip")
    rc = cli.main(["--db", str(tmp_path / "main.db"), "merge", str(tmp_path / "s1.json.gz"), str(tmp_path / "ruim.json.gz"),
                   str(tmp_path / "s2.json.gz"), str(tmp_path / "nao-existe.json.gz")])
    out = capsys.readouterr().out
    assert rc == 1 and "ruim.json.gz" in out and "4 coleta(s)" in out                       # o ruim não impede os outros
    assert db.connect(tmp_path / "main.db").execute("SELECT COUNT(*) FROM searches").fetchone()[0] == 4
    assert cli.main(["--db", str(tmp_path / "main.db"), "merge"]) == 0                      # sem arquivos: ok


# ---------- run --shard-out com scraper falso ----------
class Fake:
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def search(self, spec, **kw):
        return ScrapeResult([offer(1234, aircraft="A320")], "https://www.google.com/x", "typical")


def test_run_shard_out_exporta_so_o_que_a_execucao_coletou(tmp_path, monkeypatch):
    base_db(tmp_path / "w.db")
    monkeypatch.setattr(cli, "make_scraper", lambda a: Fake()); monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    monkeypatch.setenv("FLIGHTS_RUN_ID", "555")
    out = tmp_path / "out" / "shard.json.gz"
    assert cli.main(["--db", str(tmp_path / "w.db"), "--quiet", "run", "1", "--shard-out", str(out)]) == 0
    cli.QUIET = False
    shard = db.read_shard(out)
    assert len(shard["searches"]) == 1 and shard["searches"][0]["watch_id"] == 1 and shard["run_tag"] == "555"
    assert shard["searches"][0]["run_tag"] == "555"


# ---------- plan ----------
def run_plan(tmp_path, monkeypatch, event, ids="", requests=None, capsys=None):
    out = tmp_path / "gh_out.txt"
    out.write_text("")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    args = ["--db", str(tmp_path / "p.db"), "plan", "--event", event, "--ids", ids]
    if requests is not None:
        args += ["--requests", str(requests)]
    assert cli.main(args) == 0
    vals = dict(l.split("=", 1) for l in out.read_text().splitlines() if "=" in l)
    return vals["any"] == "true", [m["id"] for m in json.loads(vals["matrix"])]


def plan_db(tmp_path):
    conn = db.connect(tmp_path / "p.db")
    soon = (date.today() + timedelta(days=60)).isoformat()
    base = {"origin": "GRU", "destination": "LIS", "depart_date": soon, "return_date": None, "flex_days": 0}
    db.sync_watches(conn, [{"id": 1, **base, "every_hours": 6}, {"id": 2, **base, "every_hours": 6}, {"id": 3, **base},
                           {"id": 4, **base, "once": True}, {"id": 5, **base, "every_hours": 6, "active": False}])
    conn.close()


def test_plan_agendado_leva_as_vencidas(tmp_path, monkeypatch):
    plan_db(tmp_path)
    conn = db.connect(tmp_path / "p.db")
    spec = db.watch_to_spec(db.list_watches(conn)[0])
    db.save_search(conn, spec, ScrapeResult([offer(1)], "u", "typical"), watch_id=1)       # a 1 acabou de coletar: não vence
    conn.close()
    assert run_plan(tmp_path, monkeypatch, "schedule") == (True, [2])                       # 3 é manual, 4 é pontual, 5 pausada


def test_plan_manual_sem_ids_e_todas_menos_pontuais_e_com_ids_so_elas(tmp_path, monkeypatch):
    plan_db(tmp_path)
    assert run_plan(tmp_path, monkeypatch, "dispatch") == (True, [1, 2, 3])
    assert run_plan(tmp_path, monkeypatch, "dispatch", ids="4") == (True, [4])              # a pontual roda quando pedida
    assert run_plan(tmp_path, monkeypatch, "dispatch", ids="3, 1") == (True, [1, 3])
    assert run_plan(tmp_path, monkeypatch, "dispatch", ids="5") == (False, [])              # pausada não roda


def test_plan_acumula_os_pedidos_do_painel_e_esvazia_o_arquivo(tmp_path, monkeypatch):
    plan_db(tmp_path)
    req = tmp_path / "requests.json"
    req.write_text(json.dumps({"ids": [3, 4], "all": False}))                                # dois pedidos seguidos: um sobrescreveu o outro no GitHub
    assert run_plan(tmp_path, monkeypatch, "dispatch", ids="3", requests=req) == (True, [3, 4])
    assert json.loads(req.read_text()) == {"ids": [], "all": False}
    req.write_text(json.dumps({"ids": [], "all": True}))
    assert run_plan(tmp_path, monkeypatch, "dispatch", ids="", requests=req) == (True, [1, 2, 3])
    req.write_text("{quebrado")
    assert run_plan(tmp_path, monkeypatch, "dispatch", ids="3", requests=req) == (True, [3])  # arquivo ilegível não derruba


def test_plan_agendado_tambem_atende_pedidos_pendentes(tmp_path, monkeypatch):
    plan_db(tmp_path)
    req = tmp_path / "requests.json"
    req.write_text(json.dumps({"ids": [3], "all": False}))
    any_, ids = run_plan(tmp_path, monkeypatch, "schedule", requests=req)
    assert any_ and 3 in ids and {1, 2} <= set(ids)


def test_plan_sem_nada_a_coletar(tmp_path, monkeypatch, capsys):
    db.connect(tmp_path / "p.db").close()
    assert run_plan(tmp_path, monkeypatch, "schedule") == (False, [])
    monkeypatch.delenv("GITHUB_OUTPUT")
    assert cli.main(["--db", str(tmp_path / "p.db"), "plan"]) == 0
    assert '"any": false' in capsys.readouterr().out


# ---------- summary ----------
def test_summary_conta_por_status_e_falha_so_se_nada_deu_certo(tmp_path, capsys):
    base_db(tmp_path / "s.db", 1)
    conn = db.connect(tmp_path / "s.db")
    spec = db.watch_to_spec(db.list_watches(conn)[0])
    db.save_search(conn, spec, error="boom", status="error", watch_id=1, run_tag="7")
    db.save_search(conn, spec, error="captcha", status="blocked", watch_id=1, run_tag="7")
    conn.close()
    assert cli.main(["--db", str(tmp_path / "s.db"), "summary", "--run-tag", "7"]) == 1
    assert "2 consulta(s)" in capsys.readouterr().out
    conn = db.connect(tmp_path / "s.db")
    db.save_search(conn, spec, ScrapeResult([offer(1)], "u", "typical"), watch_id=1, run_tag="7")
    conn.close()
    assert cli.main(["--db", str(tmp_path / "s.db"), "summary", "--run-tag", "7"]) == 0
    assert cli.main(["--db", str(tmp_path / "s.db"), "summary", "--run-tag", "999"]) == 0
    assert cli.main(["--db", str(tmp_path / "s.db"), "summary", "--run-tag", "999", "--expect-any"]) == 1
