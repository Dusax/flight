"""Coleta mais rápida: reaproveitar detalhe/compra já conhecidos (TTL), pausa adaptativa e métricas por fase."""
import copy
import json
import random
from datetime import date, datetime, timedelta, timezone

import pytest

from flights import cli, db
from flights.details import carry_forward, fmt_ts, is_fresh, plan_detail_needs, strip_seller_prices
from flights.models import Offer, ScrapeResult, SearchSpec

NOW = datetime(2030, 5, 10, 12, 0, tzinfo=timezone.utc)


def ago(h):
    return fmt_ts(NOW - timedelta(hours=h))


def cards(*prices):
    return [{"aria": f"From {p} Brazilian reais. Nonstop", "key": f"LA {i}|2030-05-12T10:00"} for i, p in enumerate(prices, 1)]


# ---------- plan_detail_needs ----------
def test_sem_historico_le_os_n_mais_baratos_e_abre_a_compra_dos_top():
    c = cards(900, 500, 700, 300, 800)
    det, book = plan_detail_needs(c, {}, n=3, top=2, ttl_h=24, now=NOW)
    assert [d.split()[1] for d in det] == ["300", "500", "700"]          # os 3 mais baratos, do mais barato ao mais caro
    assert [b.split()[1] for b in book] == ["300", "500"]


def test_voo_com_detalhe_fresco_nao_e_lido_de_novo():
    c = cards(300, 500, 700)
    known = {"LA 1|2030-05-12T10:00": {"detail_at": ago(2), "booking_at": ago(2)},        # fresco
             "LA 2|2030-05-12T10:00": {"detail_at": ago(30), "booking_at": ago(30)}}      # velho (> 24 h)
    det, book = plan_detail_needs(c, known, n=3, top=3, ttl_h=24, now=NOW)
    assert [d.split()[1] for d in det] == ["500", "700"] and [b.split()[1] for b in book] == ["500", "700"]


def test_detalhe_e_compra_tem_validade_independente():
    c = cards(300)
    known = {"LA 1|2030-05-12T10:00": {"detail_at": ago(1), "booking_at": ago(50)}}
    det, book = plan_detail_needs(c, known, n=1, top=1, ttl_h=24, now=NOW)
    assert det == [] and len(book) == 1


def test_ttl_zero_sempre_le_e_sem_compra_para_multidestino():
    c = cards(300, 400)
    known = {"LA 1|2030-05-12T10:00": {"detail_at": ago(0.1), "booking_at": ago(0.1)}}
    det, book = plan_detail_needs(c, known, n=2, top=2, ttl_h=0, now=NOW)
    assert len(det) == 2 and len(book) == 2
    det, book = plan_detail_needs(c, {}, n=2, top=0, ttl_h=24, now=NOW)       # top 0 (multi-destino / sem compra)
    assert len(det) == 2 and book == []


def test_card_sem_chave_ou_n_zero():
    c = [{"aria": "From 100 Brazilian reais", "key": None}]
    assert plan_detail_needs(c, {}, n=1, top=1, ttl_h=24, now=NOW) == (["From 100 Brazilian reais"], ["From 100 Brazilian reais"])
    assert plan_detail_needs(c, {}, n=0, top=0, ttl_h=24, now=NOW) == ([], [])


def test_is_fresh():
    assert is_fresh(ago(1), NOW, 24) and not is_fresh(ago(25), NOW, 24) and not is_fresh(None, NOW, 24) and not is_fresh("lixo", NOW, 24)


# ---------- copiar adiante ----------
BOOKING = {"bags": ["1 free carry-on"], "bag_policy": "x", "typical_low": 2000, "typical_high": 3000, "verdict": "typical", "url": "https://www.google.com/x",
           "sellers": [{"seller": "LATAM", "type": "Airline", "price": 2500.0, "price_text": "R$2,500", "other_price_text": "R$2,600", "notes": []}]}


