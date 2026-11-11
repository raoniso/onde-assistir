# -*- coding: utf-8 -*-
# build: 2026-11-11-b (auditoria: datas do guia pelo cabeçalho do dia; guia único)
"""
ONDE.ASSISTIR — Pipeline de dados v5 (auditoria de nov/2026)
============================================================
Roda sozinho no GitHub Actions (.github/workflows/atualizar.yml). Nenhuma
curadoria manual. Escopo: Futebol (BR + Europa, incl. Libertadores,
Sul-Americana, Champions e Copa do Brasil), NBA e NFL.

FONTES (cada uma protegida por try/except — uma falhar não derruba o resto):
  FUTEBOL .... mantosdofutebol.com.br é o guia de TV: jogo, competição, fase
               e CANAIS por jogo. A data de cada jogo vem do cabeçalho do dia
               na própria página (ela lista vários dias).
               ESPN (tabelas oficiais, 7 dias) completa o que o guia ainda
               não publicou, como "Transmissão a confirmar", e confirma o
               que ele publicou (selo v=2 quando o horário confere).
               O antigo 2º guia, futebolnatv.com.br, saiu em 11/11/2026: o
               site mudou de formato no fim de outubro, o leitor passou a
               devolver zero jogos e a página hoje lista bem menos partidas
               (3 contra 11 do guia, no dia da troca).
  BASQUETE ... ESPN (NBA). Canais POR JOGO, a partir dos transmissores que a
               ESPN informa em cada partida: pacote Amazon -> Prime Video;
               ESPN/ABC -> ESPN + Disney+; todos -> NBA League Pass.
  FUT. AMER. . ESPN (NFL). Canais pela janela de horário dos contratos
               2026/27: qui=SporTV, seg=ESPN, dom noite=SporTV, dom tarde=TV
               a confirmar; NFL Game Pass (DAZN) em todos.

SAÍDAS: eventos.json (dados), index.html (= template onde-assistir.html +
dados embutidos) e diagnostico.json (log das fontes + amostras brutas — é
por ele que se depura, sem precisar do log do Actions).

DATAS SÃO ABSOLUTAS ("date": "2026-11-11"). O app só exibe um evento na aba
do dia exato — dado antigo nunca "escorrega" para hoje.
"""

import difflib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; OndeAssistir/3.0)"}
DIAG = {"fontes": {}, "amostras": {}}
BRT = timezone(timedelta(hours=-3))
HOJE = datetime.now(BRT).date()
JANELA = [HOJE + timedelta(days=i) for i in range(7)]  # baseline semanal

