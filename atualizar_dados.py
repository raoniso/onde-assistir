# -*- coding: utf-8 -*-
# build: 2026-11-09-c (parser mantos v2: competição+canais reais)
"""
ONDE.ASSISTIR — Pipeline de dados v4 (pós-Copa 2026)
====================================================
Roda sozinho no GitHub Actions (atualizar.yml). Nenhuma curadoria manual.
Escopo: os esportes que o Raoni acompanha — Futebol (BR + Europa, incl.
Libertadores/Sul-Americana/Champions/Copa do Brasil), NBA e NFL.

FONTES (cada uma protegida por try/except — uma falhar não derruba o resto):
  FUTEBOL .... futebolnatv.com.br (canais por jogo; instrumentado no
               diagnostico.json) ✕ mantosdofutebol.com.br (cruzamento).
               Jogos confirmados nas duas fontes recebem selo "v":2 (✓✓)
               + ESPN tabelas 7 dias (fixtures, "Transmissão a confirmar")
  BASQUETE ... API pública da ESPN (NBA) — canais fixos do contrato Brasil:
               Prime Video + NBA League Pass
  FUT. AMER. . API pública da ESPN (NFL) — canais por janela de horário
               (contratos 2026/27): qui=SporTV, seg=ESPN, dom noite=SporTV,
               dom tarde=TV a confirmar; NFL Game Pass (DAZN) em todos.

OBSERVABILIDADE: cada execução grava diagnostico.json no repositório com o
log das fontes e amostras brutas — é por ele que se depura sem acesso ao log
do Actions.

DATAS SÃO ABSOLUTAS ("date": "2026-06-11"). O app só exibe um evento na aba
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

# Palavras que NUNCA fazem parte do nome de um jogo (canais e ruído da fonte)
LIXO_TITULO = re.compile(
    r"\b(fim de jogo|ao vivo|globo ?play|globoplay|globo|sbt|sportv|sporttv|"
    r"ge ?tv|caz[eé] ?tv|caz[eé]|n ?sports|nsports|xsports|premiere|disney\+?|"
    r"espn ?\d?|band ?sports|band|record|youtube|tv aberta|streaming|"
    r"\d+\s*x\s*\d+|\d{1,3}\+?\d?['’]|intervalo|prorroga\w*|p[êe]naltis)\b", re.I)
def limpa_titulo(s):
    s = re.sub(r"^\s*\d{1,3}\+?\d?['’]\s*", "", s)  # cronômetro no início (40')
    s = re.sub(r"\(.*?\)", " ", s)          # remove parênteses
    s = LIXO_TITULO.sub(" ", s)               # remove canais/placar/lixo
    s = re.sub(r"\s+", " ", s).strip(" -–—")
    return s.strip()

CANAIS_FREE = {"GLOBO","SBT","BAND","RECORD","REDETV","TV BRASIL","CAZE TV","CAZETV",
               "XSPORTS","YOUTUBE","CANAL GOAT","GOAT","METROPOLES","TV CULTURA",
               "SPORTYNET","GE TV","GETV","NSPORTS","N SPORTS"}
CANAIS_STREAM = {"DISNEY+","MAX","HBO MAX","PARAMOUNT+","PRIME VIDEO","AMAZON PRIME VIDEO",
                 "ONEFOOTBALL","FANATIZ","ZAPPING","UOL PLAY","GLOBOPLAY","PREMIERE FC",
                 "PREMIERE","DAZN","APPLE TV+","NOSSO FUTEBOL+","NBA LEAGUE PASS",
                 "F1 TV PRO","WSL+"}
COMPOSTOS = CANAIS_FREE | CANAIS_STREAM | {"SPORTV 2","SPORTV 3","ESPN 2","ESPN 3",
                                           "ESPN 4","ESPN 5","ESPN 6","BAND SPORTS"}

def canal(nome):
    chave = sem_acento(nome).upper().strip()
    tipo = "free" if chave in CANAIS_FREE else "stream" if chave in CANAIS_STREAM else "tv"
    b = nome.title()
    for a, dep in [("Fc","FC"),("Espn","ESPN"),("Sbt","SBT"),("Sportv","SporTV"),
                   ("Cazétv","CazéTV"),("Caze Tv","CazéTV"),("Cazé Tv","CazéTV"),("Xsports","XSports"),
                   ("Hbo","HBO"),("Tnt","TNT"),("Nba","NBA"),("Wsl","WSL"),
                   ("Ge Tv","GE TV"),("Getv","GE TV"),("Nsports","N Sports"),
                   ("Youtube","YouTube"),(" Br ("," BR ("),
                   ("Dazn","DAZN"),("Fifa","FIFA"),("Goat","GOAT")]:
        b = b.replace(a, dep)
    return {"n": b, "y": tipo}

# Saídas alinhadas com os chips fixos do app
PAIS_POR_LIGA = [
    (r"brasileir|copa do brasil|paulista|carioca|mineiro|gaucho|copinha", "Brasil"),
    (r"libertadores|sul.?americana|recopa|argentin|chilen|uruguai|colombian", "América do Sul"),
    (r"premier league|championship|ingl|fa cup|carabao", "Inglaterra"),
    (r"la liga|espanhol|copa do rei", "Espanha"),
    (r"italian|coppa", "Itália"),
    (r"alem|bundesliga|dfb", "Alemanha"),
    (r"mls|nwsl|nba|wnba|nfl|mlb|\beua\b|canad", "EUA/Canadá"),
    (r"franc|ligue 1|portug|holand|eredivisie|champions|europa league|conference|uefa|euro|nations", "Europa (demais)"),
    (r"amistos|mundial|copa do mundo|fifa|sele|atp|wta|wsl", "Mundial"),
]
def pais_da_liga(liga):
    alvo = sem_acento(liga).lower()
    for padrao, pais in PAIS_POR_LIGA:
        if re.search(padrao, alvo): return pais
    return "Mundial"

def normalizar_liga(liga):
    return liga.strip() if isinstance(liga, str) else liga

def genero(*textos):
    alvo = sem_acento(" ".join(textos)).lower()
    return "F" if re.search(r"feminin|\bfem\b|\(w\)|wta|wnba", alvo) else "M"

def evento(date, t, sport, league, match, ch, detail="—", g=None, country=None, v=1):
    return {"date": date.isoformat() if hasattr(date, "isoformat") else date,
            "t": t, "sport": sport, "g": g or genero(league, match),
            "country": country or pais_da_liga(league),
            "league": normalizar_liga(league), "detail": detail,
            "match": match, "ch": ch, "v": v}

TRAD = {"Brazil":"Brasil","Germany":"Alemanha","France":"França","Spain":"Espanha",
 "Netherlands":"Holanda","Belgium":"Bélgica","Uruguay":"Uruguai","England":"Inglaterra",
 "Argentina":"Argentina","Portugal":"Portugal","Mexico":"México","South Africa":"África do Sul",
 "South Korea":"Coreia do Sul","Czechia":"República Tcheca","Czech Republic":"República Tcheca",
 "United States":"Estados Unidos","USA":"Estados Unidos","Canada":"Canadá",
 "Bosnia and Herzegovina":"Bósnia e Herzegovina","Scotland":"Escócia","Morocco":"Marrocos",
 "Switzerland":"Suíça","Qatar":"Catar","Japan":"Japão","Croatia":"Croácia","Italy":"Itália",
 "Norway":"Noruega","Sweden":"Suécia","Poland":"Polônia","Austria":"Áustria","Turkey":"Turquia",
 "Australia":"Austrália","Ecuador":"Equador","Colombia":"Colômbia","Paraguay":"Paraguai",
 "Ivory Coast":"Costa do Marfim","Côte d'Ivoire":"Costa do Marfim","Egypt":"Egito",
 "Senegal":"Senegal","Ghana":"Gana","Tunisia":"Tunísia","Algeria":"Argélia","Panama":"Panamá",
 "Costa Rica":"Costa Rica","Haiti":"Haiti","Jordan":"Jordânia","Uzbekistan":"Uzbequistão",
 "Iran":"Irã","Saudi Arabia":"Arábia Saudita","New Zealand":"Nova Zelândia","Cape Verde":"Cabo Verde",
 "Curacao":"Curaçao","Curaçao":"Curaçao","Ukraine":"Ucrânia","Denmark":"Dinamarca",
 "Ireland":"Irlanda","Northern Ireland":"Irlanda do Norte","Wales":"País de Gales",
 "North Macedonia":"Macedônia do Norte","Slovakia":"Eslováquia","Romania":"Romênia","Kosovo":"Kosovo"}
def trad(n): return TRAD.get(n, n)

RE_HORA = re.compile(r"\b(\d{1,2}[:h]\d{2})\b")

# =================================================================
# FUTEBOL — fonte A: futebolnatv.com.br
# =================================================================
def fonte_futebolnatv():
    urls = {HOJE: "https://www.futebolnatv.com.br/jogos-hoje",
            HOJE + timedelta(days=1): "https://www.futebolnatv.com.br/jogos-amanha"}
    evs = []
    for data, url in urls.items():
        r = requests.get(url, headers=HEADERS, timeout=30); r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        links = soup.select('a[href*="/aovivo/"]')
        # instrumentação: se o site mudar de layout, o diagnóstico mostra o que veio
        DIAG["amostras"].setdefault("futebolnatv", []).append({
            "url": url, "http": r.status_code, "bytes": len(r.text),
            "links_aovivo": len(links),
            "hrefs_amostra": [a.get("href", "")[:70] for a in soup.find_all("a", href=True)[:12]],
        })
        vistos = set()
        for a in links:
            texto = " ".join(a.get_text(" ", strip=True).split())
            m = RE_HORA.search(texto)
            if not m: continue
            hora = m.group(1).replace("h", ":").zfill(5)
            antes, depois = texto[:m.start()].strip(), texto[m.end():].strip()

            liga, detalhe = antes, ""
            if " - " in antes:
                lf, detalhe = antes.rsplit(" - ", 1)
                meio = len(lf) // 2
                liga = lf[:meio].strip() if lf[:meio].strip() == lf[meio:].strip() else lf.strip()

            tokens = depois.split()
            ctk = []
            while tokens and re.fullmatch(r"[A-Z0-9ÉÊÁ+]{2,}", tokens[-1]):
                ctk.insert(0, tokens.pop())
            resto = " ".join(tokens)
            canais, i = [], 0
            while i < len(ctk):
                par = " ".join(ctk[i:i+2])
                if sem_acento(par).upper() in COMPOSTOS: canais.append(par); i += 2
                else: canais.append(ctk[i]); i += 1
            canais = [c for c in canais
                      if not re.search(r"copa|fifa|rodada|grupo|s[ée]rie|\b20\d\d\b",
                                       sem_acento(c), re.I)]

            # nº de palavras de cada time vem do slug da URL: "time-a-x-time-b-HASH"
            partida = None
            slug = (a.get("href") or "").rsplit("/", 1)[-1].replace(".html", "")
            m_slug = re.match(r"(.+?)-x-(.+?)-[0-9a-f]{6,}$", slug)
            palavras = resto.split()
            if m_slug:
                n1 = len(m_slug.group(1).split("-")); n2 = len(m_slug.group(2).split("-"))
                if len(palavras) == n1 + n2:                # texto sem duplicação
                    partida = " ".join(palavras[:n1]) + " x " + " ".join(palavras[n1:])
                elif len(palavras) == 2 * (n1 + n2):        # texto duplicado (alt da imagem)
                    partida = " ".join(palavras[:n1]) + " x " + " ".join(palavras[2*n1:2*n1+n2])
            if not partida:                                 # último recurso: dedup por regex
                m2 = re.fullmatch(r"(.+?) \1 (.+?) \2", resto)
                partida = f"{m2.group(1)} x {m2.group(3)}" if m2 else resto
            partida = limpa_titulo(partida)
            # validação: precisa sobrar "Time x Time" plausível, senão descarta
            if not re.search(r".+\s+x\s+.+", partida, re.I): continue
            if not partida or (data, hora, norm(partida)) in vistos: continue
            vistos.add((data, hora, norm(partida)))
            evs.append(evento(data, hora, "Futebol", liga, partida,
                              [canal(c) for c in canais] or [{"n":"A confirmar","y":"tv"}],
                              detail=detalhe or "—"))
    return evs

# =================================================================
# FUTEBOL — fonte B (cruzamento): mantosdofutebol.com.br
# =================================================================
def separa_canais_mantos(texto):
    """Divide 'A, B e C' e 'Globo/SporTV' sem quebrar URLs (Youtube.com/@X)."""
    out = []
    for parte in re.split(r",| e ", texto):
        parte = parte.strip()
        if not parte: continue
        if ".com" in parte.lower():
            out.append(limpa_canal_mantos(parte))
        else:
            out += [p.strip() for p in parte.split("/") if p.strip()]
    return out

def limpa_canal_mantos(c):
    """'Youtube.com/@TNTSportsBR' -> 'TNT Sports BR (YouTube)'"""
    m = re.search(r"youtube\.com/@?([\w.]+)", c, re.I)
    if m:
        nome = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", m.group(1))
        return f"{nome} (YouTube)"
    return c

def fonte_mantos():
    urls = {HOJE: "https://mantosdofutebol.com.br/guia-de-jogos-tv-hoje-ao-vivo/",
            HOJE + timedelta(days=1): "https://mantosdofutebol.com.br/jogos-de-amanha-tv/"}
    # Estrutura real (capturada via diagnostico.json em 09/11/2026):
    #   "15h00 – Palmeiras x Santos – Paulistão Sub-20 (Semifinal-ida)"
    #   "Canais: Youtube.com/@TNTSportsBR"
    # A COMPETIÇÃO vem na linha do jogo; os CANAIS na linha seguinte.
    rx = re.compile(r"(\d{1,2})[h:](\d{2})\s*[–\-—]\s*(.+?)\s+[xX]\s+(.+?)\s*[–\-—]\s*([^(\n]+?)\s*(?:\(([^)]+)\))?\s*$")
    evs = []
    for data, url in urls.items():
        r = requests.get(url, headers=HEADERS, timeout=30); r.raise_for_status()
        corpo = BeautifulSoup(r.text, "html.parser").select_one("article")
        if not corpo: continue
        linhas = [l.strip() for l in corpo.get_text("\n").split("\n") if l.strip()]
        # instrumentação mantida: detecta mudança futura de layout
        if "mantos" not in DIAG["amostras"]:
            blocos = []
            for i, l in enumerate(linhas):
                if rx.search(l) and len(blocos) < 3:
                    blocos.append(" || ".join(linhas[i:i+2])[:300])
            DIAG["amostras"]["mantos"] = blocos
        for i, linha in enumerate(linhas):
            m = rx.search(linha)
            if not m: continue
            h, mi, t1, t2, compet, fase = m.groups()
            canais = []
            for prox in linhas[i+1:i+3]:
                mc = re.match(r"Canais?\s*:\s*(.+)", prox, re.I)
                if mc:
                    canais = separa_canais_mantos(mc.group(1))
                    break
                if rx.search(prox): break  # próximo jogo veio antes da linha de canais
            liga = (compet or "").strip() or "Futebol"
            detalhe = (fase or "—").strip() or "—"
            # segurança: se a "liga" for apenas uma fase, vira detalhe
            if re.search(r"^(semifinal|quartas|oitavas|final|rodada|jogo \d)", sem_acento(liga), re.I):
                detalhe, liga = liga, "Futebol"
            evs.append(evento(data, f"{int(h):02d}:{mi}", "Futebol", liga,
                              f"{t1.strip()} x {t2.strip()}",
                              [canal(c) for c in canais] or [{"n": "Transmissão a confirmar", "y": "tv"}],
                              detail=detalhe))
    return evs

# =================================================================
# ESPN API (pública, JSON) — NBA, F1, Tênis
# Canais por modalidade = direitos de transmissão vigentes no Brasil.
# Se os direitos mudarem, ajustar SOMENTE o dicionário abaixo.
# =================================================================
CANAIS_MODALIDADE = {
    "nba": [canal("Prime Video"), canal("NBA League Pass")],
}

def espn_json(path, data):
    url = f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={data:%Y%m%d}"
    return requests.get(url, headers=HEADERS, timeout=30).json()

def fonte_nba():
    evs = []
    for data in JANELA:
        for ev in espn_json("basketball/nba", data).get("events", []):
            dt = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(BRT)
            if dt.date() not in JANELA: continue
            comp = ev.get("competitions", [{}])[0]
            times = [c.get("team", {}).get("displayName", "?") for c in comp.get("competitors", [])]
            if len(times) < 2: continue
            notas = comp.get("notes", [])
            detalhe = notas[0].get("headline", "Temporada NBA") if notas else "Temporada NBA"
            evs.append(evento(dt.date(), dt.strftime("%H:%M"), "Basquete", "NBA",
                              f"{times[1]} x {times[0]}", CANAIS_MODALIDADE["nba"],
                              detail=detalhe, country="EUA/Canadá", v=2))
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
    for data in JANELA:
        for ev in espn_json("football/nfl", data).get("events", []):
            dt = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(BRT)
            if dt.date() not in JANELA: continue
            comp = ev.get("competitions", [{}])[0]
            times = [c.get("team", {}).get("displayName", "?") for c in comp.get("competitors", [])]
            if len(times) < 2: continue
            notas = comp.get("notes", [])
            detalhe = notas[0].get("headline", "Temporada NFL") if notas else "Temporada NFL"
            evs.append(evento(dt.date(), dt.strftime("%H:%M"), "Futebol Americano", "NFL",
                              f"{times[1]} x {times[0]}", canais_nfl(dt),
                              detail=detalhe, country="EUA/Canadá", v=2))
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
    evs = []
    for slug, liga, pais in LIGAS_FIXTURES:
        for data in JANELA:
            try:
                j = espn_json(slug, data)
            except Exception:
                continue
            for ev in j.get("events", []):
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
                hn = trad(home.get("team", {}).get("displayName", "?"))
                an = trad(away.get("team", {}).get("displayName", "?"))
                notas = comp.get("notes", [])
                detalhe = (notas[0].get("headline", "") if notas else "") or "Tabela oficial"
                evs.append(evento(dt.date(), dt.strftime("%H:%M"), "Futebol", liga,
                                  f"{hn} x {an}", [{"n": "Transmissão a confirmar", "y": "tv"}],
                                  detail=detalhe, country=pais, v=1))
    return dedup(evs)





# =================================================================
# UOL — página da Copa com jogos e ONDE VAI PASSAR por jogo.
# Parser defensivo: procura blocos "Time x Time" com canais próximos.
# Grava amostra bruta no diagnóstico para calibragem com dados reais.
def mesmo_jogo(m1, m2):
    """Compara TIME A TIME, não a string inteira (evita falsos positivos como
    'Holanda x Japão' ~ 'Alemanha x Curaçao' por letras coincidentes)."""
    p1 = re.split(r"\s+x\s+", m1, flags=re.I)
    p2 = re.split(r"\s+x\s+", m2, flags=re.I)
    if len(p1) != 2 or len(p2) != 2:
        return difflib.SequenceMatcher(None, norm(m1), norm(m2)).ratio() > 0.7
    def casa(a, b):
        a, b = norm(a), norm(b)
        if not a or not b: return False
        if a == b or a in b or b in a: return True
        return difflib.SequenceMatcher(None, a, b).ratio() > 0.8
    # mesmo jogo se ambos os times casam (direto ou invertido)
    return (casa(p1[0], p2[0]) and casa(p1[1], p2[1])) or \
           (casa(p1[0], p2[1]) and casa(p1[1], p2[0]))

def complementar_fixtures(base, fixtures):
    """Tabela só preenche jogos que nenhuma fonte trouxe. Quando o jogo JÁ
    existe com liga genérica ("Futebol", vinda do scraping), herda liga/país
    da tabela oficial — permite filtrar por campeonato de verdade."""
    for fx in fixtures:
        achou = None
        for b in base:
            if b["date"] == fx["date"] and b["sport"] == "Futebol" and \
               mesmo_jogo(b["match"], fx["match"]):
                achou = b; break
        if achou is None:
            base.append(fx)
        elif achou.get("league") in ("Futebol", "", None):
            achou["league"] = fx["league"]
            achou["country"] = fx.get("country", achou.get("country"))
            if (achou.get("detail") or "—") == "—":
                achou["detail"] = fx.get("detail", achou.get("detail"))
    return base

# ----------------------------------------------------------------- merge
def dedup_por_jogo(evs):
    """Remove o MESMO jogo aparecendo 2x no dia (fontes com horário/grafia
    divergentes). Mantém o de maior prioridade: tem canais reais > tem placar(v) > scraping."""
    def prioridade(e):
        tem_canal = any("confirmar" not in c["n"].lower() for c in e["ch"])
        return (2 if tem_canal else 0) + e.get("v", 1)
    saida = []
    for e in sorted(evs, key=prioridade, reverse=True):
        dup = next((s for s in saida
                    if s["date"] == e["date"] and s["sport"] == e["sport"]
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

def mesclar_futebol(base, extra):
    for ev in extra:
        achou = None
        for b in base:
            if b["date"] == ev["date"] and b["t"] == ev["t"] and mesmo_jogo(b["match"], ev["match"]):
                achou = b; break
        if achou:
            nomes = {norm(c["n"]) for c in achou["ch"]}
            achou["ch"] += [c for c in ev["ch"] if norm(c["n"]) not in nomes]
            achou["v"] = 2
            if achou["league"] in ("Futebol", "—") and ev["league"] not in ("Futebol", "—"):
                achou["league"], achou["country"] = ev["league"], ev["country"]
        # IMPORTANTE: fonte B é apenas CRUZAMENTO. Evento que só existe nela
        # é descartado — evita "ligas" fantasmas geradas por erro de parsing.
    return base

def main():
    eventos = []

    def roda(nome, fn, log):
        try:
            r = fn(); log.append(f"  ✓ {nome}: {len(r)} evento(s)"); return r
        except Exception as e:
            log.append(f"  ✗ {nome} indisponível ({type(e).__name__}: {e})"); return []

    log = [f"Coleta {datetime.now(BRT):%d/%m %H:%M} BRT — janela {JANELA[0]} a {JANELA[-1]}"]

    fut_a = roda("futebolnatv", fonte_futebolnatv, log)
    fut_b = roda("mantosdofutebol", fonte_mantos, log)
    eventos += mesclar_futebol(fut_a, fut_b) if fut_a else fut_b

    fixtures = roda("ESPN tabelas (futebol, 7 dias)", fonte_fixtures, log)
    eventos = complementar_fixtures(eventos, fixtures)

    eventos += roda("ESPN NBA", fonte_nba, log)
    eventos += roda("ESPN NFL", fonte_nfl, log)
    eventos = dedup_por_jogo(eventos)
    eventos = dedup(eventos)
    eventos = [e for e in eventos if e["date"] >= HOJE.isoformat()]
    # Sanitização final: nenhum pseudo-canal passa, venha de qual fonte vier
    PSEUDO = re.compile(r"copa|fifa|rodada|grupo|s[ée]rie|amistos|libertadores|\b20\d\d\b", re.I)
    for e in eventos:
        e["ch"] = [c for c in e["ch"]
                   if c["n"].strip().upper() == "FIFA+" or not PSEUDO.search(sem_acento(c["n"]))]
        if not e["ch"]: e["ch"] = [{"n": "Transmissão a confirmar", "y": "tv"}]
    eventos.sort(key=lambda e: (e["date"], e["t"]))

    Path("eventos.json").write_text(json.dumps(eventos, ensure_ascii=False, indent=1), encoding="utf-8")
    log.append(f"TOTAL: {len(eventos)} eventos → eventos.json")

    agora = datetime.now(BRT).strftime("%d/%m %H:%M")
    for nome in ("index.html", "onde-assistir.html"):
        p = Path(nome)
        if p.exists():
            html = p.read_text(encoding="utf-8")
            bloco = "/*DB-START*/\nconst DB = " + json.dumps(eventos, ensure_ascii=False, indent=1) + ";\n/*DB-END*/"
            html = re.sub(r"/\*DB-START\*/.*?/\*DB-END\*/", bloco, html, flags=re.S)
            html = re.sub(r"const GENERATED_AT = .*?;", f"const GENERATED_AT = '{agora}';", html)
            p.write_text(html, encoding="utf-8")
            log.append(f"{nome} atualizado.")
    DIAG["fontes"]["log"] = log
    Path("diagnostico.json").write_text(json.dumps(DIAG, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(log))

if __name__ == "__main__":
    main()
