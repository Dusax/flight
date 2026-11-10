from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from . import db
from .models import CABINS, Leg, SearchSpec, parse_date, parse_iata, plan_specs
from .scraper import BlockedError, FlightScraper, ScrapeError

DEFAULT_DB = "data/flights.db"


# --- formatação -------------------------------------------------------------

def money(value: Optional[float], currency: str = "") -> str:
    if value is None:
        return "-"
    s = f"{value:,.0f}".replace(",", ".")
    return f"{currency} {s}".strip()


def hm(minutes: Optional[int]) -> str:
    if minutes is None:
        return "-"
    return f"{minutes // 60}h{minutes % 60:02d}"


def stops_txt(n: Optional[int]) -> str:
    return "direto" if n == 0 else f"{n} esc."


def print_table(headers: List[str], rows: List[List[str]]) -> None:
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)] if rows else [len(h) for h in headers]
    line = lambda r: "  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip()
    print(line(headers))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print(line(r))


def print_offers(offers, top: int) -> None:
    rows = []
    for o in list(offers)[:top]:
        rows.append([
            money(o["price"], o["currency"]), (o["airline"] or "")[:28], o["flight_numbers"] or "",
            (o["dep_time"] or "")[5:].replace("T", " "), (o["arr_time"] or "")[5:].replace("T", " "),
            hm(o["duration_min"]), stops_txt(o["stops"]), o["layover_airports"] or "",
        ])
    print_table(["preço", "companhia", "voo(s)", "saída", "chegada", "duração", "escalas", "conexões"], rows)


def print_offer_details(rows) -> None:
    """Detalhe de cada voo (aeronave, assentos, conexões) e da tela de compra, quando existem."""
    for o in rows:
        segs = json.loads(o["segments_json"]) if o["segments_json"] else []
        book = json.loads(o["booking_json"]) if o["booking_json"] else None
        if not segs and not book:
            continue
        tags = [t for t in (o["cabin"], o["aircraft"],
                            f"{o['co2_kg']} kg CO2" + (f" ({o['co2_diff_pct']:+d}%)" if o["co2_diff_pct"] else "") if o["co2_kg"] else None,
                            "pernoite" if o["overnight"] else None, "costuma atrasar" if o["often_delayed"] else None) if t]
        print(f"\n  {money(o['price'], o['currency'])}  {o['airline']}  {o['flight_numbers'] or ''}  ·  " + " · ".join(tags))
        for s in segs:
            dep, arr = s.get("dep") or {}, s.get("arr") or {}
            extra = [x for x in (f"assento {s['legroom']} ({s['legroom_in']} pol)" if s.get("legroom_in") else None,
                                 ", ".join(s.get("amenities") or []) or None) if x]
            print(f"    {s.get('flight', '?'):9} {dep.get('code', '')} {dep.get('time', '')} → {arr.get('code', '')} {arr.get('time', '')}"
                  f"{'(+%d)' % arr['plus_days'] if arr.get('plus_days') else ''}  {s.get('aircraft') or ''}  {'; '.join(extra)}")
            lay = s.get("layover_after")
            if lay:
                print(f"      conexão de {hm(lay['duration_min'])} em {lay['city']} ({lay['code']}){' · ' + lay['note'] if lay.get('note') else ''}")
        if book:
            sellers = ", ".join(f"{x['seller']} {money(x['price'], o['currency'])}" for x in book.get("sellers") or [])
            print(f"    compra: bagagem [{' | '.join(book.get('bags') or ['sem informação'])}]" + (f"  vendedor: {sellers}" if sellers else ""))
            if book.get("typical_low") is not None:
                print(f"    faixa típica da rota: {money(book['typical_low'], o['currency'])}–{money(book['typical_high'], o['currency'])}"
                      + (f"  (este voo: {book['verdict']})" if book.get("verdict") else ""))


# --- execução das coletas ---------------------------------------------------

def expand_flex(spec: SearchSpec, flex: int, compare_separate: bool = False) -> List[SearchSpec]:
    """Tudo a consultar: ±flex dias (todas as datas da viagem andam juntas) e, se pedido, cada trecho como só ida."""
    return plan_specs(spec, flex, compare_separate)