# ----------------------------------------------------------------- helpers
def sem_acento(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()

def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", sem_acento(s).lower()).strip()

CANAIS_FREE = {"GLOBO","SBT","BAND","RECORD","REDETV","TV BRASIL","CAZE TV","CAZETV",
               "XSPORTS","YOUTUBE","CANAL GOAT","GOAT","METROPOLES","TV CULTURA",
               "SPORTYNET","GE TV","GETV","NSPORTS","N SPORTS","FIFA+"}
CANAIS_STREAM = {"DISNEY+","MAX","HBO MAX","PARAMOUNT+","PRIME VIDEO","AMAZON PRIME VIDEO",
                 "ONEFOOTBALL","FANATIZ","ZAPPING","UOL PLAY","GLOBOPLAY","PREMIERE FC",
                 "PREMIERE","DAZN","APPLE TV+","NOSSO FUTEBOL+","NBA LEAGUE PASS"}

# Grafia das marcas (aplicada depois do .title(), que escreveria "Espn", "Sbt"...)
MARCAS = [("Fc","FC"),("Espn","ESPN"),("Sbt","SBT"),("Sportv","SporTV"),
          ("Cazétv","CazéTV"),("Caze Tv","CazéTV"),("Cazé Tv","CazéTV"),("Caze TV","CazéTV"),
          ("Xsports","XSports"),("Hbo","HBO"),("Tnt","TNT"),("Nba","NBA"),
          ("Ge Tv","GE TV"),("Getv","GE TV"),("Nsports","N Sports"),
          ("Youtube","YouTube"),("Dazn","DAZN"),("Fifa","FIFA"),("Goat","GOAT")]
def grafia(nome):
    for a, dep in MARCAS: nome = nome.replace(a, dep)
    return nome

# Canais de YouTube cujo endereço vem todo em minúsculas (não dá para separar as
# palavras pelo maiúsculo/minúsculo): nome de exibição igual ao do canal de TV.
NOMES_YT = {"canalgoatbr": "Canal GOAT", "espnbrasil": "ESPN Brasil", "cazetv": "CazéTV",
            "metropolesesportes": "Metrópoles Esportes"}

def canal(nome):
    """Nome cru da fonte -> {"n": nome exibido, "y": tv|stream|free, "u": link exato}.
    "u" só existe quando a própria fonte deu o endereço (canais de YouTube)."""
    nome = " ".join(nome.split())
    yt = re.search(r"youtube\.com/@([\w.\-]+)", nome, re.I)
    if yt:   # 'Youtube.com/@TNTSportsBR' -> 'TNT Sports BR (YouTube)', grátis, link do canal
        handle = yt.group(1)
        legivel = NOMES_YT.get(handle.lower())
        if not legivel:
            legivel = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", handle)
            legivel = grafia(legivel[:1].upper() + legivel[1:])
        return {"n": f"{legivel} (YouTube)", "y": "free", "u": f"https://www.youtube.com/@{handle}"}
    chave = sem_acento(nome).upper().strip()
    tipo = "free" if chave in CANAIS_FREE or "YOUTUBE" in chave else \
           "stream" if chave in CANAIS_STREAM else "tv"
    return {"n": grafia(nome.title()), "y": tipo}

# País/região de cada liga, alinhado com os chips fixos do app. A PRIMEIRA
# regra que casar vence, então o específico vem antes do genérico.
PAIS_POR_LIGA = [
    (r"concacaf|\bafc\b|\bcaf\b|amistos|copa do mundo|mundial|eliminatorias|\bfifa\b|\bsele", "Mundial"),
    (r"brasileir|copa do brasil|supercopa do brasil|copa do nordeste|copa verde|copinha|"
     r"paulist|carioca|mineir|gauch|pernambucan|cearense|baian|paranaense|catarinense|goian|"
     r"paraiban|potiguar|capixaba|sergipan|alagoan|maranhense|piauiense|paraense|amazonense|"
     r"brasiliense|candango|mato.?grossense|tocantinense|fares lopes", "Brasil"),
    (r"libertadores|sul.?americana|recopa|conmebol|argentin|chilen|uruguai|colombian|"
     r"paraguai|peruan|equatorian|bolivian|venezuelan", "América do Sul"),
    (r"premier league|championship|ingl|fa cup|carabao|copa da liga inglesa", "Inglaterra"),
    (r"la ?liga|espanh|copa do rei", "Espanha"),
    (r"\bital|coppa|serie a tim", "Itália"),
    (r"alem|bundesliga|dfb", "Alemanha"),
    (r"mls|nwsl|nba|wnba|nfl|mlb|\beua\b|canad", "EUA/Canadá"),
    (r"franc|ligue 1|portug|holand|eredivisie|champions|europa league|conference|uefa|\beuro|nations|"
     r"\bturc|\bruss|\bbelg|\bgreg|\bescoc|\bnoruegu|\bsuec|\bsuic|\bdinamarqu|\bservi|\bchec|\btchec|"
     r"\baustria|\bpolon|\bcroa|\bucrani", "Europa (demais)"),
]
def pais_da_liga(liga):
    alvo = sem_acento(liga).lower()
    for padrao, pais in PAIS_POR_LIGA:
        if re.search(padrao, alvo): return pais
    return "Mundial"     # liga não reconhecida: aparece só sem filtro de país

def genero(*textos):
    alvo = sem_acento(" ".join(textos)).lower()
    return "F" if re.search(r"feminin|\bfem\b|\(w\)|\bwomen|\bnwsl\b|\bwnba\b", alvo) else "M"

def evento(date, t, sport, league, match, ch, detail="—", g=None, country=None, v=1):
    return {"date": date.isoformat() if hasattr(date, "isoformat") else date,
            "t": t, "sport": sport, "g": g or genero(league, match),
            "country": country or pais_da_liga(league),
            "league": league.strip(), "detail": detail,
            "match": match, "ch": ch, "v": v}

# =================================================================
# FUTEBOL — guia de TV: mantosdofutebol.com.br
# =================================================================
def separa_canais_mantos(texto):
    """Divide 'A, B e C' e 'Globo/SporTV' sem quebrar URLs (Youtube.com/@X),
    que seguem inteiras para canal() extrair nome e link."""
    out = []
    for parte in re.split(r",| e ", texto):
        parte = parte.strip()
        if not parte: continue
        if ".com" in parte.lower():
            out.append(parte)
        else:
            out += [p.strip() for p in parte.split("/") if p.strip()]
    return out

# Cabeçalho de dia nas páginas do mantos: "Segunda – 09/11", "Sexta-feira, 13/11"...
# Precisa COMEÇAR pelo dia da semana: data solta no meio de um texto não é cabeçalho.
RX_DIA_MANTOS = re.compile(
    r"^\W*(segunda|terca|quarta|quinta|sexta|sabado|domingo)(?:-feira)?\W+(\d{1,2})/(\d{1,2})\b", re.I)

RX_JOGO = re.compile(r"\d{1,2}h\d{2}\b")            # linha que começa por um horário
RX_CANAIS = re.compile(r"Canais?\s*:\s*(.+)", re.I)

def data_do_cabecalho(linha):
    """'Segunda – 09/11' -> date (o ano é o que deixa a data mais perto de hoje)."""
    if len(linha) > 40: return None
    m = RX_DIA_MANTOS.match(sem_acento(linha))
    if not m: return None
    dia, mes = int(m.group(2)), int(m.group(3))
    opcoes = []
    for ano in (HOJE.year - 1, HOJE.year, HOJE.year + 1):
        try:
            opcoes.append(datetime(ano, mes, dia).date())
        except ValueError:
            pass
    return min(opcoes, key=lambda d: abs((d - HOJE).days)) if opcoes else None

def fonte_mantos():
    urls = {HOJE: "https://mantosdofutebol.com.br/guia-de-jogos-tv-hoje-ao-vivo/",
            HOJE + timedelta(days=1): "https://mantosdofutebol.com.br/jogos-de-amanha-tv/"}
    # Estrutura real (capturada via diagnostico.json em 09 e 11/11/2026):
    #   "Segunda – 09/11"                                   <- cabeçalho do DIA
    #   "15h00 – Palmeiras x Santos – Paulistão Sub-20 (Semifinal-ida)"
    #   "Canais: Youtube.com/@TNTSportsBR"
    # Cada página traz VÁRIOS dias em sequência. A data de cada jogo vem do
    # cabeçalho acima dele — nunca da URL ("hoje"/"amanhã"), que só vale como
    # último recurso se a página vier sem cabeçalho nenhum.
    # Entre o 2º time e a competição o separador é um travessão (– ou —) ou um
    # hífen COM espaço dos dois lados; hífen colado faz parte do nome do time
    # ("Atlético-MG", "River Plate-URU", "Al-Nassr").
    rx = re.compile(r"(\d{1,2})[h:](\d{2})\s*[–\-—]\s*(.+?)\s+[xX]\s+(.+?)(?:\s*[–—]\s*|\s+-\s+)([^(\n]+?)\s*(?:\(([^)]+)\))?\s*$")
    # o mesmo jogo publicado SEM a competição (a linha acaba no 2º time)
    rx_sem_liga = re.compile(r"(\d{1,2})[h:](\d{2})\s*[–\-—]\s*(.+?)\s+[xX]\s+(.+?)\s*[–—\-]?\s*$")
    evs = []
    for data_url, url in urls.items():
        r = requests.get(url, headers=HEADERS, timeout=30); r.raise_for_status()
        corpo = BeautifulSoup(r.text, "html.parser").select_one("article")
        if not corpo: continue
        linhas = [l.strip() for l in corpo.get_text("\n").split("\n") if l.strip()]
        diag = {"url": url, "dias": [], "jogos_sem_cabecalho": 0, "jogos_sem_canal": 0,
                "jogos_sem_competicao": 0, "linhas_emendadas": [],
                "linhas_com_data_ignoradas": [], "linhas_de_jogo_nao_lidas": []}
        data_atual, secao = None, None
        for i, linha in enumerate(linhas):
            m, extra = rx.search(linha), 0       # extra = linhas seguintes já usadas por este jogo
            if not m and RX_JOGO.match(linha):
                # A fonte às vezes parte o jogo em 2-3 linhas (link ou negrito no meio
                # do texto): "15h00 – Ypiranga x Afogados –" + "Pernambucano". Emenda
                # as seguintes até fechar "hora – A x B – competição".
                emenda = linha
                for k in (1, 2):
                    prox = linhas[i + k] if i + k < len(linhas) else ""
                    if not prox or len(prox) > 90 or RX_JOGO.match(prox) or RX_CANAIS.match(prox) \
                            or data_do_cabecalho(prox):
                        break
                    emenda += " " + prox
                    m = rx.search(emenda)
                    if m:
                        extra = k
                        if len(diag["linhas_emendadas"]) < 5: diag["linhas_emendadas"].append(emenda[:140])
                        break
                # Jogo publicado sem a competição: vale se os canais vêm logo abaixo.
                if not m and i + 1 < len(linhas) and RX_CANAIS.match(linhas[i + 1]):
                    m = rx_sem_liga.search(linha)
                    if m: diag["jogos_sem_competicao"] += 1
            if not m:
                d = data_do_cabecalho(linha)
                if d:
                    data_atual = d
                    secao = {"cabecalho": linha[:40], "data": d.isoformat(), "jogos": 0}
                    diag["dias"].append(secao)
                elif re.search(r"\b\d{1,2}/\d{1,2}\b", linha) and len(linha) <= 60 \
                        and len(diag["linhas_com_data_ignoradas"]) < 5:
                    diag["linhas_com_data_ignoradas"].append(linha)
                elif RX_JOGO.match(linha) and len(diag["linhas_de_jogo_nao_lidas"]) < 5:
                    # formato novo? aparece aqui, com as duas linhas seguintes de contexto
                    diag["linhas_de_jogo_nao_lidas"].append(
                        {"linha": linha[:120], "seguintes": [l[:80] for l in linhas[i+1:i+3]]})
                continue
            h, mi, t1, t2 = m.group(1, 2, 3, 4)
            compet, fase = (m.group(5), m.group(6)) if m.re is rx else ("", None)
            if not fase and i + extra + 1 < len(linhas):     # fase numa linha só dela: "(Semifinal-ida)"
                mf = re.fullmatch(r"\(([^)]+)\)", linhas[i + extra + 1])
                if mf: fase, extra = mf.group(1), extra + 1
            canais = []
            for prox in linhas[i+1+extra:i+3+extra]:
                mc = RX_CANAIS.match(prox)
                if mc:
                    canais = separa_canais_mantos(mc.group(1))
                    break
                if rx.search(prox): break  # próximo jogo veio antes da linha de canais
            liga = (compet or "").strip() or "Futebol"
            detalhe = (fase or "—").strip() or "—"
            # segurança: se a "liga" for apenas uma fase, vira detalhe
            if re.search(r"^(semifinal|quartas|oitavas|final|rodada|jogo \d)", sem_acento(liga), re.I):
                detalhe, liga = liga, "Futebol"
            if secao: secao["jogos"] += 1
            else: diag["jogos_sem_cabecalho"] += 1
            if not canais: diag["jogos_sem_canal"] += 1
            esporte = "Futsal" if re.search(r"futsal|\blnf\b", liga, re.I) else "Futebol"
            evs.append(evento(data_atual or data_url, f"{int(h):02d}:{mi}", esporte, liga,
                              f"{t1.strip()} x {t2.strip()}",
                              [canal(c) for c in canais] or [{"n": "Transmissão a confirmar", "y": "tv"}],
                              detail=detalhe))
        DIAG["amostras"].setdefault("mantos", []).append(diag)
    return dedup(evs)

# =================================================================
# ESPN API (pública, JSON) — NBA, NFL e tabelas de futebol
# Os canais NÃO vêm de uma lista fixa: NBA usa os transmissores que a ESPN
# informa em cada jogo (canais_nba); NFL usa a janela de horário do contrato
# (canais_nfl); futebol entra como "Transmissão a confirmar".
# =================================================================
def espn_json(path, data):
    url = f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={data:%Y%m%d}"
    r = requests.get(url, headers=HEADERS, timeout=30); r.raise_for_status()
    return r.json()

def espn_eventos(path, dias):
    """Eventos do scoreboard em cada dia pedido. Um dia que falhe não derruba
    os outros; só é erro se TODAS as consultas falharem."""
    evs, falhas = [], 0
    for data in dias:
        try:
            evs += espn_json(path, data).get("events", [])
        except Exception:
            falhas += 1
    if falhas == len(dias):
        raise RuntimeError(f"ESPN {path}: as {falhas} consultas falharam")
    return evs

# A ESPN agrupa os jogos pelo dia dos EUA. Jogo que começa depois da
# meia-noite de Brasília (00h00–02h) pertence à rodada americana da VÉSPERA,
# então NBA/NFL consultam também o dia anterior à janela.
DIAS_EUA = [HOJE - timedelta(days=1)] + JANELA

def broadcasts_espn(comp):
    """Nomes dos transmissores (EUA) que a própria ESPN lista no jogo."""
    nomes = []
    for b in comp.get("broadcasts", []):
        nomes += b.get("names", [])
    for gb in comp.get("geoBroadcasts", []):
        n = (gb.get("media") or {}).get("shortName")
        if n: nomes.append(n)
    return " | ".join(nomes).upper()

def canais_nba(comp):
    """NBA no Brasil 2026/27, POR JOGO (fonte: broadcasts da ESPN por partida):
    - pacote Amazon (global) -> incluído no Prime Video
    - jogos ESPN/ABC (EUA)  -> ESPN/Disney+ no Brasil
    - todos os demais       -> só NBA League Pass (pago à parte, mesmo dentro do Prime)"""
    tx = broadcasts_espn(comp)
    ch = []
    if "PRIME" in tx or "AMAZON" in tx:
        ch.append({"n": "Prime Video", "y": "stream"})
    if "ESPN" in tx or "ABC" in tx:
        ch += [{"n": "ESPN", "y": "tv"}, {"n": "Disney+", "y": "stream"}]
    ch.append({"n": "NBA League Pass", "y": "stream"})
    return ch

def fonte_nba():
    evs = []
    for ev in espn_eventos("basketball/nba", DIAS_EUA):
        dt = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(BRT)
        if dt.date() not in JANELA: continue
        comp = ev.get("competitions", [{}])[0]
        times = [c.get("team", {}).get("displayName", "?") for c in comp.get("competitors", [])]
        if len(times) < 2: continue
        # instrumentação: transmissores crus dos primeiros jogos (valida canais_nba)
        if len(DIAG["amostras"].get("nba_broadcasts", [])) < 4:
            DIAG["amostras"].setdefault("nba_broadcasts", []).append(
                f"{times[1]} x {times[0]} :: {broadcasts_espn(comp) or '(vazio)'}")
        notas = comp.get("notes", [])
        detalhe = (notas[0].get("headline") if notas else "") or "—"   # só o que a ESPN destaca (NBA Cup, jogo internacional)
        evs.append(evento(dt.date(), dt.strftime("%H:%M"), "Basquete", "NBA",
                          f"{times[1]} x {times[0]}", canais_nba(comp),
                          detail=detalhe, country="EUA/Canadá"))
    return dedup(evs)

# Direitos NFL no Brasil 2026/27 (contratos públicos, por janela de horário):
# quinta = SporTV; segunda à noite = ESPN; domingo à noite = SporTV;
# domingo tarde = ESPN/SporTV/GE TV (varia por jogo); NFL Game Pass (DAZN) = todos.
def canais_nfl(dt):
    base = [{"n": "NFL Game Pass", "y": "stream"}]
    wd = dt.weekday()  # 0=seg ... 6=dom
    if wd == 3:
        return [{"n": "SporTV", "y": "tv"}, {"n": "Globoplay", "y": "stream"}] + base
    if wd == 0:
        return [{"n": "ESPN", "y": "tv"}, {"n": "Disney+", "y": "stream"}] + base
    if wd == 6 and dt.hour >= 21:
        return [{"n": "SporTV", "y": "tv"}, {"n": "Globoplay", "y": "stream"}] + base
    if wd == 6:
        return base + [{"n": "TV a confirmar", "y": "tv"}]
    return base

def fonte_nfl():
    evs = []
    for ev in espn_eventos("football/nfl", DIAS_EUA):
        dt = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(BRT)
        if dt.date() not in JANELA: continue
        comp = ev.get("competitions", [{}])[0]
        times = [c.get("team", {}).get("displayName", "?") for c in comp.get("competitors", [])]
        if len(times) < 2: continue
        notas = comp.get("notes", [])
        detalhe = (notas[0].get("headline") if notas else "") or "—"   # só o que a ESPN destaca (NBA Cup, jogo internacional)
        evs.append(evento(dt.date(), dt.strftime("%H:%M"), "Futebol Americano", "NFL",
                          f"{times[1]} x {times[0]}", canais_nfl(dt),
                          detail=detalhe, country="EUA/Canadá"))
    return dedup(evs)


# =================================================================
LIGAS_FIXTURES = [
    ("soccer/uefa.champions",         "Champions League",     "Europa (demais)"),
    ("soccer/bra.copa_do_brazil",     "Copa do Brasil",       "Brasil"),
    ("soccer/bra.1",                  "Brasileirão Série A",  "Brasil"),
    ("soccer/bra.2",                  "Brasileirão Série B",  "Brasil"),
    ("soccer/conmebol.libertadores",  "Copa Libertadores",    "América do Sul"),
    ("soccer/conmebol.sudamericana",  "Copa Sul-Americana",   "América do Sul"),
    ("soccer/eng.1",                  "Premier League",       "Inglaterra"),
    ("soccer/esp.1",                  "La Liga",              "Espanha"),
    ("soccer/ger.1",                  "Bundesliga",           "Alemanha"),
    ("soccer/ita.1",                  "Campeonato Italiano",  "Itália"),
    ("soccer/fra.1",                  "Campeonato Francês",   "Europa (demais)"),
]
def fonte_fixtures():
    evs, por_liga = [], {}
    for slug, liga, pais in LIGAS_FIXTURES:
        try:
            brutos = espn_eventos(slug, JANELA)
        except Exception as e:
            por_liga[liga] = f"ERRO ({type(e).__name__})"   # slug inválido ou ESPN fora do ar
            continue
        n = 0
        for ev in brutos:
            try:
                dt = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(BRT)
            except Exception:
                continue
            if dt.date() not in JANELA: continue
            comp = ev.get("competitions", [{}])[0]
            cs = comp.get("competitors", [])
            if len(cs) < 2: continue
            home = next((c for c in cs if c.get("homeAway") == "home"), cs[0])
            away = next((c for c in cs if c.get("homeAway") == "away"), cs[-1])
            hn = home.get("team", {}).get("displayName", "?")
            an = away.get("team", {}).get("displayName", "?")
            notas = comp.get("notes", [])
            detalhe = (notas[0].get("headline", "") if notas else "") or "Tabela oficial"
            evs.append(evento(dt.date(), dt.strftime("%H:%M"), "Futebol", liga,
                              f"{hn} x {an}", [{"n": "Transmissão a confirmar", "y": "tv"}],
                              detail=detalhe, country=pais, v=1))
            n += 1
        por_liga[liga] = n
    DIAG["fontes"]["espn_tabelas_por_liga"] = por_liga   # mostra slug morto na hora
    return dedup(evs)


# =================================================================
# Cruzamento entre fontes
# =================================================================
RE_BASE = re.compile(r"sub.?\d{1,2}|\bu-?\d{2}\b|juvenil|junior|aspirantes|\bbase\b|\bcopinha\b", re.I)
RE_TIME_B = re.compile(r"\s(B|II)$")

def categoria(e):
    """Elenco que joga: feminino, base (Sub-20...), time B ou principal.
    O mesmo confronto no mesmo dia em categorias diferentes são jogos DIFERENTES."""
    if e.get("g") == "F": return "F"
    if RE_BASE.search(sem_acento(f'{e["match"]} {e["league"]} {e.get("detail") or ""}')): return "base"
    if any(RE_TIME_B.search(t.strip()) for t in re.split(r"\s+x\s+", e["match"], flags=re.I)): return "B"
    return "principal"

def casa_time(a, b):
    a, b = norm(a), norm(b)
    if not a or not b: return False
    if a == b or a in b or b in a: return True
    return difflib.SequenceMatcher(None, a, b).ratio() > 0.8

def times_de(match):
    p = re.split(r"\s+x\s+", match, flags=re.I)
    return p if len(p) == 2 else None

def mesmo_jogo(m1, m2):
    """Os DOIS times casam (direto ou invertido). Compara time a time, não a
    string inteira (evita falsos positivos como 'Holanda x Japão' ~
    'Alemanha x Curaçao' por letras coincidentes)."""
    p1, p2 = times_de(m1), times_de(m2)
    if not p1 or not p2:
        return difflib.SequenceMatcher(None, norm(m1), norm(m2)).ratio() > 0.7
    return (casa_time(p1[0], p2[0]) and casa_time(p1[1], p2[1])) or \
           (casa_time(p1[0], p2[1]) and casa_time(p1[1], p2[0]))

def um_time_em_comum(m1, m2):
    p1, p2 = times_de(m1), times_de(m2)
    return bool(p1 and p2) and any(casa_time(a, b) for a in p1 for b in p2)

def paises_compativeis(a, b):
    pa, pb = a.get("country"), b.get("country")
    return pa == pb or "Mundial" in (pa, pb)      # "Mundial" = país não identificado

def casar_fontes(e, candidatos):
    """Acha em `candidatos` o registro do MESMO jogo que `e` (mesmo dia, mesmo
    esporte, mesma categoria). 1º: os dois times casam. 2º: mesmo horário + um
    time em comum + país compatível — cobre nomes que cada fonte escreve de um
    jeito ('PSG' x 'Paris Saint-Germain', 'Rennes' x 'Stade Rennais'): um time
    não joga duas partidas na mesma hora."""
    mesmos = [c for c in candidatos
              if c["date"] == e["date"] and c["sport"] == e["sport"] and categoria(c) == categoria(e)]
    for c in mesmos:
        if mesmo_jogo(c["match"], e["match"]): return c
    for c in mesmos:
        if c["t"] == e["t"] and paises_compativeis(c, e) and um_time_em_comum(c["match"], e["match"]):
            return c
    return None

def minutos(t):
    h, m = t.split(":"); return int(h) * 60 + int(m)

def complementar_fixtures(base, fixtures):
    """Cruza o guia de TV com a tabela oficial da ESPN.
    - jogo só na tabela  -> entra como card "Transmissão a confirmar";
    - jogo nos dois      -> fica o do guia (tem canais); se o horário confere,
      ganha selo v=2 e o nome oficial da liga; guarda os nomes da ESPN em
      "espn" (é com eles que o app casa o placar ao vivo)."""
    divergencias = []
    for fx in fixtures:
        achou = casar_fontes(fx, base)
        if achou is None:
            base.append(fx)
            continue
        achou["espn"] = fx["match"]
        confere = abs(minutos(achou["t"]) - minutos(fx["t"])) <= 30
        if confere:
            achou["v"] = 2
        elif len(divergencias) < 5:
            divergencias.append(f'{fx["date"]} {achou["match"]}: guia {achou["t"]} x ESPN {fx["t"]}')
        if confere or achou.get("league") in ("Futebol", "", None):
            # uma liga, um nome: "Brasileirão", "LaLiga", "Ligue 1" do guia viram
            # o nome da tabela oficial — é com ele que os chips do app filtram
            achou["league"], achou["country"] = fx["league"], fx["country"]
            if (achou.get("detail") or "—") == "—" and fx.get("detail") != "Tabela oficial":
                achou["detail"] = fx["detail"]
    if divergencias:
        DIAG["amostras"]["horario_guia_x_espn"] = divergencias
    return base

# ----------------------------------------------------------------- duplicatas
def dedup_por_jogo(evs):
    """Rede de segurança: o MESMO jogo 2x no dia (mesmo esporte e categoria, os
    dois times casam). Fica o de maior prioridade: canais reais > selo v."""
    def prioridade(e):
        tem_canal = any("confirmar" not in c["n"].lower() for c in e["ch"])
        return (2 if tem_canal else 0) + e.get("v", 1)
    saida = []
    for e in sorted(evs, key=prioridade, reverse=True):
        dup = next((s for s in saida
                    if s["date"] == e["date"] and s["sport"] == e["sport"]
                    and categoria(s) == categoria(e)
                    and mesmo_jogo(s["match"], e["match"])), None)
        if dup is None:
            saida.append(e)
    return saida

def dedup(evs):
    saida, vistos = [], set()
    for e in evs:
        k = (e["date"], e["t"], norm(e["match"]))
        if k in vistos: continue
        vistos.add(k); saida.append(e)
    return saida

TEMPLATE, PAGINA = "onde-assistir.html", "index.html"

def publicar_pagina(eventos, agora):
    """index.html = template (onde-assistir.html) + dados. O template nunca é
    alterado pelo robô: editar o app é editar só o template."""
    html = Path(TEMPLATE).read_text(encoding="utf-8")
    # "</" escapado: texto raspado de terceiros nunca fecha o <script> da página
    dados = json.dumps(eventos, ensure_ascii=False, indent=1).replace("</", "<\\/")
    bloco = "/*DB-START*/\nconst DB = " + dados + ";\n/*DB-END*/"
    # função no lugar de string: o re.sub não reinterpreta barras do JSON
    html, n1 = re.subn(r"/\*DB-START\*/.*?/\*DB-END\*/", lambda _: bloco, html, count=1, flags=re.S)
    html, n2 = re.subn(r"const GENERATED_AT = .*?;", lambda _: f"const GENERATED_AT = '{agora}';", html, count=1)
    if n1 != 1 or n2 != 1:
        raise RuntimeError("template sem os marcadores DB-START/DB-END ou GENERATED_AT")
    Path(PAGINA).write_text(html, encoding="utf-8")

def main():
    def roda(nome, fn, log):
        try:
            r = fn(); log.append(f"  ✓ {nome}: {len(r)} evento(s)"); return r
        except Exception as e:
            log.append(f"  ✗ {nome} indisponível ({type(e).__name__}: {e})"); return []

    log = [f"Coleta {datetime.now(BRT):%d/%m %H:%M} BRT — janela {JANELA[0]} a {JANELA[-1]}"]

    eventos = roda("mantosdofutebol", fonte_mantos, log)

    fixtures = roda("ESPN tabelas (futebol, 7 dias)", fonte_fixtures, log)
    eventos = complementar_fixtures(eventos, fixtures)

    eventos += roda("ESPN NBA", fonte_nba, log)
    eventos += roda("ESPN NFL", fonte_nfl, log)
    eventos = dedup_por_jogo(eventos)
    eventos = dedup(eventos)
    eventos = [e for e in eventos if JANELA[0].isoformat() <= e["date"] <= JANELA[-1].isoformat()]
    # Sanitização final: competição/fase que escape para o lugar do canal não passa.
    # Canais vindos de URL explícita (YouTube) e o FIFA+ são sempre legítimos.
    PSEUDO = re.compile(r"copa|fifa|rodada|grupo|s[ée]rie|amistos|libertadores|\b20\d\d\b", re.I)
    for e in eventos:
        e["ch"] = [c for c in e["ch"]
                   if c.get("u") or c["n"].strip().upper() == "FIFA+" or not PSEUDO.search(sem_acento(c["n"]))]
        if not e["ch"]: e["ch"] = [{"n": "Transmissão a confirmar", "y": "tv"}]
    eventos.sort(key=lambda e: (e["date"], e["t"]))

    Path("eventos.json").write_text(json.dumps(eventos, ensure_ascii=False, indent=1), encoding="utf-8")
    log.append(f"TOTAL: {len(eventos)} eventos → eventos.json")

    agora = datetime.now(BRT).strftime("%d/%m %H:%M")
    try:
        publicar_pagina(eventos, agora)
        log.append(f"{PAGINA} gerado a partir de {TEMPLATE}.")
    except Exception as e:
        log.append(f"  ✗ {PAGINA} NÃO atualizado ({type(e).__name__}: {e})")
    DIAG["fontes"]["log"] = log
    Path("diagnostico.json").write_text(json.dumps(DIAG, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(log))

if __name__ == "__main__":
    main()
