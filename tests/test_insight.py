"""Faixa de preço típica da rota (painel 'View price history' do Google), para qualquer busca só ida / ida e volta."""
from datetime import date

from flights import db
from flights.models import Offer, ScrapeResult, SearchSpec
from flights.parser import parse_insight

LIVE = """Prices are currently typical for your search
The least expensive flights for similar trips to Lisbon usually cost between R$3,700–4,600.
R$4,558 is typical
R$3,700
R$4,600
Price history for this search"""


def test_parse_insight_do_texto_real():
    assert parse_insight(LIVE) == {"level": "typical", "typical_low": 3700.0, "typical_high": 4600.0}
    assert parse_insight("The least expensive flights usually cost between $1,400 – 2,950.")["typical_high"] == 2950.0
    assert parse_insight("Prices are currently low for your search") == {"level": "low"}
    assert parse_insight("nada a ver") == {}
    assert parse_insight("usually cost between R$5,000–R$4,000") == {}   # faixa invertida é ignorada


def offer(price):
    return Offer(price=price, currency="BRL", airline="LATAM", operated_by=None, origin="GRU", destination="LIS", dep_time="2030-05-01T22:10",
                 arr_time="2030-05-02T12:00", duration_min=600, stops=0, flight_numbers="LA 8084", layover_airports=None, layovers=[],
                 section="Top", position=1)


def test_faixa_tipica_vai_para_o_painel_de_qualquer_busca(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    spec = SearchSpec("GRU", "LIS", date(2030, 5, 1), date(2030, 5, 10))
    wid = db.add_watch(conn, spec)
    db.save_search(conn, spec, ScrapeResult([offer(4000)], "u", "typical", typical_low=3700.0, typical_high=4600.0), watch_id=wid)
    w = db.build_dashboard(conn, today=date(2030, 1, 1))["watches"][0]
    c = w["combos"][0]
    assert (c["typical_low"], c["typical_high"], c["price_level"]) == (3700.0, 4600.0, "typical")
    db.save_search(conn, spec, ScrapeResult([offer(3900)], "u", "typical"), watch_id=wid)   # sem faixa (ex.: multi-destino): vem vazio
    c = db.build_dashboard(conn, today=date(2030, 1, 1))["watches"][0]["combos"][0]
    assert c["typical_low"] is None and c["typical_high"] is None


def test_banco_antigo_ganha_as_colunas(tmp_path):
    import sqlite3
    c = sqlite3.connect(tmp_path / "old.db")
    c.executescript("CREATE TABLE searches (id INTEGER PRIMARY KEY, watch_id INTEGER, scraped_at TEXT NOT NULL, origin TEXT NOT NULL, destination TEXT NOT NULL,"
                    "depart_date TEXT NOT NULL, return_date TEXT, trip_type TEXT NOT NULL, adults INTEGER NOT NULL, cabin TEXT NOT NULL, max_stops INTEGER,"
                    "currency TEXT NOT NULL, status TEXT NOT NULL, error TEXT, offers_count INTEGER, min_price REAL, price_level TEXT, url TEXT);")
    c.commit(); c.close()
    conn = db.connect(tmp_path / "old.db")
    assert {"typical_low", "typical_high"} <= {r["name"] for r in conn.execute("PRAGMA table_info(searches)")}