QUIET = False  # --quiet: nada que identifique a busca (rota, datas, preços, mensagens de erro) vai para a tela


def run_specs(
    conn, scraper: FlightScraper, items: List[Tuple[Optional[int], SearchSpec, int]], delay: float, top: int,
    max_offers: Optional[int] = None, detail: int = 30,
) -> Tuple[int, int]:
    """Coleta cada (watch_id, spec), grava no banco e imprime o resumo. Devolve (ok, falhas).

    Com QUIET (usado no GitHub Actions de repositório público, cujos logs são públicos) só saem
    contagens e status: as mensagens de erro do Playwright trazem a URL da busca, que contém a rota.
    """
    ok = failed = 0
    for i, (watch_id, spec, booking_top) in enumerate(items):
        if i:
            time.sleep(delay * (0.75 + random.random() * 0.5))  # pausa irregular entre buscas
        if QUIET:
            print(f"[{i + 1}/{len(items)}] ", end="", flush=True)
        else:
            print(f"\n[{i + 1}/{len(items)}] {spec.label()}  ({spec.adults} adulto(s), {spec.cabin}"
                  f"{', máx. ' + str(spec.max_stops) + ' esc.' if spec.max_stops is not None else ''})")
        try:
            result = scraper.search(spec, detail_offers=detail, booking_top=booking_top)
        except BlockedError as e:
            db.save_search(conn, spec, error=str(e), status="blocked", watch_id=watch_id)
            print("BLOQUEADO pelo Google (captcha); parando as demais." if QUIET else f"  ✗ BLOQUEADO: {e}")
            if not QUIET:
                print("  Parando as buscas restantes para não piorar o bloqueio.")
            return ok, failed + (len(items) - i)
        except ScrapeError as e:
            db.save_search(conn, spec, error=str(e), watch_id=watch_id)  # o texto completo fica só no banco (privado)
            print("erro (detalhe no painel)" if QUIET else f"  ✗ erro: {e}")
            failed += 1
            continue
        sid = db.save_search(conn, spec, result, watch_id=watch_id, max_offers=max_offers)
        ok += 1
        if QUIET:
            print(f"ok · {len(result.offers)} voos" + (f" · {len(result.warnings)} aviso(s)" if result.warnings else "")
                  + (f" · {result.cards_unreadable} ilegível(is)" if result.cards_unreadable else ""))
            continue
        for w in result.warnings:
            print(f"  ⚠ {w}")
        if not result.offers:
            print("  Nenhum voo encontrado.")
            continue
        extra = f" · Google diz: preços {result.price_level}" if result.price_level else ""
        print(f"  ✓ {len(result.offers)} voos (busca #{sid}){extra}"
              + (f" · {result.cards_no_price} sem preço no Google" if result.cards_no_price else "")
              + (f" · ⚠ {result.cards_unreadable} cards ilegíveis (layout mudou? rode com --debug)"
                 if result.cards_unreadable else ""))
        print_offers(db.search_offers(conn, sid), top)
    return ok, failed


def make_scraper(args) -> FlightScraper:
    debug_dir = Path(args.db).parent / "debug" if args.debug else None
    return FlightScraper(headless=not args.headed, chromium_path=args.chromium_path, debug_dir=debug_dir,
                         country=args.country)


def spec_from_args(args) -> SearchSpec:
    dep = parse_date(args.depart)
    ret = parse_date(args.ret) if args.ret else (dep + timedelta(days=args.nights) if args.nights else None)
    return SearchSpec(
        origin=parse_iata(args.origin), destination=parse_iata(args.destination), depart_date=dep,
        return_date=ret, adults=args.adults, cabin=args.cabin, max_stops=args.max_stops,
        currency=args.currency.upper(),
    )


