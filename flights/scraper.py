from __future__ import annotations

import os
import random
import re
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from .models import ScrapeResult, SearchSpec
from .parser import parse_booking, parse_cards
from .tfs import build_url

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Um card de voo = qualquer <li> com um [role=link] cujo aria-label descreve o voo.
CARD_SELECTOR = 'li [role="link"][aria-label*=" arrives at "]'

# Roda no navegador: devolve "ok" | "error" | "empty" | "blocked" | null (ainda carregando).
_STATE_JS = """() => {
  if (location.pathname.startsWith('/sorry')) return 'blocked';
  if (document.querySelector('%s')) return 'ok';
  const t = document.body ? document.body.innerText : '';
  if (/something went wrong/i.test(t)) return 'error';
  if (/no (flights|results|options)|couldn.t find any/i.test(t)) return 'empty';
  return null;
}""" % CARD_SELECTOR

# Expande (clicando na seta) os `n` voos mais baratos, para o detalhe aparecer no DOM.
_EXPAND_JS = """(n) => {
  const price = (a) => { const m = /^From\\s+([\\d.,]+)/.exec(a || ''); return m ? parseFloat(m[1].replace(/,/g, '')) : Infinity; };
  const cards = [];
  for (const li of document.querySelectorAll('li')) {
    const link = li.querySelector('[role="link"][aria-label*=" arrives at "]');
    if (link) cards.push({li, aria: link.getAttribute('aria-label')});
  }
  const best = [...new Set(cards.map((c) => c.aria))].sort((a, b) => price(a) - price(b)).slice(0, n);
  let clicked = 0;
  for (const c of cards) {
    if (!best.includes(c.aria)) continue;
    const b = c.li.querySelector('button[aria-expanded="false"]');
    if (b) { b.click(); clicked++; }
  }
  return clicked;
}"""

# Extrai os dados crus de cada card, na ordem do documento, anotando a seção (h3).
_EXTRACT_JS = """() => {
  const out = []; let section = null; const seen = new Set();
  for (const el of document.querySelectorAll('h3, li')) {
    if (el.tagName === 'H3') { section = el.innerText.trim(); continue; }
    const link = el.querySelector('[role="link"][aria-label*=" arrives at "]');
    if (!link || seen.has(link)) continue;
    seen.add(link);
    const impact = el.querySelector('[data-travelimpactmodelwebsiteurl]');
    let itinerary = null, co2_g = null, co2_diff_pct = null;
    if (impact) {
      try { itinerary = new URL(impact.dataset.travelimpactmodelwebsiteurl).searchParams.get('itinerary'); } catch (e) {}
      if (impact.dataset.co2currentflight) co2_g = parseInt(impact.dataset.co2currentflight, 10);
      if (impact.dataset.percentagediff !== undefined && impact.dataset.percentagediff !== '') co2_diff_pct = parseInt(impact.dataset.percentagediff, 10);
    }
    const open = el.querySelector('button[aria-expanded="true"]');
    out.push({aria: link.getAttribute('aria-label'), itinerary, section, co2_g, co2_diff_pct, detail: open ? el.innerText : null});
  }
  const m = (document.body.innerText || '').match(/Prices are currently\\s+(low|typical|high)/i);
  return {cards: out, price_level: m ? m[1].toLowerCase() : null};
}"""

# Clica no voo (pelo aria-label) para abrir a tela de compra.
_CLICK_CARD_JS = """(aria) => {
  const link = [...document.querySelectorAll('[role="link"][aria-label*=" arrives at "]')].find((a) => a.getAttribute('aria-label') === aria);
  if (!link) return false; link.click(); return true;
}"""

# Lê a tela de compra: texto todo + uma linha por vendedor ("Book with X").
_BOOKING_JS = """() => {
  const own = (e) => [...e.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('').trim();
  const hosts = [...document.querySelectorAll('*')].filter((e) => /^Book with\\b/.test(own(e)));
  const rows = hosts.map((e) => {
    let row = e;
    for (let i = 0; i < 8 && row.parentElement; i++) {
      row = row.parentElement;
      if ([...row.querySelectorAll('button,a,[role="button"]')].some((b) => /^(Continue|View options)$/.test((b.innerText || '').trim()))) break;
    }
    const full = (e.textContent || '').trim(), seller = own(e).replace(/^Book with\\s*/, '').trim();
    return {seller: seller || full.replace(/^Book with\\s*/, ''), tag: seller ? full.replace(own(e), '').trim() : '', text: row.innerText};
  });
  return {text: document.body.innerText, rows, url: location.href};
}"""


