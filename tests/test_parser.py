import json
from datetime import date
from pathlib import Path

from flights.models import SearchSpec
from flights.parser import parse_card, parse_cards, parse_duration, parse_time, _to_price

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "cards_roundtrip_GRU_LIS.json").read_text())
SPEC = SearchSpec("GRU", "LIS", date(2026, 12, 20), date(2026, 12, 30))


def test_helpers():
    assert parse_time("10:10 PM") == (22, 10)
    assert parse_time("12:05 AM") == (0, 5)
    assert parse_time("12:30 PM") == (12, 30)
    assert parse_time("15:05") == (15, 5)
    assert parse_time("3:05 PM") == (15, 5)
    assert parse_duration("20 hr 35 min") == 1235
    assert parse_duration("1 hr") == 60
    assert parse_duration("45 min") == 45
    assert _to_price("3552") == 3552
    assert _to_price("3,552") == 3552
    assert _to_price("3.552,50") == 3552.5
    assert _to_price("3,552.50") == 3552.5


def test_nonstop_operated_by():
    o = parse_card(FIXTURE["cards"][0], SPEC)
    assert (o.price, o.currency, o.stops) == (6138, "BRL", 0)
    assert o.airline == "LATAM" and o.operated_by == "Latam Airlines Brasil"
    assert (o.origin, o.destination) == ("GRU", "LIS")
    assert o.flight_numbers == "LA 8146"
    assert o.dep_time == "2026-12-20T17:50" and o.arr_time == "2026-12-21T06:20"  # chega no dia seguinte
    assert o.duration_min == 570 and o.layovers == [] and o.layover_airports is None


def test_arrival_same_day():
    o = parse_card(FIXTURE["cards"][1], SPEC)  # TAP 01:30 -> 14:30 (fuso muda o dia)
    assert o.dep_time == "2026-12-20T01:30" and o.arr_time == "2026-12-20T14:30"


def test_one_stop_has_layover():
    o = parse_card(FIXTURE["cards"][2], SPEC)
    assert o.stops == 1 and o.airline == "Air Europa"
    assert o.flight_numbers == "UX 58, UX 1159" and o.layover_airports == "MAD"
    assert len(o.layovers) == 1 and o.layovers[0]["duration_min"] == 80
    assert "Madrid" in o.layovers[0]["airport"]


def test_two_stops():
    o = parse_card(FIXTURE["cards"][3], SPEC)
    assert o.stops == 2 and o.layover_airports == "MUC,FRA" and len(o.layovers) == 2


def test_one_way_label_without_round_trip_text():
    # Rótulo real de uma busca só de ida (GRU→LIS): não tem "round trip total".
    card = {
        "aria": "From 3552 Brazilian reals. 2 stops flight with Gol. Operated by Wamos for Gol. Leaves São "
                "Paulo/Guarulhos–Governor André Franco Montoro International Airport at 10:10 PM on Sunday, "
                "December 20 and arrives at Lisbon Airport at 9:45 PM on Monday, December 21. Total duration "
                "20 hr 35 min. Layover (1 of 2) is a 6 hr layover at Aeroporto de Vitória - Eurico de Aguiar "
                "Salles in Vitoria. Layover (2 of 2) is a 2 hr 15 min layover at RIOgaleão International "
                "Airport in Rio de Janeiro. Select flight",
        "itinerary": "GRU-VIX-G3-1388-20261220,VIX-GIG-G3-1859-20261221,GIG-LIS-G3-7020-20261221",
    }
    o = parse_card(card, SearchSpec("GRU", "LIS", date(2026, 12, 20)))
    assert o.price == 3552 and o.stops == 2 and o.dep_time == "2026-12-20T22:10"
    assert o.arr_time == "2026-12-21T21:45" and o.duration_min == 1235
    assert [l["duration_min"] for l in o.layovers] == [360, 135]


def test_arrival_year_rollover():
    card = dict(FIXTURE["cards"][0])
    card["aria"] = card["aria"].replace("December 21", "January 1")
    card["itinerary"] = "GRU-LIS-LA-8146-20261231"
    o = parse_card(card, SearchSpec("GRU", "LIS", date(2026, 12, 31)))
    assert o.arr_time == "2027-01-01T06:20"


def test_price_unavailable_is_skipped_and_counted_separately():
    assert parse_card(FIXTURE["cards"][4], SPEC) is None
    offers, no_price, unreadable = parse_cards(FIXTURE["cards"], SPEC)
    assert (no_price, unreadable) == (1, 0)
    assert len(offers) == 4  # a duplicata proposital foi removida
    assert [o.position for o in offers] == [1, 2, 3, 4]


def test_unknown_layout_is_reported_as_unreadable():
    offers, no_price, unreadable = parse_cards([{"aria": "algo completamente diferente"}], SPEC)
    assert offers == [] and no_price == 0 and unreadable == 1