def offer(price, flight="LA 1", dep="2030-05-12T10:00", **kw):
    return Offer(price=price, currency="BRL", airline="LATAM", operated_by=None, origin="GRU", destination="LIS", dep_time=dep, arr_time="2030-05-12T20:00",
                 duration_min=600, stops=0, flight_numbers=flight, layover_airports=None, layovers=[], section="Top", position=1, **kw)


def test_copia_adiante_sem_preco_de_vendedor_e_com_a_data_original():
    prior = {"fields": {"cabin": "Economy", "aircraft": "Boeing 787", "co2_kg": 500, "co2_diff_pct": -10, "overnight": True, "often_delayed": False,
                        "segments": [{"flight": "LA 1"}]}, "detail_at": ago(5), "booking": BOOKING, "booking_at": ago(7)}
    o = offer(2400)
    carry_forward(o, prior, NOW, got_detail=False, got_booking=False)
    assert (o.cabin, o.aircraft, o.co2_kg, o.overnight, o.segments) == ("Economy", "Boeing 787", 500, True, [{"flight": "LA 1"}])
    assert o.detail_at == ago(5) and o.booking_at == ago(7)                     # a idade verdadeira, não a de agora
    s = o.booking["sellers"][0]
    assert s["seller"] == "LATAM" and s["price"] is None and s["price_text"] is None and o.booking["bags"] == ["1 free carry-on"]
    assert o.booking["carried"] is True and BOOKING["sellers"][0]["price"] == 2500.0   # o original não foi alterado


def test_lido_agora_carimba_agora_e_nao_mistura_com_o_anterior():
    prior = {"fields": {"aircraft": "Velho"}, "detail_at": ago(40), "booking": BOOKING, "booking_at": ago(40)}
    o = offer(2400, aircraft="Novo", booking=copy.deepcopy(BOOKING))
    carry_forward(o, prior, NOW, got_detail=True, got_booking=True)
    assert o.aircraft == "Novo" and o.detail_at == fmt_ts(NOW) and o.booking_at == fmt_ts(NOW) and o.booking["sellers"][0]["price"] == 2500.0


def test_sem_registro_anterior_nada_muda():
    o = offer(2400)
    carry_forward(o, {}, NOW, got_detail=False, got_booking=False)
    assert o.detail_at is None and o.booking_at is None and o.booking is None


def test_strip_seller_prices_ignora_none():
    assert strip_seller_prices(None) is None


# ---------- banco: detalhes conhecidos ----------
D = date(2030, 5, 12)