def parse_stops(tokens: List[str]) -> List[Leg]:
    """['GRU@2026-12-20', 'LIS@2026-12-27', 'MAD@03/01/2027', 'GRU'] -> trechos GRU→LIS, LIS→MAD, MAD→GRU.

    Cada ponto leva a data em que você SAI dele; o último é só o aeroporto final (pode ser outro
    que o de origem). Assim também dá para fazer "open jaw".
    """
    stops = []
    for i, t in enumerate(tokens):
        if i == len(tokens) - 1:
            if "@" in t:
                raise ValueError(f"o último ponto ({t!r}) é só o aeroporto final, sem data")
            stops.append((parse_iata(t), None))
        else:
            if "@" not in t:
                raise ValueError(f"informe a data de saída em {t!r}, ex.: {t}@2026-12-20")
            code, d = t.split("@", 1)
            stops.append((parse_iata(code), parse_date(d)))
    if len(stops) < 3:
        raise ValueError("multi-destino precisa de pelo menos 2 trechos (3 pontos); para 1 trecho use `search`")
    return [Leg(stops[i][0], stops[i + 1][0], stops[i][1]) for i in range(len(stops) - 1)]


def multi_spec_from_args(args) -> SearchSpec:
    return SearchSpec.multi(parse_stops(args.stops), adults=args.adults, cabin=args.cabin, max_stops=args.max_stops,
                            currency=args.currency.upper())


# --- comandos ---------------------------------------------------------------

def _search_now(args, spec: SearchSpec) -> int:
    specs = expand_flex(spec, args.flex, args.compare_separate)
    if not specs:
        print("Todas as datas estão no passado.")
        return 1
    conn = db.connect(args.db)
    with make_scraper(args) as scraper:
        ok, failed = run_specs(conn, scraper, [(None, s, args.booking) for s in specs], args.delay, args.top,
                               detail=args.detail)
    return 0 if ok and not failed else 1


def cmd_search(args) -> int:
    return _search_now(args, spec_from_args(args))


def cmd_multi(args) -> int:
    return _search_now(args, multi_spec_from_args(args))


def cmd_run(args) -> int:
    conn = db.connect(args.db)
    rows = [r for r in db.list_watches(conn, only_active=True) if not args.ids or r["id"] in args.ids]
    if args.due:
        rows = [r for r in rows if db.is_due(conn, r)]
        if not rows:
            print("Nenhuma busca vencida agora.")
            return 0
    elif not rows:
        print("Nenhuma busca monitorada ativa. Cadastre com: flights watch add ORIGEM DESTINO DATA ...")
        return 1
    items = []
    for r in rows:
        for s in expand_flex(db.watch_to_spec(r), r["flex_days"], bool(r["compare_separate"])):
            items.append((r["id"], s, r["booking_top"]))
    if not items:
        print("Todas as datas monitoradas já passaram.")
        return 0 if args.due else 1
    print(f"{len(rows)} busca(s) monitorada(s) → {len(items)} consulta(s) ao Google Voos.")
    with make_scraper(args) as scraper:
        ok, failed = run_specs(conn, scraper, items, args.delay, args.top, args.max_offers, args.detail)
    print(f"\nConcluído: {ok} ok, {failed} com falha.")
    return 0 if not failed else 1


def cmd_sync(args) -> int:
    """Espelha um watches.json (editado pela página) na tabela de buscas monitoradas."""
    path = Path(args.file)
    items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    if isinstance(items, dict):
        items = items.get("watches", [])
    conn = db.connect(args.db)
    n, warnings = db.sync_watches(conn, items)
    if QUIET:
        print(f"{n} busca(s) sincronizada(s)" + (f", {len(warnings)} ignorada(s) por erro (veja no painel)" if warnings else "") + ".")
    else:
        for w in warnings:
            print(f"aviso: {w}")
        print(f"{n} busca(s) sincronizada(s) de {path}.")
    return 0


def cmd_publish(args) -> int:
    """Gera o JSON que o painel (GitHub Pages) lê."""
    conn = db.connect(args.db)
    data = db.build_dashboard(conn)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "dashboard.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    n_combos = sum(len(w["combos"]) for w in data["watches"])
    print(f"{out / 'dashboard.json'}: {len(data['watches'])} busca(s), {n_combos} combinação(ões) de datas.")
    return 0


