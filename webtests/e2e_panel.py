"""Teste de navegador do painel (Playwright) contra o servidor local com GitHub FALSO.

    pip install playwright && playwright install chromium
    python webtests/e2e_panel.py            # (FLIGHTS_CHROMIUM_PATH aponta outro Chromium, se precisar)
"""
import os, re, subprocess, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PW = "senha-de-teste-123"


def start(omit_token=False):
    env = {**os.environ, **({"OMIT_GH_TOKEN": "1"} if omit_token else {})}
    args = ["node", str(ROOT / "webtests/run-server.mjs")] + ([] if omit_token else [str(ROOT / "webtests/seed/dashboard.json"), str(ROOT / "webtests/seed/watches.json")])
    p = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE, text=True, env=env)
    return p, p.stdout.readline().strip()


def launch(pw):
    path = os.environ.get("FLIGHTS_CHROMIUM_PATH")
    return pw.chromium.launch(**({"executable_path": path} if path else {}), args=["--no-sandbox"])


procs = []
try:
    p, base = start(); procs.append(p)
    with sync_playwright() as pw:
        b = launch(pw)
        ctx = b.new_context(viewport={"width": 1200, "height": 900})
        pg = ctx.new_page(); errs, reqs = [], []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append("CSP:" + m.text) if "Content Security" in m.text else None)
        pg.on("request", lambda r: reqs.append(r.url))
        pg.add_init_script("localStorage.setItem('flights.token','github_pat_VELHO')")
        pg.goto(base); pg.wait_for_selector("#dlg-login[open]")
        assert pg.locator(".tcard").count() == 0 and pg.is_disabled("#btn-new")
        assert pg.evaluate("localStorage.getItem('flights.token')") is None
        print("1. sem sessão: só o login; dados ocultos; token antigo apagado")

        pg.fill("#form-login [name=password]", "errada-errada-123"); pg.click("#login-go")
        pg.locator("#login-err").filter(has_text="incorreta").wait_for()
        pg.fill("#form-login [name=password]", PW); pg.click("#login-go")
        pg.wait_for_selector(".tcard"); pg.wait_for_selector("#dlg-login:not([open])", state="attached")
        assert pg.locator(".tcard").count() == 4
        kpis = pg.inner_text(".kpis"); assert "Viagens monitoradas" in kpis and "Maior queda" in kpis and "Próxima coleta" in kpis
        ck = [c for c in ctx.cookies() if c["name"] in ("fs", "__Host-fs")]
        assert len(ck) == 1 and ck[0]["httpOnly"] and ck[0]["sameSite"] == "Strict" and "fs=" not in pg.evaluate("document.cookie")
        print("2. login ok: 4 cartões e indicadores; cookie HttpOnly/Strict")

        # ---- cartão -> detalhe ----
        card = pg.locator('.tcard[data-open-trip="3"]')
        txt = card.inner_text(); assert "GRU" in txt and "GIG" in txt and "R$" in txt and "Rio de Janeiro" in txt
        card.click(); pg.wait_for_selector(".trip-head")
        assert pg.evaluate("location.hash") == "#/viagem/3/resumo" and pg.locator(".tab").count() >= 4
        assert "Mais barata direta" in pg.inner_text("body") and "Viagem mais rápida" in pg.inner_text("body")
        print("3. cartão abre o detalhe (#/viagem/3/resumo) com abas e estatísticas")

        pg.click('.tab[data-tab="voos"]'); pg.wait_for_selector(".fl")
        n_all = pg.locator(".fl").count(); assert n_all >= 3
        first_price = pg.locator(".fl .price").first.inner_text()
        pg.select_option('[data-flt="sort"]', "duration_min"); pg.wait_for_selector(".fl")
        pg.check('[data-flt="direct"]'); pg.wait_for_selector(".fl")
        assert pg.locator(".fl").count() <= n_all
        assert all("Direto" in t for t in pg.locator(".fl-mid").all_inner_texts())
        det = pg.locator(".fl details").first; det.locator("summary").click()
        assert "Compra" in det.inner_text() or "Bagagem" in det.inner_text() or det.inner_text().strip()
        print("4. voos: ordenar, filtrar só diretos e expandir detalhes funcionam")
        pg.evaluate("location.hash = '#/viagem/3/historico'"); pg.wait_for_selector("svg.chart"); assert pg.locator("table tbody tr").count() >= 2
        pg.locator("svg.chart").hover(position={"x": 300, "y": 90}); pg.wait_for_selector(".tip:not([hidden])")
        print("5. histórico: gráfico com tooltip e tabela de coletas")

        pg.evaluate("location.hash = '#/viagem/2/comparar'"); pg.wait_for_selector(".verdict")
        assert re.search(r"mais barato", pg.inner_text(".verdict")) and pg.locator("tfoot").count() == 1
        print("6. comparação pacote × separado:", pg.inner_text(".verdict"))
        pg.evaluate("location.hash = '#/viagem/4/datas'"); pg.wait_for_selector(".dtile")
        # 3 datas a R$ 3.579: duas com o mesmo voo de 7h40 (empate total: as duas são "Melhor") e uma de 8h40 ("Mesmo preço")
        assert pg.locator(".dtile.min").count() == 3
        tiles = pg.locator(".dtile.min .dx").all_inner_texts()
        assert sorted(t.split(" · ")[0] for t in tiles) == ["Melhor", "Melhor", "Mesmo preço"], tiles
        assert [t for t in tiles if t.startswith("Melhor")] == ["Melhor · 7h40"] * 2 and any("8h40" in t for t in tiles), tiles
        txt = pg.inner_text(".dates").replace("\xa0", " ") + pg.inner_text("#view")
        assert "As melhores datas são" in txt and "empate também no tempo" in txt
        pg.locator(".dtile").nth(1).click(); pg.wait_for_selector(".fl"); assert pg.evaluate("location.hash").endswith("/voos")
        print("7. calendário de datas: empate de preço desempata pelo tempo; empate total marca todas como melhores; clicar abre os voos")

        pg.evaluate("location.hash = '#/'"); pg.wait_for_selector(".tcard")
        c4 = pg.inner_text('.tcard[data-open-trip="4"]'); assert "Melhores datas:" in c4, c4
        pg.evaluate("location.hash = '#/viagem/4/resumo'"); pg.wait_for_selector(".best")
        assert pg.locator(".best .chip", has_text="Empata com").count() <= 1

        # ---- ações ----
        pg.evaluate("location.hash = '#/'"); pg.wait_for_selector(".tcard")
        # ---- autocompletar de aeroportos ----
        pg.click("#btn-new"); f = "#form-watch "
        o = pg.locator("#f-origin"); o.click(); o.press_sequentially("recif", delay=25)
        pg.wait_for_selector("#ac-o:not([hidden]) li[data-i]")
        first = pg.locator("#ac-o li").first.inner_text(); assert "REC" in first and "Recife" in first, first
        o.press("Enter")
        assert o.input_value() == "REC" and "Recife" in pg.locator("#simple-fields .ac-hint").first.inner_text() and pg.locator("#ac-o").is_hidden()
        d = pg.locator("#f-dest"); d.click(); d.press_sequentially("cusc", delay=25); pg.wait_for_selector("#ac-d li[data-i]")
        d.press("ArrowDown"); d.press("ArrowUp"); d.press("Enter")
        assert d.input_value() == "CUZ" and "Cusco" in pg.locator("#simple-fields .ac-hint").nth(1).inner_text() and "Peru" in pg.locator("#simple-fields .ac-hint").nth(1).inner_text()
        print("8. autocompletar: 'recif' -> REC · Recife; 'cusc' -> CUZ · Cusco · Peru (setas e Enter)")
        o.fill(""); o.press_sequentially("sao pa", delay=25); pg.wait_for_selector("#ac-o li[data-i]")
        codes = [li.locator("b").inner_text() for li in pg.locator("#ac-o li[data-i]").all()]
        assert codes[0] == "SAO" and "GRU" in codes and "CGH" in codes, codes
        o.press("Escape"); assert pg.locator("#ac-o").is_hidden() and pg.is_visible("#dlg-watch"), "Esc fecha só a lista"
        pg.locator("#ac-o").evaluate("e => e.hidden"); o.press_sequentially("x"); o.fill(""); o.press_sequentially("zzzqq", delay=20)
        assert "Nenhum aeroporto" in pg.locator("#ac-o").inner_text()
        o.fill(""); o.press_sequentially("congonhas", delay=20); pg.wait_for_selector("#ac-o li[data-i]"); pg.locator("#ac-o li[data-i]").first.click()
        assert o.input_value() == "CGH"
        print("9. vários aeroportos por cidade (SAO, GRU, CGH), busca pelo nome do aeroporto, 'nenhum resultado' e Esc")
        pg.click("#watch-cancel")

        # ---- digitar só o nome (sem escolher) também funciona ao salvar ----
        pg.click("#btn-new"); pg.check(f + "[name=trip][value=oneway]")
        pg.fill("#f-origin", "lisboa"); pg.fill("#f-dest", "Brasília"); pg.fill(f + "[name=depart_date]", "2027-05-10"); pg.uncheck(f + "[name=run_now]")
        pg.click("#watch-save"); pg.wait_for_selector('.tcard[data-open-trip="5"]')
        c5 = pg.inner_text('.tcard[data-open-trip="5"]'); assert "LIS" in c5 and "BSB" in c5 and "Lisboa" in c5 and "Brasília" in c5, c5
        print("10. texto digitado sem escolher ('lisboa' -> LIS, 'Brasília' -> BSB) é entendido ao salvar")
        pg.click('.tcard[data-open-trip="5"]'); pg.wait_for_selector(".trip-head"); pg.click("[data-act=edit]")
        assert pg.input_value("#f-origin") == "LIS" and "Lisboa" in pg.locator("#simple-fields .ac-hint").first.inner_text(), "editar mostra a cidade"
        pg.click("#watch-cancel"); pg.once("dialog", lambda d: d.accept()); pg.click("[data-act=remove]"); pg.wait_for_selector(".tcard")

        # ---- multi-destino: escolhendo na lista e encadeando trechos ----
        pg.click("#btn-new"); pg.check(f + "[name=trip][value=multi]"); rows = pg.locator("#legs .leg-row")
        rows.nth(0).locator(".leg-o").press_sequentially("guarulhos", delay=20); pg.wait_for_selector("#legs .ac-list:not([hidden]) li[data-i]"); rows.nth(0).locator(".leg-o").press("Enter")
        rows.nth(0).locator(".leg-d").press_sequentially("roma", delay=20); pg.wait_for_selector("#legs .ac-list:not([hidden]) li[data-i]"); rows.nth(0).locator(".leg-d").press("Enter")
        assert rows.nth(0).locator(".leg-o").input_value() == "GRU" and rows.nth(0).locator(".leg-d").input_value() == "ROM"
        assert rows.nth(1).locator(".leg-o").input_value() == "ROM", "origem do trecho 2 encadeia"
        rows.nth(0).locator(".leg-date").fill("2027-03-10")
        rows.nth(1).locator(".leg-d").press_sequentially("veneza", delay=20); pg.wait_for_selector("#legs .ac-list:not([hidden]) li[data-i]"); rows.nth(1).locator(".leg-d").press("Enter")
        rows.nth(1).locator(".leg-date").fill("2027-03-15")
        pg.fill(f + "[name=label]", "Itália"); pg.uncheck(f + "[name=run_now]"); pg.click("#watch-save")
        pg.wait_for_selector('.tcard[data-open-trip="5"]')
        assert "Roma" in pg.inner_text('.tcard[data-open-trip="5"]') and "Veneza" in pg.inner_text('.tcard[data-open-trip="5"]')
        print("11. multi-destino: trechos escolhidos na lista e encadeados; cartão mostra as cidades por extenso")
        pg.click('.tcard[data-open-trip="5"] [data-act=run]'); pg.locator(".toast").filter(has_text="Coleta disparada").wait_for()
        assert pg.locator(".toast a").first.get_attribute("href") == "https://github.com/Dusax/flight/actions/workflows/scrape.yml"
        pg.locator('.tcard[data-open-trip="5"]').click(); pg.wait_for_selector(".trip-head")
        assert "Sem resultados" in pg.inner_text("body")
        pg.click("[data-act=toggle]"); pg.locator(".chip", has_text="Pausada").first.wait_for()
        pg.on("dialog", lambda d: d.accept()); pg.click("[data-act=remove]"); pg.wait_for_selector(".tcard")
        assert pg.locator('.tcard[data-open-trip="5"]').count() == 0 and pg.evaluate("location.hash") == "#/"
        print("12. disparar, pausar e remover funcionam")

        # ---- período de datas (x até y, inclusive) ----
        pg.click("#btn-new")
        assert pg.is_hidden("#range-row") and pg.is_visible("#flex-wrap")
        pg.fill("#f-origin", "GRU"); pg.fill("#f-dest", "LIS")
        pg.fill(f + "[name=depart_date]", "2027-06-01"); pg.fill(f + "[name=return_date]", "2027-06-10")
        pg.check(f + "[name=use_range]")
        assert pg.is_visible("#range-row") and pg.is_hidden("#flex-wrap"), "período esconde o ± dias"
        assert pg.input_value(f + "[name=depart_to]") == "2027-06-07", "sugere o fim do período"
        pg.fill(f + "[name=depart_to]", "2027-06-03"); pg.fill(f + "[name=return_to]", "2027-06-12")
        pg.locator("#plan-info").filter(has_text="9 combinações").wait_for()
        pg.fill(f + "[name=return_to]", "2027-09-30")  # volta de 112 dias: passa do limite de dias
        pg.locator("#plan-info.bad").wait_for(); pg.click("#watch-save")
        assert "máximo" in pg.inner_text("#watch-err") and pg.is_visible("#dlg-watch"), "recusa período grande demais"
        pg.fill(f + "[name=depart_to]", "2027-06-30"); pg.fill(f + "[name=return_to]", "2027-07-20")
        pg.locator("#plan-info:not(.bad)").filter(has_text="cada coleta consulta até 40").wait_for()   # grande, mas não é recusado
        assert pg.is_visible("#budget-wrap") and pg.is_hidden("#flex-wrap")
        pg.select_option(f + "[name=budget]", "20"); pg.locator("#plan-info").filter(has_text="consulta até 20").wait_for(); pg.select_option(f + "[name=budget]", "40")
        pg.fill(f + "[name=depart_to]", "2027-06-03"); pg.fill(f + "[name=return_to]", "2027-06-12")
        pg.locator("#plan-info:not(.bad)").filter(has_text="9 combinações").wait_for()
        pg.fill(f + "[name=label]", "Período"); pg.uncheck(f + "[name=run_now]"); pg.click("#watch-save")
        pg.wait_for_selector('.tcard[data-open-trip="5"]')
        saved = pg.evaluate("fetch('/api/watches').then(r => r.json())")
        w5 = next(w for w in saved["list"] if w["id"] == 5)
        assert w5["depart_to"] == "2027-06-03" and w5["return_to"] == "2027-06-12" and w5["flex_days"] == 0, w5
        assert "Período de datas" in pg.inner_text('.tcard[data-open-trip="5"]')
        pg.click('.tcard[data-open-trip="5"]'); pg.wait_for_selector(".trip-head"); pg.click('.tab[data-tab="busca"]')
        body = pg.inner_text("body"); assert re.search(r"Ida 1 a 3 de jun", body) and re.search(r"Volta 10 a 12 de jun", body), body[:900]
        pg.click("[data-act=edit]")
        assert pg.is_checked(f + "[name=use_range]") and pg.input_value(f + "[name=return_to]") == "2027-06-12", "editar traz o período de volta"
        pg.uncheck(f + "[name=use_range]"); assert pg.is_visible("#flex-wrap") and pg.is_hidden("#range-row")
        pg.click("#watch-cancel"); pg.click('[data-nav="/"]'); pg.wait_for_selector(".tcard")
        # multi-destino: cada trecho com o seu período
        pg.click("#btn-new"); pg.check(f + "[name=trip][value=multi]"); pg.check(f + "[name=use_range]")
        rows = pg.locator("#legs .leg-row"); assert rows.nth(0).locator(".leg-to").is_visible()
        for i, (o, d, a, z) in enumerate([("GRU", "ROM", "2027-03-10", "2027-03-12"), ("ROM", "VCE", "2027-03-15", "2027-03-16")]):
            rows.nth(i).locator(".leg-o").fill(o); rows.nth(i).locator(".leg-d").fill(d)
            rows.nth(i).locator(".leg-date").fill(a); rows.nth(i).locator(".leg-to").fill(z)
        pg.locator("#plan-info").filter(has_text="6 combinações").wait_for()  # 3 datas do trecho 1 x 2 do trecho 2
        pg.uncheck(f + "[name=run_now]"); pg.click("#watch-save")
        try: pg.wait_for_selector('.tcard[data-open-trip="6"]', timeout=8000)
        except Exception: raise AssertionError("não salvou: " + pg.inner_text("#watch-err") + " | inválidos: " + str(pg.evaluate("[...document.querySelectorAll('#form-watch :invalid')].map(e => (e.name || e.className) + ':' + e.validationMessage + ':' + e.value)")) + " | " + pg.inner_text("#plan-info") + " | cards: " + str(pg.evaluate("[...document.querySelectorAll('.tcard')].map(e => e.dataset.openTrip)")) + " dlg:" + str(pg.is_visible("#dlg-watch")) + " toasts:" + pg.inner_text("#toasts"))
        saved = pg.evaluate("fetch('/api/watches').then(r => r.json())")
        w6 = next(w for w in saved["list"] if w["id"] == 6)
        assert [l["date_to"] for l in w6["legs"]] == ["2027-03-12", "2027-03-16"], w6
        for i in (6, 5):
            pg.click(f'.tcard[data-open-trip="{i}"]'); pg.wait_for_selector(".trip-head")
            pg.click("[data-act=remove]"); pg.wait_for_selector(".tcard")  # o diálogo de confirmação já é aceito pelo handler do passo 12
        print("12b. período de datas: ida e volta (9 combinações), limites, multi-destino por trecho, edição")

        # ---- voos favoritos ----
        assert pg.is_enabled("#btn-favs") and pg.is_hidden("#fav-count")
        pg.goto(base + "/#/viagem/3/voos"); pg.wait_for_selector(".fl .star")
        star = pg.locator(".fl .star").first
        assert star.get_attribute("aria-pressed") == "false"
        star.click(); pg.locator(".toast").filter(has_text="Salvo nos favoritos").wait_for()
        assert pg.locator(".fl .star").first.get_attribute("aria-pressed") == "true" and pg.inner_text("#fav-count") == "1"
        favs = pg.evaluate("fetch('/api/favorites').then(r => r.json())")["list"]
        assert len(favs) == 1 and favs[0]["watch_id"] == 3 and favs[0]["trip"]["origin"] == "GRU" and favs[0]["offer"]["flight_numbers"] and favs[0]["price"] > 0, favs
        # multi-destino: o favorito guarda a data de cada trecho e os trechos
        pg.goto(base + "/#/viagem/1/voos"); pg.wait_for_selector(".fl .star"); pg.locator(".fl .star").first.click()
        pg.locator("#fav-count").filter(has_text="2").wait_for()
        favs = pg.evaluate("fetch('/api/favorites').then(r => r.json())")["list"]
        multi = next(f for f in favs if f["watch_id"] == 1)
        assert len(multi["dates"]) == 3 and len(multi["trip"]["legs"]) == 3, multi
        pg.click("#btn-favs"); pg.wait_for_selector(".fav")
        assert pg.evaluate("location.hash") == "#/favoritos" and pg.locator(".fav").count() == 2
        # como sair da aba: "Todas as viagens", o próprio botão Favoritos (alterna) e o logo
        pg.click(".crumb"); pg.wait_for_selector(".tcard"); assert pg.evaluate("location.hash") == "#/"
        pg.click("#btn-favs"); pg.wait_for_selector(".fav"); pg.click("#btn-favs"); pg.wait_for_selector(".tcard"); assert pg.evaluate("location.hash") == "#/"
        pg.click("#btn-favs"); pg.wait_for_selector(".fav"); pg.click("a.brand"); pg.wait_for_selector(".tcard"); assert pg.evaluate("location.hash") == "#/"
        pg.click("#btn-favs"); pg.wait_for_selector(".fav")
        body = pg.inner_text(".favs"); assert "Rio de Janeiro" in body and "Preço quando salvou" in body and "Visto na última coleta" in body, body
        pg.select_option("[data-favsort]", "price"); pg.wait_for_selector(".fav")
        pg.goto(base + "/#/favoritos"); pg.wait_for_selector(".fav"); assert pg.locator(".fav").count() == 2, "favoritos persistem ao recarregar"
        pg.locator('.fav [data-favopen]').first.click(); pg.wait_for_selector(".fl")
        assert re.match(r"#/viagem/\d+/voos", pg.evaluate("location.hash")), "Ver na viagem abre a aba de voos"
        pg.click("#btn-favs"); pg.wait_for_selector(".fav")
        pg.locator(".fav .star").first.click(); pg.locator(".toast a[data-undo-fav]").wait_for()
        assert pg.locator(".fav").count() == 1
        pg.click("a[data-undo-fav]"); pg.locator(".toast").filter(has_text="restaurado").wait_for(); assert pg.locator(".fav").count() == 2
        for _ in range(2): pg.locator(".fav .star").first.click(); pg.wait_for_timeout(400)
        pg.wait_for_selector(".panel.empty"); assert "Nenhum favorito ainda" in pg.inner_text("#view") and pg.is_hidden("#fav-count")
        assert pg.evaluate("fetch('/api/favorites').then(r => r.json())")["list"] == []
        print("12c. favoritos: estrela no voo, aba Favoritos (preço quando salvou, visto na coleta), persiste, abrir viagem, desfazer, vazio")

        # ---- consulta pontual: busca uma vez, sem cadastrar recorrência ----
        pg.goto(base + "/#/"); pg.wait_for_selector(".tcard"); n0 = pg.locator(".tcard").count()
        pg.click("#btn-once"); pg.wait_for_selector("#dlg-watch[open]")
        assert "Consulta pontual" in pg.inner_text("#dlg-watch-title") and pg.is_visible("#once-note")
        assert pg.is_hidden("#every-wrap") and pg.is_hidden("#name-wrap") and pg.is_hidden("#now-wrap") and "Consultar agora" in pg.inner_text("#watch-save")
        pg.fill("#f-origin", "REC"); pg.fill("#f-dest", "SSA"); pg.check(f + "[name=trip][value=oneway]"); pg.fill(f + "[name=depart_date]", "2027-08-10")
        pg.click("#watch-save"); pg.locator(".toast").filter(has_text="Coleta disparada").wait_for()
        saved = pg.evaluate("fetch('/api/watches').then(r => r.json())")["list"]
        once = [w for w in saved if w.get("once")]
        assert len(once) == 1 and once[0]["every_hours"] is None and once[0]["label"] is None and once[0]["origin"] == "REC", once
        oid = once[0]["id"]
        pg.wait_for_selector(".section-title:has-text('Consultas pontuais')")
        assert pg.locator(f'.tcard[data-open-trip="{oid}"] .chip:has-text("Pontual")').count() == 1
        assert pg.locator(".tcard").count() == n0 + 1 and pg.locator(".kpis").inner_text().count("Viagens monitoradas") == 1
        assert pg.locator(".kpi-v").first.inner_text() == str(n0), "pontual não entra na contagem de viagens monitoradas"
        pg.click(f'.tcard[data-open-trip="{oid}"]'); pg.wait_for_selector(".trip-head")
        assert pg.locator("[data-act=toggle]").count() == 0 and pg.locator("[data-act=promote]").count() == 1
        pg.click("[data-act=promote]"); pg.wait_for_selector("#dlg-watch[open]")
        assert "Monitorar" in pg.inner_text("#dlg-watch-title") and pg.is_visible("#every-wrap") and pg.input_value(f + "[name=every_hours]") == "24"
        pg.uncheck(f + "[name=run_now]"); pg.click("#watch-save"); pg.wait_for_selector("#dlg-watch:not([open])", state="attached")
        saved = pg.evaluate("fetch('/api/watches').then(r => r.json())")["list"]
        w = next(x for x in saved if x["id"] == oid); assert w["once"] is False and w["every_hours"] == 24, w
        pg.click('[data-nav="/"]'); pg.wait_for_selector(".tcard"); assert pg.locator(".section-title:has-text('Consultas pontuais')").count() == 0
        pg.click("#btn-once"); pg.fill("#f-origin", "REC"); pg.fill("#f-dest", "FOR"); pg.check(f + "[name=trip][value=oneway]"); pg.fill(f + "[name=depart_date]", "2027-08-11"); pg.click("#watch-save")
        pg.wait_for_selector(".section-title:has-text('Consultas pontuais')")
        pg.click("[data-act=clear-once]"); pg.wait_for_selector(".section-title:has-text('Consultas pontuais')", state="detached")
        assert not [x for x in pg.evaluate("fetch('/api/watches').then(r => r.json())")["list"] if x.get("once")]
        pg.click(f'.tcard[data-open-trip="{oid}"]'); pg.wait_for_selector(".trip-head"); pg.click("[data-act=remove]"); pg.wait_for_selector(".tcard")
        print("12d. consulta pontual: botão próprio, sem recorrência, seção separada, fora dos indicadores, virar monitoramento, limpar todas")

        # ---- faixa de preço típica em qualquer busca (não só nas com tela de compra) ----
        pg.goto(base + "/#/"); pg.wait_for_selector(".tcard")
        assert "Faixa típica R$ 5.200 a R$ 7.400" in pg.inner_text('.tcard[data-open-trip="2"]').replace("\xa0", " ")
        assert "Faixa típica" not in pg.inner_text('.tcard[data-open-trip="1"]'), "multi-destino não tem faixa (o Google não mostra)"
        pg.goto(base + "/#/viagem/2/resumo"); pg.wait_for_selector(".range"); assert "Faixa de preço típica desta rota" in pg.inner_text(".range")
        pg.goto(base + "/#/viagem/2/voos"); pg.wait_for_selector(".gauge-top .range-bar"); assert pg.locator(".gauge-top .range-pin").count() == 1
        print("12e. faixa de preço típica aparece em ida e volta (resumo, voos e cartão), sem depender da tela de compra")

        # ---- horários de coleta (cron, Brasília) ----
        pg.goto(base + "/#/viagem/3/resumo"); pg.wait_for_selector(".trip-head"); pg.click("[data-act=edit]"); pg.wait_for_selector("#dlg-watch[open]")
        assert pg.is_hidden("#sched-box")
        pg.select_option(f + "[name=every_hours]", "custom"); pg.wait_for_selector("#sched-box:not([hidden])")
        assert pg.input_value(f + "[name=sched_cron]") == "0 0,2,4,6,22 * * *", "abre com o atalho Noite e madrugada"
        pg.locator("#sched-info").filter(has_text="Próximas coletas").wait_for()
        # janela 22h -> 6h, de 2 em 2 horas, só seg a sex
        pg.select_option(f + "[name=sched_from]", "22"); pg.select_option(f + "[name=sched_to]", "6"); pg.select_option(f + "[name=sched_every]", "2")
        for d in ("0", "6"): pg.uncheck(f + f"[name=sched_day][value='{d}']")
        assert pg.input_value(f + "[name=sched_cron]") == "0 0,2,4,6,22 * * 1-5"
        txt = pg.inner_text("#sched-info"); assert txt.count(":00") >= 5 and "Brasília" in txt, txt
        pg.click("[data-sched-preset=work]"); assert pg.input_value(f + "[name=sched_cron]") == "0 9,12,15,18 * * 1-5"
        pg.click("[data-sched-preset=dawn]"); assert pg.input_value(f + "[name=sched_cron]") == "0 2-6 * * *"
        # cron avançado que não cabe no modo simples (duas janelas): desliga os controles simples, mas aceita
        pg.fill(f + "[name=sched_cron]", "0 6,22 * * *")
        assert pg.is_disabled(f + "[name=sched_from]") and "Próximas coletas" in pg.inner_text("#sched-info")
        pg.fill(f + "[name=sched_cron]", "0 25 * * *"); pg.locator("#sched-info.bad").wait_for()
        pg.uncheck(f + "[name=run_now]"); pg.click("#watch-save")
        assert "hora" in pg.inner_text("#watch-err") and pg.is_visible("#dlg-watch"), "cron inválido não salva"
        pg.fill(f + "[name=sched_cron]", "0 22,0,2,4,6 * * 1-5"); pg.locator("#sched-info:not(.bad)").wait_for()
        assert not pg.is_disabled(f + "[name=sched_from]") and pg.input_value(f + "[name=sched_from]") == "22", "cron simples volta ao formulário"
        pg.click("#watch-save"); pg.wait_for_selector("#dlg-watch:not([open])", state="attached")
        w3 = next(w for w in pg.evaluate("fetch('/api/watches').then(r => r.json())")["list"] if w["id"] == 3)
        assert w3["schedule"] == "0 22,0,2,4,6 * * 1-5" and w3["every_hours"] is None, w3   # guarda o que foi escrito (normalizando espaços)
        assert "Próxima coleta" in pg.inner_text(".trip-head")
        pg.click('.tab[data-tab="busca"]'); body = pg.inner_text("#view"); assert "Seg a sex, das 22h às 6h, de 2 em 2 h" in body and "Brasília" in body, body[:400]
        pg.click("[data-act=edit]"); pg.wait_for_selector("#dlg-watch[open]")
        assert pg.input_value(f + "[name=every_hours]") == "custom" and pg.is_visible("#sched-box") and pg.input_value(f + "[name=sched_to]") == "6", "editar reabre no modo simples"
        pg.select_option(f + "[name=every_hours]", "24"); pg.wait_for_selector("#sched-box", state="hidden"); pg.click("#watch-save"); pg.wait_for_selector("#dlg-watch:not([open])", state="attached")
        w3 = next(w for w in pg.evaluate("fetch('/api/watches').then(r => r.json())")["list"] if w["id"] == 3)
        assert w3["schedule"] is None and w3["every_hours"] == 24, w3
        print("12f. horários de coleta: janela, dias, atalhos, cron avançado, erro, persistência e volta para 'a cada N horas'")

        # ---- versão do painel: rodapé e aviso de nova versão ----
        pg.goto(base + "/#/"); pg.wait_for_selector(".tcard")
        assert pg.is_visible("#ver") and "Versão" in pg.inner_text("#ver") and pg.locator(".banner:has-text('nova versão')").count() == 0
        import json as _json
        def fake_new_build(route):
            r = route.fetch(); body = r.json(); body["build"] = "dpl_NOVO"; body["version"] = "abc1234"
            route.fulfill(response=r, body=_json.dumps(body))
        pg.route("**/api/me", fake_new_build)
        pg.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        pg.wait_for_selector(".banner:has-text('Há uma nova versão do painel')")
        assert pg.locator("[data-act=reload]").count() == 1
        pg.unroute("**/api/me", fake_new_build)
        pg.click("[data-act=reload]"); pg.wait_for_selector(".tcard"); pg.wait_for_timeout(300)
        assert pg.locator(".banner:has-text('nova versão')").count() == 0, "depois de recarregar o aviso some"
        print("12g. versão do painel: rodapé e aviso 'Há uma nova versão' com botão Recarregar")

        # ---- voos acompanhados: evolução do preço de um voo específico ----
        pg.goto(base + "/#/viagem/4/voos"); pg.wait_for_selector(".fl [data-track]")
        pg.locator(".fl [data-track]").first.click(); pg.wait_for_selector("#trk-chart svg.chart")
        assert pg.evaluate("location.hash") == "#/viagem/4/acompanhar" and "Voos acompanhados" in pg.inner_text(".tabs")
        body = pg.inner_text("#view").lower(); assert "preço atual" in body and "menor já visto" in body and "apareceu em" in body and "mesmo voo em outras datas" in body, body[:600]
        assert pg.locator(".tablewrap").first.locator("tbody tr").count() == 5, "uma linha por coleta"
        pg.locator("#trk-chart svg.chart").hover(position={"x": 300, "y": 90}); pg.wait_for_selector("#trk-chart .tip:not([hidden])")
        # o segundo voo tem uma lacuna (não apareceu na 2ª coleta)
        opts = pg.locator("[data-track-flight] option"); n_opts = opts.count(); assert n_opts >= 3
        pg.select_option("[data-track-flight]", index=1); pg.wait_for_selector("#trk-chart .gapmark", state="attached")
        assert "não apareceu" in pg.inner_text(".tablewrap"), "a tabela marca a coleta em que o voo não apareceu"
        # filtro por número: digitar esconde as outras opções; Enter escolhe a primeira que sobrou
        pg.fill("[data-track-q]", "AV 1274"); pg.press("[data-track-q]", "Enter"); pg.wait_for_selector("#trk-chart svg.chart")
        assert "AV 1274" in pg.inner_text(".fl-air"), pg.inner_text(".fl-air")
        # mesmo voo em outras datas: troca a seleção para outra data
        pg.fill("[data-track-q]", ""); 
        od_rows = pg.locator("tr.trk-here"); assert od_rows.count() == 1
        before = pg.input_value("[data-track-date]")
        pg.locator("button[data-track]:has-text('Acompanhar')").first.click(); pg.wait_for_selector("#trk-chart svg.chart")
        assert pg.input_value("[data-track-date]") != before and "AV 1274" in pg.inner_text(".fl-air"), "trocar de data mantém o mesmo voo"
        pg.select_option("[data-track-date]", before); assert "AV 1274" in pg.inner_text(".fl-air")
        # favorito vira atalho (chip) e aparece no contador da aba
        pg.locator(".fl .star").first.click(); pg.wait_for_selector(".trk-chip")
        assert pg.locator(".tab[data-tab=acompanhar] .chip").inner_text().strip() == "1"
        # pela aba Favoritos: botão Evolução abre a aba da viagem já com o voo
        pg.click("#btn-favs"); pg.wait_for_selector(".fav [data-favtrack]"); pg.locator(".fav [data-favtrack]").first.click()
        pg.wait_for_selector("#trk-chart svg.chart"); assert pg.evaluate("location.hash") == "#/viagem/4/acompanhar"
        pg.locator(".fl .star").first.click(); pg.wait_for_selector(".trk-chip", state="detached")   # remove o favorito de teste
        print("12h. voos acompanhados: seleção por número, indicadores, gráfico com lacuna, tabela, outras datas, favoritos")

        # ---- chip "Melhores datas" não ultrapassa o cartão (ida e volta com vários empates) ----
        import copy as _copy
        def many_ties(route):
            r = route.fetch(); d = r.json(); w2 = next(w for w in d["watches"] if w["id"] == 2); base_c = w2["combos"][0]; w2["combos"] = []
            for i in range(8):
                c = _copy.deepcopy(base_c); dep = f"2027-03-{10 + i // 3:02d}"; ret = f"2027-03-{22 + i % 3:02d}"
                c.update(depart_date=dep, return_date=ret, end_date=ret)
                c["legs"] = [dict(c["legs"][0], date=dep), dict(c["legs"][1], date=ret)] if c.get("legs") else c.get("legs")
                w2["combos"].append(c)
            route.fulfill(response=r, body=_json.dumps(d))
        pg.route("**/api/dashboard", many_ties); pg.goto(base + "/#/"); pg.reload(); pg.wait_for_selector('.tcard[data-open-trip="2"]')
        for width in (1200, 390, 320):
            pg.set_viewport_size({"width": width, "height": 900}); pg.wait_for_timeout(150)
            chip = pg.locator('.tcard[data-open-trip="2"] .chip.good.wrap').first
            txt = chip.inner_text(); assert "Melhores datas:" in txt and "+6" in txt and txt.count("→") == 2, txt      # 2 datas + "+6"
            assert len(chip.get_attribute("title")) > len(txt), "o título guarda a lista completa"
            cb, kb = chip.bounding_box(), pg.locator('.tcard[data-open-trip="2"]').bounding_box()
            assert cb["x"] >= kb["x"] - 1 and cb["x"] + cb["width"] <= kb["x"] + kb["width"] + 1, (width, cb, kb)
        pg.set_viewport_size({"width": 1200, "height": 900})
        pg.unroute("**/api/dashboard", many_ties)
        print("12i. chip 'Melhores datas' curto (2 datas + N) e dentro do cartão em 1200/390/320 px")

        # ---- falha parcial não polui o cartão (a causa fica no detalhe e no balão do selo) ----
        def partial_fail(route):
            r = route.fetch(); d = r.json()
            w3 = next(w for w in d["watches"] if w["id"] == 3); w3.update(last_status="error", last_error="O Google Voos respondeu 'something went wrong'.", last_failed=2, last_total=22)
            w4 = next(w for w in d["watches"] if w["id"] == 4); w4.update(last_status="blocked", last_error="captcha", last_failed=1, last_total=1, combos=[])
            route.fulfill(response=r, body=_json.dumps(d))
        pg.route("**/api/dashboard", partial_fail); pg.reload(); pg.wait_for_selector(".tcard")
        c3 = pg.locator('.tcard[data-open-trip="3"]'); assert c3.locator(".tcard-fail").count() == 0, "parcial: sem caixa de aviso no cartão"
        assert "20 de 22 com sucesso" in c3.inner_text() and "página de erro passageira" in c3.locator(".chip.warn").first.get_attribute("title")
        assert pg.locator('.tcard[data-open-trip="4"] .tcard-fail').count() == 1, "falha total: a causa continua no cartão"
        pg.unroute("**/api/dashboard", partial_fail)
        print("12j. cartão: falha parcial só com o selo (causa no balão); falha total mantém a causa")

        # ---- sem o módulo series.js (ex.: deploy parcial) o painel continua: só some o acompanhamento de voos ----
        pg2 = ctx.new_page(); errs2 = []
        pg2.on("pageerror", lambda e: errs2.append(str(e)))
        pg2.route("**/series.js", lambda r: r.abort())
        pg2.goto(base + "/#/viagem/4/voos"); pg2.wait_for_selector(".fl")
        assert pg2.locator(".fl [data-track]").count() == 0 and pg2.locator(".tab[data-tab=acompanhar]").count() == 0
        assert pg2.locator(".fl .star").count() > 0, "o resto da aba Voos funciona"
        pg2.click("#btn-favs"); pg2.wait_for_selector(".panel.empty, .fav")
        pg2.goto(base + "/#/viagem/4/acompanhar"); pg2.wait_for_selector(".trip-head")
        assert "Voos acompanhados" not in pg2.inner_text(".tabs")
        assert not errs2, errs2
        pg2.close()
        print("12k. sem series.js: Voos e Favoritos continuam funcionando, sem os botões de acompanhamento")

        pg.goto(base + "/#/viagem/1/voos"); pg.wait_for_selector(".fl"); assert not pg.is_visible("#dlg-login")
        print("13. link direto + sessão mantida após recarregar")
        ctx.clear_cookies(); pg.click('[data-nav="/"]'); pg.click('.tcard [data-act=run]')
        pg.wait_for_selector("#dlg-login[open]"); print("14. sessão expirada abre o login")
        pg.fill("#form-login [name=password]", PW); pg.click("#login-go"); pg.wait_for_selector(".tcard")
        pg.click("#btn-auth"); pg.locator(".toast").filter(has_text="Você saiu").wait_for(); assert pg.locator(".tcard").count() == 0

        origins = {re.match(r"https?://[^/]+", u).group(0) for u in reqs if u.startswith("http")}
        assert origins == {base}, origins
        print("15. o navegador só falou com o próprio servidor:", origins)
        assert not errs, errs
        # celular: sem rolagem lateral
        m = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True); mp = m.new_page()
        mp.goto(base); mp.fill("#form-login [name=password]", PW); mp.click("#login-go"); mp.wait_for_selector(".tcard")
        for h in ("#/", "#/viagem/1/resumo", "#/viagem/3/voos", "#/viagem/2/comparar"):
            mp.evaluate(f"location.hash = '{h}'"); mp.wait_for_timeout(250)
            assert not mp.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"), f"rolagem lateral em {h}"
        mp.click("#btn-new"); mp.check("#form-watch [name=trip][value=multi]")
        mp.locator("#legs .leg-row").nth(0).locator(".leg-o").press_sequentially("new yo", delay=15); mp.wait_for_selector("#legs .ac-list:not([hidden]) li[data-i]")
        assert not mp.evaluate("(() => { const d = document.querySelector('#dlg-watch'); return d.scrollWidth > d.clientWidth + 1; })()"), "o formulário não pode rolar para o lado"
        lb = mp.locator("#legs .ac-list:not([hidden])").bounding_box(); db = mp.locator("#dlg-watch").bounding_box()
        assert lb["x"] >= db["x"] - 1 and lb["x"] + lb["width"] <= db["x"] + db["width"] + 1, "sugestões dentro do formulário"
        print("16. celular (390px): nenhuma tela com rolagem lateral; sugestões cabem no formulário")
        b.close()

    p2, base2 = start(omit_token=True); procs.append(p2)
    with sync_playwright() as pw:
        b = launch(pw); pg = b.new_page(); pg.goto(base2); pg.wait_for_selector(".banner.err")
        assert "GH_TOKEN" in pg.inner_text("#banners") and not pg.is_visible("#dlg-login")
        print("17. servidor sem GH_TOKEN: aviso vermelho dizendo o que falta")
        b.close()
finally:
    for pr in procs: pr.terminate()
print("TUDO OK")