def setup(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    spec = SearchSpec("GRU", "LIS", D)
    return conn, spec, db.add_watch(conn, spec)


def test_known_details_le_o_mais_recente_com_a_idade_certa(tmp_path):
    conn, spec, wid = setup(tmp_path)
    a = offer(2500, aircraft="Airbus A320", segments=[{"flight": "LA 1"}], detail_at=ago(30), booking=copy.deepcopy(BOOKING), booking_at=ago(30))
    db.save_search(conn, spec, ScrapeResult([a, offer(3000, "TP 9")], "u", "typical", typical_low=2000.0, typical_high=3000.0), watch_id=wid)
    conn.execute("UPDATE searches SET scraped_at = ?", (ago(30),)); conn.commit()
    b = offer(2400, aircraft="Boeing 787", segments=[{"flight": "LA 1"}], detail_at=ago(3), booking_at=None)
    db.save_search(conn, spec, ScrapeResult([b], "u", "typical"), watch_id=wid)
    k = db.known_details(conn, wid, spec)
    e = k["LA 1|2030-05-12T10:00"]
    assert e["fields"]["aircraft"] == "Boeing 787" and e["detail_at"] == ago(3)            # linha mais nova vence
    assert e["booking"]["bags"] == ["1 free carry-on"] and e["booking_at"] == ago(30)     # a compra segue a antiga (a nova não tinha)
    assert "TP 9|2030-05-12T10:00" in k and "fields" not in k["TP 9|2030-05-12T10:00"]    # sem detalhe: só existe a chave
    assert k["__insight__"]["typical_low"] == 2000.0


def test_known_details_sem_carimbo_usa_a_data_da_coleta_e_consulta_avulsa_e_vazia(tmp_path):
    conn, spec, wid = setup(tmp_path)
    db.save_search(conn, spec, ScrapeResult([offer(2500, aircraft="A320", segments=[{"x": 1}], booking=copy.deepcopy(BOOKING))], "u", "typical"), watch_id=wid)
    k = db.known_details(conn, wid, spec)
    assert k["LA 1|2030-05-12T10:00"]["detail_at"] == k["LA 1|2030-05-12T10:00"]["booking_at"] == conn.execute("SELECT scraped_at FROM searches").fetchone()[0]
    assert db.known_details(conn, None, spec) == {} and db.known_details(conn, wid, SearchSpec("GRU", "LIS", D + timedelta(days=3))) == {}   # outra data


def test_save_search_grava_os_carimbos_e_banco_antigo_migra(tmp_path):
    import sqlite3
    conn, spec, wid = setup(tmp_path)
    sid = db.save_search(conn, spec, ScrapeResult([offer(2500, detail_at=ago(1), booking_at=ago(2))], "u", "typical"), watch_id=wid)
    r = conn.execute("SELECT detail_at, booking_at FROM offers WHERE search_id = ?", (sid,)).fetchone()
    assert (r[0], r[1]) == (ago(1), ago(2))
    c = sqlite3.connect(tmp_path / "old.db")
    c.executescript("CREATE TABLE offers (id INTEGER PRIMARY KEY, search_id INTEGER, position INTEGER, price REAL NOT NULL, currency TEXT);")
    c.commit(); c.close()
    cols = {x["name"] for x in db.connect(tmp_path / "old.db").execute("PRAGMA table_info(offers)")}
    assert {"detail_at", "booking_at"} <= cols


def test_dashboard_expoe_a_idade_do_detalhe(tmp_path):
    conn, spec, wid = setup(tmp_path)
    db.save_search(conn, spec, ScrapeResult([offer(2500, detail_at=ago(5), booking_at=ago(6))], "u", "typical"), watch_id=wid)
    o = db.build_dashboard(conn, today=date(2030, 1, 1))["watches"][0]["combos"][0]["offers"][0]
    assert o["detail_at"] == ago(5) and o["booking_at"] == ago(6)


# ---------- pausa adaptativa e perfil ----------
def test_pacer_dobra_no_erro_e_volta_no_sucesso(monkeypatch):
    waits = []
    monkeypatch.setattr(cli.time, "sleep", lambda s: waits.append(s))
    monkeypatch.setattr(cli.random, "random", lambda: 0.5)           # fator 1.0
    p = cli.Pacer(3.0)
    p.wait(); assert waits[-1] == pytest.approx(3.0)
    p.error(); p.wait(); assert waits[-1] == pytest.approx(6.0)
    p.error(); p.error(); p.error(); p.wait(); assert waits[-1] == pytest.approx(12.0)       # teto: 4x a base
    for _ in range(30):
        p.ok()
    p.wait(); assert waits[-1] == pytest.approx(3.0)                                         # volta à base
    z = cli.Pacer(0); z.error(); z.wait()                                                    # base 0 (testes): nunca dorme... a menos que erro
    assert z.cur <= z.cap


def test_print_profile_mostra_percentuais(capsys):
    class S:
        timings = {"goto": 6.0, "compra": 30.0}
        counts = {"goto": 3}
    cli.print_profile(S(), 3)
    out = capsys.readouterr().out
    assert "compra" in out and "10.0 s por consulta" in out and "83 %" in out
    cli.print_profile(object(), 3)       # sem métricas: nada