def cmd_watch(args) -> int:
    conn = db.connect(args.db)
    if args.watch_cmd in ("add", "add-multi"):
        spec = spec_from_args(args) if args.watch_cmd == "add" else multi_spec_from_args(args)
        wid = db.add_watch(conn, spec, flex_days=args.flex, label=args.label, every_hours=args.every,
                           compare_separate=args.compare_separate, booking_top=args.booking)
        print(f"Monitoramento #{wid} criado: {spec.label()}"
              + (f" (±{args.flex} dias)" if args.flex else "")
              + (f", a cada {args.every}h" if args.every else ", só manual"))
    elif args.watch_cmd == "list":
        rows = db.list_watches(conn)
        if not rows:
            print("Nenhum monitoramento cadastrado.")
            return 0
        print_table(
            ["id", "rota", "ida", "volta/fim", "±dias", "adultos", "classe", "esc. máx", "cada", "extras", "ativo", "nome"],
            [[r["id"], db.row_route(r), r["depart_date"], db.watch_to_spec(r).end_date.isoformat() if db.watch_to_spec(r).end_date != db.watch_to_spec(r).depart_date else "-", r["flex_days"],
              r["adults"], r["cabin"], "-" if r["max_stops"] is None else r["max_stops"],
              f"{r['every_hours']}h" if r["every_hours"] else "manual",
              ",".join(x for x in ("separado" if r["compare_separate"] else "", f"compra×{r['booking_top']}" if r["booking_top"] else "") if x) or "-",
              "sim" if r["active"] else "não", r["label"] or ""] for r in rows])
    elif args.watch_cmd == "remove":
        print("Removido." if db.remove_watch(conn, args.id) else "Id não encontrado.")
    else:  # pause / resume
        done = db.set_watch_active(conn, args.id, args.watch_cmd == "resume")
        print("Ok." if done else "Id não encontrado.")
    return 0


def cmd_history(args) -> int:
    conn = db.connect(args.db)
    if args.watch is None and not (args.origin and args.destination):
        raise ValueError("informe ORIGEM DESTINO ou --watch ID")
    rows = db.history(conn, parse_iata(args.origin) if args.origin else None,
                      parse_iata(args.destination) if args.destination else None,
                      parse_date(args.depart).isoformat() if args.depart else None,
                      parse_date(args.ret).isoformat() if args.ret else None, args.watch)
    if not rows:
        print("Nenhuma coleta com sucesso para essa rota ainda.")
        return 1
    table, prev = [], {}
    for r in rows:
        key = (r["depart_date"], r["return_date"], r["adults"], r["cabin"])
        last = prev.get(key)
        var = "" if last is None else f"{r['min_price'] - last:+,.0f}".replace(",", ".")
        prev[key] = r["min_price"]
        table.append([r["scraped_at"].replace("T", " ").rstrip("Z"), r["depart_date"], r["return_date"] or "-",
                      money(r["min_price"], r["currency"]), money(r["min_nonstop"], r["currency"]),
                      var, r["price_level"] or "-", r["offers_count"]])
    print_table(["coletado (UTC)", "ida", "volta", "menor preço", "menor direto", "variação", "Google", "voos"], table)
    return 0


def cmd_offers(args) -> int:
    conn = db.connect(args.db)
    sid = args.search
    if sid is None:
        row = conn.execute("SELECT id FROM searches WHERE status = 'ok' ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            print("Nenhuma coleta no banco ainda.")
            return 1
        sid = row["id"]
    s = conn.execute("SELECT * FROM searches WHERE id = ?", (sid,)).fetchone()
    if not s:
        print("Busca não encontrada.")
        return 1
    print(f"Busca #{sid}: {s['origin']}→{s['destination']} {s['depart_date']}"
          f"{' ↩ ' + s['return_date'] if s['return_date'] else ''} · {s['scraped_at']} · {s['status']}")
    rows = db.search_offers(conn, sid)
    print_offers(rows, args.top)
    if args.details:
        print_offer_details(list(rows)[:args.top])
    return 0


def cmd_export(args) -> int:
    conn = db.connect(args.db)
    raw = list(db.export_rows(conn, args.search))
    if not raw:
        print("Nada para exportar.")
        return 1
    rows = []
    for r in raw:
        d = dict(r)
        raw_book = d.pop("booking_json", None)
        book = json.loads(raw_book) if raw_book else {}
        sellers = book.get("sellers") or []
        d.update(bags=" | ".join(book.get("bags") or []) or None,
                 seller=sellers[0]["seller"] if sellers else None,
                 seller_price=sellers[0]["price"] if sellers else None,
                 typical_low=book.get("typical_low"), typical_high=book.get("typical_high"),
                 price_verdict=book.get("verdict"), booking_url=book.get("url"))
        rows.append(d)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig: Excel abre com acentos certos
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} linhas → {out}")
    return 0


