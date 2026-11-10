"""Converte os dados crus de cada card de voo em `Offer`.

Cada card do Google Voos tem um `aria-label` longo e muito regular (em inglês,
porque fixamos hl=en), por exemplo:

    From 3552 Brazilian reals. 2 stops flight with Gol. Operated by Wamos for
    Gol. Leaves São Paulo/Guarulhos ... at 10:10 PM on Sunday, December 20 and
    arrives at Lisbon Airport at 9:45 PM on Monday, December 21. Total duration
    20 hr 35 min. Layover (1 of 2) is a 6 hr layover at ... in Vitoria. ...

Além disso o card traz `data-travelimpactmodelwebsiteurl` com o itinerário em
código IATA (`GRU-VIX-G3-1388-20261220,VIX-GIG-G3-1859-20261221,...`), que é
independente de idioma e usamos para aeroportos, voos e datas.

Os nomes de classe CSS do Google são ofuscados e mudam; por isso nada aqui
depende deles.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Dict, List, Optional, Tuple

from .models import Offer, SearchSpec

MONTHS = {
    m: i
    for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december"], start=1)
}

_PRICE = re.compile(r"^From\s+([\d.,]+)\s")
_STOPS_AIRLINE = re.compile(r"(Nonstop|(\d+)\s+stops?)\s+flight with\s+(.+?)\.(?=\s+(?:Operated by|Leaves)\s)")
_OPERATED = re.compile(r"Operated by\s+(.+?)\.\s+Leaves\s")
_LEAVES = re.compile(
    r"Leaves\s+.+?\s+at\s+(?P<dt>\d{1,2}:\d{2}(?:\s?[AP]M)?)\s+on\s+\w+,\s+(?P<dmon>\w+)\s+(?P<dday>\d{1,2})"
    r"\s+and arrives at\s+.+?\s+at\s+(?P<at>\d{1,2}:\d{2}(?:\s?[AP]M)?)\s+on\s+\w+,\s+(?P<amon>\w+)\s+(?P<aday>\d{1,2})"
)
_DURATION = re.compile(r"Total duration\s+(.+?)\.(?:\s|$)")
_LAYOVER = re.compile(
    r"Layover \((\d+) of \d+\) is an? (.+?) layover at (.+?)(?: in (.+?))?\.(?=\s+(?:Layover|Select)|\s*$)"
)


def parse_time(text: str) -> Tuple[int, int]:
    m = re.match(r"(\d{1,2}):(\d{2})\s?([AP]M)?$", text.strip().replace(" ", " "), re.I)
    if not m:
        raise ValueError(f"hora inválida: {text!r}")
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3)
    if ap:
        ap = ap.upper()
        h = h % 12 + (12 if ap == "PM" else 0)
    return h, mi


def parse_duration(text: str) -> Optional[int]:
    """'20 hr 35 min' -> 1235; '1 hr' -> 60; '45 min' -> 45."""
    hours = re.search(r"(\d+)\s*hr", text)
    mins = re.search(r"(\d+)\s*min", text)
    if not hours and not mins:
        return None
    return (int(hours.group(1)) * 60 if hours else 0) + (int(mins.group(1)) if mins else 0)


def parse_itinerary(itin: Optional[str]) -> List[dict]:
    """'GRU-VIX-G3-1388-20261220,VIX-GIG-...' -> lista de segmentos."""
    segs = []
    for part in (itin or "").split(","):
        bits = part.strip().split("-")
        if len(bits) >= 5 and len(bits[0]) == 3 and len(bits[1]) == 3:
            segs.append({"from": bits[0], "to": bits[1], "carrier": bits[2],
                         "number": "-".join(bits[3:-1]), "date": bits[-1]})
    return segs


def _iso(d: date, hm: Tuple[int, int]) -> str:
    return f"{d.isoformat()}T{hm[0]:02d}:{hm[1]:02d}"


def _to_price(raw: str) -> float:
    """'3552' / '3,552' / '3.552,50' / '3,552.50' -> float."""
    raw = raw.strip().rstrip(".,")
    if "," in raw and "." in raw:
        # o último separador é o decimal
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        head, _, tail = raw.rpartition(",")
        raw = head.replace(",", "") + ("." + tail if len(tail) != 3 else tail)
    return float(raw)



# --- detalhe expandido do card -------------------------------------------------

_TIME_AIRPORT = re.compile(
    r"^(?P<t>\d{1,2}:\d{2}(?:\s?[AP]M)?)(?:\+(?P<plus>\d))?(?P<airport>.+?)\s\((?P<code>[A-Z]{3})\)$")
_CARRIER_LINE = re.compile(
    r"^(?P<airline>.+?)(?P<cabin>(?i:Premium economy|Economy|Business|First))"
    r"(?P<aircraft>.*?)(?P<flight>[A-Z0-9]{2}\s\d{1,4})$")
_LAYOVER_LINE = re.compile(
    r"^(?:(?P<h>\d+)\s*hr)?\s*(?:(?P<m>\d+)\s*min)?\s*layover(?P<city>.+?)\s\((?P<code>[A-Z]{3})\)(?P<note>.*)$")
_LEGROOM = re.compile(r"^(?P<q>.+?) legroom \((?P<inches>\d+) in\)$", re.I)
_TRAVEL = re.compile(r"^Travel time:\s*(?P<d>.+?)(?P<night>Overnight)?$")
_EMISSIONS = re.compile(r"^Emissions estimate:\s*([\d.,]+)\s*kg CO2e", re.I)
_CONTRAIL = re.compile(r"^Contrail warming potential:\s*(.+)$", re.I)
_HEADER_CO2 = re.compile(r"^([\d.,]+)\s*kg CO2e$")
_HEADER_DIFF = re.compile(r"^([+-]?\d+)%\s+emissions$")


def _int(s: str) -> int:
    return int(float(s.replace(",", "")))


def parse_detail(text: Optional[str]) -> Dict:
    """Lê o innerText de um card expandido. Devolve {segments, overnight, often_delayed, co2_kg, co2_diff_pct}.

    Cada segmento (um voo, entre duas decolagens/pousos) traz horários, aeroportos, companhia, classe,
    aeronave, nº do voo, espaço entre assentos, comodidades, emissões e a conexão que vem depois dele.
    """
    lines = [" ".join(l.split()) for l in (text or "").replace(" ", " ").splitlines()]
    lines = [l for l in lines if l]
    out: Dict = {"segments": [], "overnight": False, "often_delayed": False, "co2_kg": None, "co2_diff_pct": None}
    cur: Optional[Dict] = None
    for line in lines:
        m = _TIME_AIRPORT.match(line)
        if m:
            parsed = {"time": m["t"], "plus_days": int(m["plus"] or 0), "airport": m["airport"].strip(), "code": m["code"]}
            if cur is None or "arr" in cur:
                cur = {"dep": parsed}
                out["segments"].append(cur)
            else:
                cur["arr"] = parsed
            continue
        if cur is None:  # cabeçalho do card
            hm_ = _HEADER_CO2.match(line)
            if hm_:
                out["co2_kg"] = _int(hm_.group(1))
            hd = _HEADER_DIFF.match(line)
            if hd:
                out["co2_diff_pct"] = int(hd.group(1))
            elif re.match(r"^Avg emissions$", line, re.I):
                out["co2_diff_pct"] = 0
            continue
        if (m := _TRAVEL.match(line)):
            cur["duration_min"] = parse_duration(m["d"])
            if m["night"]:
                cur["overnight"] = out["overnight"] = True
        elif (m := re.match(r"^Plane and crew by (.+)$", line)):
            cur["plane_and_crew"] = m.group(1)
        elif (m := _LEGROOM.match(line)):
            cur["legroom"], cur["legroom_in"] = m["q"], int(m["inches"])
        elif (m := _EMISSIONS.match(line)):
            cur["co2_kg"] = _int(m.group(1))
        elif (m := _CONTRAIL.match(line)):
            cur["contrail"] = m.group(1)
        elif line.lower().startswith("often delayed"):
            cur["often_delayed"] = out["often_delayed"] = True
            cur["delay_note"] = line
        elif (m := _LAYOVER_LINE.match(line)):
            mins = (int(m["h"] or 0) * 60) + int(m["m"] or 0)
            note = m["note"].strip()
            cur["layover_after"] = {"duration_min": mins, "city": m["city"].strip(), "code": m["code"], "note": note or None}
            if "overnight" in note.lower():
                out["overnight"] = True
        elif "airline" not in cur and (m := _CARRIER_LINE.match(line)):
            cur.update(airline=m["airline"].strip(), cabin=m["cabin"].strip().title(),
                       aircraft=m["aircraft"].strip() or None, flight=m["flight"])
        else:
            cur.setdefault("amenities", []).append(line)
    return out


# --- tela de compra (só ida) -----------------------------------------------------

_MONEY_LINE = re.compile(r"^(?:[A-Z]{0,3}\$|€|£)\s?[\d.,]+$")


def parse_booking(text: Optional[str], rows: Optional[List[Dict]] = None, url: Optional[str] = None) -> Dict:
    """Lê a página /booking de um voo: bagagem, vendedores (com preço) e a avaliação de preço do Google.

    `rows` = [{seller, text}] já separados pelo navegador, um por linha "Book with X".
    Só preenche o que achar; o resto fica None/[] (o Google varia o que mostra por país e voo).
    """
    lines = [" ".join(l.split()) for l in (text or "").splitlines()]
    lines = [l for l in lines if l]
    out: Dict = {"url": url, "bags": [], "bag_policy": None, "sellers": [], "verdict": None, "verdict_cabin": None,
                 "typical_low": None, "typical_high": None, "unsupported_location": False}
    out["unsupported_location"] = any("isn't supported yet in your location" in l for l in lines)

    # Bagagem: o bloco fica entre a linha de emissões e o aviso "Baggage conditions/prices apply...".
    # Linhas curtas = condições (ex.: "1 free carry-on"); a longa, quando existe, é a política da companhia.
    stop = next((i for i, l in enumerate(lines) if re.match(r"Baggage (conditions|prices) apply", l)), None)
    if stop is not None:
        start = next((i for i in range(stop - 1, -1, -1) if re.search(r"(emissions|kg CO2e)$", lines[i])), max(0, stop - 6))
        block = lines[start + 1:stop]
        out["bags"] = [l for l in block if len(l) <= 120 and not l.startswith("First checked bag costs")]
        long_ = [l for l in block if len(l) > 120]
        out["bag_policy"] = " ".join(long_) or None

    for r in rows or []:
        rl = [" ".join(x.split()) for x in (r.get("text") or "").splitlines() if x.strip()]
        seller = (r.get("seller") or "").strip()
        tag = (r.get("tag") or "").strip() or None
        prices = [l for l in rl if _MONEY_LINE.match(l)]
        # "View options" é só o rótulo acessível do botão Continue, não revela tarifas: ignorado.
        notes = [l for l in rl if l not in prices and not l.startswith("Book with") and l not in ("Continue", "View options")]
        out["sellers"].append({
            "seller": seller, "type": tag or None,
            "price": _to_price(re.sub(r"^\D+", "", prices[0])) if prices else None,
            "price_text": prices[0] if prices else None,
            "other_price_text": prices[1] if len(prices) > 1 else None,
            "notes": notes,
        })

    body = "\n".join(lines)
    m = re.search(r"is (low|typical|high) for ([A-Za-z ]+?)(?:\n|$)", body)
    if m:
        out["verdict"], out["verdict_cabin"] = m.group(1), m.group(2).strip()
    m = re.search(r"usually cost between\s+\D*?([\d.,]+)\s*[–-]\s*\D*?([\d.,]+)", body)
    if m:
        out["typical_low"], out["typical_high"] = _to_price(m.group(1)), _to_price(m.group(2))
    return out


def _detail_fields(card: Dict) -> Dict:
    """Campos de detalhe do Offer a partir do que o scraper coletou (podem faltar)."""
    d = parse_detail(card.get("detail")) if card.get("detail") else None
    co2 = card.get("co2_g")
    segs = d["segments"] if d else []
    return {
        "cabin": next((s["cabin"] for s in segs if s.get("cabin")), None),
        "aircraft": ", ".join(dict.fromkeys(s["aircraft"] for s in segs if s.get("aircraft"))) or None,
        # os atributos data-co2* do card não dependem de idioma, então têm prioridade
        "co2_kg": round(co2 / 1000) if co2 else (d["co2_kg"] if d else None),
        "co2_diff_pct": card["co2_diff_pct"] if card.get("co2_diff_pct") is not None else (d["co2_diff_pct"] if d else None),
        "overnight": d["overnight"] if d else None,
        "often_delayed": d["often_delayed"] if d else None,
        "segments": segs,
        "booking": card.get("booking"),
    }


def parse_card(card: Dict, spec: SearchSpec, position: int = 0) -> Optional[Offer]:
    """Devolve None quando o card não tem os campos essenciais (preço/horários)."""
    aria = " ".join((card.get("aria") or "").split())
    pm = _PRICE.match(aria)
    leaves = _LEAVES.search(aria)
    if not pm or not leaves:
        return None
    try:
        price = _to_price(pm.group(1))
        dep_hm, arr_hm = parse_time(leaves["dt"]), parse_time(leaves["at"])
    except ValueError:
        return None

    segs = parse_itinerary(card.get("itinerary"))
    dep_date = spec.depart_date
    if segs:
        d = segs[0]["date"]
        dep_date = date(int(d[:4]), int(d[4:6]), int(d[6:8]))
    # O label não traz o ano da chegada: assume o da partida e vira o ano se o mês voltar.
    amon, aday = MONTHS.get(leaves["amon"].lower()), int(leaves["aday"])
    arr_date = None
    if amon:
        year = dep_date.year + (1 if (amon, aday) < (dep_date.month, dep_date.day) else 0)
        try:
            arr_date = date(year, amon, aday)
        except ValueError:
            arr_date = None

    sm = _STOPS_AIRLINE.search(aria)
    stops = 0 if not sm or sm.group(1) == "Nonstop" else int(sm.group(2))
    if sm is None and segs:
        stops = len(segs) - 1
    airline = sm.group(3) if sm else ""

    om = _OPERATED.search(aria)
    operated = None
    if om:
        operated = ", ".join(dict.fromkeys(x.strip() for x in om.group(1).split(",")))

    dm = _DURATION.search(aria)
    layovers = [
        {"n": int(n), "duration_min": parse_duration(dur), "airport": airport.strip(), "city": (city or "").strip()}
        for n, dur, airport, city in _LAYOVER.findall(aria)
    ]
    origin = segs[0]["from"] if segs else spec.origin
    destination = segs[-1]["to"] if segs else spec.destination

    return Offer(
        price=price,
        currency=spec.currency,
        airline=airline,
        operated_by=operated,
        origin=origin,
        destination=destination,
        dep_time=_iso(dep_date, dep_hm),
        arr_time=_iso(arr_date, arr_hm) if arr_date else None,
        duration_min=parse_duration(dm.group(1)) if dm else None,
        stops=stops,
        flight_numbers=", ".join(f"{s['carrier']} {s['number']}" for s in segs) or None,
        layover_airports=",".join(s["from"] for s in segs[1:]) or None,
        layovers=layovers,
        section=card.get("section"),
        position=position,
        **_detail_fields(card),
    )


def parse_cards(cards: List[Dict], spec: SearchSpec) -> Tuple[List[Offer], int, int]:
    """Parseia e remove duplicatas (a página repete as listas).

    Devolve (ofertas, sem_preço, ilegíveis). "Sem preço" são cards em que o próprio
    Google diz "Total price is unavailable"; "ilegíveis" são cards que não
    conseguimos ler por outro motivo (sinal de que o layout mudou).
    """
    offers, seen, no_price, unreadable = [], set(), 0, 0
    counted = set()
    for card in cards:
        offer = parse_card(card, spec, position=len(offers) + 1)
        if offer is None:
            ident = (card.get("aria"), card.get("itinerary"))
            if ident in counted:
                continue
            counted.add(ident)
            if (card.get("aria") or "").startswith("Total price is unavailable"):
                no_price += 1
            else:
                unreadable += 1
            continue
        if offer.key() in seen:
            continue
        seen.add(offer.key())
        offers.append(offer)
    return offers, no_price, unreadable