class ScrapeError(Exception):
    pass


class BlockedError(ScrapeError):
    """O Google pediu captcha / bloqueou o acesso. Não adianta insistir agora."""


class FlightScraper:
    def __init__(
        self,
        headless: bool = True,
        chromium_path: Optional[str] = None,
        debug_dir: Optional[Path] = None,
        timeout_s: int = 40,
        max_expand: int = 3,
        country: Optional[str] = None,
    ):
        # gl=BR por padrão: sem país, o Google pode esconder as opções de compra.
        self.country = country or os.environ.get("FLIGHTS_COUNTRY", "BR")
        self.headless = headless
        self.chromium_path = chromium_path or os.environ.get("FLIGHTS_CHROMIUM_PATH")
        self.debug_dir = Path(debug_dir) if debug_dir else None
        self.timeout_s = timeout_s
        self.max_expand = max_expand
        self._pw = self._browser = self._context = None

    def __enter__(self) -> "FlightScraper":
        self._pw = sync_playwright().start()
        launch = {"headless": self.headless, "args": ["--no-sandbox", "--disable-blink-features=AutomationControlled"]}
        if self.chromium_path:
            launch["executable_path"] = self.chromium_path
        try:
            self._browser = self._pw.chromium.launch(**launch)
        except PlaywrightError as e:
            self._pw.stop()
            raise ScrapeError(
                "Não consegui abrir o Chromium. Rode `playwright install chromium` "
                f"(ou use --chromium-path / FLIGHTS_CHROMIUM_PATH).\n{e}"
            ) from e
        self._context = self._browser.new_context(
            locale="en-US", user_agent=USER_AGENT, viewport={"width": 1366, "height": 900}
        )
        return self

    def __exit__(self, *exc):
        for obj in (self._context, self._browser):
            try:
                obj and obj.close()
            except PlaywrightError:
                pass
        if self._pw:
            self._pw.stop()

    # ------------------------------------------------------------------
    def search(self, spec: SearchSpec, attempts: int = 2, detail_offers: int = 30, booking_top: int = 0) -> ScrapeResult:
        """`detail_offers`: quantos voos (os mais baratos) expandir para ler aeronave, assentos etc.
        `booking_top`: em buscas só de ida, quantos voos abrir na tela de compra (bagagem, vendedores)."""
        last: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                return self._search_once(spec, detail_offers, booking_top)
            except BlockedError:
                raise
            except ScrapeError as e:
                last = e
                if attempt < attempts:
                    time.sleep(3 + random.random() * 3)
        raise last  # type: ignore[misc]

    def _search_once(self, spec: SearchSpec, detail_offers: int, booking_top: int) -> ScrapeResult:
        url = build_url(spec, self.country)
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_s * 1000)
            self._handle_consent(page)
            state = self._wait_state(page)
            if state == "blocked":
                raise BlockedError("O Google bloqueou o acesso (captcha). Espere um tempo e tente de novo, ou use --headed.")
            if state == "error":
                raise ScrapeError("O Google Voos respondeu 'something went wrong'.")
            if state is None:
                raise ScrapeError(f"Tempo esgotado ({self.timeout_s}s) esperando os resultados.")
            if state == "empty":
                return ScrapeResult(offers=[], url=url)

            page.wait_for_timeout(1500)  # deixa a lista terminar de renderizar
            self._expand(page)
            if detail_offers:
                page.evaluate(_EXPAND_JS, detail_offers)
                page.wait_for_timeout(1500)
            data = page.evaluate(_EXTRACT_JS)
            warnings: list = []
            if booking_top:
                if spec.trip_type == "one_way":
                    self._booking_phase(page, data["cards"], booking_top, warnings)
                else:
                    warnings.append("Bagagem/vendedores só existem para busca só de ida (no pacote o Google exige "
                                    "escolher a volta antes); use a comparação por trecho.")
            offers, no_price, unreadable = parse_cards(data["cards"], spec)
            if data["cards"] and not offers:
                raise ScrapeError(
                    f"Achei {len(data['cards'])} cards mas não consegui ler nenhum: o layout do Google "
                    "provavelmente mudou. Rode com --debug e veja data/debug/."
                )
            return ScrapeResult(
                offers=offers, url=url, price_level=data["price_level"],
                cards_no_price=no_price, cards_unreadable=unreadable, warnings=warnings,
            )
        except (ScrapeError, PlaywrightError) as e:
            self._dump_debug(page, spec)
            if isinstance(e, PlaywrightTimeout):
                raise ScrapeError(f"Tempo esgotado carregando a página: {e}") from e
            if isinstance(e, ScrapeError):
                raise
            raise ScrapeError(f"Erro do navegador: {e}") from e
        finally:
            page.close()

    # ------------------------------------------------------------------
    def _booking_phase(self, page, cards: list, top: int, warnings: list) -> None:
        """Abre a tela de compra dos `top` voos mais baratos e guarda o resultado em card["booking"]."""
        price = lambda a: float((re.match(r"From\s+([\d.,]+)", a or "") or [0, "inf"])[1].replace(",", ""))
        ordered = sorted({c["aria"]: c for c in cards}.values(), key=lambda c: price(c["aria"]))[:top]
        for card in ordered:
            try:
                booking = self._open_booking(page, card["aria"])
            except BlockedError:
                warnings.append("O Google bloqueou durante a leitura da tela de compra; ficou só o que já foi coletado.")
                return
            except (ScrapeError, PlaywrightError) as e:
                warnings.append(f"Não consegui abrir a tela de compra de um voo: {str(e).splitlines()[0][:120]}")
                continue
            for c in cards:  # o mesmo voo aparece repetido nas duas listas
                if c["aria"] == card["aria"]:
                    c["booking"] = booking
            if booking.get("unsupported_location"):
                warnings.append("O Google diz que a compra não é suportada nessa localização (veja --country).")
                return

    def _open_booking(self, page, aria: str) -> dict:
        if not page.evaluate(_CLICK_CARD_JS, aria):
            self._expand(page)  # o voo pode estar atrás de "View more flights"
            if not page.evaluate(_CLICK_CARD_JS, aria):
                raise ScrapeError("voo não encontrado na lista")
        try:
            page.wait_for_url("**/travel/flights/booking**", timeout=15000)
            page.wait_for_function("() => /Booking options|Baggage conditions|Price insights/.test(document.body.innerText)",
                                   timeout=15000)
        except PlaywrightTimeout:
            if "/sorry" in page.url:
                raise BlockedError("captcha")
            raise ScrapeError("a tela de compra não carregou")
        try:  # os vendedores chegam depois da bagagem: espera por eles (ou por um aviso explícito)
            page.wait_for_function(
                "() => /Book with|isn.t supported|No booking options|not available/i.test(document.body.innerText)", timeout=10000)
        except PlaywrightTimeout:
            pass
        page.wait_for_timeout(1000)
        raw = page.evaluate(_BOOKING_JS)
        self.last_booking_raw = raw  # para depuração
        booking = parse_booking(raw["text"], raw["rows"], raw["url"])
        page.go_back(wait_until="domcontentloaded")
        if self._wait_state(page) != "ok":
            raise ScrapeError("a lista de voos não voltou depois da tela de compra")
        page.wait_for_timeout(800)
        return booking

    # ------------------------------------------------------------------
    def _wait_state(self, page) -> Optional[str]:
        deadline = time.time() + self.timeout_s
        while time.time() < deadline:
            state = page.evaluate(_STATE_JS)
            if state:
                return state
            page.wait_for_timeout(500)
        return None

    def _handle_consent(self, page) -> None:
        """Tela de consentimento de cookies (comum na Europa)."""
        if "consent.google" not in page.url:
            return
        for label in ("Reject all", "Accept all"):
            btn = page.locator(f'button:has-text("{label}")').first
            if btn.count():
                btn.click()
                page.wait_for_load_state("domcontentloaded")
                return

    def _expand(self, page) -> None:
        """Clica em 'View more flights' até a lista parar de crescer."""
        for _ in range(self.max_expand):
            btn = page.locator('button[aria-label*="more flights"]:visible').first
            if not btn.count():
                return
            before = page.locator(CARD_SELECTOR).count()
            try:
                btn.click(timeout=3000)
            except PlaywrightError:
                return
            try:
                page.wait_for_function(
                    "([sel, n]) => document.querySelectorAll(sel).length > n", arg=[CARD_SELECTOR, before], timeout=6000
                )
            except PlaywrightTimeout:
                return

    def _dump_debug(self, page, spec: SearchSpec) -> None:
        if not self.debug_dir:
            return
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            stem = f"{time.strftime('%Y%m%d-%H%M%S')}_{spec.origin}-{spec.destination}_{spec.depart_date}"
            (self.debug_dir / f"{stem}.html").write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(self.debug_dir / f"{stem}.png"))
        except PlaywrightError:
            pass