def cmd_log(args) -> int:
    conn = db.connect(args.db)
    rows = db.recent_searches(conn, args.limit)
    print_table(["#", "coletado (UTC)", "rota", "ida", "volta", "status", "voos", "menor preço"],
                [[r["id"], r["scraped_at"].replace("T", " ").rstrip("Z"), db.row_route(r) + (f" (trecho {r['leg_index'] + 1})" if r["leg_index"] is not None else ""),
                  r["depart_date"], r["return_date"] or "-", r["status"], r["offers_count"],
                  money(r["min_price"], r["currency"])] for r in rows])
    return 0


# --- parser de argumentos ---------------------------------------------------

def _add_trip_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("origin", help="aeroporto de origem (IATA), ex.: GRU")
    p.add_argument("destination", help="aeroporto de destino (IATA), ex.: LIS")
    p.add_argument("depart", help="data de ida: AAAA-MM-DD ou DD/MM/AAAA")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--return", dest="ret", metavar="DATA", help="data de volta (sem isso: só ida)")
    g.add_argument("--nights", type=int, metavar="N", help="ida e volta com N noites de estadia")
    _add_common_args(p)


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--adults", type=int, default=1, help="nº de adultos (default 1)")
    p.add_argument("--cabin", choices=list(CABINS), default="economy", help="classe (default economy)")
    p.add_argument("--max-stops", type=int, default=None, metavar="N", help="máximo de escalas (0 = só diretos)")
    p.add_argument("--currency", default="BRL", help="moeda (default BRL)")
    p.add_argument("--compare-separate", action="store_true",
                   help="também busca cada trecho como só ida e compara a soma com o preço do pacote")
    p.add_argument("--booking", type=int, default=0, metavar="N",
                   help="abre a tela de compra dos N voos mais baratos: bagagem, vendedores, faixa de preço (só ida)")


