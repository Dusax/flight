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
        assert pg.locator(".dtile.min").count() == 1
        pg.locator(".dtile").nth(1).click(); pg.wait_for_selector(".fl"); assert pg.evaluate("location.hash").endswith("/voos")
        print("7. calendário de datas: menor preço marcado; clicar numa data abre os voos dela")

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
        pg.locator("#plan-info.bad").filter(has_text="mais de 60").wait_for()
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
