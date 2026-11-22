"""'something went wrong' passageiro do Google: nova tentativa (com contexto zerado) antes de dar erro."""
from datetime import date

import pytest

from flights import scraper as sc
from flights.models import SearchSpec

SPEC = SearchSpec("GRU", "LIS", date(2030, 5, 1))


def make(monkeypatch, outcomes):
    monkeypatch.setenv("FLIGHTS_RETRY_WAIT", "0")
    s = sc.FlightScraper()
    calls = {"n": 0, "resets": 0}

    def once(spec, detail, booking, known=None, ttl=None):
        calls["n"] += 1
        out = outcomes[calls["n"] - 1]
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(s, "_search_once", once)
    monkeypatch.setattr(s, "_reset_context", lambda: calls.__setitem__("resets", calls["resets"] + 1))
    return s, calls


def test_passa_na_terceira_tentativa(monkeypatch):
    err = sc.ScrapeError("O Google Voos respondeu 'something went wrong'.")
    s, calls = make(monkeypatch, [err, err, "resultado"])
    assert s.search(SPEC) == "resultado" and calls == {"n": 3, "resets": 2}


def test_desiste_depois_de_tres_tentativas(monkeypatch):
    err = sc.ScrapeError("O Google Voos respondeu 'something went wrong'.")
    s, calls = make(monkeypatch, [err, err, err])
    with pytest.raises(sc.ScrapeError):
        s.search(SPEC)
    assert calls["n"] == 3


def test_captcha_nao_repete(monkeypatch):
    s, calls = make(monkeypatch, [sc.BlockedError("captcha")])
    with pytest.raises(sc.BlockedError):
        s.search(SPEC)
    assert calls["n"] == 1