def _add_multi_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("stops", nargs="+", metavar="PONTO",
                   help="GRU@2026-12-20 LIS@2026-12-27 MAD@2027-01-03 GRU  (aeroporto@data de saída; o último é só o aeroporto final)")
    _add_common_args(p)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="flights", description="Coleta preços do Google Voos e guarda em SQLite local.")
    p.add_argument("--db", default=DEFAULT_DB, help=f"caminho do banco (default {DEFAULT_DB})")
    p.add_argument("--headed", action="store_true", help="mostra o navegador (útil para ver captcha/depurar)")
    p.add_argument("--debug", action="store_true", help="em caso de falha, salva HTML e screenshot em <pasta do db>/debug")
    p.add_argument("--quiet", action="store_true",
                   help="não imprime rotas, datas, preços nem mensagens de erro (logs públicos do Actions); ou env FLIGHTS_QUIET=1")
    p.add_argument("--chromium-path", help="caminho do Chromium/Chrome (ou env FLIGHTS_CHROMIUM_PATH)")
    p.add_argument("--country", help="país da consulta (gl), default BR; afeta as opções de compra (ou env FLIGHTS_COUNTRY)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def scrape_opts(sp):
        sp.add_argument("--delay", type=float, default=6.0, help="pausa média entre buscas, em s (default 6)")
        sp.add_argument("--top", type=int, default=8, help="quantos voos mostrar por busca (default 8)")
        sp.add_argument("--detail", type=int, default=30, metavar="N",
                        help="lê aeronave, assentos, comodidades etc. dos N voos mais baratos (default 30; 0 = não)")

    s = sub.add_parser("search", help="busca agora e grava no banco")
    _add_trip_args(s)
    s.add_argument("--flex", type=int, default=0, metavar="N", help="também busca ±N dias em torno da ida")
    scrape_opts(s)
    s.set_defaults(func=cmd_search)

    mu = sub.add_parser("multi", help="busca multi-destino (vários trechos) agora e grava no banco")
    _add_multi_args(mu)
    mu.add_argument("--flex", type=int, default=0, metavar="N", help="também busca ±N dias (todas as datas andam juntas)")
    scrape_opts(mu)
    mu.set_defaults(func=cmd_multi)

    r = sub.add_parser("run", help="roda todos os monitoramentos ativos")
    r.add_argument("ids", nargs="*", type=int, help="ids específicos (default: todos)")
    r.add_argument("--due", action="store_true", help="só as buscas cuja frequência (--every) já venceu")
    r.add_argument("--max-offers", type=int, metavar="N", help="guarda só os N voos mais baratos de cada coleta")
    scrape_opts(r)
    r.set_defaults(func=cmd_run)

    sy = sub.add_parser("sync-watches", help="espelha um watches.json no banco (usado pelo GitHub Actions)")
    sy.add_argument("file", help="ex.: data/watches.json")
    sy.set_defaults(func=cmd_sync)

    pb = sub.add_parser("publish", help="gera dashboard.json para a página (GitHub Pages)")
    pb.add_argument("--out", default="data", help="pasta de saída (default data)")
    pb.set_defaults(func=cmd_publish)

    w = sub.add_parser("watch", help="gerencia a lista de rotas monitoradas")
    ws = w.add_subparsers(dest="watch_cmd", required=True)
    wa = ws.add_parser("add", help="cadastra uma rota")
    _add_trip_args(wa)
    wa.add_argument("--flex", type=int, default=0, metavar="N", help="busca ±N dias em torno da ida a cada run")
    wa.add_argument("--label", help="nome livre, ex.: 'Lisboa natal'")
    wa.add_argument("--every", type=int, metavar="HORAS", help="coletar a cada N horas no `run --due` (sem isso: só manual)")
    wm = ws.add_parser("add-multi", help="cadastra uma viagem multi-destino")
    _add_multi_args(wm)
    wm.add_argument("--flex", type=int, default=0, metavar="N")
    wm.add_argument("--label")
    wm.add_argument("--every", type=int, metavar="HORAS")
    ws.add_parser("list", help="lista as rotas")
    for name in ("remove", "pause", "resume"):
        x = ws.add_parser(name)
        x.add_argument("id", type=int)
    w.set_defaults(func=cmd_watch)

    h = sub.add_parser("history", help="evolução do menor preço de uma rota")
    h.add_argument("origin", nargs="?")
    h.add_argument("destination", nargs="?")
    h.add_argument("--watch", type=int, help="histórico de uma busca monitorada (inclui multi-destino)")
    h.add_argument("--depart", help="filtra por data de ida")
    h.add_argument("--return", dest="ret", help="filtra por data de volta")
    h.set_defaults(func=cmd_history)

    o = sub.add_parser("offers", help="mostra os voos de uma coleta (default: a última)")
    o.add_argument("--search", type=int, help="id da coleta")
    o.add_argument("--top", type=int, default=20)
    o.add_argument("--details", action="store_true", help="mostra aeronave, assentos, conexões, bagagem e vendedores")
    o.set_defaults(func=cmd_offers)

    e = sub.add_parser("export", help="exporta voos para CSV")
    e.add_argument("--out", default="data/export.csv")
    e.add_argument("--search", type=int, help="só uma coleta (default: tudo)")
    e.set_defaults(func=cmd_export)

    lg = sub.add_parser("log", help="últimas coletas (inclui as que falharam)")
    lg.add_argument("--limit", type=int, default=20)
    lg.set_defaults(func=cmd_log)
    return p


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    global QUIET
    QUIET = bool(args.quiet or os.environ.get("FLIGHTS_QUIET") == "1")
    try:
        return args.func(args)
    except ValueError as e:
        parser.error(str(e))
    except ScrapeError as e:
        print("Erro ao coletar." if QUIET else f"Erro: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrompido.")
        return 130
