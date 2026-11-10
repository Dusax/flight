from datetime import date

from flights.models import SearchSpec
from flights.tfs import build_tfs, build_url

# Strings validadas ao vivo: o Google Voos abriu a busca com exatamente esses filtros.
GOLDEN_ROUNDTRIP_2ADT = "GhoSCjIwMjYtMTItMjBqBRIDR1JVcgUSA0xJUxoaEgoyMDI2LTEyLTMwagUSA0xJU3IFEgNHUlVAAUABSAGYAQE"
GOLDEN_BUSINESS_1STOP = "GhwSCjIwMjYtMTItMjAoAWoFEgNHUlVyBRIDTElTGhwSCjIwMjYtMTItMzAoAWoFEgNMSVNyBRIDR1JVQAFAAUgDmAEB"


def test_roundtrip_two_adults():
    spec = SearchSpec("GRU", "LIS", date(2026, 12, 20), date(2026, 12, 30), adults=2)
    assert build_tfs(spec) == GOLDEN_ROUNDTRIP_2ADT


def test_business_max_one_stop():
    spec = SearchSpec("GRU", "LIS", date(2026, 12, 20), date(2026, 12, 30), adults=2, cabin="business", max_stops=1)
    assert build_tfs(spec) == GOLDEN_BUSINESS_1STOP


def test_one_way_has_single_leg_and_trip_type_2():
    spec = SearchSpec("GRU", "GIG", date(2026, 12, 5))
    import base64
    raw = base64.urlsafe_b64decode(build_tfs(spec) + "==")
    assert raw.count(b"GIG") == 1 and raw.endswith(b"\x98\x01\x02")


def test_url_forces_english_and_currency():
    url = build_url(SearchSpec("GRU", "GIG", date(2026, 12, 5), currency="USD"))
    assert "hl=en" in url and "curr=USD" in url and "tfu=" in url and "tfs=" in url
