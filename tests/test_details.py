import json
from datetime import date
from pathlib import Path

from flights.models import SearchSpec
from flights.parser import parse_booking, parse_card, parse_detail

# Capturado ao vivo do Google Voos (3 cards detalhados de GRU→LIS e 3 telas de compra).
FX = json.loads((Path(__file__).parent / "fixtures" / "details_and_booking_live.json").read_text())
SPEC = SearchSpec("GRU", "LIS", date(2026, 12, 20))
NONSTOP, ONE_STOP, TWO_STOPS = FX["cards"]


def test_detail_nonstop_card():
    d = parse_detail(NONSTOP["detail"])
    assert (d["co2_kg"], d["co2_diff_pct"], d["overnight"], d["often_delayed"]) == (431, -17, True, True)
    (s,) = d["segments"]
    assert (s["flight"], s["cabin"], s["aircraft"]) == ("LA 8148", "Economy", "Boeing 787")
    assert (s["dep"]["code"], s["dep"]["time"], s["arr"]["code"], s["arr"]["plus_days"]) == ("GRU", "10:40 PM", "LIS", 1)
    assert (s["duration_min"], s["legroom"], s["legroom_in"]) == (580, "Above average", 32)
    assert s["plane_and_crew"] == "Latam Airlines Brasil" and "On-demand video" in s["amenities"]
    assert s["contrail"] == "Low" and s["co2_kg"] == 431


def test_detail_connections_per_segment():
    d = parse_detail(TWO_STOPS["detail"])
    assert [s["flight"] for s in d["segments"]] == ["G3 1388", "G3 1859", "G3 7020"]
    assert [s["aircraft"] for s in d["segments"]] == ["Boeing 737MAX 8 Passenger", "Boeing 737", "Airbus A330"]
    assert [(s.get("layover_after") or {}).get("duration_min") for s in d["segments"]] == [360, 135, None]
    first = d["segments"][0]["layover_after"]
    assert (first["code"], first["city"], first["note"]) == ("VIX", "Vitoria", "Long layover")
    assert d["segments"][2]["plane_and_crew"] == "Wamos for Gol"
    assert d["co2_diff_pct"] == 31 and not d["overnight"] and not d["often_delayed"]


def test_card_gets_detail_fields():
    o = parse_card(ONE_STOP, SPEC)
    assert (o.cabin, o.aircraft, o.overnight, o.often_delayed) == ("Economy", "Boeing 787, Boeing 737", True, False)
    assert (o.co2_kg, o.co2_diff_pct, len(o.segments)) == (451, -13, 2)
    assert o.segments[0]["layover_after"]["code"] == "MAD"


def test_co2_data_attributes_win_over_text():
    card = {**NONSTOP, "co2_g": 500000, "co2_diff_pct": -5}
    o = parse_card(card, SPEC)
    assert (o.co2_kg, o.co2_diff_pct) == (500, -5)


def test_card_without_detail_has_no_detail_fields():
    o = parse_card({"aria": NONSTOP["aria"], "itinerary": NONSTOP["itinerary"]}, SPEC)
    assert o.segments == [] and o.cabin is None and o.overnight is None and o.booking is None


def test_booking_gol_free_carry_on():
    v = FX["bookings"]["gru_lis"]
    b = parse_booking(v["text"], v["rows"], v["url"])
    assert b["bags"] == ["1 free carry-on", "1st checked bag available for a fee"]
    assert [(s["seller"], s["type"], s["price"]) for s in b["sellers"]] == [("Gol", "Airline", 3552.0)]
    assert (b["verdict"], b["verdict_cabin"], b["typical_low"], b["typical_high"]) == ("typical", "Economy", 2100.0, 4600.0)
    assert not b["unsupported_location"] and b["bag_policy"] is None


def test_booking_ryanair_has_fees_and_policy_paragraph():
    v = FX["bookings"]["lis_mad"]
    b = parse_booking(v["text"], v["rows"], v["url"])
    assert b["bags"] == ["1 carry-on bag: R$79–226", "1st checked bag: R$135–376"]
    assert "Ryanair allows passengers" in b["bag_policy"]
    assert b["sellers"][0]["price"] == 287.0 and (b["typical_low"], b["typical_high"]) == (140.0, 410.0)


def test_booking_tap_seller_price_and_bag_fee():
    v = FX["bookings"]["mad_gru"]
    b = parse_booking(v["text"], v["rows"], v["url"])
    assert b["bags"] == ["1 free carry-on", "1st checked bag: R$719"]
    assert b["sellers"][0]["seller"] == "Tap Air Portugal" and b["sellers"][0]["price"] == 4619.0


def test_booking_unsupported_location_is_reported():
    b = parse_booking("Booking options\nBooking isn't supported yet in your location. To see booking options...", [])
    assert b["unsupported_location"] and b["sellers"] == [] and b["bags"] == []


def test_booking_empty_is_safe():
    b = parse_booking(None, None)
    assert b["sellers"] == [] and b["verdict"] is None
